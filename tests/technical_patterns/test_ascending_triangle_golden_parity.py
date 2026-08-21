from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.technical_patterns.core.identity import stable_hash
from tests.technical_patterns.test_ascending_triangle_detector import (
    _core_input,
    _facts,
    _parameters,
    _run,
)


FIXTURE = (
    Path(__file__).parent
    / "fixtures/tovest_tpg_v1_10_ascending_triangle_golden.json"
)

_BASE_VALUES = [
    (105.0, 106.0, 104.0, 105.0, 100.0),
    (108.0, 110.0, 107.0, 108.0, 100.0),
    (106.0, 107.0, 105.0, 106.0, 100.0),
    (104.0, 105.0, 103.0, 104.0, 100.0),
    (102.0, 103.0, 101.0, 102.0, 100.0),
    (101.0, 102.0, 100.0, 101.0, 100.0),
    (103.0, 104.0, 102.0, 103.0, 100.0),
    (106.0, 107.0, 105.0, 106.0, 100.0),
    (108.0, 109.0, 107.0, 108.0, 100.0),
    (108.0, 110.1, 107.0, 108.0, 100.0),
    (108.0, 109.0, 107.0, 108.0, 100.0),
    (107.0, 108.0, 106.0, 107.0, 100.0),
    (106.0, 107.0, 105.0, 106.0, 100.0),
    (105.0, 106.0, 104.0, 105.0, 100.0),
    (107.0, 108.0, 106.0, 107.0, 100.0),
]


def _parity_values(
    count: int,
    *,
    event: str | None = None,
) -> list[tuple[float, float, float, float, float]]:
    values = list(_BASE_VALUES)
    while len(values) < count:
        ordinal = len(values)
        upper = 109.9875 + 0.0125 * ordinal
        lower = 97.5 + 0.5 * ordinal
        close = (upper + lower) / 2.0
        values.append((close, close + 0.3, close - 0.3, close, 100.0))
    if event == "breakout":
        values[15] = (111.0, 112.0, 110.0, 111.5, 100.0)
    elif event == "invalidation":
        values[15] = (104.0, 105.0, 103.0, 104.0, 100.0)
    return values


def _parity_run(count: int, *, event: str | None = None):
    return _run(
        _core_input(_parity_values(count, event=event), count=count),
        evaluation=count - 1,
        parameters=_parameters(
            minimum_history=15,
            expiry_sessions=5,
        ),
    ).results[0]


def _contract(result):
    facts = _facts(result)
    return {
        "available_from_session_ordinal": result.candidate.available_from_session_ordinal,
        "formed_session_ordinal": result.candidate.formed_session_ordinal,
        "pattern_type": result.candidate.pattern_type.value,
        "direction": result.candidate.direction.value,
        "status": result.status,
        "candidate_id": result.candidate.candidate_id,
        "candidate_hash": stable_hash(result.candidate),
        "result_hash": result.result_hash,
        "source_pivot_ids": [item.source_id for item in result.candidate.source_pivots],
        "source_boundary_ids": [item.source_id for item in result.candidate.source_boundaries],
        "source_pivot_count": len(result.candidate.source_pivots),
        "source_boundary_count": len(result.candidate.source_boundaries),
        "upper_slope_per_session": facts["upper_slope_per_session"],
        "upper_intercept": facts["upper_intercept"],
        "lower_slope_per_session": facts["lower_slope_per_session"],
        "lower_intercept": facts["lower_intercept"],
        "upper_slope_pct_per_session": facts["upper_slope_pct_per_session"],
        "lower_slope_pct_per_session": facts["lower_slope_pct_per_session"],
        "upper_fit_error_pct": facts["upper_fit_error_pct"],
        "lower_fit_error_pct": facts["lower_fit_error_pct"],
        "start_gap": facts["start_gap"],
        "confirmed_gap": facts["confirmed_gap"],
        "contraction_pct": facts["contraction_pct"],
        "apex_session_ordinal": facts["apex_session_ordinal"],
        "apex_span_sessions": facts["apex_session_ordinal"]
        - result.candidate.formed_session_ordinal,
        "apex_progress_at_confirmation": facts["apex_progress_at_confirmation"],
        "resistance_at_confirmation": facts["resistance_at_confirmation"],
        "support_at_confirmation": facts["support_at_confirmation"],
        "structure_span_sessions": facts["structure_span_sessions"],
        "touch_sequence": facts["touch_sequence"],
        "resistance_touch_count": facts["resistance_touch_count"],
        "support_touch_count": facts["support_touch_count"],
        "structure_confirmation": result.structure_confirmation.state.value,
        "structure_reason": result.structure_confirmation.reason,
        "structure_observed_session_ordinal": result.structure_confirmation.observed_session_ordinal,
        "direction_confirmation": result.direction_confirmation.state.value,
        "direction_reason": result.direction_confirmation.reason,
        "direction_observed_session_ordinal": result.direction_confirmation.observed_session_ordinal,
        "lifecycle_transitions": [item.to_state.value for item in result.lifecycle.transitions],
    }


