"""PoC adapter: existing calculations over one enforced read-only SQLite snapshot.

Run only in a dedicated worker; session bindings are restored on exit. No init_db,
HTTP business backend, PEER, broker synchronization, or order service is loaded.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
import logging
import sqlite3

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


class ReadError(Exception):
    pass


@contextmanager
def readonly_snapshot(path, database_contract=None):
    if database_contract:
        from app.shared_sqlite import check_file, load_contract
        check_file(path, load_contract(database_contract))
    path = Path(path).resolve(strict=True)
    raw = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    raw.execute("PRAGMA query_only=ON")
    # Permit queries and transaction control only. Deny INSERT/UPDATE/DDL/ATTACH
    # and mutating PRAGMAs even if a reused service later acquires a write path.
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
               sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION}
    def authorize(action, arg1, arg2, db, trigger):
        if action in allowed:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and arg2 is None:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    raw.set_authorizer(authorize)
    engine = create_engine("sqlite://", creator=lambda: raw, poolclass=StaticPool)
    try:
        with engine.connect() as connection:
            raw.execute("BEGIN")  # Python sqlite legacy mode otherwise skips BEGIN for SELECT.
            transaction = connection.begin()
            try:
                if database_contract:
                    from app.shared_sqlite import verify
                    verify(path, database_contract, connection=raw)
                yield connection
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


@contextmanager
def service_sessions(connection):
    from app import analyzer, models
    from app import database
    from app.utils import position_aggregator
    from backend.services import portfolio_service, wealth_service
    modules = (database, models, analyzer, portfolio_service, wealth_service, position_aggregator)
    original = [(module, module.get_session) for module in modules]
    def factory():
        return Session(bind=connection, autoflush=False,
                       join_transaction_mode="rollback_only")
    try:
        for module in modules:
            module.get_session = factory
        yield wealth_service
    finally:
        for module, function in original:
            module.get_session = function


@contextmanager
def observe_cash():
    from backend.services import portfolio_service
    original = portfolio_service._get_tiger_account_cash
    observation = {"has_details": False}
    def read_cash():
        value, details = original()
        observation["has_details"] = bool(details)
        return value, details
    portfolio_service._get_tiger_account_cash = read_cash
    try:
        yield observation
    finally:
        portfolio_service._get_tiger_account_cash = original


class SourceLogProbe(logging.Handler):
    """Observe warning categories without formatting/persisting sensitive messages."""
    def __init__(self):
        super().__init__()
        self.cash_failed = False
        self.fx_degraded = False
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            if record.name == "backend.services.portfolio_service":
                self.cash_failed = True
            if record.name == "app.fx_service":
                self.fx_degraded = True


def _minor(value):
    # Match existing get_summary round(..., 2); encode only, no new statistics.
    return int(Decimal(str(round(float(value), 2))) * 100)


def read_overview(path, portfolio_id, *, cash_configured=False, now=None, database_contract=None):
    from app.models import Portfolio, Position, WealthItem
    now = now or datetime.now(timezone.utc)
    queried_at = now.isoformat()
    issues = []
    root = logging.getLogger()
    probe = SourceLogProbe()
    old_handlers = root.handlers[:]
    root.handlers = [probe]
    try:
        with readonly_snapshot(path, database_contract) as connection, service_sessions(connection) as wealth, observe_cash() as cash:
            if database_contract:
                from app.shared_sqlite import load_contract
                if load_contract(database_contract)["portfolio_id"] != portfolio_id:
                    raise ReadError("PORTFOLIO_NOT_FOUND")
            with Session(bind=connection, autoflush=False,
                         join_transaction_mode="rollback_only") as session:
                if session.get(Portfolio, portfolio_id) is None:
                    raise ReadError("PORTFOLIO_NOT_FOUND")
                positions = session.query(Position).filter_by(
                    portfolio_id=portfolio_id, segment="投资").all()
                items = session.query(WealthItem).filter_by(portfolio_id=portfolio_id).all()
                if not positions and not items:
                    issues.append("EMPTY_WEALTH_DATA")
                dates = []
                unknown = 0
                stale = 0
                # Manual timestamps are not UTC in the existing model. Keep date
                # precision, do not invent a timezone or call queried_at an update.
                for item in items:
                    if item.included_in_net_worth or item.already_investment_accounted:
                        if item.value_as_of:
                            dates.append(item.value_as_of)
                            if (now.date() - item.value_as_of).days > 30:
                                stale += 1
                        else:
                            unknown += 1
                tables = inspect(connection).get_table_names()
                owners = {(p.broker, p.broker_account_id, p.sync_source) for p in positions}
                for broker, account, sync_source in owners:
                    if not broker or not account or not sync_source or "position_snapshot_runs" not in tables:
                        unknown += 1
                        continue
                    rows = connection.execute(text(
                        "SELECT status, finished_at, started_at FROM position_snapshot_runs "
                        "WHERE portfolio_id=:portfolio AND broker=:broker AND account_id=:account AND sync_source=:source ORDER BY started_at DESC"
                    ), {"portfolio": portfolio_id, "broker": broker, "account": account, "source": sync_source}).mappings().all()
                    if rows and rows[0]["status"] == "failed":
                        issues.append("LATEST_POSITION_SYNC_FAILED")
                    success = next((r for r in rows if r["status"] == "success" and r["finished_at"]), None)
                    if success:
                        updated = datetime.fromisoformat(str(success["finished_at"]))
                        dates.append(updated.date())
                        if (now.date() - updated.date()).days > 1:
                            stale += 1
                    else:
                        unknown += 1
                if unknown:
                    issues.append("VALUATION_DATE_UNKNOWN")
                if stale:
                    issues.append("STALE_COMPONENTS")
                totals = wealth._current_totals(portfolio_id)
                if probe.cash_failed:
                    cash_status = "failed"
                    issues.append("TIGER_CASH_SOURCE_FAILED")
                elif cash_configured:
                    cash_status = "queried" if cash["has_details"] else "queried_zero_or_missing"
                    if cash_status == "queried_zero_or_missing":
                        issues.append("TIGER_CASH_ZERO_OR_SOURCE_EMPTY")
                else:
                    cash_status = "not_configured"
                if probe.fx_degraded:
                    issues.append("LIVE_CASH_FX_DEGRADED")
                # Live cash has query time, but not a broker valuation timestamp.
                if cash_configured:
                    unknown += 1
                    issues.append("LIVE_CASH_VALUATION_DATE_UNKNOWN")
                amounts = {f"{name}_minor": _minor(totals[name])
                           for name in ("total_assets", "total_liabilities", "net_worth")}
                if probe.cash_failed:
                    amounts = {key: None for key in amounts}  # no plausible zero fallback presented as complete.
                return {
                    "schema_version": "poc02.wealth-overview.v1", "synthetic": False,
                    "currency": "CNY", "amount_unit": "minor", "minor_unit_scale": 2,
                    **amounts, "queried_at": queried_at,
                    "data_updated_date": max(dates).isoformat() if dates and not unknown else None,
                    "oldest_component_date": min(dates).isoformat() if dates else None,
                    "status": "unavailable" if probe.cash_failed else ("partial" if issues else "ok"),
                    "issues": sorted(set(issues)),
                    "sources": {"stored_positions": "read", "manual_wealth_items": "read",
                                "tiger_cash": cash_status},
                    "scope": "Existing WealthOverview core net-worth scope, including existing pension exclusions and linked-investment adjustments.",
                    "notice": "Read-only stored wealth overview. Unknown dates and stale data are not live valuations; no trading or account identifiers returned.",
                }
    finally:
        root.handlers = old_handlers
