from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.services.technical_patterns.calibration import CalibrationKey, CalibrationRegistry, DetectorParameterSet
from backend.services.technical_patterns.core import CorePatternBar, PatternCoreInput
from backend.services.technical_patterns.core.identity import stable_hash, stable_id
from backend.services.technical_patterns.detectors import (
    BreakdownDetector,
    BreakoutDetector,
    DetectorFramework,
    LevelBreakDirectionConfirmation,
    LevelBreakInvalidation,
    LevelBreakStructureConfirmation,
)
from backend.services.technical_patterns.indicators import TalibIndicatorLayer


FIXTURE = Path(__file__).parent / "fixtures/tovest_tpg_v1_10_level_break_golden.json"


def _sessions(count: int) -> list[date]:
    sessions: list[date] = []
    current = date(2025, 1, 2)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current)
        current += timedelta(days=1)
    return sessions


def _values(pattern_type: str, ordinal: int) -> tuple[float, float, float, float, float]:
    if pattern_type == "breakout":
        if ordinal < 44:
            return 98.8, 99.2, 98.2, 98.8, 100.0
        if ordinal == 44:
            return 99.0, 100.0, 98.5, 99.0, 100.0
        if ordinal < 80:
            return 99.0, 99.4, 98.5, 99.0, 100.0
        if ordinal == 80:
            return 100.0, 103.2, 99.7, 102.8, 300.0
        return 100.0, 100.2, 99.8, 100.0, 100.0
    if ordinal < 44:
        return 112.0, 112.5, 111.5, 112.0, 100.0
    if ordinal < 80:
        close = 108.0 - (ordinal - 44) * 0.2
        return close + 0.1, close + 0.5, close - 0.5, close, 100.0
    if ordinal == 80:
        return 101.0, 101.3, 99.0, 99.4, 300.0
    return 100.2, 100.4, 100.0, 100.2, 100.0


def _parity_input(
    pattern_type: str,
    *,
    count: int = 81,
    invalidation_at: int | None = None,
) -> PatternCoreInput:
    bars = []
    for ordinal, session in enumerate(_sessions(count)):
        open_price, high, low, close, volume = _values(pattern_type, ordinal)
        if ordinal == invalidation_at:
            if pattern_type == "breakout":
                open_price, high, low, close = 99.4, 99.8, 98.8, 99.0
            else:
                open_price, high, low, close = 101.2, 101.8, 101.0, 101.5
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
                stable_id("bar", {"fixture": "tovest-level-break", "type": pattern_type, "ordinal": ordinal}),
            )
        )
    source_hash = stable_hash(tuple(bars))
    return PatternCoreInput(
        f"fixture:tovest:{pattern_type}",
        937,
        "TOVESTFIXTURE",
        "FIXTURE",
        "TEST",
        "USD",
        "America/New_York",
        "1d",
        "split_adjusted",
        "fixture-us-session-mapping-v1",
        bars[-1].session_date,
        source_hash,
        source_hash,
        tuple(bars),
    )


def _parity_key(pattern_type: str) -> CalibrationKey:
    return CalibrationKey("TEST", "CODE_REGRESSION_ONLY", "1d", "level_break", pattern_type, "tovest-tpg-v1.10")


def _parity_parameters(pattern_type: str) -> DetectorParameterSet:
    return DetectorParameterSet(
        _parity_key(pattern_type),
        (
            ("atr_margin_multiplier", 0.12 if pattern_type == "breakout" else 0.0),
            ("decisive_margin_pct", 0.04 if pattern_type == "breakout" else 0.0),
            ("expiry_sessions", 12),
            ("invalidation_buffer_pct", 0.5 if pattern_type == "breakout" else 0.12),
            ("lookback_bars", 36),
            ("minimum_boundary_age_sessions", 1),
            ("minimum_boundary_touches", 1),
            ("zone_atr_width_multiplier", 0.15),
            ("zone_width_pct", 0.08),
            ("volume_average_bars", 20),
            ("volume_ratio_threshold", 1.7),
        ),
        minimum_history_bars=50,
    )


def _run_parity(
    pattern_type: str,
    *,
    evaluation: int = 80,
    invalidation_at: int | None = None,
):
    detector = BreakoutDetector() if pattern_type == "breakout" else BreakdownDetector()
    return DetectorFramework(
        calibrations=CalibrationRegistry((_parity_parameters(pattern_type),)),
        indicators=TalibIndicatorLayer(),
    ).run(
        _parity_input(pattern_type, count=evaluation + 1, invalidation_at=invalidation_at),
        evaluation_session_ordinal=evaluation,
        calibration_key=_parity_key(pattern_type),
        detector=detector,
        structure_confirmation=LevelBreakStructureConfirmation(),
        direction_confirmation=LevelBreakDirectionConfirmation(),
        invalidation=LevelBreakInvalidation(),
    )


