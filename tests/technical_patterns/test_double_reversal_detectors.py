from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.services.technical_patterns.calibration import (
    CalibrationKey,
    CalibrationNotConfigured,
    CalibrationRegistry,
    DetectorParameterSet,
    US_DOUBLE_REVERSAL_DEVELOPMENT_VERSION,
    build_us_double_reversal_development_parameter_sets,
)
from backend.services.technical_patterns.core import CorePatternBar, PatternCoreInput
from backend.services.technical_patterns.core.identity import stable_hash, stable_id
from backend.services.technical_patterns.detectors import (
    DOUBLE_REVERSAL_DETECTOR_VERSION,
    DetectorFramework,
    DoubleBottomDetector,
    DoubleReversalDirectionConfirmation,
    DoubleReversalInvalidation,
    DoubleReversalStructureConfirmation,
    DoubleTopDetector,
)
from backend.services.technical_patterns.detectors.framework import InsufficientPatternHistory
from backend.services.technical_patterns.indicators import IndicatorKind, TalibIndicatorLayer


Row = tuple[float, float, float, float, float]


def _sessions(count: int) -> list[date]:
    sessions: list[date] = []
    current = date(2025, 1, 2)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current)
        current += timedelta(days=1)
    return sessions


def _base_values(pattern_type: str) -> list[Row]:
    if pattern_type == "double_top":
        return [
            (95.0, 96.0, 94.0, 95.0, 100.0),
            (92.0, 93.0, 90.0, 92.0, 100.0),
            (94.0, 95.0, 93.0, 94.0, 100.0),
            (100.0, 101.0, 99.0, 100.0, 100.0),
            (106.0, 107.0, 105.0, 106.0, 100.0),
            (108.0, 110.0, 107.0, 108.0, 100.0),
            (106.0, 107.0, 105.0, 106.0, 100.0),
            (103.0, 104.0, 102.0, 103.0, 100.0),
            (101.5, 102.5, 100.5, 101.5, 100.0),
            (102.0, 103.0, 100.0, 102.0, 100.0),
            (103.0, 104.0, 102.0, 103.0, 100.0),
            (106.0, 107.0, 105.0, 106.0, 100.0),
            (108.0, 108.5, 107.0, 108.0, 100.0),
            (107.0, 109.0, 106.0, 107.0, 100.0),
            (106.0, 107.0, 105.0, 106.0, 100.0),
        ]
    if pattern_type == "double_bottom":
        return [
            (105.0, 106.0, 104.0, 105.0, 100.0),
            (108.0, 110.0, 107.0, 108.0, 100.0),
            (106.0, 107.0, 105.0, 106.0, 100.0),
            (100.0, 101.0, 99.0, 100.0, 100.0),
            (94.0, 95.0, 93.0, 94.0, 100.0),
            (92.0, 93.0, 90.0, 92.0, 100.0),
            (94.0, 95.0, 93.0, 94.0, 100.0),
            (97.0, 98.0, 96.0, 97.0, 100.0),
            (98.5, 99.5, 97.5, 98.5, 100.0),
            (98.0, 100.0, 97.0, 98.0, 100.0),
            (97.0, 98.0, 96.0, 97.0, 100.0),
            (94.0, 95.0, 93.0, 94.0, 100.0),
            (92.0, 93.0, 91.5, 92.0, 100.0),
            (93.0, 94.0, 91.0, 93.0, 100.0),
            (94.0, 95.0, 93.0, 94.0, 100.0),
        ]
    raise ValueError(pattern_type)


def _continuation(pattern_type: str) -> Row:
    return (
        (105.0, 106.0, 104.0, 105.0, 100.0)
        if pattern_type == "double_top"
        else (95.0, 96.0, 94.0, 95.0, 100.0)
    )


