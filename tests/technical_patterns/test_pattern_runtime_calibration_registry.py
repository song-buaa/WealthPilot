from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from backend.services.technical_patterns.calibration import (
    GOVERNANCE_ACCEPTANCE,
    ApprovedRuntimeCalibrationRegistry,
    RuntimeCalibrationNotPromoted,
    RuntimeCalibrationScope,
    RuntimePromotionVerdict,
    RuntimeScopePromotionEvidence,
    build_runtime_candidate_freezes,
)
from backend.services.technical_patterns.calibration.runtime_registry import (
    GOVERNANCE_ACCEPTANCE_RECORD_HASH,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_detector_first_import_does_not_cycle_through_runtime_registry():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from backend.services.technical_patterns.detectors import "
                "ASCENDING_TRIANGLE_DETECTOR_VERSION; "
                "from backend.services.technical_patterns.calibration import "
                "build_runtime_candidate_freezes; "
                "assert len(build_runtime_candidate_freezes()) == 12"
            ),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr


def _promotion(candidate):
    return RuntimeScopePromotionEvidence(
        scope=candidate.scope,
        verdict=RuntimePromotionVerdict.READY_FOR_RUNTIME_PROMOTION,
        calibration_version=candidate.calibration_version,
        parameter_hash=candidate.final_parameter_hash,
        holdout_result="PASS",
        untouched_result="PASS",
        governance_acceptance=GOVERNANCE_ACCEPTANCE,
    )


def test_all_twelve_real_development_scopes_are_frozen_without_threshold_adjustment():
    candidates = build_runtime_candidate_freezes()

    assert len(candidates) == 12
    assert len({candidate.scope for candidate in candidates}) == 12
    assert all(candidate.adjustment_attempt_count == 0 for candidate in candidates)
    assert all("runtime-candidate-v1" in candidate.calibration_version for candidate in candidates)
    assert all("development-v1" not in candidate.calibration_version for candidate in candidates)


def test_candidate_lineage_matches_committed_real_review_manifest():
    manifest = json.loads(
        (
            REPO_ROOT
            / "docs/pattern_review/REAL_IBKR_SIX_PATTERN_HUMAN_REVIEW_MANIFEST.json"
        ).read_text(encoding="utf-8")
    )
    expected = {
        (case["pattern_type"], case["economic_asset_class"]): case["parameter_hash"]
        for case in manifest["cases"]
    }

    for candidate in build_runtime_candidate_freezes():
        key = (candidate.scope.pattern_type, candidate.scope.economic_asset_class)
        assert candidate.development_parameter_hash == expected[key]
        assert candidate.dataset_manifest_hash == manifest["dataset_manifest_hash"]
        assert candidate.review_manifest_hash == manifest["manifest_hash"]
        assert candidate.governance_acceptance == GOVERNANCE_ACCEPTANCE

    acceptance = (
        REPO_ROOT
        / "docs/PATTERN_EVIDENCE_V1_REVIEW_GOVERNANCE_ACCEPTANCE.md"
    ).read_bytes()
    assert hashlib.sha256(acceptance).hexdigest() == GOVERNANCE_ACCEPTANCE_RECORD_HASH


def test_approved_registry_contains_only_explicit_ready_scopes():
    equity, fixed_income, *_ = build_runtime_candidate_freezes()
    not_ready = RuntimeScopePromotionEvidence(
        scope=fixed_income.scope,
        verdict=RuntimePromotionVerdict.INSUFFICIENT_REAL_CASE_EVIDENCE,
        calibration_version=fixed_income.calibration_version,
        parameter_hash=fixed_income.final_parameter_hash,
        holdout_result="NOT_RUN",
        untouched_result="NOT_RUN",
        governance_acceptance=GOVERNANCE_ACCEPTANCE,
    )
    registry = ApprovedRuntimeCalibrationRegistry(
        (equity, fixed_income),
        (_promotion(equity), not_ready),
    )

    assert registry.resolve(equity.scope) == equity
    assert registry.resolve_parameters(equity.scope) == equity.parameters
    with pytest.raises(RuntimeCalibrationNotPromoted):
        registry.resolve(fixed_income.scope)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("economic_asset_class", "FIXED_INCOME"),
        ("economic_asset_class", "CRYPTO"),
        ("market", "BTC"),
        ("timeframe", "4h"),
        ("pattern_type", "rectangle"),
    ),
)
def test_registry_has_no_cross_scope_nearest_or_crypto_fallback(field, value):
    candidate = build_runtime_candidate_freezes()[0]
    registry = ApprovedRuntimeCalibrationRegistry(
        (candidate,),
        (_promotion(candidate),),
    )
    material = {
        "market": candidate.scope.market,
        "economic_asset_class": candidate.scope.economic_asset_class,
        "timeframe": candidate.scope.timeframe,
        "pattern_family": candidate.scope.pattern_family,
        "pattern_type": candidate.scope.pattern_type,
    }
    material[field] = value

    with pytest.raises(RuntimeCalibrationNotPromoted):
        registry.resolve(RuntimeCalibrationScope(**material))


def test_ready_promotion_rejects_missing_unseen_pass_or_hash_drift():
    candidate = build_runtime_candidate_freezes()[0]
    with pytest.raises(ValueError, match="both unseen passes"):
        RuntimeScopePromotionEvidence(
            scope=candidate.scope,
            verdict=RuntimePromotionVerdict.READY_FOR_RUNTIME_PROMOTION,
            calibration_version=candidate.calibration_version,
            parameter_hash=candidate.final_parameter_hash,
            holdout_result="PASS",
            untouched_result="NOT_RUN",
            governance_acceptance=GOVERNANCE_ACCEPTANCE,
        )
    drifted = RuntimeScopePromotionEvidence(
        scope=candidate.scope,
        verdict=RuntimePromotionVerdict.READY_FOR_RUNTIME_PROMOTION,
        calibration_version=candidate.calibration_version,
        parameter_hash="0" * 64,
        holdout_result="PASS",
        untouched_result="PASS",
        governance_acceptance=GOVERNANCE_ACCEPTANCE,
    )
    with pytest.raises(ValueError, match="drifted"):
        ApprovedRuntimeCalibrationRegistry((candidate,), (drifted,))


def test_frozen_candidate_is_immutable():
    candidate = build_runtime_candidate_freezes()[0]

    with pytest.raises(FrozenInstanceError):
        candidate.adjustment_attempt_count = 1
