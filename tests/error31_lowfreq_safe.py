"""CH340 error31 低频触发 + 自愈实验（monkeypatch 保护版）。

关键：在 instrument_mcp.instruments 的 monkeypatch 保护下运行，
SetCommState 误报被吞掉，避免进入"打开失败→不干净清理→驱动损坏"循环。

每 30 秒一轮，持久连接，Ctrl-C 优雅退出（先 close 再退出）。
"""
import sys
sys.path.insert(0, "src")
import instrument_mcp.instruments  # 触发 monkeypatch
import serial.serialwin32 as sw
from instrument_mcp.instruments import SerialModbusInstrument
from instrument_mcp.commands.modbus_chamber_handler import REG_T_PV, _signed
import time
import signal
from datetime import datetime

PORT = "COM30"
BAUD = 9600
INTERVAL_S = 30

_running = True
def _on_sigint(s, f):
    global _running
    _running = False
signal.signal(signal.SIGINT, _on_sigint)

def ts():
    return datetime.now().strftime("%H:%M:%S")

print(f"=== CH340 error31 低频实验（monkeypatch 保护）===")
print(f"端口 {PORT} @ {BAUD}, 每 {INTERVAL_S}s 一轮, monkeypatch={getattr(sw,'_ch340_err31_patched',False)}")
print(f"开始 {ts()}\n")

inst = None
round_no = 0
ok = fail = 0
try:
    inst = SerialModbusInstrument(address=PORT, baud_rate=BAUD)
    inst.open()
    print(f"  {ts()} 端口已打开（持久连接）\n")
    print(f"{'轮':>4} {'时间':>9} {'PV':>8}")
    print("-" * 28)

    while _running:
        round_no += 1
        try:
            pv = _signed(inst.read_holding(REG_T_PV, 1)[0]) / 100.0
            ok += 1
            print(f"{round_no:>4} {ts():>9} {pv:>7.2f}C")
        except Exception as e:
            fail += 1
            print(f"{round_no:>4} {ts():>9} FAIL {type(e).__name__}: {str(e)[:40]}")
        sys.stdout.flush()
        # 优雅等待
        for _ in range(INTERVAL_S * 10):
            if not _running:
                break
            time.sleep(0.1)
except Exception as e:
    print(f"\n{ts()} 连接异常: {type(e).__name__}: {e}")
finally:
    print(f"\n{ts()} 关闭串口...")
    if inst is not None:
        try:
            inst.close()
        except Exception:
            pass
    print(f"{ts()} 已正常关闭（无残留）")
    print(f"\n=== 结束 {ts()} === 总{round_no}轮 成功{ok} 失败{fail}")
