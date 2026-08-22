from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta

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
from backend.services.technical_patterns.calibration.double_bottom_pilot import (
    DOUBLE_BOTTOM_PILOT_ADJUSTMENT_POLICY,
    DOUBLE_BOTTOM_PILOT_CALENDAR_VERSION,
    DOUBLE_BOTTOM_PILOT_CALIBRATION_VERSION,
    DOUBLE_BOTTOM_PILOT_DATASET_VERSION,
    DOUBLE_BOTTOM_PILOT_SOURCE_PROVIDER,
    REQUIRED_DOUBLE_BOTTOM_SAMPLE_KINDS,
    DoubleBottomExpectedState,
    DoubleBottomSampleKind,
    DoubleBottomSampleOutcome,
    build_double_bottom_calibration_dataset_manifest,
    build_double_bottom_pilot_parameters,
    build_double_bottom_pilot_samples,
    execute_double_bottom_calibration_pilot,
)
from backend.services.technical_patterns.core import CorePatternBar
from backend.services.technical_patterns.core.identity import stable_hash, stable_id
from backend.services.technical_patterns.detectors import (
    DetectorFramework,
    DoubleBottomDetector,
    DoubleReversalDirectionConfirmation,
    DoubleReversalInvalidation,
    DoubleReversalStructureConfirmation,
)
from backend.services.technical_patterns.indicators import TalibIndicatorLayer


def test_manifest_freezes_required_authority_and_source_fields():
    pilot = build_double_bottom_calibration_dataset_manifest()

    assert pilot.dataset_version == DOUBLE_BOTTOM_PILOT_DATASET_VERSION
    assert len(pilot.manifest_hash) == 64
    for manifest in (pilot.equity, pilot.fixed_income):
        assert manifest.market == "US"
        assert manifest.timeframe == "1d"
        assert manifest.pattern_family == "reversal"
        assert manifest.pattern_type == "double_bottom"
        assert manifest.coverage_gaps() == ()
        for dataset in manifest.datasets:
            assert dataset.source_provider == DOUBLE_BOTTOM_PILOT_SOURCE_PROVIDER
            assert dataset.adjustment_policy == DOUBLE_BOTTOM_PILOT_ADJUSTMENT_POLICY
            assert dataset.calendar_version == DOUBLE_BOTTOM_PILOT_CALENDAR_VERSION
            assert len(dataset.source_bar_hash) == 64


def test_manifest_seals_holdout_and_untouched_labels_chronologically():
    pilot = build_double_bottom_calibration_dataset_manifest()

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


def test_development_covers_assets_regimes_and_all_reversal_volume_cases():
    pilot = build_double_bottom_calibration_dataset_manifest()

    assert {item.sample_kind for item in pilot.samples} == (
        REQUIRED_DOUBLE_BOTTOM_SAMPLE_KINDS
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
        assert {
            item.sample_kind
            for item in pilot.samples
            if item.economic_asset_class == asset_class
            and item.partition is CalibrationPartition.DEVELOPMENT
        } == REQUIRED_DOUBLE_BOTTOM_SAMPLE_KINDS


def test_development_exploration_removes_weak_volume_direction_confirmation():
    result = execute_double_bottom_calibration_pilot()

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
            DoubleBottomSampleKind.BREAKOUT_INSUFFICIENT_VOLUME
        )
        assert mismatch[0].direction_confirmed is True
        assert mismatch[0].confirmation_volume_ratio == pytest.approx(1.1)
        assert frozen.parameters.require("bottom_volume_ratio_minimum") == 1.20


