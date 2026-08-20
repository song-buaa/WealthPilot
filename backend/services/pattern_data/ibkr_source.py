"""Read-only ib_async source for ContractDetails, SCHEDULE, and Daily TRADES.

The source owns a dedicated asyncio loop/thread.  No ib_async runtime object is
returned to the calling thread, and this class deliberately exposes no order API.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from .contracts import ContractIdentity, InstrumentQuery, RawDailyBar, TradingSession


class PatternDataSourceError(ConnectionError):
    pass


@dataclass(frozen=True)
class ScheduleSnapshot:
    timezone: str
    sessions: tuple[TradingSession, ...]


class PatternHistoricalDataSource(Protocol):
    def resolve_contract(self, query: InstrumentQuery) -> ContractIdentity: ...

    def fetch_schedule(
        self,
        contract: ContractIdentity,
        *,
        end: datetime,
        num_days: int,
        use_rth: bool,
    ) -> ScheduleSnapshot: ...

    def fetch_historical_bars(
        self,
        contract: ContractIdentity,
        *,
        end: datetime,
        duration: str,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
    ) -> tuple[RawDailyBar, ...]: ...


class IBKRHistoricalDataSource:
    """Production-capable, read-only IBKR historical-data source."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 4001,
        client_id: int = 31,
        timeout: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ib = None
        self._connected = False

    def _run_on_loop(self, operation, *, timeout: float | None = None):
        if self._loop is None or not self._loop.is_running():
            raise PatternDataSourceError("IBKR historical-data event loop is not running")
        call_timeout = timeout if timeout is not None else self._timeout

        async def invoke():
            result = operation()
            if inspect.isawaitable(result):
                return await asyncio.wait_for(result, timeout=call_timeout)
            return result

        future = asyncio.run_coroutine_threadsafe(invoke(), self._loop)
        try:
            return future.result(timeout=call_timeout + 1)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"IBKR historical-data call timed out ({call_timeout}s)") from exc

    def _ensure_connected(self) -> None:
        if self._connected and self._ib is not None:
            return

        from ib_async import IB, StartupFetch

        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever,
                daemon=True,
                name="ibkr-pattern-data-loop",
            )
            self._thread.start()

        async def connect():
            self._ib = IB()
            await self._ib.connectAsync(
                host=self._host,
                port=self._port,
                clientId=self._client_id,
                timeout=self._timeout,
                readonly=True,
                fetchFields=StartupFetch(0),
            )

        try:
            self._run_on_loop(connect, timeout=self._timeout + 5)
        except Exception as exc:
            self._ib = None
            raise PatternDataSourceError(
                f"IBKR historical-data connection failed ({self._host}:{self._port}): {exc}"
            ) from exc
        self._connected = True

    def resolve_contract(self, query: InstrumentQuery) -> ContractIdentity:
        self._ensure_connected()

        async def resolve():
            from ib_async import Contract, Stock

            if query.con_id:
                candidate = Contract(
                    conId=query.con_id,
                    exchange=query.exchange,
                    currency=query.currency,
                )
            else:
                candidate = Stock(
                    query.symbol,
                    query.exchange,
                    query.currency,
                    primaryExchange=query.primary_exchange,
                )
            details = await self._ib.reqContractDetailsAsync(candidate)
            unique = {
                int(detail.contract.conId): detail
                for detail in details
                if int(detail.contract.conId or 0) > 0
            }
            if query.con_id:
                unique = {
                    con_id: detail for con_id, detail in unique.items()
                    if con_id == query.con_id
                }
            if len(unique) != 1:
                raise PatternDataSourceError(
                    f"contract resolution expected 1 candidate, got {len(unique)}"
                )
            detail = next(iter(unique.values()))
            contract = detail.contract
            sec_ids = {
                str(item.tag).upper(): str(item.value)
                for item in (getattr(detail, "secIdList", None) or [])
            }
            exchange = str(contract.exchange or query.exchange)
            primary = str(contract.primaryExchange or query.primary_exchange)
            market = exchange if exchange and exchange != "SMART" else primary or exchange
            con_id = int(contract.conId)
            return ContractIdentity(
                instrument_id=f"IBKR:{con_id}",
                con_id=con_id,
                isin=sec_ids.get("ISIN", ""),
                symbol=str(contract.symbol or query.symbol),
                local_symbol=str(contract.localSymbol or ""),
                market=market,
                exchange=exchange,
                primary_exchange=primary,
                currency=str(contract.currency or query.currency),
                sec_type=str(contract.secType or ""),
                stock_type=str(getattr(detail, "stockType", "") or ""),
                timezone=str(getattr(detail, "timeZoneId", "") or ""),
            )

        return self._run_on_loop(resolve, timeout=self._timeout + 10)

    def fetch_schedule(
        self,
        contract: ContractIdentity,
        *,
        end: datetime,
        num_days: int,
        use_rth: bool,
    ) -> ScheduleSnapshot:
        self._ensure_connected()

        async def load():
            response = await self._ib.reqHistoricalScheduleAsync(
                self._to_ib_contract(contract),
                numDays=num_days,
                endDateTime=end.astimezone(timezone.utc),
                useRTH=use_rth,
            )
            timezone_id = str(response.timeZone or contract.timezone)
            sessions = tuple(
                TradingSession(
                    ref_date=_parse_ib_date(item.refDate),
                    start=_parse_ib_datetime(item.startDateTime, timezone_id),
                    end=_parse_ib_datetime(item.endDateTime, timezone_id),
                )
                for item in response.sessions
            )
            return ScheduleSnapshot(timezone=timezone_id, sessions=sessions)

        return self._run_on_loop(load, timeout=self._timeout + 10)

    def fetch_historical_bars(
        self,
        contract: ContractIdentity,
        *,
        end: datetime,
        duration: str,
        bar_size: str,
        what_to_show: str,
        use_rth: bool,
    ) -> tuple[RawDailyBar, ...]:
        self._ensure_connected()

        async def load():
            response = await self._ib.reqHistoricalDataAsync(
                self._to_ib_contract(contract),
                endDateTime=end.astimezone(timezone.utc),
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
                keepUpToDate=False,
                timeout=self._timeout,
            )
            return tuple(
                RawDailyBar.from_values(
                    _parse_ib_date(item.date),
                    item.open,
                    item.high,
                    item.low,
                    item.close,
                    item.volume,
                )
                for item in response
            )

        return self._run_on_loop(load, timeout=self._timeout + 5)

    @staticmethod
    def _to_ib_contract(contract: ContractIdentity):
        from ib_async import Contract

        return Contract(
            conId=contract.con_id,
            symbol=contract.symbol,
            localSymbol=contract.local_symbol,
            secType=contract.sec_type,
            exchange=contract.exchange,
            primaryExchange=contract.primary_exchange,
            currency=contract.currency,
        )

    def shutdown(self) -> None:
        if self._ib is not None and self._connected:
            try:
                self._run_on_loop(lambda: self._ib.disconnect())
            except Exception:
                pass
            self._connected = False
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=5)
        self._ib = None
        self._loop = None
        self._thread = None


def _parse_ib_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unsupported IBKR date: {text}")


def _parse_ib_datetime(value: str, timezone_id: str) -> datetime:
    text = str(value).strip()
    for fmt in ("%Y%m%d-%H:%M:%S", "%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=ZoneInfo(timezone_id))
        except ValueError:
            pass
    raise ValueError(f"unsupported IBKR session datetime: {text}")
