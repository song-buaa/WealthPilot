#!/usr/bin/env python3
"""
IBKR Paper 诊断探针 #2 — 开市版，校准 permId 延迟 + Inactive/errorCode。

目标：美股开市期间运行，校准 M2 两个休市没验到的假设：
1. permId 真实回填延迟（定 PERM_ID_WAIT_SECONDS）
2. Inactive + errorCode 真实样子（校准 REJECTED_ERROR_CODES）

安全约束（开市期间，成交风险真实存在）：
- 只连 paper 账户 DUQ629797（assert 校验，DU 开头）
- 正常单：TSLA 限价 = 现价 × 0.5（远低于市价），数量 1，TIF=DAY
- 提交前 assert limitPrice < 现价 * 0.6
- 每笔单观测完立即撤单
- 脚本结束前确认 openTrades 为空

运行：
    /Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python scripts/probe_ibkr_open.py
"""
import asyncio
import time
import traceback
from datetime import datetime
from pathlib import Path

import nest_asyncio
nest_asyncio.apply()

from ib_async import IB, Stock, LimitOrder

# ── 配置 ──────────────────────────────────────────────────────
HOST = "127.0.0.1"
PORT = 7497
CLIENT_ID = 12
EXPECTED_ACCOUNT = "DUQ629797"
SYMBOL = "TSLA"
EXCHANGE = "SMART"
CURRENCY = "USD"

report: dict = {
    "connection": {},
    "scenario_1_permid": {},
    "scenario_2_reject": [],
    "cleanup": {},
    "all_error_codes": [],
}


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_trade_log(trade):
    """打印 trade.log 全量。"""
    for entry in trade.log:
        print(f"  [{entry.time}] status={entry.status} "
              f"errorCode={entry.errorCode} message={entry.message}")


def collect_error_codes(trade) -> list[dict]:
    """从 trade.log 收集所有 errorCode。"""
    codes = []
    for entry in trade.log:
        if entry.errorCode:
            codes.append({
                "errorCode": entry.errorCode,
                "message": entry.message,
                "status": entry.status,
            })
    return codes


