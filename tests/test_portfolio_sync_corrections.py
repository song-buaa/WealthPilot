"""Dashboard / IBKR / Futu 持仓同步准确性定向回归。"""
from datetime import datetime, timezone
from decimal import Decimal
import asyncio
import inspect
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, inspect as sqlalchemy_inspect, text
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.database import Base, _ensure_position_ownership_columns  # noqa: E402
from app.models import Portfolio, Position as BusinessPosition  # noqa: E402
from backend.services.action.brokers.ibkr import IBKRBrokerAdapter  # noqa: E402
from services.broker_sync import models as _broker_models  # noqa: F401,E402
from services.broker_sync.futu.sync_service import FutuSyncService  # noqa: E402
from services.broker_sync.models import PositionSnapshot, PositionSnapshotRun  # noqa: E402
from services.broker_sync.position_upsert_service import PositionUpsertService  # noqa: E402
from services.broker_sync.snowball.adapter import IBKRPortfolioAdapter  # noqa: E402


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Portfolio(id=1, name="测试组合"))
    session.commit()
    yield session
    session.close()


def _business_position(
    *, ticker: str, symbol: str, platform: str, broker: str | None,
    account_id: str | None, sync_source: str | None,
) -> BusinessPosition:
    return BusinessPosition(
        portfolio_id=1,
        name=ticker,
        ticker=ticker,
        symbol=symbol,
        platform=platform,
        broker=broker,
        broker_account_id=account_id,
        sync_source=sync_source,
        asset_class="权益",
        currency="USD",
        quantity=1,
        cost_price=1,
        current_price=1,
        market_value_cny=7,
        original_currency="USD",
        original_value=1,
        fx_rate_to_cny=7,
        segment="投资",
    )


def _snapshot(
    *, symbol: str, broker: str = "futu", account_id: str = "FUTU-1",
    asset_class: str = "equity", name: str = "测试资产",
) -> PositionSnapshot:
    return PositionSnapshot(
        run_id=1,
        broker=broker,
        account_id=account_id,
        snapshot_time=datetime.now(timezone.utc),
        symbol=symbol,
        raw_symbol=symbol.split(":", 1)[0],
        name=name,
        asset_class=asset_class,
        market="US",
        quantity=Decimal("2"),
        avg_cost=Decimal("10"),
        cost_method="weighted_average",
        cost_basis=Decimal("20"),
        current_price=Decimal("12"),
        market_value=Decimal("24"),
        currency="USD",
        unrealized_pnl=Decimal("4"),
        unrealized_pnl_pct=Decimal("0.2"),
        raw_data_json="{}",
    )


def _fake_fx(amount, _from_ccy, _to_ccy="CNY", _date="latest"):
    return amount * 7, 7, "2026-08-14"


def test_dashboard_import_tabs_are_api_fund_csv_order():
    source = (ROOT / "frontend/src/pages/Dashboard.tsx").read_text()
    fund_import_source = (ROOT / "frontend/src/components/FundEImportTab.tsx").read_text()
    tab_block = source[source.index("{/* Tab 行 */}"):source.index("{/* ── 通用 CSV ── */}")]
    assert "('api-sync')" in source
    assert tab_block.index("API 同步") < tab_block.index("基金账户") < tab_block.index("通用 CSV")
    assert "基金E账户" not in source + fund_import_source


def test_existing_sqlite_positions_table_gets_idempotent_ownership_columns():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE positions (id INTEGER PRIMARY KEY, symbol VARCHAR(30))"))

    _ensure_position_ownership_columns(engine)
    _ensure_position_ownership_columns(engine)

    columns = {column["name"] for column in sqlalchemy_inspect(engine).get_columns("positions")}
    assert {"broker", "broker_account_id", "sync_source"}.issubset(columns)


def test_ibkr_contract_mapping_uses_metadata_not_symbol():
    assert IBKRPortfolioAdapter.map_asset_class({"sec_type": "STK", "long_name": "Apple Inc."}) == "equity"
    assert IBKRPortfolioAdapter.map_asset_class({"sec_type": "BOND"}) == "bond"
    # 真实 IB01 是 STK + LSEETF；由 Contract longName 的国债语义归固收，不按 IB01 特判。
    assert IBKRPortfolioAdapter.map_asset_class({
        "sec_type": "STK",
        "exchange": "LSEETF",
        "long_name": "ISHARES US TREAS 0-1YR USD A",
    }) == "bond"


def test_ibkr_cash_uses_currency_cash_balance_only():
    mapper = IBKRPortfolioAdapter("U-TEST")
    rows = mapper.to_positions(
        securities=[],
        cash_balances=[
            {"currency": "BASE", "amount": 300},
            {"currency": "USD", "amount": 200},
            {"currency": "CNH", "amount": 100},
        ],
    )
    assert {row.currency for row in rows} == {"USD", "CNH"}
    assert all(row.asset_class == "cash" for row in rows)
    assert all(row.raw_data["tag"] == "CashBalance" for row in rows)


