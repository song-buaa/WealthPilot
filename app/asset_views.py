"""Versioned derived asset views and bounded private analysis inputs, no writer engine."""
from contextlib import contextmanager, closing
from copy import deepcopy
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


def public_view(payload, *, symbol=None, analysis_scope=None):
    result = deepcopy({k: v for k, v in payload.items() if k != "internal"})
    result["context_current"] = 0 <= (now() - datetime.fromisoformat(payload["captured_at"])).total_seconds() < VIEW_TTL
    expired = set()
    if not result["context_current"]:
        expired.add("ANALYSIS_CONTEXT_EXPIRED")
    if not 0 <= (now() - datetime.fromisoformat(payload["captured_at"])).total_seconds() <= 300:
        expired.add("STALE_ANALYSIS_VIEW")
    # Capture does not reset the source clock. A source already 290s old at
    # capture must not qualify for another 300s merely because this view is new.
    for account in result["data_quality"].get("accounts", []):
        if account.get("publication_status", "unverified") != "matched":
            expired.add("SYNC_PUBLICATION_"+account.get("publication_status", "unverified").upper())
        stamp = account.get("account_synced_at")
        if stamp:
            stamp = datetime.fromisoformat(stamp)
            if stamp.tzinfo is None:  # original SnapshotRun stores UTC-naive SQL timestamps
                stamp = stamp.replace(tzinfo=timezone.utc)
            if not 0 <= (now()-stamp).total_seconds() <= 300:
                expired.add("STALE_ACCOUNT_SYNC")
        for name, limit in (("positions_as_of",300),("cash_as_of",300),("price_as_of",86400)):
            if account.get("time_quality", {}).get(name) == "current":
                value = account[name]
                if len(value) == 10:  # price source with date precision
                    from datetime import date
                    current = 0 <= (date.today()-date.fromisoformat(value)).days <= 1
                else:
                    current = 0 <= (now()-datetime.fromisoformat(value)).total_seconds() <= limit
                if not current:
                    expired.add("STALE_SOURCE_TIME")
    if expired:
        result["data_quality"] = {**result["data_quality"], "status":"partial", "can_propose_rebalance": False,
                                  "issues": sorted(set(result["data_quality"]["issues"]) | expired)}
    if result["data_quality"].get("policy_version") == "d1.1":
        previous_scope = result["data_quality"].get("qualification_scope", {})
        qualify_view(result, at=now(), symbol=symbol or previous_scope.get("symbol"),
                     analysis_scope=analysis_scope or previous_scope.get("analysis_scope", "investments"))
    return result


def _source_current(value, at, seconds=300):
    try:
        stamp = datetime.fromisoformat(value)
        return stamp.tzinfo is not None and 0 <= (at-stamp).total_seconds() <= seconds
    except (ValueError, TypeError):
        return False


def _stored_fx_current(item, at):
    from app.fx_service import fx_date_is_current
    import math
    if (item.get("original_currency") or item.get("currency")) == "CNY":
        return True
    try:
        return (math.isfinite(float(item["fx_rate_to_cny"])) and float(item["fx_rate_to_cny"]) > 0
                and fx_date_is_current(item.get("fx_rate_date"), at.date())
                and item.get("fx_quality", "current") == "current")
    except (TypeError, ValueError, KeyError):
        return False


