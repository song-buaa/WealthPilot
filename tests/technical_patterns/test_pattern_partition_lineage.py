from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

from backend.services.pattern_data.contracts import (
    CanonicalPatternBar,
    CanonicalPatternSeries,
    build_source_bar_hash,
)
from backend.services.technical_patterns.calibration.partition_lineage import (
    FrozenPartitionReference,
    PartitionDriftClassification,
    ValidationPartitionSpec,
    build_validation_partition_snapshot,
    compare_partition_lineage,
)


SPEC = ValidationPartitionSpec(
    name="untouched_validation",
    start=date(2025, 1, 2),
    end=date(2025, 1, 6),
    timeframe="1d",
    provider="IBKR",
    what_to_show="TRADES",
    use_rth=True,
)


def _bar(day: int, close: str) -> CanonicalPatternBar:
    value = Decimal(close)
    return CanonicalPatternBar(
        date=date(2025, 1, day),
        open=value - Decimal("0.5"),
        high=value + Decimal("1"),
        low=value - Decimal("1"),
        close=value,
        volume=Decimal("1000000") + day,
    )


def _series(
    bars: tuple[CanonicalPatternBar, ...],
    *,
    adjustment_policy: str = "IBKR_TRADES_SPLIT_ADJUSTED_DIVIDENDS_UNADJUSTED",
    calendar_version: str = "IBKR_SCHEDULE_V1:full-window-digest",
) -> CanonicalPatternSeries:
    return CanonicalPatternSeries(
        instrument_id="IBKR:265598",
        con_id=265598,
        isin="US0378331005",
        symbol="AAPL",
        market="NASDAQ",
        currency="USD",
        timezone="US/Eastern",
        adjustment_policy=adjustment_policy,
        calendar_version=calendar_version,
        last_closed_session=bars[-1].date,
        source_bar_hash=build_source_bar_hash(bars),
        bars=bars,
    )


def _reference(snapshot) -> FrozenPartitionReference:
    return FrozenPartitionReference(
        instrument_id=snapshot.instrument_id,
        con_id=snapshot.con_id,
        isin=snapshot.isin,
        symbol=snapshot.symbol,
        partition_name=snapshot.partition_name,
        frozen_start=snapshot.frozen_start,
        frozen_end=snapshot.frozen_end,
        actual_start=snapshot.actual_start,
        actual_end=snapshot.actual_end,
        bar_count=snapshot.bar_count,
        source_fetch_hash=snapshot.source_fetch_hash,
        partition_bars_hash=snapshot.partition_bars_hash,
        adjustment_policy=snapshot.adjustment_policy,
        calendar_version="IBKR_SCHEDULE_V1:sealed-full-window-digest",
        timeframe="1d",
        provider="IBKR",
        what_to_show="TRADES",
        use_rth=True,
        session_set_hash=snapshot.session_set_hash,
        validation_partition_hash=snapshot.validation_partition_hash,
    )


def _base_bars() -> tuple[CanonicalPatternBar, ...]:
    return (
        _bar(1, "99"),
        _bar(2, "100"),
        _bar(3, "101"),
        _bar(6, "102"),
        _bar(7, "103"),
    )


def test_rolling_full_series_hash_changes_while_frozen_partition_stays_equal():
    original = build_validation_partition_snapshot(_series(_base_bars()), SPEC)
    rolling = _series(
        (_bar(1, "98"),) + _base_bars()[1:-1] + (_bar(7, "104"),)
    )
    current = build_validation_partition_snapshot(rolling, SPEC)

    comparison = compare_partition_lineage(_reference(original), current)

    assert original.source_fetch_hash != current.source_fetch_hash
    assert original.validation_partition_hash == current.validation_partition_hash
    assert comparison.classification is (
        PartitionDriftClassification.FULL_SERIES_WINDOW_DRIFT_ONLY
    )
    assert comparison.partition_identical is True


