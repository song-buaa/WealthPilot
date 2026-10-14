"""Explicit stopped-service cutover to shared WAL; never starts any service.

Default is a read-only plan. --apply requires --services-stopped and the existing
private-access gates. Rebinding after restore also requires a verified manifest.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

from common import ROOT, digest, host_guard, private_json, require, safe_cli, write_json
import snapshot

sys.path.insert(0, str(ROOT))
from app.shared_sqlite import load_contract, private_file, schema_digest, verify


def prepare_shared(root, *, services_stopped=False, apply=False, restore_manifest=None):
    root = Path(root).resolve(strict=True)
    runtime = private_json(root / "config/runtime.json")
    wealth = private_json(root / "config/wealth.json")
    reference = private_json(root / "data/migration-reference.json")
    database = root / "data/wealthpilot.db"
    contract_path = root / "config/database-contract.json"
    require(Path(wealth["database_path"]).absolute() == database, "PRODUCTION_DATABASE_PATH_MISMATCH")
    require(wealth["portfolio_id"] == reference["portfolio_id"], "MIGRATION_PORTFOLIO_MISMATCH")
    require(bool(wealth.get("tiger_cash")) == reference["cash_reference"]["configured"],
            "CASH_CONFIGURATION_DIFFERS_FROM_MIGRATION")
    from runner import verify_real_access
    verify_real_access(root, private_json(root / "config/access-evidence.json"))
    require(runtime.get("database_mode", "fixed_snapshot") in ("fixed_snapshot", "shared_wal"),
            "INVALID_DATABASE_MODE")
    private_file(database)
    require(database.parent.stat().st_mode & 0o777 == 0o700, "SHARED_DIRECTORY_MODE_0700_REQUIRED")
    prior = None
    if restore_manifest is not None:
        prior = load_contract(contract_path)
        require(prior["migration_database_sha256"] == reference["database_sha256"],
                "SHARED_MIGRATION_PROVENANCE_MISMATCH")
        manifest = private_json(restore_manifest)
        snapshot.verify(database, manifest)
    else:
        require(not contract_path.exists() and runtime.get("database_mode", "fixed_snapshot") == "fixed_snapshot",
                "SHARED_ALREADY_CONFIGURED_USE_VERIFIED_RESTORE")
        require(digest(database) == reference["database_sha256"], "MIGRATION_DATABASE_DIGEST_MISMATCH")
    # Do not treat the main .db digest as evidence while a WAL/journal exists.
    require(not any(Path(str(database) + s).exists() for s in ("-wal", "-shm", "-journal")),
            "STOP_AND_CLOSE_DATABASE_BEFORE_CUTOVER")
    with closing(snapshot.read_connection(database)) as conn:
        conn.execute("BEGIN")
        require(conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "SHARED_INTEGRITY_FAILED")
        require(conn.execute("SELECT id FROM portfolios WHERE id=?", (wealth["portfolio_id"],)).fetchone(),
                "SHARED_PORTFOLIO_MISSING")
        schema = schema_digest(conn)
        original_manifest = {"sha256": digest(database), "tables": snapshot.inventory(conn), "integrity": "ok"}
        if prior:
            require(prior["schema_sha256"] == schema and prior["portfolio_id"] == wealth["portfolio_id"],
                    "SHARED_RESTORE_SCOPE_MISMATCH")
    if not apply:
        return {"apply": False, "database_mode": "shared_wal", "requires_stopped_services": True,
                "restore_rebind": prior is not None, "production_activated": False}
    require(services_stopped, "STOP_SERVICES_BEFORE_SHARED_CUTOVER")
    require(os.geteuid() != 0, "CUTOVER_AS_NONROOT_SERVICE_USER")
    stamp = uuid.uuid4().hex
    # Always retain a closed, verified pre-cutover backup and exact configuration.
    # Historical migration-reference.json is never rewritten.
    saved = root / "backups" / ("pre-shared-" + stamp)
    saved.mkdir(mode=0o700)
    snapshot.backup(database, saved / "wealthpilot.db")
    if prior is None:
        # Backup API may change header bytes. Also retain the verified CLOSED
        # original exactly, so rolling back to the old SHA gate is possible.
        snapshot.restore(database, saved / "fixed-snapshot.db", original_manifest, True)
        write_json(saved / "fixed-snapshot.db.manifest.json", original_manifest)
    for name, data in (("runtime", runtime), ("wealth", wealth)):
        write_json(saved / (name + ".json"), data)
    if prior:
        write_json(saved / "database-contract.json", prior)
    os.chmod(database, 0o600)
    with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)) as conn:
        require(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal", "SHARED_WAL_REQUIRED")
        require(conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "SHARED_INTEGRITY_FAILED")
    info = database.stat()
    contract = {"schema": "wealthpilot.shared-sqlite.v1", "mode": "shared_wal",
                "database_path": str(database), "file_identity": [info.st_dev, info.st_ino],
                "portfolio_id": wealth["portfolio_id"], "schema_sha256": schema,
                "migration_database_sha256": reference["database_sha256"],
                "created_at": datetime.now(timezone.utc).isoformat(),
                "previous_configuration": str(saved), "restore_rebind": prior is not None}
    write_json(contract_path, contract)
    verify(database, contract_path, integrity=True)
    wealth["database_contract_path"] = str(contract_path)
    write_json(root / "config/wealth.json", wealth)
    runtime["database_mode"] = "shared_wal"
    write_json(root / "config/runtime.json", runtime)
    return {"apply": True, "database_mode": "shared_wal", "backup_verified": True,
            "restore_rebind": prior is not None, "production_activated": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--expected-hostname", required=True)
    parser.add_argument("--services-stopped", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--restore-manifest", help="Explicit rebind after verified, stopped-service restore")
    args = parser.parse_args()
    host_guard(args.expected_hostname)
    print(json.dumps({"ok": True, **prepare_shared(
        args.root, services_stopped=args.services_stopped, apply=args.apply,
        restore_manifest=args.restore_manifest)}))


if __name__ == "__main__":
    safe_cli(main)
