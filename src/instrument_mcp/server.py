import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any

from mcp.server.fastmcp import FastMCP

from instrument_mcp.instruments import KeysightMXA
from instrument_mcp.tools import INSTRUMENT_REGISTRY, COMMAND_REGISTRY

logger = logging.getLogger(__name__)
_sessions: Dict[str, Any] = {}


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[Dict]:
    logger.info("[Instrument MCP] Server starting...")
    yield {"sessions": _sessions}
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
    """连接仪器（根据 instrument_type 自动选择驱动类）"""
    if instrument_type not in INSTRUMENT_REGISTRY:
        supported = ", ".join(INSTRUMENT_REGISTRY.keys())
        return f"[FAIL] 不支持的仪器类型 '{instrument_type}'，支持: {supported}"
    cls, desc = INSTRUMENT_REGISTRY[instrument_type]
    try:
        inst = cls(address=address)
        inst.open()
        idn = inst.get_idn() if hasattr(inst, "get_idn") else "N/A"
        _sessions[alias] = inst
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
    """列出当前所有连接的仪器别名"""
    if not _sessions:
        return "[INFO] 无活跃连接"
    lines = []
    for alias, inst in _sessions.items():
        inst_type = type(inst).__name__
        lines.append(f"{alias} ({inst_type})")
    return "[PASS] 活跃连接:\n" + "\n".join(lines)


# ─────────────────────────────────────────────
# 通用 SCPI 透传
# ─────────────────────────────────────────────
@mcp.tool()
def scpi(alias: str = "default", command: str = "", query: bool = False) -> str:
    """发送 SCPI 命令到指定别名仪器"""
    if alias not in _sessions:
        return f"[FAIL] 未找到别名 '{alias}'"
    inst = _sessions[alias]
    try:
        if query:
            resp = inst.query(command)
            return f"[PASS] {resp}"
        inst.write(command)
        return "[PASS] OK"
    except Exception as e:
        return f"[FAIL] {e}"


# ─────────────────────────────────────────────
# 高层命令：自动分发到 COMMAND_REGISTRY
# ─────────────────────────────────────────────
@mcp.tool()
def run_command(alias: str = "default", command: str = "", params: str = "") -> str:
    """执行已注册的高层命令（params 为 JSON 字符串，可选）"""
    if alias not in _sessions:
        return f"[FAIL] 未找到别名 '{alias}'"
    if command not in COMMAND_REGISTRY:
        supported = ", ".join(COMMAND_REGISTRY.keys())
        return f"[FAIL] 未知命令 '{command}'，支持: {supported}"

    import json
    kwargs = {}
    if params:
        try:
            kwargs = json.loads(params)
        except Exception as e:
            return f"[FAIL] params JSON 解析错误: {e}"

    inst = _sessions[alias]
    try:
        return COMMAND_REGISTRY[command](inst, **kwargs)
    except Exception as e:
        return f"[FAIL] {e}"


# ─────────────────────────────────────────────
# 便捷入口：直接暴露常用命令（内部调用 run_command）
# ─────────────────────────────────────────────
@mcp.tool()
def mxa_preset(alias: str = "default") -> str:
    """MXA 恢复预设状态 (*RST)"""
    return run_command(alias, command="mxa_preset")


@mcp.tool()
def mxa_set_frequency(alias: str = "default", center_hz: float = 1e9, span_hz: float = 1e6) -> str:
    """设置 MXA 中心频率和扫宽（单位 Hz）"""
    import json
    return run_command(alias, command="mxa_set_frequency", params=json.dumps({"center_hz": center_hz, "span_hz": span_hz}))


@mcp.tool()
def mxa_peak_search(alias: str = "default") -> str:
    """MXA 执行峰值搜索并返回幅度值"""
    return run_command(alias, command="mxa_peak_search")


def main():
    mcp.run()


if __name__ == "__main__":
    main()
