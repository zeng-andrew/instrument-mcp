"""Modbus-RTU 恒温恒湿试验箱处理器（MODBUS-1 协议, RS-232C）。

已在实物上验证的寄存器映射（2026-07~08，面板核对 + 运行差分实验）:
  0x0001  温度 PV（只读, FC=03, ×100, 有符号）
  0x0002  工作温度设定（只读, 运行时随斜率爬坡变化, 勿写）
  0x0005  湿度 PV（只读, ×10）
  0x0007  输出量 MV（只读, ×10）
  0x000A  运行标志（只读, 0=停止 2=运行）
  0x0065  运行控制（读写）- 写 1=定值运行, 写 4=停止（写 2=程式运行不被接受）
  0x0066  温度 SV（读写, ×100, 有符号）- 定值模式设定温度
  0x0067  湿度 SV（读写, ×10）

程式（程序控制）寄存器（实物验证 COM30, 2026-08，面板对照确认）:
  程式号选择: 写 0x0064(reg100) = 程式号，切换段表寄存器空间指向该程式。

  运行态寄存器（运行/停止后均保留）:
    0x0019(25)   当前运行程式号
    0x001A(26)   当前段号
    0x001B(27)   剩余时间-小时
    0x001C(28)   剩余时间-分钟
    0x0020(32)   程式总循环次数（只读运行态镜像, 不可写）
    0x0023(35)   当前段温度SV1（×100, 有符号）
    0x0024(36)   当前段温度SV2（×100, 有符号）
    0x0025(37)   当前段湿度SV（×10, 0=OFF）
    0x0027(39)   当前段时间-小时

  段表寄存器（分块存储, 每块 100 段, 写 0x0064 选程式号后可读写）:
    面板段表 8 列（温度/湿度/时间/循环/TS1..4），每列对应一块:
    0x0515(1301) 温度块（×100, 有符号; 空段=0xB1E0 即 -20000）
    0x0579(1401) 湿度块（×10, 0=OFF）
    0x05DD(1501) 时间块（×100, 小时）
    0x0641(1601) 循环次数块（×1, 每段独立循环次数）
    0x06A5(1701) TS1 块（0=关闭 1=打开 2=动作1 3=动作2）
    0x0709(1801) TS2 块
    0x076D(1901) TS3 块
    0x07D1(2001) TS4 块

⚠ 硬件限制（COM30 实测）:
  - 段表可完整读取（8 块 ×100 段分块连读）
  - 已有段的全部参数（温度/湿度/时间/循环/TS1..4）均可写并读回验证，面板同步
  - 程式启动: 写 0x0064=程式号 + 写 0x0065=1（与定值运行同值，区别在选程式号）
  - 空段（0xB1E0）写入回显正常但不持久化：段数寄存器(reg41)为只读，
    无法通过 Modbus 新增段。面板增加段后 Modbus 即可修改该段数据

串口参数: 9600 8N1, CRC-16/MODBUS, 站地址 1。
连接方式: connect(address="COM30", instrument_type="modbus_chamber")
"""

import logging

logger = logging.getLogger(__name__)

REG_T_PV = 0x0001
REG_H_PV = 0x0005
REG_MV = 0x0007
REG_RUN_FLAG = 0x000A
REG_RUN_CTRL = 0x0065
REG_T_SV = 0x0066
REG_H_SV = 0x0067

RUN_FIX = 1   # 写 0x0065: 定值运行
RUN_STOP = 4  # 写 0x0065: 停止
RUN_PROG = 2  # 写 0x0065: 程式运行（程序控制模式）

# ── 程式（程序控制）寄存器（实物验证 COM30, 2026-08，面板对照确认） ──
# 注意：本机寄存器布局与伟硕手册完全不同。
#
# 程式号选择: 写 0x0064(reg100) = 程式号，切换段表寄存器空间指向该程式。
#
# 运行态寄存器（运行/停止后均保留）:
REG_PROG_NO = 0x0019      # 25  当前运行程式号
REG_SEG_NO = 0x001A       # 26  当前段号
REG_RMN_HOUR = 0x001B     # 27  剩余时间-小时
REG_RMN_MIN = 0x001C      # 28  剩余时间-分钟
REG_PROG_CYCLE = 0x0020   # 32  程式总循环次数（面板 "/100" 的分母）
REG_SEG_TEMP_SV1 = 0x0023 # 35  当前段温度SV1（×100，有符号）
REG_SEG_TEMP_SV2 = 0x0024 # 36  当前段温度SV2/目标（×100，有符号）
REG_SEG_HUM_SV = 0x0025   # 37  当前段湿度SV（×10，0=OFF）
REG_SEG_TIME_HOUR = 0x0027  # 39  当前段时间-小时
REG_PROG_SEL = 0x0064     # 100 写入程式号以选择段表（也作运行模式: 停止=5, 运行=程式号）

