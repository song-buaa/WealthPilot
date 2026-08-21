from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from backend.services.technical_patterns.calibration import (
    CalibrationKey,
    CalibrationNotConfigured,
    CalibrationRegistry,
    DetectorParameterSet,
    US_ASCENDING_TRIANGLE_DEVELOPMENT_VERSION,
    build_us_ascending_triangle_development_parameter_sets,
)
from backend.services.technical_patterns.core import CorePatternBar, PatternCoreInput
from backend.services.technical_patterns.core.identity import stable_hash, stable_id
from backend.services.technical_patterns.detectors import (
    AscendingTriangleDetector,
    AscendingTriangleDirectionConfirmation,
    AscendingTriangleInvalidation,
    AscendingTriangleStructureConfirmation,
    DetectorFramework,
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


def _triangle_values(
    *,
    first_resistance: float = 110.0,
    second_resistance: float = 110.1,
    first_support: float = 100.0,
    second_support: float = 104.0,
) -> list[tuple[float, float, float, float, float]]:
    return [
        (105.0, 106.0, 104.0, 105.0, 100.0),
        (108.0, first_resistance, 107.0, 108.0, 100.0),
        (105.0, 106.0, 104.0, 105.0, 100.0),
        (102.0, 103.0, first_support, 102.0, 100.0),
        (106.0, 107.0, 105.0, 106.0, 100.0),
        (108.0, second_resistance, 107.0, 108.0, 100.0),
        (107.0, 108.0, 106.0, 107.0, 100.0),
        (105.0, 106.0, second_support, 105.0, 100.0),
        (108.0, 109.0, 107.0, 108.0, 100.0),
    ]


def _continuation(ordinal: int) -> tuple[float, float, float, float, float]:
    upper = 109.975 + 0.025 * ordinal
    lower = 97.0 + ordinal
    close = (upper + lower) / 2.0
    return close, close + 0.4, close - 0.4, close, 100.0


def _core_input(
    values: list[tuple[float, float, float, float, float]] | None = None,
    *,
    count: int | None = None,
    event_at: int | None = None,
    event: str | None = None,
) -> PatternCoreInput:
    values = list(values or _triangle_values())
    count = count or len(values)
    while len(values) < count:
        values.append(_continuation(len(values)))
    if event_at is not None:
        while len(values) <= event_at:
            values.append(_continuation(len(values)))
        if event == "breakout":
            values[event_at] = (111.0, 112.0, 110.0, 111.5, 100.0)
        elif event == "support_break":
            values[event_at] = (105.0, 106.0, 104.0, 105.0, 100.0)
        elif event == "weak_breakout":
            values[event_at] = (110.1, 110.4, 109.8, 110.25, 100.0)
        else:
            raise ValueError("event must name a deterministic fixture event")
        count = max(count, event_at + 1)
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
                stable_id("bar", {"fixture": "ascending-triangle", "ordinal": ordinal, "row": row}),
            )
        )
    source_hash = stable_hash(tuple(bars))
    return PatternCoreInput(
        "fixture:ascending-triangle",
        315,
        "TRIANGLEFIXTURE",
        "ATRI",
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
    return CalibrationKey(
        "TEST", "EQUITY", "1d", "triangle", "ascending_triangle", "fixture-v1"
    )


def _parameters(
    *,
    minimum_history: int = 9,
    expiry_sessions: int = 5,
    boundary_tolerance_pct: float = 0.002,
    maximum_apex_horizon_sessions: int = 80,
    maximum_resistance_zone_width_pct: float = 1.0,
    support_min_slope_pct_per_session: float = 0.00012,
) -> DetectorParameterSet:
    return DetectorParameterSet(
        _key(),
        (
            ("boundary_tolerance_pct", boundary_tolerance_pct),
            ("breakout_close_margin_pct", 0.1),
            ("containment_tolerance_pct", 1.0),
            ("expiry_sessions", expiry_sessions),
            ("horizontal_resistance_max_slope_pct_per_session", 0.0005),
            ("horizontal_to_support_max_slope_ratio", 0.5),
            ("invalidation_buffer_pct", 0.1),
            ("maximum_apex_horizon_sessions", maximum_apex_horizon_sessions),
            ("maximum_apex_progress_at_confirmation", 0.9),
            ("maximum_line_fit_error_pct", 0.01),
            ("maximum_resistance_zone_width_pct", maximum_resistance_zone_width_pct),
            ("maximum_source_pivots", 8),
            ("minimum_apex_progress", 0.15),
            ("minimum_contraction_pct", 0.12),
            ("minimum_source_pivots", 4),
            ("minimum_structure_span_sessions", 6),
            ("minimum_touches_per_side", 2),
            ("pivot_left_window_bars", 1),
            ("pivot_minimum_bar_separation", 0),
            ("pivot_minimum_price_separation_pct", 0.0),
            ("pivot_plateau_tolerance_pct", 0.0),
            ("pivot_right_confirmation_bars", 1),
            ("support_min_slope_pct_per_session", support_min_slope_pct_per_session),
        ),
        minimum_history_bars=minimum_history,
    )


def _run(
    core_input: PatternCoreInput,
    *,
    evaluation: int | None = None,
    parameters: DetectorParameterSet | None = None,
):
    parameters = parameters or _parameters()
    return DetectorFramework(
        calibrations=CalibrationRegistry((parameters,)),
        indicators=TalibIndicatorLayer(),
    ).run(
        core_input,
        evaluation_session_ordinal=(
            core_input.bars[-1].session_ordinal if evaluation is None else evaluation
        ),
        calibration_key=_key(),
        detector=AscendingTriangleDetector(),
        structure_confirmation=AscendingTriangleStructureConfirmation(),
        direction_confirmation=AscendingTriangleDirectionConfirmation(),
        invalidation=AscendingTriangleInvalidation(),
    )


def _facts(result):
    return {
        item.code: item.value
        for item in result.candidate.geometry_facts + result.candidate.structure_facts
    }


def test_clean_ascending_triangle_confirms_structure_without_inventing_breakout():
    result = _run(_core_input()).results[0]
    facts = _facts(result)

    assert result.candidate.pattern_type.value == "ascending_triangle"
    assert result.candidate.direction.value == "bullish"
    assert result.candidate.direction_confirmation_required is True
    assert result.structure_confirmation.state.value == "confirmed"
    assert result.direction_confirmation.state.value == "pending"
    assert result.status == "candidate"
    assert facts["horizontal_resistance_confirmed"] is True
    assert facts["rising_support_confirmed"] is True
    assert facts["convergence_confirmed"] is True
    assert facts["touch_sequence"] == "RSRS"
    assert facts["structure_span_sessions"] == 6
    assert len(result.candidate.source_pivots) == 4
    assert len(result.candidate.source_boundaries) == 1


def test_geometry_uses_session_ordinals_and_stable_resistance_boundary():
    result = _run(_core_input()).results[0]
    facts = _facts(result)

    assert facts["upper_slope_per_session"] == pytest.approx(0.025)
    assert facts["lower_slope_per_session"] == pytest.approx(1.0)
    assert facts["contraction_pct"] == pytest.approx(0.4875)
    assert facts["apex_session_ordinal"] == pytest.approx(13.3076923077)
    assert facts["apex_progress_at_confirmation"] == pytest.approx(0.4875)
    assert facts["resistance_touch_count"] == facts["support_touch_count"] == 2
    assert facts["resistance_boundary_stable"] is True


def test_decisive_later_close_confirms_direction_and_lifecycle():
    run = _run(_core_input(count=10, event_at=9, event="breakout"), evaluation=9)
    result = next(item for item in run.results if item.candidate.available_from_session_ordinal == 8)
    assert result.structure_confirmation.state.value == "confirmed"
    assert result.direction_confirmation.state.value == "confirmed"
    assert result.direction_confirmation.observed_session_ordinal == 9
    assert result.direction_confirmation.reason == "closed_session_close_cleared_resistance"
    assert result.status == "confirmed"
    assert [item.to_state.value for item in result.lifecycle.transitions] == ["confirmed"]


def test_close_without_decisive_margin_does_not_confirm_direction():
    result = _run(
        _core_input(count=10, event_at=9, event="weak_breakout"), evaluation=9
    ).results[0]
    assert result.structure_confirmation.state.value == "confirmed"
    assert result.direction_confirmation.state.value == "pending"
    assert result.status == "candidate"


def test_ascending_triangle_declares_no_indicator_dependencies():
    detector = AscendingTriangleDetector()
    assert detector.required_indicators(_parameters()) == ()
    assert detector.descriptor.detector_version == "wp-ascending-triangle-detector-v1"


@pytest.mark.parametrize(
    "values,parameters",
    [
        (_triangle_values(second_support=100.0), _parameters()),
        (_triangle_values(second_support=99.0), _parameters()),
        (
            _triangle_values(second_support=100.01),
            _parameters(support_min_slope_pct_per_session=0.00012),
        ),
        (
            _triangle_values(second_resistance=112.0),
            _parameters(
                boundary_tolerance_pct=0.03,
                maximum_resistance_zone_width_pct=1.0,
            ),
        ),
        (
            _triangle_values(first_resistance=114.0, second_resistance=110.0),
            _parameters(boundary_tolerance_pct=0.05),
        ),
    ],
    ids=[
        "rectangle_parallel",
        "descending_support",
        "weak_support_slope",
        "unstable_resistance",
        "non_ascending_triangle",
    ],
)
def test_negative_geometry_fails_closed(values, parameters):
    assert _run(_core_input(values), parameters=parameters).results == ()


def test_insufficient_pivots_and_meaningless_apex_fail_closed():
    assert _run(
        _core_input(count=7),
        evaluation=6,
        parameters=_parameters(minimum_history=7),
    ).results == ()
    assert _run(
        _core_input(),
        parameters=_parameters(maximum_apex_horizon_sessions=4),
    ).results == ()


def test_insufficient_history_and_missing_calibration_fail_closed():
    with pytest.raises(InsufficientPatternHistory):
        _run(_core_input(count=8), evaluation=7)
    with pytest.raises(CalibrationNotConfigured):
        DetectorFramework(
            calibrations=CalibrationRegistry(), indicators=TalibIndicatorLayer()
        ).run(
            _core_input(),
            evaluation_session_ordinal=8,
            calibration_key=_key(),
            detector=AscendingTriangleDetector(),
            structure_confirmation=AscendingTriangleStructureConfirmation(),
            direction_confirmation=AscendingTriangleDirectionConfirmation(),
            invalidation=AscendingTriangleInvalidation(),
        )


def test_future_pivot_boundary_touch_and_confirmation_are_ignored():
    full = _core_input(count=11, event_at=9, event="breakout")
    truncated = replace(
        full,
        bars=full.bars[:9],
        last_closed_session=full.bars[8].session_date,
        source_bar_hash="independent-prefix-hash",
        dataset_version="independent-prefix-hash",
    )
    full_at_structure = _run(full, evaluation=8)
    truncated_at_structure = _run(truncated, evaluation=8)

    assert full_at_structure == truncated_at_structure
    assert full_at_structure.results[0].direction_confirmation.state.value == "pending"
    assert _run(full, evaluation=9).results[0].direction_confirmation.state.value == "confirmed"


def test_lower_trendline_break_invalidates_as_technical_fact():
    run = _run(_core_input(count=10, event_at=9, event="support_break"), evaluation=9)
    result = next(item for item in run.results if item.candidate.available_from_session_ordinal == 8)
    assert result.status == "invalidated"
    assert result.invalidation.observed_session_ordinal == 9
    assert result.invalidation.reason == "closed_session_below_rising_support"


def test_apex_without_resistance_break_invalidates_as_technical_fact():
    run = _run(
        _core_input(count=15),
        evaluation=14,
        parameters=_parameters(expiry_sessions=20),
    )
    result = next(item for item in run.results if item.candidate.available_from_session_ordinal == 8)
    assert result.status == "invalidated"
    assert result.invalidation.observed_session_ordinal == 14
    assert result.invalidation.reason == "apex_reached_without_resistance_break"


def test_structure_expires_by_session_ordinal_without_breakout():
    run = _run(
        _core_input(count=12),
        evaluation=11,
        parameters=_parameters(expiry_sessions=3),
    )
    result = next(item for item in run.results if item.candidate.available_from_session_ordinal == 8)
    assert result.status == "expired"
    assert result.lifecycle.expired_session_ordinal == 11
    assert result.direction_confirmation.state.value == "pending"


def test_repeated_execution_has_stable_identity_source_hash_and_result_hash():
    core_input = _core_input(count=10, event_at=9, event="breakout")
    first = _run(core_input, evaluation=9)
    second = _run(core_input, evaluation=9)
    assert first == second
    assert len(first.results) == 1
    assert first.result_hash == second.result_hash
    assert first.results[0].candidate.source_bar_hash == second.results[0].candidate.source_bar_hash


def test_us_ascending_triangle_calibrations_are_exact_without_btc_fallback():
    parameter_sets = build_us_ascending_triangle_development_parameter_sets()
    registry = CalibrationRegistry(parameter_sets)
    assert len(parameter_sets) == 2
    for asset_class in ("EQUITY", "FIXED_INCOME"):
        key = CalibrationKey(
            "US",
            asset_class,
            "1d",
            "triangle",
            "ascending_triangle",
            US_ASCENDING_TRIANGLE_DEVELOPMENT_VERSION,
        )
        assert registry.resolve(key).require("parameter_origin") == (
            "wealthpilot_us_hypothesis_not_validated"
        )
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


def test_missing_triangle_parameter_has_no_hidden_default():
    complete = _parameters()
    incomplete = DetectorParameterSet(
        complete.key,
        tuple(
            item
            for item in complete.values
            if item[0] != "support_min_slope_pct_per_session"
        ),
        complete.minimum_history_bars,
    )
    with pytest.raises(CalibrationNotConfigured, match="support_min_slope_pct_per_session"):
        AscendingTriangleDetector().required_indicators(incomplete)
