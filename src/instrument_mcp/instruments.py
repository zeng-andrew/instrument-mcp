"""独立仪器通信层，不依赖 Loom。"""

import logging
import time
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


class SerialModbusInstrument:
    """基于 pyserial 的 Modbus-RTU 串口仪器（如 MODBUS-1 协议的恒温恒湿箱）。

    与 VisaInstrument 接口兼容（open/close/write/query），
    另提供 read_holding / write_register 供自定义 handler 使用。

    说明: 本机 CH340 经常报 ERROR_GEN_FAILURE(31)，需先用其它波特率
    "唤醒"端口再以目标波特率打开，open() 内置了该重试逻辑。
    """

    def __init__(self, address: str = "COM30", baud_rate: int = 9600,
                 slave: int = 1, timeout: float = 0.5):
        self.address = address
        self.baud_rate = baud_rate
        self.slave = slave
        self.timeout = timeout
        self._ser = None

    def open(self) -> None:
        import serial
        last = None
        for _ in range(6):
            try:
                self._ser = serial.Serial(
                    self.address, self.baud_rate, bytesize=8,
                    parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                    timeout=self.timeout,
                )
                logger.info(f"Modbus-RTU connected: {self.address} @ {self.baud_rate}")
                return
            except Exception as e:
                last = e
                for kick in (4800, 2400):
                    try:
                        serial.Serial(self.address, kick, timeout=0.2).close()
                    except Exception:
                        pass
                time.sleep(0.5)
        raise RuntimeError(f"无法打开 {self.address}: {last}")

    def close(self) -> None:
        if self._ser:
            try:
                self._ser.close()
            except Exception as e:
                logger.warning(f"Error closing serial port: {e}")
            self._ser = None
        logger.info(f"Modbus-RTU disconnected: {self.address}")

    def write(self, command: str) -> None:
        raise RuntimeError("Modbus-RTU 仪器不支持文本 write")

    def query(self, command: str) -> str:
        raise RuntimeError("Modbus-RTU 仪器不支持文本 query")

    @staticmethod
    def crc16(data: bytes) -> int:
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if (crc & 1) else (crc >> 1)
        return crc

    def transact(self, pdu: bytes, settle: float = 0.3) -> bytes:
        """发送 PDU（自动追加 CRC），返回原始响应字节。"""
        import struct
        if self._ser is None:
            raise RuntimeError("Instrument not connected")
        frame = pdu + struct.pack("<H", self.crc16(pdu))
        self._ser.reset_input_buffer()
        self._ser.write(frame)
        self._ser.flush()
        time.sleep(settle)
        raw = b""
        end = time.time() + 0.6
        while time.time() < end:
            n = self._ser.in_waiting
            if n:
                raw += self._ser.read(n)
                end = time.time() + 0.1
            else:
                time.sleep(0.01)
        return raw

    def read_holding(self, addr: int, qty: int = 1) -> list:
        """FC=03 读保持寄存器，返回寄存器值列表（无符号）。"""
        import struct
        raw = self.transact(struct.pack(">BBHH", self.slave, 3, addr, qty))
        if len(raw) < 5 or raw[0] != self.slave:
            raise RuntimeError(f"无响应或站号不符: {raw.hex(' ').upper() or '(empty)'}")
        if raw[1] == 0x83:
            raise RuntimeError(f"Modbus 异常码: {raw[2]:02X}")
        if raw[1] != 0x03:
            raise RuntimeError(f"非 FC=03 响应: {raw.hex(' ').upper()}")
        if struct.unpack("<H", raw[-2:])[0] != self.crc16(raw[:-2]):
            raise RuntimeError(f"CRC 校验失败: {raw.hex(' ').upper()}")
        bc = raw[2]
        return [struct.unpack(">H", raw[3 + i * 2:5 + i * 2])[0] for i in range(bc // 2)]

    def write_register(self, addr: int, val: int) -> None:
        """FC=06 写单个保持寄存器。"""
        import struct
        raw = self.transact(struct.pack(">BBHH", self.slave, 6, addr, val & 0xFFFF))
        if len(raw) < 8 or raw[1] != 0x06:
            if len(raw) >= 5 and raw[1] == 0x86:
                raise RuntimeError(f"Modbus 异常码: {raw[2]:02X}")
            raise RuntimeError(f"写寄存器无有效回显: {raw.hex(' ').upper() or '(empty)'}")


# 仪器注册表：新增仪器时在此注册
INSTRUMENT_REGISTRY = {
    "mxa": (VisaInstrument, "Keysight MXA / EXA 系列频谱仪（通用 VISA 驱动）"),
    "keysight_ps": (VisaInstrument, "Keysight 66311B 直流电源"),
    "temperature_chamber": (VisaInstrument, "环境试验箱 / 温箱（Espec / Thermotron / CSZ 等）"),
    "modbus_chamber": (SerialModbusInstrument, "Modbus-RTU 恒温恒湿试验箱（MODBUS-1 协议, RS-232C）"),
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
