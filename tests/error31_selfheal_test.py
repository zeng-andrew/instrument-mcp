"""CH340 error31 低频触发 + 自愈实验。

实验设计（按用户要求）：
  - 每 30 秒发送一次读温度命令（低频，模拟实际使用节奏）
  - 当触发 error31（或任何打开/读写异常）时，依次尝试两种自愈手段：
      手段1: 清接收缓冲区（reset_input_buffer / PurgeComm）
      手段2: 再次重试写（重发帧，最多 N 次）
  - 记录每次失败的类型、自愈手段是否生效、最终是否恢复

注意：本脚本不依赖 monkeypatch —— 故意用未 patch 的 pyserial 行为来观察，
但因为 venv 是未patch原版，error31 会真实抛出，正好用来测试自愈。
不过 open() 层面我们仍然允许重试（这是合理的）。

用 Ctrl-C 停止。每 30 秒一轮。
"""
import serial
import struct
import time
import sys
import traceback
from datetime import datetime

PORT = "COM30"
BAUD = 9600
INTERVAL_S = 30          # 用户指定：每 30 秒一次
MAX_WRITE_RETRY = 5      # 手段2：写重试次数
READ_WAIT_S = 0.4        # 写后等响应时间


def crc16(data):
    c = 0xFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else (c >> 1)
    return c


def make_read_pv_frame():
    pdu = struct.pack(">BBHH", 1, 3, 0x0001, 1)
    return pdu + struct.pack("<H", crc16(pdu))


def parse_pv(resp):
    if len(resp) >= 5 and resp[0] == 1 and resp[1] == 3:
        return struct.unpack(">h", resp[3:5])[0] / 100.0
    return None


def ts():
    return datetime.now().strftime("%H:%M:%S")


def classify_error(e):
    """分类异常，判断是否 error31 相关。"""
    s = str(e)
    if "31" in s and ("资源数据不足" in s or "GEN_FAILURE" in s or "OSError(31" in s):
        return "error31_classic"  # 经典误报
    if "设备没有发挥作用" in s or "PermissionError(13" in s:
        return "device_gone"      # 设备掉线/驱动崩溃（真故障）
    if "PermissionError(13" in s and "31" in s:
        return "error31_device_gone"  # 日志里那种：PermissionError(13,...,31)
    if "FileNotFoundError" in s or "找不到" in s:
        return "port_missing"
    if "timeout" in s.lower() or "超时" in s:
        return "timeout"
    return "other"


def try_self_heal(ser, frame):
    """尝试两种自愈手段，返回 (是否恢复, 手段描述, pv)。

    手段1: 清接收缓冲区（PurgeComm）
    手段2: 再次重试写（重发帧）
    """
    # ── 手段1: 清接收缓冲区 ──
    try:
        ser.reset_input_buffer()   # = PurgeComm(PURGE_RXCLEAR|PURGE_RXABORT)
        # 顺便清发送缓冲
        try:
            ser.reset_output_buffer()
        except Exception:
            pass
        time.sleep(0.1)
        # 清完直接重发一次看能否读到
        ser.write(frame)
        ser.flush()
        time.sleep(READ_WAIT_S)
        r = ser.read(64)
        pv = parse_pv(r)
        if pv is not None:
            return True, "手段1清缓冲区+重写", pv
    except Exception as e:
        pass

    # ── 手段2: 再次重试写（多次） ──
    for attempt in range(1, MAX_WRITE_RETRY + 1):
        try:
            # 每次重写前清一下缓冲，避免累积
            try:
                ser.reset_input_buffer()
            except Exception:
                pass
            time.sleep(0.15)
            ser.write(frame)
            ser.flush()
            time.sleep(READ_WAIT_S)
            r = ser.read(64)
            pv = parse_pv(r)
            if pv is not None:
                return True, f"手段2重试写第{attempt}次", pv
        except Exception as e:
            # 写本身抛异常，继续重试
            continue

    return False, "两种手段均失败", None


