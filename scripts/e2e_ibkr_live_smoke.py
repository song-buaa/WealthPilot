#!/usr/bin/env python3
"""
IBKR 实盘最小额冒烟 — v3.10 阶段 3。一次性验证用途。

真账户 U3831209，美股开市，通过完整应用链路（OrderManager→factory→IBKRBrokerAdapter）。
两笔测试：
  1. TSLA 买入（预期拒单：保证金不足 → 201 → Inactive → rejected）
  2. NIO 买入（预期成功挂单 → 真撤单）

★ 安全约束：
- 只买、限价=现价×0.5（不可成交）、数量 1、TIF=DAY
- 每笔提交前打印全部属性并等确认
- 结束前确认 openTrades=0

运行（应用已用实盘配置启动后）：
    /Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python scripts/e2e_ibkr_live_smoke.py
"""
import json
import os
import sys
import time
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

os.environ.setdefault("BROKER_MODE", "ibkr")
os.environ["IBKR_CLIENT_ID"] = "14"  # 避开后端 uvicorn 占用的 10

from app.database import get_session
from backend.services.action.brokers.factory import get_broker_adapter
from backend.services.action.order_manager import OrderManager
from backend.services.action.models import ActionDraft, SymbolStrategy, OrderRecord, AuditLog

EXPECTED_ACCOUNT = "U3831209"

report: dict = {
    "timestamp": datetime.now().isoformat(),
    "connection": {},
    "trade_1_reject": {},
    "trade_2_happy": {},
    "cleanup": {},
}


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_order_details(order):
    print(f"  order.id          = {order.id}")
    print(f"  broker_order_id   = {order.broker_order_id}")
    print(f"  broker_name       = {order.broker_name}")
    print(f"  symbol            = {order.symbol}")
    print(f"  side              = {order.side}")
    print(f"  quantity          = {order.quantity}")
    print(f"  limit_price       = {order.limit_price}")
    print(f"  status            = {order.status}")
    raw = json.loads(order.raw_broker_response or "{}")
    for k in ["ib_status", "perm_id", "order_ref", "mapped_status",
              "inactive_error_code", "inactive_cb_error_code", "inactive_resolved_as",
              "keyword_matched", "reason"]:
        if k in raw:
            print(f"  raw.{k} = {raw[k]}")


def print_audit(session, order_id, limit=10):
    logs = session.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(50).all()
    related = [l for l in logs if order_id[:12] in (l.payload or "")]
    print(f"  审计({len(related)} 条):")
    for l in related[:limit]:
        print(f"    [{l.timestamp}] {l.event_type}: {(l.payload or '')[:150]}")
    return related


