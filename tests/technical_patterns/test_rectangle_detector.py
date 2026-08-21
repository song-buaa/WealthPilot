from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from backend.services.technical_patterns.calibration import (
    CalibrationKey,
    CalibrationNotConfigured,
    CalibrationRegistry,
    DetectorParameterSet,
    US_RECTANGLE_DEVELOPMENT_VERSION,
    build_us_rectangle_development_parameter_sets,
)
from backend.services.technical_patterns.core import CorePatternBar, PatternCoreInput
from backend.services.technical_patterns.core.identity import stable_hash, stable_id
from backend.services.technical_patterns.detectors import (
    DetectorFramework,
    RectangleDetector,
    RectangleInvalidation,
    RectangleStructureConfirmation,
)
from backend.services.technical_patterns.detectors.framework import InsufficientPatternHistory
from backend.services.technical_patterns.indicators import TalibIndicatorLayer


def _sessions(count: int) -> list[date]:
    sessions: list[date] = []
    current = date(2025, 1, 2)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current)
        current += timedelta(days=1)
    return sessions


def _rectangle_values(
    *,
    support_first: float = 100.0,
    support_second: float = 100.0,
    resistance: float = 110.0,
) -> list[tuple[float, float, float, float, float]]:
    return [
        (105.0, 106.0, 104.0, 105.0, 100.0),
        (102.0, 103.0, support_first, 102.0, 100.0),
        (105.0, 106.0, 104.0, 105.0, 100.0),
        (resistance - 2.0, resistance, resistance - 3.0, resistance - 2.0, 100.0),
        (105.0, 106.0, 104.0, 105.0, 100.0),
        (102.0, 103.0, support_second, 102.0, 100.0),
        (105.0, 106.0, 104.0, 105.0, 100.0),
        (resistance - 2.0, resistance, resistance - 3.0, resistance - 2.0, 100.0),
        (105.0, 106.0, 104.0, 105.0, 100.0),
    ]


def _core_input(
    values: list[tuple[float, float, float, float, float]] | None = None,
    *,
    count: int | None = None,
    invalidation_at: int | None = None,
) -> PatternCoreInput:
    values = list(values or _rectangle_values())
    count = count or len(values)
    while len(values) < count:
        values.append((105.0, 106.0, 104.0, 105.0, 100.0))
    if invalidation_at is not None:
        while len(values) <= invalidation_at:
            values.append((105.0, 106.0, 104.0, 105.0, 100.0))
        values[invalidation_at] = (111.0, 112.0, 109.0, 111.5, 100.0)
        count = max(count, invalidation_at + 1)
    bars = []
    for ordinal, (session, row) in enumerate(zip(_sessions(count), values[:count])):
        open_price, high, low, close, volume = row
        bars.append(
            CorePatternBar(
                session,
                ordinal,
                session,
                open_price,
                high,
                low,
                close,
                volume,
                stable_id("bar", {"fixture": "rectangle", "ordinal": ordinal, "row": row}),
            )
        )
    source_hash = stable_hash(tuple(bars))
    return PatternCoreInput(
        "fixture:rectangle",
        314,
        "RECTANGLEFIXTURE",
        "RECT",
        "TEST",
        "USD",
        "America/New_York",
        "1d",
        "split_adjusted",
        "fixture-us-sessions-v1",
        bars[-1].session_date,
        source_hash,
        source_hash,
        tuple(bars),
    )


def _key() -> CalibrationKey:
    return CalibrationKey("TEST", "EQUITY", "1d", "range", "rectangle", "fixture-v1")


def _parameters(
    *,
    minimum_history: int = 9,
    expiry_sessions: int = 5,
    boundary_tolerance_pct: float = 0.005,
    maximum_boundary_zone_width_pct: float = 1.0,
    minimum_range_width_pct: float = 5.0,
    maximum_range_width_pct: float = 15.0,
) -> DetectorParameterSet:
    return DetectorParameterSet(
        _key(),
        (
            ("boundary_tolerance_pct", boundary_tolerance_pct),
            ("expiry_sessions", expiry_sessions),
            ("invalidation_buffer_pct", 0.0),
            ("maximum_boundary_zone_width_pct", maximum_boundary_zone_width_pct),
            ("maximum_range_width_pct", maximum_range_width_pct),
            ("minimum_range_width_pct", minimum_range_width_pct),
            ("minimum_structure_span_sessions", 3),
            ("minimum_touches_per_side", 2),
            ("pivot_left_window_bars", 1),
            ("pivot_minimum_bar_separation", 0),
            ("pivot_minimum_price_separation_pct", 0.0),
            ("pivot_plateau_tolerance_pct", 0.0),
            ("pivot_right_confirmation_bars", 1),
        ),
        minimum_history_bars=minimum_history,
    )


