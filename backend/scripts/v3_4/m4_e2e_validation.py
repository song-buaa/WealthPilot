"""
WealthPilot v3.4 M4 — 端到端沙箱联调脚本

验证 OrderManager → TigerBrokerAdapter → Tiger API 全链路。
不启动 FastAPI 服务器,直接 Python 调用,避免无关依赖干扰。

WARNING: 仅使用模拟盘账号 21995161433588262

运行: conda activate wealthpilot && python backend/scripts/v3_4/m4_e2e_validation.py
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

# DB setup
from app.state import startup, get_session
startup()

from backend.services.action.order_manager import OrderManager, InvalidSymbolError
from backend.services.action.brokers.credentials import (
    KeyringCredentialProvider,
    InMemoryCredentialProvider,
    CredentialNotFoundError,
)
from backend.services.action.brokers.tiger import TigerBrokerAdapter, TIGER_PAPER_ACCOUNT
from backend.services.action.brokers.factory import get_broker_adapter
from backend.services.action.state_machine import OrderStatus, StrategyStatus
from backend.services.action.order_poller import scan_orphan_orders

_results = {}
_order_ids = []


def _banner(title):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


# ============================================================
# Task 1: 凭证绑定验证
# ============================================================
def task_1_credential_check():
    _banner("Task 1: 凭证绑定验证")

    provider = KeyringCredentialProvider()
    creds = provider.load("tiger.paper")
    if creds:
        print(f"  tiger.paper 已绑定:")
        print(f"    tiger_id: {creds.get('tiger_id')}")
        print(f"    account:  ***{creds.get('account_id', '')[-5:]}")
        pem = creds.get("private_key_pem", "")
        fingerprint = pem.splitlines()[1][:16] if len(pem.splitlines()) > 1 else "N/A"
        print(f"    私钥指纹: {fingerprint}...")
        return True
    else:
        print("  tiger.paper 未绑定!")
        print("  请先运行: python backend/scripts/v3_4/bind_tiger_credentials.py bind \\")
        print("    --from-file backend/secrets/tiger_private_key.pem")

        # Fallback: 从文件直接构造 InMemoryCredentialProvider
        pk_path = _project_root / "backend" / "secrets" / "tiger_private_key.pem"
        if pk_path.exists():
            print(f"\n  发现私钥文件 {pk_path},使用 InMemoryCredentialProvider 继续...")
            return "fallback"
        return False


# ============================================================
# Task 2: Adapter 初始化 + authenticate
# ============================================================
def task_2_init_adapter():
    _banner("Task 2: Adapter 初始化 + authenticate")

    # 优先用 keyring,fallback 用文件
    provider = KeyringCredentialProvider()
    creds = provider.load("tiger.paper")

    if not creds:
        pk_path = _project_root / "backend" / "secrets" / "tiger_private_key.pem"
        provider = InMemoryCredentialProvider()
        provider.save("tiger.paper", {
            "tiger_id": "20159046",
            "account_id": TIGER_PAPER_ACCOUNT,
            "private_key_pem": pk_path.read_text().strip(),
        })

    adapter = TigerBrokerAdapter(
        credential_provider=provider,
        broker_key="tiger.paper",
    )
    print(f"  broker_name: {adapter.broker_name}")
    print(f"  is_paper: {adapter._is_paper}")

    auth_ok = adapter.authenticate({})
    print(f"  authenticate: {auth_ok}")
    assert auth_ok, "authenticate 失败"

    return adapter, provider


# ============================================================
# Task 3: 端到端下单全链路
# ============================================================
def task_3_e2e_order(adapter):
    _banner("Task 3: 端到端下单全链路 (OrderManager → Tiger)")

    session = get_session()
    mgr = OrderManager(session, broker_adapter=adapter)

    # 3.1 创建 Draft
    print("\n--- 3.1 创建 Draft ---")
    draft = mgr.create_draft(
        conversation_id="m4-e2e-test",
        payload={
            "symbol_strategies": [
                {
                    "symbol": "00700:HK",
                    "side": "BUY",
                    "quantity": 100,
                    "order_type": "LIMIT",
                    "limit_price": 250.0,
                }
            ],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [],
        },
        decision_summary="M4 端到端验证 - 港股远价挂单",
    )
    print(f"  draft.id: {draft.id}")
    print(f"  draft.status: {draft.status}")

    # 3.2 确认 Draft → SymbolStrategy
    print("\n--- 3.2 确认 Draft ---")
    entities = mgr.confirm_draft(draft.id)
    print(f"  创建 {len(entities)} 个实体")
    strategy = entities[0]
    print(f"  strategy.id: {strategy.id}")
    print(f"  strategy.symbol: {strategy.symbol}")
    print(f"  strategy.status: {strategy.status}")

    # 3.3 提交到 Tiger
    print("\n--- 3.3 place_order (触发真实 Tiger API) ---")
    order = mgr.place_order(strategy.id, {
        "quantity": 100,
        "limit_price": 250.0,
    })
    print(f"  order.id: {order.id}")
    print(f"  order.status: {order.status}")
    print(f"  order.broker_order_id: {order.broker_order_id}")
    print(f"  order.broker_name: {order.broker_name}")

    if order.broker_order_id:
        _order_ids.append(order.broker_order_id)

    session.commit()

    # 3.4 查单
    print("\n--- 3.4 查单 (3 秒后) ---")
    time.sleep(3)
    order = mgr.sync_order_status(order.id)
    print(f"  order.status: {order.status}")
    print(f"  order.filled_quantity: {order.filled_quantity}")
    raw = json.loads(order.raw_broker_response) if order.raw_broker_response else {}
    print(f"  raw_response.tiger_status: {raw.get('tiger_status')}")
    print(f"  raw_response.mapped_status: {raw.get('mapped_status')}")

    session.commit()

    # 3.5 撤单
    print("\n--- 3.5 撤单 ---")
    if order.status in OrderStatus.TERMINAL:
        print(f"  订单已终态({order.status}),跳过撤单")
    elif order.broker_order_id:
        cancel_ok = adapter.cancel_order(order.broker_order_id)
        print(f"  adapter.cancel_order: {cancel_ok}")
        order = mgr.sync_order_status(order.id)
        print(f"  同步后 order.status: {order.status}")
    else:
        print(f"  无 broker_order_id,跳过撤单")

    session.commit()

    # 检查审计日志
    print("\n--- 审计日志检查 ---")
    from backend.services.action.models import AuditLog
    logs = session.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(10).all()
    for log in logs:
        payload = json.loads(log.payload) if log.payload else {}
        has_fx = "fx_rate_to_cny" in payload
        print(f"  {log.event_type}: fx_rate={'Y' if has_fx else 'N'}")

    session.close()
    return order


# ============================================================
# Task 4: 轮询 Worker 验证(简化: 手动调 poll_once)
# ============================================================
def task_4_poller_verify(adapter, provider):
    _banner("Task 4: 轮询 Worker 验证")

    session = get_session()
    mgr = OrderManager(session, broker_adapter=adapter)

    # 挂一笔新单不撤,然后手动 poll
    print("\n--- 挂一笔远价单 ---")
    draft = mgr.create_draft(
        conversation_id="m4-poller-test",
        payload={
            "symbol_strategies": [
                {"symbol": "00700:HK", "side": "BUY", "quantity": 100,
                 "order_type": "LIMIT", "limit_price": 200.0}
            ],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [],
        },
        decision_summary="M4 轮询验证",
    )
    entities = mgr.confirm_draft(draft.id)
    strategy = entities[0]
    order = mgr.place_order(strategy.id, {"quantity": 100, "limit_price": 200.0})
    if order.broker_order_id:
        _order_ids.append(order.broker_order_id)
    session.commit()
    print(f"  order.id: {order.id}, status: {order.status}")

    # 模拟 poller 调 sync_order_status
    print("\n--- 模拟 poll_once (3 秒后) ---")
    time.sleep(3)
    order = mgr.sync_order_status(order.id)
    print(f"  同步后 status: {order.status}")
    session.commit()

    # 撤单清理
    if order.broker_order_id and order.status not in OrderStatus.TERMINAL:
        adapter.cancel_order(order.broker_order_id)
        order = mgr.sync_order_status(order.id)
        print(f"  撤单后 status: {order.status}")
    session.commit()
    session.close()


# ============================================================
# Task 5: 异常场景验证
# ============================================================
def task_5_error_scenarios(adapter, provider):
    _banner("Task 5: 异常场景验证")

    # 5.1 凭证缺失
    print("\n--- 5.1 凭证缺失 ---")
    empty_provider = InMemoryCredentialProvider()
    try:
        get_broker_adapter(broker_name="tiger", mode="paper", credential_provider=empty_provider)
        print("  FAIL: 没有抛 CredentialNotFoundError")
    except CredentialNotFoundError as e:
        print(f"  PASS: {e}")

    session = get_session()
    mgr = OrderManager(session, broker_adapter=adapter)

    # 5.2 A 股拦截
    print("\n--- 5.2 A 股拦截 ---")
    draft = mgr.create_draft(
        conversation_id="m4-error-5.2",
        payload={
            "symbol_strategies": [
                {"symbol": "600519:SH", "side": "BUY", "quantity": 100,
                 "order_type": "LIMIT", "limit_price": 1000.0}
            ],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [],
        },
    )
    entities = mgr.confirm_draft(draft.id)
    strategy = entities[0]
    order = mgr.place_order(strategy.id, {"quantity": 100, "limit_price": 1000.0})
    print(f"  order.status: {order.status}")
    print(f"  broker_order_id: {order.broker_order_id}")
    raw = json.loads(order.raw_broker_response) if order.raw_broker_response else {}
    reason = raw.get("reason", "")
    print(f"  reason: {reason}")
    print(f"  PASS: status=rejected" if order.status == "rejected" else f"  FAIL: status={order.status}")
    session.commit()

    # 5.3 中文名保护
    print("\n--- 5.3 中文名保护 ---")
    draft3 = mgr.create_draft(
        conversation_id="m4-error-5.3",
        payload={
            "symbol_strategies": [
                {"symbol": "理想汽车", "side": "BUY", "quantity": 10,
                 "order_type": "LIMIT", "limit_price": 50.0}
            ],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [],
        },
    )
    entities3 = mgr.confirm_draft(draft3.id)
    strategy3 = entities3[0]
    try:
        mgr.place_order(strategy3.id, {"quantity": 10, "limit_price": 50.0})
        print("  FAIL: 没有抛 InvalidSymbolError")
    except InvalidSymbolError as e:
        print(f"  PASS: {e}")

    session.commit()

    # 5.4 孤儿订单扫描
    print("\n--- 5.4 孤儿订单扫描 ---")
    count = scan_orphan_orders(get_session, lambda: adapter)
    print(f"  发现 {count} 笔孤儿订单")

    session.close()


# ============================================================
# Cleanup
# ============================================================
def cleanup(adapter):
    """脚本结束清理: 只撤本脚本挂的单。

    WARNING: 严禁调用 list_open_orders + 全部撤单的兜底逻辑。
    Tiger 账户里可能有用户在 Tiger App 手动挂的订单,
    list_open_orders 会返回所有未成交订单(包括用户的),
    全部撤单 = 误伤用户订单。

    见 docs/v3.4/M6_事故记录.md。
    """
    _banner("Cleanup: 撤销本脚本挂的单")
    for oid in _order_ids:
        try:
            adapter.cancel_order(oid)
            print(f"  撤单: {oid}")
        except Exception:
            pass
    print(f"  清理完成,共追踪 {len(_order_ids)} 笔订单")


# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("  WealthPilot v3.4 M4 — 端到端沙箱联调")
    print("=" * 70)

    confirm = input("确认仅在模拟盘操作? 输入 YES_PAPER_ONLY 继续: ")
    assert confirm == "YES_PAPER_ONLY", "未确认,退出"

    # Task 1
    try:
        cred_status = task_1_credential_check()
        _results["Task1"] = "PASS" if cred_status else "FAIL"
    except Exception as e:
        print(f"  FAIL: {e}")
        traceback.print_exc()
        _results["Task1"] = f"FAIL: {e}"

    # Task 2
    try:
        adapter, provider = task_2_init_adapter()
        _results["Task2"] = "PASS"
    except Exception as e:
        print(f"  FAIL: {e}")
        traceback.print_exc()
        _results["Task2"] = f"FAIL: {e}"
        sys.exit(1)

    # Task 3
    try:
        task_3_e2e_order(adapter)
        _results["Task3"] = "PASS"
    except Exception as e:
        print(f"\n  FAIL: {e}")
        traceback.print_exc()
        _results["Task3"] = f"FAIL: {e}"

    time.sleep(2)

    # Task 4
    try:
        task_4_poller_verify(adapter, provider)
        _results["Task4"] = "PASS"
    except Exception as e:
        print(f"\n  FAIL: {e}")
        traceback.print_exc()
        _results["Task4"] = f"FAIL: {e}"

    time.sleep(2)

    # Task 5
    try:
        task_5_error_scenarios(adapter, provider)
        _results["Task5"] = "PASS"
    except Exception as e:
        print(f"\n  FAIL: {e}")
        traceback.print_exc()
        _results["Task5"] = f"FAIL: {e}"

    # Cleanup
    cleanup(adapter)

    # 汇总
    _banner("M4 联调结果汇总")
    for k, v in _results.items():
        print(f"  {k}: {v}")