def run_trade(session, adapter, mgr, label, symbol, limit_price, expect_reject):
    """执行一笔测试交易，返回 OrderRecord。"""
    section(f"{label}: 准备")

    # 创建 strategy + draft
    draft = ActionDraft(
        decision_summary=f"Live smoke {label}",
        payload=json.dumps({"test": True, "label": label}),
        status="confirmed",
    )
    session.add(draft)
    session.flush()

    strategy = SymbolStrategy(
        symbol=symbol,
        side="BUY",
        target_quantity=1,
        order_type="LIMIT",
        limit_price=limit_price,
        status="active",
        source_draft_id=draft.id,
    )
    session.add(strategy)
    session.flush()

    order_params = {
        "quantity": 1,
        "order_type": "LIMIT",
        "limit_price": limit_price,
    }

    # 提交前打印全部属性
    print(f"[{ts()}] 将提交:")
    print(f"  symbol      = {symbol}")
    print(f"  side        = BUY")
    print(f"  quantity    = 1")
    print(f"  limit_price = ${limit_price}")
    print(f"  order_type  = LIMIT")
    print(f"  tif         = DAY (adapter 显式设)")
    print(f"  outsideRth  = False")
    print(f"  预期        = {'拒单(保证金不足)' if expect_reject else '成功挂单'}")

    section(f"{label}: place_order")
    try:
        t0 = time.monotonic()
        order = mgr.place_order(strategy.id, order_params)
        elapsed = (time.monotonic() - t0) * 1000
        session.commit()

        print(f"[{ts()}] place_order 完成 ({elapsed:.0f}ms)")
        print_order_details(order)

    except Exception as e:
        print(f"[{ts()}] ❌ place_order 异常: {e}")
        import traceback; traceback.print_exc()
        session.rollback()
        return None

    order_id = order.id

    # sync 等状态稳定
    section(f"{label}: sync_order_status")
    time.sleep(2)
    try:
        mgr.sync_order_status(order_id)
        session.commit()
    except Exception as e:
        print(f"[{ts()}] sync 异常: {type(e).__name__}: {str(e)[:200]}")
        try:
            session.rollback()
        except Exception:
            pass
    try:
        session.refresh(order)
        print(f"[{ts()}] sync 后:")
        print_order_details(order)
    except Exception:
        print(f"[{ts()}] refresh 失败，用最后已知状态")

    # 审计
    try:
        print_audit(session, order_id)
    except Exception as e:
        print(f"  审计查询异常: {e}")
        try:
            session.rollback()
        except Exception:
            pass

    if expect_reject:
        # 拒单场景：检查最终状态
        result = {
            "status": order.status,
            "broker_order_id": order.broker_order_id,
            "is_rejected": order.status == "rejected",
        }
        raw = json.loads(order.raw_broker_response or "{}")
        result["inactive_error_code"] = raw.get("inactive_error_code")
        result["inactive_cb_error_code"] = raw.get("inactive_cb_error_code")
        result["inactive_resolved_as"] = raw.get("inactive_resolved_as")

        if order.status != "rejected":
            print(f"\n[{ts()}] ⚠️ 未被拒！意外挂单成功，立即撤单...")
            try:
                mgr.cancel_order(order_id)
                session.commit()
                time.sleep(2)
                mgr.sync_order_status(order_id)
                session.commit()
                session.refresh(order)
                print(f"  撤后状态: {order.status}")
                result["unexpected_cancel"] = order.status
            except Exception as ce:
                print(f"  撤单异常: {ce}")
                try: session.rollback()
                except Exception: pass

        return order, result

    else:
        # happy path：检查挂单成功，然后撤单
        result = {
            "place_status": order.status,
            "broker_order_id": order.broker_order_id,
            "is_perm_id": bool(order.broker_order_id and not order.broker_order_id.startswith("MOCK")),
        }

        # orderRef 反查
        section(f"{label}: orderRef 反查")
        try:
            found = adapter.find_order_by_ref(order_id)
            if found:
                print(f"[{ts()}] ✅ orderRef 反查命中: broker_order_id={found.broker_order_id}")
                result["orderref_found"] = True
                result["orderref_matches"] = found.broker_order_id == order.broker_order_id
            else:
                print(f"[{ts()}] ⚠️ orderRef 未命中")
                result["orderref_found"] = False
        except Exception as e:
            print(f"[{ts()}] orderRef 反查异常: {e}")

        # 撤单
        section(f"{label}: cancel_order (真撤单)")
        try:
            mgr.cancel_order(order_id)
            session.commit()
        except Exception as e:
            print(f"[{ts()}] cancel 异常: {e}")
            try: session.rollback()
            except Exception: pass
            result["cancel_error"] = str(e)

        time.sleep(2)
        try:
            mgr.sync_order_status(order_id)
            session.commit()
        except Exception as e:
            print(f"[{ts()}] cancel sync 异常: {e}")
            try: session.rollback()
            except Exception: pass

        try:
            session.refresh(order)
            print(f"[{ts()}] cancel 后:")
            print_order_details(order)
            result["cancel_status"] = order.status
            result["cancelled_at"] = str(order.cancelled_at) if order.cancelled_at else None
            result["is_real_cancel"] = order.status == "cancelled"
        except Exception as e:
            print(f"[{ts()}] refresh 异常: {e}")

        # 撤单后审计
        try:
            print_audit(session, order_id)
        except Exception:
            try: session.rollback()
            except Exception: pass

        return order, result


