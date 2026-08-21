from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.technical_patterns.test_double_reversal_detectors import (
    _base_values,
    _core_input,
    _facts,
    _run,
)


FIXTURE = (
    Path(__file__).parent
    / "fixtures/tovest_tpg_v1_10_double_reversal_golden.json"
)


def _scenario(pattern_type: str, name: str):
    base = _base_values(pattern_type)
    direction = (
        (99.0, 100.0, 97.0, 98.0, 100.0)
        if pattern_type == "double_top"
        else (101.0, 103.0, 100.0, 102.0, 150.0)
    )
    failure = (
        (100.5, 102.0, 99.5, 101.0, 100.0)
        if pattern_type == "double_top"
        else (99.5, 100.5, 98.0, 99.0, 100.0)
    )
    if name == "structure":
        return _run(pattern_type, _core_input(pattern_type)).results[0]
    if name == "confirmed":
        return _run(
            pattern_type, _core_input(pattern_type, base + [direction], count=16)
        ).results[0]
    if name == "neckline_invalidated":
        return _run(
            pattern_type,
            _core_input(pattern_type, base + [direction, failure], count=17),
        ).results[0]
    if name == "expired":
        return _run(
            pattern_type, _core_input(pattern_type, count=20), evaluation=19
        ).results[0]
    raise ValueError(name)


def _expected_contract(result, name: str):
    common = {
        "candidate_id": result.candidate.candidate_id,
        "result_hash": result.result_hash,
        "status": result.status,
    }
    if name == "structure":
        return {
            **common,
            "direction_confirmation": result.direction_confirmation.state.value,
            "available_from_session_ordinal": result.candidate.available_from_session_ordinal,
        }
    if name == "confirmed":
        return {
            **common,
            "direction_confirmation": result.direction_confirmation.state.value,
            "direction_observed_session_ordinal": result.direction_confirmation.observed_session_ordinal,
            "transitions": [item.to_state.value for item in result.lifecycle.transitions],
        }
    if name == "neckline_invalidated":
        return {
            **common,
            "reason": result.invalidation.reason,
            "observed_session_ordinal": result.invalidation.observed_session_ordinal,
            "transitions": [item.to_state.value for item in result.lifecycle.transitions],
        }
    return {
        **common,
        "expired_session_ordinal": result.lifecycle.expired_session_ordinal,
        "transitions": [item.to_state.value for item in result.lifecycle.transitions],
    }


@pytest.mark.parametrize("pattern_type", ["double_top", "double_bottom"])
def test_frozen_four_pivot_geometry_maps_to_wealthpilot_structure_contract(pattern_type):
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    oracle = golden["tovest_oracle"][pattern_type]
    result = _scenario(pattern_type, "structure")
    facts = _facts(result)

    assert golden["source_freeze"]["commit"] == (
        "937edb62727f4d8c36d41b36e93521d077da20f9"
    )
    assert _expected_contract(result, "structure") == golden["wealthpilot_expected"][
        pattern_type
    ]["structure"]
    assert result.candidate.pattern_type.value == oracle["pattern_type"]
    assert result.candidate.direction.value == oracle["direction"]
    assert facts["extreme_similarity_ratio"] == pytest.approx(
        oracle["extreme_similarity_ratio"]
    )
    assert facts["intervening_reaction_ratio"] == pytest.approx(
        oracle["intervening_reaction_ratio"]
    )
    assert facts["preceding_trend_ratio"] == pytest.approx(
        oracle["preceding_trend_ratio"]
    )
    assert facts["neckline_price"] == oracle["neckline_price"]
    assert facts["invalidation_boundary_price"] == oracle["invalidation_level"]
    assert facts["measured_move_reference"] == oracle["measured_move_reference"]
    assert facts["volume_confirmation_role"] == oracle["volume_role"]
    assert result.structure_confirmation.state.value == "confirmed"
    assert result.direction_confirmation.state.value == "pending"


@pytest.mark.parametrize("pattern_type", ["double_top", "double_bottom"])
def test_frozen_direction_semantics_map_without_collapsing_structure_and_direction(pattern_type):
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    result = _scenario(pattern_type, "confirmed")
    assert _expected_contract(result, "confirmed") == golden["wealthpilot_expected"][
        pattern_type
    ]["confirmed"]
    assert result.structure_confirmation.state.value == "confirmed"
    assert result.direction_confirmation.state.value == "confirmed"
    assert result.direction_confirmation.observed_session_ordinal > (
        result.candidate.available_from_session_ordinal
    )
    if pattern_type == "double_bottom":
        volume = next(
            item.value
            for item in result.direction_confirmation.facts
            if item.code == "direction_confirmation_volume_ratio"
        )
        assert volume == golden["tovest_oracle"][pattern_type]["confirmed_volume_ratio"]


@pytest.mark.parametrize("pattern_type", ["double_top", "double_bottom"])
def test_wealthpilot_neckline_invalidation_and_session_expiry_are_frozen(pattern_type):
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    invalidated = _scenario(pattern_type, "neckline_invalidated")
    expired = _scenario(pattern_type, "expired")
    expected = golden["wealthpilot_expected"][pattern_type]

    assert _expected_contract(invalidated, "neckline_invalidated") == expected[
        "neckline_invalidated"
    ]
    assert _expected_contract(expired, "expired") == expected["expired"]
    assert invalidated.invalidation.observed_session_ordinal > (
        invalidated.direction_confirmation.observed_session_ordinal
    )
    assert golden["adaptation"] == {
        "source_timeframe": "1h",
        "wealthpilot_timeframe": "1d",
        "time_axis": "dense_exchange_session_ordinal",
        "source_extreme_spacing_bars": 20,
        "wealthpilot_fixture_extreme_spacing_sessions": 8,
        "fixed_seconds_reused": False,
        "btc_parameters_reused": False,
    }
