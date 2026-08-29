"""Golden tests for the pure EconomicEvent reference normalizer."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest

from backend.services.consumption.economic_events import (
    NormalizationContext, RawTransactionInput, canonical_event_json, normalize,
)


FIXTURES = Path(__file__).parents[4] / "tests" / "fixtures" / "consumption" / "economic_events"


def test_fixture_set_covers_every_required_case_a_through_p():
    assert {path.name for path in FIXTURES.iterdir() if path.is_dir()} == set("abcdefghijklmnop")


def _raw(item: dict[str, object]) -> RawTransactionInput:
    return RawTransactionInput(
        raw_id=str(item["raw_id"]), account_id=str(item["account_id"]), account_type=str(item["account_type"]),
        transaction_date=date.fromisoformat(item["transaction_date"]) if item.get("transaction_date") else None,
        posting_date=date.fromisoformat(item["posting_date"]) if item.get("posting_date") else None,
        source_amount=Decimal(str(item["source_amount"])), currency=str(item["currency"]),
        raw_description=str(item["raw_description"]), dedup_status=str(item.get("dedup_status", "UNIQUE")),
        settlement_amount=Decimal(str(item["settlement_amount"])) if item.get("settlement_amount") is not None else None,
        settlement_currency=str(item["settlement_currency"]) if item.get("settlement_currency") else None,
    )


def _projection(event: dict[str, object], keys: list[str]) -> dict[str, object]:
    return {key: event[key] for key in keys}


@pytest.mark.parametrize("case_dir", sorted(path for path in FIXTURES.iterdir() if path.is_dir()), ids=lambda path: path.name)
def test_golden_cases(case_dir: Path):
    source = json.loads((case_dir / "input_raw_transactions.json").read_text())
    expected = json.loads((case_dir / "expected_events.json").read_text())
    manifest = json.loads((case_dir / "case_manifest.json").read_text())
    events = normalize((_raw(item) for item in source["raw_transactions"]), NormalizationContext(frozenset(source["owned_account_ids"])))
    actual = [item.to_dict() for item in events]
    assert [_projection(item, expected["fields"]) for item in actual] == expected["events"]
    assert manifest["case_id"] == case_dir.name.upper()
    assert canonical_event_json(events) == canonical_event_json(normalize((_raw(item) for item in source["raw_transactions"]), NormalizationContext(frozenset(source["owned_account_ids"]))))


def test_contract_serialization_is_decimal_exact_and_date_safe():
    event = normalize((RawTransactionInput("fx", "card", "CREDIT_CARD", date(2026, 7, 12), None, Decimal("-100.00"), "USD", "[CONSUMPTION]"),), NormalizationContext(frozenset({"card"})))[0].to_dict()
    assert event["amount"] == "100.00"
    assert event["base_amount"] is None
    assert event["event_date"] == "2026-07-12"
    assert event["resolution_status"] == "NEEDS_REVIEW"
    assert event["fx_source"] == "FX_REQUIRED"


def test_posting_date_is_the_only_event_date_fallback_and_unproved_internal_is_not_consumption():
    rows = (
        RawTransactionInput("posting-only", "card", "CREDIT_CARD", None, date(2026, 7, 16), Decimal("-12"), "CNY", "[CONSUMPTION] posting fallback"),
        RawTransactionInput("unproved-in", "unknown", "DEBIT_CARD", date(2026, 7, 18), None, Decimal("50"), "CNY", "[INTERNAL] unproved"),
    )
    events = {item.event_id: item.to_dict() for item in normalize(rows, NormalizationContext(frozenset({"card"})))}
    assert events["event:consumption:posting-only"]["event_date"] == "2026-07-16"
    assert events["event:other:unproved-in"]["resolution_reason"] == "INTERNAL_OWNERSHIP_OR_PAIR_UNPROVEN"
    assert events["event:other:unproved-in"]["event_type"] == "OTHER"


def test_multiple_matched_refunds_accumulate_without_erasing_refund_facts():
    rows = (
        RawTransactionInput("purchase", "card", "CREDIT_CARD", date(2026, 7, 1), None, Decimal("-5000"), "CNY", "[CONSUMPTION] purchase"),
        RawTransactionInput("refund-one", "card", "CREDIT_CARD", date(2026, 8, 1), None, Decimal("1000"), "CNY", "[REFUND] REF:purchase one"),
        RawTransactionInput("refund-two", "card", "CREDIT_CARD", date(2026, 8, 2), None, Decimal("1500"), "CNY", "[REFUND] REF:purchase two"),
    )
    events = {item.event_id: item.to_dict() for item in normalize(rows, NormalizationContext(frozenset({"card"})))}
    assert events["event:consumption:purchase"]["refund_amount"] == "2500.00"
    assert events["event:consumption:purchase"]["net_amount"] == "2500.00"
    assert {key for key in events if key.startswith("event:refund:")} == {"event:refund:refund-one", "event:refund:refund-two"}
