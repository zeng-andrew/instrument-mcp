#!/usr/bin/env python3
"""CMW500 SCPI 探针（原始 socket 5025，免 VISA 依赖）。

用法:
  uv run python tests/cmw500_probe.py --batch status     # 内置批次：身份/状态/当前配置
  uv run python tests/cmw500_probe.py --batch sched      # 内置批次：调度/RB 头部探测
  uv run python tests/cmw500_probe.py --batch meas       # 内置批次：LTE Meas 测量应用
  uv run python tests/cmw500_probe.py --batch cqi        # 内置批次：CQI 动态调度
  uv run python tests/cmw500_probe.py '*IDN?' 'SYST:ERR?'  # 逐条执行

查询命令打印响应；写命令自动回读 SYST:ERR? 报告错误码。
"""

import socket
import sys

HOST, PORT = "172.22.1.3", 5025
TIMEOUT = 4.0  # 无效头查询不会返回响应行，只能靠超时判定；但场景切换等重写命令会阻塞 2s+，
               # err 回读共享此超时——过短会响应错位（上轮 1.5s 在 CSPath 切换时踩坑）


class NoResp(Exception):
    """查询无响应（大概率是未定义指令头）。"""


class Cmw:
    def __init__(self, host=HOST, port=PORT, timeout=TIMEOUT):
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.s.settimeout(timeout)
        self.timeout = timeout

    def drain(self) -> None:
        """排空残留响应字节（应用切换后首条查询响应偏慢，串批时会错位到下一条）。"""
        self.s.settimeout(0.05)
        try:
            while self.s.recv(65536):
                pass
        except (socket.timeout, OSError):
            pass
        self.s.settimeout(self.timeout)

    def send(self, cmd: str) -> str:
        self.drain()
        self.s.sendall((cmd + "\n").encode("ascii"))
        if "?" in cmd.split(" ")[0]:
            data = b""
            try:
                while not data.endswith(b"\n"):
                    chunk = self.s.recv(65536)
                    if not chunk:
                        break
                    data += chunk
            except (socket.timeout, OSError):
                pass
            if not data:
                raise NoResp(cmd)
            return data.decode("latin-1").strip()
        return ""

    def err(self) -> str:
        return self.send("SYST:ERR?")

    def close(self):
        self.s.close()


def run(cm: Cmw, cmd: str) -> None:
    try:
        resp = cm.send(cmd)
    except NoResp:
        resp = ""
    try:
        err = cm.err()
    except NoResp:
        err = "ERR? 无响应"
    if resp:
        print(f"[Q] {cmd}  ->  {resp}")
        if not err.startswith('0,"'):
            print(f"    ERR: {err}")
    else:
        print(f"[-] {cmd}   (无响应, ERR: {err})")


