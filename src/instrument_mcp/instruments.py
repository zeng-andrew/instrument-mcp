"""独立仪器通信层，不依赖 Loom。"""

import logging
from typing import Optional

try:
    import pyvisa
except ImportError:
    pyvisa = None  # type: ignore

logger = logging.getLogger(__name__)


class VisaInstrument:
    """通用 VISA 仪器基类。"""

    def __init__(self, address: str, timeout_ms: int = 10000):
        self.address = address
        self.timeout_ms = timeout_ms
        self._resource: Optional[object] = None
        self._rm: Optional[object] = None

    def open(self) -> None:
        if pyvisa is None:
            raise RuntimeError("pyvisa not installed")
        self._rm = pyvisa.ResourceManager()
        self._resource = self._rm.open_resource(self.address)
        self._resource.timeout = self.timeout_ms  # type: ignore
        logger.info(f"VISA connected: {self.address}")

    def close(self) -> None:
        if self._resource:
            try:
                self._resource.close()
            except Exception as e:
                logger.warning(f"Error closing resource: {e}")
            self._resource = None
        if self._rm:
            try:
                self._rm.release()
            except Exception:
                pass
            self._rm = None
        logger.info(f"VISA disconnected: {self.address}")

    def write(self, command: str) -> None:
        if self._resource is None:
            raise RuntimeError("Instrument not connected")
        self._resource.write(command)  # type: ignore

    def query(self, command: str) -> str:
        if self._resource is None:
            raise RuntimeError("Instrument not connected")
        return self._resource.query(command).strip()  # type: ignore

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


# 仪器注册表：新增仪器时在此注册
INSTRUMENT_REGISTRY = {
    "mxa": (VisaInstrument, "Keysight MXA / EXA 系列频谱仪（通用 VISA 驱动）"),
    "keysight_ps": (VisaInstrument, "Keysight 66311B 直流电源"),
    "generic": (VisaInstrument, "通用 SCPI 仪器"),
}


class KeysightMXA(VisaInstrument):
    """Keysight MXA N9020A 频谱仪（保留用于需要自定义方法的场景）。"""

    def get_idn(self) -> str:
        return self.query("*IDN?")

    def preset(self) -> None:
        self.write("*RST")

    def set_center_freq(self, freq_hz: float) -> None:
        self.write(f"FREQ:CENT {freq_hz}")

    def set_span(self, span_hz: float) -> None:
        self.write(f"FREQ:SPAN {span_hz}")

    def set_rbw(self, rbw_hz: float) -> None:
        self.write(f"BAND:RES {rbw_hz}")

    def set_vbw(self, vbw_hz: float) -> None:
        self.write(f"BAND:VID {vbw_hz}")

    def peak_search(self) -> str:
        self.write("CALC:MARK:MAX")
        return self.query("CALC:MARK:Y?")

    def read_marker(self) -> str:
        return self.query("CALC:MARK:Y?")
