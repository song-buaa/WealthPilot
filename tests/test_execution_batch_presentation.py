from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api.execution_batch import LIVE_CONFIRMATION_TEXT, submit_leg


ROOT = Path(__file__).resolve().parents[1]
PREVIEW = (ROOT / "frontend/src/components/TradeIntentPreview.tsx").read_text()
BATCH_CARD = (ROOT / "frontend/src/components/ExecutionBatchCard.tsx").read_text()


def test_confirmed_trade_intent_exposes_manual_generate_batch_cta():
    assert "intent.confirmation_status === 'CONFIRMED'" in PREVIEW
    assert "生成交易执行计划" in PREVIEW
    assert "executionBatchApi.create" in PREVIEW


def test_batch_review_contains_required_contract_and_cash_evidence():
    for field in [
        "resolved_con_id", "local_symbol", "exchange", "currency", "isin",
        "share_class_verification", "market_rule_id", "quote_as_of",
        "safety_cushion", "estimated_fees", "estimated_residual",
        "manualLimits", "market_open",
    ]:
        assert field in BATCH_CARD


def test_final_modal_uses_explicit_live_language_and_exact_button_copy():
    assert "这是 IBKR 实盘订单" in BATCH_CARD
    assert LIVE_CONFIRMATION_TEXT in BATCH_CARD
    assert "IBTA → VDCA → CBU0 → IB01" in BATCH_CARD


def test_submit_endpoint_rejects_missing_human_ack_before_service_access():
    with pytest.raises(HTTPException) as exc:
        submit_leg("batch", "leg", {
            "live_order_acknowledged": False,
            "confirmation_version": 1,
            "confirmation_text": LIVE_CONFIRMATION_TEXT,
        })
    assert exc.value.status_code == 422


def test_submit_endpoint_rejects_wrong_confirmation_text():
    with pytest.raises(HTTPException) as exc:
        submit_leg("batch", "leg", {
            "live_order_acknowledged": True,
            "confirmation_version": 1,
            "confirmation_text": "确认",
        })
    assert exc.value.status_code == 422