def test_same_price_breakout_requires_the_independent_volume_hard_gate():
    result = execute_double_bottom_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        frozen = class_result.parameter_attempts[-1]
        sufficient = next(
            item
            for item in frozen.outcomes
            if item.sample_kind
            is DoubleBottomSampleKind.BREAKOUT_SUFFICIENT_VOLUME
        )
        insufficient = next(
            item
            for item in frozen.outcomes
            if item.sample_kind
            is DoubleBottomSampleKind.BREAKOUT_INSUFFICIENT_VOLUME
        )
        assert sufficient.structure_confirmed and sufficient.direction_confirmed
        assert sufficient.confirmation_volume_ratio == pytest.approx(1.5)
        assert insufficient.structure_confirmed and insufficient.direction_pending
        assert insufficient.direction_confirmed is False
        assert insufficient.volume_gate_blocked and insufficient.definition_conforms
        assert frozen.volume_gate_blocked_count == 1


def _run_input(core_input, parameters, evaluation_ordinal: int):
    return DetectorFramework(
        calibrations=CalibrationRegistry((parameters,)),
        indicators=TalibIndicatorLayer(),
    ).run(
        core_input,
        evaluation_session_ordinal=evaluation_ordinal,
        calibration_key=parameters.key,
        detector=DoubleBottomDetector(),
        structure_confirmation=DoubleReversalStructureConfirmation(),
        direction_confirmation=DoubleReversalDirectionConfirmation(),
        invalidation=DoubleReversalInvalidation(),
    )


def test_volume_baseline_excludes_current_bar_and_future_volume():
    sample = next(
        item
        for item in build_double_bottom_pilot_samples()
        if item.economic_asset_class == "EQUITY"
        and item.partition is CalibrationPartition.DEVELOPMENT
        and item.sample_kind is DoubleBottomSampleKind.BREAKOUT_SUFFICIENT_VOLUME
    )
    parameters = build_double_bottom_pilot_parameters("EQUITY", attempt_number=2)
    closed_input = sample.core_input()
    confirmation_ordinal = closed_input.bars[-1].session_ordinal
    confirmed = _run_input(closed_input, parameters, confirmation_ordinal)
    ratio = next(
        fact.value
        for fact in confirmed.results[0].direction_confirmation.facts
        if fact.code == "direction_confirmation_volume_ratio"
    )
    assert ratio == pytest.approx(1.5)

    future_date = closed_input.bars[-1].session_date + timedelta(days=1)
    while future_date.weekday() >= 5:
        future_date += timedelta(days=1)
    future_bar = CorePatternBar(
        session_date=future_date,
        session_ordinal=confirmation_ordinal + 1,
        available_from=future_date,
        open=102.0,
        high=103.0,
        low=101.0,
        close=102.0,
        volume=10_000.0,
        bar_id=stable_id(
            "bar",
            {"fixture": "future-volume-must-be-ignored", "session": future_date},
        ),
    )
    future_bars = closed_input.bars + (future_bar,)
    future_hash = stable_hash(future_bars)
    with_future = replace(
        closed_input,
        bars=future_bars,
        last_closed_session=future_date,
        source_bar_hash=future_hash,
        dataset_version=future_hash,
    )
    replay = _run_input(with_future, parameters, confirmation_ordinal)
    replay_ratio = next(
        fact.value
        for fact in replay.results[0].direction_confirmation.facts
        if fact.code == "direction_confirmation_volume_ratio"
    )
    assert replay_ratio == pytest.approx(1.5)
    assert replay.results[0].direction_confirmation == (
        confirmed.results[0].direction_confirmation
    )


