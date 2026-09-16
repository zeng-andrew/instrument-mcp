"""FTDI 线（COM43）连接温箱对比测试。

目的：对比 FTDI 转串口与原 CH340 的差异，重点验证：
  1. FTDI 是否仍触发虚假 error 31（CH340 的 SetCommState 误报）
  2. open/close 高频压力下的稳定性
  3. 持久连接读取可靠性
  4. 启停过程中并发读取（串行化，模拟 MCP 调用）
  5. 读取各寄存器是否正常（PV/SV/状态/程式）

安全原则：只读为主；启停测试使用立即停止，避免温度漂移。
用法:
    .venv/Scripts/python.exe tests/ftdi_chamber_test.py
    .venv/Scripts/python.exe tests/ftdi_chamber_test.py --stress 50
"""
import argparse
import json
import logging
import sys
import time

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ftdi_test")

sys.path.insert(0, "src")
from instrument_mcp.instruments import SerialModbusInstrument
from instrument_mcp.commands.modbus_chamber_handler import (
    REG_T_PV, REG_T_SV, REG_H_PV, REG_MV, REG_RUN_FLAG, REG_RUN_CTRL,
    REG_H_SV, REG_PROG_SEL, REG_PROG_NO, REG_SEG_NO,
    RUN_FIX, RUN_STOP, _signed,
    handler_read_pv, handler_read_status, handler_prog_status,
)

PORT = "COM43"
BAUD = 9600

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
INFO = "\033[36mINFO\033[0m"
WARN = "\033[33mWARN\033[0m"


