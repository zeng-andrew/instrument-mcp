"""温箱定值/程式模式寄存器差分扫描工具（COM43）。

目的: 找到控制/反映 定值模式<->程式模式 切换的寄存器。
方法: 在不同面板状态下快照 reg0..511，再 diff 两份快照，差异寄存器即
      模式寄存器候选。已知 volatile 寄存器（PV/MV/剩余时间）会自然
      变化，diff 输出中已标注。

用法:
  python tests/mode_scan.py dump <label>        # 快照 reg0..511 -> dumps/<label>.json
  python tests/mode_scan.py diff <A> <B>        # 对比两份快照
  python tests/mode_scan.py stop                # 写 reg101=4 停止
  python tests/mode_scan.py start               # 写 reg101=1（按面板当前模式启动）
  python tests/mode_scan.py progstart <n>       # 写 reg100=n + reg101=1（mc_prog_run 序列）
  python tests/mode_scan.py read <addr> [qty]   # 读任意寄存器（十进制或 0x..）
  python tests/mode_scan.py write <addr> <val>  # 写任意寄存器（慎用，先确认目标）

注意: dump/start/stop 会改变温箱运行状态，确认温箱空闲再执行。
"""
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

sys.path.insert(0, "src")
from instrument_mcp.instruments import SerialModbusInstrument

PORT = "COM43"
BAUD = 9600
DUMP_DIR = Path(__file__).parent / "dumps"
SCAN_START, SCAN_END = 0, 512     # 快照范围 reg0..511
CHUNK = 16                        # 每次 FC03 读 16 个（此机型大批量曾超时）
RETRY = 3

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
INFO = "\033[36mINFO\033[0m"
WARN = "\033[33mWARN\033[0m"

# 已知寄存器速查（diff 时显示名称）
KNOWN = {
    1: "温度PV×100", 2: "工作温度设定(只读)", 5: "湿度PV×10", 7: "输出MV×10",
    10: "运行标志(0停/1定值运/2程式运)", 25: "当前程式号", 26: "当前段号",
    27: "剩余时", 28: "剩余分", 32: "程式循环(只读)", 35: "段温SV1×100",
    36: "段温SV2×100", 37: "段湿SV×10", 39: "段时间h", 41: "选中程式段数(只读)",
    100: "程式选择", 101: "运行控制(1运/4停)", 102: "定值温度SV×100",
    103: "定值湿度SV×10", 104: "面板模式(0程式/1定值)",
}
# 运行中自然变化的寄存器（diff 时的噪声提示）
VOLATILE = {1, 2, 5, 7, 27, 28, 39}


