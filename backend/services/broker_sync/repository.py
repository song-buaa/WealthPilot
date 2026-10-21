"""Original snapshot repository: durable scoped claims and fenced publication."""
import json
import hashlib
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional
from sqlalchemy import text
from sqlalchemy.orm import Session
from services.broker_sync.models import PositionSnapshot, PositionSnapshotRun
from services.broker_sync.schema import Position


class SyncBusy(ValueError):
    def __init__(self, run_id):
        self.run_id = run_id
        super().__init__("SYNC_ALREADY_RUNNING")


class SyncReusable(ValueError):
    """Analysis can reuse a recent observation or failure cooldown, not resync."""
    def __init__(self, run_id):
        self.run_id = run_id
        super().__init__("SYNC_RECENT_OBSERVATION")


def context(run):
    return json.loads(run.context_json or "{}")


def published_positions(session, run):
    """Fingerprint the actual scoped business projection, never a second calculation.

    Sealed only inside synchronize's Snapshot+Upsert publication transaction.
    Names and creation/row IDs are presentation/storage metadata; all remaining
    Position columns affect identity, quantities, values, classification or scope.
    """
    from app.models import Position as BusinessPosition
    fields = [c.name for c in BusinessPosition.__table__.columns if c.name not in {"id", "name", "created_at"}]
    rows = session.query(BusinessPosition).filter_by(portfolio_id=run.portfolio_id,
        broker=run.broker, broker_account_id=run.account_id, sync_source=run.sync_source).all()
    facts = sorted(json.dumps({key:getattr(row,key) for key in fields}, sort_keys=True,
                              ensure_ascii=False, allow_nan=False) for row in rows)
    return {"version":1, "count":len(facts), "sha256":hashlib.sha256(json.dumps(facts).encode()).hexdigest()}


def publication_status(session, run):
    sealed = context(run).get("published_positions") if run else None
    if not isinstance(sealed, dict) or sealed.get("version") != 1:
        return "unverified"
    return "matched" if sealed == published_positions(session, run) else "diverged"


