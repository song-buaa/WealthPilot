"""Single-process manual/cron dispatch around the existing sync_and_persist.

No scheduler is created here. The Web process lock guarantees one dispatcher;
per-broker locks cover both reservation and execution, including long requests.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
from threading import Lock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import get_session
from backend.api import broker_sync as api
from services.broker_sync.models import PositionSnapshotRun
from services.broker_sync.repository import PositionSnapshotRepository

SUPPORTED = ("tiger", "snowball", "guojin")


class ReadonlyIBKRClient:
    broker_name = "ibkr"

    def __init__(self, adapter):
        self._adapter = adapter

    def get_positions(self):
        return self._adapter.get_positions()

    def get_account_info(self):
        return self._adapter.get_account_info()


class ServiceFactory:
    def __init__(self):
        self.ibkr = None

    def __call__(self, broker):
        if broker == "tiger":
            from services.broker_sync.tiger.sync_service import TigerSyncService
            service = TigerSyncService()
        elif broker == "guojin":
            from services.broker_sync.guojin.sync_service import GuojinSyncService
            service = GuojinSyncService()
        elif broker == "snowball":
            from core.config import settings
            from backend.services.action.brokers.ibkr import IBKRBrokerAdapter
            from services.broker_sync.snowball.sync_service import SnowballSyncService
            if os.environ.get("IBKR_READ_ONLY_MODE") != "true" or os.environ.get("ENABLE_IBKR_LIVE_TRADING") != "false":
                raise ValueError("READONLY_IBKR_REQUIRED")
            if self.ibkr is None:
                self.ibkr = IBKRBrokerAdapter(host=settings.ibkr_host, port=settings.ibkr_port,
                    client_id=settings.ibkr_client_id, account_id=settings.ibkr_account or "")
            service = SnowballSyncService(ibkr_client=ReadonlyIBKRClient(self.ibkr))
        else:
            raise ValueError("BROKER_NOT_SUPPORTED")
        original = service.fetch_positions
        def fetch_nonempty():
            result = original()
            positions = result[0] if isinstance(result, tuple) else result
            if not positions:
                raise ValueError("EMPTY_SOURCE_REQUIRES_REVIEW")
            if any(p.broker != broker or p.account_id in ("", "unknown", "pending", "__unconfigured__") for p in positions) or len({p.account_id for p in positions}) != 1:
                raise ValueError("MIXED_SOURCE_REQUIRES_REVIEW")
            # Use the existing FX service/cache before snapshot/upsert writes.
            from app.fx_service import fx_service
            for currency in {p.currency for p in positions}:
                fx_service.get_rate(currency, "CNY")
            return result
        service.fetch_positions = fetch_nonempty
        return service

    def close(self):
        if self.ibkr is not None:
            self.ibkr.shutdown()


class SyncExecutor:
    def __init__(self, enabled, portfolio_id, factory=None):
        if len(set(enabled)) != len(enabled) or any(b not in SUPPORTED for b in enabled):
            raise ValueError("INVALID_ENABLED_BROKERS")
        self.enabled = tuple(enabled)
        self.portfolio_id = portfolio_id
        self.factory = factory or ServiceFactory()
        self.locks = {b: Lock() for b in self.enabled}

    def recover_interrupted(self):
        # Only our own jobs, after obtaining the process-wide exclusive lock.
        with get_session() as db:
            runs = db.query(PositionSnapshotRun).filter(
                PositionSnapshotRun.status == "running",
                PositionSnapshotRun.triggered_by.like("restricted:%")).all()
            for run in runs:
                run.status = "failed"
                run.finished_at = datetime.now(timezone.utc)
                run.error_message = "SYNC_INTERRUPTED_BY_RESTART"
            db.commit()

    def reserve(self, broker, triggered_by="manual"):
        if triggered_by not in ("manual", "cron"):
            raise ValueError("INVALID_SYNC_TRIGGER")
        if broker != "all" and broker not in self.enabled:
            raise ValueError("BROKER_NOT_ENABLED")
        jobs, busy = [], []
        try:
            for target in self.enabled if broker == "all" else (broker,):
                lock = self.locks[target]
                if not lock.acquire(blocking=False):
                    busy.append(target)
                    continue
                try:
                    with get_session() as db:
                        previous = api._get_last_successful_run(db, target)
                        account = os.environ.get({"tiger": "TIGER_ACCOUNT", "snowball": "IBKR_ACCOUNT",
                                                  "guojin": "GUOJIN_ACCOUNT"}[target])
                        account = account or (previous.account_id if previous else "pending")
                        run = PositionSnapshotRepository(db).create_run(target, account,
                            triggered_by="restricted:" + triggered_by)
                        jobs.append({"broker": target, "run_id": run.id})
                except Exception:
                    lock.release()
                    raise
        except Exception:
            # If reserving an all request fails, release earlier reservations too.
            for job in jobs:
                try:
                    self._fail(job["run_id"], "SYNC_RESERVATION_FAILED")
                except Exception:
                    pass  # Original request still fails (e.g. DB unavailable).
                finally:
                    self.locks[job["broker"]].release()
            raise
        return jobs, busy

    @staticmethod
    def _fail(run_id, code):
        with get_session() as db:
            PositionSnapshotRepository(db).mark_run_failed(run_id, code)

    def execute(self, broker, run_id):
        try:
            with get_session() as db:
                db.info.update(portfolio_id=self.portfolio_id, sync_run_id=run_id)
                try:
                    run = db.get(PositionSnapshotRun, run_id)
                    returned = api._run_sync(broker, run.triggered_by, db_session=db,
                        service_factory=self.factory, raise_errors=True)
                    run = db.get(PositionSnapshotRun, run_id)
                    if returned != run_id or run.status != "success":
                        raise ValueError("SYNC_RESULT_NOT_COMMITTED")
                except Exception:
                    db.rollback()
                    raise
        except Exception:
            # Never expose raw provider errors, account IDs or credentials.
            self._fail(run_id, "SYNC_SOURCE_FAILED")
        finally:
            self.locks[broker].release()

    def status(self):
        # Existing API supplies last-attempt/last-success fields; extend safely.
        response = api.get_sync_status().model_dump()
        with get_session() as db:
            for item in response["brokers"]:
                broker = item["broker"]
                item["enabled"] = broker in self.enabled
                item["running"] = broker in self.locks and self.locks[broker].locked()
                run = api._get_last_run(db, broker)
                item["last_attempt_id"] = run.id if run else None
                for key in ("error_message", "last_attempt_error_message"):
                    if item[key]:
                        item[key] = "SYNC_INTERRUPTED_BY_RESTART" if item[key] == "SYNC_INTERRUPTED_BY_RESTART" else "SYNC_SOURCE_FAILED"
        return response
