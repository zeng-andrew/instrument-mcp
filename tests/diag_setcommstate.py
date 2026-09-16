"""诊断：直接用 Win32 API 验证 CH340 的 SetCommState 是否误报 error 31。

目的：确认 error31 是\"原样写回也失败\"（持续/随机误报），从而决定
SerialModbusInstrument.open() 的重构方案——是用底层 pyserial 调用
SetCommState 并忽略其失败，还是需要额外重试/唤醒逻辑。

不依赖 pyserial，纯 ctypes。可逆，不改任何文件。
"""
import ctypes
from ctypes import wintypes
import struct
import time

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID = ctypes.cast(-1, wintypes.HANDLE).value

k32 = ctypes.windll.kernel32
k32.CreateFileW.restype = wintypes.HANDLE
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k32.GetCommState.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.SetCommState.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
k32.CloseHandle.argtypes = [wintypes.HANDLE]

# DCB 结构（pyserial 用的完整定义，~28 字段，取 80 字节足够）
DCB_SIZE = 80

print("=== CH340 SetCommState 误报探测 (COM30) ===")
print(f"INVALID_HANDLE = 0x{INVALID:016X}")
print()

fails = 0
for i in range(10):
    h = k32.CreateFileW(r"\\.\COM30", GENERIC_READ | GENERIC_WRITE, 0,
                        None, OPEN_EXISTING, 0, None)
    valid = h not in (None, INVALID, 0)
    if not valid:
        err = k32.GetLastError()
        print(f"  轮{i+1}: CreateFile 失败 handle={h} err={err}")
        time.sleep(0.2)
        continue
    # 读真实 DCB
    buf = (ctypes.c_byte * DCB_SIZE)()
    struct.pack_into("<I", buf, 0, DCB_SIZE)  # DCBlength
    ok1 = k32.GetCommState(h, buf)
    # 原样写回
    ok2 = k32.SetCommState(h, buf)
    err2 = k32.GetLastError()
    flag = " <= 误报31!" if (not ok2 and err2 == 31) else ""
    print(f"  轮{i+1}: Get={ok1} Set原样={ok2} err={err2}{flag}")
    if not ok2 and err2 == 31:
        fails += 1
    k32.CloseHandle(h)
    time.sleep(0.1)

print()
print(f"=== 结果: {fails}/10 次 SetCommState 误报 error 31 ===")
if fails > 0:
    print(">>> 结论: CH340 驱动确实误报 SetCommState 失败(error 31)。")
    print(">>> 端口实际可用，必须在打开时忽略该失败（或在重试中容忍）。")
else:
    print(">>> 本次未复现误报（可能当前驱动状态正常，或需高频 open/close 才触发）。")
