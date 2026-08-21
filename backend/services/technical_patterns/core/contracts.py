"""Value-only contracts shared by the provider-independent Pattern Core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal


PivotType = Literal["swing_high", "swing_low"]
PivotStatus = Literal["confirmed", "superseded"]
BoundaryRole = Literal["support", "resistance"]
BoundaryStatus = Literal["active", "superseded", "invalidated"]
TrendState = Literal["bullish", "bearish", "neutral"]


@dataclass(frozen=True)
class CorePatternBar:
    """One closed Daily bar on an exchange-session axis.

    ``session_ordinal`` is dense over the adapter-authoritative session list;
    it is not ``date.toordinal()`` and therefore does not count weekends or
    exchange holidays as bars. ``available_from`` is the source session date,
    not a timestamp synthesized from a fixed UTC offset.
    """

    session_date: date
    session_ordinal: int
    available_from: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    bar_id: str

    def __post_init__(self) -> None:
        if self.session_ordinal < 0:
            raise ValueError("session_ordinal must be non-negative")
        if self.available_from < self.session_date:
            raise ValueError("available_from cannot precede the source session")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.volume < 0:
            raise ValueError("volume must be non-negative")
        if self.high < max(self.open, self.low, self.close):
            raise ValueError("high invariant failed")
        if self.low > min(self.open, self.high, self.close):
            raise ValueError("low invariant failed")


@dataclass(frozen=True)
class PatternCoreInput:
    instrument_id: str
    con_id: int
    isin: str
    symbol: str
    market: str
    currency: str
    timezone: str
    timeframe: Literal["1d"]
    adjustment_policy: str
    calendar_version: str
    last_closed_session: date
    source_bar_hash: str
    dataset_version: str
    bars: tuple[CorePatternBar, ...]

    def __post_init__(self) -> None:
        if not self.instrument_id or self.con_id <= 0 or not self.source_bar_hash:
            raise ValueError("core input requires stable instrument and source identity")
        if not self.bars:
            raise ValueError("core input requires at least one closed bar")
        for left, right in zip(self.bars, self.bars[1:]):
            if right.session_date <= left.session_date:
                raise ValueError("core bars must be strictly date ordered")
            if right.session_ordinal != left.session_ordinal + 1:
                raise ValueError("core session ordinals must be dense and ordered")
        if self.bars[-1].session_date > self.last_closed_session:
            raise ValueError("core input contains an unfinished Daily bar")


@dataclass(frozen=True)
class Pivot:
    pivot_id: str
    instrument_id: str
    timeframe: Literal["1d"]
    dataset_version: str
    pivot_type: PivotType
    price: float
    source_session: date
    source_session_ordinal: int
    confirmed_at: date
    confirmed_session_ordinal: int
    available_from: date
    available_from_ordinal: int
    confirmation_bars: int
    status: PivotStatus
    algorithm_version: str
    parameter_version: str
    source_bar_ids: tuple[str, ...]
    superseded_by_pivot_id: str | None = None

    def __post_init__(self) -> None:
        if self.price <= 0:
            raise ValueError("pivot price must be positive")
        if self.source_session_ordinal >= self.confirmed_session_ordinal:
            raise ValueError("pivot confirmation requires a later closed session")
        if self.confirmed_session_ordinal > self.available_from_ordinal:
            raise ValueError("confirmation cannot follow availability")
        if self.confirmed_at > self.available_from:
            raise ValueError("confirmation date cannot follow availability")
        if self.status == "superseded" and not self.superseded_by_pivot_id:
            raise ValueError("superseded pivot requires its replacement identity")
        if self.status != "superseded" and self.superseded_by_pivot_id is not None:
            raise ValueError("only superseded pivots may name a replacement")


@dataclass(frozen=True)
class Boundary:
    boundary_id: str
    instrument_id: str
    timeframe: Literal["1d"]
    dataset_version: str
    boundary_role: BoundaryRole
    source_pivot_ids: tuple[str, ...]
    primary_pivot_id: str
    price: float
    price_low: float
    price_high: float
    created_at: date
    created_session_ordinal: int
    available_from: date
    available_from_ordinal: int
    last_confirmed_at: date
    evaluation_session: date
    evaluation_session_ordinal: int
    touch_count: int
    confirmed_touch_count: int
    status: BoundaryStatus
    superseded_by: str | None = None
    invalidation_reason: str | None = None


@dataclass(frozen=True)
class TrendContext:
    trend_context_id: str
    instrument_id: str
    timeframe: Literal["1d"]
    trend_state: TrendState
    source_pivot_ids: tuple[str, ...]
    source_boundary_ids: tuple[str, ...]
    structure_evidence: tuple[str, ...]
    available_from: date | None
    available_from_ordinal: int | None
    evaluation_session: date
    evaluation_session_ordinal: int
    confidence_class: Literal["complete", "partial", "mixed"]
