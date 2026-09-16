"""温箱型号信息只读探测（COM43）。

目标：在不改变温箱运行状态的前提下，尽可能读出仪器型号/厂商/固件信息。

手段（全部为只读操作，零写入）：
  1. 基线 FC03 读运行标志/PV/SV —— 确认链路并记录当前状态（结束后复核未变）
  2. FC2B/MEI 0x0E Read Device Identification —— Modbus 标准设备标识
     （VendorName / ProductCode / Revision，多数现代固件支持 basic 级）
  3. FC04 读输入寄存器 0x0000 起若干块 —— 确认是否有独立输入寄存器表
  4. FC03 大范围扫描保持寄存器 0x0000~0x0FFF —— 逐寄存器对解码 ASCII，
     找型号/厂商字符串（程式名称 NAME 等证明该固件用寄存器存 ASCII）

安全原则：只发送 FC03/FC04/FC2B 读帧；不发送任何写帧、不做启停。
用法:
    .venv/Scripts/python.exe tests/chamber_identity_probe.py
"""
import struct
import sys
import time

sys.path.insert(0, "src")
from instrument_mcp.instruments import SerialModbusInstrument

PORT = "COM43"
BAUD = 9600
SLAVE = 1

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
INFO = "\033[36mINFO\033[0m"
WARN = "\033[33mWARN\033[0m"


