"""
WealthPilot 券商同步 API 路由。

提供:
- GET  /api/broker-sync/status       查询各 broker 最近同步状态
- POST /api/broker-sync/trigger      手动触发同步(指定 broker 或全部)
"""
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import desc

from app.database import get_session

router = APIRouter()


class SyncStatusItem(BaseModel):
    broker: str
    platform: str
    last_sync_time: Optional[str] = None
    last_sync_status: Optional[str] = None
    last_position_count: Optional[int] = None
    error_message: Optional[str] = None


class SyncStatusResponse(BaseModel):
    brokers: list[SyncStatusItem]


class TriggerRequest(BaseModel):
    broker: Literal["tiger", "futu", "all"] = "all"
    triggered_by: str = "manual"


class TriggerResponse(BaseModel):
    message: str
    brokers_triggered: list[str]


BROKER_PLATFORM_MAP = {
    "tiger": "老虎证券",
    "futu": "富途证券",
}


def _get_last_run(db, broker: str):
    """查询某 broker 最近一次 run。"""
    import sys, os
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from services.broker_sync.models import PositionSnapshotRun
    return (
        db.query(PositionSnapshotRun)
        .filter_by(broker=broker)
        .order_by(desc(PositionSnapshotRun.started_at))
        .first()
    )


@router.get("/status", response_model=SyncStatusResponse)
def get_sync_status():
    """查询各 broker 最近同步状态。"""
    import sys, os
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from services.broker_sync.models import PositionSnapshotRun

    db = get_session()
    try:
        brokers = []
        for broker, platform in BROKER_PLATFORM_MAP.items():
            run = _get_last_run(db, broker)
            if run:
                brokers.append(SyncStatusItem(
                    broker=broker,
                    platform=platform,
                    last_sync_time=run.started_at.strftime("%Y-%m-%d %H:%M:%S") if run.started_at else None,
                    last_sync_status=run.status,
                    last_position_count=run.position_count,
                    error_message=run.error_message,
                ))
            else:
                brokers.append(SyncStatusItem(
                    broker=broker,
                    platform=platform,
                    last_sync_status="never",
                ))
        return SyncStatusResponse(brokers=brokers)
    finally:
        db.close()


def _run_sync(broker: str, triggered_by: str = "manual"):
    """实际执行同步（在 BackgroundTask 或 scheduler 里调用）。"""
    # 确保 backend/ 在 sys.path 中（sync_service 内部用 from core.config / from services.broker_sync）
    import sys, os
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    db = get_session()
    try:
        if broker == "tiger":
            # 延迟 import 避免双重注册：_run_sync 在 backend/ 上下文执行
            from services.broker_sync.tiger.sync_service import TigerSyncService  # noqa: E402
            service = TigerSyncService()
            service.sync_and_persist(db, triggered_by=triggered_by)
        elif broker == "futu":
            from services.broker_sync.futu.sync_service import FutuSyncService  # noqa: E402
            service = FutuSyncService()
            service.sync_and_persist(db, triggered_by=triggered_by)
            service.close()
        print(f"[broker-sync] {broker} 同步完成 @ {datetime.now()}", flush=True)
    except Exception as e:
        import traceback
        print(f"[broker-sync] {broker} 同步失败: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
    finally:
        db.close()


@router.post("/trigger", response_model=TriggerResponse)
def trigger_sync(req: TriggerRequest, background_tasks: BackgroundTasks):
    """手动触发同步。异步执行,立即返回。"""
    brokers_to_run = ["tiger", "futu"] if req.broker == "all" else [req.broker]

    for broker in brokers_to_run:
        background_tasks.add_task(_run_sync, broker, req.triggered_by)

    return TriggerResponse(
        message="同步任务已提交,后台执行中",
        brokers_triggered=brokers_to_run,
    )
