import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Conversation, ConversationMessage
from backend.services.action.brokers.base import OrderStatusUpdate
from backend.services.action.models import ExecutionBatch, ExecutionLeg, OrderRecord
from backend.services.execution_batch.calculator import ExecutionSafetyError, money
from backend.services.execution_batch.service import ExecutionBatchService
from backend.services.execution_batch.trusted_instruments import (
    EXPECTED_MARKET_RULE_IDS,
    TRUSTED_INSTRUMENTS,
)


NOW = datetime(2026, 8, 15, 8, 0, tzinfo=timezone.utc)


def field(value, provenance="USER_EXPLICIT"):
    return {
        "value": value, "provenance": provenance,
        "source_text": str(value) if value is not None else None,
        "status": "RESOLVED",
    }


def canonical_intent():
    legs = []
    for sequence, (alias, mode, amount) in enumerate([
        ("IBTA", "APPROX_AMOUNT", 11350),
        ("VDCA", "APPROX_AMOUNT", 2850),
        ("CBU0", "APPROX_AMOUNT", 1400),
        ("IB01", "REMAINDER", None),
    ], start=1):
        legs.append({
            "sequence": sequence,
            "alias": field(alias),
            "allocation_mode": field(mode, "AI_INFERRED"),
            "target_amount": field(amount),
            "venue_override": field("LSE"),
            "trading_currency_override": field("USD"),
            "share_class_override": field("ACC"),
        })
    return {
        "schema_version": "v3.15-phase1", "intent_id": "ti-case1",
        "candidate": True, "broker": field("IBKR"),
        "account": field(None, "NOT_PROVIDED"),
        "funding_source": field("CASH"), "funding_currency": field("USD"),
        "budget_mode": field("ALL_AVAILABLE_CASH"),
        "stated_cash": field({"amount": 16632, "currency": "USD"}),
        "venue": field("LSE"), "trading_currency": field("USD"),
        "share_class": field("ACC"), "side": field("BUY"),
        "order_type": field("LIMIT"), "legs": legs, "issues": [],
        "readiness": "READY_FOR_CONFIRMATION", "confirmation_status": "CONFIRMED",
        "confirmed_at": NOW.isoformat(), "phase_boundary": "TYPED_INTENT_ONLY",
    }


class FakeIBKRExecutionAdapter:
    broker_name = "ibkr"

    def __init__(self, *, order_statuses=None, open_orders=None, what_if_error=False):
        self._account_id = "U-FAKE"
        self.order_statuses = list(order_statuses or ["submitted_to_broker"] * 4)
        self.open_orders = list(open_orders or [])
        self.what_if_error = what_if_error
        self.place_calls = []
        self.reconcile_result = None

    def authenticate(self, _credentials):
        return True

    def get_cash_snapshot(self, _currency):
        return {
            "currency": "USD", "account_masked": "***FAKE",
            "as_of": NOW.isoformat(), "CashBalance": 16632,
            "SettledCash": 16632, "TotalCashValue": 16632,
            "AvailableFunds": 50000, "BuyingPower": 100000,
        }

    def list_open_order_details(self):
        return list(self.open_orders)

    def resolve_lse_usd_etf(self, alias):
        result = TRUSTED_INSTRUMENTS[alias].to_dict()
        result.update({
            "candidate_count": 1,
            "market_rule_id": EXPECTED_MARKET_RULE_IDS[alias],
            "min_tick": 0.01,
            "market_rule": [{"low_edge": 0, "increment": 0.01}],
            "liquid_hours": "20260815:0000-20260815:2359",
            "trading_hours": "20260815:0000-20260815:2359",
            "time_zone_id": "UTC",
        })
        return result

    def get_executable_quote(self, resolved):
        asks = {"IBTA": 5, "VDCA": 61, "CSBGU0": 5, "IB01": 126}
        ask = asks[resolved["symbol"]]
        return {
            "bid": ask - 0.01, "ask": ask, "last": ask,
            "quote_quality": "LIVE", "market_data_type": 1,
            "quote_timestamp": NOW.isoformat(), "source": "FAKE_IBKR",
        }

    def what_if_limit_order(self, _resolved, *, quantity, limit_price):
        if self.what_if_error:
            raise ConnectionError("Gateway Read-Only")
        return {
            "status": "PASS", "commission": 1,
            "commission_currency": "USD", "what_if": True,
            "transmit": False, "quantity": quantity,
            "limit_price": str(limit_price),
        }

    def is_market_open(self, _resolved, *, now=None):
        return True

    def place_order(self, request):
        self.place_calls.append(request)
        status = self.order_statuses.pop(0)
        if status == "timeout":
            raise TimeoutError("simulated timeout")
        return OrderStatusUpdate(
            broker_order_id=f"broker-{len(self.place_calls)}",
            local_order_id=request.local_order_id,
            status=status, timestamp=1,
            raw_response={
                "currency": "USD", "quantity": request.quantity,
                "limit_price": float(request.limit_price),
            },
        )

    def find_order_by_ref(self, _order_ref):
        return self.reconcile_result


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    conversation = Conversation(id="case1-conversation", title="Case 1")
    db.add(conversation)
    db.add(ConversationMessage(
        id=1, conversation_id=conversation.id, role="assistant", content="analysis",
        metadata_json=json.dumps({"trade_intent": canonical_intent()}),
    ))
    db.commit()
    yield db
    db.close()


