"""CH340 error31 低频触发 + 自愈实验（安全版）。

核心安全原则——绝不让进程在写串口时被强杀：
  1. 所有读写都设 timeout / write_timeout，永不无限阻塞
  2. try/finally 保证每次都正常 close()，不留句柄残留
  3. 每轮独立 open → read → close，连接短促干净
  4. 信号处理：收到 Ctrl-C 时优雅退出（先关闭端口再退出）

实验设计（用户指定）：
  - 每 30 秒发一次读温度命令
  - 触发 error31 时，尝试两种自愈手段：
      手段1: 清接收缓冲区（reset_input_buffer）
      手段2: 重试写（重发帧）
  - 持久连接（一次 open，多轮读写），模拟温箱实际长连接使用场景
  - 全程记录每轮状态
"""
import serial
import struct
import time
import sys
import signal
from datetime import datetime

PORT = "COM30"
BAUD = 9600
INTERVAL_S = 30          # 每 30 秒一轮
READ_WAIT_S = 0.4        # 写后等响应
SELFHEAL_WRITE_RETRY = 5 # 手段2：重试写次数


def crc16(data):
    c = 0xFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else (c >> 1)
    return c


def make_frame():
    pdu = struct.pack(">BBHH", 1, 3, 0x0001, 1)
    return pdu + struct.pack("<H", crc16(pdu))


def parse_pv(resp):
    if len(resp) >= 5 and resp[0] == 1 and resp[1] == 3:
        return struct.unpack(">h", resp[3:5])[0] / 100.0
    return None


def ts():
    return datetime.now().strftime("%H:%M:%S")


def classify(err_str):
    if "OSError(31" in err_str or "资源数据不足" in err_str:
        return "error31_误报"
    if "设备没有发挥作用" in err_str or ("PermissionError(13" in err_str and "31" in err_str):
        return "error31_驱动卡死"
    if "FileNotFoundError" in err_str or "找不到" in err_str:
        return "设备掉线"
    if "Access" in err_str or "拒绝" in err_str or "PermissionError(13" in err_str:
        return "端口被占用"
    if "timeout" in err_str.lower() or "超时" in err_str:
        return "超时"
    return "其他"


# ── 优雅退出：确保串口关闭 ──
_ser = None
_running = True

def _on_sigint(signum, frame):
    global _running
    _running = False

signal.signal(signal.SIGINT, _on_sigint)


def safe_close(s):
    """确保串口被关闭，绝不留句柄残留。"""
    if s is None:
        return
    try:
        if s.is_open:
            s.close()
    except Exception:
        pass


def read_pv_once(s, frame):
    """单次读 PV（不清缓冲，纯发+收）。返回 (pv, err_str_or_None)。"""
    try:
        s.write(frame)
        s.flush()
        time.sleep(READ_WAIT_S)
        r = s.read(64)
        pv = parse_pv(r)
        if pv is not None:
            return pv, None
        return None, f"无响应(读到{len(r)}B)"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:100]}"


def try_selfheal(s, frame):
    """两种自愈手段：清缓冲区 + 重试写。返回 (是否恢复, 手段, pv)。"""
    # ── 手段1: 清接收缓冲区 + 重写 ──
    try:
        s.reset_input_buffer()
        s.reset_output_buffer()
        time.sleep(0.15)
        s.write(frame)
        s.flush()
        time.sleep(READ_WAIT_S)
        r = s.read(64)
        pv = parse_pv(r)
        if pv is not None:
            return True, "手段1清缓冲", pv
    except Exception as e:
        pass

    # ── 手段2: 再次重试写（多次） ──
    for attempt in range(1, SELFHEAL_WRITE_RETRY + 1):
        try:
            try:
                s.reset_input_buffer()
            except Exception:
                pass
            time.sleep(0.15)
            s.write(frame)
            s.flush()
            time.sleep(READ_WAIT_S)
            r = s.read(64)
            pv = parse_pv(r)
            if pv is not None:
                return True, f"手段2重写×{attempt}", pv
        except Exception:
            continue
    return False, "两种手段均失败", None


def main():
    global _ser
    print(f"=== CH340 error31 低频触发 + 自愈实验（安全版）===")
    print(f"端口 {PORT} @ {BAUD}, 每 {INTERVAL_S}s 一轮")
    print(f"安全: write_timeout=1s, try/finally 正常关闭, Ctrl-C 优雅退出")
    print(f"开始 {ts()}\n")
    print(f"{'轮':>4} {'时间':>9} {'状态':<8} {'PV/错误':<28} {'自愈'}")
    print("-" * 75)

    frame = make_frame()
    round_no = 0
    stats = {"OK": 0, "FAIL": 0, "healed": 0}

    # ── 持久连接：打开一次，多轮读写 ──
    try:
        # 打开（设所有超时，绝不阻塞）
        _ser = serial.Serial(PORT, BAUD, timeout=0.5, write_timeout=1.0)
        print(f"  {ts()} 端口已打开（持久连接）\n")

        while _running:
            round_no += 1
            pv, err = read_pv_once(_ser, frame)

            if pv is not None:
                stats["OK"] += 1
                print(f"{round_no:>4} {ts():>9} {'OK':<8} {pv:>8.2f}C")
            else:
                stats["FAIL"] += 1
                etype = classify(err)
                # 尝试自愈
                healed, method, hpv = try_selfheal(_ser, frame)
                if healed:
                    stats["healed"] += 1
                    print(f"{round_no:>4} {ts():>9} {'恢复':<8} {etype:<14} -> {hpv:.2f}C  [{method}]")
                else:
                    print(f"{round_no:>4} {ts():>9} {'失败':<8} {etype}: {err[:24]:<24} [{method}]")
            sys.stdout.flush()

            # 优雅等待（可被 Ctrl-C 打断）
            for _ in range(INTERVAL_S * 10):
                if not _running:
                    break
                time.sleep(0.1)

    except Exception as e:
        print(f"\n  {ts()} 连接异常: {type(e).__name__}: {e}")
    finally:
        # ── 关键：无论如何都正常关闭串口 ──
        print(f"\n  {ts()} 正在关闭串口...")
        safe_close(_ser)
        _ser = None
        print(f"  {ts()} 串口已正常关闭（无句柄残留）")

    print(f"\n=== 结束 {ts()} ===")
    print(f"总轮数 {round_no}  成功 {stats['OK']}  失败 {stats['FAIL']}  自愈成功 {stats['healed']}")


if __name__ == "__main__":
    main()