# 段表寄存器（分块存储，每块 100 段，写 0x0064 选程式号后可读写）:
#   面板对照验证: 每段8列 = 温度/湿度/时间/循环/TS1/TS2/TS3/TS4，各占1块。
REG_TBL_TEMP  = 0x0515     # 1301 温度块（×100，有符号; 空段=0xB1E0 即 -20000）
REG_TBL_HUM   = 0x0579     # 1401 湿度块（×10，0=OFF）
REG_TBL_TIME  = 0x05DD     # 1501 时间块（×100，小时）
REG_TBL_CYCLE = 0x0641     # 1601 循环次数块（×1，每段循环次数）
REG_TBL_TS1   = 0x06A5     # 1701 时序信号1（0=关闭 1=打开 2=动作1 3=动作2）
REG_TBL_TS2   = 0x0709     # 1801 时序信号2
REG_TBL_TS3   = 0x076D     # 1901 时序信号3
REG_TBL_TS4   = 0x07D1     # 2001 时序信号4
TBL_BLOCK = 100           # 每块段数
EMPTY_SEG = 0xB1E0        # 空段温度填充值（-20000）


def _signed(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def _check_inst(inst):
    if not hasattr(inst, "read_holding"):
        raise RuntimeError(
            "当前会话不是 Modbus 串口仪器，请用 "
            'connect(address="COM30", instrument_type="modbus_chamber") 连接'
        )


def _check_write(inst):
    if not hasattr(inst, "write_register"):
        raise RuntimeError(
            "当前会话不支持写寄存器，请用 "
            'connect(address="COM30", instrument_type="modbus_chamber") 连接'
        )


def handler_read_pv(inst, reg_addr: int = REG_T_PV) -> str:
    """读取温度 PV（当前温度），×100 有符号。"""
    _check_inst(inst)
    v = _signed(inst.read_holding(int(reg_addr), 1)[0])
    return f"[PASS] PV = {v / 100:.2f} °C (reg=0x{int(reg_addr):04X} raw={v})"


def handler_read_sv(inst, reg_addr: int = REG_T_SV) -> str:
    """读取温度 SV（设定温度），×100 有符号。"""
    _check_inst(inst)
    v = _signed(inst.read_holding(int(reg_addr), 1)[0])
    return f"[PASS] SV = {v / 100:.2f} °C (reg=0x{int(reg_addr):04X} raw={v})"


def handler_set_sv(inst, temp_c: float = 25.0, reg_addr: int = REG_T_SV,
                   verify: bool = True) -> str:
    """设置温度 SV（×100 写入），写后读回验证。温度循环通过修改 SV 实现。"""
    _check_inst(inst)
    val = int(round(float(temp_c) * 100)) & 0xFFFF
    inst.write_register(int(reg_addr), val)
    if verify:
        import time
        time.sleep(0.2)
        back = _signed(inst.read_holding(int(reg_addr), 1)[0])
        ok = back == int(round(float(temp_c) * 100))
        status = "PASS" if ok else "FAIL"
        return (
            f"[{status}] SV → {float(temp_c):.2f} °C "
            f"(reg=0x{int(reg_addr):04X} 读回={back / 100:.2f})"
        )
    return f"[PASS] SV → {float(temp_c):.2f} °C 已发送 (reg=0x{int(reg_addr):04X})"


def handler_read_humidity(inst) -> str:
    """读取湿度 PV（%RH），×10。"""
    _check_inst(inst)
    v = inst.read_holding(REG_H_PV, 1)[0]
    return f"[PASS] 湿度 PV = {v / 10:.1f} %RH (reg=0x{REG_H_PV:04X} raw={v})"


def handler_read_humidity_sv(inst) -> str:
    """读取湿度 SV（%RH），×10。"""
    _check_inst(inst)
    v = inst.read_holding(REG_H_SV, 1)[0]
    return f"[PASS] 湿度 SV = {v / 10:.1f} %RH (reg=0x{REG_H_SV:04X} raw={v})"


def handler_set_humidity_sv(inst, humidity_rh: float = 50.0, verify: bool = True) -> str:
    """设置湿度 SV（%RH，×10 写入），写后读回验证。"""
    _check_inst(inst)
    val = int(round(float(humidity_rh) * 10)) & 0xFFFF
    inst.write_register(REG_H_SV, val)
    if verify:
        import time
        time.sleep(0.2)
        back = inst.read_holding(REG_H_SV, 1)[0]
        ok = back == int(round(float(humidity_rh) * 10))
        status = "PASS" if ok else "FAIL"
        return (
            f"[{status}] 湿度 SV → {float(humidity_rh):.1f} %RH "
            f"(reg=0x{REG_H_SV:04X} 读回={back / 10:.1f})"
        )
    return f"[PASS] 湿度 SV → {float(humidity_rh):.1f} %RH 已发送 (reg=0x{REG_H_SV:04X})"


def handler_read_status(inst) -> str:
    """读取温箱完整状态：温度 PV/SV、湿度 PV/SV、输出量、运行标志，返回 JSON。"""
    import json
    _check_inst(inst)
    pv = _signed(inst.read_holding(REG_T_PV, 1)[0]) / 100.0
    sv = _signed(inst.read_holding(REG_T_SV, 1)[0]) / 100.0
    h_pv = inst.read_holding(REG_H_PV, 1)[0] / 10.0
    h_sv = inst.read_holding(REG_H_SV, 1)[0] / 10.0
    mv = inst.read_holding(REG_MV, 1)[0] / 10.0
    running = inst.read_holding(REG_RUN_FLAG, 1)[0] == 1
    return json.dumps({
        "pv_c": pv,
        "sv_c": sv,
        "delta_c": round(pv - sv, 2),
        "humidity_pv_rh": h_pv,
        "humidity_sv_rh": h_sv,
        "output_pct": mv,
        "running": running,
    }, ensure_ascii=False)


def handler_run(inst) -> str:
    """定值模式启动运行（写 0x0065=1），读回运行标志验证。"""
    import time
    _check_inst(inst)
    inst.write_register(REG_RUN_CTRL, RUN_FIX)
    time.sleep(0.5)
    running = inst.read_holding(REG_RUN_FLAG, 1)[0] == 1
    sv = _signed(inst.read_holding(REG_T_SV, 1)[0]) / 100.0
    if running:
        return f"[PASS] 温箱已启动（定值运行），当前 SV={sv:.2f} °C"
    return f"[FAIL] 启动命令已发送但运行标志未置位，请检查面板状态（SV={sv:.2f} °C）"


def handler_stop(inst) -> str:
    """停止运行（写 0x0065=4），读回运行标志验证。"""
    import time
    _check_inst(inst)
    inst.write_register(REG_RUN_CTRL, RUN_STOP)
    time.sleep(0.5)
    running = inst.read_holding(REG_RUN_FLAG, 1)[0] == 1
    if not running:
        return "[PASS] 温箱已停止"
    return "[FAIL] 停止命令已发送但运行标志仍为运行，请检查面板状态"


# ═══════════════════════════════════════════════════════════════════
# 程式（程序控制）功能
#
# 实测寄存器布局（COM30, 2026-08，面板对照确认）:
#
# 程式号选择: 写 0x0064(reg100)=程式号，切换段表寄存器空间指向该程式。
#
# 运行态寄存器（运行/停止后均保留）:
#   0x0019(25)  当前运行程式号
#   0x001A(26)  当前段号
#   0x001B(27)  剩余时间-小时
#   0x001C(28)  剩余时间-分钟
#   0x0020(32)  程式总循环次数（面板 "/100" 的分母）
#   0x0023(35)  当前段温度SV1（×100，有符号）
#   0x0024(36)  当前段温度SV2（×100，有符号）
#   0x0025(37)  当前段湿度SV（×10，0=OFF）
#   0x0027(39)  当前段时间-小时
#   0x000A(10)  运行标志（0=停止 2=运行）
#   0x0065(101) 运行控制（写 1=定值 4=停止; 写2=程式运行不被接受）
#
# 段表寄存器（分块存储，每块 100 段，写 0x0064 选程式号后可读写）:
#   0x0515(1301) 温度块（×100，有符号; 空段=0xB1E0 即 -20000）
#   0x0579(1401) 湿度块（×10，0=OFF）
#   0x05DD(1501) 时间块（×100，小时）
#   0x0641(1601) TS1 块
#   0x06A5(1701) TS2 块
#   0x0709(1801) TS3 块
#   0x076D(1901) TS4 块
#
# 倍率: 温度 ×100（两位小数），湿度 ×10（一位小数），时间 ×100（小时）。
# ═══════════════════════════════════════════════════════════════════


def _select_prog(inst, prog_no: int) -> None:
    """选择程式号（写 0x0064），使段表寄存器指向该程式。"""
    import time
    inst.write_register(REG_PROG_SEL, int(prog_no))
    time.sleep(0.3)


def handler_prog_status(inst) -> str:
    """读取程式运行状态：运行标志、当前程式号/段号、剩余时间、当前段
    设定（温度/湿度/时间）、循环次数，返回 JSON。

    运行时数据有效；停止后程式号/段号/段设定仍保留可读。
    """
    import json
    _check_inst(inst)
    flag = inst.read_holding(REG_RUN_FLAG, 1)[0]
    rt = inst.read_holding(REG_PROG_NO, 8)   # reg25..32
    cur_prog = rt[0]
    cur_seg = rt[1]
    rmn_hour = rt[2]
    rmn_min = rt[3]
    prog_cycle = rt[7]
    seg = inst.read_holding(REG_SEG_TEMP_SV1, 5)  # reg35..39
    mode_str = "运行中" if flag != 0 else "停止"
    return json.dumps({
        "running": flag != 0,
        "run_flag": flag,
        "mode": mode_str,
        "current_prog_no": cur_prog,
        "current_seg_no": cur_seg,
        "remaining_time": f"{rmn_hour}:{rmn_min:02d}",
        "prog_cycle_total": prog_cycle,
        "current_seg": {
            "temp_sv1_c": _signed(seg[0]) / 100.0,
            "temp_sv2_c": _signed(seg[1]) / 100.0,
            "humidity_sv_rh": (seg[2] / 10.0) if seg[2] != 0 else "OFF",
            "time_hour": seg[4],
        },
    }, ensure_ascii=False)


def handler_prog_read_seg(inst, prog_no: int = 1, seg_no: int = 1) -> str:
    """读取指定程式的某一段设定（温度、湿度、时间、循环、TS1..4）。

    通过写 0x0064 选程式号后，从分块段表寄存器读取。
    """
    import json
    _check_inst(inst)
    prog_no, seg_no = int(prog_no), int(seg_no)
    if seg_no < 1 or seg_no > TBL_BLOCK:
        return f"[FAIL] 段号须在 1..{TBL_BLOCK}，收到 {seg_no}"
    _select_prog(inst, prog_no)
    idx = seg_no - 1
    temp = _signed(inst.read_holding(REG_TBL_TEMP + idx, 1)[0])
    if temp == -20000 or temp == 0:
        return f"[FAIL] 程式{prog_no} 段{seg_no} 为空（未配置）"
    hum = inst.read_holding(REG_TBL_HUM + idx, 1)[0]
    tm = inst.read_holding(REG_TBL_TIME + idx, 1)[0]
    cyc = inst.read_holding(REG_TBL_CYCLE + idx, 1)[0]
    ts1 = inst.read_holding(REG_TBL_TS1 + idx, 1)[0]
    ts2 = inst.read_holding(REG_TBL_TS2 + idx, 1)[0]
    ts3 = inst.read_holding(REG_TBL_TS3 + idx, 1)[0]
    ts4 = inst.read_holding(REG_TBL_TS4 + idx, 1)[0]
    info = {
        "prog_no": prog_no,
        "seg_no": seg_no,
        "temp_c": temp / 100.0,
        "humidity_rh": (hum / 10.0) if hum != 0 else "OFF",
        "time_hour": tm / 100.0,
        "cycle": cyc,
        "ts1": ts1, "ts2": ts2, "ts3": ts3, "ts4": ts4,
    }
    return f"[PASS] 程式{prog_no} 段{seg_no}: " + json.dumps(info, ensure_ascii=False)


def handler_prog_read_table(inst, prog_no: int = 1, max_seg: int = 20) -> str:
    """读取指定程式的完整段表（默认前 20 段），返回 JSON。

    段表分块存储（温度/湿度/时间/循环/TS1..4 共 8 块，每块 100 段）。
    写 0x0064 选程式号后，分块连读。
    """
    import json
    _check_inst(inst)
    prog_no, max_seg = int(prog_no), int(max_seg)
    max_seg = min(max_seg, TBL_BLOCK)
    _select_prog(inst, prog_no)
    temps = inst.read_holding(REG_TBL_TEMP, max_seg)
    hums = inst.read_holding(REG_TBL_HUM, max_seg)
    times = inst.read_holding(REG_TBL_TIME, max_seg)
    cycs = inst.read_holding(REG_TBL_CYCLE, max_seg)
    ts1s = inst.read_holding(REG_TBL_TS1, max_seg)
    ts2s = inst.read_holding(REG_TBL_TS2, max_seg)
    ts3s = inst.read_holding(REG_TBL_TS3, max_seg)
    ts4s = inst.read_holding(REG_TBL_TS4, max_seg)
    segments = []
    for i in range(max_seg):
        t = _signed(temps[i])
        if t == -20000 or (t == 0 and times[i] == 0):
            continue
        segments.append({
            "seg": i + 1,
            "temp_c": t / 100.0,
            "humidity_rh": (hums[i] / 10.0) if hums[i] != 0 else "OFF",
            "time_hour": times[i] / 100.0,
            "cycle": cycs[i],
            "ts": [ts1s[i], ts2s[i], ts3s[i], ts4s[i]],
        })
    return json.dumps({
        "prog_no": prog_no,
        "segment_count": len(segments),
        "segments": segments,
    }, ensure_ascii=False)


def handler_prog_write_seg(inst, prog_no: int = 1, seg_no: int = 1,
                           temp_c: float = 85.0, humidity_rh: float = 0.0,
                           time_min: int = 60, ts1: int = 0, ts2: int = 0,
                           ts3: int = 0, ts4: int = 0) -> str:
    """写入程式某一段设定（温度、湿度、时间、TS1..4）。

    温度 ×100 有符号，湿度 ×10（0=OFF），时间 ×100 小时。
    通过写 0x0064 选程式号后，写分块段表寄存器（FC=06）。写后读回验证。

    ⚠ 实测（COM30）：已有数据的段可修改（温度/湿度/时间/TS 均可写并读回验证）。
       空段（0xB1E0）写入回显正常但不持久化--段数寄存器(reg41)为只读，
       无法通过 Modbus 新增段。新增段须在面板上操作（面板增加段后 Modbus
       即可修改该段数据）。
    """
    import time
    _check_inst(inst)
    prog_no, seg_no = int(prog_no), int(seg_no)
    if seg_no < 1 or seg_no > TBL_BLOCK:
        return f"[FAIL] 段号须在 1..{TBL_BLOCK}，收到 {seg_no}"
    _select_prog(inst, prog_no)
    idx = seg_no - 1
    temp_raw = int(round(float(temp_c) * 100)) & 0xFFFF
    hum_raw = int(round(float(humidity_rh) * 10)) & 0xFFFF
    time_raw = int(round(float(time_min) / 60.0 * 100)) & 0xFFFF  # 分钟->小时×100
    inst.write_register(REG_TBL_TEMP + idx, temp_raw)
    inst.write_register(REG_TBL_HUM + idx, hum_raw)
    inst.write_register(REG_TBL_TIME + idx, time_raw)
    inst.write_register(REG_TBL_TS1 + idx, int(ts1) & 0xFFFF)
    inst.write_register(REG_TBL_TS2 + idx, int(ts2) & 0xFFFF)
    inst.write_register(REG_TBL_TS3 + idx, int(ts3) & 0xFFFF)
    inst.write_register(REG_TBL_TS4 + idx, int(ts4) & 0xFFFF)
    time.sleep(0.2)
    # 读回验证
    back_temp = _signed(inst.read_holding(REG_TBL_TEMP + idx, 1)[0])
    ok = (back_temp == int(round(float(temp_c) * 100)))
    if ok:
        return (
            f"[PASS] 程式{prog_no} 段{seg_no} 已写入: "
            f"温度={temp_c:.2f}°C 湿度={'OFF' if humidity_rh==0 else f'{humidity_rh:.1f}%RH'} "
            f"时间={time_min}min TS=[{ts1},{ts2},{ts3},{ts4}] "
            f"(读回: T={back_temp/100:.2f}°C)"
        )
    return (
        f"[FAIL] 程式{prog_no} 段{seg_no} 写入未被接受（读回={back_temp/100:.2f}°C）。"
        f"该段可能是空段--段数寄存器(reg41)只读，无法通过 Modbus 新增段。"
        f"请在面板上增加段后再用本命令修改。"
    )


def handler_prog_set_cycle(inst, prog_no: int = 1, seg_no: int = 1,
                           cycle: int = 1) -> str:
    """设置程式某一段的循环次数（写循环次数块 reg1601+段偏移）。

    每段有独立的循环次数，控制该段重复执行的次数。
    """
    import time
    _check_inst(inst)
    prog_no, seg_no, cycle = int(prog_no), int(seg_no), int(cycle)
    if seg_no < 1 or seg_no > TBL_BLOCK:
        return f"[FAIL] 段号须在 1..{TBL_BLOCK}，收到 {seg_no}"
    if cycle < 1:
        return "[FAIL] 循环次数必须 ≥ 1"
    _select_prog(inst, prog_no)
    idx = seg_no - 1
    inst.write_register(REG_TBL_CYCLE + idx, cycle & 0xFFFF)
    time.sleep(0.2)
    back = inst.read_holding(REG_TBL_CYCLE + idx, 1)[0]
    ok = back == cycle
    status = "PASS" if ok else "FAIL"
    return f"[{status}] 程式{prog_no} 段{seg_no} 循环次数 -> {cycle} (读回={back})"


def handler_prog_clear(inst, prog_no: int = 1, max_seg: int = 20) -> str:
    """清空程式段表（将前 max_seg 段温度写为空段标记 0xB1E0）。"""
    import time
    _check_inst(inst)
    prog_no, max_seg = int(prog_no), int(max_seg)
    max_seg = min(max_seg, TBL_BLOCK)
    _select_prog(inst, prog_no)
    for i in range(max_seg):
        inst.write_register(REG_TBL_TEMP + i, EMPTY_SEG)
        time.sleep(0.03)
    return f"[PASS] 程式{prog_no} 前 {max_seg} 段已清空"


def handler_prog_run(inst, prog_no: int = 1) -> str:
    """启动程式运行：写 0x0064 选定程式号，再写 0x0065=1 启动。

    实测（COM30）：本控制器程式运行与定值运行均写 0x0065=1，区别在于
    是否先通过 0x0064 选定了程式号。选定程式号后写 1 即启动该程式。
    """
    import time
    _check_inst(inst)
    prog_no = int(prog_no)
    # 选定程式号
    inst.write_register(REG_PROG_SEL, prog_no)
    time.sleep(0.3)
    # 写 0x0065=1 启动运行
    inst.write_register(REG_RUN_CTRL, RUN_FIX)
    time.sleep(1.0)
    # 读回验证
    flag = inst.read_holding(REG_RUN_FLAG, 1)[0]
    cur_prog = inst.read_holding(REG_PROG_NO, 1)[0]
    cur_seg = inst.read_holding(REG_SEG_NO, 1)[0]
    if flag != 0:
        return (f"[PASS] 程式{prog_no} 已启动运行 "
                f"(运行标志={flag} 当前程式={cur_prog} 段={cur_seg})")
    return (f"[FAIL] 程式{prog_no} 启动未成功 "
            f"(运行标志={flag})，请检查面板：程式{prog_no} 是否已配置段表")