def _core_input(
    pattern_type: str,
    values: list[Row] | None = None,
    *,
    count: int | None = None,
) -> PatternCoreInput:
    values = list(values or _base_values(pattern_type))
    count = count or len(values)
    while len(values) < count:
        values.append(_continuation(pattern_type))
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
                stable_id(
                    "bar",
                    {"fixture": pattern_type, "ordinal": ordinal, "row": row},
                ),
            )
        )
    source_hash = stable_hash(tuple(bars))
    return PatternCoreInput(
        f"fixture:{pattern_type}",
        400 if pattern_type == "double_top" else 401,
        "REVERSALFIXTURE",
        "DREV",
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


def _key(pattern_type: str) -> CalibrationKey:
    return CalibrationKey("TEST", "EQUITY", "1d", "reversal", pattern_type, "fixture-v1")


def _parameters(
    pattern_type: str,
    *,
    minimum_history: int = 15,
    expiry_sessions: int = 5,
    extreme_similarity_max_ratio: float = 0.025,
    minimum_intervening_reaction_ratio: float = 0.015,
) -> DetectorParameterSet:
    return DetectorParameterSet(
        _key(pattern_type),
        (
            ("boundary_tolerance_pct", 0.005),
            ("bottom_volume_ratio_minimum", 1.20),
            ("direction_break_margin_pct", 0.10),
            ("expiry_sessions", expiry_sessions),
            ("extreme_similarity_max_ratio", extreme_similarity_max_ratio),
            ("invalidation_buffer_pct", 0.10),
            ("maximum_structure_duration_sessions", 20),
            ("minimum_extreme_separation_sessions", 8),
            ("minimum_intervening_reaction_ratio", minimum_intervening_reaction_ratio),
            ("minimum_preceding_trend_ratio", 0.020),
            ("neckline_tolerance_pct", 0.25),
            ("pattern_type_contract", pattern_type),
            ("pivot_left_window_bars", 1),
            ("pivot_minimum_bar_separation", 0),
            ("pivot_minimum_price_separation_pct", 0.0),
            ("pivot_plateau_tolerance_pct", 0.0),
            ("pivot_right_confirmation_bars", 1),
            ("source_pivot_count", 4),
            ("volume_average_sessions", 5),
        ),
        minimum_history_bars=minimum_history,
    )


def _detector(pattern_type: str):
    return DoubleTopDetector() if pattern_type == "double_top" else DoubleBottomDetector()


def _run(
    pattern_type: str,
    core_input: PatternCoreInput,
    *,
    evaluation: int | None = None,
    parameters: DetectorParameterSet | None = None,
):
    parameters = parameters or _parameters(pattern_type)
    return DetectorFramework(
        calibrations=CalibrationRegistry((parameters,)),
        indicators=TalibIndicatorLayer(),
    ).run(
        core_input,
        evaluation_session_ordinal=(
            core_input.bars[-1].session_ordinal if evaluation is None else evaluation
        ),
        calibration_key=_key(pattern_type),
        detector=_detector(pattern_type),
        structure_confirmation=DoubleReversalStructureConfirmation(),
        direction_confirmation=DoubleReversalDirectionConfirmation(),
        invalidation=DoubleReversalInvalidation(),
    )


def _facts(result):
    return {
        item.code: item.value
        for item in result.candidate.geometry_facts + result.candidate.structure_facts
    }


@pytest.mark.parametrize(
    ("pattern_type", "direction", "extremes", "neckline", "role"),
    [
        ("double_top", "bearish", (110.0, 109.0), 100.0, "support"),
        ("double_bottom", "bullish", (90.0, 91.0), 100.0, "resistance"),
    ],
)
def test_four_confirmed_pivots_form_structure_without_inventing_direction(
    pattern_type, direction, extremes, neckline, role
):
    run = _run(pattern_type, _core_input(pattern_type))
    assert len(run.results) == 1
    result = run.results[0]
    facts = _facts(result)

    assert result.candidate.pattern_type.value == pattern_type
    assert result.candidate.direction.value == direction
    assert result.structure_confirmation.state.value == "confirmed"
    assert result.direction_confirmation.state.value == "pending"
    assert result.status == "candidate"
    assert result.candidate.direction_confirmation_required is True
    assert (facts["first_extreme_price"], facts["second_extreme_price"]) == extremes
    assert facts["neckline_price"] == neckline
    assert facts["neckline_boundary_role"] == role
    assert facts["extreme_separation_sessions"] == 8
    assert facts["technical_evidence_only"] is True
    assert len(result.candidate.source_pivots) == 4
    assert len(result.candidate.source_boundaries) == 1


def test_volume_indicator_contract_preserves_frozen_asymmetric_gate():
    for pattern_type in ("double_top", "double_bottom"):
        definitions = _detector(pattern_type).required_indicators(_parameters(pattern_type))
        assert len(definitions) == 1
        assert definitions[0].kind is IndicatorKind.SMA
        assert definitions[0].source == "volume"
        assert definitions[0].periods == (5,)
    assert _facts(_run("double_top", _core_input("double_top")).results[0])[
        "volume_confirmation_role"
    ] == "contextual"
    assert _facts(_run("double_bottom", _core_input("double_bottom")).results[0])[
        "volume_confirmation_role"
    ] == "required"


def test_later_downside_close_confirms_double_top_without_volume_gate():
    values = _base_values("double_top") + [(99.0, 100.0, 97.0, 98.0, 100.0)]
    result = _run("double_top", _core_input("double_top", values, count=16)).results[0]
    assert result.direction_confirmation.state.value == "confirmed"
    assert result.direction_confirmation.observed_session_ordinal == 15
    assert result.direction_confirmation.reason == "later_closed_session_broke_below_neckline"
    assert result.status == "confirmed"


def test_double_bottom_requires_both_upside_close_and_relative_volume():
    quiet = _base_values("double_bottom") + [(101.0, 103.0, 100.0, 102.0, 100.0)]
    loud = _base_values("double_bottom") + [(101.0, 103.0, 100.0, 102.0, 150.0)]
    quiet_result = _run(
        "double_bottom", _core_input("double_bottom", quiet, count=16)
    ).results[0]
    loud_result = _run(
        "double_bottom", _core_input("double_bottom", loud, count=16)
    ).results[0]

    assert quiet_result.direction_confirmation.state.value == "pending"
    assert quiet_result.status == "candidate"
    assert loud_result.direction_confirmation.state.value == "confirmed"
    assert loud_result.direction_confirmation.observed_session_ordinal == 15
    assert loud_result.status == "confirmed"
    volume_fact = next(
        item
        for item in loud_result.direction_confirmation.facts
        if item.code == "direction_confirmation_volume_ratio"
    )
    assert volume_fact.value == pytest.approx(1.5)


@pytest.mark.parametrize(
    ("pattern_type", "direction_event", "failure_event", "reason"),
    [
        (
            "double_top",
            (99.0, 100.0, 97.0, 98.0, 100.0),
            (100.5, 102.0, 99.5, 101.0, 100.0),
            "closed_session_recovered_above_neckline",
        ),
        (
            "double_bottom",
            (101.0, 103.0, 100.0, 102.0, 150.0),
            (99.5, 100.5, 98.0, 99.0, 100.0),
            "closed_session_failed_below_neckline",
        ),
    ],
)
def test_post_confirmation_neckline_invalidation_is_a_later_technical_fact(
    pattern_type, direction_event, failure_event, reason
):
    values = _base_values(pattern_type) + [direction_event, failure_event]
    result = _run(pattern_type, _core_input(pattern_type, values, count=17)).results[0]
    assert result.direction_confirmation.state.value == "confirmed"
    assert result.direction_confirmation.observed_session_ordinal == 15
    assert result.invalidation.invalidated is True
    assert result.invalidation.observed_session_ordinal == 16
    assert result.invalidation.reason == reason
    assert result.status == "invalidated"
    assert [item.to_state.value for item in result.lifecycle.transitions] == [
        "confirmed",
        "invalidated",
    ]


@pytest.mark.parametrize(
    ("pattern_type", "breach"),
    [
        ("double_top", (110.5, 112.0, 110.0, 111.0, 100.0)),
        ("double_bottom", (89.5, 90.0, 88.0, 89.0, 100.0)),
    ],
)
def test_pre_confirmation_extreme_breach_invalidates_structure(pattern_type, breach):
    values = _base_values(pattern_type) + [breach]
    result = _run(pattern_type, _core_input(pattern_type, values, count=16)).results[0]
    assert result.direction_confirmation.state.value == "pending"
    assert result.invalidation.reason == "closed_session_breached_extreme_structure"
    assert result.status == "invalidated"


def test_candidate_expires_by_session_ordinal_when_neckline_never_confirms():
    result = _run(
        "double_top",
        _core_input("double_top", count=20),
        evaluation=19,
    ).results[0]
    assert result.status == "expired"
    assert result.lifecycle.expired_session_ordinal == 19


@pytest.mark.parametrize("pattern_type", ["double_top", "double_bottom"])
def test_insufficient_history_and_missing_exact_calibration_fail_closed(pattern_type):
    with pytest.raises(InsufficientPatternHistory):
        _run(pattern_type, _core_input(pattern_type, count=14), evaluation=13)
    with pytest.raises(CalibrationNotConfigured):
        DetectorFramework(
            calibrations=CalibrationRegistry(), indicators=TalibIndicatorLayer()
        ).run(
            _core_input(pattern_type),
            evaluation_session_ordinal=14,
            calibration_key=_key(pattern_type),
            detector=_detector(pattern_type),
            structure_confirmation=DoubleReversalStructureConfirmation(),
            direction_confirmation=DoubleReversalDirectionConfirmation(),
            invalidation=DoubleReversalInvalidation(),
        )


@pytest.mark.parametrize("pattern_type", ["double_top", "double_bottom"])
def test_future_pivot_neckline_and_direction_facts_are_ignored(pattern_type):
    event = (
        (99.0, 100.0, 97.0, 98.0, 100.0)
        if pattern_type == "double_top"
        else (101.0, 103.0, 100.0, 102.0, 150.0)
    )
    full = _core_input(pattern_type, _base_values(pattern_type) + [event], count=16)
    truncated = replace(
        full,
        bars=full.bars[:15],
        last_closed_session=full.bars[14].session_date,
        source_bar_hash="independent-prefix-hash",
        dataset_version="independent-prefix-hash",
    )
    full_at_structure = _run(pattern_type, full, evaluation=14)
    truncated_at_structure = _run(pattern_type, truncated, evaluation=14)

    assert full_at_structure == truncated_at_structure
    assert full_at_structure.results[0].direction_confirmation.state.value == "pending"
    assert _run(pattern_type, full, evaluation=15).results[0].direction_confirmation.state.value == "confirmed"


@pytest.mark.parametrize("pattern_type", ["double_top", "double_bottom"])
def test_prefix_replay_and_repeated_execution_are_deterministic(pattern_type):
    core_input = _core_input(pattern_type)
    first = _run(pattern_type, core_input)
    second = _run(pattern_type, core_input)
    assert first == second
    assert first.result_hash == second.result_hash
    assert first.results[0].candidate.candidate_id == second.results[0].candidate.candidate_id
    assert first.results[0].result_hash == second.results[0].result_hash


@pytest.mark.parametrize("pattern_type", ["double_top", "double_bottom"])
def test_single_peak_or_valley_and_asymmetric_extremes_fail_closed(pattern_type):
    single = _core_input(pattern_type, _base_values(pattern_type)[:11], count=11)
    assert _run(
        pattern_type,
        single,
        evaluation=10,
        parameters=_parameters(pattern_type, minimum_history=11),
    ).results == ()

    asymmetric = _base_values(pattern_type)
    asymmetric[13] = (
        (116.0, 118.0, 115.0, 116.0, 100.0)
        if pattern_type == "double_top"
        else (77.0, 78.0, 75.0, 77.0, 100.0)
    )
    assert _run(pattern_type, _core_input(pattern_type, asymmetric)).results == ()


def test_shallow_neckline_weak_prior_trend_and_continuation_fail_closed():

    shallow = _base_values("double_top")
    shallow[8] = (109.0, 109.5, 108.9, 109.0, 100.0)
    shallow[9] = (109.0, 109.4, 108.8, 109.0, 100.0)
    shallow[10] = (109.0, 109.5, 108.9, 109.0, 100.0)
    assert _run("double_top", _core_input("double_top", shallow)).results == ()

    shallow_bottom = _base_values("double_bottom")
    shallow_bottom[6:11] = [
        (90.5, 90.8, 90.2, 90.5, 100.0),
        (90.7, 91.0, 90.4, 90.7, 100.0),
        (90.9, 91.1, 90.6, 90.9, 100.0),
        (91.0, 91.2, 90.7, 91.0, 100.0),
        (90.9, 91.1, 90.6, 90.9, 100.0),
    ]
    assert _run("double_bottom", _core_input("double_bottom", shallow_bottom)).results == ()

    weak_prior = _base_values("double_top")
    weak_prior[:5] = [
        (109.5, 109.8, 109.3, 109.5, 100.0),
        (109.2, 109.5, 109.0, 109.2, 100.0),
        (109.4, 109.7, 109.2, 109.4, 100.0),
        (109.5, 109.8, 109.3, 109.5, 100.0),
        (109.7, 109.9, 109.5, 109.7, 100.0),
    ]
    assert _run("double_top", _core_input("double_top", weak_prior)).results == ()

    trend = [
        (100.0 + index, 101.0 + index, 99.0 + index, 100.0 + index, 100.0)
        for index in range(15)
    ]
    assert _run("double_bottom", _core_input("double_bottom", trend)).results == ()


def test_us_calibration_registry_has_four_exact_non_crypto_bindings():
    parameter_sets = build_us_double_reversal_development_parameter_sets()
    registry = CalibrationRegistry(parameter_sets)
    assert len(parameter_sets) == 4
    for pattern_type in ("double_top", "double_bottom"):
        for asset_class in ("EQUITY", "FIXED_INCOME"):
            key = CalibrationKey(
                "US",
                asset_class,
                "1d",
                "reversal",
                pattern_type,
                US_DOUBLE_REVERSAL_DEVELOPMENT_VERSION,
            )
            parameters = registry.resolve(key)
            assert parameters.require("pattern_type_contract") == pattern_type
            assert parameters.require("parameter_origin") == (
                "wealthpilot_us_hypothesis_not_validated"
            )
    with pytest.raises(CalibrationNotConfigured, match="BTC fallback are forbidden"):
        registry.resolve(
            CalibrationKey(
                "CRYPTO",
                "CRYPTO",
                "1d",
                "reversal",
                "double_top",
                "btc-v1",
            )
        )


def test_missing_parameter_has_no_hidden_default_and_pattern_binding_is_exact():
    complete = _parameters("double_top")
    incomplete = DetectorParameterSet(
        complete.key,
        tuple(item for item in complete.values if item[0] != "neckline_tolerance_pct"),
        complete.minimum_history_bars,
    )
    with pytest.raises(CalibrationNotConfigured, match="neckline_tolerance_pct"):
        DoubleTopDetector().required_indicators(incomplete)
    mismatched = DetectorParameterSet(
        complete.key,
        tuple(
            (name, "double_bottom" if name == "pattern_type_contract" else value)
            for name, value in complete.values
        ),
        complete.minimum_history_bars,
    )
    with pytest.raises(CalibrationNotConfigured, match="pattern_type_contract"):
        DoubleTopDetector().required_indicators(mismatched)


def test_detector_package_remains_provider_product_and_fixed_clock_free():
    path = (
        Path(__file__).parents[2]
        / "backend/services/technical_patterns/detectors/double_reversal.py"
    )
    source = path.read_text(encoding="utf-8")
    assert DOUBLE_REVERSAL_DETECTOR_VERSION == "wp-double-reversal-detector-v1"
    assert "86400" not in source
    assert "timedelta(days=1)" not in source
    assert "backend.services.pattern_data" not in source
    assert "backend.services.action" not in source
    assert "import talib" not in source