def test_frozen_tovest_geometry_maps_to_session_ordinal_structure_contract():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    oracle = golden["tovest_oracle"]
    actual = _contract(_parity_run(15))

    assert golden["source_freeze"]["commit"] == (
        "937edb62727f4d8c36d41b36e93521d077da20f9"
    )
    assert actual == golden["wealthpilot_expected"]["confirmed_structure"]
    assert actual["pattern_type"] == oracle["pattern_type"]
    assert actual["upper_slope_per_session"] == pytest.approx(oracle["upper_slope_per_bar"])
    assert actual["lower_slope_per_session"] == pytest.approx(oracle["lower_slope_per_bar"])
    assert actual["upper_slope_pct_per_session"] == pytest.approx(
        oracle["upper_slope_pct_per_bar"]
    )
    assert actual["lower_slope_pct_per_session"] == pytest.approx(
        oracle["lower_slope_pct_per_bar"]
    )
    assert actual["contraction_pct"] == pytest.approx(oracle["contraction_pct"])
    assert actual["apex_span_sessions"] == pytest.approx(oracle["apex_bar_offset"])
    assert actual["apex_progress_at_confirmation"] == pytest.approx(
        oracle["apex_progress_at_confirmation"]
    )
    assert actual["structure_span_sessions"] == oracle["structure_span_bars"]
    assert actual["source_pivot_count"] == len(oracle["source_pivot_ids"])
    assert actual["source_boundary_count"] == len(oracle["source_boundary_ids"])
    assert actual["structure_confirmation"] == "confirmed"
    assert actual["direction_confirmation"] == oracle["confirmed_structure"][
        "directional_confirmation"
    ]


def test_frozen_direction_confirmation_maps_without_collapsing_structure_layer():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    oracle = golden["tovest_oracle"]["breakout"]
    result = _parity_run(16, event="breakout")

    assert {
        "candidate_id": result.candidate.candidate_id,
        "status": result.status,
        "direction_confirmation": result.direction_confirmation.state.value,
        "direction_reason": result.direction_confirmation.reason,
        "direction_observed_session_ordinal": result.direction_confirmation.observed_session_ordinal,
        "transitions": [item.to_state.value for item in result.lifecycle.transitions],
        "result_hash": result.result_hash,
    } == golden["wealthpilot_expected"]["breakout_confirmed"]
    assert oracle["status"] == "breakout_confirmed"
    assert oracle["directional_confirmation"] == "bullish_confirmed"
    assert result.structure_confirmation.state.value == "confirmed"
    assert result.direction_confirmation.state.value == "confirmed"


def test_frozen_invalidation_and_wealthpilot_session_expiry_map_explicitly():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    invalidated = _parity_run(16, event="invalidation")
    expired = _parity_run(20)

    assert {
        "candidate_id": invalidated.candidate.candidate_id,
        "status": invalidated.status,
        "observed_session_ordinal": invalidated.invalidation.observed_session_ordinal,
        "reason": invalidated.invalidation.reason,
        "transitions": [item.to_state.value for item in invalidated.lifecycle.transitions],
        "result_hash": invalidated.result_hash,
    } == golden["wealthpilot_expected"]["invalidated"]
    assert invalidated.status == golden["tovest_oracle"]["invalidated"]["status"]
    assert {
        "candidate_id": expired.candidate.candidate_id,
        "status": expired.status,
        "expired_session_ordinal": expired.lifecycle.expired_session_ordinal,
        "transitions": [item.to_state.value for item in expired.lifecycle.transitions],
        "result_hash": expired.result_hash,
    } == golden["wealthpilot_expected"]["expired"]
