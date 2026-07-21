"""Modbus-RTU 恒温恒湿试验箱处理器（MODBUS-1 协议, RS-232C）。

已在实物上验证的寄存器映射（2026-07，面板核对 + 运行差分实验）:
  0x0001  温度 PV（只读, FC=03, ×100, 有符号） — 面板 38.04°C 时读得 3801
  0x0002  工作温度设定（只读, 运行时随斜率爬坡变化, 勿写）
  0x0005  湿度 PV（只读, ×10） — 面板 98.0%RH 时读得 980
  0x0006  湿度 SV 只读镜像（勿写）
  0x0007  输出量 MV（只读, ×10, 加热时 800=80.0%）
  0x000A  运行标志（只读, 1=运行 0=停止, 写无效）
  0x0065  运行控制（读写）— 写 1=定值运行, 写 4=停止（实测验证）
  0x0066  温度 SV（读写, FC=03/06, ×100, 有符号）— 写 40.00 后面板 SV 变为 40.00
  0x0067  湿度 SV（读写, ×10）— 写 300 后面板湿度 SV 变为 30.0

倍率规律: 温度两位小数 ×100，湿度一位小数 ×10。

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


def _signed(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v


def _check_inst(inst):
    if not hasattr(inst, "read_holding"):
        raise RuntimeError(
            "当前会话不是 Modbus 串口仪器，请用 "
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
