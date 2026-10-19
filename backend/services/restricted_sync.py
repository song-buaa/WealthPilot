"""Manual/cron dispatch; SnapshotRun claims fence writers across processes.

No scheduler is created here. Local locks only avoid redundant reservations.
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

SUPPORTED = ("tiger", "futu", "snowball", "guojin")


class ReadonlyIBKRClient:
    broker_name = "ibkr"

    def __init__(self, adapter):
        self._adapter = adapter

    def get_positions(self):
        return self._adapter.get_positions()

    def get_account_info(self):
        return self._adapter.get_account_info()


class ServiceFactory:
    def __init__(self, *, restricted=True):
        self.ibkr = None
        self.restricted = restricted

    def __call__(self, broker):
        if broker == "tiger":
            from services.broker_sync.tiger.sync_service import TigerSyncService
            service = TigerSyncService()
        elif broker == "futu":
            from services.broker_sync.futu.sync_service import FutuSyncService
            service = FutuSyncService()
        elif broker == "guojin":
            from services.broker_sync.guojin.sync_service import GuojinSyncService
            service = GuojinSyncService()
        elif broker == "snowball":
            from core.config import settings
            from backend.services.action.brokers.ibkr import IBKRBrokerAdapter
            from services.broker_sync.snowball.sync_service import SnowballSyncService
            if self.restricted and (os.environ.get("IBKR_READ_ONLY_MODE") != "true" or os.environ.get("ENABLE_IBKR_LIVE_TRADING") != "false"):
                raise ValueError("READONLY_IBKR_REQUIRED")
            if not self.restricted and settings.snowball_account and settings.ibkr_account:
                raise ValueError("AMBIGUOUS_SNOWBALL_IBKR_SOURCE")
            if not self.restricted and not settings.ibkr_account:
                return SnowballSyncService()
            if not self.restricted:
                from backend.api.action import _get_ibkr_adapter
                service = SnowballSyncService(ibkr_client=ReadonlyIBKRClient(_get_ibkr_adapter()))
                return service
            if self.ibkr is None:
                self.ibkr = IBKRBrokerAdapter(host=settings.ibkr_host, port=settings.ibkr_port,
                    client_id=settings.ibkr_client_id, account_id=settings.ibkr_account or "")
            service = SnowballSyncService(ibkr_client=ReadonlyIBKRClient(self.ibkr))
        else:
            raise ValueError("BROKER_NOT_SUPPORTED")
        return service

    def close(self):
        if self.ibkr is not None:
            self.ibkr.shutdown()


class SyncExecutor:
    """All entrypoints reserve through the original durable SnapshotRun."""
    def __init__(self, enabled, portfolio_id, factory=None, *, session_factory=None, account_resolver=None):
        if len(set(enabled)) != len(enabled) or any(b not in SUPPORTED for b in enabled):
            raise ValueError("INVALID_ENABLED_BROKERS")
        self.enabled, self.portfolio_id = tuple(enabled), portfolio_id
        self.factory = factory or ServiceFactory()
        self.session_factory = session_factory or get_session
        self.account_resolver = account_resolver or api._sync_account
        self.locks = {b:Lock() for b in self.enabled}
        self.claims = {}
        self.reused = {}

    def recover_interrupted(self):
        # Another process may legitimately be reading. Only expired leases are
        # recoverable; legacy NULL-context runs require explicit operator review.
        with self.session_factory() as db:
            db.info["portfolio_id"] = self.portfolio_id
            db.execute(__import__('sqlalchemy').text("BEGIN IMMEDIATE"))
            from services.broker_sync.repository import context
            now = datetime.now(timezone.utc)
            runs = db.query(PositionSnapshotRun).filter_by(portfolio_id=self.portfolio_id,status="running").all()
            for run in runs:
                lease = context(run).get("lease_until")
                if lease and lease <= now.isoformat():
                    run.status, run.finished_at = "failed", now
                    run.error_message = "SYNC_LEASE_EXPIRED"
            db.commit()

    def reserve(self, broker, triggered_by="manual", *, freshness_seconds=None):
        if triggered_by not in ("manual", "cron", "analysis", "fill"):
            raise ValueError("INVALID_SYNC_TRIGGER")
        if broker != "all" and broker not in self.enabled:
            raise ValueError("BROKER_NOT_ENABLED")
        from services.broker_sync.repository import SyncBusy, SyncReusable
        self.reused = {}
        jobs, busy = [], []
        try:
            for target in self.enabled if broker == "all" else (broker,):
                lock = self.locks[target]
                if not lock.acquire(blocking=False):
                    busy.append(target); continue
                try:
                    with self.session_factory() as db:
                        db.info["portfolio_id"] = self.portfolio_id
                        account = self.account_resolver(db, target)
                        run = PositionSnapshotRepository(db).create_run(target, account,
                            triggered_by=("restricted:" if getattr(self.factory,"restricted",False) else "")+triggered_by,
                            freshness_seconds=freshness_seconds, fill_only=triggered_by=="fill")
                        if run is None:
                            lock.release()
                            continue
                        run_id = run.id
                        self.claims[run_id] = db.info["sync_claims"][run_id]
                        jobs.append({"broker":target,"run_id":run_id})
                except SyncBusy:
                    lock.release(); busy.append(target)
                except SyncReusable as recent:
                    lock.release(); self.reused[target] = recent.run_id
                except ValueError as error:
                    lock.release()
                    if broker == "all" and str(error) == "SYNC_ACCOUNT_UNCONFIGURED":
                        continue  # failed attempt is recorded; configured sources still run
                    raise
                except Exception:
                    lock.release(); raise
        except Exception:
            for job in jobs:
                self._fail(job["run_id"], "SYNC_RESERVATION_FAILED")
                self.locks[job["broker"]].release()
            raise
        return jobs, busy

    def _fail(self, run_id, code):
        with self.session_factory() as db:
            db.info.update(portfolio_id=self.portfolio_id, sync_claims={run_id:self.claims.get(run_id)})
            PositionSnapshotRepository(db).mark_run_failed(run_id, code)

    def execute(self, broker, run_id):
        try:
            with self.session_factory() as db:
                db.info.update(portfolio_id=self.portfolio_id, sync_run_id=run_id,
                               sync_claims={run_id:self.claims.get(run_id)})
                run = db.get(PositionSnapshotRun, run_id)
                if run is None or run.portfolio_id != self.portfolio_id or run.broker != broker:
                    raise ValueError("SYNC_RUN_BINDING_INVALID")
                PositionSnapshotRepository(db)._check_claim(run)
                triggered_by = run.triggered_by
                db.info["sync_account_id"] = run.account_id
                db.rollback()
                service = self.factory(broker)
                try:
                    returned = service.sync_and_persist(db, triggered_by=triggered_by)
                    if returned != run_id:
                        raise ValueError("SYNC_RESULT_NOT_COMMITTED")
                    return run_id
                finally:
                    close = getattr(service,"close",None)
                    if close: close()
        except Exception as exc:
            code = str(exc) if str(exc) in api.SAFE_SYNC_CODES else "SYNC_SOURCE_FAILED"
            self._fail(run_id,code)
            return run_id
        finally:
            self.claims.pop(run_id,None)
            if self.locks[broker].locked(): self.locks[broker].release()

    def status(self):
        with self.session_factory() as db:
            db.info["portfolio_id"] = self.portfolio_id
            response = api.get_sync_status(db_session=db).model_dump()
            for item in response["brokers"]:
                broker = item["broker"]
                item["enabled"] = broker in self.enabled
                run = api._get_last_run(db, broker)
                item["running"] = bool(run and run.status == "running")
                item["last_attempt_id"] = run.id if run else None
                for key in ("error_message","last_attempt_error_message"):
                    if item[key] and item[key] not in api.SAFE_SYNC_CODES:
                        item[key] = "SYNC_SOURCE_FAILED"
            return response

    def sync_fills(self):
        """Original poller invokes after observation commits; no extra scheduler.

        Re-read due work under the original DB claim, not an in-memory queue.
        A crashed caller leaves order progress and a recoverable leased run.
        Never enable a source/account by discovering an order in the database.
        """
        from app.fill_sync import due, orders, SUPPORTED
        self.recover_interrupted()
        jobs = []
        for broker in (b for b in self.enabled if b in SUPPORTED.values()):
            with self.session_factory() as db:
                db.info["portfolio_id"] = self.portfolio_id
                account = self.account_resolver(db,broker)
                trading = "ibkr" if broker=="snowball" else broker
                if not due(orders(db,self.portfolio_id,trading,account)):
                    continue
            try:
                reserved, _ = self.reserve(broker,"fill")
                for job in reserved:
                    jobs.append(job)
                    self.execute(job["broker"],job["run_id"])
            except ValueError:
                # Ownership/configuration remains fail-closed. The original
                # durable pending orders survive until corrected/retried.
                continue
        return jobs
