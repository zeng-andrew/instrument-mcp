"""COM25 (FT232RL) AT 命令无响应探测脚本。

排查重启后 SSCOM 收不到串口板返回的问题：
  1. 端口能否独占打开（是否被占用）
  2. 静默监听 2s（模块是否主动吐数据，如开机信息）
  3. 常见波特率逐个发 AT，观察是否有响应/乱码
  4. DTR/RTS 电平影响（部分开发板把 DTR/RTS 接复位/BOOT）

只发无害的 AT 探测命令，不改模块任何配置。
用法: .venv/Scripts/python.exe tests/at_probe.py [port]
"""
import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM25"
BAUDS = [115200, 9600, 57600, 38400, 19200, 4800]


def show(data: bytes):
    if not data:
        return "(无数据)"
    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"hex={data.hex(' ')}  ascii={printable!r}"


def open_port(baud, dtr=None, rts=None):
    s = serial.Serial(None, baudrate=baud, bytesize=8, parity="N",
                      stopbits=1, timeout=0.1, write_timeout=1.0)
    # 先设好电平再打开，避免 open 瞬间 DTR/RTS 默认拉高
    if dtr is not None:
        s.dtr = dtr
    if rts is not None:
        s.rts = rts
    s.port = PORT
    s.open()
    if dtr is not None:
        s.dtr = dtr
    if rts is not None:
        s.rts = rts
    s.reset_input_buffer()
    return s


def listen(seconds=2.0, baud=115200, dtr=False, rts=False):
    s = open_port(baud, dtr, rts)
    buf = bytearray()
    t0 = time.time()
    while time.time() - t0 < seconds:
        chunk = s.read(256)
        if chunk:
            buf.extend(chunk)
    s.close()
    return bytes(buf)


def probe(baud, dtr, rts, attempts=2):
    try:
        s = open_port(baud, dtr, rts)
    except Exception as e:
        print(f"  打开失败: {e!r}")
        return None
    buf = bytearray()
    try:
        for i in range(attempts):
            s.write(b"AT\r\n")
            s.write(b"AT\r")
            t0 = time.time()
            while time.time() - t0 < 0.7:
                chunk = s.read(128)
                if chunk:
                    buf.extend(chunk)
            if buf:
                break
            time.sleep(0.2)
    finally:
        s.close()
    return bytes(buf)


def main():
    print(f"=== {PORT} AT 探测 ===\n")

    print("[1] 端口占用检查（独占方式打开）")
    try:
        s = open_port(115200)
        s.close()
        print("  OK: 端口可正常打开，未被占用\n")
    except Exception as e:
        print(f"  FAIL: {e!r}\n")

    print("[2] 静默监听 2s @115200（DTR/RTS=False），看模块是否主动吐数据")
    data = listen(2.0)
    print(f"  {show(data)}\n")

    print("[3] 逐波特率发 AT（DTR/RTS=False）")
    for baud in BAUDS:
        data = probe(baud, dtr=False, rts=False)
        tag = "有响应!" if any(13 == b or b == 10 or 79 == b for b in (data or b"")) else ("乱码" if data else "无数据")
        print(f"  {baud:>6} baud: [{tag}] {show(data or b'')}")

    print("\n[4] DTR/RTS 电平影响测试 @115200")
    for dtr, rts in [(True, False), (False, True), (True, True)]:
        data = probe(115200, dtr=dtr, rts=rts, attempts=1)
        print(f"  DTR={dtr!s:<5} RTS={rts!s:<5}: {show(data or b'')}")

    print("\n[5] 静默监听 2s @9600")
    data = listen(2.0, baud=9600)
    print(f"  {show(data)}")

    print("\n探测结束。")


if __name__ == "__main__":
    main()
