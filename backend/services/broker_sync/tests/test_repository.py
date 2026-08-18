"""Repository 层单元测试。"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import pytest

from app.database import Base
from services.broker_sync import models as _broker_sync_models  # noqa: F401
from services.broker_sync.repository import PositionSnapshotRepository
from services.broker_sync.schema import Position


@pytest.fixture
def db_session():
    """每个测试一个内存 SQLite,测试完即销毁。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def make_position(symbol="AAPL.US", **overrides):
    """构造一个最小可用的 Position。"""
    defaults = dict(
        broker="tiger",
        account_id="4472659",
        symbol=symbol,
        raw_symbol=symbol.split(".")[0],
        name="苹果",
        asset_class="equity",
        market="US",
        quantity=Decimal("60"),
        avg_cost=Decimal("211.83"),
        cost_method="fifo",
        cost_basis=Decimal("12709.80"),
        current_price=Decimal("276.45"),
        market_value=Decimal("16587.00"),
        currency="USD",
        unrealized_pnl=Decimal("3877.20"),
        unrealized_pnl_pct=Decimal("0.305"),
        snapshot_time=datetime.now(timezone.utc),
        sync_source="api",
        raw_data={"foo": "bar"},
    )
    defaults.update(overrides)
    return Position(**defaults)


def test_create_run_returns_id(db_session):
    repo = PositionSnapshotRepository(db_session)
    run = repo.create_run(broker="tiger", account_id="4472659")
    assert run.id is not None
    assert run.status == "running"


def test_persist_positions_writes_all(db_session):
    repo = PositionSnapshotRepository(db_session)
    run = repo.create_run(broker="tiger", account_id="4472659")
    positions = [make_position("AAPL.US"), make_position("00068.HK", currency="HKD", market="HK")]
    repo.persist_positions(run_id=run.id, positions=positions)

    db_session.refresh(run)
    assert run.status == "success"
    assert run.position_count == 2
    assert len(run.snapshots) == 2


def test_mark_run_failed(db_session):
    repo = PositionSnapshotRepository(db_session)
    run = repo.create_run(broker="tiger", account_id="4472659")
    repo.mark_run_failed(run_id=run.id, error_message="测试失败", retry_count=2)

    db_session.refresh(run)
    assert run.status == "failed"
    assert run.error_message == "测试失败"
    assert run.retry_count == 2


def test_decimal_roundtrip(db_session):
    """关键测试:Decimal 存进数据库再取出,精度不能丢。"""
    repo = PositionSnapshotRepository(db_session)
    run = repo.create_run(broker="tiger", account_id="4472659")
    pos = make_position(quantity=Decimal("123.45678901"))
    repo.persist_positions(run_id=run.id, positions=[pos])

    db_session.refresh(run)
    saved = run.snapshots[0]
    assert isinstance(saved.quantity, Decimal)
    assert saved.quantity == Decimal("123.45678901")


def test_canonical_classification_evidence_roundtrip(db_session):
    repo = PositionSnapshotRepository(db_session)
    run = repo.create_run(broker="tiger", account_id="4472659")
    pos = make_position(
        broker_security_type="STK",
        vehicle_type="ETF",
        economic_asset_class="FIXED_INCOME",
        economic_asset_subclass="SHORT_TERM_TREASURY",
        classification_source="issuer_verified_fixture",
        classification_confidence="HIGH",
        classification_verification_status="VERIFIED",
        classification_evidence={"con_id": 79000224, "isin": "IE00B3VWN179"},
    )
    repo.persist_positions(run_id=run.id, positions=[pos])

    saved = run.snapshots[0]
    assert saved.vehicle_type == "ETF"
    assert saved.economic_asset_class == "FIXED_INCOME"
    assert saved.economic_asset_subclass == "SHORT_TERM_TREASURY"
    assert '"con_id": 79000224' in saved.classification_evidence_json
