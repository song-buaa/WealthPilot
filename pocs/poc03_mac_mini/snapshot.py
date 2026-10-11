"""Consistent SQLite backup, private valuation reference, verified restore.

Source always mode=ro. Never init_db/get_summary, synchronize or submit orders.
Use the business venv for reference/check-reference; other commands are stdlib.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import shutil
import sys
import tempfile
import uuid

from common import ROOT, digest, private_dir, private_json, require, safe_cli, write_json


def read_connection(path):
    conn = sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def inventory(conn):
    result = {}
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    for (name,) in tables:
        quoted = '"' + name.replace('"', '""') + '"'
        # Order-independent row multiset; no private rows appear in diagnostics.
        rows = []
        for row in conn.execute("SELECT * FROM " + quoted):
            encoded = json.dumps(list(row), default=lambda b: {"bytes": b.hex()}, ensure_ascii=False, separators=(",", ":"))
            rows.append(hashlib.sha256(encoded.encode()).hexdigest())
        result[name] = {"rows": len(rows), "digest": hashlib.sha256("".join(sorted(rows)).encode()).hexdigest()}
    return result


def backup(source, destination):
    destination = Path(destination).absolute()
    private_dir(destination.parent)
    require(not destination.exists(), "BACKUP_DESTINATION_EXISTS")
    require(not Path(str(destination) + ".manifest.json").exists(), "BACKUP_MANIFEST_ALREADY_EXISTS")
    fd, name = tempfile.mkstemp(dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        with closing(read_connection(source)) as read:
            read.execute("BEGIN")
            source_inventory = inventory(read)  # pins the source snapshot, including committed WAL.
            with closing(sqlite3.connect(temporary)) as write:
                read.backup(write)
                write.execute("PRAGMA journal_mode=DELETE")
                require(write.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "BACKUP_INTEGRITY_FAILED")
                require(inventory(write) == source_inventory, "BACKUP_CONTENT_MISMATCH")
            read.rollback()
        with temporary.open("rb") as data:
            os.fsync(data.fileno())
        # Exclusive link prevents silently replacing an existing destination.
        os.link(temporary, destination)
        os.chmod(destination, 0o600)
        manifest = {"schema": "poc03.sqlite-backup.v1", "sha256": digest(destination),
                    "tables": source_inventory, "integrity": "ok",
                    "created_at": datetime.now(timezone.utc).isoformat()}
        write_json(str(destination) + ".manifest.json", manifest)
        return manifest
    finally:
        temporary.unlink(missing_ok=True)


def verify(database, manifest):
    require(digest(database) == manifest["sha256"], "BACKUP_DIGEST_MISMATCH")
    with closing(read_connection(database)) as conn:
        require(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "DATABASE_INTEGRITY_FAILED")
        require(inventory(conn) == manifest["tables"], "DATABASE_ROWS_MISMATCH")


def restore(source, target, manifest, services_stopped, archive_dir=None):
    require(services_stopped, "STOP_SERVICES_BEFORE_RESTORE")
    source, target = Path(source).resolve(strict=True), Path(target).absolute()
    require(source != target.resolve(), "RESTORE_SOURCE_EQUALS_TARGET")
    verify(source, manifest)
    require(not any(Path(str(target) + s).exists() for s in ("-wal", "-shm", "-journal")), "TARGET_SQLITE_SIDECARS_REQUIRE_REVIEW")
    private_dir(target.parent)
    if target.exists():
        archive = private_dir(archive_dir or target.parent)
        saved = archive / ("pre-restore-" + uuid.uuid4().hex + ".db")
        backup(target, saved)  # retain current data before swap.
    stage = target.parent / ("restore-" + uuid.uuid4().hex + ".db")
    # Verified exported backup is a closed, standalone DELETE-journal database.
    # Copy its bytes exactly so the migration reference digest stays stable.
    with source.open("rb") as read, stage.open("xb") as write:
        os.chmod(stage, 0o600)
        shutil.copyfileobj(read, write)
        write.flush(); os.fsync(write.fileno())
    try:
        verify(stage, manifest)
        os.replace(stage, target)
        verify(target, manifest)
        os.chmod(target, 0o400)
    finally:
        stage.unlink(missing_ok=True)


def business_modules():
    for key in list(os.environ):
        if key.startswith(("TIGER_", "IBKR_", "FUTU_", "OPENAI_")):
            os.environ.pop(key)
    os.environ.update(PYTHON_DOTENV_DISABLED="1", PUBLIC_DEMO_MODE="false")
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "pocs/poc02_wealth_mcp"))
    from reader import read_overview, SourceLogProbe
    from backend.services import portfolio_service
    probe = SourceLogProbe()
    logging.getLogger().handlers = [probe]
    return read_overview, portfolio_service, probe


def valuation_reference(database, config):
    read, portfolio, probe = business_modules()
    tiger = config.get("tiger_cash", {})
    require(not tiger or set(tiger) == {"TIGER_ID", "TIGER_ACCOUNT", "TIGER_PRIVATE_KEY_PATH"}, "CASH_CONFIGURATION_INVALID")
    os.environ.update(tiger)
    original = portfolio._get_tiger_account_cash
    value, details = original()
    require(not probe.cash_failed, "CASH_REFERENCE_UNAVAILABLE")
    portfolio._get_tiger_account_cash = lambda: (value, [{}] if details else [])
    try:
        result = read(database, int(config["portfolio_id"]), cash_configured=bool(tiger))
    finally:
        portfolio._get_tiger_account_cash = original
    require(result["status"] != "unavailable" and all(result[k] is not None for k in
            ("total_assets_minor", "total_liabilities_minor", "net_worth_minor")), "SOURCE_VALUATION_UNAVAILABLE")
    return {"schema": "poc03.valuation-reference.v1", "database_sha256": digest(database),
            "portfolio_id": int(config["portfolio_id"]), "result": result,
            "cash_reference": {"configured": bool(tiger), "value_cny": value, "has_details": bool(details)},
            "fx_degraded_at_capture": probe.fx_degraded}


def check_reference(database, reference):
    require(digest(database) == reference["database_sha256"], "REFERENCE_DATABASE_MISMATCH")
    read, portfolio, _ = business_modules()
    cash = reference["cash_reference"]
    original = portfolio._get_tiger_account_cash
    portfolio._get_tiger_account_cash = lambda: (cash["value_cny"], [{}] if cash["has_details"] else [])
    try:
        result = read(database, int(reference["portfolio_id"]), cash_configured=cash["configured"])
    finally:
        portfolio._get_tiger_account_cash = original
    checks = {k: result[k] == reference["result"][k] for k in ("total_assets_minor", "total_liabilities_minor", "net_worth_minor")}
    require(all(checks.values()), "MIGRATED_AMOUNTS_MISMATCH")
    return {"amounts_equal": checks, "cash_reference_replayed_for_migration_only": True,
            "live_cash_and_fx_equivalence": "NOT_TESTED"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("backup"); p.add_argument("--source", required=True); p.add_argument("--destination", required=True)
    p = commands.add_parser("verify"); p.add_argument("--database", required=True); p.add_argument("--manifest", required=True)
    p = commands.add_parser("restore"); p.add_argument("--backup", required=True); p.add_argument("--target", required=True); p.add_argument("--manifest", required=True); p.add_argument("--archive-dir"); p.add_argument("--services-stopped", action="store_true")
    p = commands.add_parser("reference"); p.add_argument("--database", required=True); p.add_argument("--config", required=True); p.add_argument("--output", required=True)
    p = commands.add_parser("check-reference"); p.add_argument("--database", required=True); p.add_argument("--reference", required=True)
    args = parser.parse_args()
    result = {"ok": True, "command": args.command}
    if args.command == "backup":
        backup(args.source, args.destination)
    elif args.command == "verify":
        verify(args.database, private_json(args.manifest))
    elif args.command == "restore":
        restore(args.backup, args.target, private_json(args.manifest), args.services_stopped, args.archive_dir)
    elif args.command == "reference":
        write_json(args.output, valuation_reference(args.database, private_json(args.config)))
    else:
        result.update(check_reference(args.database, private_json(args.reference)))
    print(json.dumps(result))


if __name__ == "__main__":
    safe_cli(main)
