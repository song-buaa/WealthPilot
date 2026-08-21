from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from backend.services.technical_patterns.calibration import (
    CalibrationKey,
    CalibrationNotConfigured,
    CalibrationPartition,
    CalibrationRegistry,
    DatasetReviewStatus,
    PatternReviewLabel,
    PromotionRecommendation,
)
from backend.services.technical_patterns.calibration.breakdown_pilot import (
    BREAKDOWN_PILOT_ADJUSTMENT_POLICY,
    BREAKDOWN_PILOT_CALENDAR_VERSION,
    BREAKDOWN_PILOT_CALIBRATION_VERSION,
    BREAKDOWN_PILOT_DATASET_VERSION,
    BREAKDOWN_PILOT_SOURCE_PROVIDER,
    REQUIRED_BREAKDOWN_SAMPLE_KINDS,
    BreakdownExpectedState,
    BreakdownSampleKind,
    build_breakdown_calibration_dataset_manifest,
    execute_breakdown_calibration_pilot,
)


def test_breakdown_manifest_freezes_required_authority_and_source_fields():
    pilot = build_breakdown_calibration_dataset_manifest()

    assert pilot.dataset_version == BREAKDOWN_PILOT_DATASET_VERSION
    assert len(pilot.manifest_hash) == 64
    assert pilot.equity.economic_asset_class == "EQUITY"
    assert pilot.fixed_income.economic_asset_class == "FIXED_INCOME"
    for manifest in (pilot.equity, pilot.fixed_income):
        assert manifest.market == "US"
        assert manifest.timeframe == "1d"
        assert manifest.pattern_family == "level_break"
        assert manifest.pattern_type == "breakdown"
        assert manifest.coverage_gaps() == ()
        for dataset in manifest.datasets:
            assert dataset.source_provider == BREAKDOWN_PILOT_SOURCE_PROVIDER
            assert dataset.adjustment_policy == BREAKDOWN_PILOT_ADJUSTMENT_POLICY
            assert dataset.calendar_version == BREAKDOWN_PILOT_CALENDAR_VERSION
            assert len(dataset.source_bar_hash) == 64


def test_manifest_seals_holdout_and_untouched_labels_in_chronological_order():
    pilot = build_breakdown_calibration_dataset_manifest()

    for manifest in (pilot.equity, pilot.fixed_income):
        development = manifest.partition(CalibrationPartition.DEVELOPMENT)
        holdout = manifest.partition(CalibrationPartition.HOLDOUT)
        untouched = manifest.partition(CalibrationPartition.UNTOUCHED_VALIDATION)
        assert all(item.review_status is DatasetReviewStatus.COMPLETED for item in development)
        assert all(
            item.label in {PatternReviewLabel.POSITIVE, PatternReviewLabel.NEGATIVE}
            for item in development
        )
        assert all(item.review_status is DatasetReviewStatus.SEALED for item in holdout + untouched)
        assert all(item.label is None for item in holdout + untouched)
        assert max(item.date_range[1] for item in development) < min(
            item.date_range[0] for item in holdout
        )
        assert max(item.date_range[1] for item in holdout) < min(
            item.date_range[0] for item in untouched
        )


def test_pilot_covers_assets_regimes_and_all_seven_breakdown_cases():
    pilot = build_breakdown_calibration_dataset_manifest()

    assert {item.sample_kind for item in pilot.samples} == REQUIRED_BREAKDOWN_SAMPLE_KINDS
    assert {item.instrument for item in pilot.samples} >= {
        "AAPL",
        "SPY",
        "XLK",
        "AGG",
        "TLT",
        "LQD",
    }
    assert {item.economic_asset_class for item in pilot.samples} == {
        "EQUITY",
        "FIXED_INCOME",
    }


def test_development_exploration_removes_one_touch_support_false_positive():
    result = execute_breakdown_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        first, frozen = class_result.parameter_attempts
        assert first.attempt.based_on_partitions == (CalibrationPartition.DEVELOPMENT,)
        assert frozen.attempt.based_on_partitions == (CalibrationPartition.DEVELOPMENT,)
        assert first.definition_pass_count == len(first.outcomes) - 1
        assert frozen.definition_pass_count == len(frozen.outcomes)
        mismatch = [item for item in first.outcomes if not item.definition_conforms]
        assert len(mismatch) == 1
        assert mismatch[0].sample_kind is BreakdownSampleKind.INSUFFICIENT_STRUCTURE
        assert mismatch[0].false_positive is True
        assert frozen.parameters.require("minimum_boundary_touches") == 2


def test_support_failure_without_bearish_ema_confirmation_stays_unconfirmed():
    result = execute_breakdown_calibration_pilot()
    outcomes = tuple(
        item
        for class_result in (result.equity, result.fixed_income)
        for item in class_result.parameter_attempts[-1].outcomes
        if item.sample_kind
        is BreakdownSampleKind.SUPPORT_FAILURE_WITHOUT_CONFIRMATION
    )

    assert len(outcomes) == 2
    assert all(item.candidate_count == 1 for item in outcomes)
    assert all(item.ema_direction_aligned is False for item in outcomes)
    assert all(item.direction_confirmed is False for item in outcomes)
    assert all(item.label is PatternReviewLabel.NEGATIVE for item in outcomes)
    assert all(item.definition_conforms for item in outcomes)