def make_ready_batch(session, adapter=None):
    adapter = adapter or FakeIBKRExecutionAdapter()
    service = ExecutionBatchService(
        session, adapter, clock=lambda: NOW, allow_mutation=True,
    )
    batch = service.create_batch(conversation_id="case1-conversation", message_id=1)
    assert batch.status == "READY"
    assert [leg.user_alias for leg in batch.legs] == ["IBTA", "VDCA", "CBU0", "IB01"]
    return service, adapter, batch


def test_create_batch_has_contract_quote_cash_whatif_and_dynamic_remainder(session):
    _service, _adapter, batch = make_ready_batch(session)
    assert batch.usable_cash == Decimal("16632")
    assert batch.safety_cushion == Decimal("25")
    assert batch.legs[2].symbol == "CSBGU0"
    assert batch.legs[2].local_symbol == "CBU0"
    assert all(leg.share_class_verification == "VERIFIED" for leg in batch.legs)
    assert all(json.loads(leg.what_if_snapshot)["transmit"] is False for leg in batch.legs)
    assert batch.legs[-1].allocation_mode == "REMAINDER"
    assert batch.estimated_total + batch.estimated_fees + batch.safety_cushion <= batch.usable_cash


def test_external_buy_order_blocks_batch_generation(session):
    adapter = FakeIBKRExecutionAdapter(open_orders=[{
        "side": "BUY", "remaining_quantity": 10, "order_ref": "outside",
    }])
    service = ExecutionBatchService(session, adapter, clock=lambda: NOW)
    with pytest.raises(ExecutionSafetyError, match="外部 BUY"):
        service.create_batch(conversation_id="case1-conversation", message_id=1)


def test_readonly_whatif_failure_is_recorded_and_not_ready(session):
    adapter = FakeIBKRExecutionAdapter(what_if_error=True)
    service = ExecutionBatchService(session, adapter, clock=lambda: NOW)
    batch = service.create_batch(conversation_id="case1-conversation", message_id=1)
    assert batch.status == "DRAFT"
    assert "WhatIf" in batch.attention_reason
    assert all(
        json.loads(leg.what_if_snapshot)["status"] == "PENDING_LIVE_ENABLE"
        for leg in batch.legs
    )


