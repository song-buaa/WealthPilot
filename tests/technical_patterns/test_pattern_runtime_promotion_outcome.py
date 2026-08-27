from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from backend.services.technical_patterns.calibration import (
    ApprovedRuntimeCalibrationRegistry,
    build_runtime_candidate_freezes,
)
from backend.services.technical_patterns.core.identity import stable_hash
from backend.services.technical_patterns.decision_integration import (
    DecisionPatternEvidenceCollector,
    PatternDecisionTarget,
    PatternInvocationScope,
)
from backend.services.technical_patterns.evidence import (
    PatternEvidenceResultState,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    REPO_ROOT
    / "docs/pattern_review/REAL_IBKR_PATTERN_RUNTIME_VALIDATION_MANIFEST.json"
)


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_real_runtime_validation_manifest_is_hash_bound_and_scope_complete():
    manifest = _manifest()
    recorded_hash = manifest.pop("manifest_hash")

    assert stable_hash(manifest) == recorded_hash
    assert len(manifest["promotion_scopes"]) == 12
    assert len(
        {
            (item["pattern_type"], item["economic_asset_class"])
            for item in manifest["promotion_scopes"]
        }
    ) == 12
    assert Counter(item["verdict"] for item in manifest["promotion_scopes"]) == {
        "DATA_QUALITY_BLOCKED": 9,
        "INSUFFICIENT_REAL_CASE_EVIDENCE": 3,
    }
    assert manifest["approved_runtime_scope_count"] == 0
    assert manifest["runtime_provider_activated"] is False


def test_holdout_results_use_exact_frozen_hashes_without_tuning():
    manifest = _manifest()
    candidate_hashes = {
        (item.scope.pattern_type, item.scope.economic_asset_class): (
            item.final_parameter_hash
        )
        for item in build_runtime_candidate_freezes()
    }

    assert manifest["threshold_adjustment_attempt_count"] == 0
    assert manifest["holdout"]["detector_tuning_after_open"] is False
    assert manifest["holdout"]["source_hash_matches"] == 17
    assert manifest["holdout"]["source_hash_mismatches"] == 0
    assert Counter(
        item["holdout_result"] for item in manifest["promotion_scopes"]
    ) == {"PASS": 9, "INSUFFICIENT_REAL_CASE_EVIDENCE": 3}
    for item in manifest["promotion_scopes"]:
        key = (item["pattern_type"], item["economic_asset_class"])
        assert item["parameter_hash"] == candidate_hashes[key]


def test_untouched_source_drift_blocks_before_detector_without_cherry_pick():
    untouched = _manifest()["untouched_validation"]

    assert untouched["detector_run"] is False
    assert untouched["source_hash_matches"] == 1
    assert untouched["source_hash_mismatches"] == 16
    assert len(untouched["mismatches"]) == 16
    assert {item["symbol"] for item in untouched["mismatches"]} == {
        "AAPL",
        "MSFT",
        "NVDA",
        "JPM",
        "XOM",
        "JNJ",
        "SPY",
        "QQQ",
        "IWM",
        "XLK",
        "XLF",
        "XLE",
        "AGG",
        "TLT",
        "IEF",
        "SHY",
    }


def test_zero_promoted_scopes_keep_registry_and_decision_provider_fail_closed():
    registry = ApprovedRuntimeCalibrationRegistry(
        build_runtime_candidate_freezes(),
        (),
    )
    assert registry.snapshot() == ()

    snapshot = DecisionPatternEvidenceCollector().collect(
        PatternInvocationScope.SINGLE,
        (
            PatternDecisionTarget(
                requested_symbol="AAPL:US",
                symbol="AAPL",
                market="US",
                currency="USD",
                economic_asset_class="EQUITY",
            ),
        ),
    )
    assert snapshot is not None
    assert snapshot.bundles[0].result_state is PatternEvidenceResultState.DATA_UNAVAILABLE
    assert snapshot.bundles[0].reason == "runtime_pattern_provider_not_promoted"


def test_live_read_accounting_has_no_account_or_mutation_surface():
    manifest = _manifest()
    accounting = manifest["read_accounting"]

    assert accounting["contract_details_requests"] == 17
    assert accounting["historical_requests"] == 17
    assert accounting["schedule_requests"] == 102
    assert accounting["account_data_requests"] == 0
    assert accounting["broker_mutations"] == 0
    assert accounting["order_mutations"] == 0
    assert set(manifest["mutations"].values()) == {0}
