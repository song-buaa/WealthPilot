from __future__ import annotations

from dataclasses import fields

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
from backend.services.technical_patterns.calibration.ascending_triangle_pilot import (
    ASCENDING_TRIANGLE_PILOT_ADJUSTMENT_POLICY,
    ASCENDING_TRIANGLE_PILOT_CALENDAR_VERSION,
    ASCENDING_TRIANGLE_PILOT_CALIBRATION_VERSION,
    ASCENDING_TRIANGLE_PILOT_DATASET_VERSION,
    ASCENDING_TRIANGLE_PILOT_SOURCE_PROVIDER,
    REQUIRED_ASCENDING_TRIANGLE_SAMPLE_KINDS,
    AscendingTriangleExpectedState,
    AscendingTriangleSampleKind,
    AscendingTriangleSampleOutcome,
    build_ascending_triangle_calibration_dataset_manifest,
    execute_ascending_triangle_calibration_pilot,
)


def test_manifest_freezes_required_authority_and_source_fields():
    pilot = build_ascending_triangle_calibration_dataset_manifest()

    assert pilot.dataset_version == ASCENDING_TRIANGLE_PILOT_DATASET_VERSION
    assert len(pilot.manifest_hash) == 64
    for manifest in (pilot.equity, pilot.fixed_income):
        assert manifest.market == "US"
        assert manifest.timeframe == "1d"
        assert manifest.pattern_family == "triangle"
        assert manifest.pattern_type == "ascending_triangle"
        assert manifest.coverage_gaps() == ()
        for dataset in manifest.datasets:
            assert dataset.source_provider == ASCENDING_TRIANGLE_PILOT_SOURCE_PROVIDER
            assert dataset.adjustment_policy == (
                ASCENDING_TRIANGLE_PILOT_ADJUSTMENT_POLICY
            )
            assert dataset.calendar_version == (
                ASCENDING_TRIANGLE_PILOT_CALENDAR_VERSION
            )
            assert len(dataset.source_bar_hash) == 64


def test_manifest_seals_holdout_and_untouched_labels_chronologically():
    pilot = build_ascending_triangle_calibration_dataset_manifest()

    for manifest in (pilot.equity, pilot.fixed_income):
        development = manifest.partition(CalibrationPartition.DEVELOPMENT)
        holdout = manifest.partition(CalibrationPartition.HOLDOUT)
        untouched = manifest.partition(CalibrationPartition.UNTOUCHED_VALIDATION)
        assert all(
            item.review_status is DatasetReviewStatus.COMPLETED
            for item in development
        )
        assert all(
            item.label in {PatternReviewLabel.POSITIVE, PatternReviewLabel.NEGATIVE}
            for item in development
        )
        assert all(
            item.review_status is DatasetReviewStatus.SEALED
            for item in holdout + untouched
        )
        assert all(item.label is None for item in holdout + untouched)
        assert max(item.date_range[1] for item in development) < min(
            item.date_range[0] for item in holdout
        )
        assert max(item.date_range[1] for item in holdout) < min(
            item.date_range[0] for item in untouched
        )


def test_development_covers_assets_regimes_and_all_fourteen_geometry_cases():
    pilot = build_ascending_triangle_calibration_dataset_manifest()

    assert {item.sample_kind for item in pilot.samples} == (
        REQUIRED_ASCENDING_TRIANGLE_SAMPLE_KINDS
    )
    assert {item.instrument for item in pilot.samples} >= {
        "AAPL",
        "SPY",
        "XLK",
        "AGG",
        "TLT",
        "LQD",
    }
    for asset_class in ("EQUITY", "FIXED_INCOME"):
        development_kinds = {
            item.sample_kind
            for item in pilot.samples
            if item.economic_asset_class == asset_class
            and item.partition is CalibrationPartition.DEVELOPMENT
        }
        assert development_kinds == REQUIRED_ASCENDING_TRIANGLE_SAMPLE_KINDS


def test_development_exploration_removes_weak_convergence_false_structure():
    result = execute_ascending_triangle_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        first, frozen = class_result.parameter_attempts
        assert first.attempt.based_on_partitions == (
            CalibrationPartition.DEVELOPMENT,
        )
        assert frozen.attempt.based_on_partitions == (
            CalibrationPartition.DEVELOPMENT,
        )
        assert first.definition_pass_count == len(first.outcomes) - 1
        assert frozen.definition_pass_count == len(frozen.outcomes)
        mismatch = [item for item in first.outcomes if not item.definition_conforms]
        assert len(mismatch) == 1
        assert mismatch[0].sample_kind is (
            AscendingTriangleSampleKind.WEAK_CONVERGENCE
        )
        assert mismatch[0].false_positive is True
        assert frozen.parameters.require("minimum_contraction_pct") == 0.25


