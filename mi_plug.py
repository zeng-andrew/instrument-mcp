#!/usr/bin/env python
"""米家智能插座2 (chuangmi.plug.212a01) 局域网控制 CLI —— 已实测验证。

运行方式（仓库根目录；uv 临时注入 python-miio，不改动 .venv / uv.lock）:
    uv run --with python-miio mi_plug.py info        # miIO 握手 + 设备信息（验证 token）
    uv run --with python-miio mi_plug.py status      # 读取全部关键属性
    uv run --with python-miio mi_plug.py on          # 打开插座 (siid=2, piid=1)
    uv run --with python-miio mi_plug.py off         # 关闭插座
    uv run --with python-miio mi_plug.py toggle
    uv run --with python-miio mi_plug.py loop 3600 3600   # 设备端循环定时: 开1h/关1h
    uv run --with python-miio mi_plug.py loopstop         # 停止循环(保持当前状态)
    uv run --with python-miio mi_plug.py loopinfo         # 读定时配置
    uv run --with python-miio mi_plug.py get <siid> <piid>            # 读任意属性
    uv run --with python-miio mi_plug.py set <siid> <piid> <value>    # 写任意属性

    value 取 "true"/"false"/整数/浮点/字符串，按 MIoT spec 的 format 传即可。
IP/token 明文存于同目录 mi_plug_config.json（已 gitignore，模板
mi_plug_config.example.json）；--ip/--token 可临时覆盖。
完整属性表与坑见 docs/miplug_chuangmi_212a01.md。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

CONFIG_FILE = Path(__file__).parent / "mi_plug_config.json"
EXAMPLE_FILE = CONFIG_FILE.with_name("mi_plug_config.example.json")


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        raise SystemExit(
            f"缺少 {CONFIG_FILE.name}：复制 {EXAMPLE_FILE.name} 为 {CONFIG_FILE.name} 并填入"
            "设备 IP 和 token（token 提取方式见 docs/miplug_chuangmi_212a01.md）。"
        )
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    if not cfg.get("ip") or not cfg.get("token"):
        raise SystemExit(f"{CONFIG_FILE.name} 中 ip / token 字段不完整。")
    return cfg

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


def make_device(args: argparse.Namespace):
    warnings.filterwarnings("ignore", category=FutureWarning)  # miio 0.5.12 on Py3.13
    from miio import MiotDevice

    d = MiotDevice(args.ip, args.token)
    # "Neither the class nor the parameter defines the mapping"：
    # 212a01 在 python-miio 里没有官方 mapping，我们只用 get/set_property_by，无需 mapping
    return d


def unwrap(res):
    """get_property_by 返回 list[CIResult]（0.6+）或 list[dict]，统一取出 value。"""
    item = res[0] if isinstance(res, (list, tuple)) else res
    if isinstance(item, dict):
        return item.get("value")
    return getattr(item, "value", item)


def read_prop(d, siid: int, piid: int):
    return unwrap(d.get_property_by(siid, piid))


def cmd_info(d) -> int:
    info = d.info()  # miIO info 走 token 加密通道，能返回即证明 token 正确
    raw = getattr(info, "data", {})  # 0.5.12 的 DeviceInfo 属性不全，直接读原始响应
    print(f"model:     {info.model}")
    print(f"firmware:  {info.firmware_version}")
    print(f"mac:       {raw.get('mac', '?')}")
    print(f"ip:        {raw.get('netif', {}).get('sta_ip', '?')}")
    return 0


def cmd_status(d) -> int:
    for siid, piid, name, conv in STATUS_PROPS:
        try:
            v = read_prop(d, siid, piid)
        except Exception as e:  # 单个属性失败不影响其余
            print(f"  {name:<7} (siid={siid}, piid={piid}): 读取失败: {e}")
            continue
        print(f"  {name:<7} (siid={siid}, piid={piid}): {conv(v) if conv else v}")
    return 0


def set_switch(d, target: bool) -> int:
    d.set_property_by(*SWITCH, target)
    time.sleep(0.5)
    actual = read_prop(d, *SWITCH)
    print(f"开关 -> {target}（回读: {actual}）")
    if actual != target:
        print("回读不一致！", file=sys.stderr)
        return 1
    return 0


def cmd_toggle(d) -> int:
    return set_switch(d, not read_prop(d, *SWITCH))


def cmd_loop(d, on_s: int, off_s: int) -> int:
    """启动循环任务（设备端执行，断开本机/云端也继续跑）。

    相位跟随当前状态：现在开 -> on_s 秒后关；现在关 -> off_s 秒后开；随后按
    开 on_s / 关 off_s 轮转。一次性定时可用长短时长近似（见 docs）。
    """
    d.set_property_by(4, 1, on_s)
    d.set_property_by(4, 2, off_s)
    d.set_property_by(4, 4, True)
    on = read_prop(d, *SWITCH)
    phase = f"{on_s}s 后关闭" if on else f"{off_s}s 后开启"
    print(f"循环任务已启动: 开 {on_s}s / 关 {off_s}s 轮转；当前{'开' if on else '关'} -> {phase}")
    return 0


def cmd_loopstop(d) -> int:
    d.set_property_by(4, 4, False)
    print(f"循环任务已停止（开关保持 {'开' if read_prop(d, *SWITCH) else '关'}）")
    return 0


def cmd_loopinfo(d) -> int:
    print(f"  循环: 开 {read_prop(d, 4, 1)}s / 关 {read_prop(d, 4, 2)}s | 使能 {read_prop(d, 4, 4)}"
          f" | 按键倒计时 {read_prop(d, 4, 3)}s(已启动={read_prop(d, 4, 5)})")
    return 0


def cmd_get(d, siid: int, piid: int) -> int:
    print(read_prop(d, siid, piid))
    return 0


def cmd_set(d, siid: int, piid: int, raw: str) -> int:
    value: object
    if raw.lower() in ("true", "false"):
        value = raw.lower() == "true"
    else:
        try:
            value = int(raw)
        except ValueError:
            try:
                value = float(raw)
            except ValueError:
                value = raw
    d.set_property_by(siid, piid, value)
    time.sleep(0.3)
    print(f"siid={siid} piid={piid} <- {value!r}（回读: {read_prop(d, siid, piid)}）")
    return 0


def main(argv=None) -> int:
    cfg = load_config()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--ip", default=cfg["ip"], help="设备 IP（默认取 mi_plug_config.json）")
    p.add_argument("--token", default=cfg["token"], help="32 位设备 token（默认取 mi_plug_config.json）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="miIO 握手 + 设备信息")
    sub.add_parser("status", help="读取全部关键属性")
    sub.add_parser("on", help="打开插座")
    sub.add_parser("off", help="关闭插座")
    sub.add_parser("toggle", help="翻转插座")
    lp = sub.add_parser("loop", help="启动设备端循环定时: loop <on_s> <off_s>")
    lp.add_argument("on_s", type=int, help="开启相位时长(秒)")
    lp.add_argument("off_s", type=int, help="关闭相位时长(秒)")
    sub.add_parser("loopstop", help="停止循环任务（保持当前开关状态）")
    sub.add_parser("loopinfo", help="读循环任务/倒计时配置")
    g = sub.add_parser("get", help="读任意属性")
    g.add_argument("siid", type=int)
    g.add_argument("piid", type=int)
    s = sub.add_parser("set", help="写任意属性")
    s.add_argument("siid", type=int)
    s.add_argument("piid", type=int)
    s.add_argument("value")
    args = p.parse_args(argv)

    d = make_device(args)
    return {
        "info": cmd_info,
        "status": cmd_status,
        "on": lambda d: set_switch(d, True),
        "off": lambda d: set_switch(d, False),
        "toggle": cmd_toggle,
        "loop": lambda d: cmd_loop(d, args.on_s, args.off_s),
        "loopstop": cmd_loopstop,
        "loopinfo": cmd_loopinfo,
        "get": lambda d: cmd_get(d, args.siid, args.piid),
        "set": lambda d: cmd_set(d, args.siid, args.piid, args.value),
    }[args.cmd](d)


if __name__ == "__main__":
    sys.exit(main())
