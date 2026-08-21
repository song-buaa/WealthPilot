"""Map the Stage 0 canonical Daily series into Pattern Core input."""

from __future__ import annotations

from datetime import date
from typing import Iterable

from backend.services.pattern_data.contracts import CanonicalPatternSeries

from .contracts import CorePatternBar, PatternCoreInput
from .identity import stable_id


class PatternInputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class PatternInputMapper:
    """Construct a dense exchange-session axis without wall-clock inference."""

    TIMEFRAME = "1d"

    def map_series(
        self,
        series: CanonicalPatternSeries,
        *,
        expected_sessions: Iterable[date] | None = None,
        as_of_session: date | None = None,
    ) -> PatternCoreInput:
        selected = tuple(
            bar for bar in series.bars
            if bar.date <= series.last_closed_session and (as_of_session is None or bar.date <= as_of_session)
        )
        if not selected:
            raise PatternInputError("INSUFFICIENT_HISTORY", "no closed Daily bars are available at the requested as-of session")

        actual_dates = tuple(bar.date for bar in selected)
        if len(set(actual_dates)) != len(actual_dates):
            raise PatternInputError("DUPLICATE_SESSION", "canonical series contains a duplicate Daily session")
        if any(right <= left for left, right in zip(actual_dates, actual_dates[1:])):
            raise PatternInputError("SESSION_ORDER_INVALID", "canonical series is not strictly session ordered")

        if expected_sessions is not None:
            effective_last = min(series.last_closed_session, as_of_session or series.last_closed_session)
            expected = tuple(sorted({item for item in expected_sessions if item <= effective_last}))
            missing = tuple(item for item in expected if item not in set(actual_dates))
            if missing:
                dates = ",".join(item.isoformat() for item in missing)
                raise PatternInputError("EXPECTED_SESSION_MISSING", f"scheduled Daily sessions have no canonical bars: {dates}")

        bars = tuple(
            CorePatternBar(
                session_date=bar.date,
                session_ordinal=index,
                available_from=bar.date,
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
                bar_id=stable_id(
                    "bar",
                    {
                        "instrument_id": series.instrument_id,
                        "timeframe": self.TIMEFRAME,
                        "session_date": bar.date,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                    },
                ),
            )
            for index, bar in enumerate(selected)
        )
        return PatternCoreInput(
            instrument_id=series.instrument_id,
            con_id=series.con_id,
            isin=series.isin,
            symbol=series.symbol,
            market=series.market,
            currency=series.currency,
            timezone=series.timezone,
            timeframe=self.TIMEFRAME,
            adjustment_policy=series.adjustment_policy,
            calendar_version=series.calendar_version,
            last_closed_session=min(series.last_closed_session, actual_dates[-1]),
            source_bar_hash=series.source_bar_hash,
            dataset_version=series.source_bar_hash,
            bars=bars,
        )
