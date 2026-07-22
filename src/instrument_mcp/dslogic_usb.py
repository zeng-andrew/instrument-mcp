"""
Low-level USB controller for DreamSourceLab DSLogic U3Pro16.

Derived from DSView/libsigrok4DSL source:
  - libsigrok4DSL/hardware/DSL/dslogic.c
  - libsigrok4DSL/hardware/DSL/dsl.c
  - libsigrok4DSL/hardware/DSL/dsl.h
  - libsigrok4DSL/hardware/DSL/command.h

The U3Pro16 uses Cypress FX3 + Xilinx Spartan-6 FPGA.
USB descriptors:
  VID 0x2A0E, PID 0x002A, USB 3.0
  Interface 0 (Vendor Specific)
    EP 0x02  Bulk OUT
    EP 0x86  Bulk IN
"""

from __future__ import annotations

import logging
import math
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import usb.backend.libusb1
import usb.core
import usb.util

logger = logging.getLogger(__name__)

VID = 0x2A0E
PID = 0x002A
EP_OUT = 0x02
EP_IN = 0x86

CMD_CTL_WR = 0xB0
CMD_CTL_RD_PRE = 0xB1
CMD_CTL_RD = 0xB2

DSL_CTL_HW_STATUS = 2
DSL_CTL_PROG_B = 3
DSL_CTL_SYS = 4
DSL_CTL_LED = 5
DSL_CTL_INTRDY = 6
DSL_CTL_WORDWIDE = 7
DSL_CTL_START = 8
DSL_CTL_STOP = 9
DSL_CTL_BULK_WR = 10
DSL_CTL_REG = 11
DSL_CTL_NVM = 12
DSL_CTL_I2C_DSO = 13
DSL_CTL_I2C_REG = 14
DSL_CTL_I2C_STATUS = 15

VTH_ADDR = 0x78
EI2C_ADDR = 0x60
CTR0_ADDR = 0x70

bmGPIF_DONE = 1 << 7
bmFPGA_DONE = 1 << 6
bmFPGA_INIT_B = 1 << 5
bmSYS_OVERFLOW = 1 << 4
bmSYS_CLR = 1 << 3
bmSYS_EN = 1 << 2
bmLED_RED = 1 << 1
bmLED_GREEN = 1 << 0

bmWR_PROG_B = 1 << 2
bmWR_INTRDY = 1 << 7
bmWR_WORDWIDE = 1 << 0

TRIG_EN_BIT = 0
CLK_TYPE_BIT = 1
CLK_EDGE_BIT = 2
RLE_MODE_BIT = 3
DSO_MODE_BIT = 4
HALF_MODE_BIT = 5
QUAR_MODE_BIT = 6
ANALOG_MODE_BIT = 7
FILTER_BIT = 8
INSTANT_BIT = 9
SLOW_ACQ_BIT = 10
STRIG_MODE_BIT = 11
STREAM_MODE_BIT = 12
LPB_TEST_BIT = 13
EXT_TEST_BIT = 14
INT_TEST_BIT = 15

DSLOGIC_ATOMIC_BITS = 6
DSLOGIC_ATOMIC_SAMPLES = 1 << DSLOGIC_ATOMIC_BITS
DSLOGIC_ATOMIC_SIZE = 1 << (DSLOGIC_ATOMIC_BITS - 3)
DSLOGIC_ATOMIC_MASK = 0xFFFF << DSLOGIC_ATOMIC_BITS
SAMPLES_ALIGN = DSLOGIC_ATOMIC_SAMPLES - 1

TRIG_CHECKID = 0x55555555
NUM_TRIGGER_STAGES = 16

DSL_STREAM20x16_3DN2 = 4
DSL_STREAM25x12_3DN2 = 5
DSL_STREAM50x6_3DN2 = 6
DSL_STREAM100x3_3DN2 = 7
DSL_STREAM125x16_16 = 16
DSL_STREAM250x12_16 = 17
DSL_STREAM500x6 = 18
DSL_STREAM1000x3 = 19
DSL_BUFFER500x16 = 24
DSL_BUFFER1000x8 = 25


def SR_HZ(n: int) -> int:
    return n


