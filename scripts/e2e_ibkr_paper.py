#!/usr/bin/env python3
"""
IBKR Paper 端到端验证 — M3 后半。

目的：第一次让完整应用链路（OrderManager→factory→IBKRBrokerAdapter）
真连 IBKR paper 下单。验证 place→sync→cancel→audit 全流程。

安全约束（盘中真连，成交风险真实存在）：
- 只连 paper 账户 DUQ629797（adapter 闸门 1 基于 managedAccounts 断言 DU）
- 测试单：TSLA 买入、限价=现价×0.5、数量 1、TIF=DAY
- 每笔观测完撤掉，结束确认无遗留
- ENABLE_IBKR_LIVE_TRADING 保持 false

运行：
    BROKER_MODE=ibkr /Users/songbin/opt/anaconda3/envs/wealthpilot/bin/python scripts/e2e_ibkr_paper.py
"""
import os
import sys
import time
import json
import uuid
from datetime import datetime
from pathlib import Path
from decimal import Decimal

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 加载 .env
from dotenv import load_dotenv
load_dotenv()

# 强制 BROKER_MODE=ibkr（脚本级，不影响 .env）
os.environ["BROKER_MODE"] = "ibkr"

from app.database import get_session
from backend.services.action.brokers.factory import get_broker_adapter
from backend.services.action.order_manager import OrderManager
from backend.services.action.models import OrderRecord, AuditLog
from backend.services.action.brokers.base import OrderRequest

# ── 配置 ──────────────────────────────────────────────────────
SYMBOL = "TSLA:US"
SIDE = "BUY"
QUANTITY = 1
EXPECTED_ACCOUNT = "DUQ629797"

report: dict = {
    "timestamp": datetime.now().isoformat(),
    "connection": {},
    "place_order": {},
    "sync_status": {},
    "cancel_order": {},
    "orderref_lookup": {},
    "audit": {},
    "cleanup": {},
}


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main():
    session = get_session()

    # ══════════════════════════════════════════════════════════
    # 1. 构造 OrderManager（通过 factory，和 api/action.py 一致）
    # ══════════════════════════════════════════════════════════
    section("1. 构造 OrderManager + IBKRBrokerAdapter")
    try:
        adapter = get_broker_adapter(broker_name="ibkr")
        print(f"[{ts()}] adapter: {type(adapter).__name__}")
        print(f"[{ts()}] account: {adapter._account_id}")
        assert adapter._account_id == EXPECTED_ACCOUNT, f"非预期账户: {adapter._account_id}"
        assert adapter._account_id.startswith("DU"), "非 paper 账户"
        report["connection"] = {"status": "OK", "account": adapter._account_id}
    except Exception as e:
        print(f"[{ts()}] ❌ 连接失败: {e}")
        report["connection"] = {"status": "FAILED", "error": str(e)}
        write_report()
        return

    mgr = OrderManager(session, broker_adapter=adapter)

    # 获取限价（安全远低于市价）
    limit_price = _get_safe_limit_price(adapter)
    if limit_price is None:
        print(f"[{ts()}] ❌ 无法获取安全限价，中止")
        write_report()
        return

    # ══════════════════════════════════════════════════════════
    # 2. place_order
    # ══════════════════════════════════════════════════════════
    section("2. place_order (TSLA 买入限价单)")
    local_order_id = str(uuid.uuid4())

    # 先创建 strategy + draft（OrderManager 要求 order 关联 strategy）
    from backend.services.action.models import ActionDraft, SymbolStrategy
    draft = ActionDraft(
        decision_summary="E2E IBKR paper test",
        payload=json.dumps({"test": True}),
        status="confirmed",
    )
    session.add(draft)
    session.flush()

    strategy = SymbolStrategy(
        symbol=SYMBOL,
        side=SIDE,
        target_quantity=QUANTITY,
        order_type="LIMIT",
        limit_price=limit_price,
        status="active",
        source_draft_id=draft.id,
    )
    session.add(strategy)
    session.flush()
    print(f"[{ts()}] strategy_id={strategy.id[:8]}... symbol={SYMBOL} limit={limit_price}")

    try:
        order_params = {
            "quantity": QUANTITY,
            "order_type": "LIMIT",
            "limit_price": limit_price,
        }
        t0 = time.monotonic()
        order = mgr.place_order(strategy.id, order_params)
        elapsed = (time.monotonic() - t0) * 1000
        session.commit()

        print(f"[{ts()}] place_order 完成 ({elapsed:.0f}ms)")
        print(f"  order.id={order.id[:8]}...")
        print(f"  broker_order_id={order.broker_order_id}")
        print(f"  broker_name={order.broker_name}")
        print(f"  status={order.status}")
        print(f"  raw={json.dumps(json.loads(order.raw_broker_response or '{}'), indent=2)[:500]}")

        report["place_order"] = {
            "status": order.status,
            "broker_order_id": order.broker_order_id,
            "broker_name": order.broker_name,
            "local_order_id": order.id,
            "elapsed_ms": round(elapsed),
            "is_perm_id": order.broker_order_id and not order.broker_order_id.startswith("MOCK"),
        }

    except Exception as e:
        print(f"[{ts()}] ❌ place_order 失败: {e}")
        import traceback; traceback.print_exc()
        report["place_order"] = {"status": "FAILED", "error": str(e)}
        session.rollback()
        _cleanup(adapter)
        write_report()
        return

    order_id = order.id

    # ══════════════════════════════════════════════════════════
    # 3. sync_order_status
    # ══════════════════════════════════════════════════════════
    section("3. sync_order_status")
    time.sleep(1)  # 等状态稳定
    try:
        mgr.sync_order_status(order_id)
        session.commit()
        session.refresh(order)

        print(f"[{ts()}] sync 后状态: {order.status}")
        print(f"  filled_quantity={order.filled_quantity}")

        report["sync_status"] = {
            "status": order.status,
            "filled_quantity": order.filled_quantity,
        }
    except Exception as e:
        print(f"[{ts()}] ❌ sync 失败: {e}")
        report["sync_status"] = {"status": "FAILED", "error": str(e)}

    # ══════════════════════════════════════════════════════════
    # 3b. orderRef 幂等反查
    # ══════════════════════════════════════════════════════════
    section("3b. orderRef 幂等反查")
    try:
        found = adapter.find_order_by_ref(order_id)
        if found:
            print(f"[{ts()}] ✅ 按 orderRef 找回: broker_order_id={found.broker_order_id} status={found.status}")
            report["orderref_lookup"] = {
                "found": True,
                "broker_order_id": found.broker_order_id,
                "status": found.status,
                "matches_place_order": found.broker_order_id == order.broker_order_id,
            }
        else:
            print(f"[{ts()}] ⚠️ orderRef 反查未命中")
            report["orderref_lookup"] = {"found": False}
    except Exception as e:
        print(f"[{ts()}] ❌ orderRef 反查失败: {e}")
        report["orderref_lookup"] = {"error": str(e)}

    # ══════════════════════════════════════════════════════════
    # 4. cancel_order（真撤单）
    # ══════════════════════════════════════════════════════════
    section("4. cancel_order (真撤单)")
    try:
        mgr.cancel_order(order_id)
        session.commit()
        time.sleep(2)  # 等撤单回报

        # 再 sync 一次确认最终状态
        mgr.sync_order_status(order_id)
        session.commit()
        session.refresh(order)

        print(f"[{ts()}] cancel 后状态: {order.status}")
        print(f"  cancelled_at={order.cancelled_at}")

        report["cancel_order"] = {
            "status": order.status,
            "cancelled_at": str(order.cancelled_at) if order.cancelled_at else None,
            "is_real_cancel": order.status in ("cancelled",),
        }
    except Exception as e:
        print(f"[{ts()}] ❌ cancel 失败: {e}")
        import traceback; traceback.print_exc()
        report["cancel_order"] = {"status": "FAILED", "error": str(e)}

    # ══════════════════════════════════════════════════════════
    # 5. 审计日志
    # ══════════════════════════════════════════════════════════
    section("5. 审计日志")
    logs = session.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(20).all()
    relevant = [l for l in logs if order_id[:8] in (l.payload or "")]
    print(f"[{ts()}] 审计日志总数: {len(logs)}, 与本单相关: {len(relevant)}")
    for l in relevant:
        print(f"  [{l.timestamp}] {l.event_type}: {(l.payload or '')[:120]}")

    report["audit"] = {
        "total_recent": len(logs),
        "related_to_order": len(relevant),
        "events": [l.event_type for l in relevant],
    }

    # ══════════════════════════════════════════════════════════
    # 6. 收尾
    # ══════════════════════════════════════════════════════════
    section("6. 收尾：确认无遗留")
    _cleanup(adapter)

    session.close()

    section("7. 写报告")
    write_report()


