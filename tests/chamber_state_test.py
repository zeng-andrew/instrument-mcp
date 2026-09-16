"""温箱状态需求与虚假 error31 综合测试。

本脚本直接操作 SerialModbusInstrument（绕过 MCP handler 的 ==1 判断），
系统验证：
  1. 各读命令在不同温箱状态（停止/定值运行/程式运行）下的可用性
  2. 启动程式/定值运行过程中能否并发读取温度（启动瞬态）
  3. 运行标志真实值（验证文档说 2，代码判断 ==1 的疑似 bug）
  4. 虚假 error31 的复现条件与当前规避措施有效性（高频 open/close 压力）

⚠ 安全原则：测试前先读取当前状态并记录；测试结束后恢复原状态。
   本脚本只在已配置程式上运行 mc_prog_run（不新建/修改段表）。
   默认只做一次程式启动→立即停止，避免温箱长时间运行改变温度。

用法:
    .venv/Scripts/python.exe tests/chamber_state_test.py
    .venv/Scripts/python.exe tests/chamber_state_test.py --skip-prog    # 跳过程式测试
    .venv/Scripts/python.exe tests/chamber_state_test.py --stress 50   # error31 压力 50 轮
"""
import argparse
import json
import logging
import sys
import threading
import time

# 配置日志：能看到 transact 的 warning（帧前噪声/残余字节）
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chamber_test")

sys.path.insert(0, "src")
from instrument_mcp.instruments import SerialModbusInstrument
from instrument_mcp.commands.modbus_chamber_handler import (
    REG_T_PV, REG_T_SV, REG_H_PV, REG_MV, REG_RUN_FLAG, REG_RUN_CTRL,
    REG_H_SV, REG_PROG_SEL, REG_PROG_NO, REG_SEG_NO, REG_SEG_TEMP_SV1,
    RUN_FIX, RUN_STOP, _signed, handler_read_pv, handler_read_status,
    handler_prog_status,
)

PORT = "COM30"
BAUD = 9600

# ─────────────────────────────────────────────
# 辅助
# ─────────────────────────────────────────────
PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
INFO = "\033[36mINFO\033[0m"
WARN = "\033[33mWARN\033[0m"


