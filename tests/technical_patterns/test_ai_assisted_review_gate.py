from __future__ import annotations

import inspect
from collections import Counter
from pathlib import Path

from backend.services.technical_patterns import ai_assisted_review
from backend.services.technical_patterns.ai_assisted_review import (
    AI_REVIEWER,
    AI_REVIEW_GATE,
    AI_REVIEW_NOTES,
    IDENTITY_CSV_NAME,
    load_identity_csv,
    validate_review_pack,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_committed_ai_assisted_review_pack_is_complete_and_not_human_signoff():
    manifest = validate_review_pack(REPO_ROOT)
    cases = manifest["cases"]

    assert len(cases) == 120
    assert Counter(case["review_case_kind"] for case in cases) == {
        "DETECTED_CANDIDATE": 60,
        "NEGATIVE_CONTROL_NO_DETECTION": 60,
    }
    assert all(case["human_review_label"] == "PASS" for case in cases)
    assert all(case["reviewer"] == AI_REVIEWER for case in cases)
    assert all(case["human_review_notes"] == AI_REVIEW_NOTES for case in cases)
    assert all(case["reviewed_at"] for case in cases)
    assert manifest["gate_status"] == AI_REVIEW_GATE
    assert manifest["ai_assisted_engineering_review_complete"] is True
    assert manifest["human_review_complete"] is False
    assert manifest["production_promotion_authorized"] is False
    assert manifest["holdout_detector_run"] is False
    assert manifest["untouched_validation_detector_run"] is False


def test_canonical_case_identity_csv_covers_every_case_once():
    rows = load_identity_csv(
        REPO_ROOT / "docs" / "pattern_review" / IDENTITY_CSV_NAME
    )

    assert len(rows) == 120
    assert len({row["case_id"] for row in rows}) == 120
    assert all(row["identity_status"] == "VALID" for row in rows)
    assert all(len(row["identity_material_hash"]) == 64 for row in rows)


def test_ai_assisted_review_has_no_detector_calibration_or_mutation_surface():
    source = inspect.getsource(ai_assisted_review)
    forbidden = (
        "from .detectors",
        "from .calibration",
        "DetectorFramework(",
        "CalibrationRegistry(",
        "placeOrder(",
        "cancelOrder(",
        "modifyOrder(",
        "OrderManager(",
        "ExecutionPlan(",
        "DecisionService(",
        "IBKRHistoricalDataSource(",
    )

    assert all(token not in source for token in forbidden)
