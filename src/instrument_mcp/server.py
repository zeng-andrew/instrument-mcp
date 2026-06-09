import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any

from mcp.server.fastmcp import FastMCP

from instrument_mcp.commands import register_commands_from_yaml, get_tool_registry

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
def connect(address: str, instrument_type: str = "mxa", alias: str = "default") -> str:
    """连接仪器（根据 instrument_type 自动选择驱动类，并读取 *IDN? 识别型号）"""
    from instrument_mcp.instruments import VisaInstrument, INSTRUMENT_REGISTRY

    if instrument_type not in INSTRUMENT_REGISTRY:
        supported = ", ".join(INSTRUMENT_REGISTRY.keys())
        return f"[FAIL] 不支持的仪器类型 '{instrument_type}'，支持: {supported}"

    cls, desc = INSTRUMENT_REGISTRY[instrument_type]
    try:
        inst = cls(address=address)
        inst.open()

        # 读取仪器标识
        idn = "N/A"
        try:
            idn = inst.query("*IDN?").strip()
            logger.info(f"[{alias}] IDN: {idn}")
        except Exception as e:
            logger.warning(f"[{alias}] 无法读取 IDN: {e}")

        _sessions[alias] = inst

        # 记录连接信息
        _command_history.append({
            "action": "connect",
            "alias": alias,
            "address": address,
            "instrument_type": instrument_type,
            "idn": idn,
        })

        return f"[PASS] {desc} 已连接: {address} | IDN: {idn}"
    except Exception as e:
        return f"[FAIL] {e}"


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
def get_available_tools() -> str:
    """列出当前可用的所有命令及其描述"""
    registry = get_tool_registry()
    if not registry:
        return "[INFO] 无可用命令"
    lines = []
    for name, meta in registry.items():
        lines.append(f"- {name}: {meta['description']}")
    return "[PASS] 可用命令:\n" + "\n".join(lines)


# ─────────────────────────────────────────────
# 动态注册 YAML 命令
# ─────────────────────────────────────────────
register_commands_from_yaml(mcp, _sessions, _command_history)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
