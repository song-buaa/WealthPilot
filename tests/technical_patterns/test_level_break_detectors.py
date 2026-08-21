from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from time import perf_counter

import pytest

from backend.services.technical_patterns.calibration import (
    CalibrationDataset,
    CalibrationDatasetManifest,
    CalibrationKey,
    CalibrationNotConfigured,
    CalibrationPartition,
    CalibrationRegistry,
    DetectorParameterSet,
    US_LEVEL_BREAK_DEVELOPMENT_VERSION,
    build_us_level_break_development_parameter_sets,
)
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
from backend.services.technical_patterns.detectors.framework import InsufficientPatternHistory
from backend.services.technical_patterns.indicators import TalibIndicatorLayer


def _sessions(count: int) -> list[date]:
    values: list[date] = []
    current = date(2025, 1, 2)
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return values


def _core_input(
    pattern_type: str,
    *,
    trigger_volume: float = 300.0,
    trigger_close: float | None = None,
    count: int = 86,
    post_trigger_close: float | None = None,
) -> PatternCoreInput:
    trigger = 80
    bars: list[CorePatternBar] = []
    for ordinal, session in enumerate(_sessions(count)):
        open_price = 99.5
        high = 100.0
        low = 99.0
        close = 99.6
        volume = 100.0
        if pattern_type == "breakdown":
            open_price, high, low, close = 100.5, 101.0, 100.0, 100.4
        if ordinal == trigger:
            if pattern_type == "breakout":
                open_price, high, low, close = 100.2, 103.2, 99.7, trigger_close or 102.8
            else:
                open_price, high, low, close = 99.8, 100.3, 96.0, trigger_close or 96.4
            volume = trigger_volume
        elif ordinal > trigger and post_trigger_close is not None:
            close = post_trigger_close
            high = max(high, close + 0.2)
            low = min(low, close - 0.2)
            open_price = close
        bar_material = {"instrument": f"fixture:{pattern_type}", "session": session, "ordinal": ordinal}
        bars.append(
            CorePatternBar(
                session_date=session,
                session_ordinal=ordinal,
                available_from=session,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                bar_id=stable_id("bar", bar_material),
            )
        )
    source_hash = stable_hash(tuple(bars))
    return PatternCoreInput(
        instrument_id=f"fixture:{pattern_type}",
        con_id=123,
        isin="FIXTUREISIN",
        symbol="FIXTURE",
        market="TEST",
        currency="USD",
        timezone="America/New_York",
        timeframe="1d",
        adjustment_policy="split_adjusted",
        calendar_version="fixture-us-sessions-v1",
        last_closed_session=bars[-1].session_date,
        source_bar_hash=source_hash,
        dataset_version=source_hash,
        bars=tuple(bars),
    )


def _key(pattern_type: str) -> CalibrationKey:
    return CalibrationKey("TEST", "EQUITY", "1d", "level_break", pattern_type, "fixture-v1")


def _parameters(pattern_type: str, *, expiry: int = 4) -> DetectorParameterSet:
    return DetectorParameterSet(
        _key(pattern_type),
        (
            ("atr_margin_multiplier", 0.10),
            ("decisive_margin_pct", 0.02),
            ("expiry_sessions", expiry),
            ("invalidation_buffer_pct", 0.50),
            ("lookback_bars", 60),
            ("minimum_boundary_age_sessions", 3),
            ("minimum_boundary_touches", 2),
            ("zone_atr_width_multiplier", 0.10),
            ("zone_width_pct", 0.10),
            ("volume_average_bars", 20),
            ("volume_ratio_threshold", 1.70),
        ),
        minimum_history_bars=61,
    )


def _run(core_input: PatternCoreInput, pattern_type: str, *, evaluation: int = 80, expiry: int = 4):
    detector = BreakoutDetector() if pattern_type == "breakout" else BreakdownDetector()
    framework = DetectorFramework(
        calibrations=CalibrationRegistry((_parameters(pattern_type, expiry=expiry),)),
        indicators=TalibIndicatorLayer(),
    )
    return framework.run(
        core_input,
        evaluation_session_ordinal=evaluation,
        calibration_key=_key(pattern_type),
        detector=detector,
        structure_confirmation=LevelBreakStructureConfirmation(),
        direction_confirmation=LevelBreakDirectionConfirmation(),
        invalidation=LevelBreakInvalidation(),
    )