def test_structure_direction_and_lifecycle_facts_remain_separate():
    result = execute_double_bottom_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        frozen = class_result.parameter_attempts[-1]
        pending = next(
            item
            for item in frozen.outcomes
            if item.sample_kind is DoubleBottomSampleKind.DIRECTION_PENDING
        )
        pre = next(
            item
            for item in frozen.outcomes
            if item.sample_kind
            is DoubleBottomSampleKind.PRE_CONFIRMATION_INVALIDATED
        )
        post = next(
            item
            for item in frozen.outcomes
            if item.sample_kind
            is DoubleBottomSampleKind.POST_CONFIRMATION_INVALIDATED
        )
        assert pending.structure_confirmed and pending.direction_pending
        assert pending.direction_confirmed is False
        assert pre.structure_confirmed and pre.direction_pending
        assert pre.pre_confirmation_invalidated and pre.definition_conforms
        assert post.structure_confirmed and post.direction_confirmed
        assert post.post_confirmation_invalidated and post.definition_conforms
        assert frozen.structure_only_confirmed_count == 3
        assert frozen.direction_pending_count == 4
        assert frozen.direction_confirmed_count == 2
        assert frozen.pre_confirmation_invalidation_count == 1
        assert frozen.post_confirmation_invalidation_count == 1


def test_negative_definitions_and_insufficient_history_fail_closed():
    result = execute_double_bottom_calibration_pilot()
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
        if item.sample_kind is DoubleBottomSampleKind.INSUFFICIENT_HISTORY
    )
    assert len(insufficient) == 2
    assert all(item.history_blocked for item in insufficient)


def test_parameter_freeze_is_exact_immutable_hard_gate_without_crypto_fallback():
    result = execute_double_bottom_calibration_pilot()

    for class_result in (result.equity, result.fixed_income):
        final_attempt = class_result.parameter_attempts[-1]
        version = class_result.frozen_version
        assert version.calibration_key.calibration_version == (
            DOUBLE_BOTTOM_PILOT_CALIBRATION_VERSION
        )
        assert version.parameters_hash == final_attempt.parameters.parameters_hash
        assert version.dataset_manifest_hash == class_result.manifest.manifest_hash
        assert version.attempt_count == 2
        assert final_attempt.parameters.require("calibration_stage") == (
            "pilot_frozen_not_production"
        )
        assert final_attempt.parameters.require("parameter_origin") == (
            "stage1d6_volume_fixture_pilot"
        )
        assert final_attempt.parameters.require("volume_role") == (
            "hard_confirmation_gate"
        )
        assert final_attempt.parameters.require("volume_average_sessions") == 5
        registry = CalibrationRegistry((final_attempt.parameters,))
        with pytest.raises(CalibrationNotConfigured, match="BTC fallback are forbidden"):
            registry.resolve(
                CalibrationKey(
                    "CRYPTO",
                    "CRYPTO",
                    "1d",
                    "reversal",
                    "double_bottom",
                    "btc-v1",
                )
            )


def test_holdout_and_untouched_use_the_same_frozen_hashes_in_order():
    result = execute_double_bottom_calibration_pilot()

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


def test_validation_is_definition_only_and_stops_at_governance_review():
    result = execute_double_bottom_calibration_pilot()
    forbidden = {
        "profit",
        "loss",
        "return",
        "win_rate",
        "rank",
        "probability",
        "future_upside_magnitude",
        "long_trade_outcome",
    }

    assert forbidden.isdisjoint(
        {item.name for item in fields(DoubleBottomSampleOutcome)}
    )
    for class_result in (result.equity, result.fixed_income):
        report = class_result.validation_report
        assessment = class_result.promotion_assessment
        assert report.positive_cases == 4
        assert report.negative_cases == 4
        assert report.ambiguous_cases == 0
        assert report.review_disagreement_cases == 0
        assert report.promotion_recommendation is (
            PromotionRecommendation.READY_FOR_GOVERNANCE_REVIEW
        )
        assert assessment.eligible_for_governance_review is True
        assert assessment.blocking_reasons == ()


def test_repeated_pilot_execution_is_hash_identical():
    first = execute_double_bottom_calibration_pilot()
    second = execute_double_bottom_calibration_pilot()

    assert first.dataset_manifest.manifest_hash == second.dataset_manifest.manifest_hash
    assert first.equity.frozen_version == second.equity.frozen_version
    assert first.fixed_income.frozen_version == second.fixed_income.frozen_version
    assert first.result_hash == second.result_hash
