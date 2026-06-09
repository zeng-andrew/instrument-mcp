import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any

from mcp.server.fastmcp import FastMCP

from instrument_mcp.commands import (
    register_commands_from_yaml,
    get_tool_registry,
    discover_instrument_model,
    get_commands_for_model,
)

logger = logging.getLogger(__name__)
_sessions: Dict[str, Any] = {}
_command_history: list = []


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[Dict]:
    logger.info("[Instrument MCP] Server starting...")
    yield {"sessions": _sessions, "history": _command_history}
    logger.info("[Instrument MCP] Cleaning up...")
    for alias, inst in list(_sessions.items()):
        try:
            if hasattr(inst, "close"):
                inst.close()
        except Exception:
            pass
    _sessions.clear()


mcp = FastMCP("InstrumentControl", lifespan=app_lifespan)


# ─────────────────────────────────────────────
# 通用连接 / 断开 / 会话管理
# ─────────────────────────────────────────────
@mcp.tool()
def connect(address: str, instrument_type: str = "auto", alias: str = "default") -> str:
    """连接仪器（自动识别型号，instrument_type='auto' 时根据 *IDN? 推断）"""
    from instrument_mcp.instruments import VisaInstrument, INSTRUMENT_REGISTRY

    # 先用通用驱动连接
    try:
        inst = VisaInstrument(address=address)
        inst.open()
    except Exception as e:
        return f"[FAIL] 连接失败: {e}"

    # 读取仪器标识
    idn = "N/A"
    try:
        idn = inst.query("*IDN?").strip()
        logger.info(f"[{alias}] IDN: {idn}")
    except Exception as e:
        logger.warning(f"[{alias}] 无法读取 IDN: {e}")

    # 自动识别型号
    detected_type = None
    if instrument_type == "auto":
        detected_type = discover_instrument_model(idn)
        if detected_type:
            logger.info(f"[{alias}] 自动识别为: {detected_type}")
        else:
            logger.warning(f"[{alias}] 无法识别型号，使用 generic 驱动")
            detected_type = "generic"
    else:
        detected_type = instrument_type

    # 检查是否支持
    if detected_type not in INSTRUMENT_REGISTRY:
        inst.close()
        supported = ", ".join(INSTRUMENT_REGISTRY.keys())
        return f"[FAIL] 不支持的仪器类型 '{detected_type}'，支持: {supported}"

    _sessions[alias] = inst

    # 记录连接信息
    _command_history.append({
        "action": "connect",
        "alias": alias,
        "address": address,
        "instrument_type": detected_type,
        "idn": idn,
    })

    # 获取该型号的可用命令
    cmds = get_commands_for_model(detected_type)
    cmd_list = ", ".join([c["name"] for c in cmds[:5]]) + ("..." if len(cmds) > 5 else "")

    return (
        f"[PASS] 已连接: {address}\n"
        f"  IDN: {idn}\n"
        f"  识别型号: {detected_type}\n"
        f"  可用命令数: {len(cmds)}\n"
        f"  示例命令: {cmd_list}"
    )


@mcp.tool()
def disconnect(alias: str = "default") -> str:
    """断开指定别名仪器"""
    if alias not in _sessions:
        return f"[WARN] '{alias}' 不存在"
    inst = _sessions.pop(alias)
    try:
        inst.close()
    except Exception:
        pass
    return f"[PASS] {alias} 已断开"


@mcp.tool()
def list_sessions() -> str:
    """列出当前所有连接的仪器别名及型号信息"""
    if not _sessions:
        return "[INFO] 无活跃连接"
    lines = []
    for alias, inst in _sessions.items():
        inst_type = type(inst).__name__
        lines.append(f"{alias} ({inst_type})")
    return "[PASS] 活跃连接:\n" + "\n".join(lines)


@mcp.tool()
def get_history(limit: int = 10) -> str:
    """查看最近的命令执行历史（用于调试和迭代）"""
    if not _command_history:
        return "[INFO] 无历史记录"
    recent = _command_history[-limit:]
    import json
    return "[PASS] 最近历史:\n" + json.dumps(recent, indent=2, ensure_ascii=False)


@mcp.tool()
def get_available_tools(instrument_type: str = "") -> str:
    """列出当前可用的所有命令及其描述，可指定仪器类型过滤"""
    registry = get_tool_registry()
    if not registry:
        return "[INFO] 无可用命令"

    if instrument_type:
        cmds = get_commands_for_model(instrument_type)
        lines = [f"- {c['name']}: {c['description']}" for c in cmds]
        return f"[PASS] {instrument_type} 可用命令 ({len(cmds)}个):\n" + "\n".join(lines)

    lines = []
    for name, meta in registry.items():
        lines.append(f"- [{meta['instrument_type']}] {name}: {meta['description']}")
    return f"[PASS] 全部可用命令 ({len(registry)}个):\n" + "\n".join(lines)


@mcp.tool()
def discover_my_instrument(alias: str = "default") -> str:
    """重新识别指定别名仪器的型号，并推荐可用命令"""
    if alias not in _sessions:
        return f"[FAIL] 未找到别名 '{alias}'"

    inst = _sessions[alias]
    try:
        idn = inst.query("*IDN?").strip()
    except Exception as e:
        return f"[FAIL] 无法读取 IDN: {e}"

    detected = discover_instrument_model(idn)
    if not detected:
        return (
            f"[WARN] 无法识别型号\n"
            f"  IDN: {idn}\n"
            f"  建议: 使用 scpi_query 手动探索命令，"
            f"或将型号关键字添加到对应 YAML 的 model_keywords 中"
        )

    cmds = get_commands_for_model(detected)
    cmd_lines = [f"  - {c['name']}: {c['description']}" for c in cmds]

    return (
        f"[PASS] 识别结果\n"
        f"  IDN: {idn}\n"
        f"  型号: {detected}\n"
        f"  推荐命令 ({len(cmds)}个):\n" + "\n".join(cmd_lines)
    )


@mcp.tool()
def debug_last_error(alias: str = "default") -> str:
    """调试最后一个失败的命令：读取仪器错误队列并给出建议"""
    if alias not in _sessions:
        return f"[FAIL] 未找到别名 '{alias}'"

    # 找最近一个失败的记录
    failed = None
    for entry in reversed(_command_history):
        if entry.get("status", "").startswith("error"):
            failed = entry
            break

    if not failed:
        return "[INFO] 历史记录中没有失败的命令"

    inst = _sessions[alias]
    errors = []
    try:
        # 读取错误队列（最多读 10 条）
        for _ in range(10):
            err = inst.query("SYST:ERR?").strip()
            if err.startswith("+0,") or err.startswith("0,"):
                break
            errors.append(err)
    except Exception as e:
        return f"[FAIL] 读取错误队列失败: {e}"

    result = (
        f"[PASS] 调试信息\n"
        f"  失败命令: {failed.get('command')}\n"
        f"  参数: {failed.get('params')}\n"
        f"  仪器错误队列:\n"
    )
    if errors:
        for err in errors:
            result += f"    - {err}\n"
    else:
        result += "    (无错误)\n"

    # 给出建议
    result += "\n  建议:\n"
    result += "    1. 检查命令参数类型（如数字不要加引号）\n"
    result += "    2. 使用 discover_my_instrument 确认仪器型号\n"
    result += "    3. 使用 scpi_query 发送 *IDN? 确认通信正常\n"
    result += "    4. 查阅仪器编程手册确认 SCPI 语法\n"

    return result


# ─────────────────────────────────────────────
# 动态注册 YAML 命令
# ─────────────────────────────────────────────
register_commands_from_yaml(mcp, _sessions, _command_history)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
