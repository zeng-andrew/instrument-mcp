"""独立仪器通信层，不依赖 Loom。"""

import logging
import struct
import threading
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


class _RingBuffer:
    """固定容量环形缓冲区（线程安全）。

    后台接收线程持续把串口字节写入本缓冲，前台按 Modbus 帧边界消费。
    写满时覆盖最旧字节并计数（overwritten），保证驱动层 RX 缓冲
    永远被及时腾空——残余数据堆积是 CH340 串口断连的常见诱因。
    """

    def __init__(self, capacity: int = 4096):
        self._buf = bytearray(capacity)
        self._cap = capacity
        self._head = 0    # 最旧字节下标
        self._size = 0    # 当前字节数
        self.overwritten = 0  # 被覆盖丢弃的字节总数
        self._lock = threading.Lock()
        self.cond = threading.Condition(self._lock)

    def write(self, data: bytes) -> None:
        with self.cond:
            for b in data:
                if self._size == self._cap:
                    self._head = (self._head + 1) % self._cap
                    self._size -= 1
                    self.overwritten += 1
                self._buf[(self._head + self._size) % self._cap] = b
                self._size += 1
            self.cond.notify_all()

    def snapshot(self) -> bytes:
        """返回当前内容副本。调用方须持有 self.cond。"""
        end = self._head + self._size
        if end <= self._cap:
            return bytes(self._buf[self._head:end])
        return bytes(self._buf[self._head:]) + bytes(self._buf[:end % self._cap])

    def discard(self, n: int) -> None:
        """丢弃最旧的 n 字节。调用方须持有 self.cond。"""
        n = min(n, self._size)
        self._head = (self._head + n) % self._cap
        self._size -= n


