"""MCP Tools 定义，与具体仪器类解耦。

新增仪器时，只需：
1. 在 instruments.py 里写新仪器类
2. 在 INSTRUMENT_REGISTRY 里注册映射
3. 可选：在 COMMAND_REGISTRY 里注册该仪器特有的高层命令
"""

import logging
from typing import Dict, Any, Callable

from instrument_mcp.instruments import KeysightMXA

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# 仪器注册表：alias -> (类, 描述)
# ─────────────────────────────────────────────
INSTRUMENT_REGISTRY: Dict[str, Any] = {
    "mxa": (KeysightMXA, "Keysight MXA N9020A 频谱仪"),
}

# ─────────────────────────────────────────────
# 高层命令注册表：命令名 -> 执行函数
# 函数签名: (inst, **kwargs) -> str
# ─────────────────────────────────────────────
COMMAND_REGISTRY: Dict[str, Callable[..., str]] = {}


def register_command(name: str):
    """装饰器：注册仪器高层命令。"""
    def decorator(func: Callable[..., str]):
        COMMAND_REGISTRY[name] = func
        return func
    return decorator


# ─────────────────────────────────────────────
# MXA 高层命令
# ─────────────────────────────────────────────
@register_command("mxa_preset")
def _mxa_preset(inst, **kwargs) -> str:
    inst.preset()
    return "[PASS] MXA preset done"


@register_command("mxa_set_frequency")
def _mxa_set_frequency(inst, center_hz: float = 1e9, span_hz: float = 1e6, **kwargs) -> str:
    inst.set_center_freq(center_hz)
    inst.set_span(span_hz)
    return f"[PASS] FREQ:CENT {center_hz} Hz, SPAN {span_hz} Hz"


@register_command("mxa_peak_search")
def _mxa_peak_search(inst, **kwargs) -> str:
    result = inst.peak_search()
    return f"[PASS] Peak: {result} dBm"


# ─────────────────────────────────────────────
# 通用命令（不依赖具体仪器类型）
# ─────────────────────────────────────────────
@register_command("idn")
def _idn(inst, **kwargs) -> str:
    return f"[PASS] {inst.get_idn()}"


@register_command("scpi_write")
def _scpi_write(inst, command: str = "", **kwargs) -> str:
    inst.write(command)
    return "[PASS] OK"


@register_command("scpi_query")
def _scpi_query(inst, command: str = "", **kwargs) -> str:
    resp = inst.query(command)
    return f"[PASS] {resp}"