def test_failed_breakdown_is_positive_evidence_then_technical_invalidation():
    result = execute_breakdown_calibration_pilot()
    outcomes = tuple(
        item
        for class_result in (result.equity, result.fixed_income)
        for item in class_result.parameter_attempts[-1].outcomes
        if item.sample_kind is BreakdownSampleKind.FAILED_BREAKDOWN
    )

    assert outcomes
    assert all(item.label is PatternReviewLabel.POSITIVE for item in outcomes)
    assert all(
        item.expected_state is BreakdownExpectedState.CONFIRMED_THEN_INVALIDATED
        for item in outcomes
    )
    assert all(item.direction_confirmed and item.invalidated for item in outcomes)
    assert all(item.definition_conforms for item in outcomes)


def test_parameter_freeze_is_exact_immutable_and_has_no_crypto_fallback():
    result = execute_breakdown_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        final_attempt = class_result.parameter_attempts[-1]
        version = class_result.frozen_version
        assert version.calibration_key.calibration_version == (
            BREAKDOWN_PILOT_CALIBRATION_VERSION
        )
        assert version.parameters_hash == final_attempt.parameters.parameters_hash
        assert version.dataset_manifest_hash == class_result.manifest.manifest_hash
        assert version.attempt_count == 2
        assert final_attempt.parameters.require("calibration_stage") == (
            "pilot_frozen_not_production"
        )
        registry = CalibrationRegistry((final_attempt.parameters,))
        with pytest.raises(CalibrationNotConfigured, match="BTC fallback are forbidden"):
            registry.resolve(
                CalibrationKey(
                    "CRYPTO",
                    "CRYPTO",
                    "1d",
                    "level_break",
                    "breakdown",
                    "btc-v1",
                )
            )


def test_holdout_and_untouched_validation_pass_with_same_frozen_hashes():
    result = execute_breakdown_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        holdout = class_result.holdout_evaluation
        untouched = class_result.untouched_evaluation
        assert holdout.partition is CalibrationPartition.HOLDOUT
        assert untouched.partition is CalibrationPartition.UNTOUCHED_VALIDATION
        assert holdout.completed_on < untouched.completed_on
        assert holdout.passed is True
        assert untouched.passed is True
        assert holdout.parameters_hash == untouched.parameters_hash
        assert holdout.dataset_manifest_hash == untouched.dataset_manifest_hash
        assert all(item.definition_conforms is True for item in holdout.reviews)
        assert all(item.definition_conforms is True for item in untouched.reviews)


def test_label_distribution_and_promotion_remain_definition_only():
    result = execute_breakdown_calibration_pilot()

    assert result.equity.validation_report.positive_cases == 3
    assert result.equity.validation_report.negative_cases == 3
    assert result.fixed_income.validation_report.positive_cases == 2
    assert result.fixed_income.validation_report.negative_cases == 2
    for class_result in (result.equity, result.fixed_income):
        assessment = class_result.promotion_assessment
        assert assessment.detector_pass is True
        assert assessment.calibration_frozen is True
        assert assessment.holdout_pass is True
        assert assessment.untouched_validation_pass is True
        assert assessment.human_review_pass is True
        assert assessment.eligible_for_governance_review is True
        assert assessment.blocking_reasons == ()
        assert class_result.validation_report.promotion_recommendation is (
            PromotionRecommendation.READY_FOR_GOVERNANCE_REVIEW
        )


def test_repeated_pilot_execution_is_hash_identical():
    first = execute_breakdown_calibration_pilot()
    second = execute_breakdown_calibration_pilot()

    assert first.dataset_manifest.manifest_hash == second.dataset_manifest.manifest_hash
    assert first.equity.frozen_version == second.equity.frozen_version
    assert first.fixed_income.frozen_version == second.fixed_income.frozen_version
    assert first.result_hash == second.result_hash


def test_contract_has_no_payoff_short_strategy_or_product_integration_fields():
    forbidden_fields = {
        "profit",
        "loss",
        "return",
        "win_rate",
        "ranking",
        "probability",
        "short_entry",
        "cover_price",
    }
    outcome = execute_breakdown_calibration_pilot().equity.parameter_attempts[-1].outcomes[0]
    assert not forbidden_fields & {item.name for item in fields(outcome)}

    source = (
        Path(__file__).parents[2]
        / "backend/services/technical_patterns/calibration/breakdown_pilot.py"
    ).read_text(encoding="utf-8").lower()
    forbidden_imports = (
        "backend.services.action",
        "backend.services.portfolio",
        "backend.services.decision",
        "ib_async",
        "placeorder",
        "place_order",
        "cancel_order",
    )
    assert all(item not in source for item in forbidden_imports)
