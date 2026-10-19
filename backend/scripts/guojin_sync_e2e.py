#!/usr/bin/env python3
"""Validate native exports or explicitly publish through the original Executor.

No GUI/client/credentials/HTTP gateway is started. Production publication needs
separate authorization and the existing strict shared-SQLite contract.
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"backend"))
os.environ["PYTHON_DOTENV_DISABLED"] = "1"


def main():
    from services.broker_sync.guojin.native_exports import FILES, ERRORS, parse_exports, read_config, load_batch, require
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--files", type=Path, help="Parse/reconcile only; cannot prove owner or publish")
    source.add_argument("--config", type=Path, help="Private native source config + ready batch receipt")
    parser.add_argument("--publish", action="store_true", help="Explicit authorized publication; default is validate only")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--contract", type=Path)
    args = parser.parse_args()
    try:
        if args.files:
            require(not args.publish)
            parsed = parse_exports({k:(args.files/name).read_bytes() for k,name in FILES.items()})
            result = dict(status="VALIDATED_UNBOUND", published=False, security_count=len(parsed.rows),
                counts=parsed.counts, cash_balance_cny=str(parsed.cash), account_total_assets_cny=str(parsed.total_assets),
                issues=["ACCOUNT_AND_QUERY_RECEIPT_REQUIRED", "SOURCE_VALUATION_TIME_UNKNOWN"])
        else:
            config = read_config(args.config)
            parsed, metadata = load_batch(config)
            result = dict(status="VALIDATED", published=False, native_batch_id=metadata["native_batch_id"],
                security_count=len(parsed.rows), counts=parsed.counts, account_total_assets_cny=str(parsed.total_assets),
                cash_balance_cny=str(parsed.cash), issues=metadata["issues"])
            if args.publish:
                require(args.database is not None and args.contract is not None)
                require("guojin" in os.environ.get("WEALTHPILOT_ENABLED_SYNC_BROKERS", "").split(","))
                from app.shared_sqlite import verify
                contract = verify(args.database, args.contract, integrity=True)
                require(contract["portfolio_id"] == config["portfolio_id"], "NATIVE_ACCOUNT_UNPROVEN")
                os.environ["WEALTHPILOT_DB_PATH"] = str(args.database.absolute())
                os.environ["GUOJIN_NATIVE_CONFIG"] = str(args.config.absolute())
                from backend.services.restricted_sync import SyncExecutor, ServiceFactory
                from app.database import get_session
                from services.broker_sync.models import PositionSnapshotRun
                from backend.api.broker_sync import _public_quality, safe_sync_reason
                executor = SyncExecutor(["guojin"], config["portfolio_id"], ServiceFactory(restricted=False))
                jobs, busy = executor.reserve("guojin", "manual")
                if busy:
                    result = dict(status="RUNNING", published=False)
                else:
                    require(len(jobs) == 1)
                    run_id = executor.execute("guojin", jobs[0]["run_id"])
                    with get_session() as db:
                        run = db.get(PositionSnapshotRun,run_id)
                        result = dict(status=run.status, published=run.status == "success", run_id=run.id,
                            finished_at=run.finished_at.isoformat() if run.finished_at else None,
                            position_count=run.position_count, reason_code=safe_sync_reason(run), data_quality=_public_quality(run))
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0 if result["status"] != "failed" else 1
    except Exception as exc:
        # Paths, owner/shareholder IDs and raw financial rows never enter errors.
        print(json.dumps({"status":"failed", "published":False,
            "reason_code":str(exc) if str(exc) in ERRORS else "NATIVE_BATCH_INVALID"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
