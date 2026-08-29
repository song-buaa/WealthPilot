from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from backend.services.pattern_data.contracts import build_source_bar_hash
from backend.services.pattern_data.immutable_dataset import ImmutablePatternDataset
from backend.services.technical_patterns.calibration import (
    CalibrationRegistry,
    build_approved_runtime_calibration_registry,
)
from backend.services.technical_patterns.core.identity import (
    IDENTITY_VERSION,
    PATTERN_CANDIDATE_IDENTITY_VERSION,
)
from backend.services.technical_patterns.core.input_mapper import PatternInputMapper
from backend.services.technical_patterns.core.lifecycle import LifecycleState
from backend.services.technical_patterns.detectors.framework import DetectorFramework
from backend.services.technical_patterns.detectors.level_break import EMA_SLOW_PERIOD
from backend.services.technical_patterns.indicators import TalibIndicatorLayer
from backend.services.technical_patterns.real_review import _bindings
from backend.services.technical_patterns.runtime_provider import (
    RUNTIME_BAR_WINDOW,
    RUNTIME_VISIBLE_HORIZON_BARS,
    RUNTIME_WARMUP_BARS,
    _bounded_runtime_series,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = ImmutablePatternDataset(
    REPO_ROOT / "docs/pattern_review/REAL_IBKR_PATTERN_DATASET_V2_MANIFEST.json",
    REPO_ROOT,
)


def _envelope(symbol: str, bar_count: int):
    series = DATASET.load_series(symbol)
    bars = series.bars[-bar_count:]
    return replace(
        series,
        bars=bars,
        last_closed_session=bars[-1].date,
        source_bar_hash=build_source_bar_hash(bars),
    )


def _run(series, candidate):
    core_input = PatternInputMapper().map_series(series)
    core_input = replace(core_input, market=candidate.scope.market)
    pattern_type, _, detector, structure, invalidation, direction = next(
        item for item in _bindings() if item[0] == candidate.scope.pattern_type
    )
    assert pattern_type == candidate.scope.pattern_type
    return DetectorFramework(
        calibrations=CalibrationRegistry((candidate.parameters,)),
        indicators=TalibIndicatorLayer(),
    ).run(
        core_input,
        evaluation_session_ordinal=core_input.bars[-1].session_ordinal,
        calibration_key=candidate.parameters.key,
        detector=detector,
        structure_confirmation=structure,
        invalidation=invalidation,
        direction_confirmation=direction,
    )


def _candidate(pattern_type: str, asset_class: str):
    return next(
        item
        for item in build_approved_runtime_calibration_registry().snapshot()
        if item.scope.pattern_type == pattern_type
        and item.scope.economic_asset_class == asset_class
    )


def _visible(run):
    return tuple(
        item
        for item in run.results
        if item.lifecycle.state is not LifecycleState.CANDIDATE
    )


def test_runtime_warmup_is_derived_from_promoted_history_and_indicator_requirements():
    promoted = build_approved_runtime_calibration_registry().snapshot()

    assert max(item.parameters.minimum_history_bars for item in promoted) == 80
    assert RUNTIME_WARMUP_BARS == 80
    assert RUNTIME_WARMUP_BARS >= EMA_SLOW_PERIOD
    assert RUNTIME_VISIBLE_HORIZON_BARS == 220
    assert RUNTIME_WARMUP_BARS + RUNTIME_VISIBLE_HORIZON_BARS == RUNTIME_BAR_WINDOW


def test_candidate_identity_is_date_and_anchor_based_across_raw_window_ordinals():
    candidate = _candidate("breakout", "EQUITY")
    short = _run(_envelope("SPY", RUNTIME_BAR_WINDOW), candidate)
    long = _run(_envelope("SPY", 1950), candidate)
    short_by_date = {
        item.candidate.available_from: item for item in _visible(short)
    }
    long_by_date = {
        item.candidate.available_from: item for item in _visible(long)
    }
    shared = sorted(set(short_by_date).intersection(long_by_date))

    assert shared
    current_date = shared[-1]
    short_event = short_by_date[current_date]
    long_event = long_by_date[current_date]
    assert short_event.candidate.available_from_session_ordinal != (
        long_event.candidate.available_from_session_ordinal
    )
    assert short_event.candidate.candidate_id == long_event.candidate.candidate_id
    assert short_event.candidate.identity_version == IDENTITY_VERSION
    assert IDENTITY_VERSION == "WP-PATTERN-CORE-IDENTITY-2.0"
    assert PATTERN_CANDIDATE_IDENTITY_VERSION == "wp-pattern-candidate-identity-v2"


def test_distinct_structure_anchors_produce_distinct_candidate_ids():
    from tests.technical_patterns.test_detector_framework import (
        _core_input,
        _proposal,
        _run,
    )

    core_input = _core_input()
    proposal = _proposal(core_input)
    first, _, _ = _run(
        core_input,
        replace(proposal, identity_anchors=("swing_high:bar-A",)),
    )
    second, _, _ = _run(
        core_input,
        replace(proposal, identity_anchors=("swing_high:bar-B",)),
    )

    assert first.results[0].candidate.candidate_id != (
        second.results[0].candidate.candidate_id
    )


@pytest.mark.parametrize(
    ("symbol", "asset_class", "pattern_type"),
    (
        ("SPY", "EQUITY", "breakout"),
        ("SPY", "EQUITY", "breakdown"),
        ("SPY", "EQUITY", "rectangle"),
        ("SPY", "EQUITY", "ascending_triangle"),
        ("SPY", "EQUITY", "double_top"),
        ("SPY", "EQUITY", "double_bottom"),
        ("LQD", "FIXED_INCOME", "breakout"),
        ("LQD", "FIXED_INCOME", "ascending_triangle"),
        ("LQD", "FIXED_INCOME", "double_top"),
    ),
)
def test_promoted_runtime_execution_is_exact_across_source_envelopes(
    symbol: str,
    asset_class: str,
    pattern_type: str,
):
    short_source = _envelope(symbol, RUNTIME_BAR_WINDOW)
    long_source = _envelope(symbol, 1950)
    normalized_short = _bounded_runtime_series(short_source)
    normalized_long = _bounded_runtime_series(long_source)

    assert normalized_short.bars == normalized_long.bars
    assert normalized_short.source_bar_hash == normalized_long.source_bar_hash

    candidate = _candidate(pattern_type, asset_class)
    short = _run(normalized_short, candidate)
    long = _run(normalized_long, candidate)
    assert short == long
    assert _visible(short) == _visible(long)
