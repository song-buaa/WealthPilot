from __future__ import annotations

from dataclasses import replace
from datetime import date

from backend.services.technical_patterns.core.boundary import BoundaryParameters, BoundaryTrendEngine
from backend.services.technical_patterns.core.contracts import Pivot
from backend.services.technical_patterns.core.input_mapper import PatternInputMapper
from backend.services.technical_patterns.core.range_structure import RangeSnapshot, RangeStructureEngine
from tests.technical_patterns.conftest import canonical_series_from_case


def _case_inputs(golden):
    case = golden["boundary_range_case"]
    core_input = PatternInputMapper().map_series(canonical_series_from_case(case, source_hash="boundary-source-hash-v1"))
    sessions = tuple(date.fromisoformat(item) for item in case["sessions"])
    pivots = tuple(
        Pivot(
            pivot_id=item["pivot_id"],
            instrument_id=core_input.instrument_id,
            timeframe="1d",
            dataset_version=core_input.dataset_version,
            pivot_type=item["type"],
            price=item["price"],
            source_session=sessions[item["source_ordinal"]],
            source_session_ordinal=item["source_ordinal"],
            confirmed_at=sessions[item["confirmed_ordinal"]],
            confirmed_session_ordinal=item["confirmed_ordinal"],
            available_from=sessions[item["confirmed_ordinal"]],
            available_from_ordinal=item["confirmed_ordinal"],
            confirmation_bars=item["confirmed_ordinal"] - item["source_ordinal"],
            status="confirmed",
            algorithm_version="fixture",
            parameter_version="fixture",
            source_bar_ids=(f"bar-{index}",),
        )
        for index, item in enumerate(case["pivots"])
    )
    engine = BoundaryTrendEngine(
        parameter_version=case["parameter_version"],
        parameters=BoundaryParameters(case["boundary_tolerance_pct"]),
    )
    return case, core_input, pivots, engine


def test_boundary_and_trend_match_frozen_source_semantics(golden):
    case, core_input, pivots, engine = _case_inputs(golden)
    result = engine.replay(core_input, pivots, evaluation_session_ordinal=case["evaluation_session_ordinal"])
    semantic = case["source_semantic_expected"]
    mapped = case["mapped_expected"]

    assert [
        {
            "role": item.boundary_role,
            "price_low": item.price_low,
            "price_high": item.price_high,
            "touch_count": item.touch_count,
            "source_pivot_ids": list(item.source_pivot_ids),
        }
        for item in result.boundaries
    ] == semantic["boundaries"]
    assert result.trend.trend_state == semantic["trend_state"]
    assert result.trend.confidence_class == semantic["trend_confidence"]
    assert [item.boundary_id for item in result.boundaries] == mapped["boundary_ids"]
    assert result.trend.trend_context_id == mapped["trend_id"]
    assert result.cache_key == mapped["boundary_cache_key"]
    assert result.result_hash == mapped["boundary_result_hash"]
    assert result.metrics["future_boundary_violation_count"] == 0


def test_boundary_replay_ignores_future_pivot(golden):
    case, core_input, pivots, engine = _case_inputs(golden)
    baseline = engine.replay(core_input, pivots, evaluation_session_ordinal=case["evaluation_session_ordinal"])
    future = replace(
        pivots[-1],
        pivot_id="p-future",
        source_session=core_input.bars[7].session_date,
        source_session_ordinal=7,
        confirmed_at=core_input.bars[8].session_date,
        confirmed_session_ordinal=8,
        available_from=date(2025, 2, 14),
        available_from_ordinal=9,
        price=120.0,
    )

    replay = engine.replay(core_input, pivots + (future,), evaluation_session_ordinal=case["evaluation_session_ordinal"])

    assert replay == baseline


def test_boundary_supersession_and_invalidation_match_source_rules(golden):
    case, core_input, pivots, engine = _case_inputs(golden)
    first_support = pivots[0]
    near_more_extreme = replace(pivots[2], price=98.5)
    far_more_extreme = replace(pivots[2], price=97.0)

    superseded = engine.replay(core_input, (first_support, near_more_extreme), evaluation_session_ordinal=case["evaluation_session_ordinal"])
    invalidated = engine.replay(core_input, (first_support, far_more_extreme), evaluation_session_ordinal=case["evaluation_session_ordinal"])

    assert superseded.metrics["superseded_boundary_count"] == 1
    assert invalidated.metrics["invalidated_boundary_count"] == 1
    assert all(item.available_from_ordinal <= case["evaluation_session_ordinal"] for item in superseded.boundaries + invalidated.boundaries)


def test_range_matches_frozen_source_semantics_and_hash(golden):
    case, core_input, pivots, engine = _case_inputs(golden)
    boundary = engine.replay(core_input, pivots, evaluation_session_ordinal=case["evaluation_session_ordinal"])
    result = RangeStructureEngine().replay(
        core_input,
        (RangeSnapshot(boundary.boundaries, boundary.trend, case["evaluation_session_ordinal"]),),
    )
    semantic = case["source_semantic_expected"]
    mapped = case["mapped_expected"]
    current = result.ranges[0]

    assert (current.range_low, current.range_high, current.range_width, current.range_width_pct) == (
        semantic["range_low"], semantic["range_high"], semantic["range_width"], semantic["range_width_pct"]
    )
    assert current.range_id == mapped["range_id"]
    assert result.cache_key == mapped["range_cache_key"]
    assert result.result_hash == mapped["range_result_hash"]
    assert result.metrics["active_range_count"] == 1
    assert result.metrics["future_range_violation_count"] == 0
