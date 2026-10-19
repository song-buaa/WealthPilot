"""Analysis preparation over the original SyncExecutor and WealthSnapshot.

No broker adapter, order manager, trading mutation or duplicate wealth formula.
"""
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from sqlalchemy import text
from app.asset_views import VERSION, PIPELINE, dumps, decode, now, create_frozen_copy, public_view, ERRORS
from app.models import WealthSnapshot, Portfolio

ROOT = Path(__file__).resolve().parents[2]
REQUEST_PATTERN = r"[A-Za-z0-9_-]{1,64}"


def worker_call(database, portfolio_id, operation, arguments, *, frozen=False, contract=None, synthetic=False):
    """Reuse the existing isolated RO business worker, never patch backend globals."""
    allowed = {"PATH", "HOME", "LANG", "TMPDIR", "WEALTHPILOT_ANALYSIS_CACHE_DIR",
               "WEALTHPILOT_OPENAI_API_KEY", "PERPLEXITY_API_KEY", "ALPHA_VANTAGE_API_KEY",
               "ALPHAVANTAGE_API_KEY", "AV_API_KEY", "AV_API_KEY_1", "AV_API_KEY_2", "AV_API_KEY_3", "AV_API_KEY_4",
               "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"}
    env = {k:v for k,v in os.environ.items() if k in allowed}
    env.update(PYTHON_DOTENV_DISABLED="1", IBKR_READ_ONLY_MODE="true", ENABLE_IBKR_LIVE_TRADING="false",
               ENABLE_TIGER_LIVE_TRADING="false", FUTU_READ_ONLY_MODE="true", BROKER_MODE="mock",
               PUBLIC_DEMO_MODE="true" if synthetic else "false", DEMO_ACCESS_PASSWORD="isolated-asset-view",
               DEMO_ALLOW_MARKET_DATA="false" if synthetic else "true", AV_DEV_MOCK="1" if synthetic else "0")
    command = [sys.executable, str(ROOT / "pocs/chatgpt_skills/worker.py"),
               "--frozen-database" if frozen else "--asset-view-source", str(database),
               "--portfolio-id", str(portfolio_id)]
    if contract:
        command += ["--source-contract", str(contract)]
    if synthetic:
        command += ["--synthetic-input"]
    process = subprocess.run(command, input=json.dumps({"operation":operation,"arguments":arguments})+"\n",
        capture_output=True, text=True, env=env, cwd=ROOT, timeout=270 if operation != "capture_asset_view" else 30)
    if len(process.stdout.encode()) > 8 * 1024 * 1024:
        raise ValueError("ASSET_VIEW_INVALID")
    response = json.loads(process.stdout)
    if process.returncode or not response.get("ok"):
        error = response.get("error")
        raise ValueError(error if error in ERRORS else "ASSET_VIEW_INVALID")
    return response["result"]