@pytest.mark.parametrize("pattern_type", ["breakout", "breakdown"])
def test_clean_price_and_volume_break_confirms(pattern_type):
    result = _run(_core_input(pattern_type), pattern_type)

    assert len(result.results) == 1
    pattern = result.results[0]
    assert pattern.candidate.pattern_type.value == pattern_type
    assert pattern.status == "confirmed"
    assert pattern.structure_confirmation.state.value == "confirmed"
    assert pattern.direction_confirmation.state.value == "confirmed"
    assert pattern.candidate.available_from_session_ordinal == 80
    assert pattern.candidate.source_boundaries[0].available_from_session_ordinal == 79


def test_fake_breakout_and_insufficient_volume_stay_unconfirmed():
    result = _run(_core_input("breakout", trigger_volume=120.0), "breakout")

    assert result.results[0].structure_confirmation.state.value == "confirmed"
    assert result.results[0].direction_confirmation.state.value == "pending"
    assert result.results[0].status == "candidate"


@pytest.mark.parametrize("pattern_type,close", [("breakout", 100.05), ("breakdown", 99.95)])
def test_invalid_structure_does_not_create_candidate(pattern_type, close):
    result = _run(_core_input(pattern_type, trigger_close=close), pattern_type)
    assert result.results == ()


def test_insufficient_history_and_missing_calibration_fail_closed():
    short = _core_input("breakout", count=55)
    framework = DetectorFramework(
        calibrations=CalibrationRegistry((_parameters("breakout"),)),
        indicators=TalibIndicatorLayer(),
    )
    with pytest.raises(InsufficientPatternHistory):
        framework.run(
            short,
            evaluation_session_ordinal=54,
            calibration_key=_key("breakout"),
            detector=BreakoutDetector(),
            structure_confirmation=LevelBreakStructureConfirmation(),
            direction_confirmation=LevelBreakDirectionConfirmation(),
            invalidation=LevelBreakInvalidation(),
        )
    with pytest.raises(CalibrationNotConfigured):
        DetectorFramework(calibrations=CalibrationRegistry(), indicators=TalibIndicatorLayer()).run(
            _core_input("breakout"),
            evaluation_session_ordinal=80,
            calibration_key=_key("breakout"),
            detector=BreakoutDetector(),
            structure_confirmation=LevelBreakStructureConfirmation(),
            direction_confirmation=LevelBreakDirectionConfirmation(),
            invalidation=LevelBreakInvalidation(),
        )


@pytest.mark.parametrize("detector", [BreakoutDetector(), BreakdownDetector()])
def test_indicator_dependencies_are_explicit_and_versioned(detector):
    definitions = detector.required_indicators(_parameters(detector.descriptor.pattern_type.value))
    assert [(item.code, item.kind.value, item.periods, item.source) for item in definitions] == [
        ("EMA20", "EMA", (20,), "close"),
        ("EMA50", "EMA", (50,), "close"),
        ("ATR14", "ATR", (14,), "close"),
        ("VOLUME_SMA20", "SMA", (20,), "volume"),
    ]
    assert detector.descriptor.detector_version == "wp-level-break-detector-v1"


def test_missing_detector_specific_parameter_fails_closed_without_hidden_default():
    complete = _parameters("breakout")
    incomplete = DetectorParameterSet(
        complete.key,
        tuple(item for item in complete.values if item[0] != "volume_ratio_threshold"),
        complete.minimum_history_bars,
    )
    with pytest.raises(CalibrationNotConfigured, match="volume_ratio_threshold"):
        BreakoutDetector().required_indicators(incomplete)


@pytest.mark.parametrize(
    "pattern_type,reentry_close",
    [("breakout", 99.0), ("breakdown", 101.5)],
)
def test_lifecycle_invalidation_uses_only_later_closed_sessions(pattern_type, reentry_close):
    result = _run(
        _core_input(pattern_type, post_trigger_close=reentry_close),
        pattern_type,
        evaluation=81,
    )
    trigger = next(item for item in result.results if item.candidate.formed_session_ordinal == 80)
    assert trigger.status == "invalidated"
    assert trigger.invalidation.observed_session_ordinal == 81


