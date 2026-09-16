#!/usr/bin/env python
"""米家智能插座2 (chuangmi.plug.212a01) 电流/功率采样能力实测。

运行（仓库根目录）:
    uv run --with python-miio tests/mi_plug_sampling_test.py rtt      # 单读/批量读往返延迟
    uv run --with python-miio tests/mi_plug_sampling_test.py refresh  # 持续轮询,测设备端刷新周期
    uv run --with python-miio tests/mi_plug_sampling_test.py step     # 继电器阶跃: 功率归零/恢复的检测延迟

产出:
    - rtt:     60 次单属性读 + 60 次 4 属性批量读的 RTT 统计 -> 轮询频率上限
    - refresh: 以最快速度轮询功率(5/6)+电流(5/2)+电压(5/3) 120 s, 按"数值变化事件"
               间隔推设备端计量刷新周期; 原始数据存 tests/mi_plug_refresh_raw.csv
    - step:    快速轮询功率, 中途 off->on 切继电器, 测功率反映真实状态的端到端延迟
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "mi_plug_config.json"

# 电表服务 siid=5: (piid, 名称); 功率单位 0.01W, 电流疑似 0.01A, 电压 1V
METER_PROPS = [(5, 6, "power_cW"), (5, 2, "current_cA"), (5, 3, "voltage_V")]


def make_device():
    from miio import MiotDevice

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    d = MiotDevice(cfg["ip"], cfg["token"])
    d.info()  # 预热握手, 避免首条命令带 autodetect 开销
    return d


def batch_read(d, props=METER_PROPS):
    """一次 UDP 往返读多个 MIoT 属性 (协议原生 get_properties)。

    did 是客户端自选的关联 ID, 设备原样返回; python-miio 用 "siid-piid" 字符串,
    数字型 did 会被设备拒收 (need did)。
    """
    params = [{"did": f"{s}-{p}", "siid": s, "piid": p} for s, p, _ in props]
    res = d.send("get_properties", params)
    out = {}
    for item, (_, _, name) in zip(res, props):
        out[name] = item.get("value") if isinstance(item, dict) else item
    return out


def rtt_stats(samples, label):
    ms = [t * 1000 for t in samples]
    ms_sorted = sorted(ms)
    print(f"\n[{label}] n={len(ms)}")
    print(f"  min={ms_sorted[0]:.0f}ms  median={statistics.median(ms):.0f}ms  "
          f"mean={statistics.mean(ms):.0f}ms  p95={ms_sorted[int(len(ms)*0.95)-1]:.0f}ms  "
          f"max={ms_sorted[-1]:.0f}ms")
    print(f"  => 单次往返可支撑的稳定轮询率: {1000/statistics.median(ms):.1f} Hz (按中位), "
          f"{1000/statistics.mean(ms):.1f} Hz (按均值)")
    return statistics.median(ms)


def cmd_rtt() -> int:
    d = make_device()

    # 单属性读: python-miio 高层封装 get_property_by
    samples = []
    for _ in range(60):
        t0 = time.perf_counter()
        d.get_property_by(5, 6)
        samples.append(time.perf_counter() - t0)
    rtt_stats(samples, "单属性读 get_property_by(5,6) x60")

    # 批量读: 底层 get_properties, 一次往返拿 3 个量
    v = batch_read(d)
    print(f"\n批量读回显: {v}")
    samples = []
    for _ in range(60):
        t0 = time.perf_counter()
        batch_read(d)
        samples.append(time.perf_counter() - t0)
    rtt_stats(samples, "批量读 get_properties x3属性 x60")

    # 连发无间隔压力: 看有无丢包/报错
    err = 0
    t0 = time.perf_counter()
    n = 100
    for _ in range(n):
        try:
            batch_read(d)
        except Exception:
            err += 1
    wall = time.perf_counter() - t0
    print(f"\n连发压力: {n} 次批量读 {wall:.1f}s -> {n/wall:.1f} Hz 实际吞吐, 失败 {err}")
    return 0


def cmd_refresh(duration: float = 120.0) -> int:
    d = make_device()
    rows = []  # (t_rel, power, current, voltage)
    t_end = time.perf_counter() + duration
    n_err = 0
    while time.perf_counter() < t_end:
        t = time.perf_counter()
        try:
            v = batch_read(d)
            rows.append((t, v["power_cW"], v["current_cA"], v["voltage_V"]))
        except Exception:
            n_err += 1
    wall = rows[-1][0] - rows[0][0] if len(rows) > 1 else duration
    rate = len(rows) / wall
    print(f"\n持续轮询 {wall:.1f}s: {len(rows)} 个样本 -> 实际 {rate:.1f} Hz, 失败 {n_err}")

    out = Path(__file__).parent / "mi_plug_refresh_raw.csv"
    with out.open("w", encoding="utf-8") as f:
        f.write("t_s,power_cW,current_cA,voltage_V\n")
        t0 = rows[0][0]
        for t, p, c, u in rows:
            f.write(f"{t-t0:.3f},{p},{c},{u}\n")
    print(f"原始数据: {out}")

    for idx, name in [(1, "power/0.01W"), (2, "current/0.01A"), (3, "voltage/1V")]:
        changes = []  # 变化时刻(取旧值最后样本与新值首样本的中点)
        last_v, last_t = rows[0][idx], rows[0][0]
        for t, *_rest in rows[1:]:
            v = _rest[idx - 1]
            if v != last_v:
                changes.append(((last_t + t) / 2 - rows[0][0], last_v, v))
                last_v, last_t = v, t
        vals = [r[idx] for r in rows]
        uniq = sorted(set(vals))
        span = rows[-1][0] - rows[0][0]
        print(f"\n[{name}] 样本 {len(rows)} 个, 唯一值 {len(uniq)} 个: "
              f"{uniq[:12]}{'...' if len(uniq) > 12 else ''}")
        print(f"  变化事件 {len(changes)} 次, 平均 {len(changes)/span*60:.1f} 次/min (观测 {span:.0f}s)")
        gaps = [b[0] - a[0] for a, b in zip(changes, changes[1:])]
        if len(gaps) >= 5:
            g = sorted(gaps)
            print(f"  相邻变化间隔: min={g[0]:.2f}s median={statistics.median(g):.2f}s "
                  f"mean={statistics.mean(g):.2f}s p90={g[int(len(g)*0.9)-1]:.2f}s max={g[-1]:.2f}s")
        elif changes:
            print(f"  变化时刻(前 10): {[f'{t:.1f}s:{a}->{b}' for t, a, b in changes[:10]]}")
        else:
            print("  全程无变化 -> 该量刷新慢或负载太稳")
    return 0


def cmd_step(cycles: int = 2, phase: float = 22.0) -> int:
    """功率阶跃测试: 负载在插座上, off/on 继电器, 看功率多久反映出来。

    每相观察 phase 秒 (足>一阶惯性整定), 打印每条命令后数值变化时间线,
    用于推报告刷新节拍与平滑时间常数。
    """
    d = make_device()
    on0 = d.get_property_by(2, 1)
    print(f"起始开关状态: {on0}, 负载功率基线见首行事件; 开始 {cycles} 个 off/on 周期, 每相 {phase}s")
    events = []  # (t, power, current, voltage)
    t_start = time.perf_counter()

    def poll(deadline):
        last = None
        while time.perf_counter() < deadline:
            t = time.perf_counter() - t_start
            try:
                v = batch_read(d)
            except Exception:
                continue
            key = (v["power_cW"], v["current_cA"], v["voltage_V"])
            if last is None or key != last:
                events.append((t, *key))
                last = key

    t_cmds = []  # (t, 动作)
    initial = bool(on0)
    target = not initial  # 交替翻转, 结束后恢复原状态
    for i in range(cycles * 2):
        poll_until = time.perf_counter() + phase
        poll(time.perf_counter() + 2)  # 相位前 2s 基线
        d.set_property_by(2, 1, target)
        t_cmds.append((time.perf_counter() - t_start, "OFF" if not target else "ON"))
        poll(poll_until)
        target = not target

    # 恢复原状态
    if bool(d.get_property_by(2, 1)) != initial:
        d.set_property_by(2, 1, initial)
    print(f"结束, 开关已恢复为 {'开' if initial else '关'}: {d.get_property_by(2, 1)}")

    print("\n命令时刻:", ", ".join(f"t={t:.2f}s {a}" for t, a in t_cmds))
    print("\n数值变化事件 (t, power/0.01W, current/0.01A, voltage/V):")
    for t, p, c, u in events:
        print(f"  {t:7.2f}s  P={p:>5} ({p/100:6.2f}W)  I={c:>3}  U={u}")

    # 检测延迟/整定延迟: 对每条 OFF 命令, 找首个 P 低于基线 70% 与首个 P<=0 的事件
    print("\n各 OFF 命令的功率响应:")
    for tc, act in t_cmds:
        if act != "OFF":
            continue
        base = max((p for t, p, *_ in events if t <= tc), key=lambda p: p)
        drop = next((t for t, p, *_ in events if t > tc and p <= base * 0.7), None)
        zero = next((t for t, p, *_ in events if t > tc and p <= 1), None)
        ds = f"检测(<=70%基线): +{drop-tc:.2f}s" if drop else "检测: 未观测到"
        zs = f"归零: +{zero-tc:.2f}s" if zero else "归零: 未观测到"
        print(f"  t={tc:.2f}s OFF (基线{base/100:.2f}W): {ds}, {zs}")
    return 0


def main() -> int:
    if not sys.argv[1:]:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    if cmd == "rtt":
        return cmd_rtt()
    if cmd == "refresh":
        dur = float(sys.argv[2]) if len(sys.argv) > 2 else 120.0
        return cmd_refresh(dur)
    if cmd == "step":
        cyc = int(sys.argv[2]) if len(sys.argv) > 2 else 2
        ph = float(sys.argv[3]) if len(sys.argv) > 3 else 22.0
        return cmd_step(cyc, ph)
    print(f"未知子命令: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
