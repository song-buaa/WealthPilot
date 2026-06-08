#!/usr/bin/env python3
"""
IBKR 实盘连接探针 — 只连接+只读校验，绝对不下单。

目的：验证 v3.10 闸门 1 在真实 live 连接下的安全行为，
确认实盘账户前缀 U 开头。

★ 硬约束：本脚本严禁任何下单/撤单/改单操作。
  只允许：connect、managedAccounts、accountSummary、disconnect。

运行：
    /Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python scripts/probe_ibkr_live_connect.py
"""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import nest_asyncio
nest_asyncio.apply()

from ib_async import IB

# ── 配置 ──────────────────────────────────────────────────────
HOST = "127.0.0.1"
PORT = 7496          # 实盘端口
CLIENT_ID = 13       # 避开 app(10)、历史探针(11/12)
EXPECTED_ACCOUNT = "U3831209"

report: dict = {
    "timestamp": datetime.now().isoformat(),
    "connection": {},
    "managed_accounts": {},
    "account_summary": {},
    "gate1_validation": {},
    "prefix_confirmation": {},
}


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


async def run():
    ib = IB()

    # ══════════════════════════════════════════════════════════
    # 1. 连接
    # ══════════════════════════════════════════════════════════
    section("1. 连接 TWS Live")
    try:
        await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, timeout=10)
        print(f"[{ts()}] ✅ 连接成功 {HOST}:{PORT} clientId={CLIENT_ID}")
        report["connection"] = {"status": "OK", "host": HOST, "port": PORT}
    except Exception as e:
        print(f"[{ts()}] ❌ 连接失败: {e}")
        report["connection"] = {"status": "FAILED", "error": str(e)}
        write_report()
        return

    try:
        # ══════════════════════════════════════════════════════════
        # 2. managedAccounts
        # ══════════════════════════════════════════════════════════
        section("2. managedAccounts")
        accounts = ib.managedAccounts()
        print(f"[{ts()}] managedAccounts: {accounts}")

        has_expected = EXPECTED_ACCOUNT in accounts
        print(f"[{ts()}] 包含 {EXPECTED_ACCOUNT}: {has_expected}")

        report["managed_accounts"] = {
            "accounts": accounts,
            "contains_expected": has_expected,
        }

        if not has_expected:
            print(f"[{ts()}] ⚠️ 未找到预期账户 {EXPECTED_ACCOUNT}")

        # ══════════════════════════════════════════════════════════
        # 3. accountSummary（只读）
        # ══════════════════════════════════════════════════════════
        section("3. accountSummary（只读）")
        try:
            summary = ib.accountSummary(account=EXPECTED_ACCOUNT)
            summary_data = {}
            for item in summary:
                if item.tag in ("NetLiquidation", "BuyingPower", "TotalCashValue",
                                "GrossPositionValue", "AvailableFunds"):
                    summary_data[item.tag] = {
                        "value": item.value,
                        "currency": item.currency,
                    }
                    print(f"  {item.tag}: {item.value} {item.currency}")

            report["account_summary"] = summary_data

            # 验证这不是 paper 模拟的 100 万
            net_liq = float(summary_data.get("NetLiquidation", {}).get("value", 0))
            is_likely_real = net_liq != 1_000_000.0  # paper 默认 100 万 USD
            print(f"\n[{ts()}] NetLiquidation={net_liq}")
            print(f"[{ts()}] 与 paper 100 万模拟不同: {is_likely_real}")

        except Exception as e:
            print(f"[{ts()}] accountSummary 失败: {e}")
            report["account_summary"] = {"error": str(e)}

        # ══════════════════════════════════════════════════════════
        # 4. 闸门 1 live 分支校验
        # ══════════════════════════════════════════════════════════
        section("4. 闸门 1 live 分支校验（代码逻辑，不下单）")

        from backend.services.action.brokers.ibkr import (
            _validate_account_prefix,
            IBKR_LIVE_ACCOUNT_PREFIXES,
            IBKR_PAPER_PREFIX,
        )
        # 需要在 live 模式下测试
        import backend.services.action.brokers.ibkr as ibkr_mod
        original_flag = ibkr_mod.ENABLE_IBKR_LIVE_TRADING

        gate_results = []
        try:
            ibkr_mod.ENABLE_IBKR_LIVE_TRADING = True
            print(f"[{ts()}] ENABLE_IBKR_LIVE_TRADING=True")
            print(f"[{ts()}] IBKR_LIVE_ACCOUNT_PREFIXES={IBKR_LIVE_ACCOUNT_PREFIXES}")
            print(f"[{ts()}] IBKR_PAPER_PREFIX={IBKR_PAPER_PREFIX}")

            test_cases = [
                (EXPECTED_ACCOUNT, True, "真实 live 账户"),
                ("U9999999", True, "其他 U 开头"),
                ("DUQ629797", False, "paper 账户 (DU 开头)"),
                ("", False, "空字符串"),
                ("X1234567", False, "异常前缀"),
            ]

            for account, expect_pass, desc in test_cases:
                try:
                    _validate_account_prefix(account, "live 探针")
                    passed = True
                except AssertionError as e:
                    passed = False
                    err_msg = str(e)

                ok = "✅" if passed == expect_pass else "❌ 不符预期!"
                result = "通过" if passed else "拒绝"
                print(f"  {ok} {desc} ({account or '<空>'}): {result}")

                gate_results.append({
                    "account": account or "<空>",
                    "desc": desc,
                    "expected": "通过" if expect_pass else "拒绝",
                    "actual": result,
                    "match": passed == expect_pass,
                })

        finally:
            ibkr_mod.ENABLE_IBKR_LIVE_TRADING = original_flag

        report["gate1_validation"] = {
            "results": gate_results,
            "all_match": all(r["match"] for r in gate_results),
        }

        # ══════════════════════════════════════════════════════════
        # 5. 确认 live 实盘前缀
        # ══════════════════════════════════════════════════════════
        section("5. 确认 live 实盘前缀")

        live_accounts = [a for a in accounts if not a.startswith("DU")]
        paper_accounts = [a for a in accounts if a.startswith("DU")]

        print(f"[{ts()}] 实盘账户: {live_accounts}")
        print(f"[{ts()}] Paper 账户: {paper_accounts}")

        if live_accounts:
            prefixes = set(a[0] for a in live_accounts)
            print(f"[{ts()}] 实盘前缀: {prefixes}")
            u_confirmed = all(a.startswith("U") for a in live_accounts)
            print(f"[{ts()}] 全部 U 开头: {u_confirmed}")

            report["prefix_confirmation"] = {
                "live_accounts": live_accounts,
                "all_start_with_U": u_confirmed,
                "conclusion": "实盘个人账户确认 U 前缀" if u_confirmed else "存在非 U 前缀实盘账户",
            }
        else:
            report["prefix_confirmation"] = {"conclusion": "未发现实盘账户"}

    finally:
        # ══════════════════════════════════════════════════════════
        # 6. 断连
        # ══════════════════════════════════════════════════════════
        section("6. 断连")
        ib.disconnect()
        print(f"[{ts()}] 已断开连接")
        print(f"[{ts()}] ★ 全程无任何订单操作")

    write_report()


