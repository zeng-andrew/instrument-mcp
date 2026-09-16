"""临时交互脚本：手动发送命令给 tinySA (Zeeenko ZS-407)。

用法:
    python tinysa_repl.py            # 默认 COM44
    python tinysa_repl.py COM3       # 指定串口

提示符输入命令直接回车发送，响应打印到屏幕。
特殊命令:
    quit / exit   退出
    info          设备信息
    help          设备内置命令列表
"""

import sys

sys.path.insert(0, "src")

from instrument_mcp.instruments import TinySAInstrument


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "COM44"
    with TinySAInstrument(address=port, timeout=0.5) as inst:
        print(f"已连接 {port}，输入命令测试（quit 退出）")
        while True:
            try:
                cmd = input("ch> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not cmd:
                continue
            low = cmd.lower()
            if low in ("quit", "exit"):
                break
            if low == "info":
                print(inst._get_idn())
                continue
            if low == "help":
                cmd = "help"
            try:
                resp = inst.query(cmd, timeout=8.0)
                print(resp if resp else "(空响应)")
            except Exception as e:
                print(f"错误: {e}")


if __name__ == "__main__":
    main()
