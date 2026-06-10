"""
Instrument MCP Server - 仪器控制 MCP 服务器

使用流程：
1. 连接仪器: connect(address="TCPIP::IP::INSTR", alias="mxa")
2. 查看可用命令: get_available_tools(instrument_type="mxa")
3. 执行命令: mxa_preset(alias="mxa")
4. 探索新命令: explore_scpi(alias="mxa", command="*IDN?", query=True)
5. 保存命令: save_learned_command(name="my_cmd", instrument_type="mxa", ...)
6. 初始化项目: init_project_commands()  # 复制内置 YAML 到项目目录

支持的仪器:
- Keysight MXA/EXA 频谱仪 (mxa)
- R&S CMW500 无线通信测试仪 (cmw)
- Keysight 66311B 直流电源 (keysight_ps)
- 通用 SCPI 仪器 (generic)

项目级命令扩展:
- 运行 init_project_commands() 会在当前目录创建 .instrument_mcp/
- 内置 YAML 会被复制到该目录，可直接编辑
- 新学习的命令会追加到对应仪器的 YAML 文件中
- 重启 MCP Server 后自动加载
"""

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
    """连接 VISA 仪器。必须先连接才能使用其他命令。

    使用示例:
    - 连接 MXA: connect(address="TCPIP::192.168.1.100::INSTR", alias="mxa")
    - 连接 CMW500: connect(address="TCPIP::172.22.1.3::INSTR", alias="cmw")
    - 连接电源: connect(address="GPIB0::5::INSTR", alias="ps")

    Args:
        address: VISA 地址，如 "TCPIP::IP::INSTR" 或 "GPIB0::5::INSTR"
        instrument_type: 仪器类型，设为 "auto" 自动识别，或指定 "mxa"/"cmw"/"keysight_ps"
        alias: 会话别名，后续命令通过此别名引用仪器，如 "mxa"/"cmw"/"ps"
    """
    from instrument_mcp.instruments import VisaInstrument, INSTRUMENT_REGISTRY

    try:
        inst = VisaInstrument(address=address)
        inst.open()
    except Exception as e:
        return f"[FAIL] 连接失败: {e}"

    idn = "N/A"
    try:
        idn = inst.query("*IDN?").strip()
        logger.info(f"[{alias}] IDN: {idn}")
    except Exception as e:
        logger.warning(f"[{alias}] 无法读取 IDN: {e}")

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

    if detected_type not in INSTRUMENT_REGISTRY:
        inst.close()
        supported = ", ".join(INSTRUMENT_REGISTRY.keys())
        return f"[FAIL] 不支持的仪器类型 '{detected_type}'，支持: {supported}"

    _sessions[alias] = inst

    _command_history.append({
        "action": "connect",
        "alias": alias,
        "address": address,
        "instrument_type": detected_type,
        "idn": idn,
    })

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
    """断开指定别名的仪器连接。

    Args:
        alias: 要断开的仪器别名，如 "mxa"/"cmw"/"ps"
    """
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
    """列出当前所有已连接的仪器别名。"""
    if not _sessions:
        return "[INFO] 无活跃连接"
    lines = []
    for alias, inst in _sessions.items():
        inst_type = type(inst).__name__
        lines.append(f"{alias} ({inst_type})")
    return "[PASS] 活跃连接:\n" + "\n".join(lines)


@mcp.tool()
def get_history(limit: int = 10) -> str:
    """查看最近的命令执行历史，用于调试。

    Args:
        limit: 返回最近多少条记录
    """
    if not _command_history:
        return "[INFO] 无历史记录"
    recent = _command_history[-limit:]
    import json
    return "[PASS] 最近历史:\n" + json.dumps(recent, indent=2, ensure_ascii=False)


@mcp.tool()
def get_available_tools(instrument_type: str = "") -> str:
    """列出当前可用的所有仪器命令。

    使用场景:
    - 连接仪器后查看支持哪些命令
    - 确认某个命令是否存在

    Args:
        instrument_type: 指定仪器类型过滤，如 "mxa"/"cmw"，留空显示全部
    """
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
    """识别已连接仪器的型号，并推荐可用命令。

    使用场景: 连接后确认仪器型号，查看推荐命令
    """
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
            f"  建议: 使用 explore_scpi 手动探索命令"
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
    """调试最后一个失败的命令，读取仪器错误队列。

    使用场景: 命令执行失败后排查原因
    """
    if alias not in _sessions:
        return f"[FAIL] 未找到别名 '{alias}'"

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

    result += "\n  建议:\n"
    result += "    1. 检查命令参数类型\n"
    result += "    2. 使用 discover_my_instrument 确认仪器型号\n"
    result += "    3. 使用 explore_scpi 手动探索正确语法\n"

    return result


