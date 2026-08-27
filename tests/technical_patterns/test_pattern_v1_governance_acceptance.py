from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_PATH = (
    REPO_ROOT / "docs" / "PATTERN_EVIDENCE_V1_REVIEW_GOVERNANCE_ACCEPTANCE.md"
)


def _acceptance_text() -> str:
    return ACCEPTANCE_PATH.read_text(encoding="utf-8")


def test_v1_ai_assisted_review_governance_acceptance_is_explicit():
    text = _acceptance_text()

    assert "AI_ASSISTED_REVIEW_ACCEPTED_FOR_V1_PROMOTION" in text
    assert "WealthPilot Pattern Evidence v1" in text
    assert "market = US" in text
    assert "timeframe = 1d" in text
    assert "economic_asset_class = EQUITY | FIXED_INCOME" in text


def test_governance_acceptance_does_not_claim_human_review_or_waive_validation():
    text = _acceptance_text()

    assert "Independent human chart review = NOT performed" in text
    assert "Human reviewer sign-off = NOT claimed" in text
    assert "Production Ready = NO" in text
    assert "Trading authority = NO" in text
    assert "Holdout requirement = STILL REQUIRED" in text
    assert "Untouched Validation requirement = STILL REQUIRED" in text
    assert "human_review_complete=false" in text
