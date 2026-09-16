"""精确复现日志中的 PROG4 段表读取命令。

日志显示 0x05DD (时间块) 读取超时，前后命令正常。本脚本逐条复现日志
中的命令序列，并扩展测试各块/各数量，定位超时根因。

日志命令对照:
  01 03 05 DD 00 14  -> FC03 读 0x05DD, qty=20  (时间块)  [超时]
  01 03 06 43 00 01  -> FC03 读 0x0643, qty=1   (循环块)  [正常]
  01 03 00 1A 00 01  -> FC03 读 0x001A, qty=1   (段号)    [正常]
  01 03 00 23 00 05  -> FC03 读 0x0023, qty=5   (段状态)  [正常]
  01 03 00 0A 00 01  -> FC03 读 0x000A, qty=1   (运行标志)[正常]
  01 06 00 65 00 04  -> FC06 写 0x0065=4 (停止)            [正常]
"""
import logging
import sys
import time

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

sys.path.insert(0, "src")
from instrument_mcp.instruments import SerialModbusInstrument
from instrument_mcp.commands.modbus_chamber_handler import (
    REG_PROG_SEL, REG_RUN_FLAG, REG_RUN_CTRL, RUN_FIX, RUN_STOP,
    REG_TBL_TEMP, REG_TBL_HUM, REG_TBL_TIME, REG_TBL_CYCLE,
    REG_TBL_TS1, REG_TBL_TS2, REG_TBL_TS3, REG_TBL_TS4, _signed,
)

PORT = "COM43"
BAUD = 9600
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
INFO = "\033[36mINFO\033[0m"
WARN = "\033[33mWARN\033[0m"


def section(t):
    print(f"\n{'='*70}\n{t}\n{'='*70}")


def open_inst():
    inst = SerialModbusInstrument(address=PORT, baud_rate=BAUD)
    inst.open()
    return inst


def try_read(inst, label, addr, qty, timeout=5.0):
    """单次读测试，返回 (ok, value, elapsed_ms, err)。"""
    t0 = time.time()
    try:
        vals = inst.read_holding(addr, qty)
        dt = (time.time() - t0) * 1000
        print(f"  [{PASS}] {label}: addr=0x{addr:04X} qty={qty} -> {len(vals)} regs  ({dt:.0f}ms)")
        return True, vals, dt, None
    except Exception as e:
        dt = (time.time() - t0) * 1000
        print(f"  [{FAIL}] {label}: addr=0x{addr:04X} qty={qty} -> {repr(e)}  ({dt:.0f}ms)")
        return False, None, dt, repr(e)


def main():
    section("精确复现日志 PROG4 段表读取")
    inst = open_inst()

    # 选 PROG4
    print(f"  {INFO} 选择 PROG4 (写 0x0064=4)")
    inst.write_register(REG_PROG_SEL, 4)
    time.sleep(0.3)

    section("A. 逐条复现日志命令（addr/qty 完全一致）")
    # 日志中超时的那条：0x05DD qty 20
    ok_time, _, _, _ = try_read(inst, "时间块 0x05DD qty=20 [日志超时项]", 0x05DD, 20)
    # 日志中正常的
    try_read(inst, "循环块 0x0643 qty=1", 0x0643, 1)
    try_read(inst, "段号 0x001A qty=1", 0x001A, 1)
    try_read(inst, "段状态 0x0023 qty=5", 0x0023, 5)
    try_read(inst, "运行标志 0x000A qty=1", 0x000A, 1)

    section("B. 重试超时项 5 次（看是偶发还是稳定超时）")
    hits = 0
    for i in range(5):
        ok, _, _, _ = try_read(inst, f"时间块 retry {i+1}", 0x05DD, 20)
        if ok:
            hits += 1
        time.sleep(0.2)
    print(f"  {INFO} 时间块 0x05DD qty=20: {hits}/5 成功")

    section("C. 测试时间块不同数量（1/5/10/20/50）定位是否数量相关")
    for q in [1, 5, 10, 15, 20, 30, 50, 100]:
        ok, vals, dt, _ = try_read(inst, f"时间块 0x05DD qty={q}", 0x05DD, q)
        if ok and vals:
            sample = vals[:min(5, len(vals))]
            print(f"        前5值: {sample}")

    section("D. 横向对比各块 addr qty=20（看是否只有时间块有问题）")
    blocks = [
        ("温度块", REG_TBL_TEMP),
        ("湿度块", REG_TBL_HUM),
        ("时间块", REG_TBL_TIME),
        ("循环块", REG_TBL_CYCLE),
        ("TS1块", REG_TBL_TS1),
        ("TS2块", REG_TBL_TS2),
        ("TS3块", REG_TBL_TS3),
        ("TS4块", REG_TBL_TS4),
    ]
    for name, addr in blocks:
        try_read(inst, f"{name} 0x{addr:04X} qty=20", addr, 20)

    section("E. 切换到其他程式号（1/2/3/5）后读时间块")
    for pno in [1, 2, 3, 5]:
        inst.write_register(REG_PROG_SEL, pno)
        time.sleep(0.3)
        try_read(inst, f"PROG{pno} 时间块 0x05DD qty=20", 0x05DD, 20)

    section("F. 连续读 8 块（模拟 mc_prog_read_table 完整流程）")
    inst.write_register(REG_PROG_SEL, 4)
    time.sleep(0.3)
    allok = True
    for name, addr in blocks:
        ok, _, _, _ = try_read(inst, f"连读 {name} 0x{addr:04X} qty=20", addr, 20)
        allok = allok and ok
    print(f"  {'['+PASS+']' if allok else '['+FAIL+']'} 连续 8 块读取 {'全部成功' if allok else '存在失败'}")

    inst.close()
    section("完成")


if __name__ == "__main__":
    main()