# ─────────────────────────────────────────────
# 命令探索与学习工具
# ─────────────────────────────────────────────
@mcp.tool()
def explore_scpi(alias: str = "default", command: str = "", query: bool = False) -> str:
    """探索性发送 SCPI 命令，用于学习和发现仪器支持的命令。

    使用场景:
    - 仪器型号未知，需要探索支持的命令
    - 内置命令不够用，需要发现新功能
    - 命令语法不确定，需要验证

    工作流程:
    1. 先用 explore_scpi(command="*IDN?", query=True) 确认通信
    2. 尝试发送各种命令探索功能
    3. 成功的命令用 save_learned_command() 保存

    Args:
        alias: 已连接仪器的别名，如 "mxa"/"cmw"
        command: SCPI 命令字符串，如 "FREQ:CENT?" 或 "*OPT?"
        query: 是否为查询命令（命令以 ? 结尾时自动识别）
    """
    if alias not in _sessions:
        return f"[FAIL] 未找到别名 '{alias}'，请先使用 connect 连接仪器"

    inst = _sessions[alias]
    cmd = command.strip()
    if not cmd:
        return "[FAIL] 命令不能为空"

    entry = {
        "action": "explore",
        "alias": alias,
        "command": cmd,
        "query": query,
        "status": "running",
    }
    _command_history.append(entry)

    try:
        if query or cmd.endswith("?"):
            resp = inst.query(cmd)
            entry["status"] = "ok"
            return f"[PASS] 查询结果:\n  发送: {cmd}\n  返回: {resp}"
        else:
            inst.write(cmd)
            entry["status"] = "ok"
            return f"[PASS] 命令已发送: {cmd}"

    except Exception as e:
        entry["status"] = f"error: {e}"
        try:
            err = inst.query("SYST:ERR?")
            return f"[FAIL] {e}\n  仪器错误: {err}\n  提示: 检查命令语法或仪器状态"
        except Exception:
            return f"[FAIL] {e}\n  提示: 仪器可能未响应，检查连接"


@mcp.tool()
def save_learned_command(
    name: str = "",
    description: str = "",
    scpi_template: str = "",
    instrument_type: str = "generic",
    params_json: str = "[]",
    is_query: bool = False,
) -> str:
    """将探索成功的 SCPI 命令保存为可复用的 MCP 工具。

    保存规则:
    - 按 instrument_type 保存到 .instrument_mcp/{instrument_type}.yaml
    - 如果该仪器有内置 YAML，会先复制作为基础再追加
    - 同名命令会自动更新而非重复
    - 重启 MCP Server 后自动加载

    使用场景:
    - 用 explore_scpi 发现新命令后保存
    - 为常用命令创建快捷方式
    - 团队协作时共享命令配置

    参数格式示例:
    - params_json: '[{"name":"freq_hz","type":"number","description":"频率(Hz)","default":1000000000}]'

    Args:
        name: 命令名称，如 "mxa_set_peak_threshold"
        description: 命令功能描述
        scpi_template: SCPI 模板，如 "FREQ:CENT {freq_hz}" 或 "*IDN?"
        instrument_type: 仪器类型，如 "mxa"/"cmw"/"keysight_ps"
        params_json: 参数定义 JSON 数组字符串
        is_query: 是否为查询命令（返回数据的命令）
    """
    import json
    import os
    import shutil
    from pathlib import Path

    if not name or not scpi_template:
        return "[FAIL] name 和 scpi_template 不能为空"

    try:
        params = json.loads(params_json) if params_json else []
    except Exception as e:
        return f"[FAIL] params_json 解析错误: {e}"

    cmd_def = {
        "name": name,
        "description": description or f"Custom command: {name}",
        "annotations": {"readOnlyHint": is_query},
        "params": params,
    }

    if is_query:
        cmd_def["scpi_template"] = {"query": scpi_template}
    else:
        cmd_def["scpi_template"] = scpi_template

    cwd = Path(os.getcwd())
    custom_dir = cwd / ".instrument_mcp"
    custom_dir.mkdir(exist_ok=True)

    filepath = custom_dir / f"{instrument_type}.yaml"

    try:
        import yaml

        if not filepath.exists():
            builtin_path = Path(__file__).parent / "commands" / f"{instrument_type}.yaml"
            if builtin_path.exists():
                shutil.copy2(builtin_path, filepath)
                logger.info(f"Copied builtin YAML to: {filepath}")
            else:
                yaml_content = {
                    "instrument_type": instrument_type,
                    "description": f"Custom commands for {instrument_type}",
                    "model_keywords": [],
                    "commands": [],
                }
                with open(filepath, "w", encoding="utf-8") as f:
                    yaml.dump(yaml_content, f, allow_unicode=True, sort_keys=False)

        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "commands" not in data:
            data["commands"] = []

        existing_idx = None
        for idx, cmd in enumerate(data["commands"]):
            if cmd.get("name") == name:
                existing_idx = idx
                break

        if existing_idx is not None:
            data["commands"][existing_idx] = cmd_def
            action = "更新"
        else:
            data["commands"].append(cmd_def)
            action = "追加"

        with open(filepath, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)

        return (
            f"[PASS] 命令已{action}:\n"
            f"  文件: {filepath}\n"
            f"  名称: {name}\n"
            f"  类型: {instrument_type}\n"
            f"  该文件命令数: {len(data['commands'])}\n"
            f"  提示: 重启 MCP Server 后可用"
        )
    except Exception as e:
        return f"[FAIL] 保存失败: {e}"