def test_frozen_partition_ohlcv_change_blocks_with_exact_session_lineage():
    original = build_validation_partition_snapshot(_series(_base_bars()), SPEC)
    changed = list(_base_bars())
    changed[2] = _bar(3, "101.25")
    current = build_validation_partition_snapshot(_series(tuple(changed)), SPEC)

    comparison = compare_partition_lineage(_reference(original), current)

    assert comparison.classification is (
        PartitionDriftClassification.FROZEN_PARTITION_BAR_VALUE_DRIFT
    )
    assert comparison.partition_identical is False


def test_frozen_partition_session_set_drift_blocks():
    original = build_validation_partition_snapshot(_series(_base_bars()), SPEC)
    current = build_validation_partition_snapshot(
        _series(tuple(bar for bar in _base_bars() if bar.date.day != 3)),
        SPEC,
    )

    comparison = compare_partition_lineage(_reference(original), current)

    assert comparison.classification is (
        PartitionDriftClassification.FROZEN_PARTITION_SESSION_SET_DRIFT
    )
    assert comparison.partition_identical is False


def test_adjustment_policy_drift_blocks_even_when_bars_match():
    original = build_validation_partition_snapshot(_series(_base_bars()), SPEC)
    current = build_validation_partition_snapshot(
        _series(_base_bars(), adjustment_policy="DIFFERENT_ADJUSTMENT_POLICY"),
        SPEC,
    )

    comparison = compare_partition_lineage(_reference(original), current)

    assert comparison.classification is PartitionDriftClassification.ADJUSTMENT_POLICY_DRIFT
    assert comparison.partition_identical is False


def test_calendar_policy_lineage_drift_blocks_but_digest_change_outside_partition_does_not():
    original = build_validation_partition_snapshot(_series(_base_bars()), SPEC)
    reference = _reference(original)
    same_policy = build_validation_partition_snapshot(
        _series(_base_bars(), calendar_version="IBKR_SCHEDULE_V1:new-window-digest"),
        SPEC,
    )
    changed_policy = build_validation_partition_snapshot(
        _series(_base_bars(), calendar_version="IBKR_SCHEDULE_V2:new-window-digest"),
        SPEC,
    )

    assert compare_partition_lineage(reference, same_policy).partition_identical is True
    assert compare_partition_lineage(reference, changed_policy).classification is (
        PartitionDriftClassification.CALENDAR_LINEAGE_DRIFT
    )


def test_partition_hash_is_deterministic_and_ignores_bars_outside_frozen_bounds():
    first = build_validation_partition_snapshot(_series(_base_bars()), SPEC)
    second = build_validation_partition_snapshot(
        _series(
            (_bar(1, "75"),) + _base_bars()[1:-1] + (_bar(7, "125"),)
        ),
        ValidationPartitionSpec(
            use_rth=True,
            what_to_show="TRADES",
            provider="IBKR",
            timeframe="1d",
            end=date(2025, 1, 6),
            start=date(2025, 1, 2),
            name="untouched_validation",
        ),
    )

    assert first.validation_partition_hash == second.validation_partition_hash
    assert first.session_set_hash == second.session_set_hash
    assert first.partition_bars_hash == second.partition_bars_hash
    assert first.source_fetch_hash != second.source_fetch_hash


def test_legacy_reference_without_session_set_fails_closed_as_unknown_data_drift():
    original = build_validation_partition_snapshot(_series(_base_bars()), SPEC)
    changed = list(_base_bars())
    changed[1] = replace(changed[1], close=Decimal("100.25"))
    current = build_validation_partition_snapshot(_series(tuple(changed)), SPEC)
    reference = replace(
        _reference(original),
        session_set_hash=None,
        validation_partition_hash=None,
    )

    comparison = compare_partition_lineage(reference, current)

    assert comparison.classification is PartitionDriftClassification.UNKNOWN_DATA_DRIFT
    assert "lacks the exact session set" in comparison.reason
