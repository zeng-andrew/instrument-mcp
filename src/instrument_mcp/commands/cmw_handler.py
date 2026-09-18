"""CMW500 LTE Signaling 参数关联处理器。

沉淀 2026-09-17 于固件 3.7.110 真机实测确认的参数联动关系（详见
docs/cmw500_lte_signaling.md）:

1. DL RMC 的 RB 数受 Cell Bandwidth 限制: 必须先改带宽才能改 DL RB。
   违规时仪器报 -203 "Command protected; option missing"（文案有误导性，
   并非缺选件）。实测: B100 下仅 N50 可写（N25/N100/N6 均 -203）;
   B50 下 N25 可写。cmw_set_dl_rb 负责自动补齐带宽前置。
2. UL RMC 用 64QAM/256QAM 前必须打开 CELL:PCC:ULSupport:QAM64/256:ENABle，
   否则报 -221 Settings conflict（本机默认 OFF）。cmw_set_ul_rb 负责检查/自动开启。
3. 改带宽后 RMC:UL 与 UDCHannels 的值会被仪器自动钳位到新带宽下的合法值
   （正常行为，非配置丢失）。

本模块函数经 cmw.yaml 的 handler 字段挂接为 MCP tool，inst 为已连接会话
（VisaInstrument，CMW500 走 TCPIP socket 地址），只需 .write()/.query()。
"""

from __future__ import annotations

import re

# 不同信道带宽(MHz)下实测/推断可写的 DL RMC RB 数。
# verified=True 的条目为真机实测；其余为按 3GPP 满带宽 RMC 的推断值，
# 使用时若仪器报 -203 则说明推断不成立，请回填实测结果。
_DL_RMC_ALLOWED: dict[int, tuple[list[str], bool]] = {
    100: (["N50"], True),   # B100: N50 实测可写; N25/N100/N6 实测 -203
    50: (["N25"], True),    # B50:  N25 实测可写（GUI 修改后读回确认）
    25: (["N25"], False),   # B25(5MHz):  推断满带宽 RMC
    15: (["N15"], False),   # B15(3MHz):  推断
    6: (["N6"], False),     # B6(1.4MHz): 推断
}

# 写带宽用仪器返回的同款格式（B050 而非 B50）
def _bw_token(mhz: int) -> str:
    return f"B{mhz:03d}"


def _q(inst, cmd: str) -> str:
    return inst.query(cmd).strip()


def _err(inst) -> str:
    return inst.query("SYST:ERR?").strip()


def _drain(inst) -> None:
    """排空 SCPI 错误队列。写前调用，防止残留错误被误算到本次写入头上。"""
    for _ in range(20):
        if _err(inst).startswith('0,"'):
            return


def _parse_rb(token: str) -> int:
    """'N25' -> 25。"""
    m = re.fullmatch(r"[Nn](\d{1,3})", token.strip())
    if not m:
        raise ValueError(f"number_rb 应为 N<数> 形式（如 N25/N50/N100），收到: {token!r}")
    return int(m.group(1))


def cmw_wait_ue_state(inst, target: str = "ATT", timeout_s: int = 300) -> str:
    """轮询等待 UE 状态到达 target（ATT/CEST/ON/OFF），超时返回当前状态。

    背景：休眠类模组搜网极慢（实测 Detach 后 4.6 分钟自动重附），开小区后
    人工反复查询不现实，此工具封一次"等到为止"。只读轮询，不发控制命令。
    """
    import time

    target = target.strip().upper()
    deadline = time.time() + max(5, int(timeout_s))
    last = None
    while time.time() < deadline:
        try:
            st = inst.query("FETC:LTE:SIGN:PSWitched:STATe?").strip().upper()
        except Exception:
            time.sleep(2)
            continue
        if st != last:
            print(f"[{time.strftime('%H:%M:%S')}] PS={st}", flush=True)
            last = st
        if st == target:
            return f"[PASS] UE 已到达目标状态 {target}（等待中经过状态: {st}）"
        time.sleep(2)
    return f"[FAIL] 等待 {timeout_s}s 超时，目标 {target} 未到达，当前 {last}"