@mcp.tool()
def list_learned_commands() -> str:
    """列出当前项目中已保存的自定义命令文件。"""
    import os
    from pathlib import Path

    cwd = Path(os.getcwd())
    custom_dir = cwd / ".instrument_mcp"

    if not custom_dir.exists():
        return "[INFO] 未找到 .instrument_mcp/ 目录，运行 init_project_commands() 初始化"

    files = list(custom_dir.glob("*.yaml"))
    if not files:
        return "[INFO] 暂无命令文件"

    lines = [f"[PASS] 项目命令文件 ({len(files)}个):"]
    for f in sorted(files):
        lines.append(f"  - {f.name}")
    return "\n".join(lines)


@mcp.tool()
def init_project_commands() -> str:
    """初始化项目命令目录，复制内置 YAML 到当前项目的 .instrument_mcp/。

    使用场景:
    - 新项目开始使用 instrument-mcp
    - 需要自定义或扩展仪器命令
    - 团队协作时统一命令配置

    执行后:
    - 创建 .instrument_mcp/ 目录
    - 复制所有内置 YAML 文件（mxa.yaml, cmw.yaml 等）
    - 可直接编辑这些文件添加自定义命令
    - 新命令会追加到对应文件
    """
    import os
    import shutil
    from pathlib import Path

    cwd = Path(os.getcwd())
    custom_dir = cwd / ".instrument_mcp"
    custom_dir.mkdir(exist_ok=True)

    builtin_dir = Path(__file__).parent / "commands"
    copied = []
    if builtin_dir.exists():
        for builtin_file in sorted(builtin_dir.glob("*.yaml")):
            dest = custom_dir / builtin_file.name
            if not dest.exists():
                shutil.copy2(builtin_file, dest)
                copied.append(dest.name)
                logger.info(f"Copied builtin YAML: {dest.name}")

    gitignore = custom_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            "# Project-specific instrument commands\n"
            "# Edit these YAML files to customize commands\n",
            encoding="utf-8",
        )

    if copied:
        file_list = "\n".join([f"    - {f}" for f in copied])
        return (
            f"[PASS] 项目命令目录已初始化:\n"
            f"  路径: {custom_dir}\n"
            f"  已复制内置 YAML ({len(copied)}个):\n{file_list}\n"
            f"  提示: 直接编辑这些 YAML 文件即可自定义命令"
        )
    else:
        existing = sorted([f.name for f in custom_dir.glob("*.yaml")])
        return (
            f"[PASS] 项目命令目录已存在:\n"
            f"  路径: {custom_dir}\n"
            f"  现有文件 ({len(existing)}个): {', '.join(existing)}"
        )


# ─────────────────────────────────────────────
# 动态注册 YAML 命令
# ─────────────────────────────────────────────
register_commands_from_yaml(mcp, _sessions, _command_history)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