class AssetAnalysisPreparation:
    LEASE_SECONDS = 300

    def __init__(self, executor, database, *, contract=None, capture=None, synthetic=False):
        self.executor, self.database, self.contract = executor, Path(database), contract
        self.sessions = executor.session_factory
        self.portfolio_id = executor.portfolio_id
        self.capture = capture or worker_call
        self.synthetic = synthetic

    def _begin(self, db):
        db.execute(text("BEGIN IMMEDIATE"))

    def request(self, request_id):
        if not re.fullmatch(REQUEST_PATTERN, request_id):
            raise ValueError("ANALYSIS_REQUEST_CONFLICT")
        # Resolve owner from existing configuration, never client/LLM account data.
        with self.sessions() as db:
            db.info["portfolio_id"] = self.portfolio_id
            owners = [{"broker":b,"account":self.executor.account_resolver(db,b)} for b in self.executor.enabled]
        fingerprint = hashlib.sha256(dumps({"pipeline":PIPELINE,"portfolio":self.portfolio_id,"owners":owners}).encode()).hexdigest()
        with self.sessions() as db:
            self._begin(db)
            if db.get(Portfolio, self.portfolio_id) is None:
                raise ValueError("ASSET_VIEW_NOT_FOUND")
            row = db.query(WealthSnapshot).filter_by(portfolio_id=self.portfolio_id, analysis_request_id=request_id).first()
            if row:
                payload = decode(row.payload_json)
                if payload["internal"]["request_fingerprint"] != fingerprint:
                    raise ValueError("ANALYSIS_REQUEST_CONFLICT")
                return self.response(row, payload), None
            if db.query(WealthSnapshot.id).filter(WealthSnapshot.portfolio_id == self.portfolio_id,
                    WealthSnapshot.analysis_request_id.isnot(None),
                    WealthSnapshot.recorded_at >= (now()-timedelta(minutes=1)).replace(tzinfo=None)).count() >= 6:
                raise ValueError("ANALYSIS_RATE_LIMITED")
            token = uuid.uuid4().hex
            payload = dict(schema_version=VERSION, pipeline=PIPELINE, state="preparing", portfolio_id=self.portfolio_id,
                internal=dict(token=token, lease_until=(now()+timedelta(seconds=self.LEASE_SECONDS)).isoformat(),
                              owners=owners, request_fingerprint=fingerprint))
            row = WealthSnapshot(portfolio_id=self.portfolio_id, analysis_request_id=request_id,
                recorded_at=now().replace(tzinfo=None),
                payload_json=dumps(payload), total_assets=0, total_liabilities=0, net_worth=0,
                investment_assets=0, non_investment_assets=0, pension_benefit_value=0)
            db.add(row); db.commit()
            return self.response(row, payload), (row.id, token)

    def response(self, row, payload):
        state = payload["state"]
        if state == "preparing" and payload["internal"]["lease_until"] <= now().isoformat():
            state = "expired"
        if state == "ready" and not public_view(payload)["context_current"]:
            state = "expired"
        return dict(analysis_request_id=row.analysis_request_id, state=state,
            snapshot_id=f"asset-view:v1:{row.id}" if state == "ready" else None,
            data_quality=public_view(payload)["data_quality"] if state == "ready" else
                {"status":"unavailable","can_propose_rebalance":False,
                 "issues":["ANALYSIS_CONTEXT_EXPIRED" if state == "expired" else payload.get("error", "ANALYSIS_PREPARING")]})

    def status(self, request_id):
        with self.sessions() as db:
            row = db.query(WealthSnapshot).filter_by(portfolio_id=self.portfolio_id, analysis_request_id=request_id).first()
            if row is None:
                raise ValueError("ASSET_VIEW_NOT_FOUND")
            return self.response(row, decode(row.payload_json))

    def run(self, row_id, token):
        with self.sessions() as db:
            row = db.get(WealthSnapshot, row_id)
            if row is None or row.portfolio_id != self.portfolio_id:
                return
            pending = decode(row.payload_json)
            if not self._owns(pending, token):
                return
        internal = pending["internal"]
        try:
            # Reserve with freshness/cooldown checked under U0's account claim.
            # A busy external run is not ours to restart or declare successful.
            for broker in self.executor.enabled:
                try:
                    jobs, _ = self.executor.reserve(broker, "analysis", freshness_seconds=300)
                    for job in jobs:
                        self.executor.execute(job["broker"],job["run_id"])
                except ValueError as exc:
                    from backend.api.broker_sync import SAFE_SYNC_CODES
                    internal.setdefault("prepare_issues", []).append(str(exc) if str(exc) in SAFE_SYNC_CODES else "SYNC_SCOPE_REJECTED")
            copy_info, path = create_frozen_copy(self.database, self.contract)
            internal = {**internal, **copy_info}
            captured = self.capture(path, self.portfolio_id, "capture_asset_view", {"internal":internal},
                frozen=True, synthetic=self.synthetic)
            captured.update(snapshot_id=f"asset-view:v1:{row_id}", internal=internal)
            encoded = dumps(captured)
            with self.sessions() as db:
                self._begin(db)
                row = db.get(WealthSnapshot, row_id)
                if row is None or row.portfolio_id != self.portfolio_id or not self._owns(decode(row.payload_json), token):
                    return
                for name, value in captured["totals"].items():
                    setattr(row,name,value)
                row.payload_json = encoded
                db.commit()
        except Exception as exc:
            with self.sessions() as db:
                self._begin(db)
                row = db.get(WealthSnapshot, row_id)
                if row is not None and row.portfolio_id == self.portfolio_id and self._owns(decode(row.payload_json),token):
                    pending.update(state="failed",error=str(exc) if str(exc) in ERRORS else "ASSET_VIEW_INVALID")
                    row.payload_json = dumps(pending)
                    db.commit()

    @staticmethod
    def _owns(payload, token):
        return (payload["state"] == "preparing" and payload["internal"]["token"] == token
                and payload["internal"]["lease_until"] > now().isoformat())
