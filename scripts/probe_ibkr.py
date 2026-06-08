#!/usr/bin/env python3
"""
IBKR Paper 诊断探针 — 一次性脚本，校准 M2 假设。

用途：第一次真实连接 IBKR TWS Paper，观测未经 adapter 映射的"地面真相"。
校准：_wait_for_perm_id 的 2 秒够不够、Inactive/errorCode 表、orderRef 反查。

安全约束：
- 只连 paper 账户 DUQ629797（assert 校验）
- 只下 1 股 TSLA，限价 = 现价 × 0.6（绝不可成交）
- 观测完立即撤单 + 断连
- clientId=11（与 app 的 10 区分）

运行：
    /Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python scripts/probe_ibkr.py

注意：需要 TWS 已启动、API 已启用（Configure → API → Settings → Enable ActiveX and Socket Clients）。
"""
import asyncio
import time
from datetime import datetime

# ib_async 需要 nest_asyncio（已随 ib_async 安装）
import nest_asyncio
nest_asyncio.apply()

from ib_async import IB, Stock, LimitOrder, MarketOrder


HOST = "127.0.0.1"
PORT = 7497
CLIENT_ID = 11
EXPECTED_ACCOUNT = "DUQ629797"
SYMBOL = "TSLA"
EXCHANGE = "SMART"
CURRENCY = "USD"
ORDER_REF = f"PROBE-{int(time.time())}"