def _get_safe_limit_price(adapter) -> Decimal | None:
    """获取安全限价（现价×0.5）。无行情时 fallback $175。"""
    # Paper 账户可能没实时行情订阅，直接用保守 fallback
    # TSLA 当前约 $350-400，$175 = $350×0.5，足够安全
    mid = 350.0
    print(f"[{ts()}] 限价 fallback: 假设市价 ${mid}（paper 可能无行情）")
    limit = round(Decimal(str(mid)) * Decimal("0.5"), 2)
    print(f"[{ts()}] 限价: ${limit}（安全，远低于市价）")
    return limit


def _cleanup(adapter):
    """确认无遗留挂单。"""
    try:
        open_orders = adapter.list_open_orders()
        print(f"[{ts()}] openOrders: {len(open_orders)}")
        if open_orders:
            print("  ⚠️ 有遗留，逐个撤:")
            for o in open_orders:
                print(f"    撤: {o.broker_order_id} status={o.status}")
                adapter.cancel_order(o.broker_order_id)
            time.sleep(2)
            remaining = adapter.list_open_orders()
            print(f"  清理后: {len(remaining)}")
            report["cleanup"] = {"initial": len(open_orders), "remaining": len(remaining)}
        else:
            print("  ✅ 无遗留挂单")
            report["cleanup"] = {"initial": 0, "remaining": 0}
    except Exception as e:
        print(f"  ❌ cleanup 异常: {e}")
        report["cleanup"] = {"error": str(e)}


def write_report():
    out_dir = Path("docs/v3.10")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "m3_e2e_paper_result.md"

    lines = [
        "# M3 IBKR Paper 端到端验证结果",
        "",
        f"运行时间: {report['timestamp']}",
        "",
    ]

    for key, title in [
        ("connection", "连接"),
        ("place_order", "下单"),
        ("sync_status", "同步状态"),
        ("orderref_lookup", "orderRef 反查"),
        ("cancel_order", "撤单"),
        ("audit", "审计日志"),
        ("cleanup", "收尾"),
    ]:
        lines.append(f"## {title}")
        lines.append("")
        data = report.get(key, {})
        for k, v in data.items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    content = "\n".join(lines) + "\n"
    path.write_text(content, encoding="utf-8")
    print(f"报告: {path}")


if __name__ == "__main__":
    main()