def qualify_view(view, *, at, symbol=None, analysis_scope="investments"):
    """Derived, purpose-scoped eligibility over the SAME frozen facts.

    No quote/sync I/O, no persisted mutation, no override. Old payloads keep
    their old conservative boolean. This is analysis quality, never execution
    authorization. Material means >=1% of gross selected investment value.
    """
    from backend.services.execution_plan.market_hours import valuation_quality
    from app.fx_service import fx_date_is_current
    import math
    quality = view["data_quality"]
    inputs = quality.get("security_inputs", [])
    selected = [i for i in inputs if not symbol or symbol in (i.get("symbol"), i.get("security_symbol"), i.get("ticker"))]
    refs = {i.get("account_ref") for i in selected}
    accounts = {a["account_ref"]:a for a in quality.get("accounts", []) if a.get("account_ref")}
    levels = {k:{"allowed":False, "blockers":[]} for k in ("A","B","C")}
    def block(tiers, code, item=None):
        detail = {"code":code}
        if item:
            detail.update({k:item[k] for k in ("id","symbol","account_ref") if item.get(k) is not None})
        for tier in tiers:
            if detail not in levels[tier]["blockers"]:
                levels[tier]["blockers"].append(detail)
    def amount(item):
        try:
            value = float(item["market_value_cny"])
            return value if math.isfinite(value) else None
        except (ValueError, TypeError, KeyError):
            return None
    gross = sum(abs(amount(i) or 0) for i in selected)
    if not selected or gross == 0:
        block("ABC", "NO_RELEVANT_ASSET_VALUES")
    expired = not view.get("context_current", True)
    if expired:
        block("BC", "ANALYSIS_CONTEXT_EXPIRED")
    if not _source_current(view["captured_at"], at):
        block("C", "STALE_ANALYSIS_VIEW")
    qualified_inputs = []
    for item in inputs:
        a = accounts.get(item.get("account_ref"), {})
        security = item.get("security_symbol") or item.get("symbol") or ""
        market = security.rsplit(":",1)[-1] if ":" in security else None
        price = valuation_quality(item.get("price_as_of"), market, at=at, basis=item.get("price_time_basis"))
        cash = item.get("vehicle_type") == "CASH"
        row_codes = []
        if not cash and price["reason_code"]:
            row_codes.append(price["reason_code"])
        position_current = _source_current(item.get("positions_as_of"), at)
        cash_current = _source_current(item.get("cash_as_of"), at)
        if not cash and not position_current:
            row_codes.append("POSITION_TIME_UNKNOWN_OR_STALE")
        if cash and not cash_current:
            row_codes.append("CASH_TIME_UNKNOWN_OR_STALE")
        fx_current = _stored_fx_current(item, at)
        if not fx_current:
            row_codes.append("FX_STORED_VALUATION_UNVERIFIED")
        qualified_inputs.append({**item, "valuation_quality":price, "quality_issues":row_codes})
        if item not in selected:
            continue
        value = amount(item)
        material = value is None or abs(value) >= gross*.01
        if value is None:
            block("ABC", "ASSET_AMOUNT_UNVERIFIED", item)
        if a.get("publication_status") == "diverged":
            block("ABC", "SYNC_PUBLICATION_DIVERGED", item)
        if a.get("publication_status") != "matched" or not a.get("complete"):
            block("C", "SYNC_PUBLICATION_UNVERIFIED", item)
            if material:
                block("B", "SYNC_PUBLICATION_UNVERIFIED", item)
        if item.get("economic_asset_class") in (None, "UNKNOWN"):
            block("C", "UNCLASSIFIED_HOLDING", item)
            if material:
                block("B", "MATERIAL_UNCLASSIFIED_HOLDING", item)
        if not fx_current:
            block("C", "FX_STORED_VALUATION_UNVERIFIED", item)
            if material:
                block("B", "FX_STORED_VALUATION_UNVERIFIED", item)
        if not cash and price["status"] not in {"intraday","recent_close"}:
            block("C", price["reason_code"], item)
            if material:
                block("B", price["reason_code"], item)
        if not cash and price["status"] == "recent_close":
            block("C", "CLOSE_VALUATION_NOT_EXECUTABLE_QUOTE", item)
        if not cash and not position_current:
            block("C", "POSITION_TIME_UNKNOWN_OR_STALE", item)
        if not cash:
            if item.get("price_basis") == "BROKER_CNY_VALUE_PER_UNIT":
                block("C", "VALUATION_UNIT_VALUE_NOT_EXECUTABLE_QUOTE", item)
            try:
                price_valid = math.isfinite(float(item["current_price"])) and float(item["current_price"]) > 0
                quantity_valid = math.isfinite(float(item["quantity"])) and float(item["quantity"]) >= 0
            except (KeyError, ValueError, TypeError):
                price_valid = quantity_valid = False
            if not price_valid or not quantity_valid:
                block("C", "SECURITY_PRICE_OR_QUANTITY_UNVERIFIED", item)
        if cash and not cash_current:
            block("C", "CASH_TIME_UNKNOWN_OR_STALE", item)
        if not _source_current(a.get("account_synced_at"), at):
            block("C", "STALE_ACCOUNT_SYNC", item)
        if a.get("last_attempt_status") != "success":
            block("C", "LATEST_SYNC_NOT_SUCCESSFUL", item)
        if a.get("fill_sync_pending"):
            block("BC", "FILL_SYNC_PENDING_OR_UNVERIFIED", item)
    # Precision for one security still requires its OWN account's cash; other
    # accounts' unknown balances cannot grant permission or veto this scope.
    for ref in refs:
        cash_rows = [i for i in inputs if i.get("account_ref") == ref and i.get("vehicle_type") == "CASH"]
        if not cash_rows:
            block("C", "CASH_BALANCE_UNAVAILABLE", {"account_ref":ref})
        for cash in cash_rows:
            if not _source_current(cash.get("cash_as_of"), at):
                block("C", "CASH_TIME_UNKNOWN_OR_STALE", cash)
            if not _stored_fx_current(cash, at):
                block("C", "FX_STORED_VALUATION_UNVERIFIED", cash)
    if not symbol:
        for member in view["issuer_exposure"].get("unmapped_direct_holdings", []):
            if abs(amount(member) or 0) >= gross*.01:
                block("B", "MATERIAL_ISSUER_MAPPING_INCOMPLETE", member)
    tasks = []
    for item in view["views"]["get_manual_wealth_items"]["items"]:
        codes = list(item.get("quality_issues", []))
        if not _manual_date_current(item.get("value_as_of"), at):
            codes.append("MANUAL_VALUE_STALE")
        if item.get("currency") != "CNY" and not fx_date_is_current(item.get("fx_rate_date"), at.date()):
            codes.append("MANUAL_FX_UNVERIFIED")
        if item.get("already_investment_accounted"):
            codes.append("PENSION_HOLDING_LINK_UNVERIFIED")
        if codes:
            tasks.append({**{k:item.get(k) for k in ("id","name","kind","item_type","current_value","currency",
                "value_as_of","last_verified_at","fx_rate_date")}, "reason_codes":sorted(set(codes)), "update_path":"/#/wealth"})
        # Loans affect net-worth/solvency planning; no veto of unrelated single
        # security analysis. Unproven linked pensions affect portfolio denominator.
        relevant = analysis_scope == "net_worth" and item.get("effective_included_in_net_worth")
        linked = not symbol and item.get("already_investment_accounted")
        if codes and (relevant or linked):
            for code in set(codes):
                block("BC", code, item)
    for level in levels.values():
        level["allowed"] = not level["blockers"]
    for ref, account in accounts.items():
        account["time_quality"] = {key:"current" if _source_current(account.get(key),at) else "unknown" if not account.get(key) else "stale"
            for key in ("positions_as_of","cash_as_of")}
        prices = [i["valuation_quality"]["status"] for i in qualified_inputs
                  if i.get("account_ref") == ref and i.get("vehicle_type") != "CASH"]
        account["time_quality"]["price_as_of"] = next((s for s in ("unknown","stale","recent_close","intraday") if s in prices),"not_applicable")
    warnings = {code for i in qualified_inputs for code in i["quality_issues"]}
    warnings.update(code for task in tasks for code in task["reason_codes"])
    quality["issues"] = sorted(set(quality["issues"]) | warnings)
    if quality["issues"] or not levels["C"]["allowed"]:
        quality["status"] = "partial"
    quality.update(security_inputs=qualified_inputs, manual_review_required=tasks,
        qualification_scope={"symbol":symbol,"analysis_scope":analysis_scope},
        analysis_eligibility=levels, can_propose_rebalance=levels["C"]["allowed"],
        materiality_policy="1% of gross selected investment CNY value; all unknowns disclosed; no exemption for target precision",
        execution_boundary="Analysis eligibility never replaces live quote TTL, WhatIf, funds/risk checks or final Web authorization.")


def _manual_date_current(value, at):
    try:
        return 0 <= (at.date()-datetime.fromisoformat(value).date()).days <= 30
    except (ValueError, TypeError):
        return False


def frozen_result(payload, operation, arguments):
    view = public_view(payload, symbol=arguments.get("symbol") if operation == "get_investment_details" else None)
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
                           and (not symbol or symbol in (i["symbol"], i["ticker"], i.get("security_symbol")))]
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
