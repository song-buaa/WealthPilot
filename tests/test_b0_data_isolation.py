"""Explicit Portfolio/account ownership, empty snapshot and LSE isolation."""
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Portfolio, Position

from app.utils.position_aggregator import aggregate


@pytest.fixture(autouse=True)
def backend_imports(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    global PositionSnapshotRun, PositionSnapshotRepository, PositionUpsertService, IBKRPortfolioAdapter
    from services.broker_sync.models import PositionSnapshotRun
    from services.broker_sync.repository import PositionSnapshotRepository
    from services.broker_sync.position_upsert_service import PositionUpsertService
    from services.broker_sync.snowball.adapter import IBKRPortfolioAdapter


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path/'scope.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, info={"portfolio_id": 11}) as session:
        session.add_all([Portfolio(id=11, name="Owner"), Portfolio(id=12, name="Other")])
        session.commit()
        yield session
    engine.dispose()


def row(pid, account, symbol="AAPL:US", broker="tiger"):
    return Position(portfolio_id=pid, name="Synthetic", ticker=symbol.split(":")[0], symbol=symbol,
        platform="老虎证券", broker=broker, broker_account_id=account, sync_source="api", segment="投资",
        asset_class="权益", quantity=3, currency="USD", market_value_cny=200, cost_price=10, current_price=11)


def test_complete_empty_snapshot_is_bound_to_run_and_exact_owner(db):
    db.add_all([row(11, "account-a"), row(12, "account-b"), row(11, "account-c")])
    db.commit()
    service = PositionUpsertService(db)
    with pytest.raises(ValueError, match="COMPLETE_ACCOUNT"):
        service.upsert_from_snapshots([], broker="tiger", account_id="account-a", sync_source="api")
    with pytest.raises(ValueError, match="EMPTY_SNAPSHOT_RUN_OWNER"):
        service.upsert_from_snapshots([], broker="tiger", account_id="account-a", sync_source="api", complete_snapshot=True)
    PositionSnapshotRepository(db).synchronize("tiger", "account-a", lambda: [], retries=0,
        metadata=lambda: {"complete": True, "empty_confirmed": True, "source": "synthetic"})
    assert {(p.portfolio_id, p.broker_account_id) for p in db.query(Position)} == {(12, "account-b"), (11, "account-c")}


def test_duplicate_physical_account_and_run_rebinding_fail_without_deletion(db):
    db.add_all([row(11, "same-account"), row(12, "same-account")]); db.commit()
    repo = PositionSnapshotRepository(db)
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        repo.create_run("tiger", "same-account")
    db.rollback()
    assert db.query(Position).count() == 2
    run = repo.create_run("tiger", "unique-account")
    db.info["sync_run_id"] = run.id
    with pytest.raises(ValueError, match="BINDING_INVALID"):
        repo.create_run("tiger", "different-account")
    db.info["portfolio_id"] = 12
    with pytest.raises(ValueError, match="BINDING_INVALID"):
        repo.create_run("tiger", "same-account")


def test_legacy_null_run_never_claims_positions_in_another_portfolio(db):
    legacy = PositionSnapshotRun(broker="tiger", account_id="account-a", status="success")
    db.add(legacy); db.commit()
    with pytest.raises(ValueError, match="EMPTY_SNAPSHOT_RUN_OWNER"):
        PositionUpsertService(db).upsert_from_snapshots([], broker="tiger", account_id="account-a", sync_source="api",
                                                     complete_snapshot=True, run_id=legacy.id)


@pytest.mark.parametrize("con_id,symbol,primary", [(79000224, "CBU3", "EBS"), (354802220, "IB01", "LSEETF")])
def test_trusted_lse_identity_does_not_depend_on_usd_or_primary_exchange(con_id, symbol, primary):
    raw = {"con_id": con_id, "local_symbol": symbol, "primary_exchange": primary,
           "exchange": "SMART", "currency": "USD", "sec_type": "STK"}
    position = IBKRPortfolioAdapter("synthetic-account").security_to_position(raw, datetime.now(timezone.utc))
    assert position.symbol == f"{symbol}:LSE" and position.raw_symbol == symbol
    assert position.raw_data["source_channel"] == "ibkr" and position.broker == "snowball"
    with pytest.raises(ValueError, match="UNKNOWN_LISTING"):
        IBKRPortfolioAdapter._market({"con_id": 1, "currency": "USD", "exchange": "SMART"})


def test_aggregation_keeps_same_ticker_different_listing_or_currency_separate():
    def item(symbol, currency):
        return dict(name="Same name", ticker="ABC", symbol=symbol, currency=currency, platform="broker",
                    asset_class="权益", market_value_cny=100, profit_loss_value=0, cost_price=10,
                    current_price=10, quantity=10, profit_loss_rate=0, is_leverage_etf=False)
    positions, total = aggregate([item("ABC:US", "USD"), item("ABC:LSE", "USD"), item("ABC:LSE", "GBP")])
    assert len(positions) == 3 and total == 300


def test_nonshared_multiple_portfolios_never_inherit_legacy_first_portfolio(db, monkeypatch):
    from backend.services.action.ownership import portfolio_scope
    from app import state
    monkeypatch.delenv('WEALTHPILOT_SQLITE_CONTRACT', raising=False)
    monkeypatch.delenv('WEALTHPILOT_PORTFOLIO_ID', raising=False)
    monkeypatch.setattr(state, 'portfolio_id', 11)
    db.info.pop('portfolio_id')
    with pytest.raises(ValueError, match='EXPLICIT_PORTFOLIO_REQUIRED'):
        portfolio_scope(db)
    monkeypatch.setenv('WEALTHPILOT_PORTFOLIO_ID', '12')
    assert portfolio_scope(db) == 12