def cmw_lte_snapshot(inst) -> str:
    """一键读取 LTE Signaling 面板全部关键状态，返回对齐文本。

    只读操作。单项查询失败不影响其余项（标 <查询失败>）。
    """
    items = [
        ("小区状态", "SOUR:LTE:SIGN:CELL:STATe:ALL?"),
        ("UE连接状态", "FETC:LTE:SIGN:PSWitched:STATe?"),
        ("UE IP(每承载一个)", "SENS:LTE:SIGN:UESinfo:UEADdress:IPV4?"),
        ("双工模式", "CONF:LTE:SIGN:DMODe?"),
        ("频段", "CONF:LTE:SIGN:PCC:BAND?"),
        ("DL信道(EARFCN)", "CONF:LTE:SIGN:RFSettings:PCC:CHANnel:DL?"),
        ("UL信道(EARFCN)", "CONF:LTE:SIGN:RFSettings:PCC:CHANnel:UL?"),
        ("DL频率(Hz)", "CONF:LTE:SIGN:RFSettings:PCC:FREQuency:DL?"),
        ("UL频率(Hz)", "CONF:LTE:SIGN:RFSettings:PCC:FREQuency:UL?"),
        ("DL带宽", "CONF:LTE:SIGN:CELL:BANDwidth:PCC:DL?"),
        ("UL带宽", "CONF:LTE:SIGN:CELL:BANDwidth:PCC:UL?"),
        ("调度类型", "CONF:LTE:SIGN:CONNection:PCC:STYPe?"),
        ("RMC上行(RB/调制/TBS)", "CONF:LTE:SIGN:CONNection:PCC:RMC:UL?"),
        ("RMC下行(RB/调制/TBS)", "CONF:LTE:SIGN:CONNection:PCC:RMC:DL?"),
        ("RMC下行RB位置", "CONF:LTE:SIGN:CONNection:PCC:RMC:RBPosition:DL?"),
        ("用户自定义上行", "CONF:LTE:SIGN:CONNection:PCC:UDCHannels:UL?"),
        ("用户自定义下行", "CONF:LTE:SIGN:CONNection:PCC:UDCHannels:DL?"),
        ("PCID", "CONF:LTE:SIGN:CELL:PCID?"),
        ("下行小区功率RS EPRE(dBm)", "CONF:LTE:SIGN:DL:PCC:RSEPre:LEVel?"),
        ("UE期望功率(dBm)", "CONF:LTE:SIGN:RFSettings:PCC:ENPower?"),
        ("用户裕量(dB)", "CONF:LTE:SIGN:RFSettings:PCC:UMARgin?"),
        ("输入衰减(dB)", "CONF:LTE:SIGN:RFSettings:PCC:EATTenuation:INPut?"),
        ("输出衰减(dB)", "CONF:LTE:SIGN:RFSettings:PCC:EATTenuation:OUTPut1?"),
        ("上行TPC模式", "CONF:LTE:SIGN:UL:PCC:PUSCh:TPC:SET?"),
        ("UL 64QAM支持", "CONF:LTE:SIGN:CELL:PCC:ULSupport:QAM64:ENABle?"),
        ("最近Event Log", "SENS:LTE:SIGN:ELOG:LAST? HRES"),
    ]
    width = max(len(k) for k, _ in items)
    lines = []
    for label, cmd in items:
        try:
            lines.append(f"{label.ljust(width)} : {_q(inst, cmd)}")
        except Exception as e:
            lines.append(f"{label.ljust(width)} : <查询失败: {e}>")
    _drain(inst)  # 失败查询会把错误码留在队列里，清掉避免污染后续操作
    return "CMW500 LTE Signaling 面板快照:\n" + "\n".join(lines)


