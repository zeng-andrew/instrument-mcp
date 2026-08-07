"""tinySA / tinySA Ultra+（Zeeenko ZS-407）频谱仪命令处理器。

协议文档: https://tinysa.org/wiki/pmwiki.php?n=Main.USBInterface
- 文本命令以 \\r\\n 结尾，设备回显命令后返回响应并以 "ch> " 提示符结束
- 频率可用整数或 k/M/G 后缀（如 300M），电平单位 dBm
- scan: 文本输出 "{freq} {level} [stored...]"，每点一行
- scanraw: 二进制输出 "{" + 每点 ("x" LSB MSB) + "}"，
  dBm = raw/32 - 174（tinySA4 / Ultra 系）
- capture: 480x320 像素 RGB565（小端），共 307200 字节
"""

import csv
import struct
import time
from pathlib import Path

# tinySA4 / Ultra 系的 scanraw 电平偏移（dBm = raw/32 - 174）
SCANRAW_OFFSET = 174.0


def _freq_text(f: float) -> str:
    """把 Hz 频率格式化为易读文本。"""
    if f >= 1e9:
        return f"{f / 1e9:.4f} GHz"
    if f >= 1e6:
        return f"{f / 1e6:.3f} MHz"
    if f >= 1e3:
        return f"{f / 1e3:.1f} kHz"
    return f"{f:.0f} Hz"


def _freq_num(v) -> int:
    """把用户输入的频率（Hz 数值或 300M 样式字符串）转为 Hz 整数。"""
    if isinstance(v, str):
        v = v.strip()
        mult = 1.0
        low = v.lower()
        for suffix, m in (("g", 1e9), ("m", 1e6), ("k", 1e3)):
            if low.endswith(suffix):
                mult = m
                v = v[:-1]
                break
        return int(float(v) * mult)
    return int(float(v))


def _fmt_table(rows, limit: int = 20) -> str:
    """格式化 (freq, level) 行列表。"""
    lines = []
    for freq, level in rows[:limit]:
        lines.append(f"    {freq:>12d}  {level:8.2f}")
    if len(rows) > limit:
        lines.append(f"    ... 共 {len(rows)} 点，已省略 {len(rows) - limit} 点")
    return "\n".join(lines)


def _parse_scan_text(text: str) -> list:
    """解析 scan 文本输出为 [(freq_hz:int, level_dbm:float), ...]。"""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            freq = int(float(parts[0]))
            level = float(parts[1])
        except ValueError:
            continue
        rows.append((freq, level))
    return rows


def _decode_scanraw(payload: bytes) -> list:
    """解码 scanraw 二进制响应为 [level_dbm, ...]。

    格式: '{' + 每点 ('x' LSB MSB 16bit) + '}'，dBm = raw/32 - 174。
    """
    vals = []
    data = payload
    if data.startswith(b"{"):
        data = data[1:]
    i = 0
    while i + 3 <= len(data):
        if data[i] != 0x78:  # 'x' 分界符
            i += 1
            continue
        raw = data[i + 1] | (data[i + 2] << 8)
        vals.append(raw / 32.0 - SCANRAW_OFFSET)
        i += 3
    return vals


def _peak(rows) -> tuple:
    """返回 (freq_hz, level_dbm) 峰值。"""
    if not rows:
        return (0, float("-inf"))
    return max(rows, key=lambda r: r[1])


