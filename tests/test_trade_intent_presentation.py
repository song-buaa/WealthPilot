"""Static presentation-contract checks for the TypeScript-only Decision UI."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DECISION_SOURCE = (ROOT / "frontend/src/pages/Decision.tsx").read_text()
SUMMARY_SOURCE = (ROOT / "frontend/src/lib/tradeIntentPresentation.ts").read_text()


def test_ready_trade_intent_replaces_conflicting_legacy_intent_rows():
    assert "tradeIntent.readiness !== 'READY_FOR_CONFIRMATION'" in SUMMARY_SOURCE
    assert "tradeIntent.confirmation_status !== 'CONFIRMED'" in SUMMARY_SOURCE
    assert "type: '多标的交易意图'" in SUMMARY_SOURCE
    assert "legCount: tradeIntent.legs.length" in SUMMARY_SOURCE
    assert "tradeIntent.side.value === 'BUY'" in SUMMARY_SOURCE

    assert "tradeIntentSummary ? (" in DECISION_SOURCE
    assert "{tradeIntentSummary.legCount} 个" in DECISION_SOURCE
    assert "{tradeIntentSummary.action}" in DECISION_SOURCE


def test_legacy_intent_rows_remain_for_messages_without_trade_intent():
    assert "!tradeIntentSummary && intent?.asset" in DECISION_SOURCE
    assert "!tradeIntentSummary && intent?.action" in DECISION_SOURCE
    assert "!tradeIntentSummary && intent?.time_context" in DECISION_SOURCE
    assert "!tradeIntentSummary && intent?.confidence" in DECISION_SOURCE

    # Both loaded explain data and fallback data receive the current message's
    # typed intent, so the priority rule is consistent across conversation views.
    assert "data={explainData} tradeIntent={lastDone?.tradeIntent}" in DECISION_SOURCE
    assert "data={fallback} tradeIntent={lastDone.tradeIntent}" in DECISION_SOURCE
