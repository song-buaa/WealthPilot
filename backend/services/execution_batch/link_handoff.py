"""Thin Web-only handoff into existing typed-intent and ExecutionBatch services."""
import json
from contextlib import contextmanager
from datetime import timezone

from app.models import Conversation, ConversationMessage
from backend.services.action.models import ExecutionBatch
from backend.services.trade_intent.models import StructuredTradeIntent, TradeIntentField
from backend.services.trade_intent.persistence import confirm_trade_intent
from backend.services.execution_batch.service import ExecutionBatchService
from backend.services.execution_batch.calculator import ExecutionSafetyError
from pocs.chatgpt_skills.trade_link import IBKRLinkPreviewRequest


def _utc(value):
    # SQLite stores naive UTC; browser ISO values may carry an explicit offset.
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def link_intent(order: IBKRLinkPreviewRequest) -> StructuredTradeIntent:
    def field(value, provenance="USER_EXPLICIT"):
        return TradeIntentField(value=value, provenance=provenance,
                                source_text=str(value), status="RESOLVED")
    return StructuredTradeIntent(
        intent_id="ti_"+order.request_id.hex, broker=field("IBKR"),
        account=field("现有 IBKR 配置账户（请在批次预览核对）", "AI_INFERRED"),
        funding_source=field("CASH", "AI_INFERRED"), funding_currency=field("USD"),
        budget_mode=field("ALL_IN_TOTAL"), stated_cash=field(str(order.target_amount)),
        venue=field("LSE", "AI_INFERRED"), trading_currency=field("USD"), share_class=field("ACC", "AI_INFERRED"),
        side=field(order.side), order_type=field(order.order_type),
        legs=[{"sequence": 1, "alias": field("IB01"),
               "allocation_mode": field("APPROX_AMOUNT", "AI_INFERRED"),
               "target_amount": field(str(order.target_amount)),
               "venue_override": field("LSE", "AI_INFERRED"), "trading_currency_override": field("USD"),
               "share_class_override": field("ACC", "AI_INFERRED")}],
        readiness="READY_FOR_CONFIRMATION", confirmation_status="PENDING",
    )


def create_link_batch(session, adapter, order: IBKRLinkPreviewRequest, *, limit_price=None,
                      reviewed_at=None, review_hash=None):
    """Called only by the explicit Web intent-confirm button; never submits."""
    request_id = str(order.request_id)
    parameters = order.model_dump(mode="json")
    conversation = session.get(Conversation, request_id)
    if conversation is None:
        conversation = Conversation(id=request_id, title="IBKR IB01 增持意图")
        session.add(conversation)
        session.add(ConversationMessage(conversation_id=request_id, role="user",
                                       content=json.dumps(parameters, ensure_ascii=False)))
        message = ConversationMessage(
            conversation_id=request_id, role="assistant", content="Web 核对的 IB01 交易意图；尚未授权提交订单。",
            metadata_json=json.dumps({"trade_link_order": parameters,
                                      "trade_intent": link_intent(order).model_dump(mode="json")}, ensure_ascii=False),
        )
        session.add(message)
        session.flush()
    else:
        message = session.query(ConversationMessage).filter_by(
            conversation_id=request_id, role="assistant").first()
        if message is None or json.loads(message.metadata_json).get("trade_link_order") != parameters:
            raise ValueError("TRADE_LINK_REQUEST_ID_PARAMETER_MISMATCH")
    # A known request cannot be adopted by a different Portfolio or broker
    # account. Check before meaning-confirmation commits, not only at submit.
    from backend.services.action.ownership import execution_owner
    prior = session.query(ExecutionBatch).filter_by(source_message_id=message.id).order_by(
        ExecutionBatch.created_at.desc()).first()
    if prior is not None:
        owner = execution_owner(session, adapter)
        policy = json.loads(prior.execution_policy or '{}')
        if policy.get('portfolio_id') != owner.portfolio_id or prior.account_ref != owner.account_id:
            raise ValueError('TRADE_LINK_OWNER_MISMATCH')
    confirm_trade_intent(session, conversation_id=request_id, message_id=message.id,
                         intent_id="ti_"+order.request_id.hex)
    # confirm_trade_intent commits the original meaning-only confirmation.
    # Serialize retries before consulting/creating the original batch.
    # Meaning-only confirmation is committed; release reads before broker I/O.
    session.rollback()
    existing = session.query(ExecutionBatch).filter_by(source_message_id=message.id).order_by(
        ExecutionBatch.created_at.desc()).first()
    if existing is not None:
        if (reviewed_at is not None and existing.status == "CANCELLED"
                and json.loads(existing.execution_policy or "{}").get("review_refresh_pending")
                and not any(leg.linked_order_id for leg in existing.legs)):
            # A failed refresh invalidated the old confirmation durably. A new
            # explicit fresh review may retry; explicit user cancellations cannot.
            next_batch = ExecutionBatchService(session, adapter).create_batch(
                conversation_id=request_id, message_id=message.id,
                _manual_limits={"IB01": limit_price} if limit_price is not None else None)
            next_batch.confirmation_version = existing.confirmation_version
            return next_batch
        # A link replay must stay on the same request, but a newly reviewed quote
        # must not get trapped behind its old persisted review. Submitted/terminal
        # batches are never resurrected. Identical retries reuse the fresh batch.
        if (reviewed_at is not None and existing.status not in {"CANCELLED", "COMPLETED"}
                and not any(leg.linked_order_id for leg in existing.legs)):
            service = ExecutionBatchService(session, adapter)
            try:
                service.assert_review_fresh(existing)
                fresh = all(
                    _utc(leg.quote_as_of) >= _utc(reviewed_at)
                    for leg in existing.legs
                )
                same_review = service.review_fingerprint(existing) == review_hash
            except ExecutionSafetyError:
                fresh = same_review = False
            if not fresh or not same_review:
                return service.reprice_all_in(
                    existing.id, {"IB01": limit_price} if limit_price is not None else None,
                    existing.confirmation_version,
                )
        return existing
    return ExecutionBatchService(session, adapter).create_batch(
        conversation_id=request_id, message_id=message.id,
        _manual_limits={"IB01": limit_price} if limit_price is not None else None)


@contextmanager
def preview_link_batch(adapter, order, *, portfolio_id, limit_price=None):
    """Use the original engine on disposable in-memory tables, never production DB.

    Only original read-only IBKR facts/WhatIf are requested; no strategy or order
    is created. The result is consumed before the temporary session is disposed.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            from app.models import Portfolio
            pid = portfolio_id
            session.info["portfolio_id"] = pid
            session.add(Portfolio(id=pid, name="Disposable preview owner"))
            session.commit()
            batch = create_link_batch(session, adapter, order, limit_price=limit_price)
            yield batch
            session.rollback()
    finally:
        engine.dispose()
