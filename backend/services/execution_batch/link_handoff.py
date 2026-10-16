"""Thin Web-only handoff into existing typed-intent and ExecutionBatch services."""
import json

from app.models import Conversation, ConversationMessage
from backend.services.action.models import ExecutionBatch
from backend.services.trade_intent.models import StructuredTradeIntent, TradeIntentField
from backend.services.trade_intent.persistence import confirm_trade_intent
from backend.services.execution_batch.service import ExecutionBatchService
from pocs.chatgpt_skills.trade_link import IBKRLinkPreviewRequest


def link_intent(order: IBKRLinkPreviewRequest) -> StructuredTradeIntent:
    def field(value, provenance="USER_EXPLICIT"):
        return TradeIntentField(value=value, provenance=provenance,
                                source_text=str(value), status="RESOLVED")
    return StructuredTradeIntent(
        intent_id="ti_"+order.request_id.hex, broker=field("IBKR"),
        account=field("现有 IBKR 配置账户（请在批次预览核对）", "AI_INFERRED"),
        funding_source=field("CASH", "AI_INFERRED"), funding_currency=field("USD"),
        budget_mode=field("FIXED_TOTAL"), stated_cash=field(str(order.target_amount)),
        venue=field("LSE", "AI_INFERRED"), trading_currency=field("USD"), share_class=field("ACC", "AI_INFERRED"),
        side=field(order.side), order_type=field(order.order_type),
        legs=[{"sequence": 1, "alias": field("IB01"),
               "allocation_mode": field("APPROX_AMOUNT", "AI_INFERRED"),
               "target_amount": field(str(order.target_amount)),
               "venue_override": field("LSE", "AI_INFERRED"), "trading_currency_override": field("USD"),
               "share_class_override": field("ACC", "AI_INFERRED")}],
        readiness="READY_FOR_CONFIRMATION", confirmation_status="PENDING",
    )


def create_link_batch(session, adapter, order: IBKRLinkPreviewRequest):
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
    confirm_trade_intent(session, conversation_id=request_id, message_id=message.id,
                         intent_id="ti_"+order.request_id.hex)
    # confirm_trade_intent commits the original meaning-only confirmation.
    # Serialize retries before consulting/creating the original batch.
    from sqlalchemy import text
    session.execute(text("BEGIN IMMEDIATE"))
    existing = session.query(ExecutionBatch).filter_by(source_message_id=message.id).order_by(
        ExecutionBatch.created_at.desc()).first()
    if existing is not None:
        return existing  # Includes terminal attempts: opening the same link is never a new order.
    return ExecutionBatchService(session, adapter).create_batch(
        conversation_id=request_id, message_id=message.id)