def _parity_contract(result):
    facts = {item.code: item.value for item in result.candidate.geometry_facts + result.candidate.structure_facts}
    return {
        "available_from_session_ordinal": result.candidate.available_from_session_ordinal,
        "boundary_available_ordinal": result.candidate.source_boundaries[0].available_from_session_ordinal,
        "source_boundary_count": len(result.candidate.source_boundaries),
        "source_boundary_id": result.candidate.source_boundaries[0].source_id,
        "source_pivot_count": len(result.candidate.source_pivots),
        "boundary_axis": facts["boundary_axis"],
        "boundary_authoritative": facts["boundary_authoritative"],
        "boundary_zone_high": facts["boundary_zone_high"],
        "boundary_zone_low": facts["boundary_zone_low"],
        "break_close": facts["break_close"],
        "break_edge": facts["break_edge"],
        "break_threshold": facts["break_threshold"],
        "candidate_hash": stable_hash(result.candidate),
        "candidate_id": result.candidate.candidate_id,
        "direction": result.candidate.direction.value,
        "direction_confirmation": result.direction_confirmation.state.value,
        "ema_direction_aligned": facts["ema_direction_aligned"],
        "formed_session_ordinal": result.candidate.formed_session_ordinal,
        "invalidation_boundary": facts["invalidation_boundary"],
        "lifecycle_transitions": [item.to_state.value for item in result.lifecycle.transitions],
        "pattern_type": result.candidate.pattern_type.value,
        "result_hash": result.result_hash,
        "status": result.status,
        "structure_confirmation": result.structure_confirmation.state.value,
        "structure_observed_session_ordinal": result.structure_confirmation.observed_session_ordinal,
        "structure_reason": result.structure_confirmation.reason,
        "direction_observed_session_ordinal": result.direction_confirmation.observed_session_ordinal,
        "direction_reason": result.direction_confirmation.reason,
        "volume_confirmed": facts["volume_confirmed"],
        "volume_ratio": facts["volume_ratio"],
    }


@pytest.mark.parametrize("pattern_type", ["breakout", "breakdown"])
def test_frozen_tovest_oracle_maps_to_deterministic_wealthpilot_contract(pattern_type):
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = _run_parity(pattern_type).results[0]
    actual = _parity_contract(result)
    oracle = golden["tovest_oracle"][pattern_type]

    assert golden["source_freeze"]["commit"] == "937edb62727f4d8c36d41b36e93521d077da20f9"
    assert actual == golden["wealthpilot_expected"][pattern_type]
    assert actual["pattern_type"] == oracle["pattern_type"]
    assert actual["status"] == oracle["status"]
    assert actual["boundary_axis"] == pytest.approx(oracle["boundary_axis"])
    assert actual["boundary_zone_low"] == pytest.approx(oracle["boundary_zone_low"], abs=1e-8)
    assert actual["boundary_zone_high"] == pytest.approx(oracle["boundary_zone_high"], abs=1e-8)
    assert actual["break_close"] == pytest.approx(oracle["break_close"])
    assert actual["volume_ratio"] == pytest.approx(oracle["volume_ratio"])
    assert actual["volume_confirmed"] is oracle["volume_confirmed"]
    if pattern_type == "breakout":
        assert actual["boundary_authoritative"] is oracle["authority_confirmed"]
    else:
        assert actual["ema_direction_aligned"] is oracle["trend_aligned"]
    assert actual["invalidation_boundary"] == pytest.approx(oracle["invalidation_boundary"])


@pytest.mark.parametrize("pattern_type", ["breakout", "breakdown"])
def test_frozen_level_break_lifecycle_maps_invalidation_and_session_expiry(pattern_type):
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    invalidated = _run_parity(pattern_type, evaluation=81, invalidation_at=81).results[0]
    expired = _run_parity(pattern_type, evaluation=92).results[0]

    assert {
        "candidate_id": invalidated.candidate.candidate_id,
        "status": invalidated.status,
        "observed_session_ordinal": invalidated.invalidation.observed_session_ordinal,
        "transitions": [item.to_state.value for item in invalidated.lifecycle.transitions],
        "result_hash": invalidated.result_hash,
    } == golden["wealthpilot_lifecycle_expected"][pattern_type]["invalidated"]
    assert {
        "candidate_id": expired.candidate.candidate_id,
        "status": expired.status,
        "expired_session_ordinal": expired.lifecycle.expired_session_ordinal,
        "transitions": [item.to_state.value for item in expired.lifecycle.transitions],
        "result_hash": expired.result_hash,
    } == golden["wealthpilot_lifecycle_expected"][pattern_type]["expired"]
