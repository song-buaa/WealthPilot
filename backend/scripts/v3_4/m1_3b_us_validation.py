"""
WealthPilot v3.4 M1.3b -- 美股沙箱验证脚本

WARNING: 仅使用模拟盘账号 21995161433588262
WARNING: 严禁使用实盘账户 4472659
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from decimal import Decimal
from pathlib import Path
from pprint import pformat

_project_root = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "backend"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=_project_root / ".env")

from app.state import startup, get_session
startup()

from backend.services.action.order_manager import OrderManager
from backend.services.action.brokers.credentials import InMemoryCredentialProvider
from backend.services.action.brokers.tiger import TigerBrokerAdapter, OrphanOrderError, TIGER_PAPER_ACCOUNT
from backend.services.action.state_machine import OrderStatus

TIGER_ID = "20159046"
PK_PATH = _project_root / "backend" / "secrets" / "tiger_private_key.pem"

_placed_order_ids: list[str] = []
_results = {}


def _banner(title):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def _record(result, label="OrderStatusUpdate"):
    print(f"\n{label}:")
    print(f"  status:           {result.status}")
    print(f"  broker_order_id:  {result.broker_order_id}")
    print(f"  filled_quantity:  {result.filled_quantity}")
    print(f"  avg_filled_price: {result.avg_filled_price}")
    print(f"  timestamp:        {result.timestamp}")
    print(f"\nraw_response:")
    print(pformat(result.raw_response, width=100))


# ============================================================
def scenario_1_us_buy_held(adapter):
    _banner("场景 1: 美股 LIMIT BUY 挂单 -> HELD (远价,不成交)")
    from backend.services.action.brokers.base import OrderRequest

    req = OrderRequest(
        symbol="QQQ:US", side="BUY", quantity=1, order_type="LIMIT",
        limit_price=Decimal("350.00"), local_order_id="m13b-s1",
    )
    print(f"\n下单: QQQ BUY 1 @ $350 (远低于市价)")

    result = adapter.place_order(req)
    _record(result, "place_order")
    if result.broker_order_id:
        _placed_order_ids.append(result.broker_order_id)

    assert result.status == "submitted_to_broker"
    assert result.broker_order_id is not None

    print("\n等待 3 秒后查单...")
    time.sleep(3)
    status = adapter.get_order_status(result.broker_order_id)
    _record(status, "get_order_status (3s)")
    print(f"\n验证: status == 'broker_pending' ? {status.status == 'broker_pending'}")
    return result.broker_order_id


# ============================================================
def scenario_2_us_buy_fill(adapter):
    _banner("场景 2: 美股 LIMIT BUY 成交全链路")

    session = get_session()
    mgr = OrderManager(session, broker_adapter=adapter)

    draft = mgr.create_draft(
        conversation_id="m13b-buy",
        payload={
            "symbol_strategies": [
                {"symbol": "QQQ:US", "side": "BUY", "quantity": 1,
                 "order_type": "LIMIT", "limit_price": 720.0}
            ],
            "allocation_intents": [], "risk_notes": [], "missing_fields": [],
        },
        decision_summary="M1.3b BUY QQQ",
    )
    entities = mgr.confirm_draft(draft.id)
    strategy = entities[0]

    order = mgr.place_order(strategy.id, {"quantity": 1, "limit_price": 720.0})
    session.commit()
    if order.broker_order_id:
        _placed_order_ids.append(order.broker_order_id)

    print(f"  order.id: {order.id}")
    print(f"  order.status: {order.status}")
    print(f"  order.broker_order_id: {order.broker_order_id}")

    filled = False
    for attempt in range(10):
        time.sleep(3)
        order = mgr.sync_order_status(order.id)
        session.commit()
        print(f"  [{(attempt+1)*3}s] status={order.status} filled={order.filled_quantity}")
        if order.status == "filled":
            filled = True
            print(f"\n  BUY 成交! avg_price={order.avg_filled_price}")
            break

    # 审计日志检查
    from backend.services.action.models import AuditLog
    logs = session.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(5).all()
    print("\n审计日志:")
    for log in logs:
        payload = json.loads(log.payload) if log.payload else {}
        fx = payload.get("fx_rate_to_cny", "-")
        print(f"  {log.event_type}: fx_rate={fx}")

    session.close()
    return order if filled else None


# ============================================================
def scenario_3_us_sell_fill(adapter, buy_order):
    _banner("场景 3: 美股 LIMIT SELL 全链路")

    session = get_session()
    mgr = OrderManager(session, broker_adapter=adapter)

    draft = mgr.create_draft(
        conversation_id="m13b-sell",
        payload={
            "symbol_strategies": [
                {"symbol": "QQQ:US", "side": "SELL", "quantity": 1,
                 "order_type": "LIMIT", "limit_price": 695.0}
            ],
            "allocation_intents": [], "risk_notes": [], "missing_fields": [],
        },
        decision_summary="M1.3b SELL QQQ",
    )
    entities = mgr.confirm_draft(draft.id)
    strategy = entities[0]

    order = mgr.place_order(strategy.id, {"quantity": 1, "limit_price": 695.0})
    session.commit()
    if order.broker_order_id:
        _placed_order_ids.append(order.broker_order_id)

    print(f"  order.id: {order.id}")
    print(f"  order.status: {order.status}")
    print(f"  order.broker_order_id: {order.broker_order_id}")

    filled = False
    for attempt in range(10):
        time.sleep(3)
        order = mgr.sync_order_status(order.id)
        session.commit()
        print(f"  [{(attempt+1)*3}s] status={order.status} filled={order.filled_quantity}")
        if order.status == "filled":
            filled = True
            print(f"\n  SELL 成交! avg_price={order.avg_filled_price}")
            break

    session.close()
    return order if filled else None


# ============================================================
def scenario_4_cancel_filled(adapter, filled_order):
    _banner("场景 4: 撤已成交单 -> False 不报错")

    if not filled_order or not filled_order.broker_order_id:
        print("  跳过(无已成交订单)")
        return

    result = adapter.cancel_order(filled_order.broker_order_id)
    print(f"  cancel_order 返回: {result}")
    print(f"  验证: result == False ? {result is False}")


# ============================================================
def scenario_5_not_found(adapter, s1_order_id):
    _banner("场景 5: not_found 补测")

    # 先撤场景 1 的远价单
    adapter.cancel_order(s1_order_id)
    print(f"  已撤场景 1 订单: {s1_order_id}")
    time.sleep(2)

    # 查已撤订单(Tiger 通常仍能查到历史订单)
    print(f"\n  get_order_status({s1_order_id}) — 已撤单,观察是否触发 not_found")
    t0 = time.time()
    try:
        result = adapter.get_order_status(s1_order_id)
        elapsed = time.time() - t0
        print(f"  未触发 not_found,返回 status={result.status} (耗时 {elapsed:.1f}s)")
        print(f"  记录: Tiger 可查到已撤订单的历史状态")
    except OrphanOrderError as e:
        elapsed = time.time() - t0
        print(f"  触发 OrphanOrderError! 耗时 {elapsed:.1f}s")
        print(f"  isinstance(ConnectionError): {isinstance(e, ConnectionError)}")


# ============================================================
def scenario_6_partial_fill(adapter):
    _banner("场景 6: 部分成交观察 (可选)")
    from backend.services.action.brokers.base import OrderRequest

    req = OrderRequest(
        symbol="QQQ:US", side="BUY", quantity=10, order_type="LIMIT",
        limit_price=Decimal("710.00"), local_order_id="m13b-s6",
    )
    print(f"\n下单: QQQ BUY 10 @ $710 (接近市价,观察部分成交)")
    result = adapter.place_order(req)
    if result.broker_order_id:
        _placed_order_ids.append(result.broker_order_id)
    print(f"  broker_order_id: {result.broker_order_id}")

    saw_partial = False
    for attempt in range(10):
        time.sleep(3)
        if not result.broker_order_id:
            break
        status = adapter.get_order_status(result.broker_order_id)
        print(f"  [{(attempt+1)*3}s] status={status.status} filled={status.filled_quantity}")
        if status.status == "partially_filled":
            saw_partial = True
            print(f"  部分成交! filled={status.filled_quantity}")
        if status.status in ("filled", "cancelled", "rejected", "expired"):
            break

    if not saw_partial:
        print("\n  未观察到 partially_filled 状态")

    # 撤掉
    if result.broker_order_id:
        adapter.cancel_order(result.broker_order_id)
        print("  已撤单")


# ============================================================
def cleanup(adapter):
    """只撤本脚本挂的单。

    WARNING: 严禁调用 list_open_orders + 全部撤单的兜底逻辑。
    见 docs/v3.4/M6_事故记录.md。
    """
    _banner("Cleanup: 撤销本脚本挂的单")
    for oid in _placed_order_ids:
        try:
            adapter.cancel_order(oid)
            print(f"  撤单: {oid}")
        except Exception:
            pass
    print(f"  本脚本产生的 broker_order_id 列表 ({len(_placed_order_ids)} 笔):")
    for oid in _placed_order_ids:
        print(f"    {oid}")


# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("  WealthPilot v3.4 M1.3b -- 美股沙箱验证")
    print("=" * 70)

    provider = InMemoryCredentialProvider()
    provider.save("tiger.paper", {
        "tiger_id": TIGER_ID,
        "account_id": TIGER_PAPER_ACCOUNT,
        "private_key_pem": PK_PATH.read_text().strip(),
    })
    adapter = TigerBrokerAdapter(credential_provider=provider, broker_key="tiger.paper")
    assert adapter._is_paper, "FATAL: 不是模拟盘!"
    assert adapter.authenticate({}), "authenticate 失败!"
    print(f"  模拟盘: {TIGER_PAPER_ACCOUNT}, is_paper={adapter._is_paper}")

    s1_oid = None
    buy_order = None

    # 场景 1
    try:
        s1_oid = scenario_1_us_buy_held(adapter)
        _results["场景1"] = "PASS"
    except Exception as e:
        traceback.print_exc()
        _results["场景1"] = f"FAIL: {e}"

    time.sleep(2)

    # 场景 2
    try:
        buy_order = scenario_2_us_buy_fill(adapter)
        _results["场景2"] = "PASS" if buy_order else "FAIL: 未成交"
    except Exception as e:
        traceback.print_exc()
        _results["场景2"] = f"FAIL: {e}"

    time.sleep(2)

    # 场景 3
    sell_order = None
    try:
        sell_order = scenario_3_us_sell_fill(adapter)
        _results["场景3"] = "PASS" if sell_order else "FAIL: 未成交"
    except Exception as e:
        traceback.print_exc()
        _results["场景3"] = f"FAIL: {e}"

    time.sleep(2)

    # 场景 4
    try:
        scenario_4_cancel_filled(adapter, buy_order or sell_order)
        _results["场景4"] = "PASS"
    except Exception as e:
        traceback.print_exc()
        _results["场景4"] = f"FAIL: {e}"

    # 场景 5
    if s1_oid:
        try:
            scenario_5_not_found(adapter, s1_oid)
            _results["场景5"] = "PASS"
        except Exception as e:
            traceback.print_exc()
            _results["场景5"] = f"FAIL: {e}"
    else:
        _results["场景5"] = "SKIP"

    time.sleep(2)

    # 场景 6
    try:
        scenario_6_partial_fill(adapter)
        _results["场景6"] = "PASS"
    except Exception as e:
        traceback.print_exc()
        _results["场景6"] = f"FAIL: {e}"

    cleanup(adapter)

    _banner("M1.3b 执行结果汇总")
    for k, v in _results.items():
        print(f"  {k}: {v}")