def write_report():
    out_dir = Path("docs/v3.10")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "m5_live_connect_result.md"

    lines = [
        "# M5 IBKR 实盘连接探针结果",
        "",
        f"运行时间: {report['timestamp']}",
        "",
        "**★ 全程只读，无任何订单操作**",
        "",
    ]

    # 连接
    conn = report["connection"]
    lines += ["## 连接", "",
              f"- 状态: {conn.get('status')}",
              f"- 端口: {conn.get('port')}（实盘）",
              ""]

    # managedAccounts
    ma = report["managed_accounts"]
    lines += ["## managedAccounts", "",
              f"- 账户列表: {ma.get('accounts')}",
              f"- 包含预期账户: {ma.get('contains_expected')}",
              ""]

    # accountSummary
    lines += ["## accountSummary（只读）", ""]
    for tag, info in report.get("account_summary", {}).items():
        if isinstance(info, dict) and "value" in info:
            lines.append(f"- {tag}: {info['value']} {info['currency']}")
    lines.append("")

    # 闸门 1
    g1 = report.get("gate1_validation", {})
    lines += ["## 闸门 1 live 校验", "",
              f"全部符合预期: {g1.get('all_match')}", "",
              "| 账户 | 说明 | 预期 | 实际 | 符合? |",
              "|------|------|------|------|-------|"]
    for r in g1.get("results", []):
        m = "✅" if r["match"] else "❌"
        lines.append(f"| {r['account']} | {r['desc']} | {r['expected']} | {r['actual']} | {m} |")
    lines.append("")

    # 前缀确认
    pc = report.get("prefix_confirmation", {})
    lines += ["## live 实盘前缀确认", "",
              f"- 实盘账户: {pc.get('live_accounts')}",
              f"- 全部 U 开头: {pc.get('all_start_with_U')}",
              f"- **结论: {pc.get('conclusion')}**",
              "",
              "此结论消除 v3.10 验证报告 checklist 中「live=U 前缀待确认」的标注。",
              ""]

    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8")
    print(f"\n报告: {path}")


if __name__ == "__main__":
    asyncio.run(run())