def test_missing_quote_can_use_user_manual_tick_validated_limits(session):
    adapter = FakeIBKRExecutionAdapter()
    adapter.get_executable_quote = lambda _resolved: {
        "bid": None, "ask": None, "last": None,
        "quote_quality": "MISSING", "market_data_type": 1,
        "quote_timestamp": NOW.isoformat(), "source": "FAKE_IBKR",
    }
    service = ExecutionBatchService(session, adapter, clock=lambda: NOW)
    batch = service.create_batch(conversation_id="case1-conversation", message_id=1)
    assert batch.status == "DRAFT"
    service.apply_manual_limits(batch.id, {
        "IBTA": 5, "VDCA": 61, "CBU0": 5, "IB01": 126,
    })
    assert batch.status == "READY"
    assert all(leg.limit_source == "USER_MANUAL_CONFIRMED" for leg in batch.legs)
    assert all(leg.what_if_snapshot for leg in batch.legs)


def test_manual_limit_must_be_exact_market_rule_tick(session):
    service, _adapter, batch = make_ready_batch(session)
    with pytest.raises(ExecutionSafetyError, match="MarketRule tick"):
        service.apply_manual_limits(batch.id, {
            "IBTA": 5.005, "VDCA": 61, "CBU0": 5, "IB01": 126,
        })


def test_confirmation_hash_and_sequential_submission_are_server_authoritative(session):
    service, adapter, batch = make_ready_batch(session)
    service.confirm_batch(batch.id)
    first = batch.legs[0]
    order = service.submit_next_leg(
        batch.id, confirmation_version=1, leg_id=first.id,
    )
    assert order.batch_id == batch.id
    assert order.batch_leg_id == first.id
    assert adapter.place_calls[0].resolved_contract["con_id"] == 272686955
    with pytest.raises(ExecutionSafetyError, match="顺序"):
        service.submit_next_leg(
            batch.id, confirmation_version=1, leg_id=batch.legs[2].id,
        )


def test_duplicate_leg_submit_returns_same_order_without_second_broker_call(session):
    service, adapter, batch = make_ready_batch(session)
    service.confirm_batch(batch.id)
    leg_id = batch.legs[0].id
    first = service.submit_next_leg(batch.id, confirmation_version=1, leg_id=leg_id)
    repeated = service.submit_next_leg(batch.id, confirmation_version=1, leg_id=leg_id)
    assert repeated.id == first.id
    assert len(adapter.place_calls) == 1


def test_timeout_hard_stops_and_reconcile_found_recovers_without_new_order(session):
    adapter = FakeIBKRExecutionAdapter(order_statuses=["timeout"])
    service, adapter, batch = make_ready_batch(session, adapter)
    service.confirm_batch(batch.id)
    leg = batch.legs[0]
    order = service.submit_next_leg(batch.id, confirmation_version=1, leg_id=leg.id)
    assert order.status == "unknown"
    assert batch.status == "ATTENTION_REQUIRED"
    adapter.reconcile_result = OrderStatusUpdate(
        broker_order_id="broker-found", local_order_id=order.id,
        status="broker_pending", timestamp=2, raw_response={"found": True},
    )
    service.reconcile_leg(batch.id, leg.id)
    assert leg.status == "OPEN"
    assert len(adapter.place_calls) == 1


def test_rejected_skip_cannot_flow_fixed_target_to_remainder(session):
    adapter = FakeIBKRExecutionAdapter(order_statuses=["rejected"])
    service, _adapter, batch = make_ready_batch(session, adapter)
    service.confirm_batch(batch.id)
    leg = batch.legs[0]
    service.submit_next_leg(batch.id, confirmation_version=1, leg_id=leg.id)
    assert leg.status == "REJECTED"
    service.skip_rejected_leg(batch.id, leg.id)
    assert batch.status == "DRAFT"
    assert batch.confirmation_hash is None
    assert leg.released_intent_amount == Decimal("11350")