def test_lifecycle_expiry_uses_session_ordinal_not_wall_clock():
    result = _run(_core_input("breakout", count=86), "breakout", evaluation=84, expiry=4)
    trigger = next(item for item in result.results if item.candidate.formed_session_ordinal == 80)
    assert trigger.status == "expired"
    assert trigger.lifecycle.transitions[-1].session_ordinal == 84


def test_prefix_replay_blocks_future_bar_volume_and_identity_changes():
    full = _core_input("breakout", count=86, post_trigger_close=150.0)
    prefix = replace(
        full,
        bars=full.bars[:81],
        last_closed_session=full.bars[80].session_date,
        source_bar_hash="independent-prefix-source",
        dataset_version="independent-prefix-source",
    )
    full_result = _run(full, "breakout", evaluation=80)
    prefix_result = _run(prefix, "breakout", evaluation=80)

    assert full_result == prefix_result
    assert full_result.results[0].candidate.candidate_id == prefix_result.results[0].candidate.candidate_id
    assert full_result.result_hash == prefix_result.result_hash


def test_repeated_execution_has_deterministic_candidate_and_result_hash():
    core_input = _core_input("breakdown")
    first = _run(core_input, "breakdown")
    second = _run(core_input, "breakdown")
    assert first.result_hash == second.result_hash
    assert first.results[0].result_hash == second.results[0].result_hash


def test_us_development_calibrations_are_exact_stock_etf_keys_not_btc_fallback():
    parameter_sets = build_us_level_break_development_parameter_sets()
    registry = CalibrationRegistry(parameter_sets)
    assert len(parameter_sets) == 4
    for asset_class in ("EQUITY", "FIXED_INCOME"):
        for pattern_type in ("breakout", "breakdown"):
            key = CalibrationKey(
                "US", asset_class, "1d", "level_break", pattern_type, US_LEVEL_BREAK_DEVELOPMENT_VERSION
            )
            assert registry.resolve(key).require("parameter_origin") == "wealthpilot_us_hypothesis_not_validated"
    with pytest.raises(CalibrationNotConfigured, match="BTC fallback are forbidden"):
        registry.resolve(CalibrationKey("CRYPTO", "CRYPTO", "1d", "level_break", "breakout", "btc-v1"))


def test_calibration_dataset_manifest_keeps_development_holdout_validation_disjoint():
    def dataset(partition, suffix):
        return CalibrationDataset(
            f"us-equity-{suffix}",
            partition,
            "US",
            "EQUITY",
            "1d",
            (f"instrument:{suffix}",),
            (f"hash:{suffix}",),
            f"Frozen {suffix} partition",
        )

    manifest = CalibrationDatasetManifest(
        dataset(CalibrationPartition.DEVELOPMENT, "development"),
        dataset(CalibrationPartition.HOLDOUT, "holdout"),
        dataset(CalibrationPartition.VALIDATION, "validation"),
    )
    assert manifest.manifest_id.startswith("caldata_")

    overlapping_holdout = CalibrationDataset(
        "us-equity-overlap",
        CalibrationPartition.HOLDOUT,
        "US",
        "EQUITY",
        "1d",
        ("instrument:development",),
        ("hash:holdout-independent",),
        "Invalid overlapping holdout",
    )
    with pytest.raises(ValueError, match="disjoint instruments"):
        CalibrationDatasetManifest(
            dataset(CalibrationPartition.DEVELOPMENT, "development"),
            overlapping_holdout,
            dataset(CalibrationPartition.VALIDATION, "validation"),
        )


def test_single_instrument_runtime_is_bounded_and_records_no_detector_cache_contract():
    core_input = _core_input("breakout", count=300)
    started = perf_counter()
    result = _run(core_input, "breakout", evaluation=299)
    elapsed = perf_counter() - started
    assert result.evaluation_session_ordinal == 299
    assert elapsed < 2.0
