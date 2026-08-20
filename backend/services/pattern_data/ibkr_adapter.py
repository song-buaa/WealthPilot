"""IBKR raw historical data -> CanonicalPatternSeries adapter."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .cache import DailyPatternDataCache
from .contracts import (
    CanonicalPatternBar,
    CanonicalPatternSeries,
    InstrumentQuery,
    PatternDataResult,
    PatternDataStatus,
    RawDailyBar,
    build_source_bar_hash,
    validate_bar,
)
from .ibkr_source import PatternHistoricalDataSource, ScheduleSnapshot


ADJUSTMENT_POLICY = "IBKR_TRADES_SPLIT_ADJUSTED_DIVIDENDS_UNADJUSTED"
CALENDAR_POLICY_VERSION = "IBKR_SCHEDULE_V1"


class PatternDataQualityError(ValueError):
    pass


@dataclass(frozen=True)
class IBKRPatternAdapterConfig:
    target_bar_count: int = 1460
    durations: tuple[str, ...] = ("2 Y", "4 Y", "6 Y", "7 Y")
    schedule_calendar_days: int = 2200

    def __post_init__(self) -> None:
        if self.target_bar_count <= 0:
            raise ValueError("target_bar_count must be positive")
        if not self.durations:
            raise ValueError("at least one bounded duration is required")
        if self.schedule_calendar_days < self.target_bar_count:
            raise ValueError("schedule calendar span must cover target bars")


class IBKRPatternDataAdapter:
    """Fail-closed canonicalization boundary for Pattern Engine input."""

    def __init__(
        self,
        source: PatternHistoricalDataSource,
        *,
        config: IBKRPatternAdapterConfig | None = None,
        cache: DailyPatternDataCache | None = None,
    ) -> None:
        self._source = source
        self._config = config or IBKRPatternAdapterConfig()
        self._cache = cache or DailyPatternDataCache()

    def get_series(
        self,
        query: InstrumentQuery,
        *,
        as_of: datetime | None = None,
        refresh: bool = False,
    ) -> PatternDataResult:
        observed_at = as_of or datetime.now(timezone.utc)
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        key = (
            "ibkr-pattern-daily-v1",
            query.cache_key(),
            self._config.target_bar_count,
            tuple(self._config.durations),
            observed_at.astimezone(timezone.utc).date().isoformat(),
        )
        return self._cache.get_or_load(
            key,
            lambda: self._load(query, observed_at),
            refresh=refresh,
        )

    def _load(self, query: InstrumentQuery, observed_at: datetime) -> PatternDataResult:
        requested: list[str] = []
        try:
            identity = self._source.resolve_contract(query)
            if not identity.isin:
                return PatternDataResult(
                    PatternDataStatus.DATA_QUALITY_BLOCKED,
                    reason="ContractDetails did not provide ISIN",
                )
            schedule = self._source.fetch_schedule(
                identity,
                end=observed_at,
                num_days=self._config.schedule_calendar_days,
                use_rth=True,
            )
            closed_sessions = tuple(
                session for session in schedule.sessions if session.end <= observed_at
            )
            if not closed_sessions:
                return PatternDataResult(
                    PatternDataStatus.DATA_UNAVAILABLE,
                    reason="IBKR SCHEDULE returned no closed session",
                )
            last_closed = max(session.ref_date for session in closed_sessions)

            for duration in self._config.durations:
                requested.append(duration)
                raw = self._source.fetch_historical_bars(
                    identity,
                    end=observed_at,
                    duration=duration,
                    bar_size="1 day",
                    what_to_show="TRADES",
                    use_rth=True,
                )
                normalized = self._normalize(raw, last_closed)
                blocked = self._quality_gate(
                    normalized,
                    schedule,
                    observed_at,
                    requested,
                )
                if blocked is not None:
                    return blocked
                if len(normalized) < self._config.target_bar_count:
                    continue

                selected = normalized[-self._config.target_bar_count:]
                canonical = tuple(
                    CanonicalPatternBar(
                        date=bar.session_date,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume,
                    )
                    for bar in selected
                )
                calendar_version = _calendar_version(schedule, selected[0].session_date, last_closed)
                series = CanonicalPatternSeries(
                    instrument_id=identity.instrument_id,
                    con_id=identity.con_id,
                    isin=identity.isin,
                    symbol=identity.symbol,
                    market=identity.market,
                    currency=identity.currency,
                    timezone=schedule.timezone,
                    adjustment_policy=ADJUSTMENT_POLICY,
                    calendar_version=calendar_version,
                    last_closed_session=last_closed,
                    source_bar_hash=build_source_bar_hash(canonical),
                    bars=canonical,
                )
                return PatternDataResult(
                    PatternDataStatus.READY,
                    series=series,
                    requested_durations=tuple(requested),
                )
        except PatternDataQualityError as exc:
            return PatternDataResult(
                PatternDataStatus.DATA_QUALITY_BLOCKED,
                reason=f"IBKR historical-data quality gate failed: {exc}",
                requested_durations=tuple(requested),
            )
        except (ConnectionError, TimeoutError, RuntimeError, ValueError, KeyError) as exc:
            return PatternDataResult(
                PatternDataStatus.DATA_UNAVAILABLE,
                reason=f"IBKR historical-data query failed: {exc}",
                requested_durations=tuple(requested),
            )

        return PatternDataResult(
            PatternDataStatus.INSUFFICIENT_HISTORY,
            reason=(
                f"maximum bounded duration {self._config.durations[-1]} returned fewer than "
                f"{self._config.target_bar_count} closed bars"
            ),
            requested_durations=tuple(requested),
        )

    @staticmethod
    def _normalize(
        raw: tuple[RawDailyBar, ...],
        last_closed_session: date,
    ) -> tuple[RawDailyBar, ...]:
        closed = tuple(bar for bar in raw if bar.session_date <= last_closed_session)
        dates = [bar.session_date for bar in closed]
        if dates != sorted(dates):
            raise PatternDataQualityError("historical bars are not in ascending session order")
        if len(dates) != len(set(dates)):
            raise PatternDataQualityError("historical bars contain duplicate sessions")
        try:
            for bar in closed:
                validate_bar(bar)
        except ValueError as exc:
            raise PatternDataQualityError(str(exc)) from exc
        return closed

    @staticmethod
    def _quality_gate(
        bars: tuple[RawDailyBar, ...],
        schedule: ScheduleSnapshot,
        observed_at: datetime,
        requested: list[str],
    ) -> PatternDataResult | None:
        if not bars:
            return None
        closed_schedule = tuple(sorted(
            (
                session for session in schedule.sessions
                if session.end <= observed_at and session.ref_date >= bars[0].session_date
            ),
            key=lambda session: session.ref_date,
        ))
        if not closed_schedule or closed_schedule[0].ref_date > bars[0].session_date:
            return PatternDataResult(
                PatternDataStatus.DATA_QUALITY_BLOCKED,
                reason="SCHEDULE coverage does not reach the first returned bar",
                requested_durations=tuple(requested),
            )
        actual = {bar.session_date for bar in bars}
        missing = tuple(
            session.ref_date for session in closed_schedule
            if session.ref_date not in actual
        )
        if missing:
            return PatternDataResult(
                PatternDataStatus.DATA_QUALITY_BLOCKED,
                reason=f"{len(missing)} expected closed session(s) are missing",
                missing_sessions=missing,
                requested_durations=tuple(requested),
            )
        return None


def _calendar_version(
    schedule: ScheduleSnapshot,
    first_session: date,
    last_session: date,
) -> str:
    payload = [
        {
            "date": item.ref_date.isoformat(),
            "start": item.start.isoformat(),
            "end": item.end.isoformat(),
        }
        for item in schedule.sessions
        if first_session <= item.ref_date <= last_session
    ]
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"{CALENDAR_POLICY_VERSION}:{digest}"
