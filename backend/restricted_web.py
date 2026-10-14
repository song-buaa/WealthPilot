"""Loopback-only private Web: explicit handlers, no full backend lifespan."""
from contextlib import asynccontextmanager
import fcntl
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import database, state
from app.shared_sqlite import verify
from backend.api import portfolio, wealth, broker_sync
from backend.services.restricted_sync import SyncExecutor


def create_app(config, static_dir, *, factory=None):
    db_path = config["database_path"]
    if os.environ.get("WEALTHPILOT_SQLITE_CONTRACT") != config["database_contract_path"]:
        raise ValueError("WEB_SHARED_WRITER_CONTRACT_REQUIRED")
    contract = verify(db_path, config["database_contract_path"], integrity=True)
    if contract["portfolio_id"] != config["portfolio_id"] or str(Path(database.DB_PATH).resolve()) != str(Path(db_path).resolve()):
        raise ValueError("WEB_DATABASE_BINDING_MISMATCH")
    state.portfolio_id = contract["portfolio_id"]
    directory = Path(static_dir).resolve(strict=True)
    if not (directory / "index.html").is_file():
        raise ValueError("WEB_DIST_REQUIRED")
    if "</head>" not in (directory / "index.html").read_text():
        raise ValueError("WEB_DIST_HEAD_REQUIRED")
    executor = SyncExecutor(config.get("enabled_brokers", []), state.portfolio_id, factory)

    @asynccontextmanager
    async def lifespan(app):
        with Path(config["lock_path"]).open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            executor.recover_interrupted()
            try:
                yield
            finally:
                close = getattr(executor.factory, "close", None)
                if close:
                    close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sync_executor = executor

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        host = request.headers.get("host", "")
        try:
            parsed = urlsplit("http://" + host)
        except ValueError:
            return JSONResponse({"detail": "PRIVATE_LOOPBACK_ACCESS_REQUIRED"}, status_code=403)
        origin = request.headers.get("origin")
        if parsed.hostname not in ("localhost", "127.0.0.1") or parsed.username or parsed.path or parsed.query or parsed.fragment or (
            origin is not None and origin != "http://" + host
        ) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "PRIVATE_LOOPBACK_ACCESS_REQUIRED"}, status_code=403)
        try:
            response = await call_next(request)
        except Exception:
            return JSONResponse({"detail": "WEB_OPERATION_FAILED"}, status_code=503)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/api/demo/status")
    def mode_status():
        return {"public_demo_mode": False, "password_required": False,
                "restricted_web": True}

    @app.get("/api/web/status")
    def web_status():
        verify(db_path, config["database_contract_path"])
        return {"status": "ok", "mode": "restricted", "database_mode": "shared_wal",
                "revision": config.get("revision"), "enabled_brokers": list(executor.enabled),
                "scheduler_enabled": False}

    for path, handler in (("summary", portfolio.get_summary), ("positions", portfolio.get_positions),
                          ("liabilities", portfolio.get_liabilities), ("alerts", portfolio.get_alerts)):
        app.add_api_route("/api/portfolio/" + path, handler, methods=["GET"])
    app.add_api_route("/api/wealth/summary", wealth.get_summary, methods=["GET"])
    app.add_api_route("/api/wealth/items", wealth.get_items, methods=["GET"])

    @app.get("/api/allocation/targets")
    def targets():
        from backend.services.allocation_service import get_targets
        return [t.model_dump() for t in get_targets()]

    app.add_api_route("/api/broker-sync/status", executor.status, methods=["GET"])

    @app.post("/api/broker-sync/trigger", status_code=202)
    def trigger(req: broker_sync.TriggerRequest, background_tasks: BackgroundTasks):
        try:
            jobs, busy = executor.reserve(req.broker, "manual")
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        for job in jobs:
            background_tasks.add_task(executor.execute, job["broker"], job["run_id"])
        return {"message": "同步任务已提交" if jobs else "已有同步任务运行中" if busy else "尚未启用券商同步",
                "brokers_triggered": [job["broker"] for job in jobs], "jobs": jobs,
                "already_running": busy}

    # No catch-all SPA fallback: unknown API/method must never serve index.html.
    @app.get("/")
    def index():
        html = (directory / "index.html").read_text()
        return HTMLResponse(html.replace("</head>", "<script>window.__WP_RESTRICTED_WEB__=true</script></head>"))

    app.mount("/assets", StaticFiles(directory=directory / "assets"), name="assets")
    return app
