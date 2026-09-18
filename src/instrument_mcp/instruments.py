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
        if "SOCKET" in self.address.upper():
            # TCPIP SOCKET 资源必须显式启用终止符，否则 viRead 等不到协议层 END
            # 会一直挂到超时（数据早已在缓冲区）——CMW500 的 5025 socket 实测如此；
            # VXI-11/HiSLIP 仪器（MXA 等）有原生 END 标志，不需要也不能一概设置
            self._resource.read_termination = "\n"  # type: ignore
            self._resource.write_termination = "\n"  # type: ignore
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


def _patch_ch340_error31() -> None:
    """运行时规避 CH340 USB-转串口驱动的虚假 error 31。

    CH340 驱动对 SetCommState 误报 ERROR_GEN_FAILURE(31)——即使把
    GetCommState 读出的 DCB 原样写回也持续失败（实测 10/10 次），但串口
    实际收发完全正常。pyserial 原版在 _reconfigure_port() 中对此直接
    raise SerialException，把好端口误判为配置失败、中断连接。

    本函数在导入时（Windows 平台）把 pyserial 的 _reconfigure_port 包一层：
    SetCommState 失败且错误码为 31 时忽略（仅记 debug 日志），其余异常照常
    抛出。幂等——多次调用只 patch 一次。修复随本模块代码走，重建虚拟环境
    后无需任何额外操作（不再依赖改 .venv 的 serialwin32.py 文件）。

    详见 docs/CH340_error31_fix.md。
    """
    import sys
    if sys.platform != "win32":
        return
    try:
        import serial.serialwin32 as _sw
        from serial.serialutil import SerialException
    except ImportError:
        return  # 非 Windows 后端（如 posix），无 serialwin32
    if getattr(_sw, "_ch340_err31_patched", False):
        return
    _orig_reconfigure = _sw.Serial._reconfigure_port

    def _reconfigure_ignoring_ch340_err31(self):
        try:
            _orig_reconfigure(self)
        except SerialException as e:
            # CH340 误报 error 31 时消息形如
            # "...Original message: OSError(31, '资源数据不足', None, 31)"
            if "31" in str(e):
                logger.debug(
                    "忽略 CH340 SetCommState 误报 error 31（端口实际可用）: %s", e
                )
                return
            raise

    _sw.Serial._reconfigure_port = _reconfigure_ignoring_ch340_err31
    _sw._ch340_err31_patched = True
    logger.debug("已应用 CH340 error31 运行时补丁（pyserial _reconfigure_port）")


# 导入本模块即自动规避 CH340 error31（仅 Windows 生效，幂等）
_patch_ch340_error31()


