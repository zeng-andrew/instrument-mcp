import sys
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, Any

# ═════════════════════════════════════════════
# 零侵入桥接：把 Loom 加入 Python 路径
# ═════════════════════════════════════════════
LOOM_PATH = Path(r"D:\Projects\Loom")  # 改成你的 Loom 实际路径
if str(LOOM_PATH) not in sys.path:
    sys.path.insert(0, str(LOOM_PATH))

# 现在可以像 Loom 内部一样 import
from core.helpers.keysight_mxa_helper import KeysightMxaHelper
# from core.helpers.cmw_helper import CMWHelper  # 等你有了再放开

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)
_sessions: Dict[str, Any] = {}


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[Dict]:
    logger.info("[Instrument MCP] Server starting...")
    yield {"sessions": _sessions}
    # 退出时强制清理
    logger.info("[Instrument MCP] Cleaning up...")
    for alias, helper in list(_sessions.items()):
        try:
            if hasattr(helper, "close"):
                helper.close()
        except Exception:
            pass
    _sessions.clear()


mcp = FastMCP("InstrumentControl", lifespan=app_lifespan)


@mcp.tool()
def connect_mxa(address: str, alias: str = "mxa") -> str:
    """连接 Keysight MXA N9020A 频谱仪"""
    try:
        helper = KeysightMxaHelper(address=address)  # 按你实际接口调整
        helper.open()  # 假设有 open()
        _sessions[alias] = helper
        return f"[PASS] MXA 已连接: {address}"
    except Exception as e:
        return f"[FAIL] {e}"


@mcp.tool()
def send_scpi(alias: str, command: str, query: bool = False) -> str:
    """发送 SCPI 命令"""
    if alias not in _sessions:
        return f"[FAIL] 未找到别名 '{alias}'"
    helper = _sessions[alias]
    try:
        if query:
            resp = helper.query(command)
            return f"[PASS] {resp}"
        helper.write(command)
        return "[PASS] OK"
    except Exception as e:
        return f"[FAIL] {e}"


@mcp.tool()
def disconnect(alias: str) -> str:
    """断开指定仪器"""
    if alias not in _sessions:
        return f"[WARN] '{alias}' 不存在"
    helper = _sessions.pop(alias)
    try:
        helper.close()
    except Exception:
        pass
    return f"[PASS] {alias} 已断开"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