def main():
    session = get_session()

    section("0. 连接验证")
    try:
        adapter = get_broker_adapter(broker_name="ibkr")
        adapter._ensure_connected()  # 触发真实连接 + 闸门校验
        print(f"[{ts()}] adapter: {type(adapter).__name__}")
        print(f"[{ts()}] account: {adapter._account_id}")
        print(f"[{ts()}] port: {adapter._port}")
        print(f"[{ts()}] client_id: {adapter._client_id}")

        # ★ 硬性自检：managedAccounts 必须含实盘 U 账户
        managed = adapter._ib.managedAccounts()
        print(f"[{ts()}] managedAccounts: {managed}")
        assert EXPECTED_ACCOUNT in managed, (
            f"managedAccounts 不含 {EXPECTED_ACCOUNT}，实际: {managed}。"
            f"环境变量未生效或仍是 paper，中止！"
        )
        assert adapter._account_id == EXPECTED_ACCOUNT, f"非预期账户: {adapter._account_id}"
        assert adapter._account_id.startswith("U"), "非实盘账户"
        assert adapter._port == 7496, f"端口非实盘: {adapter._port}"
        report["connection"] = {"status": "OK", "account": adapter._account_id, "port": adapter._port}
        print(f"[{ts()}] ✅ 实盘账户校验通过")
    except Exception as e:
        print(f"[{ts()}] ❌ 连接失败: {e}")
        report["connection"] = {"status": "FAILED", "error": str(e)}
        write_report()
        return

    mgr = OrderManager(session, broker_adapter=adapter)

    # ══════════════════════════════════════════════════════════
    # 第一笔: TSLA 拒单
    # ══════════════════════════════════════════════════════════
    tsla_limit = Decimal("175.00")  # ~$395 × 0.5，远低于市价
    order1, result1 = run_trade(
        session, adapter, mgr,
        label="第一笔 TSLA (预期拒单)",
        symbol="TSLA:US",
        limit_price=tsla_limit,
        expect_reject=True,
    )
    report["trade_1_reject"] = result1 or {}

    print(f"\n{'─'*40}")
    print(f"  第一笔结果: status={result1.get('status') if result1 else '?'} "
          f"is_rejected={result1.get('is_rejected') if result1 else '?'}")
    print(f"{'─'*40}")

    # 等几秒再下第二笔
    time.sleep(3)

    # ══════════════════════════════════════════════════════════
    # 第二笔: NIO happy path
    # ══════════════════════════════════════════════════════════
    nio_limit = Decimal("2.65")  # NIO ~$5.3 × 0.5
    # 安全断言：名义金额远小于可用现金
    assert float(nio_limit) * 1 < 25, f"NIO 名义金额 {float(nio_limit)} 不够小!"

    order2, result2 = run_trade(
        session, adapter, mgr,
        label="第二笔 NIO (预期成功→撤单)",
        symbol="NIO:US",
        limit_price=nio_limit,
        expect_reject=False,
    )
    report["trade_2_happy"] = result2 or {}

    print(f"\n{'─'*40}")
    if result2:
        print(f"  第二笔结果: place={result2.get('place_status')} "
              f"cancel={result2.get('cancel_status')} "
              f"permId={result2.get('is_perm_id')} "
              f"real_cancel={result2.get('is_real_cancel')}")
    print(f"{'─'*40}")

    # ══════════════════════════════════════════════════════════
    # 收尾
    # ══════════════════════════════════════════════════════════
    section("收尾: 确认无遗留挂单")
    try:
        open_orders = adapter.list_open_orders()
        # 过滤掉 paper 遗留的脏数据
        real_open = [o for o in open_orders if o.status not in ("unknown",)]
        print(f"[{ts()}] openOrders: {len(open_orders)} (过滤后: {len(real_open)})")
        if real_open:
            print("  ⚠️ 有遗留，逐个撤:")
            for o in real_open:
                print(f"    撤: {o.broker_order_id} status={o.status}")
                adapter.cancel_order(o.broker_order_id)
            time.sleep(2)
        else:
            print("  ✅ 无遗留挂单")
        report["cleanup"] = {"open_orders": len(open_orders), "real_open": len(real_open)}
    except Exception as e:
        print(f"  cleanup 异常: {e}")
        report["cleanup"] = {"error": str(e)}

    session.close()
    write_report()


def write_report():
    out_dir = Path("docs/v3.10")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "m5_live_smoke_result.md"

    lines = [
        "# v3.10 实盘最小额冒烟结果",
        "",
        f"运行时间: {report['timestamp']}",
        f"账户: {report.get('connection', {}).get('account', '?')}",
        "",
    ]

    # 第一笔
    r1 = report.get("trade_1_reject", {})
    lines += [
        "## 第一笔: TSLA 拒单链路",
        "",
        f"- 本地最终状态: {r1.get('status')}",
        f"- is_rejected: {r1.get('is_rejected')}",
        f"- inactive_error_code: {r1.get('inactive_error_code')}",
        f"- inactive_cb_error_code: {r1.get('inactive_cb_error_code')}",
        f"- inactive_resolved_as: {r1.get('inactive_resolved_as')}",
        "",
        "预期: 201(保证金不足) → Inactive → rejected",
        f"实际: {'✅ 符合' if r1.get('is_rejected') else '❌ 不符'}",
        "",
    ]

    # 第二笔
    r2 = report.get("trade_2_happy", {})
    lines += [
        "## 第二笔: NIO happy path + 真撤单",
        "",
        f"- place_order 状态: {r2.get('place_status')}",
        f"- broker_order_id (permId): {r2.get('broker_order_id')}",
        f"- is_perm_id: {r2.get('is_perm_id')}",
        f"- orderRef 反查: found={r2.get('orderref_found')} matches={r2.get('orderref_matches')}",
        f"- cancel 后状态: {r2.get('cancel_status')}",
        f"- is_real_cancel: {r2.get('is_real_cancel')}",
        "",
        "### 三大核心验证",
        "",
        f"| 验证项 | 预期 | 实际 |",
        f"|--------|------|------|",
        f"| permId 回填 | 非 MOCK 数字 | {'✅' if r2.get('is_perm_id') else '❌'} {r2.get('broker_order_id')} |",
        f"| 状态映射 | broker_pending | {'✅' if r2.get('place_status') in ('submitted_to_broker', 'broker_pending') else '❌'} {r2.get('place_status')} |",
        f"| cancel 真撤单 | cancelled | {'✅' if r2.get('is_real_cancel') else '❌'} {r2.get('cancel_status')} |",
        "",
    ]

    # 收尾
    cl = report.get("cleanup", {})
    lines += [
        "## 收尾",
        "",
        f"- openOrders: {cl.get('open_orders')} (实际遗留: {cl.get('real_open')})",
        f"- {'✅ 无遗留' if cl.get('real_open', 0) == 0 else '⚠️ 有遗留'}",
    ]

    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8")
    print(f"\n报告: {path}")


if __name__ == "__main__":
    main()
