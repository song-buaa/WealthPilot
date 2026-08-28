"""Exact frozen-partition lineage for real Pattern calibration validation.

``CanonicalPatternSeries.source_bar_hash`` remains the public hash of every bar
returned by the adapter.  Validation snapshots add a separate, bounded hash so
rolling fetch-window changes cannot masquerade as frozen-partition drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Mapping

from backend.services.pattern_data.contracts import (
    CanonicalPatternBar,
    CanonicalPatternSeries,
    build_source_bar_hash,
)

from ..core.identity import stable_hash


class PartitionLineageError(ValueError):
    """Raised when a current series cannot satisfy a frozen partition contract."""


class PartitionDriftClassification(str, Enum):
    FULL_SERIES_WINDOW_DRIFT_ONLY = "FULL_SERIES_WINDOW_DRIFT_ONLY"
    FROZEN_PARTITION_IDENTICAL = "FROZEN_PARTITION_IDENTICAL"
    FROZEN_PARTITION_BAR_VALUE_DRIFT = "FROZEN_PARTITION_BAR_VALUE_DRIFT"
    FROZEN_PARTITION_SESSION_SET_DRIFT = "FROZEN_PARTITION_SESSION_SET_DRIFT"
    ADJUSTMENT_POLICY_DRIFT = "ADJUSTMENT_POLICY_DRIFT"
    CALENDAR_LINEAGE_DRIFT = "CALENDAR_LINEAGE_DRIFT"
    UNKNOWN_DATA_DRIFT = "UNKNOWN_DATA_DRIFT"


@dataclass(frozen=True)
class ValidationPartitionSpec:
    name: str
    start: date
    end: date
    timeframe: str
    provider: str
    what_to_show: str
    use_rth: bool

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.name,
                self.timeframe,
                self.provider,
                self.what_to_show,
            )
        ):
            raise ValueError("validation partition spec requires named provider semantics")
        if self.start > self.end:
            raise ValueError("validation partition start must not follow its end")

    @property
    def provider_semantics(self) -> dict[str, object]:
        return {
            "provider": self.provider.upper(),
            "timeframe": self.timeframe.lower(),
            "use_rth": self.use_rth,
            "what_to_show": self.what_to_show.upper(),
        }


@dataclass(frozen=True)
class ValidationPartitionSnapshot:
    instrument_id: str
    con_id: int
    isin: str
    symbol: str
    partition_name: str
    frozen_start: date
    frozen_end: date
    actual_start: date
    actual_end: date
    bar_count: int
    source_fetch_hash: str
    partition_bars_hash: str
    session_set_hash: str
    validation_partition_hash: str
    adjustment_policy: str
    calendar_policy_version: str
    provider_semantics: Mapping[str, object]


@dataclass(frozen=True)
class FrozenPartitionReference:
    instrument_id: str
    con_id: int
    isin: str
    symbol: str
    partition_name: str
    frozen_start: date
    frozen_end: date
    actual_start: date
    actual_end: date
    bar_count: int
    source_fetch_hash: str
    partition_bars_hash: str
    adjustment_policy: str
    calendar_version: str
    timeframe: str
    provider: str
    what_to_show: str
    use_rth: bool
    session_set_hash: str | None = None
    validation_partition_hash: str | None = None

    @classmethod
    def from_manifest_entry(
        cls,
        entry: Mapping[str, object],
        *,
        source_fetch_hash: str,
    ) -> "FrozenPartitionReference":
        date_range = entry["date_range"]
        if not isinstance(date_range, Mapping):
            raise ValueError("frozen dataset entry has no date-range mapping")
        values = {
            "requested_start": date_range.get("requested_start"),
            "requested_end": date_range.get("requested_end"),
            "actual_start": date_range.get("actual_start"),
            "actual_end": date_range.get("actual_end"),
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise ValueError("frozen dataset entry has incomplete date lineage")
        return cls(
            instrument_id=str(entry["instrument_id"]),
            con_id=int(entry["conId"]),
            isin=str(entry["ISIN"]),
            symbol=str(entry["symbol"]),
            partition_name=str(entry["partition"]),
            frozen_start=date.fromisoformat(values["requested_start"]),
            frozen_end=date.fromisoformat(values["requested_end"]),
            actual_start=date.fromisoformat(values["actual_start"]),
            actual_end=date.fromisoformat(values["actual_end"]),
            bar_count=int(entry["bar_count"]),
            source_fetch_hash=source_fetch_hash,
            partition_bars_hash=str(entry["source_bar_hash"]),
            adjustment_policy=str(entry["adjustment_policy"]),
            calendar_version=str(entry["calendar_version"]),
            timeframe=str(entry["timeframe"]),
            provider=str(entry["provider"]),
            what_to_show=str(entry["whatToShow"]),
            use_rth=bool(entry["useRTH"]),
        )


@dataclass(frozen=True)
class PartitionLineageComparison:
    classification: PartitionDriftClassification
    partition_identical: bool
    source_fetch_hash_equal: bool
    partition_bars_hash_equal: bool
    session_set_equal: bool | None
    reason: str


def build_validation_partition_snapshot(
    series: CanonicalPatternSeries,
    spec: ValidationPartitionSpec,
) -> ValidationPartitionSnapshot:
    bars = tuple(bar for bar in series.bars if spec.start <= bar.date <= spec.end)
    _validate_partition_bars(bars)
    partition_bars_hash = build_source_bar_hash(bars)
    session_dates = tuple(bar.date for bar in bars)
    calendar_policy_version = _calendar_policy_version(series.calendar_version)
    material = {
        "instrument": {
            "con_id": series.con_id,
            "instrument_id": series.instrument_id,
            "isin": series.isin,
            "symbol": series.symbol,
        },
        "partition": {
            "name": spec.name,
            "frozen_start": spec.start,
            "frozen_end": spec.end,
            "actual_start": bars[0].date,
            "actual_end": bars[-1].date,
            "bar_count": len(bars),
        },
        "session_dates": session_dates,
        "bars": tuple(bar.as_dict() for bar in bars),
        "adjustment_policy": series.adjustment_policy,
        "calendar_policy_version": calendar_policy_version,
        "provider_semantics": spec.provider_semantics,
    }
    return ValidationPartitionSnapshot(
        instrument_id=series.instrument_id,
        con_id=series.con_id,
        isin=series.isin,
        symbol=series.symbol,
        partition_name=spec.name,
        frozen_start=spec.start,
        frozen_end=spec.end,
        actual_start=bars[0].date,
        actual_end=bars[-1].date,
        bar_count=len(bars),
        source_fetch_hash=series.source_bar_hash,
        partition_bars_hash=partition_bars_hash,
        session_set_hash=stable_hash(session_dates),
        validation_partition_hash=stable_hash(material),
        adjustment_policy=series.adjustment_policy,
        calendar_policy_version=calendar_policy_version,
        provider_semantics=spec.provider_semantics,
    )


def compare_partition_lineage(
    reference: FrozenPartitionReference,
    current: ValidationPartitionSnapshot,
) -> PartitionLineageComparison:
    fetch_equal = reference.source_fetch_hash == current.source_fetch_hash
    bars_equal = reference.partition_bars_hash == current.partition_bars_hash
    session_equal = (
        None
        if reference.session_set_hash is None
        else reference.session_set_hash == current.session_set_hash
    )

    if not _identity_equal(reference, current):
        return _comparison(
            PartitionDriftClassification.UNKNOWN_DATA_DRIFT,
            fetch_equal,
            bars_equal,
            session_equal,
            "instrument identity differs from the frozen reference",
        )
    if reference.adjustment_policy != current.adjustment_policy:
        return _comparison(
            PartitionDriftClassification.ADJUSTMENT_POLICY_DRIFT,
            fetch_equal,
            bars_equal,
            session_equal,
            "adjustment policy differs from the frozen reference",
        )
    if _calendar_policy_version(reference.calendar_version) != current.calendar_policy_version:
        return _comparison(
            PartitionDriftClassification.CALENDAR_LINEAGE_DRIFT,
            fetch_equal,
            bars_equal,
            session_equal,
            "calendar policy version differs from the frozen reference",
        )
    if not _partition_envelope_equal(reference, current) or session_equal is False:
        return _comparison(
            PartitionDriftClassification.FROZEN_PARTITION_SESSION_SET_DRIFT,
            fetch_equal,
            bars_equal,
            session_equal,
            "frozen partition dates or session set differ",
        )
    if reference.validation_partition_hash is not None:
        identical = reference.validation_partition_hash == current.validation_partition_hash
        if identical:
            classification = (
                PartitionDriftClassification.FROZEN_PARTITION_IDENTICAL
                if fetch_equal
                else PartitionDriftClassification.FULL_SERIES_WINDOW_DRIFT_ONLY
            )
            return _comparison(
                classification,
                fetch_equal,
                True,
                True,
                "exact frozen validation partition is identical",
                partition_identical=True,
            )
    if bars_equal:
        classification = (
            PartitionDriftClassification.FROZEN_PARTITION_IDENTICAL
            if fetch_equal
            else PartitionDriftClassification.FULL_SERIES_WINDOW_DRIFT_ONLY
        )
        return _comparison(
            classification,
            fetch_equal,
            True,
            session_equal,
            "legacy bars-only partition hash is identical",
            partition_identical=True,
        )
    if session_equal is True:
        return _comparison(
            PartitionDriftClassification.FROZEN_PARTITION_BAR_VALUE_DRIFT,
            fetch_equal,
            False,
            True,
            "session set is identical but canonical OHLCV changed",
        )
    return _comparison(
        PartitionDriftClassification.UNKNOWN_DATA_DRIFT,
        fetch_equal,
        False,
        session_equal,
        "legacy reference lacks the exact session set needed to distinguish bar-value and session drift",
    )


def _validate_partition_bars(bars: tuple[CanonicalPatternBar, ...]) -> None:
    if not bars:
        raise PartitionLineageError("frozen partition contains no canonical bars")
    dates = tuple(bar.date for bar in bars)
    if dates != tuple(sorted(dates)) or len(dates) != len(set(dates)):
        raise PartitionLineageError(
            "frozen partition dates must be unique and strictly ordered"
        )


def _calendar_policy_version(calendar_version: str) -> str:
    value = calendar_version.strip().split(":", 1)[0]
    if not value:
        raise PartitionLineageError("calendar version has no policy lineage")
    return value


def _identity_equal(
    reference: FrozenPartitionReference,
    current: ValidationPartitionSnapshot,
) -> bool:
    return (
        reference.instrument_id,
        reference.con_id,
        reference.isin,
        reference.symbol,
        reference.partition_name,
        reference.frozen_start,
        reference.frozen_end,
        reference.timeframe.lower(),
        reference.provider.upper(),
        reference.what_to_show.upper(),
        reference.use_rth,
    ) == (
        current.instrument_id,
        current.con_id,
        current.isin,
        current.symbol,
        current.partition_name,
        current.frozen_start,
        current.frozen_end,
        str(current.provider_semantics["timeframe"]).lower(),
        str(current.provider_semantics["provider"]).upper(),
        str(current.provider_semantics["what_to_show"]).upper(),
        bool(current.provider_semantics["use_rth"]),
    )


def _partition_envelope_equal(
    reference: FrozenPartitionReference,
    current: ValidationPartitionSnapshot,
) -> bool:
    return (
        reference.actual_start,
        reference.actual_end,
        reference.bar_count,
    ) == (
        current.actual_start,
        current.actual_end,
        current.bar_count,
    )


def _comparison(
    classification: PartitionDriftClassification,
    fetch_equal: bool,
    bars_equal: bool,
    session_equal: bool | None,
    reason: str,
    *,
    partition_identical: bool = False,
) -> PartitionLineageComparison:
    return PartitionLineageComparison(
        classification=classification,
        partition_identical=partition_identical,
        source_fetch_hash_equal=fetch_equal,
        partition_bars_hash_equal=bars_equal,
        session_set_equal=session_equal,
        reason=reason,
    )
