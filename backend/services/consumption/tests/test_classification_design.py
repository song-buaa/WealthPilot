"""A–T Golden tests for pure Consumption Eligibility / Classification design."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path

import pytest

from backend.services.consumption.classification_design import (
    AccountPurposePrior, ClassificationContext, PrimaryCategory, TravelContext,
    UserConfirmation, UserRule, canonical_classification_json, classify,
    EligibilityStatus, EventInput,
)
from backend.services.consumption.economic_events import EventType


FIXTURES = Path(__file__).parents[4] / "tests" / "fixtures" / "consumption" / "classification"


def _event(data):
    return EventInput(
        event_id=data["event_id"], event_type=EventType(data["event_type"]),
        event_date=date.fromisoformat(data["event_date"]) if data.get("event_date") else None,
        account_id=data["account_id"], descriptor=data["descriptor"],
        amount=Decimal(data.get("amount", "0")), original_event_id=data.get("original_event_id"),
    )


def _context(data):
    return ClassificationContext(
        account_purpose_priors=tuple(AccountPurposePrior(
            item["account_id"], PrimaryCategory(item["preferred_primary"]), date.fromisoformat(item["effective_from"]),
            date.fromisoformat(item["effective_to"]) if item.get("effective_to") else None,
        ) for item in data.get("account_purpose_priors", [])),
        travel_contexts=tuple(TravelContext(item["destination"], date.fromisoformat(item["start_date"]), date.fromisoformat(item["end_date"])) for item in data.get("travel_contexts", [])),
        user_rules=tuple(UserRule(
            item["rule_id"], item["match_text"], EligibilityStatus(item["eligibility_status"]),
            PrimaryCategory(item["primary_category"]) if item.get("primary_category") else None,
            item.get("secondary_category"), item.get("account_id"),
            Decimal(item["amount"]) if item.get("amount") is not None else None,
            Decimal(item.get("amount_tolerance", "0")),
            date.fromisoformat(item["effective_from"]) if item.get("effective_from") else None,
            date.fromisoformat(item["effective_to"]) if item.get("effective_to") else None,
        ) for item in data.get("user_rules", [])),
        user_confirmations=tuple(UserConfirmation(
            item["event_id"], EligibilityStatus(item["eligibility_status"]),
            PrimaryCategory(item["primary_category"]) if item.get("primary_category") else None,
            item.get("secondary_category"), item.get("reason", "USER_CONFIRMED"),
        ) for item in data.get("user_confirmations", [])),
    )


def _project(value, fields):
    return {field: value[field] for field in fields}


def test_fixture_set_covers_required_a_through_t():
    assert {item.name for item in FIXTURES.iterdir() if item.is_dir()} == set("abcdefghijklmnopqrst")


@pytest.mark.parametrize("case_dir", sorted(item for item in FIXTURES.iterdir() if item.is_dir()), ids=lambda item: item.name)
def test_golden_classification_cases(case_dir):
    events = json.loads((case_dir / "input_events.json").read_text())
    context = json.loads((case_dir / "context.json").read_text())
    expected = json.loads((case_dir / "expected_classification.json").read_text())
    manifest = json.loads((case_dir / "case_manifest.json").read_text())
    result = classify((_event(item) for item in events), _context(context))
    actual = [item.to_dict() for item in result]
    assert [_project(item, expected["fields"]) for item in actual] == expected["results"]
    assert manifest["case_id"] == case_dir.name.upper()
    assert canonical_classification_json(result) == canonical_classification_json(classify((_event(item) for item in events), _context(context)))