async def run():
    ib = IB()

    # ── 连接 ──────────────────────────────────────────────
    section("1. 连接 TWS Paper")
    try:
        await ib.connectAsync(HOST, PORT, clientId=CLIENT_ID, timeout=10)
        print(f"[{ts()}] 连接成功")
    except Exception as e:
        print(f"[{ts()}] ❌ 连接失败: {e}")
        report["connection"] = {"status": "FAILED", "error": str(e)}
        return

    # ── 账户校验（硬约束）──────────────────────────────────
    accounts = ib.managedAccounts()
    print(f"[{ts()}] managedAccounts: {accounts}")
    assert EXPECTED_ACCOUNT in accounts, f"未找到 {EXPECTED_ACCOUNT}，实际: {accounts}"
    assert EXPECTED_ACCOUNT.startswith("DU"), f"非 paper 账户: {EXPECTED_ACCOUNT}"
    report["connection"] = {"status": "OK", "accounts": accounts}
    print(f"[{ts()}] ✅ 账户校验通过: {EXPECTED_ACCOUNT}")

    try:
        # ══════════════════════════════════════════════════
        # 场景 1: permId 回填延迟（正常单）
        # ══════════════════════════════════════════════════
        section("2. 场景 1: permId 回填延迟（TSLA 正常单）")

        contract = Stock(SYMBOL, EXCHANGE, CURRENCY)
        await ib.qualifyContractsAsync(contract)
        print(f"[{ts()}] 合约: {contract}")

        # 获取当前价格（paper 账户可能没实时行情，尝试延迟行情再 fallback）
        mid = None
        try:
            ib.reqMarketDataType(3)  # 3=delayed
            await asyncio.sleep(0.5)
            [ticker] = await ib.reqTickersAsync(contract)
            await asyncio.sleep(2)  # 等延迟行情填充
            ib.sleep(0)
            candidates = [ticker.midpoint(), ticker.last, ticker.close,
                          ticker.marketPrice(), getattr(ticker, 'delayedLast', None)]
            for c in candidates:
                if c is not None and c == c and c > 0:  # not NaN, positive
                    mid = c
                    break
            print(f"[{ts()}] 行情: midpoint={ticker.midpoint()} last={ticker.last} "
                  f"close={ticker.close} marketPrice={ticker.marketPrice()} → 用 {mid}")
        except Exception as mde:
            print(f"[{ts()}] 行情获取异常: {mde}")

        if mid is None or mid != mid or mid <= 0:
            # 最终 fallback：TSLA 当前约 $350-400 区间，用 $50 作为安全限价（远低于任何合理市价）
            mid = 350.0  # 保守假设
            print(f"[{ts()}] ⚠️ 无法取实时/延迟行情，fallback 假设市价 ${mid}")

        limit_price = round(mid * 0.5, 2)
        # 安全断言：限价必须远低于市价
        assert limit_price < mid * 0.6, f"限价 {limit_price} 不够低！市价 {mid}"
        print(f"[{ts()}] 限价: ${limit_price}（市价 ${mid} × 0.5，安全）")

        order = LimitOrder(
            action="BUY",
            totalQuantity=1,
            lmtPrice=limit_price,
            outsideRth=False,
            tif="DAY",
            orderRef=f"PROBE2-{int(time.time())}",
            account=EXPECTED_ACCOUNT,
        )

        t0 = time.monotonic()
        trade = ib.placeOrder(contract, order)
        t_place = time.monotonic()
        print(f"[{ts()}] placeOrder 返回 (耗时 {(t_place-t0)*1000:.0f}ms)")
        print(f"  orderId={trade.order.orderId} permId={trade.order.permId}")

        # 轮询 permId
        permid_wait_start = time.monotonic()
        while trade.order.permId == 0:
            await asyncio.sleep(0.05)
            ib.sleep(0)  # 驱动事件
            if time.monotonic() - permid_wait_start > 10:
                print(f"[{ts()}] ⚠️ permId 10 秒未回填，放弃等待")
                break

        permid_elapsed_ms = (time.monotonic() - permid_wait_start) * 1000
        print(f"[{ts()}] permId={trade.order.permId} 回填耗时: {permid_elapsed_ms:.0f}ms")

        # 等状态稳定
        await asyncio.sleep(2)
        ib.sleep(0)

        # 打印状态序列
        status_seq = []
        print(f"\n  状态序列:")
        for entry in trade.log:
            status_seq.append({"time": str(entry.time), "status": entry.status,
                               "errorCode": entry.errorCode, "message": entry.message})
            print(f"    [{entry.time}] {entry.status} errorCode={entry.errorCode} msg={entry.message}")

        report["scenario_1_permid"] = {
            "permId": trade.order.permId,
            "permId_elapsed_ms": round(permid_elapsed_ms),
            "limit_price": limit_price,
            "market_price": mid,
            "status_sequence": status_seq,
            "final_status": trade.orderStatus.status,
        }
        report["all_error_codes"].extend(collect_error_codes(trade))

        # 撤单
        print(f"\n[{ts()}] 撤单...")
        ib.cancelOrder(order)
        await asyncio.sleep(2)
        ib.sleep(0)
        print(f"  撤后状态: {trade.orderStatus.status}")
        print(f"  撤单 trade.log:")
        print_trade_log(trade)

        # ══════════════════════════════════════════════════
        # 场景 2: Inactive + errorCode（拒单场景）
        # ══════════════════════════════════════════════════
        section("3. 场景 2: 拒单场景")

        reject_cases = [
            {
                "name": "2a) 无效合约 ZZZZINVALID",
                "contract": Stock("ZZZZINVALID", "SMART", "USD"),
                "action": "BUY",
                "quantity": 1,
                "price_override": 1.00,  # 随便一个价
            },
            {
                "name": "2b) 数量异常（TSLA 1 亿股）",
                "contract": contract,  # 已 qualify 的 TSLA
                "action": "BUY",
                "quantity": 100_000_000,
                "price_override": limit_price,
            },
            {
                "name": "2c) 价格异常（TSLA 限价 $0.01）",
                "contract": contract,
                "action": "BUY",
                "quantity": 1,
                "price_override": 0.01,
            },
        ]

        for case in reject_cases:
            print(f"\n--- {case['name']} ---")
            result = {
                "name": case["name"],
                "status": None,
                "error_codes": [],
                "exception": None,
                "is_inactive": False,
            }

            try:
                # 无效合约可能在 qualify 阶段就报错
                test_contract = case["contract"]
                if case["name"].startswith("2a"):
                    try:
                        await ib.qualifyContractsAsync(test_contract)
                        print(f"  [qualify] 竟然成功: {test_contract}")
                    except Exception as qe:
                        print(f"  [qualify] 异常: {type(qe).__name__}: {qe}")
                        result["exception"] = f"qualify: {type(qe).__name__}: {qe}"
                        # 仍然尝试下单，看会发生什么

                test_order = LimitOrder(
                    action=case["action"],
                    totalQuantity=case["quantity"],
                    lmtPrice=case["price_override"],
                    outsideRth=False,
                    tif="DAY",
                    orderRef=f"PROBE2-REJECT-{int(time.time())}",
                    account=EXPECTED_ACCOUNT,
                )

                test_trade = ib.placeOrder(test_contract, test_order)
                print(f"  [placeOrder] orderId={test_trade.order.orderId}")

                # 等待状态
                await asyncio.sleep(3)
                ib.sleep(0)

                result["status"] = test_trade.orderStatus.status
                result["is_inactive"] = (test_trade.orderStatus.status == "Inactive")
                result["error_codes"] = collect_error_codes(test_trade)
                report["all_error_codes"].extend(result["error_codes"])

                print(f"  最终状态: {test_trade.orderStatus.status}")
                print(f"  trade.log:")
                print_trade_log(test_trade)

                # 清理：撤单（如果不是已终态）
                terminal = {"Cancelled", "Filled", "Inactive", "ApiCancelled"}
                if test_trade.orderStatus.status not in terminal:
                    print(f"  撤单...")
                    ib.cancelOrder(test_order)
                    await asyncio.sleep(1)
                    ib.sleep(0)

            except Exception as e:
                print(f"  ❌ 异常: {type(e).__name__}: {e}")
                result["exception"] = f"{type(e).__name__}: {e}"
                traceback.print_exc()

            report["scenario_2_reject"].append(result)

    except Exception as e:
        print(f"\n❌ 未预期异常: {e}")
        traceback.print_exc()

    finally:
        # ══════════════════════════════════════════════════
        # 收尾：确认无遗留挂单
        # ══════════════════════════════════════════════════
        section("4. 收尾：清理遗留挂单")
        await asyncio.sleep(1)
        ib.sleep(0)
        open_trades = ib.openTrades()
        print(f"[{ts()}] openTrades 数量: {len(open_trades)}")

        if open_trades:
            print("  ⚠️ 有遗留挂单，逐个撤销:")
            for ot in open_trades:
                print(f"    撤: orderId={ot.order.orderId} symbol={ot.contract.symbol} "
                      f"status={ot.orderStatus.status}")
                try:
                    ib.cancelOrder(ot.order)
                except Exception as ce:
                    print(f"    撤单异常: {ce}")
            await asyncio.sleep(2)
            ib.sleep(0)
            remaining = ib.openTrades()
            print(f"  清理后 openTrades: {len(remaining)}")
            report["cleanup"] = {"initial": len(open_trades), "remaining": len(remaining)}
        else:
            print("  ✅ 无遗留挂单")
            report["cleanup"] = {"initial": 0, "remaining": 0}

        ib.disconnect()
        print(f"\n[{ts()}] 已断开连接")

    # ── 写报告 ────────────────────────────────────────────
    section("5. 写入报告")
    write_report()


