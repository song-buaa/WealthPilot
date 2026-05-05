"""PositionUpsertService 单元测试。"""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Portfolio, Position as BusinessPosition
from services.broker_sync import models as _bs_models  # noqa: F401
from services.broker_sync.models import PositionSnapshot, PositionSnapshotRun
from services.broker_sync.position_upsert_service import PositionUpsertService


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Position.portfolio_id 是 FK,需要先建 Portfolio
    portfolio = Portfolio(id=1, name="测试组合")
    session.add(portfolio)
    session.commit()

    yield session
    session.close()


@pytest.fixture
def mock_fx():
    """mock 汇率服务,USD→CNY 用 7.2,HKD→CNY 用 0.92"""
    def fake_convert(amount, from_ccy, to_ccy="CNY", date="latest"):
        rates = {"USD": 7.2, "HKD": 0.92, "CNY": 1.0}
        rate = rates.get(from_ccy, 1.0)
        return amount * rate, rate, "2026-05-05"

    with patch(
        "services.broker_sync.position_upsert_service.fx_service.convert",
        side_effect=fake_convert,
    ):
        yield


def make_snapshot(symbol="AAPL.US", broker="tiger", currency="USD",
                  quantity=Decimal("60"), market_value=Decimal("16587"),
                  unrealized_pnl=Decimal("3877.20"),
                  unrealized_pnl_pct=Decimal("0.305"),
                  asset_class="equity", market="US",
                  name="苹果", run_id=1):
    """构造 PositionSnapshot ORM 对象。"""
    return PositionSnapshot(
        run_id=run_id,
        broker=broker,
        account_id="4472659",
        snapshot_time=datetime.now(timezone.utc),
        symbol=symbol,
        raw_symbol=symbol.split(".")[0],
        name=name,
        asset_class=asset_class,
        market=market,
        quantity=quantity,
        avg_cost=Decimal("211.83"),
        cost_method="fifo",
        cost_basis=quantity * Decimal("211.83"),
        current_price=Decimal("276.45"),
        market_value=market_value,
        currency=currency,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_pct=unrealized_pnl_pct,
        raw_data_json='{}',
    )


def test_insert_new_position(db_session, mock_fx):
    """新建 Position 行,所有字段正确填充。"""
    run = PositionSnapshotRun(broker="tiger", account_id="4472659", status="success")
    db_session.add(run)
    db_session.commit()

    snap = make_snapshot(run_id=run.id)
    db_session.add(snap)
    db_session.commit()

    service = PositionUpsertService(db_session)
    report = service.upsert_from_snapshots([snap])

    assert report["inserted"] == 1
    assert report["updated"] == 0

    pos = db_session.query(BusinessPosition).first()
    assert pos.ticker == "AAPL"
    assert pos.platform == "老虎证券"
    assert pos.quantity == 60
    assert pos.original_currency == "USD"
    assert pos.fx_rate_to_cny == 7.2
    assert abs(pos.market_value_cny - 119426.4) < 0.01
    assert pos.segment == "投资"


def test_update_protects_user_modified_fields(db_session, mock_fx):
    """更新已有 Position 行时,name/asset_class/segment 受保护。"""
    run = PositionSnapshotRun(broker="tiger", account_id="4472659", status="success")
    db_session.add(run)
    db_session.commit()

    # 1. 先 insert 一条
    snap1 = make_snapshot(run_id=run.id, name="苹果", market_value=Decimal("16000"))
    db_session.add(snap1)
    db_session.commit()
    PositionUpsertService(db_session).upsert_from_snapshots([snap1])

    # 2. 用户手动修改 name 和 segment
    pos = db_session.query(BusinessPosition).first()
    pos.name = "我的苹果"
    pos.segment = "持有"
    db_session.commit()

    # 3. 再 update
    snap2 = make_snapshot(run_id=run.id, name="Apple Inc.", market_value=Decimal("17000"))
    PositionUpsertService(db_session).upsert_from_snapshots([snap2])

    pos = db_session.query(BusinessPosition).first()
    assert pos.name == "我的苹果"
    assert pos.segment == "持有"
    assert abs(pos.market_value_cny - 17000 * 7.2) < 0.01


def test_hk_ticker_denormalization(db_session, mock_fx):
    """港股 00068.HK → ticker=00068 (保留前导零)。"""
    run = PositionSnapshotRun(broker="tiger", account_id="4472659", status="success")
    db_session.add(run)
    db_session.commit()

    snap = make_snapshot(
        run_id=run.id, symbol="00068.HK", currency="HKD",
        market="HK", name="MANYCORE TECH",
        market_value=Decimal("12380"),
    )
    db_session.add(snap)
    db_session.commit()

    PositionUpsertService(db_session).upsert_from_snapshots([snap])

    pos = db_session.query(BusinessPosition).first()
    assert pos.ticker == "00068"
    assert pos.original_currency == "HKD"
    assert abs(pos.market_value_cny - 11389.6) < 0.01


def test_multiple_brokers_same_ticker_creates_two_rows(db_session, mock_fx):
    """老虎 MSFT + 富途 MSFT 应该是两行(platform 区分)。"""
    run1 = PositionSnapshotRun(broker="tiger", account_id="4472659", status="success")
    run2 = PositionSnapshotRun(broker="futu", account_id="6169", status="success")
    db_session.add_all([run1, run2])
    db_session.commit()

    snap_tiger = make_snapshot(
        run_id=run1.id, symbol="MSFT.US", broker="tiger",
        quantity=Decimal("12"), market_value=Decimal("4972"),
    )
    snap_futu = make_snapshot(
        run_id=run2.id, symbol="MSFT.US", broker="futu",
        quantity=Decimal("5"), market_value=Decimal("2072"),
    )
    db_session.add_all([snap_tiger, snap_futu])
    db_session.commit()

    PositionUpsertService(db_session).upsert_from_snapshots([snap_tiger, snap_futu])

    rows = db_session.query(BusinessPosition).filter_by(ticker="MSFT").all()
    assert len(rows) == 2
    platforms = {r.platform for r in rows}
    assert platforms == {"老虎证券", "富途证券"}