def test_ibkr_account_read_exposes_cash_balance_not_valuation_fields():
    adapter = IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
    adapter._account_id = "U-TEST"
    adapter._ensure_connected = MagicMock()
    adapter._ib = MagicMock()
    adapter._ib.accountSummaryAsync = AsyncMock(return_value=[
        MagicMock(tag="TotalCashValue", value="300", currency="USD"),
        MagicMock(tag="NetLiquidation", value="900", currency="USD"),
        MagicMock(tag="BuyingPower", value="1200", currency="USD"),
    ])
    adapter._ib.accountValues.return_value = [
        MagicMock(tag="CashBalance", value="300", currency="BASE"),
        MagicMock(tag="CashBalance", value="200", currency="USD"),
        MagicMock(tag="AvailableFunds", value="700", currency="USD"),
    ]

    def run_immediately(operation, **_kwargs):
        result = operation()
        return asyncio.run(result) if inspect.isawaitable(result) else result

    adapter._run_on_loop = run_immediately
    info = adapter.get_account_info()
    assert info["cash_balances"] == [{"currency": "USD", "amount": 200.0}]
    assert info["TotalCashValue"] == 300.0  # 保留账户摘要，但不作为持仓输入。
    assert info["BuyingPower"] == 1200.0


def test_successful_snapshot_removes_only_stale_owned_positions(db_session):
    db_session.add_all([
        _business_position(ticker="A", symbol="A:US", platform="富途证券", broker="futu", account_id="FUTU-1", sync_source="api"),
        _business_position(ticker="B", symbol="B:US", platform="富途证券", broker="futu", account_id="FUTU-1", sync_source="api"),
        _business_position(ticker="B2", symbol="B2:US", platform="富途证券", broker="futu", account_id="FUTU-2", sync_source="api"),
        _business_position(ticker="CSV", symbol="CSV:US", platform="富途证券", broker=None, account_id=None, sync_source=None),
        _business_position(ticker="C", symbol="C:US", platform="雪盈证券", broker="snowball", account_id="IB-1", sync_source="api"),
    ])
    db_session.commit()

    with patch("services.broker_sync.position_upsert_service.fx_service.convert", side_effect=_fake_fx):
        report = PositionUpsertService(db_session).upsert_from_snapshots(
            [_snapshot(symbol="A:US")],
            broker="futu", account_id="FUTU-1", sync_source="api",
        )

    assert report["removed"] == 1
    assert {row.ticker for row in db_session.query(BusinessPosition).all()} == {"A", "B2", "CSV", "C"}


def test_ibkr_bond_metadata_corrects_previously_legal_equity_class(db_session):
    db_session.add(_business_position(
        ticker="IB01", symbol="IB01:US", platform="雪盈证券",
        broker="snowball", account_id="IB-1", sync_source="api",
    ))
    db_session.commit()

    with patch("services.broker_sync.position_upsert_service.fx_service.convert", side_effect=_fake_fx):
        PositionUpsertService(db_session).upsert_from_snapshots(
            [_snapshot(
                symbol="IB01:US", broker="snowball", account_id="IB-1",
                asset_class="bond", name="ISHARES US TREAS 0-1YR USD A",
            )],
            broker="snowball", account_id="IB-1", sync_source="api",
        )

    assert db_session.query(BusinessPosition).one().asset_class == "固收"


def test_successful_empty_snapshot_clears_only_exact_scope(db_session):
    db_session.add_all([
        _business_position(ticker="A", symbol="A:US", platform="富途证券", broker="futu", account_id="FUTU-1", sync_source="api"),
        _business_position(ticker="B", symbol="B:US", platform="富途证券", broker="futu", account_id="FUTU-1", sync_source="api"),
        _business_position(ticker="C", symbol="C:US", platform="老虎证券", broker="tiger", account_id="T-1", sync_source="api"),
        _business_position(ticker="FUND", symbol="FUND:US", platform="基金账户", broker=None, account_id=None, sync_source=None),
    ])
    db_session.commit()

    report = PositionUpsertService(db_session).upsert_from_snapshots(
        [], broker="futu", account_id="FUTU-1", sync_source="api",
    )

    assert report["removed"] == 2
    assert {row.ticker for row in db_session.query(BusinessPosition).all()} == {"C", "FUND"}


def test_successful_empty_claims_and_removes_legacy_row_with_snapshot_evidence(db_session):
    run = PositionSnapshotRun(
        broker="futu", account_id="FUTU-1", sync_source="api",
        status="success", position_count=1,
    )
    db_session.add(run)
    db_session.flush()
    old_snapshot = _snapshot(symbol="A:US")
    old_snapshot.run_id = run.id
    db_session.add_all([
        old_snapshot,
        _business_position(
            ticker="A", symbol="A:US", platform="富途证券",
            broker=None, account_id=None, sync_source=None,
        ),
    ])
    db_session.commit()

    report = PositionUpsertService(db_session).upsert_from_snapshots(
        [], broker="futu", account_id="FUTU-1", sync_source="api",
    )

    assert report["removed"] == 1
    assert db_session.query(BusinessPosition).count() == 0


def test_failed_futu_fetch_preserves_previous_positions_and_marks_failed(db_session):
    db_session.add(_business_position(
        ticker="A", symbol="A:US", platform="富途证券",
        broker="futu", account_id="FUTU-1", sync_source="api",
    ))
    db_session.commit()

    service = FutuSyncService.__new__(FutuSyncService)
    service.account_id = "FUTU-1"
    service.fetch_positions = MagicMock(side_effect=TimeoutError("OpenD timeout"))
    with patch("services.broker_sync.futu.sync_service.time.sleep"):
        with pytest.raises(TimeoutError):
            service.sync_and_persist(db_session)

    assert db_session.query(BusinessPosition).filter_by(ticker="A").count() == 1
    run = db_session.query(PositionSnapshotRun).one()
    assert run.status == "failed"
    assert run.position_count is None