def section(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def generic_find_frame(inst, data: bytes, fc: int):
    """通用帧定位：站号+功能码匹配后，用 CRC16 滑窗找完整帧。

    支持 _find_frame 原生不认识的响应（FC04/FC2B 等）：
    从站号匹配处开始，取最短的 CRC 校验通过的帧尾。
    """
    i, n = 0, len(data)
    while i + 4 <= n:
        if data[i] != inst.slave:
            i += 1
            continue
        if data[i + 1] not in (fc, fc | 0x80):
            i += 1
            continue
        for e in range(i + 4, n + 1):
            frame = data[i:e]
            if len(frame) >= 4 and struct.unpack(
                "<H", frame[-2:]
            )[0] == inst.crc16(frame[:-2]):
                return i, e
        i += 1
    return None


def raw_transact(inst, pdu: bytes, timeout: float = 1.0) -> bytes:
    """发送 PDU 并取回一帧（用通用帧定位，支持任意功能码）。"""
    raw = inst.transact(pdu, timeout=timeout)
    return raw


MEI_NAMES = {
    0x00: "VendorName",
    0x01: "ProductCode",
    0x02: "MajorMinorRevision",
}


def parse_device_id(resp: bytes):
    """解析 FC2B/MEI0x0E 响应，返回 [(object_id, name, value)]。"""
    # resp: slave fc mei code conformity morefollows nextobj (objid len data)* crc
    p = 1 + 1 + 1 + 1 + 1  # slave fc mei conformity 之后是 more/nextobj
    conformity = resp[3]
    objs = []
    i = p + 2  # 跳过 more_follows + next_object_id
    while i + 2 <= len(resp) - 2:
        obj_id = resp[i]
        ln = resp[i + 1]
        val = resp[i + 2:i + 2 + ln]
        if len(val) < ln:
            break
        objs.append((obj_id, MEI_NAMES.get(obj_id, f"Obj{obj_id}"),
                     val.decode("ascii", "replace")))
        i += 2 + ln
    return conformity, objs


def probe_device_id(inst):
    section("FC2B Read Device Identification（Modbus 标准设备标识）")
    found_any = False
    for code in (0x01, 0x02, 0x03, 0x04):
        pdu = struct.pack(">BBBBB", SLAVE, 0x2B, 0x0E, code, 0x00)
        try:
            raw = raw_transact(inst, pdu, timeout=1.0)
        except Exception as e:
            print(f"  [{INFO}] ReadDevId code={code:#04x}: 无响应/超时 "
                  f"({type(e).__name__})")
            continue
        if raw[1] == 0x8B:
            print(f"  [{WARN}] ReadDevId code={code:#04x}: 异常码 "
                  f"{raw[2]:02X}（设备不支持）")
            continue
        if raw[1] != 0x2B or len(raw) < 7:
            print(f"  [{WARN}] code={code:#04x}: 非预期响应 {raw.hex(' ').upper()}")
            continue
        try:
            conformity, objs = parse_device_id(raw)
        except Exception as e:
            print(f"  [{WARN}] code={code:#04x}: 解析失败 {e} — "
                  f"{raw.hex(' ').upper()}")
            continue
        if objs:
            found_any = True
            print(f"  [{PASS}] ReadDevId code={code:#04x} conformity="
                  f"{conformity:#04x}:")
            for obj_id, name, val in objs:
                print(f"        {name:<22} = {val!r}")
    if not found_any:
        print(f"  [{INFO}] 该固件未实现标准设备标识（国产控制器常见）")
    return found_any


def probe_fc04(inst):
    section("FC04 输入寄存器探测（0x0000 起，检查是否有独立输入表）")
    got = False
    for addr, qty in ((0x0000, 20), (0x0001, 6)):
        pdu = struct.pack(">BBHH", SLAVE, 4, addr, qty)
        try:
            raw = raw_transact(inst, pdu, timeout=1.0)
        except Exception as e:
            print(f"  [{INFO}] FC04 @0x{addr:04X}: 无响应/超时 ({type(e).__name__})")
            continue
        if raw[1] == 0x84:
            print(f"  [{WARN}] FC04 @0x{addr:04X}: Modbus 异常码 {raw[2]:02X}")
            continue
        if raw[1] != 4:
            print(f"  [{WARN}] FC04 @0x{addr:04X}: 非预期响应 {raw.hex(' ').upper()}")
            continue
        bc = raw[2]
        vals = [struct.unpack(">H", raw[3 + i*2:5 + i*2])[0] for i in range(bc // 2)]
        got = True
        print(f"  [{PASS}] FC04 @0x{addr:04X} x{qty}: {vals}")
    if not got:
        print(f"  [{INFO}] FC04 不可用 — 寄存器表仅在 FC03 保持寄存器")
    return got


def scan_holdings_for_ascii(inst):
    section("FC03 保持寄存器大范围扫描 — 查找 ASCII 型号/厂商字符串")
    strings = []
    raw_dump = {}
    BLOCK = 50
    exception_counts = {}
    t0 = time.time()
    for base in range(0x0000, 0x1000, BLOCK):
        addr = base
        qty = BLOCK
        pdu = struct.pack(">BBHH", SLAVE, 3, addr, qty)
        try:
            raw = raw_transact(inst, pdu, timeout=1.0)
        except Exception:
            exception_counts["timeout"] = exception_counts.get("timeout", 0) + 1
            continue
        if raw[1] == 0x83:
            exception_counts[raw[2]] = exception_counts.get(raw[2], 0) + 1
            continue
        if raw[1] != 3:
            continue
        bc = raw[2]
        vals = [struct.unpack(">H", raw[3 + i*2:5 + i*2])[0] for i in range(bc // 2)]
        for k, v in enumerate(vals):
            raw_dump[addr + k] = v
        # 寄存器对 → 4 字节（高字在前，Modbus/该固件 NAME 约定），找可打印串
        stream = b"".join(struct.pack(">H", v) for v in vals)
        run = bytearray()
        start_idx = None
        for j, ch in enumerate(stream):
            if 0x20 <= ch <= 0x7E:
                if not run:
                    start_idx = j
                run.append(ch)
            else:
                if len(run) >= 3:
                    reg = addr + start_idx // 2
                    strings.append((reg, run.decode("ascii")))
                run = bytearray()
        if len(run) >= 3:
            reg = addr + start_idx // 2
            strings.append((reg, run.decode("ascii")))
    dt = time.time() - t0
    print(f"  {INFO} 扫描 0x0000~0x0FFF 完成，耗时 {dt:.1f}s，"
          f"可读寄存器 {len(raw_dump)} 个")
    if exception_counts:
        print(f"  {INFO} 异常/超时统计: {exception_counts}")

    # 合并跨块重复报告的相邻串
    merged = []
    for reg, s in strings:
        if merged and reg <= merged[-1][0] + len(merged[-1][1]) // 2 + 2 \
                and s == merged[-1][1][:len(s)]:
            continue
        merged.append((reg, s))
    if merged:
        print(f"  [{PASS}] 发现 {len(merged)} 处 ASCII 字符串:")
        for reg, s in merged:
            print(f"        reg 0x{reg:04X} ({reg}): {s!r}")
    else:
        print(f"  [{INFO}] 未发现 ≥3 字符的 ASCII 串")

    # 寄存器 0 与预留区原始值（型号可能以 BCD/整数存于非常规位置）
    section("关键寄存器原始值（预留区/低位区，供人工判读）")
    for reg in sorted(raw_dump):
        if reg <= 60 or 46 <= reg <= 49:
            v = raw_dump[reg]
            hi, lo = v >> 8, v & 0xFF
            printable = ""
            if 0x20 <= hi <= 0x7E and 0x20 <= lo <= 0x7E:
                printable = f"  ascii={chr(hi)!r}{chr(lo)!r}"
            print(f"    reg 0x{reg:04X} ({reg:>4}) = {v:>6} "
                  f"(0x{v:04X}){printable}")
    return raw_dump, strings


def baseline(inst, label):
    flag = inst.read_holding(0x000A, 1)[0]
    pv = struct.unpack(">h", struct.pack(">H", inst.read_holding(0x0001, 1)[0]))[0] / 100.0
    sv = struct.unpack(">h", struct.pack(">H", inst.read_holding(0x0066, 1)[0]))[0] / 100.0
    print(f"  {INFO} [{label}] 运行标志={flag}  PV={pv:.2f}°C  SV={sv:.2f}°C")
    return flag, pv, sv


def main():
    section("温箱型号信息只读探测")
    print(f"端口: {PORT} @ {BAUD} 8N1 站号{SLAVE} — 全程只读，不写任何寄存器")

    inst = SerialModbusInstrument(address=PORT, baud_rate=BAUD, slave=SLAVE)
    inst.open()
    # 用通用帧定位替换原生 _find_frame（支持 FC04/FC2B 等任意功能码响应）
    inst._find_frame = lambda data, fc: generic_find_frame(inst, data, fc)

    try:
        b0 = baseline(inst, "开始")
        probe_device_id(inst)
        probe_fc04(inst)
        scan_holdings_for_ascii(inst)
        b1 = baseline(inst, "结束复核")
        if b0 == b1:
            print(f"  [{PASS}] 运行状态未变化: flag={b1[0]} pv={b1[1]:.2f}°C")
        else:
            print(f"  [{WARN}] 运行状态有变化（温箱自身运行所致，非本脚本写入）")
    finally:
        inst.close()

    section("探测完成")
    print(f"  {INFO} 所有探测均为只读（FC03/FC04/FC2B），未发送任何写帧")


if __name__ == "__main__":
    main()
