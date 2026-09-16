# -*- coding: utf-8 -*-
"""66319D 双通道独立性实验 v2（临时脚本）。

v1 教训: 66319D 不支持 INST:NSEL 通道选择(-113 Undefined header)。
手册(ch8 语言字典)明确 66319D 的两路输出用后缀区分:
  主输出: VOLT/CURR/OUTP/MEAS:VOLT?;  辅助输出: VOLT2/CURR2/OUTP2/MEAS:VOLT2?
  裸 OUTP 命令在未耦合时只作用于主输出;
  INST:COUP:OUTP:STAT ALL 时所有 OUTP 命令同时开关两路(耦合联动)。

实验场景:
  A) 独立关断: OUTP1 OFF -> CH2 是否保持供电(核心问题)
  B) 裸命令作用域: 两路都开, 裸 OUTP OFF -> 是否只关主输出
  C) 耦合联动: INST:COUP:OUTP:STAT ALL 后发 OUTP OFF -> 两路是否一起关
  最后恢复两路原始输出状态与原始耦合状态。

用法: uv run python tests/ps_66319d_dual_channel_test.py [资源地址]
退出码: 0=全部符合预期, 1=有不符合项或异常。
"""
import sys
import time

import pyvisa

RESOURCE = sys.argv[1] if len(sys.argv) > 1 else "GPIB2::5::INSTR"


class T:
    def __init__(self) -> None:
        self.fails: list[str] = []
        self.n = 0

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.n += 1
        print(f"      [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        if not ok:
            self.fails.append(name)


def main() -> int:
    rm = pyvisa.ResourceManager()
    inst = rm.open_resource(RESOURCE)
    inst.timeout = 5000
    t = T()

    def q(c: str) -> str:
        return inst.query(c).strip()

    def w(c: str) -> None:
        inst.write(c)

    idn = q("*IDN?")
    print(f"[0] IDN: {idn}")
    t.check("机型确认", "66319D" in idn, idn)

    def snap() -> tuple[dict, dict]:
        s1 = {
            "outp": q("OUTP?"),
            "meas_v": float(q("MEAS:VOLT?")),
            "meas_i": float(q("MEAS:CURR?")),
        }
        s2 = {
            "outp": q("OUTP2?"),
            "meas_v": float(q("MEAS:VOLT2?")),
            "meas_i": float(q("MEAS:CURR2?")),
        }
        return s1, s2

    def show(tag: str, s1: dict, s2: dict) -> None:
        print(f"      {tag} CH1(主): OUTP={s1['outp']} "
              f"实测={s1['meas_v']:.3f}V/{s1['meas_i']:.3f}A | "
              f"CH2(辅): OUTP={s2['outp']} "
              f"实测={s2['meas_v']:.3f}V/{s2['meas_i']:.3f}A")

    print("[1/5] 原始状态:")
    print(f"      CH1 设定: VOLT={q('VOLT?')}V CURR={q('CURR?')}A | "
          f"CH2 设定: VOLT2={q('VOLT2?')}V CURR2={q('CURR2?')}A")
    coup_orig = q("INST:COUP:OUTP:STAT?")
    orig1, orig2 = snap()
    show("原始", orig1, orig2)
    print(f"      耦合状态 INST:COUP:OUTP:STAT? = {coup_orig}")

    print("[2/5] 解除耦合(NONE)后确保两路都在输出, 取基线:")
    w("INST:COUP:OUTP:STAT NONE")
    print(f"      耦合状态读回: {q('INST:COUP:OUTP:STAT?')}")
    if orig1["outp"] != "1":
        w("OUTP1 ON")
        print("      (CH1 原本 OFF, 已临时开启)")
    if orig2["outp"] != "1":
        w("OUTP2 ON")
        print("      (CH2 原本 OFF, 已临时开启)")
    time.sleep(0.5)
    b1, b2 = snap()
    show("基线", b1, b2)
    t.check("两路基线均在输出", b1["outp"] == "1" and b2["outp"] == "1",
            f"CH1 OUTP={b1['outp']}, CH2 OUTP={b2['outp']}")

    print("[3/5] 场景A(核心): OUTP1 OFF, 观察CH2:")
    w("OUTP1 OFF")
    time.sleep(0.5)
    a1, a2 = snap()
    show("关CH1后", a1, a2)
    t.check("CH1 已关断", a1["outp"] == "0" and a1["meas_v"] < 0.5,
            f"OUTP={a1['outp']}, 实测={a1['meas_v']:.3f}V(关断残余偏移正常)")
    t.check("CH2 输出开关不受影响", a2["outp"] == "1",
            f"OUTP2={a2['outp']}")
    dv = abs(a2["meas_v"] - b2["meas_v"])
    t.check("CH2 电压不受影响", dv < 0.05,
            f"|{a2['meas_v']:.3f} - {b2['meas_v']:.3f}| = {dv:.3f}V")
    w("OUTP1 ON")
    time.sleep(0.3)

    print("[4/5] 场景B: 两路都开, 发裸 OUTP OFF(不带通道参数):")
    w("OUTP OFF")
    time.sleep(0.3)
    b1x, b2x = snap()
    show("裸OUTP OFF后", b1x, b2x)
    t.check("裸 OUTP 只关主输出", b1x["outp"] == "0" and b2x["outp"] == "1",
            f"CH1 OUTP={b1x['outp']}, CH2 OUTP={b2x['outp']}")
    w("OUTP1 ON")
    time.sleep(0.3)

    print("[5/5] 场景C: 耦合联动 INST:COUP:OUTP:STAT ALL 后发 OUTP OFF:")
    w("INST:COUP:OUTP:STAT ALL")
    time.sleep(0.2)
    print(f"      耦合状态读回: {q('INST:COUP:OUTP:STAT?')}")
    w("OUTP OFF")
    time.sleep(0.3)
    c1, c2 = snap()
    show("耦合+OUTP OFF后", c1, c2)
    t.check("耦合模式下两路一起关断", c1["outp"] == "0" and c2["outp"] == "0",
            f"CH1 OUTP={c1['outp']}, CH2 OUTP={c2['outp']}")
    w("INST:COUP:OUTP:STAT " + coup_orig)  # 恢复原耦合状态
    w("OUTP ON")
    time.sleep(0.3)
    r1, r2 = snap()
    show("解除耦合+OUTP ON", r1, r2)

    print("[恢复] 两路恢复原始输出状态(耦合已恢复为原值):")
    w("OUTP1 " + ("ON" if orig1["outp"] == "1" else "OFF"))
    w("OUTP2 " + ("ON" if orig2["outp"] == "1" else "OFF"))
    time.sleep(0.3)
    f1, f2 = snap()
    show("最终", f1, f2)
    print(f"      耦合状态: {q('INST:COUP:OUTP:STAT?')} (原始: {coup_orig})")

    errs = []
    for _ in range(20):
        e = q("SYST:ERR?")
        if e.startswith("+0"):
            break
        errs.append(e)
    print(f"[收尾] 错误队列: {errs if errs else '空(无错误)'}")
    t.check("无 SCPI 错误", not errs, str(errs[:3]))
    inst.close()

    print()
    if t.fails:
        print(f"结论: {len(t.fails)}/{t.n} 项不符合预期 -> {t.fails}")
        return 1
    print(f"结论: {t.n}/{t.n} 项全部符合预期 —— "
          "两通道可独立供电; 裸OUTP只作用主输出; 耦合ALL时联动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
