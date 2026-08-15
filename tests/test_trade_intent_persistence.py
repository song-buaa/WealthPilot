"""Phase 1 persistence and human-confirmation boundary tests."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.database import Base, _ensure_conversation_message_metadata_column
from app.models import Conversation, ConversationMessage
from backend.services.action.models import (
    ActionDraft,
    AllocationIntent,
    OrderRecord,
    SymbolStrategy,
)
from backend.services.execution_plan.models import ExecutionPlan, ExecutionTranche
from backend.services.trade_intent.models import (
    FieldProvenance,
    FieldResolutionStatus,
    IntentConfirmationStatus,
    IntentReadiness,
    StructuredTradeIntent,
    TradeIntentField,
    TradeIntentIssue,
    TradeIntentLeg,
)
from backend.services.trade_intent.persistence import (
    TradeIntentConfirmationBlockedError,
    confirm_trade_intent,
)


def field(value, *, status=FieldResolutionStatus.RESOLVED):
    return TradeIntentField(
        value=value,
        provenance=(
            FieldProvenance.NOT_PROVIDED
            if status == FieldResolutionStatus.MISSING
            else FieldProvenance.USER_EXPLICIT
        ),
        source_text=None,
        status=status,
    )


def ready_intent() -> StructuredTradeIntent:
    missing = field(None, status=FieldResolutionStatus.MISSING)
    return StructuredTradeIntent(
        broker=field("IBKR"),
        account=missing,
        funding_source=field("CASH"),
        funding_currency=field("USD"),
        budget_mode=field("ALL_AVAILABLE_CASH"),
        stated_cash=field({"amount": 16632.0, "currency": "USD"}),
        venue=field("LSE"),
        trading_currency=field("USD"),
        share_class=field("ACC"),
        side=field("BUY"),
        order_type=field("LIMIT"),
        legs=[
            TradeIntentLeg(
                sequence=1,
                alias=field("IBTA"),
                allocation_mode=field("REMAINDER"),
                target_amount=missing.model_copy(deep=True),
                venue_override=missing.model_copy(deep=True),
                trading_currency_override=missing.model_copy(deep=True),
                share_class_override=missing.model_copy(deep=True),
            ),
        ],
        issues=[
            TradeIntentIssue(
                code="account_deferred_to_phase2",
                field_path="account",
                status=FieldResolutionStatus.MISSING,
                message="账户选择与验证属于 Phase 2",
                blocking=False,
            ),
        ],
        readiness=IntentReadiness.READY_FOR_CONFIRMATION,
        confirmation_status=IntentConfirmationStatus.PENDING,
    )


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'intent.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def persist_assistant_intent(db_session, intent: StructuredTradeIntent) -> ConversationMessage:
    conversation = Conversation(id="conversation-phase1", title="Phase 1")
    message = ConversationMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="这里是原有决策回答。",
        metadata_json=json.dumps(
            {"trade_intent": intent.model_dump(mode="json")},
            ensure_ascii=False,
        ),
    )
    db_session.add_all([conversation, message])
    db_session.commit()
    db_session.refresh(message)
    return message


def test_confirm_only_updates_message_metadata_and_is_idempotent(db_session):
    intent = ready_intent()
    message = persist_assistant_intent(db_session, intent)

    confirmed = confirm_trade_intent(
        db_session,
        conversation_id="conversation-phase1",
        message_id=message.id,
        intent_id=intent.intent_id,
    )
    repeated = confirm_trade_intent(
        db_session,
        conversation_id="conversation-phase1",
        message_id=message.id,
        intent_id=intent.intent_id,
    )

    assert confirmed.confirmation_status == IntentConfirmationStatus.CONFIRMED
    assert confirmed.confirmed_at is not None
    assert repeated.confirmed_at == confirmed.confirmed_at

    db_session.refresh(message)
    stored = StructuredTradeIntent.model_validate(
        json.loads(message.metadata_json)["trade_intent"]
    )
    assert stored.confirmation_status == IntentConfirmationStatus.CONFIRMED

    # Phase 1 confirmation must not create any execution-side object.
    for model in (
        ActionDraft,
        AllocationIntent,
        SymbolStrategy,
        OrderRecord,
        ExecutionPlan,
        ExecutionTranche,
    ):
        assert db_session.query(model).count() == 0


def test_blocked_intent_cannot_be_confirmed(db_session):
    intent = ready_intent()
    intent.readiness = IntentReadiness.NEEDS_REVIEW
    intent.confirmation_status = IntentConfirmationStatus.BLOCKED
    message = persist_assistant_intent(db_session, intent)

    with pytest.raises(TradeIntentConfirmationBlockedError):
        confirm_trade_intent(
            db_session,
            conversation_id="conversation-phase1",
            message_id=message.id,
            intent_id=intent.intent_id,
        )


def test_existing_sqlite_database_gets_metadata_column_idempotently(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE conversation_messages ("
            "id INTEGER PRIMARY KEY, role VARCHAR NOT NULL, content TEXT NOT NULL)"
        ))

    _ensure_conversation_message_metadata_column(engine)
    _ensure_conversation_message_metadata_column(engine)

    columns = {column["name"] for column in inspect(engine).get_columns(
        "conversation_messages"
    )}
    assert "metadata_json" in columns
    engine.dispose()


def test_conversation_save_and_history_round_trip_metadata(db_session, monkeypatch):
    from app import database
    from backend.api.conversations import get_messages
    from backend.services import decision_service

    intent = ready_intent()
    monkeypatch.setattr(database, "get_session", lambda: db_session)
    monkeypatch.setattr(decision_service, "_update_conversation_title_async", lambda *_: None)

    message_id = decision_service.save_conversation_turn(
        "conversation-round-trip",
        "请按我的结构买入。",
        "已完成现有决策分析。",
        "PositionDecision",
        "IBTA",
        assistant_metadata={"trade_intent": intent.model_dump(mode="json")},
    )
    history = get_messages("conversation-round-trip")

    assistant = next(item for item in history if item["role"] == "assistant")
    assert assistant["id"] == message_id
    assert assistant["metadata"]["trade_intent"]["intent_id"] == intent.intent_id
    assert assistant["content"] == "已完成现有决策分析。"


def test_sse_trade_intent_event_is_optional_and_contains_persisted_message_id():
    from backend.services.decision_service_v3 import _trade_intent_event

    assert _trade_intent_event(None, None) is None

    event = _trade_intent_event(ready_intent(), 42)
    assert event.startswith("event: trade_intent\n")
    payload = json.loads(event.split("data: ", 1)[1])
    assert payload["message_id"] == 42
    assert payload["intent"]["phase_boundary"] == "TYPED_INTENT_ONLY"
