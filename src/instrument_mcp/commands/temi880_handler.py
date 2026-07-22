"""TEMI880 PC-Link 协议处理器
用于 instrument_mcp 的 temperature_chamber 仪器类型。

协议帧格式:
  STX(0x02) + 站号(2) + 命令(3) + 寄存器(4) + [数据(4 hex)] + ETX(0x03) + 校验和(2 hex)

命令:
  RMD - 读测量值 (PV)
  RSD - 读设定值 (SV)
  WSD - 写设定值 (SV)
  WMD - 写测量值

寄存器地址（常见）:
  0100 - 温度 PV (过程值/当前温度，单位 0.1°C)
  0001 - 温度 SV (设定值，单位 0.1°C)
  0200 - 湿度 PV (如果有)
"""

import logging

logger = logging.getLogger(__name__)

STX = chr(0x02)
ETX = chr(0x03)


def calc_checksum(body: str) -> str:
    """计算校验和：body 所有字符 ASCII 累加 mod 256，转2位大写hex"""
    s = sum(ord(c) for c in body)
    return f"{s % 256:02X}"


def build_frame(station: str, command: str, register: str, data_hex: str = "") -> bytes:
    """构建 PC-Link 协议帧。

    Args:
        station: 站号，2字符，如 "01"
        command: 3字符命令，如 "RMD"/"RSD"/"WSD"
        register: 4字符寄存器地址，如 "0100"
        data_hex: 4字符十六进制数据（仅写命令需要），如 "03E8"
    """
    payload = f"{station}{command}{register}{data_hex}"
    body = payload + ETX
    cs = calc_checksum(body)
    frame = f"{STX}{payload}{ETX}{cs}"
    return frame.encode('ascii')


def parse_response(raw: bytes) -> dict:
    """解析 PC-Link 响应帧。

    Returns:
        {
            "station": str,
            "command": str,
            "register": str,
            "data_hex": str,
            "data_int": int,      # 解析后的整数值
            "data_value": float,  # 解析后的实际值 (÷10)
            "checksum_ok": bool,
            "raw_hex": str,
        }
    """
    result = {
        "station": "",
        "command": "",
        "register": "",
        "data_hex": "",
        "data_int": 0,
        "data_value": 0.0,
        "checksum_ok": False,
        "raw_hex": raw.hex(' '),
    }
    try:
        s = raw.decode('ascii', errors='strict')
    except UnicodeDecodeError:
        return result

    if not s.startswith(STX):
        return result

    etx_idx = s.find(ETX, 1)
    if etx_idx < 0 or etx_idx + 3 > len(s):
        return result

    body = s[1:etx_idx]  # 不含 STX，含 ETX 前的所有内容
    body_with_etx = body + ETX
    expected_cs = calc_checksum(body_with_etx)
    received_cs = s[etx_idx + 1:etx_idx + 3]

    result["checksum_ok"] = (received_cs.upper() == expected_cs.upper())
    result["station"] = body[0:2]
    result["command"] = body[2:5]
    result["register"] = body[5:9]
    data_raw = body[9:]
    result["data_hex"] = data_raw

    if data_raw and len(data_raw) == 4:
        try:
            val = int(data_raw, 16)
            # TEMI880 数据格式: 高位在前，温度 × 10
            # 处理负数（补码）
            if val >= 0x8000:
                val = val - 0x10000
            result["data_int"] = val
            result["data_value"] = val / 10.0
        except ValueError:
            pass

    return result


def value_to_hex(value: float) -> str:
    """将温度值转为 4 位十六进制（×10，高位在前）。

    负数使用补码表示。
    """
    val_int = int(round(value * 10))
    if val_int < 0:
        val_int = val_int + 0x10000  # 转为补码
    return f"{val_int & 0xFFFF:04X}"


# ── MCP Tool Handler 函数 ──

def handler_read_pv(inst, station: str = "01", register: str = "0100") -> str:
    """读取温度 PV（当前温度）。"""
    frame = build_frame(station, "RMD", register)
    inst.write(frame.decode('ascii', errors='replace'))
    raw = inst._resource.read_bytes(64)
    if not raw:
        return "[FAIL] 无响应"
    r = parse_response(raw)
    if not r["command"]:
        return f"[FAIL] 无法解析响应: {r['raw_hex']}"
    return (
        f"[PASS] PV={r['data_value']:.1f}°C "
        f"(raw={r['data_hex']} int={r['data_int']} cs={'OK' if r['checksum_ok'] else 'BAD'})"
    )


def handler_read_sv(inst, station: str = "01", register: str = "0001") -> str:
    """读取温度 SV（设定温度）。"""
    frame = build_frame(station, "RSD", register)
    inst.write(frame.decode('ascii', errors='replace'))
    raw = inst._resource.read_bytes(64)
    if not raw:
        return "[FAIL] 无响应"
    r = parse_response(raw)
    if not r["command"]:
        return f"[FAIL] 无法解析响应: {r['raw_hex']}"
    return (
        f"[PASS] SV={r['data_value']:.1f}°C "
        f"(raw={r['data_hex']} cs={'OK' if r['checksum_ok'] else 'BAD'})"
    )


def handler_set_sv(inst, temp_c: float = 25.0, station: str = "01", register: str = "0001") -> str:
    """设置温度 SV（设定温度），用于温度循环控制。"""
    data_hex = value_to_hex(temp_c)
    frame = build_frame(station, "WSD", register, data_hex)
    inst.write(frame.decode('ascii', errors='replace'))
    # WSD 命令通常无响应或返回简短确认
    try:
        raw = inst._resource.read_bytes(64)
        if raw:
            r = parse_response(raw)
            return f"[PASS] SV→{temp_c:.1f}°C (hex={data_hex}) | resp: {r['raw_hex']}"
    except Exception:
        pass
    return f"[PASS] SV→{temp_c:.1f}°C (hex={data_hex}) 已发送"


def handler_read_status(inst, station: str = "01") -> str:
    """读取温箱状态（PV + SV 同时读取）。"""
    # 读 PV
    frame1 = build_frame(station, "RMD", "0100")
    inst.write(frame1.decode('ascii', errors='replace'))
    raw1 = inst._resource.read_bytes(64)
    pv = parse_response(raw1) if raw1 else {}

    # 读 SV
    frame2 = build_frame(station, "RSD", "0001")
    inst.write(frame2.decode('ascii', errors='replace'))
    raw2 = inst._resource.read_bytes(64)
    sv = parse_response(raw2) if raw2 else {}

    import json
    return json.dumps({
        "pv": pv.get("data_value", "N/A"),
        "sv": sv.get("data_value", "N/A"),
        "pv_raw": pv.get("raw_hex", ""),
        "sv_raw": sv.get("raw_hex", ""),
    }, ensure_ascii=False)


def handler_probe(inst, station: str = "01") -> str:
    """探测 TEMI880：尝试读取 PV，返回原始响应。"""
    frame = build_frame(station, "RMD", "0100")
    logger.info(f"Probe frame: {frame.hex(' ')}")
    inst.write(frame.decode('ascii', errors='replace'))
    try:
        raw = inst._resource.read_bytes(128)
    except Exception as e:
        return f"[FAIL] 读取超时: {e}"
    if not raw:
        return "[FAIL] 无响应 — 检查波特率和接线"
    r = parse_response(raw)
    import json
    return json.dumps(r, ensure_ascii=False, indent=2)
