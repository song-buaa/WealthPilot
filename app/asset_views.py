"""Versioned derived asset views and bounded private analysis inputs, no writer engine."""
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time
import uuid

VERSION = "wealthpilot.asset-view.v1"
PIPELINE = "u1.1"
VIEW_TTL = 1800
MAX_PAYLOAD = 4 * 1024 * 1024
MAX_DATABASE = 256 * 1024 * 1024
MAX_COPIES = 8
ERRORS = {"ASSET_VIEW_NOT_FOUND", "ASSET_VIEW_NOT_READY", "ANALYSIS_CONTEXT_EXPIRED",
          "ASSET_VIEW_INVALID", "ANALYSIS_REQUEST_CONFLICT", "ANALYSIS_CAPACITY_EXCEEDED", "ANALYSIS_RATE_LIMITED"}


def now():
    return datetime.now(timezone.utc)


def dumps(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    if len(encoded.encode()) > MAX_PAYLOAD:
        raise ValueError("ASSET_VIEW_INVALID")
    return encoded


def decode(value):
    if not isinstance(value, str) or len(value.encode()) > MAX_PAYLOAD:
        raise ValueError("ASSET_VIEW_INVALID")
    result = json.loads(value)
    if result.get("schema_version") != VERSION or result.get("pipeline") != PIPELINE:
        raise ValueError("ASSET_VIEW_INVALID")
    return result


def snapshot_number(value):
    match = re.fullmatch(r"asset-view:v1:([1-9][0-9]{0,17})", value or "")
    if not match:
        raise ValueError("ASSET_VIEW_NOT_FOUND")
    return int(match[1])


def load_view(path, portfolio_id, snapshot_id, contract=None):
    if contract:
        from app.shared_sqlite import verify
        checked = verify(path, contract)
        if checked["portfolio_id"] != portfolio_id:
            raise ValueError("ASSET_VIEW_NOT_FOUND")
    with closing(sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT payload_json FROM wealth_snapshots WHERE id=? AND portfolio_id=? "
                         "AND analysis_request_id IS NOT NULL", (snapshot_number(snapshot_id), portfolio_id)).fetchone()
    if not row:
        raise ValueError("ASSET_VIEW_NOT_FOUND")
    payload = decode(row[0])
    if payload.get("state") != "ready":
        raise ValueError("ASSET_VIEW_NOT_READY")
    if payload.get("portfolio_id") != portfolio_id or payload.get("snapshot_id") != snapshot_id:
        raise ValueError("ASSET_VIEW_INVALID")
    return payload


def public_view(payload):
    result = {k: v for k, v in payload.items() if k != "internal"}
    result["context_current"] = 0 <= (now() - datetime.fromisoformat(payload["captured_at"])).total_seconds() < VIEW_TTL
    if not 0 <= (now() - datetime.fromisoformat(payload["captured_at"])).total_seconds() <= 300:
        result["data_quality"] = {**result["data_quality"], "status":"partial", "can_propose_rebalance": False,
                                  "issues": sorted(set(result["data_quality"]["issues"] + ["STALE_ANALYSIS_VIEW"]))}
    return result


def frozen_result(payload, operation, arguments):
    view = public_view(payload)
    result = json.loads(json.dumps(view["views"][operation]))
    if operation == "get_manual_wealth_items":
        kind, item = arguments.get("kind"), arguments.get("item_id")
        result["items"] = [i for i in result["items"] if (not kind or i["kind"].upper() == kind.upper())
                           and (item is None or i["id"] == item)]
        if item is not None and not result["items"]:
            raise ValueError("ITEM_NOT_FOUND")
    elif operation == "get_investment_details":
        ref, symbol = arguments.get("account_ref"), arguments.get("symbol")
        if ref and ref not in {i["account_ref"] for i in result["accounts"]}:
            raise ValueError("ACCOUNT_NOT_FOUND")
        result["items"] = [i for i in result["items"] if (not ref or i["account_ref"] == ref)
                           and (not symbol or symbol in (i["symbol"], i["ticker"]))]
        if ref:
            result["accounts"] = [i for i in result["accounts"] if i["account_ref"] == ref]
    result.update(snapshot_id=view["snapshot_id"], data_quality=view["data_quality"])
    result["issues"] = sorted(set(result["issues"] + view["data_quality"]["issues"]))
    if result["status"] == "ok" and result["issues"]:
        result["status"] = "partial"
    if operation == "get_investment_details":
        result["issuer_exposure"] = view["issuer_exposure"]
    return result


def cache_root():
    root = Path(os.environ.get("WEALTHPILOT_ANALYSIS_CACHE_DIR", str(Path(tempfile.gettempdir()) / f"wealthpilot-analysis-{os.getuid()}")))
    if root.is_symlink():
        raise ValueError("ASSET_VIEW_INVALID")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("ASSET_VIEW_INVALID")
    return root.resolve()


def _private_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        os.close(fd)
        raise ValueError("ASSET_VIEW_INVALID")
    return os.fdopen(fd, "rb")


def digest(path):
    with _private_file(path) as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cleanup(root):
    for path in root.glob("*.sqlite"):
        if not re.fullmatch(r"[0-9a-f]{32}\.sqlite", path.name):
            continue
        try:
            with _private_file(path) as stream:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if time.time() - os.fstat(stream.fileno()).st_mtime > VIEW_TTL:
                    path.unlink()
        except (FileNotFoundError, BlockingIOError):
            continue


def create_frozen_copy(database, contract=None):
    """SQLite Backup API, never copy an active WAL or keep an LLM read txn."""
    root = cache_root()
    # Serialize capacity checks across prepare processes, not just threads.
    lock_path = root / "capacity.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        cleanup(root)
        if len(list(root.glob("*.sqlite"))) >= MAX_COPIES:
            raise ValueError("ANALYSIS_CAPACITY_EXCEEDED")
        if contract:
            from app.shared_sqlite import verify
            verify(database, contract)
        key = uuid.uuid4().hex
        target = root / (key + ".sqlite")
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        try:
            with closing(sqlite3.connect(Path(database).resolve(strict=True).as_uri() + "?mode=ro", uri=True)) as source:
                page_size = source.execute("PRAGMA page_size").fetchone()[0]
                if source.execute("PRAGMA page_count").fetchone()[0] * page_size > MAX_DATABASE:
                    raise ValueError("ANALYSIS_CAPACITY_EXCEEDED")
                started = time.monotonic()
                def bounded_backup(status, remaining, total):
                    if total * page_size > MAX_DATABASE or time.monotonic()-started > 15:
                        raise ValueError("ANALYSIS_CAPACITY_EXCEEDED")
                with closing(sqlite3.connect(target)) as destination:
                    source.backup(destination, pages=256, progress=bounded_backup, sleep=.05)
                    destination.execute("PRAGMA journal_mode=DELETE")
            if target.stat().st_size > MAX_DATABASE:
                raise ValueError("ANALYSIS_CAPACITY_EXCEEDED")
            return {"cache_key": key, "database_sha256": digest(target), "frozen_at":now().isoformat()}, target
        except BaseException:
            target.unlink(missing_ok=True)
            raise


@contextmanager
def frozen_input(payload):
    if not 0 <= (now() - datetime.fromisoformat(payload["captured_at"])).total_seconds() < VIEW_TTL:
        raise ValueError("ANALYSIS_CONTEXT_EXPIRED")
    key = payload["internal"].get("cache_key", "")
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        raise ValueError("ASSET_VIEW_INVALID")
    path = cache_root() / (key + ".sqlite")
    try:
        stream = _private_file(path)
    except FileNotFoundError:
        raise ValueError("ANALYSIS_CONTEXT_EXPIRED") from None
    with stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        if hashlib.file_digest(stream, "sha256").hexdigest() != payload["internal"]["database_sha256"]:
            raise ValueError("ASSET_VIEW_INVALID")
        yield path