class SerialModbusInstrument:
    """基于 pyserial 的 Modbus-RTU 串口仪器（如 MODBUS-1 协议的恒温恒湿箱）。

    与 VisaInstrument 接口兼容（open/close/write/query），
    另提供 read_holding / write_register 供自定义 handler 使用。

    接收路径: open() 启动后台线程持续把串口字节读入环形缓冲区
    （_RingBuffer），transact() 在缓冲中按「站号+功能码+CRC」搜索
    完整帧并只消费该帧——帧前噪声丢弃并记日志，帧后多余数据留给
    下次事务。驱动层 RX 缓冲始终被腾空，残余/迟到数据不会造成
    帧错位或端口卡死。

    CH340 error 31: 本机 CH340 USB-转串口驱动对 SetCommState 持续误报
    ERROR_GEN_FAILURE(31)（端口实际可用）。导入本模块时已通过
    _patch_ch340_error31() 运行时 patch pyserial 自动规避，无需手动改
    .venv。open() 另内置少量重试，应对端口被占用等其他打开失败（驱动
    卡死无法靠重试恢复，须 restart_ch340.bat 重启设备）。
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
        # CH340 的 SetCommState 持续误报 error 31 已由 _patch_ch340_error31()
        # 在 pyserial 配置阶段解决，正常情况首次打开即成功。此处重试仅作
        # 为对端口被占用 / 物理断开等其他打开失败的退避（驱动卡死无法靠
        # 重试恢复，须 restart_ch340.bat 重启设备）。
        last = None
        for _ in range(3):
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


class TinySAInstrument:
    """tinySA / tinySA Ultra+（Zeeenko ZS-407）频谱仪，USB 串口行协议。

    与 VisaInstrument 保持 open/close/write/query 接口兼容，使 server.connect()
    可以统一处理。协议要点（详见 tinysa.org/wiki USBInterface 页面）：
    - 文本命令以 \\r\\n 结尾；设备先回显命令，响应以 "ch> " 提示符结束
    - 频率可用整数或 k/M/G 后缀（如 300M），电平单位 dBm
    - scanraw 返回二进制："{" + 每点 ("x" LSB MSB 16bit) + "}"
      dBm = raw/32 - 174（tinySA4 / Ultra 系）
    - capture 返回 480x320 像素 RGB565（小端 2 字节/像素）= 307200 字节，
      随后跟随 "ch> " 提示符
    - 经 USB 连接（STM32 USB CDC, VID 0483）时波特率仅示意，数据按 USB 速度
      传输；经 UART 引脚连接时按波特率传输
    """

    LCD_WIDTH = 480
    LCD_HEIGHT = 320

    def __init__(self, address: str = "COM44", baud_rate: int = 115200,
                 timeout: float = 0.5):
        self.address = address
        self.baud_rate = baud_rate
        self.timeout = timeout
        self._ser = None

    def open(self) -> None:
        import serial
        last = None
        for _ in range(3):
            try:
                self._ser = serial.Serial(
                    self.address, self.baud_rate, bytesize=8,
                    parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                    timeout=self.timeout,
                )
                try:
                    # USB CDC 突发传输很快，放大接收缓冲避免丢字节
                    self._ser.set_buffer_size(rx_size=4 * 1024 * 1024, tx_size=4096)
                except Exception:
                    pass
                time.sleep(0.3)
                self._ser.reset_input_buffer()
                logger.info(f"tinySA connected: {self.address} @ {self.baud_rate}")
                return
            except Exception as e:
                last = e
                time.sleep(0.5)
        raise RuntimeError(f"无法打开 {self.address}: {last}")

    def close(self) -> None:
        if self._ser:
            try:
                self._ser.close()
            except Exception as e:
                logger.warning(f"Error closing serial port: {e}")
            self._ser = None
        logger.info(f"tinySA disconnected: {self.address}")

    def _send(self, command: str) -> None:
        if self._ser is None:
            raise RuntimeError("Instrument not connected")
        cmd = command.strip()
        if not cmd:
            return
        self._ser.write((cmd + "\r\n").encode("ascii"))

    def _consume_echo(self, command: str, timeout: float = 2.0) -> None:
        """读取并丢弃设备回显的命令行（{command}\\r\\n）。"""
        want = command.encode("ascii") + b"\r\n"
        seen = bytearray()
        deadline = time.time() + timeout
        while time.time() < deadline:
            b = self._ser.read(1)
            if not b:
                continue
            seen.append(b[0])
            idx = seen.find(want)
            if idx >= 0:
                if idx:
                    logger.warning(f"回显前多余数据 {idx}B: {bytes(seen[:idx])!r}")
                return
        logger.warning(f"未匹配到命令回显 {command!r}，已读 {bytes(seen)!r}")

    def _read_until_prompt(self, timeout: float = 8.0) -> bytes:
        """读取串口直到 "ch> " 提示符，返回提示符之前的全部字节。"""
        buf = bytearray()
        marker = b"ch> "
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self._ser.read(4096)
            if chunk:
                buf += chunk
                idx = buf.find(marker)
                if idx >= 0:
                    return bytes(buf[:idx])
        raise TimeoutError(
            f"等待 'ch> ' 提示符超时 ({timeout}s)，已收 {len(buf)} 字节"
        )

    def _get_idn(self) -> str:
        """*IDN? 模拟：返回 info 输出的型号与版本信息首行。"""
        self._send("info")
        self._consume_echo("info")
        data = self._read_until_prompt(8.0)
        lines = data.decode("ascii", errors="replace").splitlines()
        return "\n".join(lines[:4])

    def write(self, command: str) -> None:
        self._send(command)
        self._consume_echo(command)

    def query(self, command: str, timeout: float = 8.0) -> str:
        if command.strip().upper() == "*IDN?":
            return self._get_idn()
        self._send(command)
        self._consume_echo(command)
        data = self._read_until_prompt(timeout)
        return data.decode("ascii", errors="replace").strip()

    def raw_query(self, command: str, timeout: float = 15.0) -> bytes:
        """发送命令并返回去除回显与提示符的原始字节（二进制安全）。

        注意: 仅适用于响应中不会出现 "ch> " 字节序列的命令
        （scanraw 的电平原始值远达不到 0x68 高字节，可安全使用）。
        """
        self._send(command)
        self._consume_echo(command)
        return self._read_until_prompt(timeout)

    def read_exact(self, n: int, timeout: float = 15.0) -> bytes:
        """读取精确 n 字节（用于 capture 等纯二进制传输）。"""
        buf = bytearray()
        deadline = time.time() + timeout
        while len(buf) < n and time.time() < deadline:
            chunk = self._ser.read(n - len(buf))
            if chunk:
                buf += chunk
        if len(buf) < n:
            raise TimeoutError(f"读取 {n} 字节超时 ({timeout}s)，仅收到 {len(buf)}")
        return bytes(buf)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


class MiPlugInstrument:
    """米家智能插座2（chuangmi.plug.212a01），LAN miIO/MIoT 协议（UDP，token 加密）。

    与 VisaInstrument 保持 open/close/write/query 接口兼容，使 server.connect()
    可以统一处理。miIO 是无连接 UDP 协议，open() 仅做握手验证（info()），
    不持有连接；close() 置空即可。

    凭据解析顺序：构造函数参数 > 环境变量 MI_PLUG_CONFIG 指定的配置文件 >
    cwd 下 mi_plug_config.json（模板 mi_plug_config.example.json）。
    完整属性表与坑见 docs/miplug_chuangmi_212a01.md。
    """

    def __init__(self, address: str = "", token: str = "", timeout: float = 5.0):
        cfg = self._load_config()
        self.address = address or cfg.get("ip", "")
        self.token = token or cfg.get("token", "")
        self.timeout = timeout
        if not self.address or not self.token:
            raise RuntimeError(
                "缺少插座 IP/token：通过 connect(address=..., token=...) 传入，"
                "或在 cwd 放置 mi_plug_config.json（模板见 mi_plug_config.example.json，"
                "token 提取方式见 docs/miplug_chuangmi_212a01.md）"
            )
        self._device: Optional[object] = None

    @staticmethod
    def _load_config() -> dict:
        import json
        import os
        from pathlib import Path

        path = os.environ.get("MI_PLUG_CONFIG")
        candidates = [Path(path)] if path else []
        candidates.append(Path(os.getcwd()) / "mi_plug_config.json")
        for p in candidates:
            if p.is_file():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"读取 {p} 失败: {e}")
        return {}

    def open(self) -> None:
        import warnings
        warnings.filterwarnings("ignore", category=FutureWarning)  # miio 0.5.12 on Py3.13
        from miio import MiotDevice

        # "Neither the class nor the parameter defines the mapping"：
        # 212a01 在 python-miio 里没有官方 mapping，只用 get/set_property_by
        self._device = MiotDevice(self.address, self.token)
        try:
            self._device.info()  # miIO info 走 token 加密通道，能返回即证明 token 正确
        except Exception as e:
            self._device = None
            raise RuntimeError(
                f"米家插座握手失败（{self.address}）: {e}（检查 IP/token、是否同网段）"
            )
        logger.info(f"MiPlug connected: {self.address}")

    def close(self) -> None:
        self._device = None
        logger.info(f"MiPlug disconnected: {self.address}")

    def write(self, command: str) -> None:
        raise RuntimeError("米家插座不支持文本 write，请使用 miplug_* 命令")

    def query(self, command: str) -> str:
        """模拟 SCPI *IDN?，用于 connect() 中的型号识别。"""
        if command.strip() == "*IDN?":
            if self._device is None:
                raise RuntimeError("MiPlug not connected")
            info = self._device.info()
            raw = getattr(info, "data", {})  # 0.5.12 的 DeviceInfo 属性不全，读原始响应
            return (
                f"Xiaomi,{info.model},{info.firmware_version},"
                f"{raw.get('mac', '?')}"
            )
        raise RuntimeError(f"米家插座不支持查询: {command}，请使用 miplug_* 命令")

    @staticmethod
    def _unwrap(res):
        """get_property_by 返回 list[CIResult]（0.6+）或 list[dict]，统一取出 value。"""
        item = res[0] if isinstance(res, (list, tuple)) else res
        if isinstance(item, dict):
            return item.get("value")
        return getattr(item, "value", item)

    def read_prop(self, siid: int, piid: int):
        """按 MIoT spec 读属性。"""
        if self._device is None:
            raise RuntimeError("MiPlug not connected")
        return self._unwrap(self._device.get_property_by(siid, piid))

    def set_prop(self, siid: int, piid: int, value) -> None:
        """按 MIoT spec 写属性。"""
        if self._device is None:
            raise RuntimeError("MiPlug not connected")
        self._device.set_property_by(siid, piid, value)

    def device_info(self):
        """返回 miio DeviceInfo（open() 之后可用）。"""
        if self._device is None:
            raise RuntimeError("MiPlug not connected")
        return self._device.info()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()


# 仪器注册表：新增仪器时在此注册
INSTRUMENT_REGISTRY = {
    "mxa": (VisaInstrument, "Keysight MXA / EXA 系列频谱仪（通用 VISA 驱动）"),
    "cmw": (VisaInstrument, "R&S CMW500 无线通信测试仪（TCPIP0::<ip>::5025::SOCKET）"),
    "keysight_ps": (VisaInstrument, "Keysight 66311B 直流电源"),
    "temperature_chamber": (VisaInstrument, "环境试验箱 / 温箱（Espec / Thermotron / CSZ 等）"),
    "modbus_chamber": (SerialModbusInstrument, "Modbus-RTU 恒温恒湿试验箱（MODBUS-1 协议, RS-232C）"),
    "dslogic": (DSLogicInstrument, "DreamSourceLab DSLogic U3Pro16 USB 逻辑分析仪"),
    "tinysa": (TinySAInstrument, "tinySA / tinySA Ultra+（Zeeenko ZS-407）频谱仪（USB 串口协议）"),
    "mi_plug": (MiPlugInstrument, "米家智能插座2 (chuangmi.plug.212a01)，LAN miIO/MIoT 协议"),
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
