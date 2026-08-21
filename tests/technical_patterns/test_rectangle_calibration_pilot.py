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
from backend.services.technical_patterns.calibration.rectangle_pilot import (
    RECTANGLE_PILOT_ADJUSTMENT_POLICY,
    RECTANGLE_PILOT_CALENDAR_VERSION,
    RECTANGLE_PILOT_CALIBRATION_VERSION,
    RECTANGLE_PILOT_DATASET_VERSION,
    RECTANGLE_PILOT_SOURCE_PROVIDER,
    REQUIRED_RECTANGLE_SAMPLE_KINDS,
    RectangleExpectedState,
    RectangleSampleKind,
    RectangleSampleOutcome,
    build_rectangle_calibration_dataset_manifest,
    execute_rectangle_calibration_pilot,
)


def test_rectangle_manifest_freezes_required_authority_and_source_fields():
    pilot = build_rectangle_calibration_dataset_manifest()

    assert pilot.dataset_version == RECTANGLE_PILOT_DATASET_VERSION
    assert len(pilot.manifest_hash) == 64
    assert pilot.equity.economic_asset_class == "EQUITY"
    assert pilot.fixed_income.economic_asset_class == "FIXED_INCOME"
    for manifest in (pilot.equity, pilot.fixed_income):
        assert manifest.market == "US"
        assert manifest.timeframe == "1d"
        assert manifest.pattern_family == "range"
        assert manifest.pattern_type == "rectangle"
        assert manifest.coverage_gaps() == ()
        for dataset in manifest.datasets:
            assert dataset.source_provider == RECTANGLE_PILOT_SOURCE_PROVIDER
            assert dataset.adjustment_policy == RECTANGLE_PILOT_ADJUSTMENT_POLICY
            assert dataset.calendar_version == RECTANGLE_PILOT_CALENDAR_VERSION
            assert len(dataset.source_bar_hash) == 64


def test_manifest_seals_holdout_and_untouched_labels_chronologically():
    pilot = build_rectangle_calibration_dataset_manifest()

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


def test_pilot_covers_assets_regimes_and_all_rectangle_cases():
    pilot = build_rectangle_calibration_dataset_manifest()

    assert {item.sample_kind for item in pilot.samples} == (
        REQUIRED_RECTANGLE_SAMPLE_KINDS
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
        assert development_kinds == REQUIRED_RECTANGLE_SAMPLE_KINDS


def test_development_exploration_removes_too_narrow_false_structure():
    result = execute_rectangle_calibration_pilot()

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
        assert mismatch[0].sample_kind is RectangleSampleKind.TOO_NARROW_RANGE
        assert mismatch[0].false_positive is True
        assert frozen.parameters.require("minimum_range_width_pct") == 2.0


def test_clean_rectangle_is_neutral_structure_and_direction_is_not_required():
    result = execute_rectangle_calibration_pilot()
    clean = tuple(
        item
        for class_result in (result.equity, result.fixed_income)
        for item in class_result.parameter_attempts[-1].outcomes
        if item.sample_kind is RectangleSampleKind.CLEAN_RECTANGLE
    )

    assert len(clean) == 2
    assert all(item.structure_confirmed for item in clean)
    assert all(item.neutral_direction for item in clean)
    assert all(item.direction_not_required for item in clean)
    assert all(item.definition_conforms for item in clean)


def test_negative_structure_cases_fail_closed_in_frozen_attempt():
    result = execute_rectangle_calibration_pilot()
    negative = tuple(
        item
        for class_result in (result.equity, result.fixed_income)
        for item in class_result.parameter_attempts[-1].outcomes
        if item.label is PatternReviewLabel.NEGATIVE
    )

    assert negative
    assert all(not item.structure_confirmed for item in negative)
    assert all(item.definition_conforms for item in negative)
    insufficient = tuple(
        item
        for item in negative
        if item.sample_kind is RectangleSampleKind.INSUFFICIENT_HISTORY
    )
    assert len(insufficient) == 2
    assert all(item.expected_state is RectangleExpectedState.HISTORY_BLOCKED for item in insufficient)
    assert all(item.history_blocked for item in insufficient)


def test_parameter_freeze_is_exact_immutable_and_has_no_crypto_fallback():
    result = execute_rectangle_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        final_attempt = class_result.parameter_attempts[-1]
        version = class_result.frozen_version
        assert version.calibration_key.calibration_version == (
            RECTANGLE_PILOT_CALIBRATION_VERSION
        )
        assert version.parameters_hash == final_attempt.parameters.parameters_hash
        assert version.dataset_manifest_hash == class_result.manifest.manifest_hash
        assert version.attempt_count == 2
        assert final_attempt.parameters.require("calibration_stage") == (
            "pilot_frozen_not_production"
        )
        assert final_attempt.parameters.require("parameter_origin") == (
            "stage1d3_definition_fixture_pilot"
        )
        registry = CalibrationRegistry((final_attempt.parameters,))
        with pytest.raises(CalibrationNotConfigured, match="BTC fallback are forbidden"):
            registry.resolve(
                CalibrationKey(
                    "CRYPTO",
                    "CRYPTO",
                    "1d",
                    "range",
                    "rectangle",
                    "btc-v1",
                )
            )


def test_holdout_and_untouched_validation_use_the_same_frozen_hashes():
    result = execute_rectangle_calibration_pilot()

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


def test_validation_is_definition_only_and_reaches_governance_review_gate():
    result = execute_rectangle_calibration_pilot()

    forbidden = {"profit", "loss", "return", "win_rate", "rank", "probability"}
    assert forbidden.isdisjoint({item.name for item in fields(RectangleSampleOutcome)})
    for class_result in (result.equity, result.fixed_income):
        report = class_result.validation_report
        assessment = class_result.promotion_assessment
        assert report.positive_cases == 2
        assert report.negative_cases == 4
        assert report.ambiguous_cases == 0
        assert report.review_disagreement_cases == 0
        assert report.promotion_recommendation is (
            PromotionRecommendation.READY_FOR_GOVERNANCE_REVIEW
        )
        assert assessment.detector_pass is True
        assert assessment.calibration_frozen is True
        assert assessment.holdout_pass is True
        assert assessment.untouched_validation_pass is True
        assert assessment.human_review_pass is True
        assert assessment.eligible_for_governance_review is True
        assert assessment.blocking_reasons == ()


def test_repeated_pilot_execution_is_hash_identical():
    first = execute_rectangle_calibration_pilot()
    second = execute_rectangle_calibration_pilot()

    assert first.dataset_manifest.manifest_hash == second.dataset_manifest.manifest_hash
    assert first.equity.frozen_version == second.equity.frozen_version
    assert first.fixed_income.frozen_version == second.fixed_income.frozen_version
    assert first.result_hash == second.result_hash