def test_all_four_legs_submit_in_original_order_and_remainder_last(session):
    service, adapter, batch = make_ready_batch(session)
    service.confirm_batch(batch.id)
    order_ids = []
    for leg in list(batch.legs):
        order = service.submit_next_leg(
            batch.id, confirmation_version=1, leg_id=leg.id,
        )
        order_ids.append(order.id)
    assert [request.symbol for request in adapter.place_calls] == [
        "IBTA:LSE", "VDCA:LSE", "CBU0:LSE", "IB01:LSE",
    ]
    assert len(set(order_ids)) == 4
    assert batch.status == "SUBMITTED"


def test_open_and_partial_fill_are_advanceable_but_keep_reservation(session):
    service, adapter, batch = make_ready_batch(session)
    service.confirm_batch(batch.id)
    first = batch.legs[0]
    order = service.submit_next_leg(batch.id, confirmation_version=1, leg_id=first.id)
    adapter.reconcile_result = OrderStatusUpdate(
        broker_order_id=order.broker_order_id, local_order_id=order.id,
        status="partially_filled", filled_quantity=100,
        avg_filled_price=Decimal("4.99"), timestamp=2,
        raw_response={"partial": True},
    )
    service.reconcile_leg(batch.id, first.id)
    assert first.status == "PARTIAL_FILLED"
    ledger = service._build_ledger(batch)
    assert ledger.filled_cost == Decimal("499")
    assert ledger.active_reservations > 0
    second = batch.legs[1]
    service.submit_next_leg(batch.id, confirmation_version=1, leg_id=second.id)
    assert len(adapter.place_calls) == 2


def test_dynamic_remainder_respects_committed_fixed_legs_and_cushion(session):
    service, _adapter, batch = make_ready_batch(session)
    service.confirm_batch(batch.id)
    for leg in batch.legs[:3]:
        service.submit_next_leg(batch.id, confirmation_version=1, leg_id=leg.id)
    remainder = batch.legs[3]
    order = service.submit_next_leg(
        batch.id, confirmation_version=1, leg_id=remainder.id,
    )
    ledger_after = service._build_ledger(batch)
    assert order.quantity == remainder.final_quantity
    assert money(order.quantity) * money(order.limit_price) <= (
        money(batch.usable_cash) - money(batch.safety_cushion)
    )
    assert ledger_after.remaining >= 0


def test_reconcile_not_found_remains_hard_stop(session):
    adapter = FakeIBKRExecutionAdapter(order_statuses=["timeout"])
    service, adapter, batch = make_ready_batch(session, adapter)
    service.confirm_batch(batch.id)
    leg = batch.legs[0]
    service.submit_next_leg(batch.id, confirmation_version=1, leg_id=leg.id)
    with pytest.raises(ExecutionSafetyError, match="NOT_FOUND"):
        service.reconcile_leg(batch.id, leg.id)
    assert batch.status == "ATTENTION_REQUIRED"
    assert len(adapter.place_calls) == 1


def test_stop_remaining_never_cancels_submitted_broker_order(session):
    service, adapter, batch = make_ready_batch(session)
    service.confirm_batch(batch.id)
    first = batch.legs[0]
    service.submit_next_leg(batch.id, confirmation_version=1, leg_id=first.id)
    service.stop_remaining(batch.id)
    assert first.status == "SUBMITTED"
    assert [leg.status for leg in batch.legs[1:]] == [
        "CANCELLED", "CANCELLED", "CANCELLED",
    ]
    assert len(adapter.place_calls) == 1


def test_live_guard_rejects_mutation_before_adapter_call(session):
    adapter = FakeIBKRExecutionAdapter()
    service = ExecutionBatchService(session, adapter, clock=lambda: NOW, allow_mutation=False)
    batch = service.create_batch(conversation_id="case1-conversation", message_id=1)
    service.allow_mutation = False
    batch.status = "READY"
    guarded = ExecutionBatchService(session, adapter, clock=lambda: NOW, allow_mutation=False)
    guarded.confirm_batch(batch.id)
    with pytest.raises(PermissionError, match="真实提交未启用"):
        guarded.submit_next_leg(batch.id, confirmation_version=1, leg_id=batch.legs[0].id)
    assert adapter.place_calls == []
