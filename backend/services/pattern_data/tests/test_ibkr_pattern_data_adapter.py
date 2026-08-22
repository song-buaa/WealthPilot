from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from backend.services.pattern_data.cache import DailyPatternDataCache
from backend.services.pattern_data.contracts import (
    ContractIdentity,
    InstrumentQuery,
    PatternDataStatus,
    RawDailyBar,
    TradingSession,
)
from backend.services.pattern_data.ibkr_adapter import (
    ADJUSTMENT_POLICY,
    IBKRPatternAdapterConfig,
    IBKRPatternDataAdapter,
)
from backend.services.pattern_data.ibkr_source import ScheduleSnapshot
from backend.services.pattern_data.ibkr_source import IBKRHistoricalDataSource


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "contracts.json").read_text(encoding="utf-8")
)
AS_OF = datetime(2026, 8, 20, 15, 0, tzinfo=timezone.utc)
DURATION_COUNTS = {"2 Y": 500, "4 Y": 1000, "6 Y": 1400, "7 Y": 1600}


def _weekdays(count: int, end: date = date(2026, 8, 20)) -> list[date]:
    values: list[date] = []
    current = end
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current -= timedelta(days=1)
    return sorted(values)


class FakeIBKRSource:
    def __init__(
        self,
        alias: str,
        *,
        maximum_count: int = 1600,
        missing_session: date | None = None,
        fail: bool = False,
        delay: float = 0,
    ) -> None:
        self.alias = alias
        self.identity = ContractIdentity(**FIXTURE[alias])
        self.maximum_count = maximum_count
        self.missing_session = missing_session
        self.fail = fail
        self.delay = delay
        self.resolve_calls = 0
        self.schedule_calls = 0
        self.history_calls: list[dict] = []
        self.dates = _weekdays(1600)

    def resolve_contract(self, query: InstrumentQuery) -> ContractIdentity:
        self.resolve_calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise ConnectionError("fixture provider unavailable")
        return self.identity

    def fetch_schedule(self, contract, *, end, num_days, use_rth):
        self.schedule_calls += 1
        zone = ZoneInfo(self.identity.timezone)
        is_us = self.identity.timezone == "US/Eastern"
        start_at = datetime_time(9, 30) if is_us else datetime_time(8, 0)
        end_at = datetime_time(16, 0) if is_us else datetime_time(16, 30)
        sessions = tuple(
            TradingSession(
                ref_date=value,
                start=datetime.combine(value, start_at, zone),
                end=datetime.combine(value, end_at, zone),
            )
            for value in self.dates
        )
        return ScheduleSnapshot(self.identity.timezone, sessions)

    def fetch_historical_bars(
        self,
        contract,
        *,
        end,
        duration,
        bar_size,
        what_to_show,
        use_rth,
    ):
        self.history_calls.append({
            "duration": duration,
            "bar_size": bar_size,
            "what_to_show": what_to_show,
            "use_rth": use_rth,
        })
        count = min(DURATION_COUNTS[duration], self.maximum_count)
        dates = self.dates[-count:]
        if self.missing_session:
            dates = [value for value in dates if value != self.missing_session]
        bars = []
        for index, value in enumerate(dates):
            base = 50 + index / 10
            # Deliberate AAPL split-like discontinuity. The adapter must not
            # re-adjust IBKR TRADES; its adjustment policy is explicit.
            if self.alias == "AAPL" and index < max(1, len(dates) // 2):
                base *= 2
            bars.append(
                RawDailyBar.from_values(
                    value,
                    base,
                    base + 1,
                    base - 1,
                    base + 0.25,
                    1_000_000 + index,
                )
            )
        return tuple(bars)


class PaginatedScheduleFakeIBKRSource(FakeIBKRSource):
    def __init__(self, alias: str) -> None:
        super().__init__(alias)
        self.schedule_requests: list[dict] = []

    def fetch_schedule(self, contract, *, end, num_days, use_rth):
        self.schedule_calls += 1
        self.schedule_requests.append({"end": end, "num_days": num_days})
        zone = ZoneInfo(self.identity.timezone)
        local_end = end.astimezone(zone)
        eligible = [value for value in self.dates if value <= local_end.date()]
        dates = eligible[-num_days:]
        is_us = self.identity.timezone == "US/Eastern"
        start_at = datetime_time(9, 30) if is_us else datetime_time(8, 0)
        end_at = datetime_time(16, 0) if is_us else datetime_time(16, 30)
        return ScheduleSnapshot(
            self.identity.timezone,
            tuple(
                TradingSession(
                    ref_date=value,
                    start=datetime.combine(value, start_at, zone),
                    end=datetime.combine(value, end_at, zone),
                )
                for value in dates
            ),
        )


def _query(alias: str) -> InstrumentQuery:
    item = FIXTURE[alias]
    return InstrumentQuery(
        symbol=alias,
        exchange=item["exchange"],
        currency=item["currency"],
        con_id=item["con_id"],
        primary_exchange=item["primary_exchange"],
    )


def _adapter(source: FakeIBKRSource, target: int = 10, **cache_kwargs):
    return IBKRPatternDataAdapter(
        source,
        config=IBKRPatternAdapterConfig(target_bar_count=target),
        cache=DailyPatternDataCache(**cache_kwargs),
    )


@pytest.mark.parametrize("alias", ["AAPL", "SPY", "CBU3", "IB01"])
def test_canonical_contract_covers_all_stage_zero_instruments(alias):
    source = FakeIBKRSource(alias)
    result = _adapter(source).get_series(_query(alias), as_of=AS_OF)

    assert result.status is PatternDataStatus.READY
    series = result.series
    assert series is not None
    assert series.conId == FIXTURE[alias]["con_id"]
    assert series.isin == FIXTURE[alias]["isin"]
    assert series.instrument_id == f"IBKR:{series.conId}"
    assert series.timezone == FIXTURE[alias]["timezone"]
    assert series.adjustment_policy == ADJUSTMENT_POLICY
    assert len(series.source_bar_hash) == 64
    assert len(series.bars) == 10


def test_schedule_trims_unfinished_us_daily_bar_without_fixed_utc_offset():
    source = FakeIBKRSource("AAPL")
    result = _adapter(source).get_series(_query("AAPL"), as_of=AS_OF)

    assert result.series is not None
    assert result.series.last_closed_session == date(2026, 8, 19)
    assert result.series.bars[-1].date == date(2026, 8, 19)
    assert all(bar.date != date(2026, 8, 20) for bar in result.series.bars)


def test_schedule_timezone_allows_closed_lse_session_at_same_utc_instant():
    source = FakeIBKRSource("IB01")
    result = _adapter(source).get_series(_query("IB01"), as_of=AS_OF)

    assert result.series is not None
    assert result.series.timezone == "MET"
    assert result.series.last_closed_session == date(2026, 8, 20)
    assert result.series.bars[-1].date == date(2026, 8, 20)


def test_historical_contract_is_split_adjusted_trades_and_never_locally_readjusted():
    source = FakeIBKRSource("AAPL")
    result = _adapter(source).get_series(_query("AAPL"), as_of=AS_OF)

    assert result.status is PatternDataStatus.READY
    assert source.history_calls == [{
        "duration": "2 Y",
        "bar_size": "1 day",
        "what_to_show": "TRADES",
        "use_rth": True,
    }]
    assert result.series.adjustment_policy == (
        "IBKR_TRADES_SPLIT_ADJUSTED_DIVIDENDS_UNADJUSTED"
    )


def test_missing_expected_session_is_quality_blocked_without_fill_forward():
    missing = date(2026, 8, 18)
    source = FakeIBKRSource("CBU3", missing_session=missing)
    result = _adapter(source).get_series(_query("CBU3"), as_of=AS_OF)

    assert result.status is PatternDataStatus.DATA_QUALITY_BLOCKED
    assert result.series is None
    assert result.missing_sessions == (missing,)
    assert "missing" in result.reason


def test_bounded_history_expands_to_1460_capacity():
    source = FakeIBKRSource("SPY")
    result = _adapter(source, target=1460).get_series(_query("SPY"), as_of=AS_OF)

    assert result.status is PatternDataStatus.READY
    assert result.series is not None
    assert len(result.series.bars) == 1460
    assert result.requested_durations == ("2 Y", "4 Y", "6 Y", "7 Y")
    assert [call["duration"] for call in source.history_calls] == ["2 Y", "4 Y", "6 Y", "7 Y"]


def test_schedule_is_paged_backwards_with_a_bounded_live_safe_page_size():
    source = PaginatedScheduleFakeIBKRSource("AAPL")
    result = _adapter(source, target=1460).get_series(_query("AAPL"), as_of=AS_OF)

    assert result.status is PatternDataStatus.READY
    assert result.series is not None
    assert len(result.series.bars) == 1460
    assert len(source.schedule_requests) >= 4
    assert all(item["num_days"] <= 365 for item in source.schedule_requests)
    assert all(
        right["end"] < left["end"]
        for left, right in zip(source.schedule_requests, source.schedule_requests[1:])
    )


def test_insufficient_history_fails_closed_after_maximum_duration():
    source = FakeIBKRSource("IB01", maximum_count=1200)
    result = _adapter(source, target=1460).get_series(_query("IB01"), as_of=AS_OF)

    assert result.status is PatternDataStatus.INSUFFICIENT_HISTORY
    assert result.series is None
    assert result.requested_durations[-1] == "7 Y"
    assert len(source.history_calls) == 4


def test_source_bar_hash_is_deterministic_for_equal_canonical_input():
    first = _adapter(FakeIBKRSource("SPY")).get_series(_query("SPY"), as_of=AS_OF)
    second = _adapter(FakeIBKRSource("SPY")).get_series(_query("SPY"), as_of=AS_OF)

    assert first.series is not None and second.series is not None
    assert first.series.source_bar_hash == second.series.source_bar_hash
    assert first.series.calendar_version == second.series.calendar_version


def test_daily_read_through_cache_hit_and_explicit_refresh():
    source = FakeIBKRSource("AAPL")
    adapter = _adapter(source)

    adapter.get_series(_query("AAPL"), as_of=AS_OF)
    adapter.get_series(_query("AAPL"), as_of=AS_OF)
    assert source.resolve_calls == 1
    assert len(source.history_calls) == 1

    adapter.get_series(_query("AAPL"), as_of=AS_OF, refresh=True)
    assert source.resolve_calls == 2
    assert len(source.history_calls) == 2


def test_concurrent_cache_misses_are_single_flight_deduplicated():
    source = FakeIBKRSource("SPY", delay=0.05)
    adapter = _adapter(source)
    barrier = threading.Barrier(3)
    results = []

    def load():
        barrier.wait()
        results.append(adapter.get_series(_query("SPY"), as_of=AS_OF))

    threads = [threading.Thread(target=load), threading.Thread(target=load)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 2
    assert all(result.status is PatternDataStatus.READY for result in results)
    assert source.resolve_calls == 1
    assert len(source.history_calls) == 1


def test_data_unavailable_negative_cache_expires_after_short_ttl():
    now = [100.0]
    source = FakeIBKRSource("IB01", fail=True)
    adapter = _adapter(
        source,
        positive_ttl_seconds=1000,
        negative_ttl_seconds=5,
        clock=lambda: now[0],
    )

    first = adapter.get_series(_query("IB01"), as_of=AS_OF)
    second = adapter.get_series(_query("IB01"), as_of=AS_OF)
    assert first.status is PatternDataStatus.DATA_UNAVAILABLE
    assert second.status is PatternDataStatus.DATA_UNAVAILABLE
    assert source.resolve_calls == 1

    now[0] += 6
    third = adapter.get_series(_query("IB01"), as_of=AS_OF)
    assert third.status is PatternDataStatus.DATA_UNAVAILABLE
    assert source.resolve_calls == 2


def test_naive_as_of_is_rejected_instead_of_assuming_a_fixed_offset():
    with pytest.raises(ValueError, match="timezone-aware"):
        _adapter(FakeIBKRSource("AAPL")).get_series(
            _query("AAPL"), as_of=datetime(2026, 8, 20, 15, 0)
        )


def test_production_source_uses_readonly_dedicated_loop_and_value_snapshots(monkeypatch):
    calls = []

    class FakeRuntime:
        async def connectAsync(self, **kwargs):
            calls.append(("connect", threading.current_thread().name, kwargs))

        async def reqContractDetailsAsync(self, contract):
            calls.append(("details", threading.current_thread().name, contract))
            contract.conId = 265598
            contract.symbol = "AAPL"
            contract.localSymbol = "AAPL"
            contract.secType = "STK"
            contract.exchange = "SMART"
            contract.primaryExchange = "NASDAQ"
            contract.currency = "USD"
            return [SimpleNamespace(
                contract=contract,
                secIdList=[SimpleNamespace(tag="ISIN", value="US0378331005")],
                stockType="COMMON",
                timeZoneId="US/Eastern",
            )]

        async def reqHistoricalScheduleAsync(self, contract, **kwargs):
            calls.append(("schedule", threading.current_thread().name, kwargs))
            return SimpleNamespace(
                timeZone="US/Eastern",
                sessions=[SimpleNamespace(
                    refDate="20260819",
                    startDateTime="20260819-09:30:00",
                    endDateTime="20260819-16:00:00",
                )],
            )

        async def reqHistoricalDataAsync(self, contract, **kwargs):
            calls.append(("history", threading.current_thread().name, kwargs))
            return [SimpleNamespace(
                date="20260819", open=100, high=101, low=99,
                close=100.5, volume=1000,
            )]

        def disconnect(self):
            calls.append(("disconnect", threading.current_thread().name, {}))

    monkeypatch.setattr("ib_async.IB", FakeRuntime)
    source = IBKRHistoricalDataSource(client_id=97, timeout=1)
    identity = source.resolve_contract(_query("AAPL"))
    schedule = source.fetch_schedule(
        identity, end=AS_OF, num_days=30, use_rth=True,
    )
    bars = source.fetch_historical_bars(
        identity,
        end=AS_OF,
        duration="2 Y",
        bar_size="1 day",
        what_to_show="TRADES",
        use_rth=True,
    )
    source.shutdown()

    assert identity == ContractIdentity(**FIXTURE["AAPL"])
    assert schedule.timezone == "US/Eastern"
    assert bars[0].session_date == date(2026, 8, 19)
    assert all(item[1] == "ibkr-pattern-data-loop" for item in calls)
    connect = next(item[2] for item in calls if item[0] == "connect")
    history = next(item[2] for item in calls if item[0] == "history")
    assert connect["readonly"] is True
    assert connect["fetchFields"].value == 0
    assert history["barSizeSetting"] == "1 day"
    assert history["whatToShow"] == "TRADES"
    assert history["useRTH"] is True
    assert history["keepUpToDate"] is False
