#!/usr/bin/env python3
"""cmw 新能力 MCP 层集成验证（走 FastMCP.call_tool 真实分发路径，直连真机）。

与 cmw_handler_smoke_test.py（socket 桩直调 handler）的区别：本脚本从
`instrument_mcp.server` 的 MCP 层发起调用——import 即完成 YAML 注册，断言
工具注册表完整性，再以 MCP 客户端同款 call_tool(name, arguments) 驱动真机，
验证"YAML 定义 -> Pydantic 模型 -> FastMCP 工具 -> VisaInstrument -> 仪器"全链路。

覆盖 2026-09-18 验证的 LTE Meas / CQI 能力（读为主；场景 CSP->SAL 往返净变化为零）:
  1. 注册完整性：新增工具在册、删除的坏占位（cmw_set_meas_port 等）不在册
  2. FastMCP.list_tools 客户端可见性
  3. cmw_meas_query_scenario / query_state / query_ctype（只读）
  4. cmw_meas_set_scenario_cspath -> run_once（无上行信号时的守卫/超时路径）
     -> cmw_meas_tx_report（NAV/NCAP 容错）
  5. cmw_cqi_stats（CQI 统计 + MCS 映射表）
  6. cmw_meas_set_scenario_salone 还原（到场状态 SAL）
  7. cmw_query_app_name / cmw_query_display_update / cmw_lte_snapshot

用法: uv run python tests/cmw_mcp_integration_test.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import instrument_mcp.server as srv  # noqa: E402  (import 即注册全部 YAML 工具)
from instrument_mcp.commands import get_tool_registry  # noqa: E402

ADDRESS = "TCPIP0::172.22.1.3::5025::SOCKET"
ALIAS = "cmw"

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    failures += not ok


def _block_text(c) -> str:
    if isinstance(c, dict):
        return c.get("text", "")
    return getattr(c, "text", "")


async def call(tool: str, params_json: str = "{}") -> str:
    """以 MCP 客户端同款路径调用工具，取回全部文本块。

    本 mcp SDK 版本 call_tool 返回 (内容块列表, 元数据 dict) 元组。
    """
    result = await srv.mcp.call_tool(tool, {"alias": ALIAS, "params_json": params_json})
    if isinstance(result, tuple):
        blocks = result[0]
    elif isinstance(result, dict):
        blocks = result.get("content", [])
    else:
        blocks = result
    out = "\n".join(t for t in (_block_text(c) for c in blocks) if t)
    return out if out else repr(result)


async def switch_scenario(tool: str, expect: str) -> str:
    """切场景并轮询等待生效（切换是异步重操作，2s+）。"""
    r = await call(tool)
    if not r.startswith("[PASS]"):
        return f"写入失败: {r}"
    for _ in range(4):
        await asyncio.sleep(2)
        r = await call("cmw_meas_query_scenario")
        if expect in r:
            break
    return r


async def main() -> int:
    reg = get_tool_registry()

    print("=" * 30, "1. 注册完整性", "=" * 30)
    new_tools = [
        "cmw_meas_query_scenario", "cmw_meas_set_scenario_cspath",
        "cmw_meas_set_scenario_salone", "cmw_meas_query_state", "cmw_meas_init",
        "cmw_meas_stop", "cmw_meas_set_ctype", "cmw_meas_query_ctype",
        "cmw_meas_set_trigger_source", "cmw_meas_tx_report", "cmw_meas_run_once",
        "cmw_cqi_set", "cmw_cqi_stats", "cmw_init_ebler",
        "cmw_query_display_update",
    ]
    for t in new_tools:
        check(f"在册 {t}", t in reg)
    for t in ("cmw_set_meas_port", "cmw_set_sign_port"):
        check(f"已删 {t} 不在册", t not in reg)
    cmw_total = sum(1 for m in reg.values() if m["instrument_type"] == "cmw")
    check("cmw 工具总数", cmw_total == 97, f"实际 {cmw_total}")

    print("=" * 30, "2. FastMCP 客户端可见性", "=" * 30)
    tools = await srv.mcp.list_tools()
    names = {t.name for t in tools}
    check("list_tools 含 cmw_meas_run_once", "cmw_meas_run_once" in names)
    check("list_tools 含 cmw_cqi_stats", "cmw_cqi_stats" in names)
    # 注册表只含 YAML 工具；server.py 另有 @mcp.tool 直写的通用工具（connect 等），
    # 故断言 YAML 注册表 ⊆ 客户端可见集合
    missing = set(reg) - names
    check("YAML 注册表全部客户端可见", not missing, f"缺失: {sorted(missing)[:5]}")

    print("=" * 30, "3. 连接真机（VISA SOCKET）", "=" * 30)
    r = srv.connect(address=ADDRESS, instrument_type="cmw", alias=ALIAS)
    check("connect", r.startswith("[PASS]") or "已连接" in r, r.splitlines()[0])

    # ---- 以下全部经 mcp.call_tool 分发 ----
    print("=" * 30, "4. Meas/CQI 工具真机调用", "=" * 30)
    r = await call("cmw_meas_query_scenario")
    scen0 = r.split("]")[-1].strip()
    check("cmw_meas_query_scenario", scen0 in ("SAL", "CSP"), scen0)

    r = await call("cmw_meas_query_state")
    check("cmw_meas_query_state", "OFF" in r or "RDY" in r or "RUN" in r, r)
    r = await call("cmw_meas_query_ctype")
    check("cmw_meas_query_ctype", "PUSC" in r, r)

    r = await switch_scenario("cmw_meas_set_scenario_cspath", "CSP")
    check("场景已切 CSP", "CSP" in r, r)

    r = await call("cmw_meas_run_once", '{"timeout_s": 6}')
    # 无上行信号时本固件约 1s 即 RDY"完成"（数据全 INV），报告须带可靠性告警；
    # 有信号时出真实数据。两种都算通过
    ok = r.startswith("[PASS]") and ("可靠性" in r)
    check("cmw_meas_run_once", ok, r.splitlines()[0])

    r = await call("cmw_meas_tx_report")
    check("cmw_meas_tx_report", "调制质量" in r and "ACLR" in r and "带内发射" in r)

    r = await call("cmw_cqi_stats")
    check("cmw_cqi_stats", "CQI中位值" in r and "CQI→MCS映射" in r)

    r = await switch_scenario("cmw_meas_set_scenario_salone", "SAL")
    check("场景已还原 SAL", "SAL" in r, r)

    print("=" * 30, "5. 周边只读工具", "=" * 30)
    r = await call("cmw_query_app_name")
    check("cmw_query_app_name", r.startswith("[PASS]"), r)
    r = await call("cmw_query_display_update")
    check("cmw_query_display_update", r.strip().endswith("1"), r)
    r = await call("cmw_lte_snapshot")
    check("cmw_lte_snapshot", "CMW500 LTE Signaling 面板快照" in r)

    print("=" * 30, f"结果: {'全部通过' if failures == 0 else f'{failures} 项失败'}", "=" * 30)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    finally:
        if ALIAS in srv._sessions:
            try:
                srv._sessions[ALIAS].close()
                srv._sessions.pop(ALIAS)
            except Exception:
                pass
    sys.exit(rc)