def open_port():
    """打开端口，带少量重试（只兜真实打开失败）。返回 (ser, err_or_None)。"""
    last = None
    for _ in range(3):
        try:
            s = serial.Serial(PORT, BAUD, timeout=0.5, write_timeout=1.0)
            return s, None
        except Exception as e:
            last = e
            time.sleep(0.5)
    return None, last


def run_round(round_no, ser_holder):
    """执行一轮：发命令读温度，失败则尝试自愈。返回结果 dict。"""
    result = {"round": round_no, "time": ts()}
    frame = make_read_pv_frame()

    # 端口未开则先开
    if ser_holder["ser"] is None or not ser_holder["ser"].is_open:
        ser, oerr = open_port()
        if ser is None:
            result["status"] = "OPEN_FAIL"
            result["err_type"] = classify_error(oerr)
            result["err"] = repr(oerr)
            # 尝试自愈打开（清缓冲手段对打开失败无效，但重试有意义——已在 open_port 内）
            result["healed"] = False
            return result
        ser_holder["ser"] = ser

    ser = ser_holder["ser"]

    # ── 正常发命令 ──
    try:
        # 发送前先清残余（每轮都清，保持干净）
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        ser.write(frame)
        ser.flush()
        time.sleep(READ_WAIT_S)
        r = ser.read(64)
        pv = parse_pv(r)
        if pv is not None:
            result["status"] = "OK"
            result["pv"] = pv
            result["healed"] = False
            return result
        # 无响应（读到空），进入自愈
        result["status"] = "NO_RESP"
        result["err_type"] = "no_response"
    except Exception as e:
        result["status"] = "RW_FAIL"
        result["err_type"] = classify_error(e)
        result["err"] = repr(e)[:120]

    # ── 触发自愈 ──
    # 如果端口已坏（写抛异常），可能需要重开
    if not ser.is_open:
        ser2, oerr2 = open_port()
        if ser2:
            ser_holder["ser"] = ser2
            ser = ser2
        else:
            result["healed"] = False
            result["heal_method"] = "重开失败"
            return result

    healed, method, pv = try_self_heal(ser, frame)
    result["healed"] = healed
    result["heal_method"] = method
    if pv is not None:
        result["pv"] = pv
    return result


def main():
    print(f"=== CH340 error31 低频触发 + 自愈实验 ===")
    print(f"端口 {PORT} @ {BAUD}, 每 {INTERVAL_S}s 一轮, 写重试最多 {MAX_WRITE_RETRY} 次")
    print(f"Ctrl-C 停止。开始时间 {ts()}")
    print()
    print(f"{'轮次':>4} {'时间':>10} {'状态':<10} {'错误类型':<20} {'PV':>8} {'自愈':<6} {'手段'}")
    print("-" * 90)

    ser_holder = {"ser": None}
    round_no = 0
    stats = {"OK": 0, "NO_RESP": 0, "RW_FAIL": 0, "OPEN_FAIL": 0, "healed": 0}

    try:
        while True:
            round_no += 1
            r = run_round(round_no, ser_holder)
            stats[r["status"]] = stats.get(r["status"], 0) + 1
            if r.get("healed"):
                stats["healed"] += 1

            pv_str = f"{r['pv']:.2f}C" if r.get("pv") is not None else "-"
            err_type = r.get("err_type", "")
            method = r.get("heal_method", "")
            heal_str = "是" if r.get("healed") else "否"
            print(f"{round_no:>4} {ts():>10} {r['status']:<10} {err_type:<20} {pv_str:>8} {heal_str:<6} {method}")
            sys.stdout.flush()

            # 等待下一轮（最后一轮不等）
            time.sleep(INTERVAL_S)
    except KeyboardInterrupt:
        print(f"\n\n=== 实验结束 {ts()} ===")
        print(f"总轮数: {round_no}")
        print(f"成功(OK): {stats.get('OK',0)}  无响应: {stats.get('NO_RESP',0)}  "
              f"读写失败: {stats.get('RW_FAIL',0)}  打开失败: {stats.get('OPEN_FAIL',0)}")
        print(f"自愈成功: {stats.get('healed',0)}")
    finally:
        if ser_holder["ser"] and ser_holder["ser"].is_open:
            try:
                ser_holder["ser"].close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
