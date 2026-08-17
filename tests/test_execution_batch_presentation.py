from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import backend.api.execution_batch as batch_api
from backend.api.execution_batch import LIVE_CONFIRMATION_TEXT, live_confirmation_text, submit_leg


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
    assert "batch.legs.length" in BATCH_CARD
    assert "batch.legs.map" in BATCH_CARD
    assert live_confirmation_text(2) == "确认并提交 2 笔 IBKR 实盘限价单"


def test_replaced_batch_remains_visible_but_is_explicitly_terminal():
    assert "batch.status === 'CANCELLED'" in BATCH_CARD
    assert "已因投资意图变更终止 / 已替换" in BATCH_CARD
    assert "不能继续提交" in BATCH_CARD


def test_submit_endpoint_rejects_missing_human_ack_before_service_access():
    with pytest.raises(HTTPException) as exc:
        submit_leg("batch", "leg", {
            "live_order_acknowledged": False,
            "confirmation_version": 1,
            "confirmation_text": LIVE_CONFIRMATION_TEXT,
        })
    assert exc.value.status_code == 422


def test_submit_endpoint_rejects_wrong_confirmation_text(monkeypatch):
    class FakeSession:
        def rollback(self):
            pass

        def close(self):
            pass

    class FakeService:
        def get_batch(self, _batch_id):
            return SimpleNamespace(legs=[object(), object()])

    monkeypatch.setattr(batch_api, "get_session", FakeSession)
    monkeypatch.setattr(batch_api, "_service", lambda _session: FakeService())
    with pytest.raises(HTTPException) as exc:
        submit_leg("batch", "leg", {
            "live_order_acknowledged": True,
            "confirmation_version": 1,
            "confirmation_text": "确认",
        })
    assert exc.value.status_code == 422