def _run(
    core_input: PatternCoreInput,
    *,
    evaluation: int = 8,
    parameters: DetectorParameterSet | None = None,
):
    parameters = parameters or _parameters()
    return DetectorFramework(
        calibrations=CalibrationRegistry((parameters,)),
        indicators=TalibIndicatorLayer(),
    ).run(
        core_input,
        evaluation_session_ordinal=evaluation,
        calibration_key=_key(),
        detector=RectangleDetector(),
        structure_confirmation=RectangleStructureConfirmation(),
        invalidation=RectangleInvalidation(),
    )


def _facts(result):
    return {
        item.code: item.value
        for item in result.candidate.geometry_facts + result.candidate.structure_facts
    }


def test_clear_rectangle_is_confirmed_neutral_structure_with_no_direction_requirement():
    run = _run(_core_input())
    assert len(run.results) == 1
    result = run.results[0]
    facts = _facts(result)

    assert result.candidate.pattern_type.value == "rectangle"
    assert result.candidate.direction.value == "neutral"
    assert result.candidate.direction_confirmation_required is False
    assert result.direction_confirmation.state.value == "not_required"
    assert result.structure_confirmation.state.value == "confirmed"
    assert result.status == "confirmed"
    assert facts["range_low"] == 100.0
    assert facts["range_high"] == 110.0
    assert facts["range_width"] == 10.0
    assert facts["support_touch_count"] == facts["resistance_touch_count"] == 2
    assert facts["touch_sequence"] == "SRSR"
    assert facts["structure_span_sessions"] == 6
    assert len(result.candidate.source_pivots) == 4
    assert len(result.candidate.source_boundaries) == 2


def test_rectangle_declares_no_indicator_dependencies():
    detector = RectangleDetector()
    assert detector.required_indicators(_parameters()) == ()
    assert detector.descriptor.detector_version == "wp-rectangle-detector-v1"


@pytest.mark.parametrize(
    "values,parameters",
    [
        (
            [(100.0 + index, 101.0 + index, 99.0 + index, 100.5 + index, 100.0) for index in range(9)],
            _parameters(),
        ),
        (
            _rectangle_values(support_first=100.0, support_second=100.8),
            _parameters(boundary_tolerance_pct=0.01, maximum_boundary_zone_width_pct=0.5),
        ),
        (_rectangle_values(resistance=130.0), _parameters(maximum_range_width_pct=15.0)),
        (_rectangle_values(), _parameters(minimum_range_width_pct=11.0)),
        (
            [
                (105.0, 106.0, 104.0, 105.0, 100.0),
                (102.0, 103.0, 100.0, 102.0, 100.0),
                (105.0, 106.0, 104.0, 105.0, 100.0),
                (108.0, 110.0, 107.0, 108.0, 100.0),
                (105.0, 106.0, 104.0, 105.0, 100.0),
                (97.0, 99.0, 95.0, 97.0, 100.0),
                (105.0, 106.0, 104.0, 105.0, 100.0),
                (108.0, 110.0, 107.0, 108.0, 100.0),
                (105.0, 106.0, 104.0, 105.0, 100.0),
            ],
            _parameters(),
        ),
    ],
    ids=["single_direction_trend", "unstable_boundary", "range_too_wide", "range_too_narrow", "non_rectangle"],
)
def test_negative_structures_fail_closed(values, parameters):
    assert _run(_core_input(values), parameters=parameters).results == ()


def test_touches_insufficient_until_second_resistance_is_confirmed():
    full = _core_input()
    parameters = _parameters(minimum_history=7)
    before = _run(full, evaluation=6, parameters=parameters)
    after = _run(full, evaluation=8, parameters=parameters)
    assert before.results == ()
    assert len(after.results) == 1


