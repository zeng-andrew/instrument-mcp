"""Keysight 66311B / 66332A（66300 系列）高速电流数字化采集处理器。

硬件能力（来自 66300 系列编程规格，66311B 与 66332A 共用同一套命令）:
  - 缓冲深度固定 4096 点（SENSe:SWEep:POINts 上限 4096），不可扩展
  - 最小采样间隔 15.6 us（约 64 kS/s 最高采样率）
  - 采集方式: 单次有限缓冲采集（INITiate 触发，采满 4096 点后停止）
  - 不支持连续流式输出: 必须用 SENSe:DATA? 取回缓冲，再 INITiate 采下一批

重要事实 -- "批次阴影"无法物理消除:
  每批采满 4096 点后，必须发 SENSe:DATA? 取回数据 + 重新 INITiate 触发下一批。
  在这个取回/重触发的时间里，仪器不采样，形成阴影（GPIB 上约几十~上百 ms）。
  单批内由硬件保证绝对均匀（间距 = TINTerval）；阴影只发生在批次之间。

降低阴影占比的策略（本 handler 默认采用）:
  用合理的（较低的）采样率 + 用满 4096 点，让单批覆盖足够长的时间。
  单批时长 = 4096 / 采样率；采样率越低，单批越长，批次越少，阴影占比越小。
  例: 1 kHz 时单批 = 4.096 s，若总时长 1 分钟只需 ~15 批，阴影占比极小。

数据落地: 大数组写 CSV 文件（避免 MCP 字符串过大）；返回 JSON 摘要。
  CSV 每行: batch_index, t_offset_s, value, shadow_flag
    - t_offset_s = 相对采集开始的累计时间（含阴影，连续递增）
    - shadow_flag = 1 表示该点是某批的第一个点（其前方存在阴影间隙）

用法示例（先 connect 连接 keysight_ps 后）:
  ps_acquire_current(output_path="D:/data/cur.csv",
                     sample_rate_hz=1000, duration_s=10)
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path

# 66300 系列高速数字化仪的硬件常数
MAX_POINTS = 4096          # SENSe:SWEep:POINts 上限（固定缓冲深度）
MIN_TINTERVAL_S = 15.6e-6  # 最小采样间隔 15.6 us => 最高采样率 ~64 kS/s
MIN_SAMPLE_RATE_HZ = 1.0 / MIN_TINTERVAL_S  # ~64103 Hz


def _check_inst(inst) -> None:
    """确认会话是 VISA 仪器（66311B/66332A 走 VisaInstrument）。"""
    if not hasattr(inst, "query") or not hasattr(inst, "write"):
        raise RuntimeError(
            "当前会话不是 VISA 仪器。请用 "
            'connect(address="GPIB0::5::INSTR", instrument_type="keysight_ps") 连接'
        )


def _read_sweep_data(inst) -> list[float]:
    """触发一次扫描并取回 4096 点数据数组。

    66300 系列: INITiate:IMMediate 触发 -> *OPC? 等待采满 -> SENSe:DATA? 取回。
    SENSe:DATA? 返回逗号分隔的 ASCII 浮点数组（电流 A 或电压 V，取决于 SENSe:FUNCtion）。
    """
    # 触发单次扫描
    inst.write("INIT:IMM")
    # *OPC? 在扫描完成后才返回，用作"采满 4096 点"的同步信号
    inst.query("*OPC?")
    # 取回缓冲数据（ASCII 逗号分隔）
    raw = inst.query("SENS:DATA?")
    vals = [float(x) for x in raw.split(",") if x.strip()]
    return vals


def ps_acquire_current(
    inst,
    output_path: str = "power_supply_acquire.csv",
    sample_rate_hz: float = 1000.0,
    duration_s: float = 10.0,
    function: str = "CURR",
) -> str:
    """高速采集电流（或电压），用满 4096 点缓冲，多批拼接覆盖指定总时长。

    参数:
      output_path    - CSV 输出路径（大数据写文件）
      sample_rate_hz - 采样率(Hz)。越低单批覆盖越长、批次越少、阴影占比越小。
                       上限 ~64103 Hz(15.6us 间隔)。建议 100~5000 Hz 以压低阴影。
      duration_s     - 总采集时长(秒)。采集会持续到覆盖此时长。
      function       - 采集量: "CURR"(电流,默认) 或 "VOLT"(电压)

    返回: JSON 摘要（含批次统计、阴影时长、总点数、文件路径、首尾预览）。
    大数组本身写入 output_path 指定的 CSV 文件。
    """
    _check_inst(inst)

    # ── 参数校验与规整 ─────────────────────────────
    sample_rate_hz = float(sample_rate_hz)
    duration_s = float(duration_s)
    function = str(function).upper().strip()
    if function not in ("CURR", "VOLT"):
        return f"[FAIL] function 必须是 CURR 或 VOLT，收到: {function}"
    if sample_rate_hz <= 0:
        return f"[FAIL] sample_rate_hz 必须 > 0，收到: {sample_rate_hz}"
    if duration_s <= 0:
        return f"[FAIL] duration_s 必须 > 0，收到: {duration_s}"

    if sample_rate_hz > MIN_SAMPLE_RATE_HZ * 1.001:
        return (
            f"[FAIL] 采样率 {sample_rate_hz:.1f} Hz 超过硬件上限 "
            f"{MIN_SAMPLE_RATE_HZ:.0f} Hz（最小间隔 15.6 us）。"
            f"如需最高速率，请用 {MIN_SAMPLE_RATE_HZ:.0f}。"
        )

    tinterval_s = 1.0 / sample_rate_hz
    points_per_batch = MAX_POINTS                       # 用满缓冲
    batch_duration_s = points_per_batch * tinterval_s   # 单批覆盖时长
    n_batches = max(1, int(duration_s / batch_duration_s) + 1)

    # ── 配置仪器：测量函数 + 采样参数 ─────────────
    func_cmd = "CURR" if function == "CURR" else "VOLT"
    # 清状态 + 选测量量 + 设采样点数(用满) + 设采样间隔
    inst.write("*CLS")
    inst.write(f"SENS:FUNC \"{func_cmd}\"")
    inst.write(f"SENS:SWE:POIN {points_per_batch}")
    inst.write(f"SENS:SWE:TINT {tinterval_s:.9f}")

    # 回读确认配置已生效（诊断用）
    cfg_func = inst.query("SENS:FUNC?")
    cfg_poin = inst.query("SENS:SWE:POIN?")
    cfg_tint = inst.query("SENS:SWE:TINT?")

    # ── 多批采集，写入 CSV ─────────────────────────
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    batches = []            # 每批元信息
    total_points = 0
    preview_head: list[float] = []
    preview_tail: list[float] = []

    t_start = time.perf_counter()
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["batch_index", "t_offset_s", "value", "shadow_flag"])
        w.writerow(
            ["meta", 0.0,
             f"sample_rate={sample_rate_hz}Hz tinterval={tinterval_s:.9f}s "
             f"points_per_batch={points_per_batch} function={function}", 0]
        )

        for b in range(n_batches):
            t_batch_start = time.perf_counter()
            vals = _read_sweep_data(inst)
            t_batch_end = time.perf_counter()

            # 单批实际覆盖时长（用硬件间隔估算，比墙上时钟更准）
            batch_covered = len(vals) * tinterval_s
            # 第 N 批数据起点 = 上一批数据终点 + 取回阴影。
            # 阴影用墙上时钟测量: 本批发起时刻 - 上批取回完成时刻。
            if b == 0:
                t_offset = 0.0
                shadow_s = 0.0
            else:
                prev = batches[-1]
                prev_end = prev["t_offset"] + prev["covered_s"]
                # 上批取回完成的墙上时刻 -> 本批发起的墙上时刻，即取回/重触发阴影
                shadow_s = max(0.0, t_batch_start - prev["wall_end"])
                t_offset = prev_end + shadow_s

            for i, v in enumerate(vals):
                # 非首批的第一个点标记 shadow_flag=1（其前方存在批次间隙）
                flag = 1 if (b > 0 and i == 0) else 0
                w.writerow([b, f"{t_offset + i * tinterval_s:.9f}", f"{v:.9f}", flag])

            batches.append({
                "batch": b,
                "points": len(vals),
                "wall_s": round(t_batch_end - t_batch_start, 4),
                "wall_end": t_batch_end,
                "covered_s": round(batch_covered, 4),
                "shadow_before_s": round(shadow_s, 4),
                "t_offset": round(t_offset, 4),
                "first": round(vals[0], 9) if vals else None,
                "last": round(vals[-1], 9) if vals else None,
            })
            total_points += len(vals)

            # 预览：首批头部 + 末批尾部
            if b == 0:
                preview_head = [round(x, 6) for x in vals[:5]]
            if b == n_batches - 1:
                preview_tail = [round(x, 6) for x in vals[-5:]]

            # 已覆盖目标时长则提前结束
            if (b + 1) * batch_duration_s >= duration_s:
                break

    t_total = time.perf_counter() - t_start

    # ── 统计阴影占比 ───────────────────────────────
    total_shadow = sum(bt["shadow_before_s"] for bt in batches)
    total_covered = sum(bt["covered_s"] for bt in batches)
    shadow_ratio = (total_shadow / (total_covered + total_shadow)) if (total_covered + total_shadow) > 0 else 0.0

    summary = {
        "status": "PASS",
        "output_path": str(out),
        "function": function,
        "sample_rate_hz": sample_rate_hz,
        "tinterval_s": tinterval_s,
        "points_per_batch": points_per_batch,
        "n_batches": len(batches),
        "total_points": total_points,
        "total_covered_s": round(total_covered, 4),
        "total_shadow_s": round(total_shadow, 4),
        "shadow_ratio": round(shadow_ratio, 4),
        "wall_total_s": round(t_total, 4),
        "config_readback": {
            "function": cfg_func,
            "points": cfg_poin,
            "tinterval": cfg_tint,
        },
        "preview_head": preview_head,
        "preview_tail": preview_tail,
        "batches": batches,
    }
    return json.dumps(summary, ensure_ascii=False)


def ps_acquire_abort(inst) -> str:
    """中止正在进行的扫描采集（发 *RST 或 ABOR）。

    若某批采集卡住（例如触发了但迟迟采不满），可调用本命令重置仪器状态。
    """
    _check_inst(inst)
    inst.write("ABOR")
    return "[PASS] 已中止扫描采集（ABOR）"


def ps_get_acquire_config(inst) -> str:
    """查询当前高速扫描采集配置（测量函数/点数/间隔），返回 JSON。"""
    _check_inst(inst)
    info = {
        "function": inst.query("SENS:FUNC?"),
        "points": inst.query("SENS:SWE:POIN?"),
        "tinterval_s": inst.query("SENS:SWE:TINT?"),
        "max_points": MAX_POINTS,
        "min_tinterval_s": MIN_TINTERVAL_S,
        "max_sample_rate_hz": round(MIN_SAMPLE_RATE_HZ, 1),
    }
    return json.dumps(info, ensure_ascii=False)
