"""Value-only contracts for the IBKR Pattern historical-data boundary."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


class PatternDataStatus(str, Enum):
    READY = "READY"
    DATA_QUALITY_BLOCKED = "DATA_QUALITY_BLOCKED"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"


@dataclass(frozen=True)
class InstrumentQuery:
    """Stable IBKR lookup input; conId is preferred whenever already trusted."""

    symbol: str
    exchange: str
    currency: str
    con_id: int | None = None
    primary_exchange: str = ""

    def cache_key(self) -> tuple[Any, ...]:
        return (
            int(self.con_id or 0),
            self.symbol.upper(),
            self.exchange.upper(),
            self.primary_exchange.upper(),
            self.currency.upper(),
        )


@dataclass(frozen=True)
class ContractIdentity:
    instrument_id: str
    con_id: int
    isin: str
    symbol: str
    local_symbol: str
    market: str
    exchange: str
    primary_exchange: str
    currency: str
    sec_type: str
    stock_type: str
    timezone: str


@dataclass(frozen=True)
class TradingSession:
    ref_date: date
    start: datetime
    end: datetime


@dataclass(frozen=True)
class RawDailyBar:
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    @classmethod
    def from_values(
        cls,
        session_date: date,
        open_: Any,
        high: Any,
        low: Any,
        close: Any,
        volume: Any,
    ) -> "RawDailyBar":
        try:
            values = tuple(Decimal(str(value)) for value in (open_, high, low, close, volume))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid OHLCV value for {session_date}") from exc
        if not all(value.is_finite() for value in values):
            raise ValueError(f"non-finite OHLCV value for {session_date}")
        return cls(session_date, *values)


@dataclass(frozen=True)
class CanonicalPatternBar:
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def as_dict(self) -> dict[str, str]:
        return {
            "date": self.date.isoformat(),
            "open": _decimal_text(self.open),
            "high": _decimal_text(self.high),
            "low": _decimal_text(self.low),
            "close": _decimal_text(self.close),
            "volume": _decimal_text(self.volume),
        }


@dataclass(frozen=True)
class CanonicalPatternSeries:
    instrument_id: str
    con_id: int
    isin: str
    symbol: str
    market: str
    currency: str
    timezone: str
    adjustment_policy: str
    calendar_version: str
    last_closed_session: date
    source_bar_hash: str
    bars: tuple[CanonicalPatternBar, ...]

    @property
    def conId(self) -> int:  # noqa: N802 - external contract deliberately follows IBKR naming
        return self.con_id

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "conId": self.con_id,
            "ISIN": self.isin,
            "symbol": self.symbol,
            "market": self.market,
            "currency": self.currency,
            "timezone": self.timezone,
            "adjustment_policy": self.adjustment_policy,
            "calendar_version": self.calendar_version,
            "last_closed_session": self.last_closed_session.isoformat(),
            "source_bar_hash": self.source_bar_hash,
            "bars": [bar.as_dict() for bar in self.bars],
        }


@dataclass(frozen=True)
class PatternDataResult:
    status: PatternDataStatus
    series: CanonicalPatternSeries | None = None
    reason: str = ""
    missing_sessions: tuple[date, ...] = ()
    requested_durations: tuple[str, ...] = ()

    @property
    def is_ready(self) -> bool:
        return self.status is PatternDataStatus.READY and self.series is not None


def validate_bar(bar: RawDailyBar) -> None:
    if bar.volume < 0:
        raise ValueError(f"negative volume for {bar.session_date}")
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        raise ValueError(f"non-positive price for {bar.session_date}")
    if bar.high < max(bar.open, bar.low, bar.close):
        raise ValueError(f"high invariant failed for {bar.session_date}")
    if bar.low > min(bar.open, bar.high, bar.close):
        raise ValueError(f"low invariant failed for {bar.session_date}")


def build_source_bar_hash(bars: tuple[CanonicalPatternBar, ...]) -> str:
    payload = json.dumps(
        [bar.as_dict() for bar in bars],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite() or not math.isfinite(float(value)):
        raise ValueError("canonical numeric value must be finite")
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
