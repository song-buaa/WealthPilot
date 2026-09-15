"""同步状态 API：最近一次尝试与最近一次成功必须分开表达。"""
from datetime import datetime, timedelta, timezone
import sys
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from services.broker_sync import models as _broker_sync_models  # noqa: F401
from services.broker_sync.models import PositionSnapshotRun


def test_status_keeps_failed_attempt_separate_from_previous_success(monkeypatch):
    from backend.api import broker_sync as api

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    now = datetime.now(timezone.utc)
    session.add_all([
        PositionSnapshotRun(
            broker="tiger", account_id="account", started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(hours=1), status="success", position_count=11,
            triggered_by="manual",
        ),
        PositionSnapshotRun(
            broker="tiger", account_id="account", started_at=now,
            finished_at=now, status="failed",
            error_message="数据格式错误(不重试): RuntimeError: TIGER_ID 未配置",
            triggered_by="manual",
        ),
    ])
    session.commit()
    monkeypatch.setattr(api, "get_session", lambda: session)
    monkeypatch.setitem(
        sys.modules,
        "backend.core.demo_mode",
        SimpleNamespace(PUBLIC_DEMO_MODE=False),
    )

    result = api.get_sync_status()
    tiger = next(item for item in result.brokers if item.broker == "tiger")

    assert tiger.last_attempt_status == "failed"
    assert tiger.last_attempt_error_message.endswith("TIGER_ID 未配置")
    assert tiger.last_successful_sync_time is not None
    assert tiger.last_successful_position_count == 11
    assert tiger.last_sync_status == "failed"  # 旧字段也不能继续伪装为 success
    session.close()