def write_report():
    """写结构化报告到 docs/v3.10/m2_5_probe_open_result.md"""
    out_dir = Path("docs/v3.10")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "m2_5_probe_open_result.md"

    lines = [
        "# M2.5 IBKR Paper 开市诊断探针结果",
        "",
        f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    # 连接
    conn = report["connection"]
    lines += ["## 连接", "", f"状态: {conn.get('status')}", f"账户: {conn.get('accounts')}", ""]

    # 场景 1
    s1 = report["scenario_1_permid"]
    if s1:
        lines += [
            "## 场景 1: permId 回填延迟",
            "",
            f"- permId: {s1.get('permId')}",
            f"- **回填耗时: {s1.get('permId_elapsed_ms')} ms**",
            f"- 市价参考: {s1.get('market_price')}",
            f"- 限价: {s1.get('limit_price')}",
            f"- 最终状态: {s1.get('final_status')}",
            "",
            "### 状态序列",
            "",
            "| 时间 | 状态 | errorCode | message |",
            "|------|------|-----------|---------|",
        ]
        for entry in s1.get("status_sequence", []):
            lines.append(f"| {entry['time']} | {entry['status']} | {entry['errorCode']} | {entry['message']} |")
        lines.append("")

    # 场景 2
    lines += ["## 场景 2: 拒单场景", ""]
    for r in report["scenario_2_reject"]:
        lines += [
            f"### {r['name']}",
            "",
            f"- 最终状态: {r.get('status')}",
            f"- is_Inactive: {r.get('is_inactive')}",
            f"- 异常: {r.get('exception') or '无'}",
            "",
        ]
        if r.get("error_codes"):
            lines += [
                "| errorCode | message | status |",
                "|-----------|---------|--------|",
            ]
            for ec in r["error_codes"]:
                lines.append(f"| {ec['errorCode']} | {ec['message']} | {ec['status']} |")
            lines.append("")

    # 汇总 errorCode
    all_codes = report["all_error_codes"]
    if all_codes:
        seen = {}
        for ec in all_codes:
            key = ec["errorCode"]
            if key not in seen:
                seen[key] = ec["message"]
        lines += [
            "## 汇总: 观测到的全部 errorCode",
            "",
            "| errorCode | 含义 |",
            "|-----------|------|",
        ]
        for code, msg in sorted(seen.items()):
            lines.append(f"| {code} | {msg} |")
        lines.append("")

    # 收尾
    cl = report["cleanup"]
    lines += [
        "## 收尾确认",
        "",
        f"- 清理前 openTrades: {cl.get('initial')}",
        f"- 清理后 openTrades: {cl.get('remaining')}",
        f"- {'✅ 无遗留挂单' if cl.get('remaining', 0) == 0 else '⚠️ 仍有遗留'}",
    ]

    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8")
    print(f"报告已写入: {path}")
    print(f"内容预览:\n{content[:500]}...")


if __name__ == "__main__":
    asyncio.run(run())
