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
    last_successful_run_id: Optional[int] = None
    data_quality: Optional[dict] = None


class SyncStatusResponse(BaseModel):
    brokers: list[SyncStatusItem]


class TriggerRequest(BaseModel):
    broker: Literal["tiger", "futu", "snowball", "guojin", "all"] = "all"
    triggered_by: str = "manual"


class TriggerResponse(BaseModel):
    message: str
    brokers_triggered: list[str]


SAFE_SYNC_CODES = {"SYNC_LEASE_EXPIRED","SYNC_ACCOUNT_UNCONFIGURED","SYNC_SOURCE_FAILED",
    "EMPTY_SOURCE_REQUIRES_REVIEW","INCOMPLETE_SOURCE","FX_RATE_UNAVAILABLE",
    "DUPLICATE_SNAPSHOT_IDENTITY","MIXED_SNAPSHOT_SCOPE","SYNC_CLAIM_EXPIRED_OR_REPLACED",
    "ACCOUNT_PORTFOLIO_AMBIGUOUS","SYNC_RESERVATION_FAILED",
    "SOURCE_ACCOUNT_UNPROVEN","CASH_IDENTITY_COLLISION_REQUIRES_REVIEW"}


def safe_sync_reason(run):
    """Shared safe failure projection; never forward raw SDK/connection text."""
    if run is None:
        return None
    if not run.error_message:
        return "SYNC_SOURCE_FAILED" if run.status == "failed" else None
    return run.error_message if run.error_message in SAFE_SYNC_CODES else "SYNC_SOURCE_FAILED"


def public_sync_attempt(run):
    from datetime import timezone
    stamp = run.started_at if run else None
    if stamp is not None and stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return {"last_attempt_id":run.id if run else None,
            "last_attempt_time":stamp.isoformat() if stamp else None,
            "last_attempt_status":run.status if run else "never",
            "last_attempt_reason_code":safe_sync_reason(run)}


def _public_quality(run):
    if run is None:
        return {"quality":"unavailable","issues":["NO_SUCCESSFUL_SYNC"]}
    import json
    meta = json.loads(run.context_json or "{}") or {"quality":"partial","issues":["SOURCE_EVIDENCE_UNKNOWN"]}
    return {key:meta.get(key) for key in ("quality","issues","source","complete","coverage","markets","empty_confirmed","fetched_at",
        "positions_as_of","cash_as_of","price_as_of","currencies","row_count","fx")}


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
        .filter(PositionSnapshotRun.account_id.in_([_sync_account(db, broker), "pending", "__unconfigured__"]))
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


def get_sync_status(db_session=None):
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

    db = db_session if db_session is not None else get_session()
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
                    error_message=safe_sync_reason(attempt),
                    last_attempt_time=_format_beijing_time(attempt.started_at),
                    last_attempt_status=attempt.status,
                    last_attempt_position_count=attempt.position_count,
                    last_attempt_error_message=safe_sync_reason(attempt),
                    last_successful_sync_time=(
                        _format_beijing_time(successful_run.started_at)
                        if successful_run else None
                    ),
                    last_successful_position_count=(successful_run.position_count if successful_run else None),
                    last_successful_run_id=successful_run.id if successful_run else None,
                    data_quality=_public_quality(successful_run),
                ))
            else:
                brokers.append(SyncStatusItem(
                    broker=broker,
                    platform=platform,
                    last_sync_status="never",
                ))
        return SyncStatusResponse(brokers=brokers)
    finally:
        if db_session is None:
            db.close()


def _executor():
    from backend.services.restricted_sync import SyncExecutor, ServiceFactory
    with get_session() as db:
        pid = _portfolio_scope(db)
    import os
    configured = os.environ.get("WEALTHPILOT_ENABLED_SYNC_BROKERS")
    enabled = tuple(b.strip() for b in configured.split(",") if b.strip()) if configured is not None else tuple(BROKER_PLATFORM_MAP)
    return SyncExecutor(enabled, pid, factory=ServiceFactory(restricted=False))


def _run_sync(broker: str, triggered_by: str = "manual", *, db_session=None,
              service_factory=None, raise_errors=False):
    """Compatibility entrypoint for existing 22:00 cron/CLI and injected tests."""
    try:
        if db_session is not None or service_factory is not None:
            # Explicit injection keeps the same Repository claim/publication.
            if db_session is None:
                raise ValueError("EXPLICIT_SYNC_SESSION_REQUIRED")
            return service_factory(broker).sync_and_persist(db_session, triggered_by=triggered_by)
        executor = _executor()
        jobs, _ = executor.reserve(broker, triggered_by)
        for job in jobs:
            executor.execute(job["broker"],job["run_id"])
        return jobs[0]["run_id"] if jobs else None
    except Exception:
        if raise_errors:
            raise
        return None


@router.post("/trigger", response_model=TriggerResponse)
def trigger_sync(req: TriggerRequest, background_tasks: BackgroundTasks):
    """手动触发同步。PUBLIC_DEMO_MODE 下短路。"""
    from backend.core.demo_mode import PUBLIC_DEMO_MODE
    if PUBLIC_DEMO_MODE:
        return TriggerResponse(message="PUBLIC_DEMO_MODE: 同步已禁用", brokers_triggered=[])
    from fastapi import HTTPException
    executor = _executor()
    try:
        jobs, busy = executor.reserve(req.broker,"manual")
    except ValueError:
        raise HTTPException(400,"SYNC_SCOPE_OR_CONFIGURATION_INVALID") from None
    for job in jobs:
        background_tasks.add_task(executor.execute,job["broker"],job["run_id"])
    return TriggerResponse(message="已有同步任务运行中" if busy and not jobs else "同步任务已提交,后台执行中",
        brokers_triggered=[job["broker"] for job in jobs])


@router.get("/status", response_model=SyncStatusResponse)
def sync_status_endpoint():
    return get_sync_status()
