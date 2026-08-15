"""Conversation-message persistence for Phase 1 intent confirmation."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models import ConversationMessage

from .models import (
    IntentConfirmationStatus,
    IntentReadiness,
    StructuredTradeIntent,
)


class TradeIntentNotFoundError(LookupError):
    pass


class TradeIntentConfirmationBlockedError(ValueError):
    pass


def decode_message_metadata(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def confirm_trade_intent(
    session: Session,
    *,
    conversation_id: str,
    message_id: int,
    intent_id: str,
) -> StructuredTradeIntent:
    """Confirm the parsed meaning only; never create execution entities."""
    message = (
        session.query(ConversationMessage)
        .filter(
            ConversationMessage.id == message_id,
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.role == "assistant",
        )
        .first()
    )
    if message is None:
        raise TradeIntentNotFoundError("assistant message not found")

    metadata = decode_message_metadata(message.metadata_json)
    raw_intent = metadata.get("trade_intent")
    if not isinstance(raw_intent, dict):
        raise TradeIntentNotFoundError("trade intent not found on message")

    intent = StructuredTradeIntent.model_validate(raw_intent)
    if intent.intent_id != intent_id:
        raise TradeIntentNotFoundError("trade intent id mismatch")
    if intent.confirmation_status == IntentConfirmationStatus.CONFIRMED:
        return intent
    if intent.readiness != IntentReadiness.READY_FOR_CONFIRMATION:
        raise TradeIntentConfirmationBlockedError(
            "trade intent has unresolved, conflicting, or unsupported fields"
        )

    intent.confirmation_status = IntentConfirmationStatus.CONFIRMED
    intent.confirmed_at = datetime.now(timezone.utc)
    metadata["trade_intent"] = intent.model_dump(mode="json")
    message.metadata_json = json.dumps(metadata, ensure_ascii=False)
    session.commit()
    return intent