def test_insufficient_history_and_missing_calibration_fail_closed():
    with pytest.raises(InsufficientPatternHistory):
        _run(_core_input(count=8), evaluation=7)
    with pytest.raises(CalibrationNotConfigured):
        DetectorFramework(calibrations=CalibrationRegistry(), indicators=TalibIndicatorLayer()).run(
            _core_input(),
            evaluation_session_ordinal=8,
            calibration_key=_key(),
            detector=RectangleDetector(),
            structure_confirmation=RectangleStructureConfirmation(),
            invalidation=RectangleInvalidation(),
        )


def test_future_bar_boundary_and_touch_are_ignored_by_causal_prefix():
    full = _core_input(count=10, invalidation_at=9)
    truncated = replace(
        full,
        bars=full.bars[:9],
        last_closed_session=full.bars[8].session_date,
        source_bar_hash="independent-prefix-hash",
        dataset_version="independent-prefix-hash",
    )
    full_run = _run(full, evaluation=8)
    truncated_run = _run(truncated, evaluation=8)
    assert full_run == truncated_run
    assert full_run.results[0].candidate.candidate_id == truncated_run.results[0].candidate.candidate_id
    assert full_run.result_hash == truncated_run.result_hash


def test_boundary_break_invalidates_as_technical_fact():
    run = _run(_core_input(count=10, invalidation_at=9), evaluation=9)
    result = next(item for item in run.results if item.candidate.available_from_session_ordinal == 8)
    assert result.status == "invalidated"
    assert result.invalidation.observed_session_ordinal == 9
    assert result.invalidation.reason == "closed_session_above_resistance"


def test_lower_boundary_break_invalidates_as_technical_fact():
    values = _rectangle_values() + [(99.0, 100.0, 98.0, 99.0, 100.0)]
    run = _run(_core_input(values, count=10), evaluation=9)
    result = next(item for item in run.results if item.candidate.available_from_session_ordinal == 8)
    assert result.status == "invalidated"
    assert result.invalidation.observed_session_ordinal == 9
    assert result.invalidation.reason == "closed_session_below_support"


def test_rectangle_expires_by_session_ordinal_without_direction_semantics():
    run = _run(_core_input(count=14), evaluation=13, parameters=_parameters(expiry_sessions=5))
    result = next(item for item in run.results if item.candidate.available_from_session_ordinal == 8)
    assert result.status == "expired"
    assert result.lifecycle.expired_session_ordinal == 13
    assert result.direction_confirmation.state.value == "not_required"


def test_repeated_execution_has_stable_identity_source_hash_and_result_hash():
    core_input = _core_input()
    first = _run(core_input)
    second = _run(core_input)
    assert first == second
    assert first.result_hash == second.result_hash
    assert first.results[0].candidate.source_bar_hash == second.results[0].candidate.source_bar_hash


def test_us_rectangle_calibrations_are_exact_and_have_no_btc_fallback():
    parameter_sets = build_us_rectangle_development_parameter_sets()
    registry = CalibrationRegistry(parameter_sets)
    assert len(parameter_sets) == 2
    for asset_class in ("EQUITY", "FIXED_INCOME"):
        key = CalibrationKey(
            "US", asset_class, "1d", "range", "rectangle", US_RECTANGLE_DEVELOPMENT_VERSION
        )
        assert registry.resolve(key).require("parameter_origin") == "wealthpilot_us_hypothesis_not_validated"
    with pytest.raises(CalibrationNotConfigured, match="BTC fallback are forbidden"):
        registry.resolve(CalibrationKey("CRYPTO", "CRYPTO", "1d", "range", "rectangle", "btc-v1"))


def test_missing_rectangle_parameter_has_no_hidden_default():
    complete = _parameters()
    incomplete = DetectorParameterSet(
        complete.key,
        tuple(item for item in complete.values if item[0] != "minimum_touches_per_side"),
        complete.minimum_history_bars,
    )
    with pytest.raises(CalibrationNotConfigured, match="minimum_touches_per_side"):
        RectangleDetector().required_indicators(incomplete)