class SerialModbusInstrument:
    """基于 pyserial 的 Modbus-RTU 串口仪器（如 MODBUS-1 协议的恒温恒湿箱）。

    与 VisaInstrument 接口兼容（open/close/write/query），
    另提供 read_holding / write_register 供自定义 handler 使用。

    接收路径: open() 启动后台线程持续把串口字节读入环形缓冲区
    （_RingBuffer），transact() 在缓冲中按「站号+功能码+CRC」搜索
    完整帧并只消费该帧——帧前噪声丢弃并记日志，帧后多余数据留给
    下次事务。驱动层 RX 缓冲始终被腾空，残余/迟到数据不会造成
    帧错位或端口卡死。

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
        self._rb = _RingBuffer()
        self._rx_thread: Optional[threading.Thread] = None
        self._rx_stop = threading.Event()
        self._rx_error: Optional[Exception] = None

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
                self._rx_stop.clear()
                self._rx_error = None
                self._rx_thread = threading.Thread(
                    target=self._reader_loop, name=f"modbus-rx-{self.address}",
                    daemon=True,
                )
                self._rx_thread.start()
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
        self._rx_stop.set()
        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=self.timeout + 0.5)
        self._rx_thread = None
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

    def _reader_loop(self) -> None:
        """后台接收线程：持续把串口字节搬入环形缓冲区。

        注意不能用 read(N>1)：pyserial 会阻塞到读满 N 字节或超时，
        短帧（如 7 字节的 FC03 响应）会被扣住整个 timeout 才上交。
        先 read(1) 等首字节，再用 in_waiting 抓走其余已到达字节，
        剩余字节下一轮循环会立即补齐（帧组装在 transact 侧完成）。
        """
        while not self._rx_stop.is_set():
            try:
                chunk = self._ser.read(1)
                if chunk:
                    n = self._ser.in_waiting
                    if n:
                        chunk += self._ser.read(n)
            except Exception as e:
                if not self._rx_stop.is_set():
                    self._rx_error = e
                    with self._rb.cond:
                        self._rb.cond.notify_all()
                    logger.error(f"串口接收线程异常退出: {e}")
                return
            if chunk:
                self._rb.write(chunk)

    def _find_frame(self, data: bytes, fc: int):
        """在字节流中定位一帧合法响应（站号匹配 + 功能码匹配 + CRC 通过）。

        fc 为请求功能码（0x03/0x06/0x10），异常响应 fc|0x80 也接受。
        返回 (start, end)；流中尚不足一整帧时返回 None。
        """
        i, n = 0, len(data)
        while i + 5 <= n:  # 最短帧 = 异常帧 5 字节
            if data[i] != self.slave:
                i += 1
                continue
            f = data[i + 1]
            if f == (fc | 0x80):
                flen = 5
            elif f == fc == 0x06:
                flen = 8
            elif f == fc == 0x10:
                # 写多个寄存器的正常响应: 站号 + FC + 起始地址(2) + 数量(2) + CRC(2) = 8
                flen = 8
            elif f == fc == 0x03:
                bc = data[i + 2]
                if bc % 2 or bc > 250:
                    i += 1
                    continue
                flen = bc + 5
            else:
                i += 1
                continue
            if i + flen > n:
                return None  # 帧未收齐，等更多数据
            frame = data[i:i + flen]
            if struct.unpack("<H", frame[-2:])[0] == self.crc16(frame[:-2]):
                return i, i + flen
            i += 1  # CRC 不过，滑窗继续找
        return None

    def transact(self, pdu: bytes, timeout: float = 1.0) -> bytes:
        """发送 PDU（自动追加 CRC），从环形缓冲区中取回一帧校验通过的响应。

        帧前的噪声字节会被丢弃（记 warning）；帧后的多余字节留在缓冲
        中，由后续事务或下一次发送前的清理处理。
        """
        if self._ser is None:
            raise RuntimeError("Instrument not connected")
        frame = pdu + struct.pack("<H", self.crc16(pdu))
        fc = pdu[1]
        with self._rb.cond:
            stale = self._rb.snapshot()
            if stale:
                self._rb.discard(len(stale))
                logger.warning(
                    f"发送前丢弃残余字节 {len(stale)}B: {stale.hex(' ').upper()}"
                )
        self._ser.write(frame)
        self._ser.flush()
        deadline = time.time() + timeout
        with self._rb.cond:
            while True:
                if self._rx_error is not None:
                    err, self._rx_error = self._rx_error, None
                    raise RuntimeError(f"串口读取错误（可能已断连）: {err}")
                data = self._rb.snapshot()
                hit = self._find_frame(data, fc)
                if hit:
                    start, end = hit
                    if start:
                        logger.warning(
                            f"丢弃帧前噪声 {start}B: {data[:start].hex(' ').upper()}"
                        )
                    self._rb.discard(end)
                    return data[start:end]
                remaining = deadline - time.time()
                if remaining <= 0:
                    self._rb.discard(len(data))
                    raise RuntimeError(
                        f"等待响应超时 ({timeout}s)，缓冲残留: "
                        f"{data.hex(' ').upper() or '(empty)'}"
                    )
                self._rb.cond.wait(remaining)

    def read_holding(self, addr: int, qty: int = 1) -> list:
        """FC=03 读保持寄存器，返回寄存器值列表（无符号）。"""
        raw = self.transact(struct.pack(">BBHH", self.slave, 3, addr, qty))
        if len(raw) < 5 or raw[0] != self.slave:
            raise RuntimeError(f"无响应或站号不符: {raw.hex(' ').upper() or '(empty)'}")
        if raw[1] == 0x83:
            raise RuntimeError(f"Modbus 异常码: {raw[2]:02X}")
        if raw[1] != 0x03:
            raise RuntimeError(f"非 FC=03 响应: {raw.hex(' ').upper()}")
        bc = raw[2]
        return [struct.unpack(">H", raw[3 + i * 2:5 + i * 2])[0] for i in range(bc // 2)]

    def write_register(self, addr: int, val: int) -> None:
        """FC=06 写单个保持寄存器。"""
        raw = self.transact(struct.pack(">BBHH", self.slave, 6, addr, val & 0xFFFF))
        if len(raw) < 8 or raw[1] != 0x06:
            if len(raw) >= 5 and raw[1] == 0x86:
                raise RuntimeError(f"Modbus 异常码: {raw[2]:02X}")
            raise RuntimeError(f"写寄存器无有效回显: {raw.hex(' ').upper() or '(empty)'}")

    def write_registers(self, addr: int, values: list) -> None:
        """FC=10(16) 写多个连续保持寄存器（最多 100 个）。

        用于程序控制段表等批量写入场景。
        """
        if not values:
            raise RuntimeError("write_registers: 值列表为空")
        qty = len(values)
        if qty > 100:
            raise RuntimeError(f"write_registers: 数量 {qty} 超过上限 100")
        byte_count = qty * 2
        pdu = struct.pack(">BBHHB", self.slave, 0x10, addr, qty, byte_count)
        for v in values:
            pdu += struct.pack(">H", int(v) & 0xFFFF)
        raw = self.transact(pdu, timeout=1.5)
        if len(raw) < 8 or raw[1] != 0x10:
            if len(raw) >= 5 and raw[1] == 0x90:
                raise RuntimeError(f"Modbus 异常码: {raw[2]:02X}")
            raise RuntimeError(f"写多寄存器无有效回显: {raw.hex(' ').upper() or '(empty)'}")


class DSLogicInstrument:
    """DreamSourceLab DSLogic U3Pro16 USB 逻辑分析仪。

    与 VisaInstrument 保持 open/close/write/query 接口兼容，
    使 server.connect() 可以统一处理。
    """

    def __init__(self, address: str = "USB", timeout_ms: int = 10000):
        self.address = address
        self.timeout_ms = timeout_ms
        self._usb: Optional[object] = None

    def open(self) -> None:
        from instrument_mcp.dslogic_usb import DSLogicU3Pro16USB

        self._usb = DSLogicU3Pro16USB()
        self._usb.open()
        logger.info(f"DSLogic connected: {self._usb.dev}")

    def close(self) -> None:
        if self._usb:
            try:
                self._usb.close()
            except Exception as e:
                logger.warning(f"Error closing DSLogic: {e}")
            self._usb = None
        logger.info("DSLogic disconnected")

    def write(self, command: str) -> None:
        raise RuntimeError("DSLogic 不支持文本 write，请使用专用命令")

    def query(self, command: str) -> str:
        """模拟 SCPI *IDN?，用于 connect() 中的型号识别。"""
        if command.strip() == "*IDN?":
            if self._usb is None:
                raise RuntimeError("DSLogic not connected")
            return self._usb.get_idn()
        raise RuntimeError(f"DSLogic 不支持查询: {command}")

    def __getattr__(self, name: str):
        """透传所有未实现方法到底层 USB 控制器。"""
        if self._usb is None:
            raise RuntimeError("DSLogic not connected")
        return getattr(self._usb, name)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


# 仪器注册表：新增仪器时在此注册
INSTRUMENT_REGISTRY = {
    "mxa": (VisaInstrument, "Keysight MXA / EXA 系列频谱仪（通用 VISA 驱动）"),
    "keysight_ps": (VisaInstrument, "Keysight 66311B 直流电源"),
    "temperature_chamber": (VisaInstrument, "环境试验箱 / 温箱（Espec / Thermotron / CSZ 等）"),
    "modbus_chamber": (SerialModbusInstrument, "Modbus-RTU 恒温恒湿试验箱（MODBUS-1 协议, RS-232C）"),
    "dslogic": (DSLogicInstrument, "DreamSourceLab DSLogic U3Pro16 USB 逻辑分析仪"),
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