def test_structure_confirmation_remains_separate_from_direction_confirmation():
    result = execute_ascending_triangle_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        frozen = class_result.parameter_attempts[-1]
        clean = next(
            item
            for item in frozen.outcomes
            if item.sample_kind is AscendingTriangleSampleKind.CLEAN
        )
        assert clean.structure_confirmed is True
        assert clean.direction_pending is True
        assert clean.direction_confirmed is False
        assert frozen.structure_only_confirmed_count == 2
        assert frozen.direction_pending_count == 2
        assert frozen.direction_confirmed_count == 0


def test_structure_break_is_positive_geometry_then_technical_invalidation():
    result = execute_ascending_triangle_calibration_pilot()
    outcomes = tuple(
        item
        for class_result in (result.equity, result.fixed_income)
        for item in class_result.parameter_attempts[-1].outcomes
        if item.sample_kind is AscendingTriangleSampleKind.STRUCTURE_BROKEN
    )

    assert len(outcomes) == 2
    assert all(item.label is PatternReviewLabel.POSITIVE for item in outcomes)
    assert all(
        item.expected_state
        is AscendingTriangleExpectedState.STRUCTURE_CONFIRMED_THEN_INVALIDATED
        for item in outcomes
    )
    assert all(item.structure_confirmed for item in outcomes)
    assert all(item.direction_pending for item in outcomes)
    assert all(not item.direction_confirmed for item in outcomes)
    assert all(item.invalidated and item.definition_conforms for item in outcomes)


def test_frozen_negative_geometry_and_insufficient_history_fail_closed():
    result = execute_ascending_triangle_calibration_pilot()
    outcomes = tuple(
        item
        for class_result in (result.equity, result.fixed_income)
        for item in class_result.parameter_attempts[-1].outcomes
        if item.label is PatternReviewLabel.NEGATIVE
    )

    assert outcomes
    assert all(not item.structure_confirmed for item in outcomes)
    assert all(not item.direction_confirmed for item in outcomes)
    assert all(item.definition_conforms for item in outcomes)
    insufficient = tuple(
        item
        for item in outcomes
        if item.sample_kind is AscendingTriangleSampleKind.INSUFFICIENT_HISTORY
    )
    assert len(insufficient) == 2
    assert all(item.history_blocked for item in insufficient)


def test_parameter_freeze_is_exact_immutable_and_has_no_crypto_fallback():
    result = execute_ascending_triangle_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        final_attempt = class_result.parameter_attempts[-1]
        version = class_result.frozen_version
        assert version.calibration_key.calibration_version == (
            ASCENDING_TRIANGLE_PILOT_CALIBRATION_VERSION
        )
        assert version.parameters_hash == final_attempt.parameters.parameters_hash
        assert version.dataset_manifest_hash == class_result.manifest.manifest_hash
        assert version.attempt_count == 2
        assert final_attempt.parameters.require("calibration_stage") == (
            "pilot_frozen_not_production"
        )
        assert final_attempt.parameters.require("parameter_origin") == (
            "stage1d4_geometry_fixture_pilot"
        )
        registry = CalibrationRegistry((final_attempt.parameters,))
        with pytest.raises(CalibrationNotConfigured, match="BTC fallback are forbidden"):
            registry.resolve(
                CalibrationKey(
                    "CRYPTO",
                    "CRYPTO",
                    "1d",
                    "triangle",
                    "ascending_triangle",
                    "btc-v1",
                )
            )


def test_holdout_and_untouched_use_the_same_frozen_hashes_in_order():
    result = execute_ascending_triangle_calibration_pilot()

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


def test_validation_is_geometry_only_and_stops_at_governance_review():
    result = execute_ascending_triangle_calibration_pilot()

    forbidden = {
        "profit",
        "loss",
        "return",
        "win_rate",
        "rank",
        "probability",
        "future_price_direction",
    }
    assert forbidden.isdisjoint(
        {item.name for item in fields(AscendingTriangleSampleOutcome)}
    )
    for class_result in (result.equity, result.fixed_income):
        report = class_result.validation_report
        assessment = class_result.promotion_assessment
        assert report.positive_cases == 3
        assert report.negative_cases == 5
        assert report.ambiguous_cases == 0
        assert report.review_disagreement_cases == 0
        assert report.promotion_recommendation is (
            PromotionRecommendation.READY_FOR_GOVERNANCE_REVIEW
        )
        assert assessment.eligible_for_governance_review is True
        assert assessment.blocking_reasons == ()


def test_repeated_pilot_execution_is_hash_identical():
    first = execute_ascending_triangle_calibration_pilot()
    second = execute_ascending_triangle_calibration_pilot()

    assert first.dataset_manifest.manifest_hash == second.dataset_manifest.manifest_hash
    assert first.equity.frozen_version == second.equity.frozen_version
    assert first.fixed_income.frozen_version == second.fixed_income.frozen_version
    assert first.result_hash == second.result_hash
