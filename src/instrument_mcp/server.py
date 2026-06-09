import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any

from mcp.server.fastmcp import FastMCP

from instrument_mcp.instruments import KeysightMXA

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


@mcp.tool()
def connect_mxa(address: str, alias: str = "mxa") -> str:
    """连接 Keysight MXA N9020A 频谱仪（VISA 地址如 TCPIP0::192.168.1.10::inst0::INSTR）"""
    try:
        inst = KeysightMXA(address=address)
        inst.open()
        idn = inst.get_idn()
        _sessions[alias] = inst
        return f"[PASS] MXA 已连接: {address} | IDN: {idn}"
    except Exception as e:
        return f"[FAIL] {e}"


@mcp.tool()
def send_scpi(alias: str, command: str, query: bool = False) -> str:
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


@mcp.tool()
def disconnect(alias: str) -> str:
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
    return "[PASS] 活跃连接: " + ", ".join(_sessions.keys())


@mcp.tool()
def mxa_preset(alias: str = "mxa") -> str:
    """MXA 恢复预设状态 (*RST)"""
    if alias not in _sessions:
        return f"[FAIL] 未找到别名 '{alias}'"
    try:
        _sessions[alias].preset()
        return "[PASS] MXA preset done"
    except Exception as e:
        return f"[FAIL] {e}"


@mcp.tool()
def mxa_set_frequency(alias: str = "mxa", center_hz: float = 1e9, span_hz: float = 1e6) -> str:
    """设置 MXA 中心频率和扫宽（单位 Hz）"""
    if alias not in _sessions:
        return f"[FAIL] 未找到别名 '{alias}'"
    try:
        inst = _sessions[alias]
        inst.set_center_freq(center_hz)
        inst.set_span(span_hz)
        return f"[PASS] FREQ:CENT {center_hz} Hz, SPAN {span_hz} Hz"
    except Exception as e:
        return f"[FAIL] {e}"


@mcp.tool()
def mxa_peak_search(alias: str = "mxa") -> str:
    """MXA 执行峰值搜索并返回幅度值"""
    if alias not in _sessions:
        return f"[FAIL] 未找到别名 '{alias}'"
    try:
        result = _sessions[alias].peak_search()
        return f"[PASS] Peak: {result} dBm"
    except Exception as e:
        return f"[FAIL] {e}"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