def cmw_set_dl_rb(inst, number_rb: str, modulation: str = "QPSK",
                  tbs_index: str = "KEEP", auto_bandwidth: bool = True) -> str:
    """带参数关联校验的下行 RMC RB 设置。

    自动处理 "带宽优先" 关联:
      - 当前带宽下 RB 数不可写时: auto_bandwidth=True 则自动切到支持该 RB 的
        最小带宽（先改带宽再改 RB，规避 -203）；False 则拒绝并给出说明。
      - 注意: 若带宽因此改变，RMC:UL/UDCHannels 会被仪器自动钳位，返回值中会报告。

    参数: number_rb 如 "N25"；modulation: QPSK/Q16/Q64/Q256；
          tbs_index: T1~T37 或 KEEP（由仪器选兼容值）。
    """
    rb = _parse_rb(number_rb)
    bw_now = _q(inst, "CONF:LTE:SIGN:CELL:BANDwidth:PCC:DL?")  # 形如 B050
    m = re.fullmatch(r"[Bb](\d+)", bw_now)
    bw_mhz = int(m.group(1)) if m else -1

    rb_token = f"N{rb}"
    allowed, verified = _DL_RMC_ALLOWED.get(bw_mhz, ([], False))
    if rb_token not in allowed:
        # 候选档位：实测验证过的优先，其次按带宽升序
        cands = [(mhz, v) for mhz, (lst, v) in sorted(_DL_RMC_ALLOWED.items())
                 if rb_token in lst]
        if not cands:
            return (f"[FAIL] RB={rb} 在已知带宽档位中均不可用（含实测: B100 下 N100 也被拒）。"
                    f"建议改用调度类型 UDCHannels + UDCHannels:DL 配任意 RB 数。")
        if not auto_bandwidth:
            toks = [_bw_token(mhz) for mhz, _ in cands]
            return (f"[FAIL] 当前带宽 {bw_now} 下 RB={rb} 不可写（会报 -203 option missing，"
                    f"实为带宽约束而非缺选件）。可用带宽: {toks}。"
                    f"设 auto_bandwidth=true 可自动切带宽。")
        target, target_verified = min(cands, key=lambda x: (not x[1], x[0]))
        _drain(inst)
        for cmd in ("CONF:LTE:SIGN:CELL:BANDwidth:PCC:DL",
                    "CONF:LTE:SIGN:CELL:BANDwidth:PCC:UL"):
            inst.write(f"{cmd} {_bw_token(target)}")
        err = _err(inst)
        if not err.startswith('0,"'):
            return f"[FAIL] 切带宽到 {_bw_token(target)} 失败: {err}"
        bw_note = f"带宽已自动切换 {bw_now} -> {_bw_token(target)}（DL+UL 同步）"
    else:
        bw_note = f"带宽 {bw_now} 保持不变"

    _drain(inst)
    inst.write(f"CONF:LTE:SIGN:CONNection:PCC:RMC:DL {number_rb},{modulation},{tbs_index}")
    err = _err(inst)
    if not err.startswith('0,"'):
        return f"[FAIL] RMC:DL 写入失败: {err}（当前带宽 {_q(inst, 'CONF:LTE:SIGN:CELL:BANDwidth:PCC:DL?')}）"

    rb_now = _q(inst, "CONF:LTE:SIGN:CONNection:PCC:RMC:DL?")
    ul_now = _q(inst, "CONF:LTE:SIGN:CONNection:PCC:RMC:UL?")
    ud_now = _q(inst, "CONF:LTE:SIGN:CONNection:PCC:UDCHannels:DL?")
    table_note = "" if verified else "（本带宽档位为推断值，未实测）"
    return (f"[PASS] 下行 RMC = {rb_now}；{bw_note}{table_note}\n"
            f"联动状态: RMC:UL={ul_now}（改带宽会被自动钳位，如与预期不符请用 cmw_set_rmc_ul 重设）\n"
            f"          UDCHannels:DL={ud_now}（同样被钳位，仅调度类型切到 UDCHannels 时生效）")


def cmw_set_ul_rb(inst, number_rb: str, modulation: str = "QPSK",
                  tbs_index: str = "KEEP", auto_qam: bool = True) -> str:
    """带参数关联校验的上行 RMC RB 设置。

    自动处理 "高阶调制需先开支持开关" 关联:
      - modulation 为 Q64/Q256 时先查 CELL:PCC:ULSupport:QAM64/256:ENABle，
        为 OFF 时: auto_qam=True 自动开启再写 RMC；False 则拒绝并说明（-221）。
    参数同 cmw_set_dl_rb；UL RB 数不受带宽限制（N1/N25/N50 均实测可写）。
    """
    rb = _parse_rb(number_rb)
    qam_note = ""
    if modulation in ("Q64", "Q256"):
        # 本机 ULSupport 只有 QAM64 后缀可用（QAM256 查询被仪器拒 -114），
        # 64QAM 开关是 UL 高阶调制的前置；Q256 的独立开关如存在需 GUI 确认
        state = _q(inst, "CONF:LTE:SIGN:CELL:PCC:ULSupport:QAM64:ENABle?")
        if state == "OFF":
            if not auto_qam:
                return (f"[FAIL] UL {modulation} 需要先开 ULSupport:QAM64:ENABle，"
                        f"否则 -221 Settings conflict。设 auto_qam=true 可自动开启。")
            _drain(inst)
            inst.write("CONF:LTE:SIGN:CELL:PCC:ULSupport:QAM64:ENABle ON")
            err = _err(inst)
            if not err.startswith('0,"'):
                return f"[FAIL] 开启 ULSupport:QAM64 失败: {err}"
            qam_note = "已自动开启 UL 64QAM 支持开关（此前 OFF）; "
        else:
            qam_note = "UL 64QAM 支持已开启; "

    _drain(inst)
    inst.write(f"CONF:LTE:SIGN:CONNection:PCC:RMC:UL {number_rb},{modulation},{tbs_index}")
    err = _err(inst)
    if not err.startswith('0,"'):
        return f"[FAIL] RMC:UL 写入失败: {err}（{qam_note}）"

    rb_now = _q(inst, "CONF:LTE:SIGN:CONNection:PCC:RMC:UL?")
    return f"[PASS] 上行 RMC = {rb_now}；{qam_note}TBS 为 KEEP 时以上述读回值为准（仪器自动选兼容值）"