def SR_KHZ(n: int) -> int:
    return n * 1000


def SR_MHZ(n: int) -> int:
    return n * 1000000


def SR_GHZ(n: int) -> int:
    return n * 1000000000


@dataclass
class ChannelMode:
    id: int
    stream: bool
    num: int
    vld_num: int
    unit_bits: int
    min_samplerate: int
    max_samplerate: int
    hw_min_samplerate: int
    hw_max_samplerate: int
    pre_div: int
    descr: str


U3PRO16_MODES = {
    DSL_STREAM125x16_16: ChannelMode(
        DSL_STREAM125x16_16, True, 16, 16, 1,
        SR_MHZ(1), SR_MHZ(125), SR_KHZ(10), SR_MHZ(500), 5,
        "Stream 16ch @ 125MHz"
    ),
    DSL_STREAM250x12_16: ChannelMode(
        DSL_STREAM250x12_16, True, 16, 12, 1,
        SR_MHZ(1), SR_MHZ(250), SR_KHZ(10), SR_MHZ(500), 5,
        "Stream 12ch @ 250MHz"
    ),
    DSL_STREAM500x6: ChannelMode(
        DSL_STREAM500x6, True, 16, 6, 1,
        SR_MHZ(1), SR_MHZ(500), SR_KHZ(10), SR_MHZ(500), 5,
        "Stream 6ch @ 500MHz"
    ),
    DSL_STREAM1000x3: ChannelMode(
        DSL_STREAM1000x3, True, 8, 3, 1,
        SR_MHZ(1), SR_GHZ(1), SR_KHZ(10), SR_MHZ(500), 5,
        "Stream 3ch @ 1GHz"
    ),
    DSL_BUFFER500x16: ChannelMode(
        DSL_BUFFER500x16, False, 16, 16, 1,
        SR_MHZ(1), SR_MHZ(500), SR_KHZ(10), SR_MHZ(500), 5,
        "Buffer 16ch @ 500MHz"
    ),
    DSL_BUFFER1000x8: ChannelMode(
        DSL_BUFFER1000x8, False, 8, 8, 1,
        SR_MHZ(1), SR_GHZ(1), SR_KHZ(10), SR_MHZ(500), 5,
        "Buffer 8ch @ 1GHz"
    ),
}