def _save_csv(rows, path: str) -> str:
    """保存 (freq, level) 到 CSV 文件，返回文件路径。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["freq_hz", "level_dbm"])
        for freq, level in rows:
            w.writerow([freq, f"{level:.3f}"])
    return str(p)


def _scan_timeout(points: int) -> float:
    return max(10.0, 3.0 + points * 0.03)


# ─────────────────────────────────────────────
# MCP Tool Handler 函数（参数来自 YAML 定义的 params_json）
# ─────────────────────────────────────────────

def tinysa_identity(inst, alias: str = "default") -> str:
    """读取设备型号、固件版本与硬件信息。"""
    version = inst.query("version", timeout=5.0)
    info = inst.query("info", timeout=5.0)
    return (
        f"[PASS] tinySA 设备信息\n"
        f"  固件: {version}\n"
        f"  ---\n{info}"
    )


def tinysa_sweep_get(inst, alias: str = "default") -> str:
    """读取当前扫描设置（起始/停止频率、点数）与设备状态。"""
    sweep = inst.query("sweep", timeout=5.0)
    try:
        start, stop, points = sweep.split()
    except ValueError:
        return f"[FAIL] 无法解析 sweep 响应: {sweep!r}"
    status = "未知"
    try:
        status = inst.query("status", timeout=5.0).strip()
    except Exception:
        pass
    return (
        f"[PASS] 当前扫描设置\n"
        f"  频率范围: {_freq_text(float(start))} - {_freq_text(float(stop))}\n"
        f"  扫描点数: {points}\n"
        f"  设备状态: {status}"
    )


def tinysa_sweep_set(
    inst,
    alias: str = "default",
    start_hz=None,
    stop_hz=None,
    center_hz=None,
    span_hz=None,
    cw_hz=None,
    points: int = None,
) -> str:
    """设置扫描频率范围。

    支持四种设置方式（按优先级）:
    1. start_hz + stop_hz: 直接指定起止频率（可选 points 点数）
    2. center_hz: 以中心频率设置（保持当前跨度）
    3. span_hz: 以跨度设置（保持当前中心）
    4. cw_hz: 连续波单点频率（跨度归零）

    Args:
        start_hz: 起始频率(Hz)，如 100000000
        stop_hz: 停止频率(Hz)
        center_hz: 中心频率(Hz)
        span_hz: 频率跨度(Hz)
        cw_hz: 连续波频率(Hz)
        points: 扫描点数（配合 start_hz+stop_hz 使用）
    """
    if start_hz is not None and stop_hz is not None:
        start = _freq_num(start_hz)
        stop = _freq_num(stop_hz)
        if stop <= start:
            return f"[FAIL] stop_hz 必须大于 start_hz: {start} / {stop}"
        cmd = f"sweep {start} {stop}"
        if points:
            cmd += f" {int(points)}"
        inst.query(cmd, timeout=8.0)
    elif center_hz is not None:
        inst.query(f"sweep center {_freq_num(center_hz)}", timeout=8.0)
    elif span_hz is not None:
        inst.query(f"sweep span {_freq_num(span_hz)}", timeout=8.0)
    elif cw_hz is not None:
        inst.query(f"sweep cw {_freq_num(cw_hz)}", timeout=8.0)
    elif start_hz is not None:
        inst.query(f"sweep start {_freq_num(start_hz)}", timeout=8.0)
    elif stop_hz is not None:
        inst.query(f"sweep stop {_freq_num(stop_hz)}", timeout=8.0)
    else:
        return "[FAIL] 至少提供 start_hz/stop_hz、center_hz、span_hz 或 cw_hz 之一"

    sweep = inst.query("sweep", timeout=5.0)
    return f"[PASS] 扫描范围已更新: {sweep}"


def tinysa_scan(
    inst,
    alias: str = "default",
    start_hz: float = 100000000,
    stop_hz: float = 500000000,
    points: int = 101,
    csv_path: str = "",
) -> str:
    """执行一次频谱扫描（文本模式）并返回数据。

    每点输出 (频率Hz, 电平dBm)。点数上限 290。

    Args:
        start_hz: 起始频率(Hz)
        stop_hz: 停止频率(Hz)
        points: 扫描点数（2~290，默认 101）
        csv_path: 可选，将全量数据保存为 CSV 文件
    """
    start = _freq_num(start_hz)
    stop = _freq_num(stop_hz)
    pts = max(2, min(290, int(points)))
    t0 = time.time()
    resp = inst.query(
        f"scan {start} {stop} {pts} 3", timeout=_scan_timeout(pts)
    )
    dt = time.time() - t0
    rows = _parse_scan_text(resp)
    if not rows:
        return f"[FAIL] 扫描无有效数据: {resp[:200]!r}"
    pf, pl = _peak(rows)
    csv_note = ""
    if csv_path:
        path = _save_csv(rows, csv_path)
        csv_note = f"\n  已保存 CSV: {path}"
    return (
        f"[PASS] 频谱扫描 {_freq_text(float(start))} - {_freq_text(float(stop))}"
        f"，{len(rows)} 点，耗时 {dt:.1f}s\n"
        f"  峰值: {_freq_text(pf)} @ {pl:.2f} dBm\n"
        f"  数据 (频率Hz, dBm):\n{_fmt_table(rows, 20)}{csv_note}"
    )


def tinysa_scanraw(
    inst,
    alias: str = "default",
    start_hz: float = 100000000,
    stop_hz: float = 500000000,
    points: int = 101,
    csv_path: str = "",
) -> str:
    """执行一次频谱扫描（二进制模式，更快）并返回数据。

    dBm = raw/32 - 174（Ultra 系）。点数可超过 290。

    Args:
        start_hz: 起始频率(Hz)
        stop_hz: 停止频率(Hz)
        points: 扫描点数（2~2000，默认 101）
        csv_path: 可选，将全量数据保存为 CSV 文件
    """
    start = _freq_num(start_hz)
    stop = _freq_num(stop_hz)
    pts = max(2, min(2000, int(points)))
    t0 = time.time()
    payload = inst.raw_query(
        f"scanraw {start} {stop} {pts} 0", timeout=_scan_timeout(pts) + 5
    )
    dt = time.time() - t0
    levels = _decode_scanraw(payload)
    if not levels:
        return f"[FAIL] scanraw 无有效数据: {payload[:200]!r}"
    n = len(levels)
    rows = []
    for i in range(n):
        f = start + (stop - start) * i / (n - 1) if n > 1 else start
        rows.append((int(round(f)), levels[i]))
    pf, pl = _peak(rows)
    csv_note = ""
    if csv_path:
        path = _save_csv(rows, csv_path)
        csv_note = f"\n  已保存 CSV: {path}"
    return (
        f"[PASS] 频谱扫描(快速) {_freq_text(float(start))} - {_freq_text(float(stop))}"
        f"，{n} 点，耗时 {dt:.1f}s\n"
        f"  峰值: {_freq_text(pf)} @ {pl:.2f} dBm\n"
        f"  数据 (频率Hz, dBm):\n{_fmt_table(rows, 20)}{csv_note}"
    )


def tinysa_marker(
    inst,
    alias: str = "default",
    action: str = "peak",
    marker_id: int = 1,
    frequency_hz: float = 0,
) -> str:
    """控制频谱标记（Marker）。

    Args:
        action: peak=定位最强信号 / on=开启 / off=关闭 / freq=设置到指定频率
        marker_id: 标记编号（1~8）
        frequency_hz: action=freq 时的目标频率(Hz)
    """
    act = str(action).strip().lower()
    if act == "off":
        inst.query(f"marker {marker_id} off", timeout=5.0)
        return f"[PASS] Marker {marker_id} 已关闭"
    if act == "on":
        inst.query(f"marker {marker_id} on", timeout=5.0)
        return f"[PASS] Marker {marker_id} 已开启"
    if act == "freq":
        if not frequency_hz:
            return "[FAIL] action=freq 时必须提供 frequency_hz"
        resp = inst.query(f"marker {marker_id} {_freq_num(frequency_hz)}", timeout=5.0)
        return f"[PASS] Marker {marker_id} 设置完成: {resp}"
    # peak（默认）
    resp = inst.query(f"marker {marker_id} peak", timeout=8.0)
    parts = resp.split()
    if len(parts) >= 4:
        try:
            mid, idx, freq, level = parts[0], parts[1], float(parts[2]), float(parts[3])
            return (
                f"[PASS] Marker {mid} 峰值\n"
                f"  索引: {idx}\n"
                f"  频率: {_freq_text(freq)}\n"
                f"  电平: {level:.2f} dBm"
            )
        except ValueError:
            pass
    return f"[PASS] Marker {marker_id} 响应: {resp}"


def tinysa_pause(inst, alias: str = "default") -> str:
    """暂停频谱扫描（冻结当前画面/数据）。"""
    inst.query("pause", timeout=5.0)
    return "[PASS] 扫描已暂停"


def tinysa_resume(inst, alias: str = "default") -> str:
    """恢复频谱扫描。"""
    inst.query("resume", timeout=5.0)
    return "[PASS] 扫描已恢复"


def tinysa_freq(inst, alias: str = "default", frequency_hz: float = 100000000) -> str:
    """暂停扫描并设置测量频率（用于定点测量某个频率的电平）。"""
    f = _freq_num(frequency_hz)
    inst.query(f"freq {f}", timeout=5.0)
    resp = inst.query(f"scan {f} {f} 1 3", timeout=_scan_timeout(1) + 5)
    rows = _parse_scan_text(resp)
    level_str = f"{rows[0][1]:.2f} dBm" if rows else "N/A"
    return (
        f"[PASS] 测量频率已设为 {_freq_text(f)}（扫描已暂停）\n"
        f"  该频率电平: {level_str}\n"
        f"  提示: 用 tinysa_resume 恢复扫描"
    )


def tinysa_settings(
    inst,
    alias: str = "default",
    rbw: str = None,
    attenuate: str = None,
    repeat: int = None,
    trigger: str = None,
    calc: str = None,
    ext_gain: float = None,
) -> str:
    """配置频谱仪测量设置（只需提供要修改的参数）。

    Args:
        rbw: 分辨率带宽 kHz，如 100，或 "auto"
        attenuate: 内部衰减 dB（0~31），或 "auto"
        repeat: 每频率测量次数（1~1000），增大可降低噪声
        trigger: 触发模式 auto/normal/single 或触发电平(dBm)
        calc: 测量模式 off/minh/maxh/maxd/aver4/aver16/quasip
        ext_gain: 外部衰减/增益补偿（-100~100 dB）
    """
    cmds = []
    if rbw is not None:
        v = str(rbw).strip().lower()
        cmds.append(("rbw", f"rbw {v}" if v != "auto" else "rbw auto"))
    if attenuate is not None:
        v = str(attenuate).strip().lower()
        cmds.append(("attenuate", f"attenuate {v}" if v != "auto" else "attenuate auto"))
    if repeat is not None:
        cmds.append(("repeat", f"repeat {max(1, min(1000, int(repeat)))}"))
    if trigger is not None:
        cmds.append(("trigger", f"trigger {trigger}"))
    if calc is not None:
        cmds.append(("calc", f"calc {calc}"))
    if ext_gain is not None:
        cmds.append(("ext_gain", f"ext_gain {float(ext_gain)}"))

    if not cmds:
        return "[FAIL] 未提供任何设置参数"

    done = []
    for name, cmd in cmds:
        try:
            inst.query(cmd, timeout=8.0)
            done.append(f"{name} -> {cmd.split(' ', 1)[1]}")
        except Exception as e:
            done.append(f"{name} -> 失败: {e}")
    return "[PASS] 设置已应用:\n  " + "\n  ".join(done)


def tinysa_capture(inst, alias: str = "default", output_path: str = "") -> str:
    """抓取设备屏幕截图并保存为 BMP 文件。

    Args:
        output_path: 输出文件路径（.bmp），默认 tinysa_screen.bmp
    """
    path = output_path.strip() or "tinysa_screen.bmp"
    if not path.lower().endswith(".bmp"):
        path += ".bmp"

    inst._send("capture")
    inst._consume_echo("capture")
    px = inst.read_exact(inst.LCD_WIDTH * inst.LCD_HEIGHT * 2, timeout=20.0)
    try:
        inst._read_until_prompt(2.0)
    except Exception:
        pass

    width = inst.LCD_WIDTH
    height = inst.LCD_HEIGHT
    bmp = _rgb565_to_bmp(px, width, height)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bmp)

    return (
        f"[PASS] 屏幕截图已保存: {path}\n"
        f"  尺寸: {width}x{height}, BMP {len(bmp)} 字节 (RGB565 原始 {len(px)} 字节)"
    )


def _rgb565_to_bmp(px: bytes, width: int, height: int) -> bytes:
    """RGB565 小端像素数据转 24 位 BMP（纯 Python，无第三方依赖）。"""
    row_size = (width * 3 + 3) & ~3
    data_size = row_size * height
    file_size = 54 + data_size
    out = bytearray()
    out += b"BM"
    out += struct.pack("<IHHI", file_size, 0, 0, 54)
    out += struct.pack(
        "<IiiHHIIiiII", 40, width, height, 1, 24, 0, data_size, 2835, 2835, 0, 0
    )
    for y in range(height - 1, -1, -1):
        row = bytearray(row_size)
        base = y * width * 2
        for x in range(width):
            v = struct.unpack_from("<H", px, base + x * 2)[0]
            r = (v >> 11) & 0x1F
            g = (v >> 5) & 0x3F
            b = v & 0x1F
            row[x * 3] = b << 3
            row[x * 3 + 1] = g << 2
            row[x * 3 + 2] = r << 3
        out += row
    return bytes(out)


def tinysa_raw_command(inst, alias: str = "default", command: str = "") -> str:
    """直接发送任意 tinySA 串口命令并返回响应（用于探索新功能）。

    Args:
        command: tinySA 命令，如 "help" / "vbat" / "spur on"
    """
    if not command.strip():
        return "[FAIL] 命令不能为空，试试 'help'"
    resp = inst.query(command.strip(), timeout=15.0)
    return f"[PASS] {command.strip()}\n{resp}"
