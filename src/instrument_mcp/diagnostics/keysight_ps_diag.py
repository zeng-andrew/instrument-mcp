"""Keysight 66300 系列直流电源（66311B/D、66321B/D、66319B/D 等）诊断与健康检查。

把一次完整排查流程封装为两个 MCP handler：
  - ps_health_check      只读健康检查，不改仪器状态，返回综合 verdict
  - ps_clear_protection  清除输出保护锁存，读回确认是否清除成功

寄存器位映射依据手册 Table 4-1(自检错误) / Table 7-1(状态位) /
Table 8-5(QUES 位权重，权威)。手册对自检错误的判定标准(第41页):
  "turn the power off and then back on to see if the error persists.
   If the error message persists, the dc source requires service."
即 *TST? 非零或错误队列含自检错误码 => 需返修。

测量有效性: 66300 系列在测量子系统无法给出有效结果时返回占位符
9.91000E+37(约 9.91e+37)。本模块用 > 1e30 判定测量值无效。
"""

from __future__ import annotations

import json
import time

# ── 自检错误码 (手册 Table 4-1, 第41页) ──────────────────────
SELFTEST_ERRORS: dict[int, str] = {
    0: "无错误",
    1: "非易失RAM RD0区校验和失败",
    2: "非易失RAM CONFIG区校验和失败",
    3: "非易失RAM CAL区校验和失败",
    4: "非易失RAM STATE区校验和失败",
    5: "非易失RST区校验和失败",
    10: "RAM自检失败",
    11: "VDAC/IDAC自检1失败",
    12: "VDAC/IDAC自检2失败",
    13: "VDAC/IDAC自检3失败",
    14: "VDAC/IDAC自检4失败",
    15: "OVDAC自检失败",
    80: "数字I/O自检失败",
}
# 需要返修的自检错误码（硬件级故障）
SELFTEST_SERVICE_CODES = {1, 2, 3, 4, 5, 10, 11, 12, 13, 14, 15, 80}

# ── Questionable 状态位 (手册 Table 8-5, 第119页, 带bit weight权威确认) ──
#   bit4=OT过温(非OS), bit5=SD/OS传感线开路(注意此处易混淆)
QUES_BITS: dict[int, str] = {
    0: "OV 过压保护跳闸",
    1: "OCP 过流保护跳闸",
    3: "FP 前面板Local键按下",
    4: "OT 过温保护跳闸",
    5: "SD/OS 传感线开路检测",
    8: "UNR2 输出2失压(仅66319B/D)",
    9: "RI 远程禁止激活",
    10: "UNR 输出失压",
    12: "OC2 输出2过流(仅66319B/D)",
    14: "MeasOvld 测量量程超限",
}

# ── 标准事件状态位 (手册 Table 7-1, 第84页) ───────────────────
ESR_BITS: dict[int, str] = {
    0: "OPC 操作完成",
    2: "QYE 查询错误",
    3: "DDE 设备相关错误(自检/硬件类)",
    4: "EXE 执行错误",
    5: "CME 命令错误",
    7: "PON 上电",
}

# ── 测量无效占位符判定阈值 ─────────────────────────────────
# 66300 系列测量无效时返回 9.91000E+37
MEAS_INVALID_THRESHOLD = 1e30


def _decode_register(value: int, bit_map: dict[int, str]) -> list[dict]:
    """通用状态寄存器位解码，返回置位位的列表。

    每项: {"bit": n, "weight": 2^n, "signal": 说明}
    """
    result = []
    for bit, desc in sorted(bit_map.items()):
        if value & (1 << bit):
            result.append({"bit": bit, "weight": 1 << bit, "signal": desc})
    return result


def _check_inst(inst) -> None:
    """确认会话是 VISA 仪器。"""
    if not hasattr(inst, "query") or not hasattr(inst, "write"):
        raise RuntimeError(
            "当前会话不是 VISA 仪器。请用 "
            'connect(address="GPIB0::5::INSTR", instrument_type="keysight_ps") 连接'
        )


def _safe_query(inst, cmd: str, timeout_notes: list[str] | None = None) -> str:
    """容错查询：超时/异常时返回 "error: <msg>"，不抛出中断整体诊断。

    timeout_notes 若提供，超时异常会追加一条提示（某些查询超时本身是诊断信号）。
    """
    try:
        return inst.query(cmd).strip()
    except Exception as e:  # noqa: BLE001 - 诊断需要捕获所有I/O异常
        msg = str(e)
        if timeout_notes is not None and "TMO" in msg or "Timeout" in msg:
            timeout_notes.append(f"{cmd} 超时(可能该功能不可用或内部状态异常)")
        return f"error: {msg}"


def _is_meas_invalid(raw: str) -> bool:
    """判断测量返回值是否为无效占位符(9.91e+37)或错误字符串。"""
    if not raw or raw.startswith("error"):
        return True
    try:
        return abs(float(raw)) > MEAS_INVALID_THRESHOLD
    except ValueError:
        return True


