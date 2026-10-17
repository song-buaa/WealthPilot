"""
WealthPilot 券商同步 API 路由。

提供:
- GET  /api/broker-sync/status       查询各 broker 最近同步状态
- POST /api/broker-sync/trigger      手动触发同步(指定 broker 或全部)
"""
from datetime import datetime, timezone
from typing import Literal, Optional

import pytz
from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import desc

from app.database import get_session

_BEIJING_TZ = pytz.timezone("Asia/Shanghai")


def _format_beijing_time(dt) -> Optional[str]:
    """UTC datetime → 北京时间字符串。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")

router = APIRouter()


class SyncStatusItem(BaseModel):
    broker: str
    platform: str
    # 兼容旧客户端：这三个字段表示最近一次尝试，而非最近一次成功。
    last_sync_time: Optional[str] = None
    last_sync_status: Optional[str] = None
    last_position_count: Optional[int] = None
    error_message: Optional[str] = None
    # 明确区分最近尝试与最近成功，避免失败后继续把历史成功显示为当前状态。
    last_attempt_time: Optional[str] = None
    last_attempt_status: Optional[str] = None
    last_attempt_position_count: Optional[int] = None
    last_attempt_error_message: Optional[str] = None
    last_successful_sync_time: Optional[str] = None
    last_successful_position_count: Optional[int] = None


class SyncStatusResponse(BaseModel):
    brokers: list[SyncStatusItem]


class TriggerRequest(BaseModel):
    broker: Literal["tiger", "futu", "snowball", "guojin", "all"] = "all"
    triggered_by: str = "manual"


class TriggerResponse(BaseModel):
    message: str
    brokers_triggered: list[str]


BROKER_PLATFORM_MAP = {
    "tiger": "老虎证券",
    "futu": "富途证券",
    "snowball": "雪盈证券",
    "guojin": "国金证券",
}


def _portfolio_scope(db):
    from backend.services.action.ownership import portfolio_scope
    return portfolio_scope(db)


def _sync_account(db, broker):
    from core.config import settings
    configured = {"tiger": settings.tiger_account, "futu": settings.futu_account,
                  "snowball": settings.ibkr_account or settings.snowball_account}.get(broker)
    if configured:
        return configured
    if broker != "guojin":
        return None
    import os
    if os.environ.get("GUOJIN_ACCOUNT"):
        return os.environ["GUOJIN_ACCOUNT"]
    from services.broker_sync.models import PositionSnapshotRun
    # Only a unique, explicitly owned observed account is usable, never the
    # most recent run or a run whose Portfolio is NULL.
    accounts = {r[0] for r in db.query(PositionSnapshotRun.account_id).filter_by(
        portfolio_id=_portfolio_scope(db), broker=broker, sync_source="api").all()
        if r[0] not in {"pending", "unknown", "__unconfigured__", ""}}
    return next(iter(accounts)) if len(accounts) == 1 else None


def _get_last_run(db, broker: str):
    """查询某 broker 最近一次 run。"""
    import sys, os
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from services.broker_sync.models import PositionSnapshotRun
    return (
        db.query(PositionSnapshotRun)
        .filter_by(portfolio_id=_portfolio_scope(db), broker=broker, sync_source="api")
        .filter(PositionSnapshotRun.account_id.in_([_sync_account(db, broker), "pending"]))
        .order_by(desc(PositionSnapshotRun.started_at))
        .first()
    )


def _get_last_successful_run(db, broker: str):
    import sys, os
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from services.broker_sync.models import PositionSnapshotRun
    return (
        db.query(PositionSnapshotRun)
        .filter_by(portfolio_id=_portfolio_scope(db), broker=broker, account_id=_sync_account(db, broker),
                   sync_source="api", status="success")
        .order_by(desc(PositionSnapshotRun.started_at))
        .first()
    )


@router.get("/status", response_model=SyncStatusResponse)
def get_sync_status():
    """查询各 broker 最近同步状态。"""
    from backend.core.demo_mode import PUBLIC_DEMO_MODE
    if PUBLIC_DEMO_MODE:
        # demo 模式: 不查库，返回静态"已禁用"状态
        return SyncStatusResponse(brokers=[
            SyncStatusItem(broker=b, platform=p, last_sync_status="disabled")
            for b, p in BROKER_PLATFORM_MAP.items()
        ])

    import sys, os
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    from services.broker_sync.models import PositionSnapshotRun

    db = get_session()
    try:
        brokers = []
        for broker, platform in BROKER_PLATFORM_MAP.items():
            attempt = _get_last_run(db, broker)
            successful_run = _get_last_successful_run(db, broker)
            if attempt:
                brokers.append(SyncStatusItem(
                    broker=broker,
                    platform=platform,
                    last_sync_time=_format_beijing_time(attempt.started_at),
                    last_sync_status=attempt.status,
                    last_position_count=attempt.position_count,
                    error_message=attempt.error_message,
                    last_attempt_time=_format_beijing_time(attempt.started_at),
                    last_attempt_status=attempt.status,
                    last_attempt_position_count=attempt.position_count,
                    last_attempt_error_message=attempt.error_message,
                    last_successful_sync_time=(
                        _format_beijing_time(successful_run.started_at)
                        if successful_run else None
                    ),
                    last_successful_position_count=(
                        successful_run.position_count if successful_run else None
                    ),
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


def _run_sync(broker: str, triggered_by: str = "manual", *, db_session=None,
              service_factory=None, raise_errors=False):
    """实际执行同步（在 BackgroundTask 或 scheduler 里调用）。"""
    # 确保 backend/ 在 sys.path 中（sync_service 内部用 from core.config / from services.broker_sync）
    import sys, os
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    db = db_session if db_session is not None else get_session()
    try:
        db.info["portfolio_id"] = _portfolio_scope(db)
        if service_factory is not None:
            service = service_factory(broker)
            return service.sync_and_persist(db, triggered_by=triggered_by)
        elif broker == "tiger":
            # 延迟 import 避免双重注册：_run_sync 在 backend/ 上下文执行
            from services.broker_sync.tiger.sync_service import TigerSyncService  # noqa: E402
            service = TigerSyncService()
            service.sync_and_persist(db, triggered_by=triggered_by)
        elif broker == "futu":
            from services.broker_sync.futu.sync_service import FutuSyncService  # noqa: E402
            service = FutuSyncService()
            service.sync_and_persist(db, triggered_by=triggered_by)
            service.close()
        elif broker == "snowball":
            from services.broker_sync.snowball.sync_service import SnowballSyncService  # noqa: E402
            from backend.services.restricted_sync import ReadonlyIBKRClient
            from backend.api.action import _get_ibkr_adapter
            from core.config import settings
            if settings.snowball_account and settings.ibkr_account:
                raise ValueError("AMBIGUOUS_SNOWBALL_IBKR_SOURCE")
            service = (SnowballSyncService(ibkr_client=ReadonlyIBKRClient(_get_ibkr_adapter()))
                       if settings.ibkr_account else SnowballSyncService())
            service.sync_and_persist(db, triggered_by=triggered_by)
        elif broker == "guojin":
            from services.broker_sync.guojin.sync_service import GuojinSyncService  # noqa: E402
            service = GuojinSyncService()
            service.sync_and_persist(db, triggered_by=triggered_by)
        print(f"[broker-sync] {broker} 同步完成 @ {datetime.now()}", flush=True)

        # ── 统一后处理：补全裸代码名称（如雪盈 LI → 理想汽车）──
        from services.broker_sync.position_upsert_service import backfill_bare_names
        backfill_bare_names(db)
    except Exception as e:
        if raise_errors:
            raise
        import traceback
        print(f"[broker-sync] {broker} 同步失败: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
    finally:
        if db_session is None:
            db.close()


@router.post("/trigger", response_model=TriggerResponse)
def trigger_sync(req: TriggerRequest, background_tasks: BackgroundTasks):
    """手动触发同步。PUBLIC_DEMO_MODE 下短路。"""
    from backend.core.demo_mode import PUBLIC_DEMO_MODE
    if PUBLIC_DEMO_MODE:
        return TriggerResponse(message="PUBLIC_DEMO_MODE: 同步已禁用", brokers_triggered=[])
    brokers_to_run = ["tiger", "futu", "snowball", "guojin"] if req.broker == "all" else [req.broker]

    for broker in brokers_to_run:
        background_tasks.add_task(_run_sync, broker, req.triggered_by)

    return TriggerResponse(
        message="同步任务已提交,后台执行中",
        brokers_triggered=brokers_to_run,
    )
