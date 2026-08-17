"""v3.15 Case 1 ExecutionBatch API.

The only mutation endpoint requires a current confirmed version and explicit
live-order acknowledgement.  Environment and adapter guards remain final.
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException

from app.database import get_session
from backend.services.execution_batch.calculator import ExecutionSafetyError
from backend.services.execution_batch.service import ExecutionBatchService
from backend.utils.datetime_utils import utc_iso


router = APIRouter()
def live_confirmation_text(leg_count: int) -> str:
    return f"确认并提交 {leg_count} 笔 IBKR 实盘限价单"


LIVE_CONFIRMATION_TEXT = live_confirmation_text(4)


def _adapter():
    # Reuse the action singleton: one Gateway clientId, one dedicated loop.
    from backend.api.action import _get_adapter
    return _get_adapter()


def _service(session):
    return ExecutionBatchService(session, _adapter())


def _load_json(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


def _serialize_leg(leg) -> dict:
    return {
        "id": leg.id, "sequence": leg.sequence, "user_alias": leg.user_alias,
        "allocation_mode": leg.allocation_mode,
        "target_amount": float(leg.target_amount) if leg.target_amount is not None else None,
        "authorization_class": leg.authorization_class,
        "resolved_con_id": leg.resolved_con_id, "symbol": leg.symbol,
        "local_symbol": leg.local_symbol, "sec_type": leg.sec_type,
        "stock_type": leg.stock_type, "exchange": leg.exchange,
        "primary_exchange": leg.primary_exchange, "currency": leg.currency,
        "trading_class": leg.trading_class, "isin": leg.isin,
        "long_name": leg.long_name,
        "share_class_requirement": leg.share_class_requirement,
        "share_class_verification": leg.share_class_verification,
        "verification_source": leg.verification_source,
        "quote_bid": float(leg.quote_bid) if leg.quote_bid is not None else None,
        "quote_ask": float(leg.quote_ask) if leg.quote_ask is not None else None,
        "quote_last": float(leg.quote_last) if leg.quote_last is not None else None,
        "quote_as_of": utc_iso(leg.quote_as_of), "quote_quality": leg.quote_quality,
        "market_data_type": leg.market_data_type,
        "market_rule_id": leg.market_rule_id,
        "min_tick": float(leg.min_tick) if leg.min_tick is not None else None,
        "market_rule": _load_json(leg.market_rule),
        "reference_price": float(leg.reference_price) if leg.reference_price is not None else None,
        "suggested_limit": float(leg.suggested_limit) if leg.suggested_limit is not None else None,
        "final_limit": float(leg.final_limit) if leg.final_limit is not None else None,
        "limit_source": leg.limit_source,
        "manual_limit_confirmed_at": utc_iso(leg.manual_limit_confirmed_at),
        "market_open": bool(leg.market_open),
        "estimated_quantity": leg.estimated_quantity,
        "final_quantity": leg.final_quantity,
        "estimated_notional": (
            float(leg.estimated_notional) if leg.estimated_notional is not None else None
        ),
        "what_if": _load_json(leg.what_if_snapshot),
        "execution_variance_amount": float(leg.execution_variance_amount or 0),
        "released_intent_amount": float(leg.released_intent_amount or 0),
        "status": leg.status, "linked_strategy_id": leg.linked_strategy_id,
        "linked_order_id": leg.linked_order_id,
        "submission_attempted_at": utc_iso(leg.submission_attempted_at),
    }


def _serialize_batch(batch) -> dict:
    snapshot = _load_json(batch.authoritative_cash_snapshot) or {}
    return {
        "id": batch.id, "broker": batch.broker,
        "account_masked": snapshot.get("account_masked", "***"),
        "funding_currency": batch.funding_currency,
        "budget_mode": batch.budget_mode,
        "source_conversation_id": batch.source_conversation_id,
        "source_message_id": batch.source_message_id,
        "stated_cash": float(batch.stated_cash) if batch.stated_cash is not None else None,
        "authoritative_cash_snapshot": snapshot,
        "cash_accounting_model_version": batch.cash_accounting_model_version,
        "usable_cash": float(batch.usable_cash) if batch.usable_cash is not None else None,
        "safety_cushion": float(batch.safety_cushion),
        "estimated_fees": float(batch.estimated_fees),
        "reserved_amount": float(batch.reserved_amount),
        "estimated_total": float(batch.estimated_total),
        "estimated_residual": float(batch.estimated_residual),
        "status": batch.status,
        "confirmation_version": batch.confirmation_version,
        "confirmation_hash": batch.confirmation_hash,
        "execution_policy": _load_json(batch.execution_policy),
        "attention_reason": _load_json(batch.attention_reason) or batch.attention_reason,
        "created_at": utc_iso(batch.created_at), "updated_at": utc_iso(batch.updated_at),
        "confirmed_at": utc_iso(batch.confirmed_at),
        "legs": [_serialize_leg(leg) for leg in batch.legs],
    }


def _raise(exc: Exception):
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PermissionError):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, (ExecutionSafetyError, ValueError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/safety")
def safety_status():
    return {
        "ibkr_read_only_mode": os.getenv("IBKR_READ_ONLY_MODE", "true").lower() == "true",
        "live_trading_enabled": (
            os.getenv("ENABLE_IBKR_LIVE_TRADING", "false").lower() == "true"
        ),
        "broker_gateway_read_only": "VERIFY_IN_GATEWAY_UI",
        "mutation_endpoint": "HUMAN_UI_ONLY",
    }


@router.post("")
def create_batch(body: dict):
    session = get_session()
    try:
        batch = _service(session).create_batch(
            conversation_id=str(body.get("conversation_id") or ""),
            message_id=int(body.get("message_id") or 0),
        )
        session.commit()
        return _serialize_batch(batch)
    except Exception as exc:
        session.rollback()
        _raise(exc)
    finally:
        session.close()


@router.get("")
def list_batches():
    session = get_session()
    try:
        return {"items": [_serialize_batch(item) for item in _service(session).list_batches()]}
    finally:
        session.close()


@router.get("/{batch_id}")
def get_batch(batch_id: str):
    session = get_session()
    try:
        return _serialize_batch(_service(session).get_batch(batch_id))
    except Exception as exc:
        _raise(exc)
    finally:
        session.close()


@router.post("/{batch_id}/refresh")
def refresh_batch(batch_id: str):
    session = get_session()
    try:
        batch = _service(session).refresh_batch(batch_id)
        session.commit()
        return _serialize_batch(batch)
    except Exception as exc:
        session.rollback()
        _raise(exc)
    finally:
        session.close()


@router.post("/{batch_id}/confirm")
def confirm_batch(batch_id: str):
    session = get_session()
    try:
        batch = _service(session).confirm_batch(batch_id)
        session.commit()
        return _serialize_batch(batch)
    except Exception as exc:
        session.rollback()
        _raise(exc)
    finally:
        session.close()


@router.post("/{batch_id}/manual-limits")
def apply_manual_limits(batch_id: str, body: dict):
    session = get_session()
    try:
        limits = body.get("limits")
        if not isinstance(limits, dict):
            raise ExecutionSafetyError("limits 必须为 alias → price")
        batch = _service(session).apply_manual_limits(batch_id, limits)
        session.commit()
        return _serialize_batch(batch)
    except Exception as exc:
        session.rollback()
        _raise(exc)
    finally:
        session.close()


@router.post("/{batch_id}/legs/{leg_id}/submit")
def submit_leg(batch_id: str, leg_id: str, body: dict):
    if body.get("live_order_acknowledged") is not True:
        raise HTTPException(status_code=422, detail="必须确认这是 IBKR 实盘订单")
    session = get_session()
    try:
        service = _service(session)
        batch = service.get_batch(batch_id)
        if body.get("confirmation_text") != live_confirmation_text(len(batch.legs)):
            raise ExecutionSafetyError("实盘确认文字不匹配")
        order = service.submit_next_leg(
            batch_id,
            confirmation_version=int(body.get("confirmation_version") or 0),
            leg_id=leg_id,
        )
        session.commit()
        return {
            "order_id": order.id, "batch_id": order.batch_id,
            "leg_id": order.batch_leg_id, "status": order.status,
            "broker_order_id": order.broker_order_id,
        }
    except Exception as exc:
        session.rollback()
        _raise(exc)
    finally:
        session.close()


@router.post("/{batch_id}/legs/{leg_id}/reconcile")
def reconcile_leg(batch_id: str, leg_id: str):
    session = get_session()
    try:
        leg = _service(session).reconcile_leg(batch_id, leg_id)
        session.commit()
        return _serialize_leg(leg)
    except Exception as exc:
        session.rollback()
        _raise(exc)
    finally:
        session.close()


@router.post("/{batch_id}/legs/{leg_id}/skip")
def skip_leg(batch_id: str, leg_id: str):
    session = get_session()
    try:
        batch = _service(session).skip_rejected_leg(batch_id, leg_id)
        session.commit()
        return _serialize_batch(batch)
    except Exception as exc:
        session.rollback()
        _raise(exc)
    finally:
        session.close()


@router.post("/{batch_id}/stop-remaining")
def stop_remaining(batch_id: str):
    session = get_session()
    try:
        batch = _service(session).stop_remaining(batch_id)
        session.commit()
        return _serialize_batch(batch)
    except Exception as exc:
        session.rollback()
        _raise(exc)
    finally:
        session.close()


@router.post("/{batch_id}/retire-replaced-intent")
def retire_replaced_intent(batch_id: str):
    session = get_session()
    try:
        batch = _service(session).retire_replaced_intent(batch_id)
        session.commit()
        return _serialize_batch(batch)
    except Exception as exc:
        session.rollback()
        _raise(exc)
    finally:
        session.close()
