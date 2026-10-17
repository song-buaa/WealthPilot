"""B0 fault injection on disposable WAL databases; no real broker I/O."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import sqlite3
import threading
import time

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Portfolio
from backend.services.action.models import OrderRecord, SymbolStrategy, AuditLog
from backend.services.action.order_manager import OrderManager
from backend.services.action.order_poller import scan_orphan_orders
from backend.services.action.brokers.base import OrderStatusUpdate


class ProcessCrash(BaseException):
    pass


class Broker:
    broker_name = "ibkr"
    _account_id = "DU-SYNTHETIC-B0"

    def __init__(self, factory):
        self.factory = factory
        self.requests = []
        self.observation = None
        self.mode = "open"
        self.lock = threading.Lock()

    def place_order(self, request):
        # A separate connection must be able to write while broker I/O is active.
        with self.factory() as independent:
            independent.connection().exec_driver_sql("BEGIN IMMEDIATE")
            persisted = independent.get(OrderRecord, request.local_order_id)
            assert persisted.status == "unknown" and persisted.submission_key
            assert persisted.submission_payload_json and persisted.broker_account_id == self._account_id
            independent.rollback()
        with self.lock:
            self.requests.append(request)
        self.observation = OrderStatusUpdate("broker-perm-10", request.local_order_id,
            "filled", filled_quantity=request.quantity, avg_filled_price=Decimal("10"),
            raw_response={"observed_account_id": self._account_id})
        if self.mode == "crash":
            raise ProcessCrash("accepted by broker, local process lost")
        if self.mode == "timeout":
            raise TimeoutError("accepted, reply lost")
        if self.mode == "partial":
            return OrderStatusUpdate("broker-perm-10", request.local_order_id, "partially_filled",
                filled_quantity=4, avg_filled_price=Decimal("10"))
        if self.mode == "filled":
            return self.observation
        time.sleep(.04)  # deterministic overlap for concurrent identical HTTP retries
        return OrderStatusUpdate("broker-perm-10", request.local_order_id, "broker_pending")

    def find_order_by_ref(self, ref):
        return self.observation if self.observation and self.observation.local_order_id == ref else None

    def cancel_order(self, _id):
        raise AssertionError("fault tests never send cancellation mutations")


@pytest.fixture
def rig(tmp_path):
    path = tmp_path / "isolated.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 5, "check_same_thread": False})
    @event.listens_for(engine, "connect")
    def wal(connection, _):
        connection.execute("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, info={"portfolio_id": 11}, expire_on_commit=False)
    with factory() as db:
        db.add_all([Portfolio(id=11, name="Synthetic owner"), Portfolio(id=12, name="Other owner")])
        manager = OrderManager(db)
        draft = manager.create_draft("synthetic-conversation", {"symbol_strategies": [
            {"symbol": "AAPL:US", "side": "BUY", "quantity": 10, "limit_price": 10}], "missing_fields": []})
        strategy = manager.confirm_draft(draft.id)[0]
        strategy_id = strategy.id
        db.commit()
    broker = Broker(factory)
    with factory() as db:
        review = OrderManager(db, broker).create_order_review(strategy_id,
            {"quantity": 10, "limit_price": 10}, risk_snapshot={"requires_confirmation": False})
        db.commit()
    params = {"quantity": 10, "limit_price": 10, "review_id": review}
    yield factory, broker, strategy_id, params
    engine.dispose()


@pytest.mark.parametrize("failure", ["crash", "timeout"])
def test_accepted_then_lost_reply_survives_restart_without_resend(rig, failure):
    factory, broker, strategy, params = rig
    broker.mode = failure
    with factory() as db:
        if failure == "crash":
            with pytest.raises(ProcessCrash):
                OrderManager(db, broker).place_order(strategy, params)
        else:
            assert OrderManager(db, broker).place_order(strategy, params).status == "unknown"
    # New manager/session represents a restarted local process. Durable orderRef
    # is the exact broker ref; identical retry reads the first claim only.
    with factory() as db:
        manager = OrderManager(db, broker)
        order = manager.place_order(strategy, params)
        assert order.broker_order_id is None and order.status == "unknown"
        order = manager.sync_order_status(order.id)
        db.commit()
        assert order.status == "filled"
        assert manager.get_strategy(strategy).cumulative_filled_quantity == 10
    assert len(broker.requests) == 1


def test_concurrent_and_repeated_claim_is_one_send_and_one_ref(rig):
    factory, broker, strategy, params = rig
    barrier = threading.Barrier(2)
    def invoke():
        with factory() as db:
            barrier.wait()
            return OrderManager(db, broker).place_order(strategy, params).id
    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: invoke(), range(2)))
    assert ids[0] == ids[1] == broker.requests[0].local_order_id
    assert len(broker.requests) == 1
    with factory() as db:
        assert OrderManager(db, broker).place_order(strategy, params).id == ids[0]
        with pytest.raises(ValueError, match="CONFLICT"):
            OrderManager(db, broker).place_order(strategy, {**params, "quantity": 9})
        assert db.query(OrderRecord).count() == 1


def test_commit_failure_grants_no_dispatch(rig):
    factory, broker, strategy, params = rig
    engine = factory.kw["bind"]
    def fail_commit(connection):
        raise RuntimeError("commit unavailable")
    event.listen(engine, "commit", fail_commit)
    try:
        with factory() as db, pytest.raises(RuntimeError, match="commit unavailable"):
            OrderManager(db, broker).place_order(strategy, params)
    finally:
        event.remove(engine, "commit", fail_commit)
    assert not broker.requests
    with factory() as db:
        # A commit error may be ambiguous; any durable row remains UNKNOWN and
        # cannot dispatch on replay, regardless of the driver's commit outcome.
        records = db.query(OrderRecord).all()
        assert all(row.status == "unknown" for row in records)
        if records:
            assert OrderManager(db, broker).place_order(strategy, params).id == records[0].id
            assert not broker.requests


def test_no_id_unknown_and_submitted_are_not_cancelled_or_resent(rig):
    factory, broker, strategy, _ = rig
    with factory() as db:
        db.add(OrderRecord(id="unresolved", strategy_id=strategy, symbol="AAPL:US", side="BUY",
                           quantity=10, status="submitted_to_broker", broker_name="ibkr"))
        db.commit()
    scan_orphan_orders(factory, lambda: broker, lambda _: broker)
    with factory() as db:
        manager = OrderManager(db, broker)
        assert manager.get_order("unresolved").status == "unknown"
        assert manager.cancel_order("unresolved").status == "unknown"
        db.commit()
    assert not broker.requests


@pytest.mark.parametrize("initial,expected", [("filled", 10), ("partial", 4)])
def test_initial_ack_recomputes_strategy_and_duplicate_stale_cannot_erase(rig, initial, expected):
    factory, broker, strategy, params = rig
    broker.mode = initial
    with factory() as db:
        manager = OrderManager(db, broker)
        order = manager.place_order(strategy, params)
        assert order.filled_quantity == expected
        assert manager.get_strategy(strategy).cumulative_filled_quantity == expected
        first_time = order.filled_at
        for status, qty in [(order.status, expected), ("broker_pending", 0), ("unknown", 0)]:
            manager.apply_broker_observation(order.id, OrderStatusUpdate(order.broker_order_id, order.id, status, filled_quantity=qty))
        db.commit()
        assert order.filled_quantity == expected and order.avg_filled_price == 10
        assert manager.get_strategy(strategy).cumulative_filled_quantity == expected
        assert order.filled_at == first_time


def test_late_fill_after_cancel_and_correction_are_evidence_constrained(rig):
    factory, broker, strategy, params = rig
    with factory() as db:
        manager = OrderManager(db, broker)
        order = manager.place_order(strategy, params)
        manager.apply_broker_observation(order.id, OrderStatusUpdate(order.broker_order_id, order.id, "cancelled"))
        manager.apply_broker_observation(order.id, broker.observation)
        assert order.status == "filled" and manager.get_strategy(strategy).status == "completed"
        manager.apply_broker_observation(order.id, OrderStatusUpdate(order.broker_order_id, order.id, "unknown",
            filled_quantity=5, raw_response={"execution_correction": True}))
        assert order.status == "unknown" and order.filled_quantity == 10
        assert manager.get_strategy(strategy).status == "paused"
        db.commit()


def test_missing_owner_stale_epoch_and_new_nonce_cannot_bypass_unknown(rig):
    factory, broker, strategy, params = rig
    broker.mode = "timeout"
    with factory() as db:
        manager = OrderManager(db, broker)
        manager.place_order(strategy, params)
        review = manager.create_order_review(strategy, params, risk_snapshot={"requires_confirmation": False})
        db.commit()
        with pytest.raises(ValueError, match="RECONCILIATION_REQUIRED"):
            manager.place_order(strategy, {**params, "review_id": review})
        db.info["portfolio_id"] = 12
        with pytest.raises(ValueError, match="RECONCILIATION_REQUIRED|CHANGED"):
            manager.place_order(strategy, params)
    assert len(broker.requests) == 1


def test_legacy_unlinked_strategy_requires_manual_reconciliation(rig):
    factory, broker, _, _ = rig
    with factory() as db:
        legacy = SymbolStrategy(symbol="AAPL:US", side="BUY", target_quantity=10, limit_price=10, status="active")
        db.add(legacy); db.commit()
        with pytest.raises(ValueError, match="LEGACY_STRATEGY"):
            OrderManager(db, broker).create_order_review(legacy.id, {"quantity": 10}, risk_snapshot={})
        assert not broker.requests


@pytest.mark.parametrize("script", ["scripts/e2e_ibkr_live_smoke.py", "scripts/e2e_ibkr_paper.py",
    "backend/scripts/v3_4/m1_3a_hk_validation.py", "backend/scripts/v3_4/m1_3b_us_validation.py",
    "backend/scripts/v3_4/m4_e2e_validation.py", "backend/scripts/v3_4/tiger_explore.py"])
def test_legacy_mutation_cli_stops_before_importing_credentials_or_connecting(script):
    import subprocess
    import sys
    result = subprocess.run([sys.executable, script], env={"PYTHON_DOTENV_DISABLED": "1"},
                            capture_output=True, text=True, timeout=5)
    assert result.returncode != 0
    assert "Legacy trading validation is disabled" in result.stderr
    assert "Traceback" not in result.stderr


def test_broker_price_precision_matches_existing_numeric_scale_without_false_correction(rig):
    factory, broker, strategy, params = rig
    with factory() as db:
        manager = OrderManager(db, broker)
        order = manager.place_order(strategy, params)
        update = OrderStatusUpdate(order.broker_order_id, order.id, "partially_filled", filled_quantity=4,
                                   avg_filled_price=Decimal("10.123456789"))
        manager.apply_broker_observation(order.id, update); db.commit()
        order_id = order.id
    with factory() as db:
        manager = OrderManager(db, broker)
        manager.apply_broker_observation(order_id, update)
        order = manager.get_order(order_id)
        assert order.status == "partially_filled"
        assert not json.loads(order.raw_broker_response).get("reconciliation_disputed")


def test_cancel_owner_mismatch_is_blocked_before_broker_mutation(rig):
    factory, broker, strategy, params = rig
    with factory() as db:
        manager = OrderManager(db, broker)
        order = manager.place_order(strategy, params)
        broker._account_id = 'DIFFERENT-ACCOUNT'
        with pytest.raises(ValueError, match='OWNER_MISMATCH'):
            manager.cancel_order(order.id)


@pytest.mark.parametrize("status,quantity", [("partially_filled", 4), ("unknown", 0), ("cancelled", 0)])
def test_r01_old_connection_observation_cannot_erase_committed_fill(rig, status, quantity):
    factory, broker, strategy_id, params = rig
    with factory() as db:
        order_id = OrderManager(db, broker).place_order(strategy_id, params).id
    with factory() as older, factory() as newer:
        old_manager = OrderManager(older, broker)
        cached = old_manager.get_order(order_id)  # strong reference across broker I/O
        assert cached.filled_quantity == 0
        new_manager = OrderManager(newer, broker)
        new_manager.apply_broker_observation(order_id, OrderStatusUpdate(
            "broker-perm-10", order_id, "filled", filled_quantity=10, avg_filled_price=Decimal("10")))
        newer.commit()
        old_manager.apply_broker_observation(order_id, OrderStatusUpdate(
            "broker-perm-10", order_id, status, filled_quantity=quantity))
        older.commit()
    with factory() as db:
        manager = OrderManager(db, broker)
        order = manager.get_order(order_id)
        strategy = manager.get_strategy(strategy_id)
        assert order.status == "filled" and order.filled_quantity == 10
        assert strategy.status == "completed" and strategy.cumulative_filled_quantity == 10


def test_r03_new_review_after_prior_fill_must_recheck_risk_and_acknowledge(rig, monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.models import Position
    from app.fx_service import fx_service
    from backend.api import action
    from backend.services.action.risk_engine import portfolio_order_risk, CONFIRMATION_TEXT
    factory, broker, _, _ = rig
    monkeypatch.setattr(fx_service, "_cache", {("USD", "CNY", "latest"): (7, "2026-10-17")})
    with factory() as db:
        db.add(Position(portfolio_id=11, name="Synthetic CNY balance", platform="Synthetic",
            ticker="CASH-CNY", symbol="CASH-CNY:SH", segment="投资", asset_class="货币",
            currency="CNY", quantity=20000, cost_price=1, market_value_cny=20000))
        manager = OrderManager(db, broker)
        draft = manager.create_draft("synthetic-risk", {"symbol_strategies": [
            {"symbol": "AAPL:US", "side": "BUY", "quantity": 20, "limit_price": 10}], "missing_fields": []})
        sid = manager.confirm_draft(draft.id)[0].id
        db.commit()
        risk = portfolio_order_risk(db, portfolio_id=11, symbol="AAPL:US", side="BUY", quantity=10, limit_price=Decimal("10"))
        assert not risk["requires_confirmation"]
        params = {"quantity": 10, "limit_price": 10}
        first_review = manager.create_order_review(sid, params, risk_snapshot=risk)
        db.commit()
        broker.mode = "filled"
        first = manager.place_order(sid, {**params, "review_id": first_review})
        first_id = first.id
        second_review = manager.create_order_review(sid, params, risk_snapshot=risk)
        db.commit()
    def manager_for_api():
        db = factory()
        return OrderManager(db, broker), db
    monkeypatch.setattr(action, "_get_manager", manager_for_api)
    request = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 12345)})
    fx_service._cache[("USD", "CNY", "latest")] = (7, "fallback")
    with pytest.raises(HTTPException) as failure:
        action.place_order(sid, {**params, "review_id": second_review}, request)
    assert failure.value.detail == "RISK_REVIEW_CHANGED" and len(broker.requests) == 1
    # Exact old claim remains a read, even after risk changed or review expired.
    replay = action.place_order(sid, {**params, "review_id": first_review}, request)
    assert replay["id"] == first_id and len(broker.requests) == 1
    reviewed = action.check_risk(sid, params, request)
    assert reviewed["requires_confirmation"]
    with pytest.raises(HTTPException) as missing_ack:
        action.place_order(sid, {**params, "review_id": reviewed["review_id"]}, request)
    assert missing_ack.value.detail == "RISK_CONFIRMATION_REQUIRED" and len(broker.requests) == 1
    result = action.place_order(sid, {**params, "review_id": reviewed["review_id"],
                                     "confirmation_text": CONFIRMATION_TEXT}, request)
    assert result["status"] == "filled" and len(broker.requests) == 2


@pytest.mark.parametrize("changed", [{"order_type": "MARKET"}, {"stop_price": 8},
                                     {"time_in_force": "DAY"}, {"resolved_contract": {"con_id": 1}}])
def test_r03_existing_claim_requires_all_immutable_parameters(rig, changed):
    factory, broker, strategy, params = rig
    with factory() as db:
        manager = OrderManager(db, broker)
        manager.place_order(strategy, params)
        with pytest.raises(ValueError, match="IDEMPOTENCY_PARAMETER_CONFLICT"):
            manager.place_order(strategy, {**params, **changed})
    assert len(broker.requests) == 1