def _signed(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def open_inst():
    inst = SerialModbusInstrument(address=PORT, baud_rate=BAUD)
    inst.open()
    return inst


def read_chunk(inst, addr, qty):
    """带重试的块读取，全部失败则抛最后一个异常。"""
    last = None
    for i in range(RETRY):
        try:
            return inst.read_holding(addr, qty)
        except Exception as e:
            last = e
            time.sleep(0.15 * (i + 1))
    raise last


def cmd_dump(inst, label):
    DUMP_DIR.mkdir(exist_ok=True)
    regs = {}
    holes = []
    t0 = time.time()
    for base in range(SCAN_START, SCAN_END, CHUNK):
        qty = min(CHUNK, SCAN_END - base)
        try:
            vals = read_chunk(inst, base, qty)
        except Exception as e:
            holes.append(base)
            print(f"  [{WARN}] reg{base}..{base+qty-1} 读取失败: {e}")
            continue
        for i, v in enumerate(vals):
            regs[base + i] = v
        time.sleep(0.02)
    out = DUMP_DIR / f"{label}.json"
    out.write_text(json.dumps({
        "label": label, "port": PORT, "time": time.strftime("%F %T"),
        "regs": {str(k): v for k, v in regs.items()},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  [{PASS}] 快照 {len(regs)} 个寄存器 -> {out.name} "
          f"({time.time()-t0:.1f}s, 失败块 {len(holes)})")
    print("  关键寄存器:")
    for a in (10, 25, 26, 27, 28, 100, 101, 102, 103):
        if a in regs:
            name = KNOWN.get(a, "")
            v = regs[a]
            extra = ""
            if a in (1, 2, 35, 36, 102):
                extra = f" (有符号={_signed(v)/100:.2f})"
            print(f"    reg{a:<3} = {v:<6}{extra}  {name}")


def cmd_diff(inst, label_a, label_b):
    fa = DUMP_DIR / f"{label_a}.json"
    fb = DUMP_DIR / f"{label_b}.json"
    for f in (fa, fb):
        if not f.exists():
            print(f"  [{FAIL}] 找不到快照 {f}")
            return
    ra = {int(k): v for k, v in json.loads(fa.read_text(encoding="utf-8"))["regs"].items()}
    rb = {int(k): v for k, v in json.loads(fb.read_text(encoding="utf-8"))["regs"].items()}
    diffs = []
    for a in sorted(set(ra) & set(rb)):
        if ra[a] != rb[a]:
            diffs.append(a)
    print(f"  [{INFO}] {label_a} vs {label_b}: {len(diffs)} 个寄存器不同")
    stable = [a for a in diffs if a not in VOLATILE]
    noise = [a for a in diffs if a in VOLATILE]
    if stable:
        print(f"  [{PASS}] === 模式候选（非易变）===")
        for a in stable:
            print(f"    reg{a:<4} {ra[a]:<7} -> {rb[a]:<7} "
                  f"(有符号 {_signed(ra[a])} -> {_signed(rb[a])})  {KNOWN.get(a, '')}")
    if noise:
        print(f"  [{WARN}] 易变寄存器（大概率时间噪声，仅供参考）:")
        for a in noise:
            print(f"    reg{a:<4} {ra[a]:<7} -> {rb[a]:<7}  {KNOWN.get(a, '')}")


def cmd_stop(inst):
    inst.write_register(101, 4)
    time.sleep(1.0)
    flag = inst.read_holding(10, 1)[0]
    print(f"  [{'PASS' if flag == 0 else 'FAIL'}] 停止命令已发送，运行标志={flag}")
    v100 = inst.read_holding(100, 1)[0]
    print(f"  [{INFO}] 停止后 reg100(选中程式)={v100}")


def cmd_start(inst):
    inst.write_register(101, 1)
    time.sleep(1.0)
    flag = inst.read_holding(10, 1)[0]
    mode = inst.read_holding(104, 1)[0]
    prog, seg = inst.read_holding(25, 2)
    state = {0: "停止", 1: "定值运行", 2: "程式运行"}.get(flag, f"未知({flag})")
    print(f"  [{'PASS' if flag != 0 else 'FAIL'}] 启动(按面板当前模式) "
          f"标志={flag}({state}) reg104={mode} 当前程式={prog} 段={seg}")


def cmd_progstart(inst, prog_no):
    inst.write_register(100, int(prog_no))
    time.sleep(0.3)
    v100 = inst.read_holding(100, 1)[0]
    print(f"  [{INFO}] 写 reg100={prog_no} 后读回={v100}")
    inst.write_register(101, 1)
    time.sleep(1.5)
    flag = inst.read_holding(10, 1)[0]
    mode = inst.read_holding(104, 1)[0]
    prog, seg = inst.read_holding(25, 2)
    if flag == 2:
        print(f"  [PASS] 程式运行 标志=2 reg104={mode} reg25当前程式={prog} 段={seg}")
    elif flag == 1:
        print(f"  [FAIL] 实际是定值运行(标志=1)!-- 面板不在程式模式(reg104={mode})，"
              f"须先写 reg104=0 切程式模式")
    else:
        print(f"  [FAIL] 未启动(标志={flag})，检查程式{prog_no}段表是否已配置")


def cmd_read(inst, addr, qty=1):
    vals = read_chunk(inst, addr, qty)
    for i, v in enumerate(vals):
        a = addr + i
        print(f"  reg{a:<4} = {v:<7} (0x{v:04X}, 有符号={_signed(v)})  {KNOWN.get(a, '')}")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    cmd, rest = args[0], args[1:]
    inst = open_inst()
    try:
        if cmd == "dump" and rest:
            cmd_dump(inst, rest[0])
        elif cmd == "diff" and len(rest) == 2:
            cmd_diff(inst, rest[0], rest[1])
        elif cmd == "stop":
            cmd_stop(inst)
        elif cmd == "start":
            cmd_start(inst)
        elif cmd == "progstart" and rest:
            cmd_progstart(inst, rest[0])
        elif cmd == "read" and rest:
            cmd_read(inst, int(rest[0], 0), int(rest[1], 0) if len(rest) > 1 else 1)
        elif cmd == "write" and len(rest) == 2:
            a, v = int(rest[0], 0), int(rest[1], 0)
            inst.write_register(a, v)
            time.sleep(0.3)
            back = inst.read_holding(a, 1)[0]
            print(f"  [{'PASS' if back == (v & 0xFFFF) else 'WARN'}] "
                  f"写 reg{a}={v} 读回={back}")
        else:
            print(__doc__)
    finally:
        inst.close()


if __name__ == "__main__":
    main()
