"""CMW500 LTE Signaling/Measurement 参数关联处理器。

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

2026-09-18 新增 LTE Meas 测量应用与 CQI 调度处理器（详见 docs/cmw500_lte_meas.md）:
4. LTE:SIGN 与 LTE:MEAS 两棵指令树可同时寻址（无需 INST:SEL 切换）;
   INST:SEL 只接受测量类应用实例（MEAS/IQSETUPTEST），信令应用拒绝 -200。
5. MEValuation 结果按统计档（CURRent/AVERage/MAXimum）各 29 字段,
   NAV/NCAP/INV 占位需容错解析。cmw_meas_tx_report / cmw_meas_run_once 封装。
6. CQI 动态调度（STYPe CQI,FWB）实测: 切换即 RRC 重配开启 CQI 上报，但
   Cat.1 模组约 7s 后 UL out of Sync 掉链（详见文档）。cmw_cqi_set 提供
   先切后连（连接建立前配置）的重试路径。

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


# ═══════════════════════════════════════════════
# LTE Meas 测量应用（2026-09-18 固件 3.7.110 实测）
# LTE:MEAS 指令树与 LTE:SIGN 平级、可同时寻址（无需 INST:SEL 切换）。
# MEValuation（多评估）= UE 上行调制质量主测量，结果 29 字段。
# ═══════════════════════════════════════════════

# FETCh:LTE:MEAS:MEValuation:MODulation:<stat>? 的 29 字段
# （顺序与仪器响应一致；NAV/NCAP/INV 为对应场景下的占位值）
_MOD_FIELDS = [
    "可靠性", "超限率%", "EVM_RMS低窗", "EVM_RMS高窗", "EVM峰值低窗", "EVM峰值高窗",
    "幅误RMS低", "幅误RMS高", "幅误峰低", "幅误峰高",
    "相误RMS低", "相误RMS高", "相误峰低", "相误峰高",
    "IQ偏移dBc", "频偏Hz", "时偏Ts", "TX功率dBm", "峰值功率dBm", "PSD",
    "EVM_DMRS低", "EVM_DMRS高", "幅误DMRS低", "幅误DMRS高", "相误DMRS低", "相误DMRS高",
    "IQ增益失衡dB", "IQ正交误差deg", "EVM_SRS",
]

# FETCh:LTE:MEAS:MEValuation:ACLR:<stat>? 的 8 字段
_ACLR_FIELDS = [
    "可靠性", "UTRA2邻道-低dB", "UTRA1邻道-低dB", "EUTRA邻道-低dB",
    "载波信道功率dBm", "EUTRA邻道+高dB", "UTRA1邻道+高dB", "UTRA2邻道+高dB",
]

_ESF_FIELDS = [
    "可靠性", "超限率%", "波纹1dB", "波纹2dB", "maxR1-minR2", "maxR2-minR1",
    "minR1", "maxR1", "minR2", "maxR2",
]


def _cells(raw: str) -> list[str]:
    return [c.strip().strip('"') for c in raw.split(",")]


def _g(token: str) -> str:
    """科学计数转紧凑显示（1.000000E+002 -> 100）；占位值原样返回。"""
    try:
        return f"{float(token):g}"
    except ValueError:
        return token


def _pair(fields: list[str], values: list[str]) -> str:
    """字段名=值 对齐输出，数字走 _g 紧凑显示；字段数多于值时截断到值长度。"""
    n = min(len(fields), len(values))
    vals = [_g(v) for v in values[:n]]
    w = max(len(f) for f in fields[:n])
    return "\n".join(f"  {fields[i].ljust(w)} = {vals[i]}" for i in range(n))


def _meas_fetch(inst, path: str, statistic: str) -> list[str]:
    raw = _q(inst, f"{path}:{statistic}?").strip()
    return _cells(raw)


def cmw_meas_tx_report(inst, statistic: str = "AVERage") -> str:
    """读取 LTE Meas MEValuation 全套结果并格式化（调制质量/ACLR/平坦度/带内发射）。

    只读。测量未运行或无数据时各字段为 NAV/NCAP/INV——原样显示不报错。
    statistic: CURRent | AVERage | MAXimum（EXTReme/SDEViation 同样有效）。
    """
    statistic = statistic.strip().upper()
    sections = [
        ("调制质量", "FETC:LTE:MEAS:MEValuation:MODulation", _MOD_FIELDS),
        ("ACLR 邻道泄漏比", "FETC:LTE:MEAS:MEValuation:ACLR", _ACLR_FIELDS),
        ("频谱平坦度", "FETC:LTE:MEAS:MEValuation:ESFLatness", _ESF_FIELDS),
    ]
    out = [f"LTE Meas MEValuation 报告（统计档 {statistic}）:"]
    try:
        out.append("测量状态: " + _q(inst, "FETC:LTE:MEAS:MEValuation:STATe:ALL?"))
    except Exception as e:
        out.append(f"测量状态: <查询失败: {e}>")
    for title, path, fields in sections:
        try:
            vals = _meas_fetch(inst, path, statistic)
            out.append(f"{title}:")
            out.append(_pair(fields, vals))
        except Exception as e:
            out.append(f"{title}: <查询失败: {e}>")
    try:
        vals = _meas_fetch(inst, "FETC:LTE:MEAS:MEValuation:IEMission:CC1:MARGin", statistic)
        if len(vals) >= 3:
            out.append(f"带内发射余量: {_g(vals[2])} dB（正值=有裕量；超限率 {_g(vals[1])}%）")
        else:
            out.append(f"带内发射余量: {vals}")
    except Exception as e:
        out.append(f"带内发射余量: <查询失败: {e}>")
    _drain(inst)
    return "\n".join(out)


def cmw_meas_run_once(inst, timeout_s: int = 15, statistic: str = "AVERage") -> str:
    """一键单发 UE 上行测量：INIT MEValuation → 轮询状态到 RDY → 返回格式化报告。

    前提：UE 上行有信号（信令连接 CEST 且 PUSCH 被调度最理想；ATT 空闲态只能
    抓到零星突发，可能一直等不到触发）。要求场景为 CSP（信令+测量共用路径），
    可先用 cmw_meas_set_scenario_cspath 设置。单发完成后状态回 RDY，可反复调用。
    """
    import time

    scen = _q(inst, "ROUT:LTE:MEAS:SCENario?")
    if scen != "CSP":
        return (f"[FAIL] 当前 Meas 场景 {scen}（非 CSP）。信令+测量联动请先调 "
                f"cmw_meas_set_scenario_cspath（master 填信令应用全名，本机 'LTE Sig1'）。")
    _drain(inst)
    inst.write("INIT:LTE:MEAS:MEValuation")
    err = _err(inst)
    if not err.startswith('0,"'):
        return f"[FAIL] INIT 失败: {err}"
    deadline = time.time() + max(3, int(timeout_s))
    state = "?"
    while time.time() < deadline:
        state = _q(inst, "FETC:LTE:MEAS:MEValuation:STATe?")
        if state == "RDY":
            break
        time.sleep(1)
    if state != "RDY":
        return (f"[FAIL] {timeout_s}s 内测量未完成（状态 {state}）。"
                f"检查 UE 上行是否有连续信号（ATT 空闲态无 PUSCH 会等不到触发）。")
    return f"[PASS] 单发测量完成。\n" + cmw_meas_tx_report(inst, statistic)


def cmw_cqi_set(inst, mode: str = "FWB", number_rb: int = 25,
                start_rb: int = 0, table: str = "DET") -> str:
    """切换调度类型到 CQI 并配置 DL（参数关联版）。

    STYPe CQI,<mode>：TTIBased=固定 CQI / FWB=跟随宽带 CQI / FPMI / FCPRi / FCRI / FPRI。
    FWB/FPMI/FCPRi/FCRI/FPRI 下 DL RB 配置走 CONNection:PCC:FWBCqi:DL（等命令组），
    table=DET 用仪器自动 CQI→MCS 映射（SENSe:...:MCSTable:DETermined? 可读出映射表）。

    ⚠ 实测风险（2026-09-18，Cat.1 模组）：连接中（CEST）切换会触发 RRC 重配
    "Wideband CQI Reports ON"，本机模组约 7s 后 UL out of Sync 掉链（CEST→ON），
    且之后 >60min 未自主重附（对比干净 Detach 后 4.6min；小区重启也唤不醒，
    疑似深度 PSM/协议栈卡死，需人工重启模组）。
    推荐路径：先在无连接时调用本命令（小区 ON 即可），再让 UE 连接，让 CQI 配置
    随连接建立一并下发。等待重附用 cmw_wait_ue_state。
    恢复静态调度：cmw_set_scheduling_type RMC。
    """
    mode = mode.strip().upper()
    table = table.strip().upper()
    _drain(inst)
    inst.write(f"CONF:LTE:SIGN:CONNection:PCC:STYPe CQI,{mode}")
    err = _err(inst)
    if not err.startswith('0,"'):
        return f"[FAIL] STYPe CQI,{mode} 写入失败: {err}"
    dl_note = ""
    if mode != "TTIBASED":
        _drain(inst)
        inst.write(f"CONF:LTE:SIGN:CONNection:PCC:FWBCqi:DL {int(number_rb)},{int(start_rb)},{table}")
        err = _err(inst)
        if not err.startswith('0,"'):
            return (f"[FAIL] FWBCqi:DL 写入失败: {err}（CQI 模式已生效，DL 配置未改）")
        dl = _q(inst, "CONF:LTE:SIGN:CONNection:PCC:FWBCqi:DL?")
        dl_note = f"；DL 配置(RB,起始RB,映射表) = {dl}"
    stype = _q(inst, "CONF:LTE:SIGN:CONNection:PCC:STYPe?")
    ps = _q(inst, "FETC:LTE:SIGN:PSWitched:STATe?")
    return (f"[PASS] 调度类型 = {stype}{dl_note}；连接状态 = {ps}\n"
            f"提示: 若在连接中切换，请观察是否出现 UL out of Sync 掉链（Event Log）；"
            f"CQI 统计用 cmw_cqi_stats，MCS 映射表见 SENSe:...:MCSTable:DETermined?")


def cmw_cqi_stats(inst) -> str:
    """读取 CQI 动态调度的远程观察窗口（只读）：UE 上报 CQI 统计 + CQI→MCS 映射表。

    CQI 统计来自信令应用 eBLER 测量的 CQI Reporting 视图（需要 INIT:LTE:SIGN:EBLer
    启动后才累积；CQI 中位值即 UE 实测信道质量的量化）。MCS 映射表（DETermined 模式）
    展示各 CQI 档位对应的 DL MCS——下行功率变化时 CQI 中位值移动，MCS 随映射表跟随。
    """
    out = ["CQI 动态调度观察:"]
    try:
        v = _cells(_q(inst, "FETCh:LTE:SIGN:EBLer:PCC:CQIReporting:STReam1?"))
        if len(v) >= 7:
            out.append(f"  CQI中位值   = {v[1]}（NAV=尚无上报）")
            out.append(f"  中位±1占比  = {_g(v[3])}%（{v[2]} 条）")
            out.append(f"  DL BLER     = {_g(v[4])}%")
            out.append(f"  累计CQI报告 = {v[5]} 条 / 已调度子帧 {v[6]}")
        else:
            out.append(f"  CQI 统计原始: {v}")
    except Exception as e:
        out.append(f"  CQI 统计: <查询失败: {e}>")
    try:
        st = _q(inst, "FETCh:LTE:SIGN:EBLer:STATe:ALL?")
        out.append(f"  eBLER测量状态 = {st}（OFF 时 CQI 统计不累积，需 INIT:LTE:SIGN:EBLer）")
    except Exception as e:
        out.append(f"  eBLER测量状态: <查询失败: {e}>")
    try:
        tbl = _cells(_q(inst, "SENSe:LTE:SIGN:CONNection:PCC:FWBCqi:DL:MCSTable:DETermined?"))
        out.append(f"  CQI→MCS映射(DET) = {','.join(tbl)}")
        out.append("  （首项为可靠性指示，其后为 CQI 低→高对应的 DL MCS；27 为本机上限）")
    except Exception as e:
        out.append(f"  MCS 映射表: <查询失败: {e}>")
    _drain(inst)
    return "\n".join(out)
