"""TigerSyncService.sync_and_persist 单元测试(mock SDK)。"""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from services.broker_sync import models as _broker_sync_models  # noqa: F401
from services.broker_sync.tiger.sync_service import TigerSyncService


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def mock_service(monkeypatch):
    """构造一个不真正连网的 TigerSyncService。"""
    from core.config import settings
    monkeypatch.setattr(settings, "tiger_id", "20159046")
    monkeypatch.setattr(settings, "tiger_account", "4472659")
    monkeypatch.setattr(settings, "tiger_private_key_path", "fake/path")

    with patch.object(TigerSyncService, "_build_trade_client", return_value=MagicMock()):
        service = TigerSyncService()
    return service


def test_sync_and_persist_success(db_session, mock_service):
    """成功路径:fetch_positions 返回 1 条 → 数据库有 1 个 run + 1 条 snapshot。"""
    from services.broker_sync.schema import Position

    fake_positions = [
        Position(
            broker="tiger", account_id="4472659",
            symbol="AAPL.US", raw_symbol="AAPL", name="苹果",
            asset_class="equity", market="US",
            quantity=Decimal("60"), avg_cost=Decimal("211.83"),
            cost_method="fifo", cost_basis=Decimal("12709.80"),
            current_price=Decimal("276.45"), market_value=Decimal("16587.00"),
            currency="USD",
            unrealized_pnl=Decimal("3877.20"), unrealized_pnl_pct=Decimal("0.305"),
            snapshot_time=datetime.now(timezone.utc),
            sync_source="api", raw_data={},
        ),
    ]

    with patch.object(mock_service, "fetch_positions", return_value=fake_positions):
        run_id = mock_service.sync_and_persist(db_session)

    from services.broker_sync.models import PositionSnapshotRun
    run = db_session.get(PositionSnapshotRun, run_id)
    assert run.status == "success"
    assert run.position_count == 1
    assert len(run.snapshots) == 1


def test_sync_and_persist_data_error_no_retry(db_session, mock_service):
    """数据格式错误立即失败,不重试。"""
    with patch.object(
        mock_service, "fetch_positions",
        side_effect=ValueError("字段缺失")
    ):
        with pytest.raises(ValueError):
            mock_service.sync_and_persist(db_session)

    from services.broker_sync.models import PositionSnapshotRun
    run = db_session.query(PositionSnapshotRun).first()
    assert run.status == "failed"
    assert run.retry_count == 0


def test_sync_and_persist_network_error_retries(db_session, mock_service):
    """网络错误重试 2 次后失败,retry_count == 2。"""
    with patch.object(
        mock_service, "fetch_positions",
        side_effect=ConnectionError("connection failed")
    ):
        with patch("services.broker_sync.tiger.sync_service.time.sleep"):
            with pytest.raises(ConnectionError):
                mock_service.sync_and_persist(db_session)

    from services.broker_sync.models import PositionSnapshotRun
    run = db_session.query(PositionSnapshotRun).first()
    assert run.status == "failed"
    assert run.retry_count == 2
    assert "网络错误" in run.error_message
