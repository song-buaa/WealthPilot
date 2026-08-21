from __future__ import annotations

from dataclasses import replace

from backend.services.technical_patterns.core.input_mapper import PatternInputMapper
from backend.services.technical_patterns.core.pivot import PivotEngine, PivotParameters
from tests.technical_patterns.conftest import canonical_series_from_case


def _run(golden, *, evaluation_session_ordinal=None):
    case = golden["pivot_case"]
    core_input = PatternInputMapper().map_series(canonical_series_from_case(case, source_hash="fixture-source-hash-v1"))
    engine = PivotEngine(parameter_version=case["parameter_version"], parameters=PivotParameters(**case["parameters"]))
    return core_input, engine.replay(core_input, evaluation_session_ordinal=evaluation_session_ordinal)


def test_frozen_tovest_pivot_semantics_and_mapped_golden_hash(golden):
    _, result = _run(golden)
    semantic = golden["pivot_case"]["source_semantic_expected"]
    mapped = golden["pivot_case"]["mapped_expected"]

    assert [
        {"type": item.pivot_type, "price": item.price, "source_ordinal": item.source_session_ordinal, "confirmed_ordinal": item.confirmed_session_ordinal}
        for item in result.confirmed
    ] == semantic["confirmed"]
    assert [
        {"type": item.pivot_type, "price": item.price, "source_ordinal": item.source_session_ordinal, "confirmed_ordinal": item.confirmed_session_ordinal}
        for item in result.superseded
    ] == semantic["superseded"]
    assert [item.event for item in result.timeline] == semantic["timeline_events"]
    assert [item.pivot_id for item in result.confirmed] == mapped["confirmed_ids"]
    assert [item.pivot_id for item in result.superseded] == mapped["superseded_ids"]
    assert result.result_hash == mapped["result_hash"]
    assert result.metrics["candidate_pivot_count"] == semantic["candidate_pivot_count"]
    assert result.metrics["candidate_replacement_count"] == semantic["candidate_replacement_count"]
    assert result.metrics["confirmed_supersession_count"] == semantic["confirmed_supersession_count"]
    assert result.metrics["future_pivot_violation_count"] == 0


def test_pivot_prefix_replay_cannot_see_future_confirmation(golden):
    core_input, prefix = _run(golden, evaluation_session_ordinal=3)
    case = golden["pivot_case"]
    engine = PivotEngine(parameter_version=case["parameter_version"], parameters=PivotParameters(**case["parameters"]))
    independently_truncated = replace(core_input, bars=core_input.bars[:4], last_closed_session=core_input.bars[3].session_date)

    rebuilt = engine.replay(independently_truncated)

    assert prefix == rebuilt
    assert all(item.available_from_ordinal <= 3 for item in prefix.confirmed)
    assert all(item.source_session_ordinal != 3 for item in prefix.confirmed)


def test_pivot_remains_candidate_until_right_session_closes(golden):
    case = golden["pivot_case"]
    core_input = PatternInputMapper().map_series(canonical_series_from_case(case, source_hash="fixture-source-hash-v1"))
    engine = PivotEngine(parameter_version=case["parameter_version"], parameters=PivotParameters(**case["parameters"]))

    before = engine.replay(core_input, evaluation_session_ordinal=1)
    after = engine.replay(core_input, evaluation_session_ordinal=2)

    assert not before.confirmed
    assert any(item.event == "candidate" and item.source_session_ordinal == 1 for item in before.timeline)
    assert len(after.confirmed) == 1
    assert after.confirmed[0].source_session_ordinal == 1
    assert after.confirmed[0].available_from_ordinal == 2


def test_pivot_repeat_is_deterministic(golden):
    _, first = _run(golden)
    _, second = _run(golden)

    assert first == second
    assert first.result_hash == second.result_hash