def _find_libusb_dll() -> Optional[str]:
    """Search for a 64-bit libusb-1.0.dll in common locations."""
    pkg_dir = Path(__file__).resolve().parent
    candidates = [
        pkg_dir / "libusb-1.0.dll",
        pkg_dir.parent / "libusb-1.0.dll",
        Path.cwd() / "libusb-1.0.dll",
        Path(r"D:\MCPTools\instrument-mcp\libusb-1.0.dll"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)

    # Search a few known program directories
    for base in [Path("D:/"), Path("C:/Program Files")]:
        try:
            for p in base.rglob("libusb-1.0.dll"):
                return str(p)
        except Exception:
            pass
    return None


class DSLogicU3Pro16USB:
    """Low-level USB controller for DSLogic U3Pro16."""

    def __init__(self, libusb_path: Optional[str] = None):
        if libusb_path is None:
            libusb_path = _find_libusb_dll()

        if libusb_path:
            backend = usb.backend.libusb1.get_backend(
                find_library=lambda _name: libusb_path
            )
        else:
            backend = usb.backend.libusb1.get_backend()

        if backend is None:
            raise RuntimeError(
                "No libusb-1.0 backend found. Place a 64-bit libusb-1.0.dll "
                "next to the package or in PATH."
            )

        self.backend = backend
        self.dev: Optional[usb.core.Device] = None
        self._claimed = False
        self._stream_active = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def open(self) -> None:
        dev = usb.core.find(idVendor=VID, idProduct=PID, backend=self.backend)
        if dev is None:
            raise RuntimeError(f"DSLogic U3Pro16 not found ({VID:04X}:{PID:04X})")

        self.dev = dev
        self.dev.set_configuration()
        usb.util.claim_interface(self.dev, 0)
        self._claimed = True

    def close(self) -> None:
        if self._stream_active:
            try:
                self.stop()
            except Exception:
                pass
        if self.dev and self._claimed:
            try:
                usb.util.release_interface(self.dev, 0)
            except Exception:
                pass
            self._claimed = False
        self.dev = None

    # ------------------------------------------------------------------
    # Low-level USB primitives
    # ------------------------------------------------------------------
    def _ctrl_write(self, b_request: int, data: bytes, w_value: int = 0, w_index: int = 0) -> int:
        return self.dev.ctrl_transfer(
            bmRequestType=0x40,
            bRequest=b_request,
            wValue=w_value,
            wIndex=w_index,
            data_or_wLength=data,
            timeout=3000,
        )

    def _ctrl_read(self, b_request: int, length: int, w_value: int = 0, w_index: int = 0) -> bytes:
        return bytes(self.dev.ctrl_transfer(
            bmRequestType=0xC0,
            bRequest=b_request,
            wValue=w_value,
            wIndex=w_index,
            data_or_wLength=length,
            timeout=3000,
        ))

    def _command_write(self, dest: int, data: bytes, offset: int = 0) -> None:
        header = struct.pack("<BHB", dest, offset, len(data))
        self._ctrl_write(CMD_CTL_WR, header + data)

    def _command_read(self, dest: int, offset: int, length: int) -> bytes:
        header = struct.pack("<BHB", dest, offset, length)
        self._ctrl_write(CMD_CTL_RD_PRE, header)
        time.sleep(0.01)
        return self._ctrl_read(CMD_CTL_RD, length)

    def _bulk_out(self, data: bytes, timeout: int = 3000) -> int:
        return self.dev.write(EP_OUT, data, timeout=timeout)

    def _bulk_in(self, length: int, timeout: int = 5000) -> bytes:
        return bytes(self.dev.read(EP_IN, length, timeout=timeout))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def read_hw_status(self) -> int:
        return self._command_read(DSL_CTL_HW_STATUS, 0, 1)[0]

    def write_reg(self, addr: int, value: int) -> None:
        self._command_write(DSL_CTL_I2C_REG, bytes([value]), offset=addr)

    def read_reg(self, addr: int) -> int:
        return self._command_read(DSL_CTL_I2C_STATUS, addr, 1)[0]

    def get_idn(self) -> str:
        """Return a VISA-style IDN string for auto-discovery."""
        try:
            return f"DreamSourceLab,{self.dev.product},{self.dev.serial_number},v2"
        except Exception:
            return "DreamSourceLab,DSLogic U3Pro16,,v2"

    def set_threshold(self, vth_volts: float, max25: bool = True) -> None:
        if max25:
            code = int(vth_volts / 3.3 * 0.5 * 255)
        else:
            code = int(vth_volts / 3.3 * (1.5 / 2.5) * 255)
        code = max(0, min(255, code))
        self.write_reg(VTH_ADDR, code)

    def load_fpga_bitstream(self, bitstream_path: str) -> None:
        path = Path(bitstream_path)
        if not path.exists():
            raise FileNotFoundError(bitstream_path)

        data = path.read_bytes()
        size = len(data)

        self._command_write(DSL_CTL_PROG_B, bytes([~bmWR_PROG_B & 0xFF]))
        self._command_write(DSL_CTL_PROG_B, bytes([bmWR_PROG_B]))
        self._command_write(DSL_CTL_INTRDY, bytes([~bmWR_INTRDY & 0xFF]))
        self._command_write(
            DSL_CTL_BULK_WR,
            bytes([size & 0xFF, (size >> 8) & 0xFF, (size >> 16) & 0xFF]),
        )
        transferred = self._bulk_out(data, timeout=10000)
        if transferred != size:
            raise RuntimeError(f"FPGA upload incomplete: {transferred}/{size}")
        self._command_write(DSL_CTL_INTRDY, bytes([bmWR_INTRDY]))

        for _ in range(100):
            if self.read_hw_status() & bmGPIF_DONE:
                return
            time.sleep(0.05)
        raise RuntimeError("FPGA configure timed out")

    # ------------------------------------------------------------------
    # FPGA setting builder
    # ------------------------------------------------------------------
    def _build_setting(
        self,
        mode: ChannelMode,
        samplerate: int,
        limit_samples: int,
        ch_en: int,
        trigger_en: bool = False,
        clock_type: bool = False,
        clock_edge: bool = False,
        rle_mode: bool = False,
        stream: bool = False,
        instant: bool = False,
    ) -> bytes:
        actual_samples = (limit_samples + SAMPLES_ALIGN) & ~SAMPLES_ALIGN

        mode_word = (
            (int(trigger_en) << TRIG_EN_BIT)
            | (int(clock_type) << CLK_TYPE_BIT)
            | (int(clock_edge) << CLK_EDGE_BIT)
            | (int(rle_mode) << RLE_MODE_BIT)
            | (int(stream) << STREAM_MODE_BIT)
            | (int(instant) << INSTANT_BIT)
        )

        ch_num = bin(ch_en).count("1")
        if ch_num == 0:
            ch_num = mode.num

        tmp_u32 = int(math.ceil(mode.hw_max_samplerate / samplerate))
        div_h = (
            (mode.pre_div - 1 if tmp_u32 >= mode.pre_div else tmp_u32 - 1) << 8
        ) & 0xFFFF
        tmp_u32 = int(math.ceil(tmp_u32 / mode.pre_div))
        div_l = tmp_u32 & 0xFFFF
        div_h = (div_h + (tmp_u32 >> 16)) & 0xFFFF

        cnt = actual_samples >> 4
        cnt_l = cnt & 0xFFFF
        cnt_h = (cnt >> 16) & 0xFFFF

        tpos = DSLOGIC_ATOMIC_SAMPLES
        tpos_l = tpos & 0xFFFF
        tpos_h = (tpos >> 16) & 0xFFFF

        trig_glb = ((ch_num & 0x1F) << 8) | (NUM_TRIGGER_STAGES & 0xFF)

        setting = struct.pack(
            "<IHHHHHHHHHHHHHHHHHHHHHH",
            0xF5A5F5A5,
            0x0001, mode_word & 0xFFFF,
            0x0102, div_l, div_h,
            0x0302, cnt_l, cnt_h,
            0x0502, tpos_l, tpos_h,
            0x0701, trig_glb,
            0x0802, 0, 0,
            0x0A02, ch_en & 0xFFFF, (ch_en >> 16) & 0xFFFF,
            0x0C01, 0,
            0x40A0,
        )

        for _ in range(NUM_TRIGGER_STAGES):
            setting += struct.pack("<H", 0)
        for _ in range(NUM_TRIGGER_STAGES):
            setting += struct.pack("<H", 0)
        for _ in range(NUM_TRIGGER_STAGES):
            setting += struct.pack("<H", 0)
        for _ in range(NUM_TRIGGER_STAGES):
            setting += struct.pack("<H", 0)
        for _ in range(NUM_TRIGGER_STAGES):
            setting += struct.pack("<H", 0)
        for _ in range(NUM_TRIGGER_STAGES):
            setting += struct.pack("<H", 0)
        for _ in range(NUM_TRIGGER_STAGES):
            setting += struct.pack("<H", 2)
        for _ in range(NUM_TRIGGER_STAGES):
            setting += struct.pack("<H", 2)
        for _ in range(NUM_TRIGGER_STAGES):
            setting += struct.pack("<I", 0)

        setting += struct.pack("<I", 0xFA5AFA5A)
        return setting

    # ------------------------------------------------------------------
    # Buffer mode
    # ------------------------------------------------------------------
    def arm_and_start(
        self,
        mode_id: int = DSL_BUFFER500x16,
        samplerate: int = SR_MHZ(100),
        limit_samples: int = 1024,
        ch_en: int = 0xFFFF,
    ) -> None:
        mode = U3PRO16_MODES[mode_id]

        self._command_write(DSL_CTL_STOP, b"")

        setting = self._build_setting(
            mode=mode,
            samplerate=samplerate,
            limit_samples=limit_samples,
            ch_en=ch_en,
            stream=False,
        )
        arm_size = len(setting) // 2

        self._command_write(
            DSL_CTL_BULK_WR,
            bytes([arm_size & 0xFF, (arm_size >> 8) & 0xFF, (arm_size >> 16) & 0xFF]),
        )

        for _ in range(200):
            if self.read_hw_status() & bmSYS_CLR:
                break
            time.sleep(0.005)
        else:
            raise RuntimeError("bmSYS_CLR never asserted")

        transferred = self._bulk_out(setting, timeout=3000)
        if transferred != len(setting):
            raise RuntimeError(f"Setting bulk OUT incomplete: {transferred}/{len(setting)}")

        self._command_write(DSL_CTL_INTRDY, bytes([bmWR_INTRDY]))

        for _ in range(200):
            if self.read_hw_status() & bmGPIF_DONE:
                break
            time.sleep(0.005)
        else:
            raise RuntimeError("GPIF_DONE never asserted after arm")

        self._command_write(DSL_CTL_START, b"")

    def read_capture(self, limit_samples: int, ch_en: int = 0xFFFF) -> bytes:
        ch_num = bin(ch_en).count("1") or 16
        actual_samples = (limit_samples + SAMPLES_ALIGN) & ~SAMPLES_ALIGN
        actual_bytes = actual_samples // DSLOGIC_ATOMIC_SAMPLES * ch_num * DSLOGIC_ATOMIC_SIZE

        header = self._bulk_in(1024, timeout=5000)
        if len(header) >= 4:
            check_id = struct.unpack_from("<I", header, 0)[0]
            if check_id != TRIG_CHECKID:
                logger.warning(f"Unexpected header check_id=0x{check_id:08X}")

        data = self._bulk_in(actual_bytes, timeout=10000)
        return data

    # ------------------------------------------------------------------
    # Stream mode
    # ------------------------------------------------------------------
    def start_stream(
        self,
        mode_id: int = DSL_STREAM125x16_16,
        samplerate: int = SR_MHZ(10),
        ch_en: int = 0xFFFF,
    ) -> None:
        mode = U3PRO16_MODES[mode_id]
        if not mode.stream:
            raise ValueError(f"Mode {mode_id} is not a streaming mode")

        limit_samples = 1 << 30

        self._command_write(DSL_CTL_STOP, b"")

        setting = self._build_setting(
            mode=mode,
            samplerate=samplerate,
            limit_samples=limit_samples,
            ch_en=ch_en,
            stream=True,
        )
        arm_size = len(setting) // 2

        self._command_write(
            DSL_CTL_BULK_WR,
            bytes([arm_size & 0xFF, (arm_size >> 8) & 0xFF, (arm_size >> 16) & 0xFF]),
        )

        for _ in range(200):
            if self.read_hw_status() & bmSYS_CLR:
                break
            time.sleep(0.005)
        else:
            raise RuntimeError("bmSYS_CLR never asserted")

        transferred = self._bulk_out(setting, timeout=3000)
        if transferred != len(setting):
            raise RuntimeError(f"Setting bulk OUT incomplete: {transferred}/{len(setting)}")

        self._command_write(DSL_CTL_INTRDY, bytes([bmWR_INTRDY]))

        for _ in range(200):
            if self.read_hw_status() & bmGPIF_DONE:
                break
            time.sleep(0.005)
        else:
            raise RuntimeError("GPIF_DONE never asserted after arm")

        self._command_write(DSL_CTL_START, b"")
        self._stream_active = True

    def stream_sync(
        self,
        callback: Callable[[bytes], bool],
        duration: Optional[float] = None,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        t0 = time.time()
        try:
            while True:
                if duration and (time.time() - t0) >= duration:
                    break
                try:
                    chunk = self._bulk_in(chunk_size, timeout=500)
                except usb.core.USBTimeoutError:
                    continue
                if not chunk:
                    continue
                if len(chunk) >= 4 and struct.unpack_from("<I", chunk, 0)[0] == TRIG_CHECKID:
                    chunk = chunk[1024:]
                if not chunk:
                    continue
                if callback(chunk) is False:
                    break
        except KeyboardInterrupt:
            logger.info("Stream interrupted by user")
        finally:
            self._stream_active = False

    def stop(self) -> None:
        self._command_write(DSL_CTL_STOP, b"")
        self._stream_active = False