# 收集结构化结果
report: dict = {}


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main():
    ib = IB()

    # ══════════════════════════════════════════════════════════
    # A. 连接层
    # ══════════════════════════════════════════════════════════
    section("A. 连接层")
    try:
        ib.connect(HOST, PORT, clientId=CLIENT_ID, timeout=10)
    except Exception as e:
        msg = (
            f"连接失败 ({HOST}:{PORT}): {type(e).__name__}: {e}\n"
            f"请检查:\n"
            f"  1. TWS 是否已启动\n"
            f"  2. Configure → API → Settings → Enable ActiveX and Socket Clients ✓\n"
            f"  3. Socket port = {PORT}\n"
            f"  4. Read-Only API → 取消勾选（否则无法下单）\n"
            f"  5. Trusted IPs 包含 127.0.0.1"
        )
        print(f"  ❌ {msg}")
        report["connection"] = {"status": "FAILED", "error": str(e)}
        _write_report()
        return

    print(f"  ✅ 连接成功 {HOST}:{PORT} clientId={CLIENT_ID}")

    # 账户校验
    accounts = ib.managedAccounts()
    print(f"  managedAccounts: {accounts}")
    report["connection"] = {"status": "OK", "accounts": accounts}

    assert len(accounts) > 0, "managedAccounts 为空"
    account = accounts[0]
    assert account.startswith("DU"), f"非 Paper 账户: {account}"
    assert account == EXPECTED_ACCOUNT, f"账户不匹配: {account} != {EXPECTED_ACCOUNT}"
    print(f"  ✅ 账户校验通过: {account}")

    # accountSummary
    try:
        summary = ib.accountSummary(account=account)
        summary_dict = {}
        for item in summary:
            if item.tag in ("TotalCashValue", "NetLiquidation", "BuyingPower",
                            "AvailableFunds", "GrossPositionValue"):
                summary_dict[item.tag] = item.value
                print(f"  {item.tag} = {item.value} {item.currency}")
        report["account_summary"] = summary_dict
    except Exception as e:
        print(f"  ⚠️ accountSummary 异常: {e}")
        report["account_summary"] = {"error": str(e)}

    # ══════════════════════════════════════════════════════════
    # 下单准备：获取现价
    # ══════════════════════════════════════════════════════════
    section("下单准备：获取 TSLA 现价")

    contract = Stock(SYMBOL, EXCHANGE, CURRENCY)
    ib.qualifyContracts(contract)
    print(f"  conId: {contract.conId}")
    report["contract"] = {"symbol": SYMBOL, "conId": contract.conId}

    # 请求延迟行情（paper 通常是 15 分钟延迟）
    ib.reqMarketDataType(3)  # 3 = delayed
    ticker = ib.reqMktData(contract)
    ib.sleep(3)  # 等行情回来

    last_price = ticker.last or ticker.close or ticker.marketPrice()
    print(f"  ticker.last={ticker.last}, ticker.close={ticker.close}, marketPrice={ticker.marketPrice()}")
    print(f"  使用价格: {last_price}")

    if not last_price or last_price <= 0 or str(last_price) == 'nan':
        print("  ❌ 无法获取现价，中止下单")
        report["price"] = {"status": "FAILED", "last": str(last_price)}
        ib.disconnect()
        _write_report()
        return

    limit_price = round(float(last_price) * 0.6, 2)
    print(f"  限价 = 现价 × 0.6 = {limit_price}")
    assert limit_price < float(last_price), f"限价 {limit_price} >= 现价 {last_price}，中止！"
    report["price"] = {"last": float(last_price), "limit": limit_price}

    ib.cancelMktData(contract)

    # ══════════════════════════════════════════════════════════
    # B~F. 下单 + 观测
    # ══════════════════════════════════════════════════════════
    section("B~F. 下单 + 观测")

    order = LimitOrder("BUY", 1, limit_price)
    order.outsideRth = False
    order.tif = "DAY"
    order.orderRef = ORDER_REF
    print(f"  orderRef: {ORDER_REF}")
    print(f"  outsideRth: {order.outsideRth}, tif: {order.tif}")

    # 记录下单时刻
    t0 = time.monotonic()
    trade = ib.placeOrder(contract, order)
    t_place = time.monotonic()
    print(f"\n  [B] placeOrder 返回 @ {ts()}")
    print(f"      orderId={trade.order.orderId}")
    print(f"      permId={trade.order.permId} (可能为 0，等待回填)")
    print(f"      orderRef={trade.order.orderRef}")
    print(f"      初始 status={trade.orderStatus.status}")

    # permId 回填耗时
    perm_id_wait_start = time.monotonic()
    perm_id = 0
    for _ in range(100):  # 最多 10 秒
        ib.sleep(0.1)
        perm_id = trade.order.permId or trade.orderStatus.permId
        if perm_id and perm_id > 0:
            break
    perm_id_elapsed_ms = (time.monotonic() - perm_id_wait_start) * 1000

    print(f"\n  [B] permId 回填结果:")
    print(f"      permId = {perm_id}")
    print(f"      耗时 = {perm_id_elapsed_ms:.1f} ms")
    print(f"      trade.order.permId = {trade.order.permId}")
    print(f"      trade.orderStatus.permId = {trade.orderStatus.permId}")
    report["perm_id"] = {
        "value": perm_id,
        "elapsed_ms": round(perm_id_elapsed_ms, 1),
        "order_perm_id": trade.order.permId,
        "status_perm_id": trade.orderStatus.permId,
    }

    # 等几秒观测状态变化
    print(f"\n  [C] 状态序列（等 5 秒观测）:")
    status_log = []
    prev_status = ""
    for i in range(50):
        ib.sleep(0.1)
        cur = trade.orderStatus.status
        if cur != prev_status:
            entry = {"time": ts(), "status": cur, "elapsed_s": round(time.monotonic() - t_place, 2)}
            status_log.append(entry)
            print(f"      {entry['time']} → {cur} (elapsed {entry['elapsed_s']}s)")
            prev_status = cur
    report["status_sequence"] = status_log

    # [D] trade.log 全量
    print(f"\n  [D] trade.log ({len(trade.log)} 条):")
    log_entries = []
    for entry in trade.log:
        e = {
            "time": str(entry.time),
            "status": entry.status,
            "message": entry.message,
            "errorCode": entry.errorCode,
        }
        log_entries.append(e)
        print(f"      {e['time']} | status={e['status']} | errorCode={e['errorCode']} | msg={e['message']}")
    report["trade_log"] = log_entries

    # [E] orderRef 读回
    print(f"\n  [E] orderRef 读回: {trade.order.orderRef}")
    report["order_ref"] = {
        "written": ORDER_REF,
        "read_back": trade.order.orderRef,
        "match": trade.order.orderRef == ORDER_REF,
    }

    # [F] 全部 ID
    print(f"\n  [F] 全部 ID:")
    ids = {
        "orderId": trade.order.orderId,
        "permId": trade.order.permId,
        "clientId": trade.order.clientId,
        "account": trade.order.account,
        "conId": contract.conId,
        "orderRef": trade.order.orderRef,
    }
    for k, v in ids.items():
        print(f"      {k} = {v}")
    report["ids"] = ids

    # [G] orderRef 反查
    print(f"\n  [G] orderRef 反查:")
    found_by_ref = None
    for t in ib.trades():
        if t.order.orderRef == ORDER_REF:
            found_by_ref = t
            break
    if found_by_ref:
        print(f"      ✅ 在 ib.trades() 中按 orderRef 找到: orderId={found_by_ref.order.orderId}")
        report["order_ref_lookup"] = {"found": True, "orderId": found_by_ref.order.orderId}
    else:
        print(f"      ❌ 在 ib.trades() 中按 orderRef 未找到")
        report["order_ref_lookup"] = {"found": False}

    # openTrades 也查一下
    found_in_open = None
    for t in ib.openTrades():
        if t.order.orderRef == ORDER_REF:
            found_in_open = t
            break
    print(f"      openTrades 反查: {'✅ 找到' if found_in_open else '❌ 未找到'}")

    # ══════════════════════════════════════════════════════════
    # H. 撤单流程
    # ══════════════════════════════════════════════════════════
    section("H. 撤单流程")

    t_cancel_start = time.monotonic()
    ib.cancelOrder(trade.order)
    print(f"  cancelOrder 发出 @ {ts()}")

    cancel_status_log = []
    prev_status = trade.orderStatus.status
    for i in range(50):  # 最多 5 秒
        ib.sleep(0.1)
        cur = trade.orderStatus.status
        if cur != prev_status:
            entry = {
                "time": ts(),
                "status": cur,
                "elapsed_ms": round((time.monotonic() - t_cancel_start) * 1000),
            }
            cancel_status_log.append(entry)
            print(f"      {entry['time']} → {cur} (elapsed {entry['elapsed_ms']}ms)")
            prev_status = cur
            if cur in ("Cancelled", "ApiCancelled", "Filled"):
                break
    report["cancel_sequence"] = cancel_status_log

    # 撤单后 trade.log
    print(f"\n  撤单后 trade.log ({len(trade.log)} 条):")
    cancel_log = []
    for entry in trade.log:
        e = {
            "time": str(entry.time),
            "status": entry.status,
            "message": entry.message,
            "errorCode": entry.errorCode,
        }
        cancel_log.append(e)
        print(f"      {e['time']} | status={e['status']} | errorCode={e['errorCode']} | msg={e['message']}")
    report["cancel_trade_log"] = cancel_log

    final_status = trade.orderStatus.status
    print(f"\n  最终状态: {final_status}")
    report["final_status"] = final_status

    # ══════════════════════════════════════════════════════════
    # 清理
    # ══════════════════════════════════════════════════════════
    section("清理")
    ib.disconnect()
    print("  ✅ 已断开连接")

    _write_report()
    print(f"\n  报告已写入 docs/v3.10/m2_5_probe_result.md")


