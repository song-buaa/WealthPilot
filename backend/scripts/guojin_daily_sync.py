#!/usr/bin/env python3
"""Same Guojin-only native flow for launchd and authorized on-demand execution."""
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
    from services.broker_sync.guojin.daily_sync import run, CODES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--publish", action="store_true", help="Explicit authorized ready/DB publication; default validates only")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--trigger", choices=("manual", "cron"), default="manual")
    parser.add_argument("--retry-batch", help="Recover a sealed batch without repeating Windows export")
    args = parser.parse_args()
    try:
        result = run(args.config, database=args.database, contract=args.contract,
                     trigger=args.trigger, retry_batch=args.retry_batch, publish_assets=args.publish)
        # Private status contains financial summary; operational stdout doesn't.
        print(json.dumps({k:result[k] for k in ("status", "reason_code", "run_id", "finished_at") if k in result}))
        return 1 if result["status"] == "FAILED" else 0
    except Exception as exc:
        print(json.dumps({"status":"FAILED", "reason_code":str(exc) if str(exc) in CODES else "AUTO_CONFIG_INVALID"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
