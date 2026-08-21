from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from backend.services.technical_patterns.calibration import (
    CalibrationPartition,
    DatasetReviewStatus,
    PatternReviewLabel,
    PromotionRecommendation,
)
from backend.services.technical_patterns.calibration.breakout_pilot import (
    BREAKOUT_PILOT_ADJUSTMENT_POLICY,
    BREAKOUT_PILOT_CALENDAR_VERSION,
    BREAKOUT_PILOT_CALIBRATION_VERSION,
    BREAKOUT_PILOT_DATASET_VERSION,
    BREAKOUT_PILOT_SOURCE_PROVIDER,
    REQUIRED_BREAKOUT_SAMPLE_KINDS,
    BreakoutExpectedState,
    BreakoutSampleKind,
    build_breakout_calibration_dataset_manifest,
    execute_breakout_calibration_pilot,
)


def test_breakout_manifest_freezes_required_authority_and_source_fields():
    pilot = build_breakout_calibration_dataset_manifest()

    assert pilot.dataset_version == BREAKOUT_PILOT_DATASET_VERSION
    assert len(pilot.manifest_hash) == 64
    assert pilot.equity.economic_asset_class == "EQUITY"
    assert pilot.fixed_income.economic_asset_class == "FIXED_INCOME"
    for manifest in (pilot.equity, pilot.fixed_income):
        assert manifest.market == "US"
        assert manifest.timeframe == "1d"
        assert manifest.pattern_family == "level_break"
        assert manifest.pattern_type == "breakout"
        assert manifest.coverage_gaps() == ()
        assert len(manifest.manifest_hash) == 64
        for dataset in manifest.datasets:
            assert dataset.source_provider == BREAKOUT_PILOT_SOURCE_PROVIDER
            assert dataset.adjustment_policy == BREAKOUT_PILOT_ADJUSTMENT_POLICY
            assert dataset.calendar_version == BREAKOUT_PILOT_CALENDAR_VERSION
            assert len(dataset.source_bar_hash) == 64
            assert dataset.date_range[0] < dataset.date_range[1]


def test_manifest_seals_holdout_and_untouched_labels_until_review():
    pilot = build_breakout_calibration_dataset_manifest()

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


def test_pilot_covers_assets_regimes_and_all_six_breakout_definition_cases():
    pilot = build_breakout_calibration_dataset_manifest()

    assert {item.sample_kind for item in pilot.samples} == REQUIRED_BREAKOUT_SAMPLE_KINDS
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


def test_development_exploration_fixes_definition_false_positive_only_from_development():
    result = execute_breakout_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        first, frozen = class_result.parameter_attempts
        assert first.attempt.based_on_partitions == (CalibrationPartition.DEVELOPMENT,)
        assert frozen.attempt.based_on_partitions == (CalibrationPartition.DEVELOPMENT,)
        assert first.definition_pass_count == len(first.outcomes) - 1
        assert frozen.definition_pass_count == len(frozen.outcomes)
        first_mismatch = [item for item in first.outcomes if not item.definition_conforms]
        assert len(first_mismatch) == 1
        assert first_mismatch[0].sample_kind is BreakoutSampleKind.INSUFFICIENT_STRUCTURE
        assert first_mismatch[0].false_positive is True
        assert frozen.parameters.require("minimum_boundary_touches") == 2


def test_parameter_freeze_binds_immutable_key_parameter_and_dataset_hashes():
    result = execute_breakout_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        frozen_attempt = class_result.parameter_attempts[-1]
        version = class_result.frozen_version
        assert version.calibration_key.calibration_version == (
            BREAKOUT_PILOT_CALIBRATION_VERSION
        )
        assert version.parameters_hash == frozen_attempt.parameters.parameters_hash
        assert version.dataset_manifest_hash == class_result.manifest.manifest_hash
        assert version.attempt_count == 2
        assert frozen_attempt.parameters.require("calibration_stage") == (
            "pilot_frozen_not_production"
        )


def test_holdout_and_untouched_validation_pass_in_strict_order():
    result = execute_breakout_calibration_pilot()

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


def test_failed_breakout_is_positive_structure_then_technical_invalidation():
    result = execute_breakout_calibration_pilot()
    outcomes = tuple(
        item
        for class_result in (result.equity, result.fixed_income)
        for attempt in class_result.parameter_attempts[-1:]
        for item in attempt.outcomes
        if item.sample_kind is BreakoutSampleKind.FAILED_BREAKOUT
    )

    assert outcomes
    assert all(item.label is PatternReviewLabel.POSITIVE for item in outcomes)
    assert all(
        item.expected_state is BreakoutExpectedState.CONFIRMED_THEN_INVALIDATED
        for item in outcomes
    )
    assert all(item.direction_confirmed and item.invalidated for item in outcomes)
    assert all(item.definition_conforms for item in outcomes)


def test_label_distribution_and_promotion_are_definition_focused_only():
    result = execute_breakout_calibration_pilot()

    assert result.equity.validation_report.positive_cases == 3
    assert result.equity.validation_report.negative_cases == 3
    assert result.fixed_income.validation_report.positive_cases == 2
    assert result.fixed_income.validation_report.negative_cases == 2
    for class_result in (result.equity, result.fixed_income):
        assessment = class_result.promotion_assessment
        report = class_result.validation_report
        assert assessment.detector_pass is True
        assert assessment.calibration_frozen is True
        assert assessment.holdout_pass is True
        assert assessment.untouched_validation_pass is True
        assert assessment.human_review_pass is True
        assert assessment.eligible_for_governance_review is True
        assert assessment.blocking_reasons == ()
        assert report.promotion_recommendation is (
            PromotionRecommendation.READY_FOR_GOVERNANCE_REVIEW
        )


def test_repeated_pilot_execution_is_hash_identical():
    first = execute_breakout_calibration_pilot()
    second = execute_breakout_calibration_pilot()

    assert first.dataset_manifest.manifest_hash == second.dataset_manifest.manifest_hash
    assert first.equity.frozen_version == second.equity.frozen_version
    assert first.fixed_income.frozen_version == second.fixed_income.frozen_version
    assert first.result_hash == second.result_hash


def test_pilot_contract_has_no_financial_outcome_or_product_integration_fields():
    forbidden_fields = {"profit", "loss", "return", "win_rate", "ranking", "probability"}
    outcome_fields = {
        item.name
        for item in fields(
            execute_breakout_calibration_pilot().equity.parameter_attempts[-1].outcomes[0]
        )
    }
    assert not forbidden_fields & outcome_fields

    source = (
        Path(__file__).parents[2]
        / "backend/services/technical_patterns/calibration/breakout_pilot.py"
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