def section(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def open_inst():
    inst = SerialModbusInstrument(address=PORT, baud_rate=BAUD)
    inst.open()
    return inst


def raw_state(inst):
    flag = inst.read_holding(REG_RUN_FLAG, 1)[0]
    pv = _signed(inst.read_holding(REG_T_PV, 1)[0]) / 100.0
    sv = _signed(inst.read_holding(REG_T_SV, 1)[0]) / 100.0
    try:
        prog = inst.read_holding(REG_PROG_NO, 1)[0]
    except Exception:
        prog = None
    return {"run_flag_raw": flag, "pv_c": pv, "sv_c": sv, "prog_no": prog}


# ─────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────
def test_driver_info():
    """探测串口芯片型号，确认是否为 FTDI。"""
    section("串口芯片探测")
    try:
        import serial.tools.list_ports as lp
        ports = lp.comports()
        for p in ports:
            print(f"  {INFO} {p.device}: {p.description}  hwid={p.hwid}")
            if p.device.upper() == PORT.upper():
                if "FTDI" in p.description.upper() or "FTDI" in p.hwid.upper() or "VID:PID=0403" in p.hwid.upper():
                    print(f"  [{PASS}] {PORT} 确认为 FTDI 芯片")
                elif "CH340" in p.description.upper() or "VID:PID=1A86" in p.hwid.upper():
                    print(f"  [{WARN}] {PORT} 仍是 CH340 芯片（未换成 FTDI?）")
                else:
                    print(f"  [{INFO}] {PORT} 芯片类型未识别")
    except Exception as e:
        print(f"  {WARN} 无法枚举端口信息: {e}")


def test_read_in_state(inst, state_label):
    section(f"读命令可用性 - 状态: {state_label}")
    results = []
    t0 = time.time()
    try:
        pv = _signed(inst.read_holding(REG_T_PV, 1)[0]) / 100.0
        results.append(("read_holding(PV)", f"{pv:.2f}°C", PASS, time.time()-t0))
    except Exception as e:
        results.append(("read_holding(PV)", repr(e), FAIL, time.time()-t0))
    t0 = time.time()
    try:
        r = handler_read_pv(inst)
        results.append(("mc_read_pv", r, PASS, time.time()-t0))
    except Exception as e:
        results.append(("mc_read_pv", repr(e), FAIL, time.time()-t0))
    t0 = time.time()
    try:
        r = handler_read_status(inst)
        d = json.loads(r)
        results.append(("mc_read_status", f"running={d['running']} pv={d['pv_c']}°C", PASS, time.time()-t0))
    except Exception as e:
        results.append(("mc_read_status", repr(e), FAIL, time.time()-t0))
    t0 = time.time()
    try:
        r = handler_prog_status(inst)
        d = json.loads(r)
        results.append(("mc_prog_status",
                        f"running={d['running']} flag={d['run_flag']} prog={d['current_prog_no']} seg={d['current_seg_no']}",
                        PASS, time.time()-t0))
    except Exception as e:
        results.append(("mc_prog_status", repr(e), FAIL, time.time()-t0))
    t0 = time.time()
    try:
        sv = _signed(inst.read_holding(REG_T_SV, 1)[0]) / 100.0
        results.append(("read_holding(SV)", f"{sv:.2f}°C", PASS, time.time()-t0))
    except Exception as e:
        results.append(("read_holding(SV)", repr(e), FAIL, time.time()-t0))

    w = max(len(n) for n, *_ in results)
    for name, res, st, dt in results:
        print(f"  [{st}] {name:<{w}}  {res}   ({dt*1000:.0f}ms)")
    return results


def test_read_during_start(inst, mode="fix"):
    section(f"启动过程中读取温度（串行）- 模式: {mode}")
    print(f"  {INFO} 先停止温箱，确保从已知状态开始")
    inst.write_register(REG_RUN_CTRL, RUN_STOP)
    time.sleep(1.0)
    pre = raw_state(inst)
    print(f"  {INFO} 启动前状态: flag={pre['run_flag_raw']} pv={pre['pv_c']:.2f}°C")

    t_start = time.time()
    print(f"  {INFO} 发送启动命令 (mode={mode})...")
    if mode == "fix":
        inst.write_register(REG_RUN_CTRL, RUN_FIX)
    else:
        inst.write_register(REG_PROG_SEL, 1)
        time.sleep(0.3)
        inst.write_register(REG_RUN_CTRL, RUN_FIX)
    t_cmd = time.time() - t_start
    print(f"  {INFO} 启动命令耗时 {t_cmd*1000:.0f}ms")

    print(f"  {INFO} 启动后立即连续读取温度（观察启动瞬态）：")
    all_ok = True
    for delay in [0.0, 0.1, 0.5, 1.0, 1.5]:
        if delay > 0:
            time.sleep(delay)
        t0 = time.time()
        elapsed = t0 - t_start
        try:
            pv = _signed(inst.read_holding(REG_T_PV, 1)[0]) / 100.0
            flag = inst.read_holding(REG_RUN_FLAG, 1)[0]
            dt = time.time() - t0
            reasonable = abs(pv) < 300
            print(f"    [{PASS if reasonable else WARN}] t+{elapsed:.2f}s  "
                  f"PV={pv:.2f}°C  flag={flag}  ({dt*1000:.0f}ms)")
            if not reasonable:
                all_ok = False
        except Exception as e:
            all_ok = False
            print(f"    [{FAIL}] t+{elapsed:.2f}s  读取失败: {repr(e)}")

    inst.write_register(REG_RUN_CTRL, RUN_STOP)
    time.sleep(0.5)
    post = raw_state(inst)
    print(f"  {INFO} 测试后已停止: flag={post['run_flag_raw']}")
    print(f"  [{PASS if all_ok else FAIL}] 启动过程中{'可' if all_ok else '不可'}读取温度（串行）")
    return all_ok


def test_error31_stress(rounds=30):
    """高频 open/close 压力测试。

    对比要点：CH340 在此测试中频繁触发虚假 error 31（SetCommState 误报），
    需要运行时补丁才能通过。FTDI 若无此问题，应直接零失败。
    """
    section(f"open/close 压力测试 - {rounds} 轮 open/read/close")
    ok = 0
    fail = 0
    times = []
    errs = []
    for i in range(rounds):
        t0 = time.time()
        try:
            inst = open_inst()
            pv = _signed(inst.read_holding(REG_T_PV, 1)[0]) / 100.0
            inst.close()
            dt = time.time() - t0
            times.append(dt)
            ok += 1
            if i < 3 or i % 10 == 0:
                print(f"  [{i+1:>3}/{rounds}] {PASS} pv={pv:.2f}°C  ({dt*1000:.0f}ms)")
        except Exception as e:
            dt = time.time() - t0
            fail += 1
            errs.append((i+1, repr(e)))
            print(f"  [{i+1:>3}/{rounds}] {FAIL} {repr(e)}  ({dt*1000:.0f}ms)")
            try:
                inst.close()
            except Exception:
                pass
            time.sleep(0.5)

    print(f"\n  {INFO} 成功 {ok}/{rounds}  失败 {fail}/{rounds}")
    if times:
        import statistics
        print(f"  {INFO} 单轮耗时: 均值 {statistics.mean(times)*1000:.0f}ms  "
              f"中位 {statistics.median(times)*1000:.0f}ms  "
              f"max {max(times)*1000:.0f}ms")
    if errs:
        print(f"  {WARN} 失败明细:")
        for i, e in errs[:10]:
            print(f"      轮{i}: {e}")
    else:
        print(f"  [{PASS}] {rounds} 轮全部成功")
    return ok, fail, errs


def test_persistent_conn_stress(rounds=50):
    section(f"持久连接压力测试 - {rounds} 次连续读")
    inst = open_inst()
    ok = 0
    fail = 0
    errs = []
    t0 = time.time()
    for i in range(rounds):
        try:
            pv = _signed(inst.read_holding(REG_T_PV, 1)[0]) / 100.0
            ok += 1
        except Exception as e:
            fail += 1
            errs.append((i+1, repr(e)))
    dt = time.time() - t0
    inst.close()
    print(f"  {INFO} 成功 {ok}/{rounds}  失败 {fail}/{rounds}  总耗时 {dt*1000:.0f}ms "
          f"(均值 {dt/rounds*1000:.0f}ms/次)")
    if errs:
        print(f"  {WARN} 失败明细:")
        for i, e in errs[:10]:
            print(f"      第{i}次: {e}")
    else:
        print(f"  [{PASS}] 持久连接 {rounds} 次读取零失败")
    return ok, fail, errs


# ─────────────────────────────────────────────
# main
# ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-prog", action="store_true", help="跳过程式启动测试")
    ap.add_argument("--stress", type=int, default=30, help="open/close 压力测试轮数")
    ap.add_argument("--persistent", type=int, default=50, help="持久连接读取次数")
    args = ap.parse_args()

    section("FTDI 线（COM43）连接温箱对比测试")
    print(f"端口: {PORT} @ {BAUD} 8N1")

    test_driver_info()

    section("基线检查：连接并读取当前状态（不改状态）")
    inst = open_inst()
    base = raw_state(inst)
    print(f"  {INFO} 基线: 运行标志(raw)={base['run_flag_raw']}  "
          f"PV={base['pv_c']:.2f}°C  SV={base['sv_c']:.2f}°C  "
          f"当前程式号={base['prog_no']}")
    print(f"  {INFO} 运行标志解读: "
          f"{'停止' if base['run_flag_raw']==0 else '运行'}")

    if base["run_flag_raw"] != 0:
        print(f"\n  {WARN} 温箱当前在运行，先停止以测停止态")
        inst.write_register(REG_RUN_CTRL, RUN_STOP)
        time.sleep(1.0)
    s = raw_state(inst)
    print(f"  {INFO} 当前: flag={s['run_flag_raw']}")
    test_read_in_state(inst, "停止")

    test_read_during_start(inst, mode="fix")
    time.sleep(1.0)

    if not args.skip_prog:
        test_read_during_start(inst, mode="prog")
        time.sleep(1.0)
    else:
        print(f"\n  {INFO} --skip-prog: 跳过程式启动测试")

    section("验证：定值运行态下读命令可用性")
    inst.write_register(REG_RUN_CTRL, RUN_FIX)
    time.sleep(1.0)
    s = raw_state(inst)
    print(f"  {INFO} 定值运行中: flag={s['run_flag_raw']}  pv={s['pv_c']:.2f}°C")
    test_read_in_state(inst, "定值运行")
    inst.write_register(REG_RUN_CTRL, RUN_STOP)
    time.sleep(1.0)
    s = raw_state(inst)
    print(f"  {INFO} 已停止: flag={s['run_flag_raw']}")
    inst.close()

    test_persistent_conn_stress(args.persistent)
    test_error31_stress(args.stress)

    section("测试完成")
    print(f"  {INFO} 所有阶段执行完毕。温箱已停止。")


if __name__ == "__main__":
    main()
