from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from backend.services.pattern_data.immutable_dataset import content_hash
from backend.services.technical_patterns.calibration import (
    RuntimeCalibrationNotPromoted,
    RuntimeCalibrationScope,
    build_approved_runtime_calibration_registry,
    build_dataset_v2_runtime_promotions,
    build_runtime_candidate_freezes,
)
from backend.services.technical_patterns.runtime_provider import (
    PromotedIBKRPatternEvidenceProvider,
    RUNTIME_BAR_WINDOW,
    _bounded_runtime_series,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = (
    REPO_ROOT
    / "docs/pattern_review/REAL_IBKR_PATTERN_RUNTIME_VALIDATION_V2_MANIFEST.json"
)


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_dataset_v2_validation_manifest_is_hash_bound_and_scope_complete():
    manifest = _manifest()
    recorded_hash = manifest.pop("manifest_hash")

    assert content_hash(manifest) == recorded_hash
    assert manifest["evaluation_authority"] == "IMMUTABLE_DATASET_V2_ARTIFACT"
    assert manifest["ibkr_reads_after_capture"] == 0
    assert len(manifest["promotion_scopes"]) == 12
    assert Counter(item["verdict"] for item in manifest["promotion_scopes"]) == {
        "READY_FOR_RUNTIME_PROMOTION": 9,
        "INSUFFICIENT_REAL_CASE_EVIDENCE": 3,
    }


def test_nine_scopes_use_one_hash_through_all_three_partitions():
    candidates = {
        (item.scope.pattern_type, item.scope.economic_asset_class): item
        for item in build_runtime_candidate_freezes()
    }
    for item in _manifest()["promotion_scopes"]:
        key = (item["pattern_type"], item["economic_asset_class"])
        assert item["parameter_hash"] == candidates[key].final_parameter_hash
        assert item["parameter_hash_consistent"] is True
        if item["verdict"] == "READY_FOR_RUNTIME_PROMOTION":
            assert item["development_sanity"] == "PASS"
            assert item["holdout_result"] == "PASS"
            assert item["untouched_result"] == "PASS"
            assert {
                detail["parameter_hash"]
                for detail in item["partition_details"].values()
            } == {item["parameter_hash"]}


def test_three_fixed_income_evidence_gaps_remain_closed():
    scopes = {
        (item["pattern_type"], item["economic_asset_class"]): item
        for item in _manifest()["promotion_scopes"]
    }
    for key in (
        ("breakdown", "FIXED_INCOME"),
        ("rectangle", "FIXED_INCOME"),
        ("double_bottom", "FIXED_INCOME"),
    ):
        item = scopes[key]
        assert item["verdict"] == "INSUFFICIENT_REAL_CASE_EVIDENCE"
        assert item["development_sanity"] == "NOT_REOPENED"
        assert item["untouched_result"] == "NOT_OPENED"
        assert item["partition_details"] == {}


def test_approved_registry_is_exactly_the_nine_promoted_scopes():
    registry = build_approved_runtime_calibration_registry()
    assert len(build_dataset_v2_runtime_promotions()) == 9
    assert len(registry.snapshot()) == 9
    with pytest.raises(RuntimeCalibrationNotPromoted):
        registry.resolve(
            RuntimeCalibrationScope(
                market="US",
                economic_asset_class="FIXED_INCOME",
                timeframe="1d",
                pattern_family="range",
                pattern_type="rectangle",
            )
        )


def test_runtime_provider_does_not_open_ibkr_for_unpromoted_scope():
    calls = 0

    def forbidden_source():
        nonlocal calls
        calls += 1
        raise AssertionError("unpromoted scope must fail before a live read")

    provider = PromotedIBKRPatternEvidenceProvider(source_factory=forbidden_source)
    from backend.services.technical_patterns.decision_integration import (
        PatternDecisionTarget,
    )

    bundles = provider.collect(
        PatternDecisionTarget(
            requested_symbol="BTC:US",
            symbol="BTC",
            market="US",
            currency="USD",
            economic_asset_class="CRYPTO",
        )
    )
    assert calls == 0
    assert bundles[0].reason == "exact_runtime_pattern_scope_not_promoted"


def test_runtime_provider_uses_current_data_with_a_deterministic_bounded_window():
    from backend.services.pattern_data.immutable_dataset import ImmutablePatternDataset
    from backend.services.pattern_data.contracts import build_source_bar_hash

    dataset = ImmutablePatternDataset(
        REPO_ROOT / "docs/pattern_review/REAL_IBKR_PATTERN_DATASET_V2_MANIFEST.json",
        REPO_ROOT,
    )
    captured = dataset.load_series("AAPL")
    bounded = _bounded_runtime_series(captured)

    assert len(bounded.bars) == RUNTIME_BAR_WINDOW == 300
    assert bounded.bars == captured.bars[-RUNTIME_BAR_WINDOW:]
    assert bounded.source_bar_hash == build_source_bar_hash(bounded.bars)
    assert bounded.source_bar_hash != captured.source_bar_hash


def test_capture_read_accounting_has_no_account_or_mutation_surface():
    dataset = json.loads(
        (
            REPO_ROOT
            / "docs/pattern_review/REAL_IBKR_PATTERN_DATASET_V2_MANIFEST.json"
        ).read_text(encoding="utf-8")
    )
    assert dataset["capture_read_accounting"] == {
        "contract_details": 17,
        "historical_data": 17,
        "schedule": 102,
        "account_requests": 0,
        "portfolio_requests": 0,
        "order_requests": 0,
        "broker_mutations": 0,
        "order_mutations": 0,
    }
