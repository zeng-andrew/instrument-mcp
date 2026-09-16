# -*- coding: utf-8 -*-
"""GPIB 连通性冒烟测试（临时脚本）。

用法:
    python gpib_smoke_test.py                 # 默认 GPIB0::5::INSTR
    python gpib_smoke_test.py GPIB0::5::INSTR # 指定资源地址

测试内容: VISA 后端 -> 接口注册(等待至多40s) -> 打开设备 -> *IDN? ->
读写电压设定(写 4.0V 再读回, 最后恢复原值, 不开启输出) -> 错误队列检查。
退出码: 0=全部通过, 1=失败。
"""
import sys
import time

import pyvisa

RESOURCE = sys.argv[1] if len(sys.argv) > 1 else "GPIB0::5::INSTR"
INTFC = RESOURCE.split("::")[0] + "::INTFC"


def main() -> int:
    rm = pyvisa.ResourceManager()
    print(f"[1/5] VISA 后端: {rm.visalib}")

    # Keysight 服务注册 GPIB 接口需要约 20-30s, 插线后立即测试要等
    print(f"[2/5] 等待接口 {INTFC} 注册 (至多 40s)...")
    ok = False
    for _ in range(8):
        try:
            ifc = rm.open_resource(INTFC)
            ifc.close()
            ok = True
            break
        except Exception:
            time.sleep(5)
    if not ok:
        print("      失败: 接口未注册。请检查适配器是否插好、驱动是否安装。")
        return 1
    print("      接口在线")

    print(f"[3/5] 打开 {RESOURCE} ...")
    try:
        inst = rm.open_resource(RESOURCE)
    except Exception as e:
        print(f"      失败: {e}")
        print("      提示: RSRC_NFOUND 通常是设备未开机、地址不对或适配器未插。")
        return 1
    inst.timeout = 5000

    try:
        print("[4/5] 查询 *IDN? ...")
        idn = inst.query("*IDN?").strip()
        print(f"      IDN: {idn}")

        print("[5/5] 读写测试 (不开输出)...")
        orig_volt = float(inst.query("VOLT?"))
        inst.write("VOLT 4.0")
        readback = float(inst.query("VOLT?"))
        inst.write(f"VOLT {orig_volt}")
        assert abs(readback - 4.0) < 1e-6, f"写读不一致: {readback}"
        print(f"      写 VOLT 4.0 -> 读回 {readback}V, 已恢复为 {orig_volt}V")

        err = inst.query("SYST:ERR?").strip()
        print(f"      错误队列: {err}")
        if not err.startswith(("0", "+0")):
            print("      警告: 存在非零错误码")
            return 1
    except Exception as e:
        print(f"      失败: {e}")
        return 1
    finally:
        inst.close()

    print("\n全部通过: GPIB 双向通信正常。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
