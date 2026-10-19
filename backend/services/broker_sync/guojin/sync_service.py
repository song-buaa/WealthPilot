"""国金原生文件来源，复用原 SnapshotRun/Upsert 原子发布。

生产构造器只接受私有原生来源配置。旧 MiniQMT HTTP 拉取已退出，
文件生成方式（按需 GUI/未来定时导出）不在该服务内。
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from services.broker_sync.guojin.adapter import GuojinAdapter
from services.broker_sync.guojin.native_exports import load_batch, read_config, require
from services.broker_sync.repository import PositionSnapshotRepository, context
from services.broker_sync.models import PositionSnapshotRun
from services.broker_sync.schema import cash_position


class GuojinSyncService:
    MAX_RETRIES = 0  # native files are a sealed batch, never poll a stale export
    RETRY_DELAY_SECONDS = 0

    def __init__(self, config_path=None):
        path = config_path or os.environ.get("GUOJIN_NATIVE_CONFIG")
        require(bool(path), "NATIVE_BATCH_INCOMPLETE")
        self.config = read_config(Path(path))
        self.adapter = GuojinAdapter(self.config["account_id"])

    def fetch_positions(self):
        parsed, metadata = load_batch(self.config)
        now = datetime.now(timezone.utc)
        positions = [self.adapter.from_native_export(row, now) for row in parsed.rows]
        positions.append(cash_position("guojin", self.adapter.account_id, "CNY", parsed.cash, now))
        return positions, metadata

    def sync_and_persist(self, db_session, triggered_by="manual"):
        account = db_session.info.get("sync_account_id") or getattr(self.adapter, "account_id", None)
        if hasattr(self, "config"):
            from backend.services.action.ownership import portfolio_scope
            require((portfolio_scope(db_session), account) ==
                    (self.config["portfolio_id"], self.config["account_id"]), "NATIVE_ACCOUNT_UNPROVEN")
        metadata = {}
        def fetch():
            positions, evidence = self.fetch_positions()
            metadata.clear()
            metadata.update(evidence)
            return positions
        def reconcile(db, upsert, snapshots):
            native = metadata.get("native_batch")
            if native:
                self._validate_publication(db, upsert, native)
            # Preserve the normalized injection contract used by existing
            # restricted/backend integration callers; no HTTP source remains.
            for snap in snapshots:
                upsert._upsert_single(snap, allow_legacy_claim=not bool(native))
            self._remove_qmt_stale_positions(db, upsert, snapshots)
        return PositionSnapshotRepository(db_session).synchronize("guojin", account, fetch,
            triggered_by=triggered_by, metadata=lambda:metadata, reconcile=reconcile,
            retries=self.MAX_RETRIES, retry_delay=self.RETRY_DELAY_SECONDS)

    def _validate_publication(self, db, upsert, native):
        """Under original write lock: reject old batches and ambiguous legacy rows."""
        previous = db.query(PositionSnapshotRun).filter_by(portfolio_id=upsert.portfolio_id,
            broker="guojin", account_id=self.config["account_id"], sync_source="api", status="success").order_by(
                PositionSnapshotRun.id.desc()).all()
        old = next((context(r)["native_batch"] for r in previous if context(r).get("native_batch")), None)
        if old and old["id"] != native["id"]:
            require(all(native["queries"][k] > old["queries"][k] for k in native["queries"]), "NATIVE_BATCH_STALE")
        owned, candidates = [], {}
        for row in upsert._business_query().all():
            if row.broker not in {None, "guojin"}:
                continue
            if row.broker != "guojin" and row.platform != "国金证券":
                continue
            if row.broker == "guojin" and row.broker_account_id and row.broker_account_id != self.config["account_id"]:
                continue
            if row.broker == "guojin" and row.broker_account_id == self.config["account_id"] and row.sync_source == "api":
                owned.append(row)
            else:
                candidates[row.id] = row
        require(len({p.symbol for p in owned}) == len(owned), "NATIVE_LEGACY_REVIEW_REQUIRED")
        decisions = self.config.get("legacy_positions", [])
        require(len({d.get("position_id") for d in decisions}) == len(decisions), "NATIVE_LEGACY_REVIEW_REQUIRED")
        decided = set()
        retired = {}
        for item in decisions:
            key = item.get("position_id")
            row = candidates.get(key)
            if row is None:
                # A deletion is reusable only when this exact decision was
                # committed in a prior successful native publication.
                require(item.get("action") == "retire" and any(
                    context(run).get("native_retired_rows", {}).get(str(key)) == item.get("fingerprint")
                    for run in previous), "NATIVE_LEGACY_REVIEW_REQUIRED")
                # SQLite may reuse a deleted highest row ID for a new owned
                # holding. This committed receipt retires the OLD fingerprint,
                # never authorizes deleting a later row with the same integer.
                retired[str(key)] = item["fingerprint"]
                continue
            require(item.get("evidence") and item.get("action") in {"retire", "retain"}, "NATIVE_LEGACY_REVIEW_REQUIRED")
            require(position_fingerprint(row) == item.get("fingerprint"), "NATIVE_LEGACY_REVIEW_REQUIRED")
            decided.add(key)
            if item["action"] == "retire":
                retired[str(key)] = item["fingerprint"]
                db.delete(row)
        require(decided == set(candidates), "NATIVE_LEGACY_REVIEW_REQUIRED")
        db.flush()
        run = db.get(PositionSnapshotRun, db.info["sync_publish_run_id"])
        meta = context(run)
        meta["native_retired_rows"] = retired
        run.context_json = json.dumps(meta)

    @staticmethod
    def _remove_qmt_stale_positions(db_session, upsert_service, snapshots):
        """Full validated account snapshot replaces only that owner's API scope.

        Historical method name kept for existing callers; native ingestion no
        longer relies on a ticker-shape heuristic or preserves sold HK rows.
        """
        if not snapshots:
            return 0
        return upsert_service._remove_stale_positions(snapshots, broker="guojin",
            account_id=snapshots[0].account_id, sync_source="api")


def position_fingerprint(row):
    """CAS over every stored field, including identity, ownership and values."""
    facts = {c.name:getattr(row,c.name) for c in row.__table__.columns}
    return hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()
