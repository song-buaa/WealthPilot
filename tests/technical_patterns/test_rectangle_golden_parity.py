from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.technical_patterns.core.identity import stable_hash
from tests.technical_patterns.test_rectangle_detector import _core_input, _facts, _parameters, _run


FIXTURE = Path(__file__).parent / "fixtures/tovest_tpg_v1_10_rectangle_golden.json"


def _parity_contract(result):
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
        "range_low": facts["range_low"],
        "range_high": facts["range_high"],
        "range_width": facts["range_width"],
        "range_width_pct": facts["range_width_pct"],
        "support_touch_count": facts["support_touch_count"],
        "resistance_touch_count": facts["resistance_touch_count"],
        "touch_sequence": facts["touch_sequence"],
        "structure_span_sessions": facts["structure_span_sessions"],
        "support_zone_low": facts["support_zone_low"],
        "support_zone_high": facts["support_zone_high"],
        "resistance_zone_low": facts["resistance_zone_low"],
        "resistance_zone_high": facts["resistance_zone_high"],
        "structure_confirmation": result.structure_confirmation.state.value,
        "structure_reason": result.structure_confirmation.reason,
        "structure_observed_session_ordinal": result.structure_confirmation.observed_session_ordinal,
        "direction_confirmation": result.direction_confirmation.state.value,
        "direction_reason": result.direction_confirmation.reason,
        "lifecycle_transitions": [item.to_state.value for item in result.lifecycle.transitions],
    }


def test_frozen_tovest_rectangle_maps_to_deterministic_wealthpilot_structure_contract():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = _parity_contract(_run(_core_input()).results[0])
    oracle = golden["tovest_oracle"]

    assert golden["source_freeze"]["commit"] == "937edb62727f4d8c36d41b36e93521d077da20f9"
    assert actual == golden["wealthpilot_expected"]
    assert actual["pattern_type"] == oracle["pattern_type"]
    assert actual["status"] == oracle["status"]
    assert actual["direction"] == oracle["direction"]
    assert actual["range_low"] == pytest.approx(oracle["support"])
    assert actual["range_high"] == pytest.approx(oracle["resistance"])
    assert actual["range_width"] == pytest.approx(oracle["range_width"])
    assert actual["support_touch_count"] == oracle["support_touch_count"]
    assert actual["resistance_touch_count"] == oracle["resistance_touch_count"]
    assert actual["touch_sequence"] == oracle["touch_sequence"]
    assert actual["structure_span_sessions"] == oracle["structure_span_bars"]
    assert actual["source_pivot_count"] == len(oracle["source_pivot_ids"])
    assert actual["direction_confirmation"] == "not_required"


def test_frozen_rectangle_lifecycle_maps_invalidation_and_session_expiry():
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    invalidated = _run(_core_input(count=10, invalidation_at=9), evaluation=9).results[0]
    expired = _run(_core_input(count=14), evaluation=13, parameters=_parameters(expiry_sessions=5)).results[0]

    assert {
        "candidate_id": invalidated.candidate.candidate_id,
        "status": invalidated.status,
        "observed_session_ordinal": invalidated.invalidation.observed_session_ordinal,
        "reason": invalidated.invalidation.reason,
        "transitions": [item.to_state.value for item in invalidated.lifecycle.transitions],
        "result_hash": invalidated.result_hash,
    } == golden["wealthpilot_lifecycle_expected"]["invalidated"]
    assert invalidated.status == golden["tovest_oracle"]["invalidated_status"]
    assert {
        "candidate_id": expired.candidate.candidate_id,
        "status": expired.status,
        "expired_session_ordinal": expired.lifecycle.expired_session_ordinal,
        "transitions": [item.to_state.value for item in expired.lifecycle.transitions],
        "result_hash": expired.result_hash,
    } == golden["wealthpilot_lifecycle_expected"]["expired"]
