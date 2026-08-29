"""A–T Golden tests for the pure Consumption Analytics Design reference."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest

from backend.services.consumption.analytics_design import (
    ActiveEventProjection, ActiveInterpretation, SourceCoverageInput,
    SourceCoverageStatus, evaluate_spending,
)
from backend.services.consumption.classification_design import (
    ClassificationStatus, EligibilityStatus, PrimaryCategory,
)
from backend.services.consumption.economic_events import EventType


FIXTURES = Path(__file__).parents[4] / "tests" / "fixtures" / "consumption" / "analytics"


def _event(value):
    return ActiveEventProjection(
        event_id=value["event_id"], event_type=EventType(value["event_type"]),
        analytics_effective_date=date.fromisoformat(value["analytics_effective_date"]) if value.get("analytics_effective_date") else None,
        account_id=value["account_id"], original_amount=Decimal(value["original_amount"]),
        base_net_amount=Decimal(value["base_net_amount"]) if value.get("base_net_amount") is not None else None,
        fx_source=value["fx_source"],
    )


def _interpretation(value):
    return ActiveInterpretation(
        event_id=value["event_id"], eligibility_status=EligibilityStatus(value["eligibility_status"]),
        classification_status=ClassificationStatus(value["classification_status"]),
        primary_category=PrimaryCategory(value["primary_category"]) if value.get("primary_category") else None,
        secondary_category=value.get("secondary_category"),
    )


def _coverage(value):
    return SourceCoverageInput(
        account_id=value["account_id"], month=date.fromisoformat(value["month"]),
        status=SourceCoverageStatus(value["status"]),
        observed_through=date.fromisoformat(value["observed_through"]) if value.get("observed_through") else None,
        connected=value.get("connected", True),
    )


def _text(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "value"):
        return value.value
    return value.isoformat() if isinstance(value, date) else value


def test_fixture_set_covers_a_through_t_with_all_required_files():
    cases = {item.name for item in FIXTURES.iterdir() if item.is_dir()}
    assert cases == set("abcdefghijklmnopqrst")
    for case in cases:
        assert {item.name for item in (FIXTURES / case).iterdir()} == {
            "input_events.json", "input_interpretations.json", "input_coverage.json",
            "expected_analytics.json", "case_manifest.json",
        }


@pytest.mark.parametrize("case_dir", sorted(path for path in FIXTURES.iterdir() if path.is_dir()), ids=lambda path: path.name.upper())
def test_analytics_golden_cases(case_dir):
    events = json.loads((case_dir / "input_events.json").read_text())
    interpretations = json.loads((case_dir / "input_interpretations.json").read_text())
    coverage_input = json.loads((case_dir / "input_coverage.json").read_text())
    expected = json.loads((case_dir / "expected_analytics.json").read_text())
    summary = evaluate_spending(
        (_event(item) for item in events), (_interpretation(item) for item in interpretations),
        (_coverage(item) for item in coverage_input["coverage"]),
        as_of_date=date.fromisoformat(coverage_input["as_of_date"]),
        expected_account_ids=coverage_input["expected_account_ids"],
    )
    month = date.fromisoformat(expected.pop("month"))
    point = next(item for item in summary.months if item.month == month)
    actual = {field: _text(getattr(point, field)) for field in expected}
    assert actual == {field: _text(value) for field, value in expected.items()}
    assert json.loads((case_dir / "case_manifest.json").read_text())["synthetic"] is True


def test_complete_month_averages_and_comparisons_only_use_complete_resolved_months():
    events = (
        ActiveEventProjection("july", EventType.CONSUMPTION, date(2026, 7, 10), "card", Decimal("100"), Decimal("100"), "NATIVE_CNY"),
        ActiveEventProjection("aug", EventType.CONSUMPTION, date(2026, 8, 10), "card", Decimal("200"), Decimal("200"), "NATIVE_CNY"),
    )
    interpretations = tuple(ActiveInterpretation(item.event_id, EligibilityStatus.ELIGIBLE, ClassificationStatus.CLASSIFIED, PrimaryCategory.DAILY, "FOOD_DINING") for item in events)
    coverage = (
        SourceCoverageInput("card", date(2026, 7, 1), SourceCoverageStatus.EXPLICIT, date(2026, 7, 31)),
        SourceCoverageInput("card", date(2026, 8, 1), SourceCoverageStatus.EXPLICIT, date(2026, 8, 10)),
    )
    summary = evaluate_spending(events, interpretations, coverage, as_of_date=date(2026, 8, 10), expected_account_ids=("card",))
    july = next(item for item in summary.months if item.month == date(2026, 7, 1))
    august = next(item for item in summary.months if item.month == date(2026, 8, 1))
    assert (summary.complete_month_average_cny, summary.complete_month_count) == (Decimal("100"), 1)
    assert july.data_coverage_status == "COMPLETE"
    assert (august.is_partial_month, august.comparison_available, august.comparison_reason) == (True, False, "MONTH_NOT_COMPARABLE")


def test_secondary_breakdown_is_deterministic_and_uses_total_eligible_denominator():
    events = (
        ActiveEventProjection("food", EventType.CONSUMPTION, date(2026, 7, 1), "card", Decimal("40"), Decimal("40"), "NATIVE_CNY"),
        ActiveEventProjection("hotel", EventType.CONSUMPTION, date(2026, 7, 2), "card", Decimal("60"), Decimal("60"), "NATIVE_CNY"),
    )
    interpretations = (
        ActiveInterpretation("food", EligibilityStatus.ELIGIBLE, ClassificationStatus.CLASSIFIED, PrimaryCategory.DAILY, "FOOD_DINING"),
        ActiveInterpretation("hotel", EligibilityStatus.ELIGIBLE, ClassificationStatus.CLASSIFIED, PrimaryCategory.TRAVEL, "ACCOMMODATION"),
    )
    coverage = (SourceCoverageInput("card", date(2026, 7, 1), SourceCoverageStatus.EXPLICIT, date(2026, 7, 31)),)
    result = evaluate_spending(events, interpretations, coverage, as_of_date=date(2026, 7, 31), expected_account_ids=("card",))
    assert [(item.secondary_category, item.share_of_total, item.share_within_primary) for item in result.secondary_breakdowns] == [
        ("ACCOMMODATION", Decimal("0.6"), Decimal("1")), ("FOOD_DINING", Decimal("0.4"), Decimal("1")),
    ]
