"""Temporary loopback validation of unchanged Web over a private database clone.

Not a business backend: GET-only wealth endpoints, no lifecycle jobs, no init_db.
The original summary's snapshot write occurs only in the disposable clone.
Financial responses are held locally; never print them or put the clone in Git.
"""
import argparse
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--private-dir", required=True)
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    private = Path(args.private_dir).resolve()
    if private.is_relative_to(ROOT) or private.stat().st_mode & 0o077:
        parser.error("verification files require a private directory outside Git")
    os.umask(0o077)
    clone = private / "web-compare.db"
    if clone.exists():
        parser.error("clone already exists; use a fresh private directory")
    source = Path(config["database_path"]).resolve(strict=True)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as read:
        read.execute("PRAGMA query_only=ON")
        with sqlite3.connect(clone) as write:
            read.backup(write)
    os.environ["WEALTHPILOT_DB_PATH"] = str(clone)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["PUBLIC_DEMO_MODE"] = "false"
    for key in list(os.environ):
        if key.startswith(("TIGER_", "IBKR_", "FUTU_", "OPENAI_")):
            os.environ.pop(key)
    for key, value in config.get("tiger_cash", {}).items():
        if key in ("TIGER_ID", "TIGER_ACCOUNT", "TIGER_PRIVATE_KEY_PATH"):
            os.environ[key] = value

    from fastapi import FastAPI, HTTPException
    from fastapi.staticfiles import StaticFiles
    from backend.services import wealth_service
    from reader import SourceLogProbe
    import uvicorn

    # Calculate once with the original public service and original cash helper.
    # A stable local reference avoids creating more snapshots on UI refocus.
    probe = SourceLogProbe()
    logging.getLogger().handlers = [probe]
    summary = wealth_service.get_summary(int(config["portfolio_id"]), 365)
    if probe.cash_failed:
        raise RuntimeError("WEB_REFERENCE_CASH_UNAVAILABLE")
    reference = private / "web-summary.json"
    reference.write_text(json.dumps(summary, ensure_ascii=False))
    os.chmod(reference, 0o600)
    items = {kind: wealth_service.list_items(int(config["portfolio_id"]), kind)
             for kind in ("asset", "liability")}
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/demo/status")
    def demo_status():
        return {"public_demo_mode": False}

    @app.get("/api/wealth/summary")
    def wealth_summary():
        return summary

    @app.get("/api/wealth/items")
    def wealth_items(kind: str):
        if kind not in items:
            raise HTTPException(400, "Unsupported kind")
        return {"items": items[kind], "total": len(items[kind])}

    @app.get("/api/{path:path}")
    def unused_endpoint(path: str):
        raise HTTPException(404, "Outside wealth validation scope")

    app.mount("/", StaticFiles(directory=ROOT / "frontend" / "dist", html=True))
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False,
                log_level="error")


if __name__ == "__main__":
    main()
