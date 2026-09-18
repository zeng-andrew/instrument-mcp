#!/usr/bin/env python3
"""cmw_handler 参数关联逻辑冒烟测试（直连真机，socket 桩实现 .write/.query）。

用例设计为对当前配置零扰动:
  1. cmw_lte_snapshot            全面板只读快照
  2. cmw_set_dl_rb N25           当前 B50 允许 N25 -> 直接写入（与现值相同，等效 no-op）
  3. cmw_set_dl_rb N50 不许自动切带宽 -> 应拒绝（带宽表 B50 不含 N50），不发生任何写入
  4. cmw_set_ul_rb N1,QPSK,T5    UL 不受带宽限制 -> 直接写入（与现值相同）
  5. cmw_set_ul_rb Q64 关闭自动开 QAM -> 应拒绝（ULSupport QAM64=OFF），不发生任何写入

用法: uv run python tests/cmw_handler_smoke_test.py
"""

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from instrument_mcp.commands import cmw_handler as H  # noqa: E402

HOST, PORT = "172.22.1.3", 5025


class SocketInst:
    """裸 socket SCPI 会话，提供 VisaInstrument 同款 write/query 接口。"""

    def __init__(self, host: str, port: int = PORT, timeout: float = 5.0):
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.s.settimeout(timeout)

    def write(self, cmd: str) -> None:
        self.s.sendall((cmd + "\n").encode("ascii"))

    def query(self, cmd: str) -> str:
        self.write(cmd)
        data = b""
        while not data.endswith(b"\n"):
            chunk = self.s.recv(65536)
            if not chunk:
                break
            data += chunk
        return data.decode("latin-1").strip()

    def close(self):
        self.s.close()


def main() -> int:
    inst = SocketInst(HOST)
    failures = 0
    try:
        print("=" * 30, "用例1: 面板快照", "=" * 30)
        print(H.cmw_lte_snapshot(inst))

        print("=" * 30, "用例2: DL N25 @B50（应直接写入）", "=" * 30)
        r = H.cmw_set_dl_rb(inst, number_rb="N25", modulation="QPSK",
                            tbs_index="T5", auto_bandwidth=True)
        print(r)
        failures += not r.startswith("[PASS]")

        print("=" * 30, "用例3: DL N50 @B50 不许自动切带宽（应拒绝）", "=" * 30)
        r = H.cmw_set_dl_rb(inst, number_rb="N50", auto_bandwidth=False)
        print(r)
        ok = r.startswith("[FAIL]") and "auto_bandwidth" in r
        failures += not ok
        bw = inst.query("CONF:LTE:SIGN:CELL:BANDwidth:PCC:DL?").strip()
        print(f"复核: 带宽未被改动 = {bw}")
        failures += bw != "B050"

        print("=" * 30, "用例4: UL N1,QPSK（应直接写入）", "=" * 30)
        r = H.cmw_set_ul_rb(inst, number_rb="N1", modulation="QPSK", tbs_index="T5")
        print(r)
        failures += not r.startswith("[PASS]")

        print("=" * 30, "用例5: UL Q64 关闭自动开QAM（应拒绝）", "=" * 30)
        r = H.cmw_set_ul_rb(inst, number_rb="N50", modulation="Q64",
                            tbs_index="KEEP", auto_qam=False)
        print(r)
        ok = r.startswith("[FAIL]") and "auto_qam" in r
        failures += not ok
        qam = inst.query("CONF:LTE:SIGN:CELL:PCC:ULSupport:QAM64:ENABle?").strip()
        print(f"复核: QAM64 开关未被改动 = {qam}")
        failures += qam != "OFF"

        print("=" * 30, f"结果: {'全部通过' if failures == 0 else f'{failures} 项失败'}", "=" * 30)
    finally:
        inst.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
