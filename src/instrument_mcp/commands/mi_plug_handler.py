"""米家智能插座2（chuangmi.plug.212a01）命令处理器。

经 LAN miIO/MIoT 协议（UDP，token 加密）控制，完整属性表与坑见
docs/miplug_chuangmi_212a01.md。仓库根目录另有独立 CLI mi_plug.py。
"""

import time

SWITCH = (2, 1)  # siid, piid —— 开关属性

# status 展示的属性（来自 MIoT spec v2，实测可读）: (siid, piid, 名称, 显示函数)
STATUS_PROPS = [
    (2, 1, "开关", None),
    (2, 6, "插座温度", lambda v: f"{v} °C"),
    (2, 7, "已工作时间", lambda v: f"{v} min"),
    (5, 6, "电功率", lambda v: f"{v / 100:.2f} W"),  # spec: 单位 0.01 W
    (5, 3, "电压", lambda v: f"{v} (raw, spec 未标单位)"),
    (5, 2, "电流", lambda v: f"{v} (raw, spec 未标单位)"),
    (5, 1, "累计耗电量", lambda v: f"{v} (raw, spec 未标单位)"),
    (5, 7, "过功率", lambda v: f"{v} (raw)"),
    (3, 1, "指示灯", None),
    (7, 1, "功率保护开关", None),
    (4, 3, "按键倒计时", lambda v: f"{v} s"),
    (4, 1, "循环-开时长", lambda v: f"{v} s"),
    (4, 2, "循环-关时长", lambda v: f"{v} s"),
    (4, 4, "循环任务使能", None),
    (4, 5, "倒计时已启动", None),
]


def _parse_value(raw: str):
    """按 true/false/int/float/字符串 顺序解析属性值（同 mi_plug.py CLI）。"""
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    try:
        return int(raw)
    except ValueError:
        try:
            return float(raw)
        except ValueError:
            return raw


def _set_switch(inst, target: bool) -> str:
    inst.set_prop(*SWITCH, target)
    time.sleep(0.5)
    actual = inst.read_prop(*SWITCH)
    if actual != target:
        return f"[FAIL] 开关写入 {target} 后回读不一致（回读: {actual}）"
    return f"[PASS] 开关 -> {target}（回读: {actual}）"


# ─────────────────────────────────────────────
# MCP Tool Handler 函数（参数来自 YAML 定义的 params_json）
# ─────────────────────────────────────────────

def miplug_info(inst, alias: str = "default") -> str:
    """miIO 握手 + 设备信息（验证 token）。"""
    info = inst.device_info()
    raw = getattr(info, "data", {})  # 0.5.12 的 DeviceInfo 属性不全，读原始响应
    return (
        f"[PASS] 米家插座设备信息\n"
        f"  model:    {info.model}\n"
        f"  firmware: {info.firmware_version}\n"
        f"  mac:      {raw.get('mac', '?')}\n"
        f"  ip:       {raw.get('netif', {}).get('sta_ip', '?')}"
    )


def miplug_status(inst, alias: str = "default") -> str:
    """读取全部关键属性（开关/温度/功率/电压/电流/循环配置等）。"""
    lines = []
    for siid, piid, name, conv in STATUS_PROPS:
        try:
            v = inst.read_prop(siid, piid)
        except Exception as e:  # 单个属性失败不影响其余
            lines.append(f"  {name:<7} (siid={siid}, piid={piid}): 读取失败: {e}")
            continue
        lines.append(f"  {name:<7} (siid={siid}, piid={piid}): {conv(v) if conv else v}")
    return "[PASS] 插座状态:\n" + "\n".join(lines)


def miplug_on(inst, alias: str = "default") -> str:
    """打开插座（写后 0.5s 回读校验）。"""
    return _set_switch(inst, True)


def miplug_off(inst, alias: str = "default") -> str:
    """关闭插座（写后 0.5s 回读校验）。"""
    return _set_switch(inst, False)


def miplug_toggle(inst, alias: str = "default") -> str:
    """翻转插座开关状态。"""
    return _set_switch(inst, not inst.read_prop(*SWITCH))


def miplug_loop_start(inst, alias: str = "default",
                      on_s: int = 3600, off_s: int = 3600) -> str:
    """启动设备端循环定时（设备端执行，断开本机/云端也继续跑）。

    相位跟随当前状态：现在开 -> on_s 秒后关；现在关 -> off_s 秒后开；
    随后按 开 on_s / 关 off_s 轮转。

    Args:
        on_s: 开启相位时长（秒）
        off_s: 关闭相位时长（秒）
    """
    on_s, off_s = int(on_s), int(off_s)
    inst.set_prop(4, 1, on_s)
    inst.set_prop(4, 2, off_s)
    inst.set_prop(4, 4, True)
    on = inst.read_prop(*SWITCH)
    phase = f"{on_s}s 后关闭" if on else f"{off_s}s 后开启"
    return (
        f"[PASS] 循环任务已启动: 开 {on_s}s / 关 {off_s}s 轮转；"
        f"当前{'开' if on else '关'} -> {phase}"
    )


def miplug_loop_stop(inst, alias: str = "default") -> str:
    """停止设备端循环任务（保持当前开关状态）。"""
    inst.set_prop(4, 4, False)
    state = "开" if inst.read_prop(*SWITCH) else "关"
    return f"[PASS] 循环任务已停止（开关保持 {state}）"


def miplug_loop_info(inst, alias: str = "default") -> str:
    """读取循环任务与按键倒计时配置。"""
    return (
        f"[PASS] 循环: 开 {inst.read_prop(4, 1)}s / 关 {inst.read_prop(4, 2)}s"
        f" | 使能 {inst.read_prop(4, 4)}"
        f" | 按键倒计时 {inst.read_prop(4, 3)}s(已启动={inst.read_prop(4, 5)})"
    )


def miplug_get_property(inst, alias: str = "default",
                        siid: int = 2, piid: int = 1) -> str:
    """按 MIoT spec 读任意属性。

    Args:
        siid: 服务 ID（如 2=开关服务，4=循环/倒计时，5=电量统计）
        piid: 属性 ID
    """
    v = inst.read_prop(int(siid), int(piid))
    return f"[PASS] siid={int(siid)} piid={int(piid)} -> {v!r}"


def miplug_set_property(inst, alias: str = "default",
                        siid: int = 2, piid: int = 1, value: str = "") -> str:
    """按 MIoT spec 写任意属性（写后回读）。

    Args:
        siid: 服务 ID
        piid: 属性 ID
        value: 属性值，"true"/"false" 解析为布尔，数字解析为 int/float，其余按字符串
    """
    v = _parse_value(str(value))
    inst.set_prop(int(siid), int(piid), v)
    time.sleep(0.3)
    actual = inst.read_prop(int(siid), int(piid))
    return f"[PASS] siid={int(siid)} piid={int(piid)} <- {v!r}（回读: {actual!r}）"