def _parse_int(raw: str) -> int | None:
    """安全解析仪器返回的整数字符串（如 *TST? -> +1）。"""
    if not raw or raw.startswith("error"):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_float(raw: str) -> float | None:
    """安全解析仪器返回的浮点字符串。"""
    if not raw or raw.startswith("error"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def ps_health_check(inst) -> str:
    """一键只读健康检查。

    执行完整排查流程（不改仪器状态）：自检 -> 错误队列 -> 状态寄存器解码 ->
    保护配置 -> 测量有效性判定，返回综合 verdict。

    verdict 取值:
      - NEEDS_SERVICE: *TST? 非零 或 错误队列含自检错误码(1-5,10-15,80)
      - WARNING:       保护位置位 或 测量返回无效占位符
      - USABLE:        一切正常
    """
    _check_inst(inst)

    timeout_notes: list[str] = []
    result: dict = {}

    # ── 标识 ───────────────────────────────────────
    idn = _safe_query(inst, "*IDN?")
    idn_parts = [p.strip() for p in idn.split(",")] if not idn.startswith("error") else []
    result["idn"] = idn
    result["manufacturer"] = idn_parts[0] if len(idn_parts) > 0 else None
    result["model"] = idn_parts[1] if len(idn_parts) > 1 else None
    result["serial"] = idn_parts[2] if len(idn_parts) > 2 else None
    result["firmware"] = idn_parts[3] if len(idn_parts) > 3 else None

    # ── 自检 *TST? ────────────────────────────────
    tst_raw = _safe_query(inst, "*TST?", timeout_notes)
    tst_code = _parse_int(tst_raw)
    result["selftest"] = {
        "raw": tst_raw,
        "code": tst_code,
        "description": SELFTEST_ERRORS.get(tst_code, f"未知自检错误码: {tst_code}")
        if tst_code is not None
        else "无法解析自检结果",
        "passed": tst_code == 0,
    }

    # ── 错误队列 SYST:ERR? 排空 ───────────────────
    errors: list[dict] = []
    for _ in range(20):
        err_raw = _safe_query(inst, "SYST:ERR?")
        if err_raw.startswith("error") or not err_raw:
            errors.append({"raw": err_raw, "parse_error": True})
            break
        # 格式: +1,"Non-volatile RAM RDO section checksum failed"
        parts = err_raw.split(",", 1)
        code_str = parts[0].strip().lstrip("+")
        message = parts[1].strip().strip('"') if len(parts) > 1 else ""
        try:
            code = int(code_str)
        except ValueError:
            errors.append({"raw": err_raw, "parse_error": True})
            break
        if code == 0:
            break
        # 自检类错误码翻译
        desc = SELFTEST_ERRORS.get(code)
        errors.append({"code": code, "message": message, "selftest_desc": desc})
    result["error_queue"] = errors
    result["error_queue_count"] = len(errors)

    # ── 标准事件状态 *ESR? ────────────────────────
    esr_raw = _safe_query(inst, "*ESR?")
    esr_val = _parse_int(esr_raw)
    result["esr"] = {
        "raw": esr_raw,
        "value": esr_val,
        "bits": _decode_register(esr_val, ESR_BITS) if esr_val is not None else [],
    }

    # ── Questionable 状态 ────────────────────────
    ques_cond_raw = _safe_query(inst, "STAT:QUES:COND?")
    ques_cond_val = _parse_int(ques_cond_raw)
    ques_even_raw = _safe_query(inst, "STAT:QUES:EVEN?")
    ques_even_val = _parse_int(ques_even_raw)
    result["questionable"] = {
        "condition_raw": ques_cond_raw,
        "condition_value": ques_cond_val,
        "condition_bits": _decode_register(ques_cond_val, QUES_BITS)
        if ques_cond_val is not None
        else [],
        "event_raw": ques_even_raw,
        "event_value": ques_even_val,
        "event_bits": _decode_register(ques_even_val, QUES_BITS)
        if ques_even_val is not None
        else [],
    }

    # ── 输出状态与设定值 ──────────────────────────
    result["output_state"] = _safe_query(inst, "OUTP?")
    result["voltage_set"] = _safe_query(inst, "VOLT?")
    result["current_set"] = _safe_query(inst, "CURR?")

    # ── 保护配置 ──────────────────────────────────
    result["protection_config"] = {
        "ovp_threshold": _safe_query(inst, "VOLT:PROT?", timeout_notes),
        "ovp_enabled": _safe_query(inst, "VOLT:PROT:STAT?", timeout_notes),
        "ocp_enabled": _safe_query(inst, "CURR:PROT:STAT?", timeout_notes),
        "prot_delay": _safe_query(inst, "OUTP:PROT:DEL?", timeout_notes),
        "ri_mode": _safe_query(inst, "OUTP:RI:MODE?", timeout_notes),
    }

    # ── 测量值与有效性 ────────────────────────────
    vmeas_raw = _safe_query(inst, "MEAS:VOLT?")
    cmeas_raw = _safe_query(inst, "MEAS:CURR?")
    result["measurement"] = {
        "voltage_raw": vmeas_raw,
        "voltage_value": _parse_float(vmeas_raw),
        "voltage_valid": not _is_meas_invalid(vmeas_raw),
        "current_raw": cmeas_raw,
        "current_value": _parse_float(cmeas_raw),
        "current_valid": not _is_meas_invalid(cmeas_raw),
    }

    # ── 超时备注 ──────────────────────────────────
    result["timeout_notes"] = timeout_notes

    # ── 综合 verdict ──────────────────────────────
    # 1) 需返修：自检非零 或 错误队列含自检错误码
    needs_service = False
    service_reasons: list[str] = []
    if tst_code is not None and tst_code != 0:
        needs_service = True
        service_reasons.append(
            f"*TST?={tst_code}({SELFTEST_ERRORS.get(tst_code, '未知')})"
        )
    for e in errors:
        if e.get("code") in SELFTEST_SERVICE_CODES:
            needs_service = True
            service_reasons.append(f"错误队列含自检错误 {e['code']}({e.get('selftest_desc', e.get('message', ''))})")

    # 2) 警告：保护位置位 或 测量无效
    has_warning = False
    warning_reasons: list[str] = []
    if ques_cond_val is not None and ques_cond_val != 0:
        has_warning = True
        bits_desc = ", ".join(
            b["signal"] for b in result["questionable"]["condition_bits"]
        )
        warning_reasons.append(f"保护位置位({bits_desc})")
    if not result["measurement"]["voltage_valid"]:
        has_warning = True
        warning_reasons.append("电压测量返回无效占位符(9.91e+37)")
    if not result["measurement"]["current_valid"]:
        has_warning = True
        warning_reasons.append("电流测量返回无效占位符(9.91e+37)")

    if needs_service:
        verdict = "NEEDS_SERVICE"
    elif has_warning:
        verdict = "WARNING"
    else:
        verdict = "USABLE"

    result["verdict"] = verdict
    result["verdict_reasons"] = service_reasons if needs_service else warning_reasons
    result["verdict_advice"] = (
        "自检错误持续(冷启动后仍存在)，按手册第41页判定需返修：更换NVRAM+重新校准"
        if needs_service
        else "测量或保护状态异常，建议排查接线/保护配置，详见各字段"
        if has_warning
        else "仪器状态正常，可正常使用"
    )

    return json.dumps(result, ensure_ascii=False, indent=2)


def ps_clear_protection(inst, disable_ovp: bool = False) -> str:
    """清除输出保护锁存。

    手册第111页: "All conditions that generate the fault must be removed
    before the latch can be cleared." 即故障源必须先消除，否则清不掉。

    参数:
      disable_ovp - True 时先关闭过压保护(VOLT:PROT:STAT OFF)再清保护。
                    用于排查 OV 是否为误报。默认 False。
    """
    _check_inst(inst)

    def _qu_cond() -> int | None:
        return _parse_int(_safe_query(inst, "STAT:QUES:COND?"))

    # ── 清前状态 ──────────────────────────────────
    ques_before = _qu_cond()
    bits_before = _decode_register(ques_before, QUES_BITS) if ques_before is not None else []

    actions: list[str] = []

    # ── 可选：关闭 OVP ────────────────────────────
    if disable_ovp:
        try:
            inst.write("VOLT:PROT:STAT OFF")
            actions.append("VOLT:PROT:STAT OFF (关闭过压保护)")
            time.sleep(0.3)
        except Exception as e:  # noqa: BLE001
            actions.append(f"VOLT:PROT:STAT OFF 失败: {e}")

    # ── 清状态 + 清保护锁存 ──────────────────────
    try:
        inst.write("*CLS")
        actions.append("*CLS (清状态)")
    except Exception as e:  # noqa: BLE001
        actions.append(f"*CLS 失败: {e}")
    try:
        inst.write("OUTP:PROT:CLE")
        actions.append("OUTP:PROT:CLE (清保护锁存)")
    except Exception as e:  # noqa: BLE001
        actions.append(f"OUTP:PROT:CLE 失败: {e}")
    time.sleep(0.5)

    # ── 清后状态 ──────────────────────────────────
    ques_after = _qu_cond()
    bits_after = _decode_register(ques_after, QUES_BITS) if ques_after is not None else []

    cleared = ques_after == 0
    # 若故障源仍存在，QUES 不会清零
    if ques_before is not None and ques_after is not None and ques_after == ques_before:
        cleared = False

    result = {
        "status": "PASS",
        "ques_before": {
            "value": ques_before,
            "bits": bits_before,
        },
        "ques_after": {
            "value": ques_after,
            "bits": bits_after,
        },
        "actions": actions,
        "cleared": cleared,
        "advice": (
            "保护已清除，输出可正常开启"
            if cleared
            else "保护未清除：故障源仍存在(手册第111页)。"
            "请先消除根因(过压/过流/过温/传感线开路/远程禁止)，"
            "或确认是否为NVRAM故障导致的误报(跑 ps_health_check 查看 verdict)"
        ),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)