class PositionSnapshotRepository:
    """One owner claim per run; broker/FX I/O never occurs inside publication."""
    LEASE_SECONDS = 300

    def __init__(self, session: Session):
        self.session = session

    def _portfolio_id(self):
        from backend.services.action.ownership import portfolio_scope
        return portfolio_scope(self.session)

    def _begin(self):
        if self.session.new or self.session.dirty or self.session.deleted:
            raise ValueError("COMMIT_PREPARATION_BEFORE_SYNC")
        self.session.rollback()
        self.session.execute(text("BEGIN IMMEDIATE"))
        self.session.expire_all()

    def _token(self, run_id):
        return self.session.info.get("sync_claims", {}).get(run_id)

    def _check_claim(self, run):
        meta = context(run)
        if (run.portfolio_id != self._portfolio_id() or run.status != "running"
                or not self._token(run.id) or meta.get("token") != self._token(run.id)
                or meta.get("lease_until", "") <= datetime.now(timezone.utc).isoformat()):
            raise ValueError("SYNC_CLAIM_EXPIRED_OR_REPLACED")
        return meta

    def _owner_check(self, portfolio_id, broker, account):
        from app.models import Position as BusinessPosition
        if (self.session.query(BusinessPosition.id).filter(
                BusinessPosition.broker == broker, BusinessPosition.broker_account_id == account,
                BusinessPosition.portfolio_id != portfolio_id).first()
            or self.session.query(PositionSnapshotRun.id).filter(
                PositionSnapshotRun.broker == broker, PositionSnapshotRun.account_id == account,
                PositionSnapshotRun.portfolio_id != portfolio_id).first()):
            raise ValueError("ACCOUNT_PORTFOLIO_AMBIGUOUS")

    def create_run(self, broker, account_id, sync_source="api", triggered_by="manual", *, freshness_seconds=None, fill_only=False):
        portfolio_id = self._portfolio_id()
        existing_id = self.session.info.get("sync_run_id")
        if existing_id is not None:
            run = self.session.get(PositionSnapshotRun, existing_id)
            if (run is None or (run.portfolio_id, run.broker, run.account_id, run.sync_source)
                    != (portfolio_id, broker, account_id, sync_source)):
                raise ValueError("SYNC_RUN_BINDING_INVALID")
            self._check_claim(run)
            return run
        self._begin()
        now = datetime.now(timezone.utc)
        if not account_id or account_id in {"pending", "unknown", "__unconfigured__"}:
            run = PositionSnapshotRun(portfolio_id=portfolio_id, broker=broker,
                account_id="__unconfigured__", sync_source=sync_source, triggered_by=triggered_by,
                status="failed", started_at=now, finished_at=now, error_message="SYNC_ACCOUNT_UNCONFIGURED")
            self.session.add(run); self.session.commit()
            raise ValueError("SYNC_ACCOUNT_UNCONFIGURED")
        try:
            self._owner_check(portfolio_id, broker, account_id)
        except Exception:
            self.session.rollback()
            raise
        active = self.session.query(PositionSnapshotRun).filter_by(portfolio_id=portfolio_id,
            broker=broker, account_id=account_id, sync_source=sync_source, status="running").first()
        if active:
            meta = context(active)
            if not meta.get("lease_until") or meta["lease_until"] > now.isoformat():
                active_id = active.id
                self.session.rollback()
                raise SyncBusy(active_id)
            active.status, active.finished_at = "failed", now
            active.error_message = "SYNC_LEASE_EXPIRED"
            self.session.flush()
        if freshness_seconds is not None and active is None:
            # Check under the SAME write lock as claim creation. A separate
            # pre-check races with a fast publisher on another connection.
            recent = self.session.query(PositionSnapshotRun).filter_by(portfolio_id=portfolio_id,
                broker=broker, account_id=account_id, sync_source=sync_source).order_by(
                    PositionSnapshotRun.id.desc()).first()
            if recent and recent.finished_at:
                from app.fill_sync import orders, pending
                trade_broker = "ibkr" if broker=="snowball" else broker
                has_pending = any(pending(r) for r in orders(self.session,portfolio_id,trade_broker,account_id))
                finished = recent.finished_at.replace(tzinfo=timezone.utc) if recent.finished_at.tzinfo is None else recent.finished_at
                age = (now - finished).total_seconds()
                if (recent.status == "success" and context(recent).get("complete") is True and 0 <= age <= freshness_seconds
                        and publication_status(self.session, recent) == "matched" and not has_pending
                        or recent.status == "failed" and 0 <= age <= 15):
                    recent_id = recent.id
                    self.session.rollback()
                    raise SyncReusable(recent_id)
        token = uuid.uuid4().hex
        run = PositionSnapshotRun(portfolio_id=portfolio_id, broker=broker, account_id=account_id,
            sync_source=sync_source, triggered_by=triggered_by, status="running", started_at=now,
            context_json=json.dumps({"version":1, "token":token,
                "lease_until":(now+timedelta(seconds=self.LEASE_SECONDS)).isoformat(),
                "quality":"pending", "issues":[]}))
        self.session.add(run)
        self.session.flush()
        from app.fill_sync import bind_targets
        targets = bind_targets(self.session, run, force=not fill_only)
        if not targets and fill_only:
            self.session.rollback()
            return None
        if targets:
            meta = context(run)
            meta["fill_targets"] = targets
            run.context_json = json.dumps(meta)
        self.session.commit()
        run_id = run.id
        self.session.info.setdefault("sync_claims", {})[run_id] = token
        return run

    def bind_run_account(self, run_id, account_id):
        run = self.session.get(PositionSnapshotRun, run_id)
        if run is None or run.account_id != account_id:
            raise ValueError("SYNC_RUN_BINDING_INVALID")
        self._check_claim(run)
        return run  # account is fixed BEFORE source I/O; never rebind discovered owners

    def persist_positions(self, run_id, positions, total_market_value_cnh=None, finalize=True):
        self._begin()
        run = self.session.get(PositionSnapshotRun, run_id)
        if run is None:
            raise ValueError("SYNC_RUN_NOT_FOUND")
        meta = self._check_claim(run)
        prepared = self.session.info.get("sync_prepared", {}).get(run_id)
        if prepared:
            meta.update(prepared)
            run.context_json = json.dumps(meta, default=str)
        self._owner_check(run.portfolio_id, run.broker, run.account_id)
        seen = set()
        for pos in positions:
            if (pos.broker, pos.account_id, pos.sync_source) != (run.broker, run.account_id, run.sync_source):
                raise ValueError("MIXED_SNAPSHOT_SCOPE")
            # Upsert's current key is canonical symbol. Mixed-currency aliases
            # must fail, not overwrite each other under one symbol.
            if pos.symbol in seen:
                raise ValueError("DUPLICATE_SNAPSHOT_IDENTITY")
            seen.add(pos.symbol)
        if not positions and not (prepared and prepared.get("empty_confirmed") and prepared.get("complete")):
            raise ValueError("EMPTY_SOURCE_REQUIRES_REVIEW")
        for pos in positions:
            self.session.add(self._position_to_snapshot(run, pos))
        self.session.info["sync_publish_run_id"] = run_id
        if finalize:
            self.mark_run_succeeded(run_id, len(positions), total_market_value_cnh)
        else:
            self.session.flush()

    def mark_run_succeeded(self, run_id, position_count, total_market_value_cnh=None):
        run = self.session.get(PositionSnapshotRun, run_id)
        self._check_claim(run)
        if self.session.info.get("sync_publish_run_id") != run_id:
            raise ValueError("SYNC_PUBLICATION_REQUIRED")
        meta = context(run)
        meta.setdefault("quality", "partial")
        if meta["quality"] == "pending":
            meta.update(quality="partial", issues=["SOURCE_EVIDENCE_UNKNOWN"])
        run.context_json = json.dumps(meta, default=str)
        run.finished_at, run.status = datetime.now(timezone.utc), "success"
        run.position_count, run.total_market_value_cnh = position_count, total_market_value_cnh
        self.session.commit()
        self.session.info.pop("sync_publish_run_id", None)

    def mark_run_failed(self, run_id, error_message, retry_count=0):
        self.session.rollback()
        self._begin()
        run = self.session.get(PositionSnapshotRun, run_id)
        if (run is None or run.portfolio_id != self._portfolio_id() or run.status != "running"
                or not self._token(run_id) or context(run).get("token") != self._token(run_id)):
            self.session.rollback()
            return  # a late old worker cannot fail a completed/replaced task
        meta = context(run)
        meta.update(quality="unavailable", issues=[error_message.split(":",1)[0]])
        run.context_json = json.dumps(meta)
        run.finished_at, run.status = datetime.now(timezone.utc), "failed"
        run.error_message, run.retry_count = error_message[:5000], retry_count
        self.session.commit()

    def synchronize(self, broker, account_id, fetch, *, triggered_by="manual", metadata=None,
                    reconcile=None, retries=2, retry_delay=5, observed_rows_only=False):
        """Reuse original Snapshot+Upsert commit for every sync entrypoint."""
        from app.fx_service import fx_service, FXRateUnavailable
        from services.broker_sync.position_upsert_service import PositionUpsertService
        run_id = self.create_run(broker, account_id, triggered_by=triggered_by).id
        for attempt in range(retries+1):
            self.session.rollback()  # release reservation/read transaction before ALL external I/O
            try:
                result = fetch()
                positions = result[0] if isinstance(result, tuple) else result
                if positions is None:
                    raise ValueError("INCOMPLETE_SOURCE")
                info = dict(metadata() if metadata else {})
                if not positions and not (info.get("complete") and info.get("empty_confirmed")):
                    raise ValueError("EMPTY_SOURCE_REQUIRES_REVIEW")
                partial = (observed_rows_only and broker == "guojin" and reconcile is not None
                           and info.get("source") == "guojin_qnx_native"
                           and info.get("publication_mode") == "observed_rows" and info.get("complete") is False)
                if info.get("complete") is False and not partial:
                    raise ValueError("INCOMPLETE_SOURCE")
                quotes = {ccy:fx_service.get_quote(ccy,"CNY").to_dict() for ccy in {p.currency for p in positions}}
                if any(quotes[p.currency]["rate"] is None and
                       (p.market_value != 0 or p.unrealized_pnl != 0) for p in positions):
                    raise FXRateUnavailable("FX_RATE_UNAVAILABLE")
                issues = set(info.get("issues", []))
                if not info.get("source"):
                    issues.add("SOURCE_EVIDENCE_UNKNOWN")
                for q in quotes.values(): issues.update(q["issues"])
                if any(p.broker_security_type != "CASH" for p in positions) and not info.get("price_as_of"): issues.add("PRICE_TIME_UNKNOWN")
                prepared = {**info, "complete":not partial, "fx":quotes,
                    "fetched_at":datetime.now(timezone.utc).isoformat(),
                    "currencies":sorted(quotes), "row_count":len(positions),
                    "quality":"partial" if issues else "current", "issues":sorted(issues)}
                self.session.info.setdefault("sync_prepared", {})[run_id] = prepared
                self.persist_positions(run_id, positions, finalize=False)
                snapshots = self.session.query(PositionSnapshot).filter_by(run_id=run_id).all()
                upsert = PositionUpsertService(self.session)
                if reconcile:
                    reconcile(self.session, upsert, snapshots)
                else:
                    report = upsert.upsert_from_snapshots(snapshots, broker=broker, account_id=account_id,
                        sync_source="api", commit=False, complete_snapshot=True, run_id=run_id)
                    if report["errors"]: raise ValueError("SYNC_BUSINESS_UPSERT_FAILED")
                from services.broker_sync.position_upsert_service import backfill_bare_names
                backfill_bare_names(self.session, portfolio_id=self._portfolio_id(),
                    broker=broker, account_id=account_id, commit=False)
                self.session.flush()
                run = self.session.get(PositionSnapshotRun, run_id)
                meta = context(run)
                meta["published_positions"] = published_positions(self.session, run)
                run.context_json = json.dumps(meta)
                if meta.get("fill_targets"):
                    from app.fill_sync import coverage, _snapshots
                    coverage(self.session, run, _snapshots(self.session,run.id), meta["fill_targets"])
                self.mark_run_succeeded(run_id, len(positions))
                return run_id
            except Exception as exc:
                self.session.rollback()
                transient = isinstance(exc, (ConnectionError, TimeoutError, OSError)) or type(exc).__name__ == "ApiException"
                if transient and attempt < retries:
                    time.sleep(retry_delay)
                    continue
                # Stable codes only: provider messages can contain credentials/account IDs.
                code = str(exc) if str(exc) in {"EMPTY_SOURCE_REQUIRES_REVIEW", "INCOMPLETE_SOURCE",
                    "FX_RATE_UNAVAILABLE", "DUPLICATE_SNAPSHOT_IDENTITY", "MIXED_SNAPSHOT_SCOPE",
                    "SYNC_CLAIM_EXPIRED_OR_REPLACED", "ACCOUNT_PORTFOLIO_AMBIGUOUS",
                    "SOURCE_ACCOUNT_UNPROVEN", "CASH_IDENTITY_COLLISION_REQUIRES_REVIEW",
                    "NATIVE_BATCH_INCOMPLETE", "NATIVE_BATCH_INVALID", "NATIVE_ACCOUNT_UNPROVEN",
                    "NATIVE_RECONCILIATION_FAILED", "NATIVE_BATCH_STALE", "NATIVE_LEGACY_REVIEW_REQUIRED"} else "SYNC_SOURCE_FAILED"
                self.mark_run_failed(run_id, code, attempt)
                raise ValueError(code) from None

    def get_latest_successful_run(self, broker, account_id):
        return (self.session.query(PositionSnapshotRun).filter_by(portfolio_id=self._portfolio_id(),
            broker=broker, account_id=account_id, status="success").order_by(PositionSnapshotRun.started_at.desc()).first())

    @staticmethod
    def _position_to_snapshot(
        run: PositionSnapshotRun, pos: Position
    ) -> PositionSnapshot:
        """Pydantic Position → SQLAlchemy PositionSnapshot。"""
        return PositionSnapshot(
            run_id=run.id,
            broker=pos.broker,
            account_id=pos.account_id,
            snapshot_time=pos.snapshot_time,
            symbol=pos.symbol,
            raw_symbol=pos.raw_symbol,
            name=pos.name,
            name_en=pos.name_en,
            asset_class=pos.asset_class,
            broker_security_type=pos.broker_security_type or None,
            vehicle_type=pos.vehicle_type,
            economic_asset_class=pos.economic_asset_class,
            economic_asset_subclass=pos.economic_asset_subclass,
            classification_source=pos.classification_source,
            classification_confidence=pos.classification_confidence,
            classification_verification_status=pos.classification_verification_status,
            classification_version=pos.classification_version,
            classification_evidence_json=json.dumps(
                pos.classification_evidence,
                ensure_ascii=False,
                default=str,
            ),
            market=pos.market,
            quantity=pos.quantity,
            available_quantity=pos.available_quantity,
            avg_cost=pos.avg_cost,
            cost_method=pos.cost_method,
            cost_basis=pos.cost_basis,
            current_price=pos.current_price,
            market_value=pos.market_value,
            currency=pos.currency,
            unrealized_pnl=pos.unrealized_pnl,
            unrealized_pnl_pct=pos.unrealized_pnl_pct,
            realized_pnl=pos.realized_pnl,
            day_pnl=pos.day_pnl,
            option_meta_json=(
                json.dumps(pos.option_meta.model_dump(mode="json"))
                if pos.option_meta else None
            ),
            raw_data_json=json.dumps(pos.raw_data, ensure_ascii=False, default=str),
        )