def _write_report():
    """把 report dict 写成 markdown。"""
    import os
    os.makedirs("docs/v3.10", exist_ok=True)

    lines = [
        "# IBKR Paper 诊断探针结果",
        "",
        f"> 运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 账户: {EXPECTED_ACCOUNT} | 端口: {PORT} | clientId: {CLIENT_ID}",
        "",
        "---",
        "",
    ]

    # 连接
    conn = report.get("connection", {})
    lines.append("## A. 连接层")
    lines.append(f"- 状态: **{conn.get('status', 'N/A')}**")
    if conn.get("error"):
        lines.append(f"- 错误: `{conn['error']}`")
    if conn.get("accounts"):
        lines.append(f"- managedAccounts: `{conn['accounts']}`")
    lines.append("")

    # 账户摘要
    summary = report.get("account_summary", {})
    if summary:
        lines.append("## A. 账户摘要")
        for k, v in summary.items():
            if k != "error":
                lines.append(f"- {k}: `{v}`")
        lines.append("")

    # 价格
    price = report.get("price", {})
    if price:
        lines.append("## 下单准备")
        lines.append(f"- 现价: `{price.get('last')}`")
        lines.append(f"- 限价 (×0.6): `{price.get('limit')}`")
        lines.append("")

    # permId
    perm = report.get("perm_id", {})
    if perm:
        lines.append("## B. permId 回填")
        lines.append(f"- permId: `{perm.get('value')}`")
        lines.append(f"- 回填耗时: **{perm.get('elapsed_ms')} ms**")
        lines.append(f"- order.permId: `{perm.get('order_perm_id')}`")
        lines.append(f"- orderStatus.permId: `{perm.get('status_perm_id')}`")
        lines.append(f"- 结论: {'✅ 2 秒足够' if perm.get('elapsed_ms', 9999) < 2000 else '⚠️ 超过 2 秒，需调大 PERM_ID_WAIT_SECONDS'}")
        lines.append("")

    # 状态序列
    seq = report.get("status_sequence", [])
    if seq:
        lines.append("## C. 状态序列")
        lines.append("| 时间 | 状态 | 耗时(s) |")
        lines.append("|------|------|---------|")
        for s in seq:
            lines.append(f"| {s['time']} | `{s['status']}` | {s['elapsed_s']} |")
        lines.append("")

    # trade.log
    tlog = report.get("trade_log", [])
    if tlog:
        lines.append("## D. trade.log（下单后）")
        lines.append("| 时间 | status | errorCode | message |")
        lines.append("|------|--------|-----------|---------|")
        for e in tlog:
            lines.append(f"| {e['time']} | `{e['status']}` | {e['errorCode']} | {e['message']} |")
        lines.append("")

    # orderRef
    oref = report.get("order_ref", {})
    if oref:
        lines.append("## E. orderRef")
        lines.append(f"- 写入: `{oref.get('written')}`")
        lines.append(f"- 读回: `{oref.get('read_back')}`")
        lines.append(f"- 匹配: {'✅' if oref.get('match') else '❌'}")
        lines.append("")

    # IDs
    ids = report.get("ids", {})
    if ids:
        lines.append("## F. 全部 ID")
        for k, v in ids.items():
            lines.append(f"- {k}: `{v}`")
        lines.append("")

    # orderRef lookup
    lookup = report.get("order_ref_lookup", {})
    if lookup:
        lines.append("## G. orderRef 反查")
        lines.append(f"- trades() 反查: {'✅ 找到' if lookup.get('found') else '❌ 未找到'}")
        if lookup.get("orderId"):
            lines.append(f"- 反查到的 orderId: `{lookup['orderId']}`")
        lines.append("")

    # 撤单
    cseq = report.get("cancel_sequence", [])
    if cseq:
        lines.append("## H. 撤单状态序列")
        lines.append("| 时间 | 状态 | 耗时(ms) |")
        lines.append("|------|------|----------|")
        for s in cseq:
            lines.append(f"| {s['time']} | `{s['status']}` | {s['elapsed_ms']} |")
        lines.append("")

    clog = report.get("cancel_trade_log", [])
    if clog:
        lines.append("## H. 撤单后 trade.log")
        lines.append("| 时间 | status | errorCode | message |")
        lines.append("|------|--------|-----------|---------|")
        for e in clog:
            lines.append(f"| {e['time']} | `{e['status']}` | {e['errorCode']} | {e['message']} |")
        lines.append("")

    final = report.get("final_status", "N/A")
    lines.append(f"## 最终状态: `{final}`")
    lines.append("")

    with open("docs/v3.10/m2_5_probe_result.md", "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