BATCHES = {
    "status": [
        "*IDN?",
        "*OPT?",
        "SYST:ERR?",
        "SOUR:LTE:SIGN:CELL:STATe:ALL?",
        "FETC:LTE:SIGN:PSWitched:STATe?",
        "CALL:LTE:SIGN:PSWitched:STATe?",
        "CONF:LTE:SIGN:DMODe?",
        "CONF:LTE:SIGN:PCC:BAND?",
        "CONF:LTE:SIGN:RFSettings:PCC:CHANnel:DL?",
        "CONF:LTE:SIGN:RFSettings:PCC:CHANnel:UL?",
        "CONF:LTE:SIGN:RFSettings:PCC:FREQuency:DL?",
        "CONF:LTE:SIGN:RFSettings:PCC:FREQuency:UL?",
        "CONF:LTE:SIGN:CELL:BANDwidth:PCC:DL?",
        "CONF:LTE:SIGN:CELL:BANDwidth:PCC:UL?",
        "CONF:LTE:SIGN:RFSettings:ENPower?",
        "CONF:LTE:SIGN:RFSettings:UMARker?",
        "CONF:LTE:SIGN:CELL:POWer?",
        "CONF:LTE:SIGN:CELL:PCID?",
        "ROUT:LTE:SIGN:SCENario?",
    ],
    "sched2": [
        # RMC 子参数是否可单独读写
        "CONF:LTE:SIGN:CONNection:RMC:UL:RB?",
        "CONF:LTE:SIGN:CONNection:RMC:UL:MODulation?",
        "CONF:LTE:SIGN:CONNection:RMC:UL:TBS?",
        "CONF:LTE:SIGN:CONNection:RMC:DL:RB?",
        "CONF:LTE:SIGN:CONNection:RMC:DL:MODulation?",
        "CONF:LTE:SIGN:CONNection:RMC:DL:TBS?",
        # 调度类型选择器（RMC / 用户定义 / CQI / 动态）
        "CONF:LTE:SIGN:CONNection:MODE?",
        "CONF:LTE:SIGN:CONNection:TYPE?",
        "CONF:LTE:SIGN:CONNection:RMC:TYPE?",
        "CONF:LTE:SIGN:CONNection:SCHeduling:TYPE:UL?",
        # 用户定义 TTI（发行说明提到 User defined TTI Based, KS510）
        "CONF:LTE:SIGN:CONNection:UDTTI?",
        "CONF:LTE:SIGN:CONNection:UDTTI:MODE?",
        "CONF:LTE:SIGN:CONNection:UDTTI:TYPE?",
        "CONF:LTE:SIGN:CONNection:UDTTI:UL?",
        "CONF:LTE:SIGN:CONNection:UDTTI:DL?",
        "CONF:LTE:SIGN:CONNection:UDTTI:UL:ALL?",
        "CONF:LTE:SIGN:CONNection:UDTTI:DL:ALL?",
        "CONF:LTE:SIGN:CONNection:UDTTI:UL:SUBF0?",
        "CONF:LTE:SIGN:CONNection:UDTTI:UL:SUBFrame0?",
        # 带索引的用户定义配置
        "CONF:LTE:SIGN:CONNection:UDRB0?",
        "CONF:LTE:SIGN:CONNection:UDSC0?",
        # 其他面板项的头尾变体
        "CONF:LTE:SIGN:RFSettings:UMARker:PCC?",
        "CONF:LTE:SIGN:CELL:POWer:PCC?",
        "CONF:LTE:SIGN:CELL:POWer:DL?",
    ],
    "sched": [
        # 调度模式 / 连接样式
        "CONF:LTE:SIGN:CONNection:STYLe?",
        "CONF:LTE:SIGN:CONNection:SCHeduling:MODE?",
        "CONF:LTE:SIGN:CONNection:SCHeduling:TYPE?",
        "CONF:LTE:SIGN:CONNection:SCHeduling?",
        # 用户定义 RB / UDSC / UDRB
        "CONF:LTE:SIGN:CONNection:UDRB?",
        "CONF:LTE:SIGN:CONNection:UDRB:MODE?",
        "CONF:LTE:SIGN:CONNection:UDRB:COUNt?",
        "CONF:LTE:SIGN:CONNection:UDSC?",
        "CONF:LTE:SIGN:CONNection:UDSC:COUNt?",
        "CONF:LTE:SIGN:CONNection:UDSC:UL?",
        "CONF:LTE:SIGN:CONNection:UDSC:DL?",
        # RMC
        "CONF:LTE:SIGN:CONNection:RMC?",
        "CONF:LTE:SIGN:CONNection:RMC:UL?",
        "CONF:LTE:SIGN:CONNection:RMC:DL?",
        # 直接带 RB 关键字
        "CONF:LTE:SIGN:CONNection:SCHeduling:UL:RB?",
        "CONF:LTE:SIGN:CONNection:SCHeduling:UL:RB:COUNt?",
        "CONF:LTE:SIGN:CONNection:SCHeduling:UL:RB:STARt?",
        "CONF:LTE:SIGN:CONNection:SCHeduling:UL:MODulation?",
        "CONF:LTE:SIGN:CONNection:SCHeduling:DL:RB:COUNt?",
        "CONF:LTE:SIGN:CONNection:SCHeduling:DL:RB:STARt?",
        "CONF:LTE:SIGN:CONNection:SCHeduling:DL:MODulation?",
        # TBS / MCS
        "CONF:LTE:SIGN:CONNection:SCHeduling:UL:TBS?",
        "CONF:LTE:SIGN:CONNection:UDSC:UL:RB:COUNt?",
        "CONF:LTE:SIGN:CONNection:UDSC:UL:MODulation?",
        "CONF:LTE:SIGN:CONNection:UDSC:DL:RB:COUNt?",
    ],
    # LTE Meas 测量应用（2026-09-18）
    "meas": [
        "INST:SEL?",
        "ROUT:LTE:MEAS:SCENario?",
        "ROUT:LTE:MEAS:SCENario:CSPath?",
        "CONF:LTE:MEAS:DMODe?",
        "CONF:LTE:MEAS:BAND?",
        "CONF:LTE:MEAS:RFSettings:ENPower?",
        "CONF:LTE:MEAS:MEValuation:CTYPe?",
        "TRIG:LTE:MEAS:MEValuation:SOUR?",
        "TRIG:LTE:MEAS:MEValuation:MGAP?",
        "CONF:LTE:MEAS:MEValuation:REPetition?",
        "FETC:LTE:MEAS:MEValuation:STATe:ALL?",
        "FETC:LTE:MEAS:MEValuation:MODulation:AVERage?",
        "FETC:LTE:MEAS:MEValuation:ACLR:AVERage?",
        "FETC:LTE:MEAS:MEValuation:ESFLatness:AVERage?",
        "FETC:LTE:MEAS:MEValuation:IEMission:CC1:MARGin:AVERage?",
        "FETC:LTE:SIGN:PSWitched:STATe?",
    ],
    # CQI 动态调度（2026-09-18）
    "cqi": [
        "CONF:LTE:SIGN:CONNection:PCC:STYPe?",
        "CONF:LTE:SIGN:CONNection:PCC:FWBCqi:DL?",
        "CONF:LTE:SIGN:CQIReporting:PCC:CINDex?",
        "FETCh:LTE:SIGN:EBLer:STATe:ALL?",
        "FETCh:LTE:SIGN:EBLer:PCC:CQIReporting:STReam1?",
        "SENSe:LTE:SIGN:CONNection:PCC:FWBCqi:DL:MCSTable:DETermined?",
        "CONF:LTE:SIGN:DL:PCC:RSEPre:LEVel?",
        "FETC:LTE:SIGN:PSWitched:STATe?",
    ],
}


def main() -> int:
    args = sys.argv[1:]
    cmds: list[str] = []
    if "--batch" in args:
        i = args.index("--batch")
        name = args[i + 1]
        cmds = BATCHES[name]
    elif args:
        cmds = args
    else:
        print(__doc__)
        return 2

    cm = Cmw()
    try:
        for c in cmds:
            run(cm, c)
    finally:
        cm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