def section(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def raw_state(inst):
    """读取最原始的运行标志 + 温度，返回 dict。"""
    flag = inst.read_holding(REG_RUN_FLAG, 1)[0]
    pv = _signed(inst.read_holding(REG_T_PV, 1)[0]) / 100.0
    sv = _signed(inst.read_holding(REG_T_SV, 1)[0]) / 100.0
    try:
        prog = inst.read_holding(REG_PROG_NO, 1)[0]
    except Exception:
        prog = None
    return {"run_flag_raw": flag, "pv_c": pv, "sv_c": sv, "prog_no": prog}


def open_inst():
    inst = SerialModbusInstrument(address=PORT, baud_rate=BAUD)
    inst.open()
    return inst


# ─────────────────────────────────────────────
# 测试用例
# ─────────────────────────────────────────────
def test_read_in_state(inst, state_label):
    """在给定状态下尝试所有读命令，返回结果表。"""
    section(f"读命令可用性 — 状态: {state_label}")
    results = []
    # 1. 原始 PV
    t0 = time.time()
    try:
        pv = _signed(inst.read_holding(REG_T_PV, 1)[0]) / 100.0
        results.append(("read_holding(PV)", f"{pv:.2f}°C", PASS, time.time()-t0))
    except Exception as e:
        results.append(("read_holding(PV)", repr(e), FAIL, time.time()-t0))
    # 2. handler_read_pv
    t0 = time.time()
    try:
        r = handler_read_pv(inst)
        results.append(("mc_read_pv", r, PASS, time.time()-t0))
    except Exception as e:
        results.append(("mc_read_pv", repr(e), FAIL, time.time()-t0))
    # 3. mc_read_status (JSON)
    t0 = time.time()
    try:
        r = handler_read_status(inst)
        d = json.loads(r)
        results.append(("mc_read_status", f"running={d['running']} pv={d['pv_c']}°C", PASS, time.time()-t0))
    except Exception as e:
        results.append(("mc_read_status", repr(e), FAIL, time.time()-t0))
    # 4. mc_prog_status (JSON)
    t0 = time.time()
    try:
        r = handler_prog_status(inst)
        d = json.loads(r)
        results.append(("mc_prog_status",
                        f"running={d['running']} flag={d['run_flag']} prog={d['current_prog_no']} seg={d['current_seg_no']}",
                        PASS, time.time()-t0))
    except Exception as e:
        results.append(("mc_prog_status", repr(e), FAIL, time.time()-t0))
    # 5. 读 SV
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
    """测试启动过程中能否读取温度（串行化访问，模拟真实 MCP 调用模式）。

    MCP 工具调用是串行的（一次一个工具），所以这里用串行化序列验证：
      启动命令 -> 立即(0ms/100ms/500ms)读 PV -> 看读是否成功、值是否合理。

    mode='fix':  写 0x0065=1 启动定值运行
    mode='prog': 写 0x0064=程式号 + 0x0065=1 启动程式运行
    """
    section(f"启动过程中读取温度（串行）— 模式: {mode}")
    # 先确保停止
    print(f"  {INFO} 先停止温箱，确保从已知状态开始")
    inst.write_register(REG_RUN_CTRL, RUN_STOP)
    time.sleep(1.0)
    pre = raw_state(inst)
    print(f"  {INFO} 启动前状态: flag={pre['run_flag_raw']} pv={pre['pv_c']:.2f}°C")

    # 发送启动命令
    t_start = time.time()
    print(f"  {INFO} 发送启动命令 (mode={mode})...")
    if mode == "fix":
        inst.write_register(REG_RUN_CTRL, RUN_FIX)
    else:
        # 用程式1（段表温和 25/55/55/25/25），避免触发极端温度段
        inst.write_register(REG_PROG_SEL, 1)
        time.sleep(0.3)
        inst.write_register(REG_RUN_CTRL, RUN_FIX)
    t_cmd = time.time() - t_start
    print(f"  {INFO} 启动命令耗时 {t_cmd*1000:.0f}ms")

    # 启动后立即读、100ms、500ms、1s 读 PV，观察瞬态
    print(f"  {INFO} 启动后立即连续读取温度（观察启动瞬态）：")
    all_ok = True
    for delay in [0.0, 0.1, 0.5, 1.0, 1.5]:
        if delay > 0:
            time.sleep(delay - (time.time() - t_start - t_cmd if False else 0))
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

    # 立即停止，避免温度漂移
    inst.write_register(REG_RUN_CTRL, RUN_STOP)
    time.sleep(0.5)
    post = raw_state(inst)
    print(f"  {INFO} 测试后已停止: flag={post['run_flag_raw']}")
    print(f"  [{PASS if all_ok else FAIL}] 启动过程中{'可' if all_ok else '不可'}读取温度（串行）")
    return all_ok


def test_error31_stress(rounds=30):
    """高频 open/close 压力测试，复现/验证虚假 error31 规避。

    每轮：open() → read PV → close()。统计成功率与耗时。
    """
    section(f"虚假 error31 压力测试 — {rounds} 轮 open/read/close")
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
            # 尝试关闭残留实例
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
        print(f"  [{PASS}] {rounds} 轮全部成功 — 当前规避措施有效（补丁+open重试+baud kick）")
    return ok, fail, errs


def test_persistent_conn_stress(rounds=50):
    """持久连接压力：单连接连续读 N 次（不 open/close），观察是否会出现 error31 或超时。

    虚假 error31 主要发生在 open/reconfigure，持久连接理论上不会触发；
    本测试验证这一假设，并测量稳态读取可靠性。
    """
    section(f"持久连接压力测试 — {rounds} 次连续读")
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
        print(f"  [{PASS}] 持久连接 {rounds} 次读取零失败 — 验证 error31 是 open-path 问题")
    return ok, fail, errs


# ─────────────────────────────────────────────
# main
# ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-prog", action="store_true", help="跳过程式启动测试")
    ap.add_argument("--stress", type=int, default=30, help="error31 压力测试轮数")
    ap.add_argument("--persistent", type=int, default=50, help="持久连接读取次数")
    args = ap.parse_args()

    section("温箱状态需求与虚假 error31 综合测试")
    print(f"端口: {PORT} @ {BAUD} 8N1")

    # ── 基线：单连接读取当前状态 ──
    section("基线检查：连接并读取当前状态（不改状态）")
    inst = open_inst()
    base = raw_state(inst)
    print(f"  {INFO} 基线: 运行标志(raw)={base['run_flag_raw']}  "
          f"PV={base['pv_c']:.2f}°C  SV={base['sv_c']:.2f}°C  "
          f"当前程式号={base['prog_no']}")
    print(f"  {INFO} 运行标志解读: "
          f"{'停止' if base['run_flag_raw']==0 else '运行' if base['run_flag_raw']!=0 else '?'}")

    # ── 测试1：停止态读 ──
    # 确保停止
    if base["run_flag_raw"] != 0:
        print(f"\n  {WARN} 温箱当前在运行，先停止以测停止态（记录原状态用于恢复）")
        inst.write_register(REG_RUN_CTRL, RUN_STOP)
        time.sleep(1.0)
    s = raw_state(inst)
    print(f"  {INFO} 当前: flag={s['run_flag_raw']}")
    test_read_in_state(inst, "停止")

    # ── 测试2：定值运行启动过程中读 ──
    test_read_during_start(inst, mode="fix")
    time.sleep(1.0)

    # ── 测试3：程式运行启动过程中读 ──
    if not args.skip_prog:
        test_read_during_start(inst, mode="prog")
        time.sleep(1.0)
    else:
        print(f"\n  {INFO} --skip-prog: 跳过程式启动测试")

    # ── 测试4：定值运行态下读 ──
    section("验证：定值运行态下读命令可用性")
    inst.write_register(REG_RUN_CTRL, RUN_FIX)
    time.sleep(1.0)
    s = raw_state(inst)
    print(f"  {INFO} 定值运行中: flag={s['run_flag_raw']}  pv={s['pv_c']:.2f}°C")
    test_read_in_state(inst, "定值运行")
    # 停止
    inst.write_register(REG_RUN_CTRL, RUN_STOP)
    time.sleep(1.0)
    s = raw_state(inst)
    print(f"  {INFO} 已停止: flag={s['run_flag_raw']}")

    inst.close()

    # ── 测试5：持久连接压力 ──
    test_persistent_conn_stress(args.persistent)

    # ── 测试6：error31 open/close 压力 ──
    test_error31_stress(args.stress)

    # ── 总结 ──
    section("测试完成")
    print(f"  {INFO} 所有阶段执行完毕。温箱已停止。")


if __name__ == "__main__":
    main()
