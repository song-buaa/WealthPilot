#!/usr/bin/env python3
"""Explicit offline B0 plan/apply/verify. Never starts services or broker clients.

Runbook: docs/B0_PRODUCTION_RUNBOOK.md. Production use requires separate approval.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pocs/poc03_mac_mini"))
from app.b0_schema import plan as ddl_plan, verify as verify_shape
from app.shared_sqlite import verify, schema_digest, load_contract, check_file
from snapshot import backup, inventory

MIGRATION_STAGE = "b0"
RECEIPT_KEY = "b0_migration_receipt"
SCHEMA_COLUMNS = __import__("app.b0_schema", fromlist=["COLUMNS"]).COLUMNS

def select_stage(stage):
    """Explicit opt-in only; the original B0 CLI/receipts stay B0 by default."""
    global MIGRATION_STAGE, RECEIPT_KEY, SCHEMA_COLUMNS, ddl_plan, verify_shape
    if stage not in {"b0", "u0", "u1", "u2", "b2"}:
        raise ValueError("UNKNOWN_MIGRATION_STAGE")
    module = __import__("app."+stage+"_schema", fromlist=["plan","verify","COLUMNS"])
    MIGRATION_STAGE, RECEIPT_KEY = stage, stage+"_migration_receipt"
    SCHEMA_COLUMNS, ddl_plan, verify_shape = module.COLUMNS, module.plan, module.verify



def write_private(path, value):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("SYMLINK_REFUSED")
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def private_read(path):
    from app.shared_sqlite import private_file
    return json.loads(private_file(path, 0o600).read_text())


def row_fingerprint(row):
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True, default=str).encode()).hexdigest()


def identity_plan(conn, requests, portfolio_id):
    """Only exact, evidenced current rows; historical snapshots remain immutable."""
    from backend.services.execution_batch.trusted_instruments import TRUSTED_INSTRUMENTS
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    result = []
    sources, targets = set(), set()
    for item in requests:
        if item["position_id"] in sources:
            raise ValueError("DUPLICATE_POSITION_CORRECTION")
        sources.add(item["position_id"])
        row = conn.execute("SELECT * FROM positions WHERE id=?", (item["position_id"],)).fetchone()
        if row is None or row_fingerprint(row) != item["expected_fingerprint"]:
            raise ValueError("POSITION_CAS_MISMATCH")
        if row["portfolio_id"] != portfolio_id or row["broker"] != "snowball" or row["sync_source"] != "api" or not row["broker_account_id"]:
            raise ValueError("POSITION_OWNER_UNPROVEN")
        evidence = json.loads(row["classification_evidence_json"] or "{}")
        matches = [t for t in TRUSTED_INSTRUMENTS.values()
                   if str(t.con_id) == str(evidence.get("con_id"))
                   and t.currency == row["currency"]
                   and (not evidence.get("isin") or t.isin == evidence["isin"])]
        if len(matches) != 1:
            raise ValueError("POSITION_LISTING_UNPROVEN")
        trusted = matches[0]
        if row["ticker"] not in {trusted.symbol, trusted.local_symbol, trusted.user_alias}:
            raise ValueError("POSITION_SYMBOL_MISMATCH")
        target = f"{trusted.local_symbol}:LSE"
        identity = (portfolio_id, row["broker"], row["broker_account_id"], row["sync_source"], target, row["currency"])
        if identity in targets:
            raise ValueError("POSITION_IDENTITY_COLLISION_REQUIRES_REVIEW")
        targets.add(identity)
        collision = conn.execute("SELECT id FROM positions WHERE portfolio_id=? AND broker=? AND broker_account_id=? AND sync_source=? AND symbol=? AND id<>?",
                (portfolio_id, row["broker"], row["broker_account_id"], row["sync_source"], target, row["id"])).fetchone()
        if collision:
            raise ValueError("POSITION_IDENTITY_COLLISION_REQUIRES_REVIEW")
        result.append({**item, "symbol": target, "ticker": trusted.local_symbol})
    conn.row_factory = previous_factory
    return result


def cash_owner_plan(conn, requests, portfolio_id):
    """Operator-attested same-cash mapping, exact-row CAS; never merge duplicates."""
    from backend.services.broker_sync.schema import cash_position, is_cash_balance
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    result, sources, targets = [], set(), set()
    try:
        for item in requests:
            if (item.get("kind") != "cash_owner" or item.get("confirmed_same_cash") is not True
                    or not isinstance(item.get("evidence_ref"), str) or not item["evidence_ref"].strip()):
                raise ValueError("EXPLICIT_CASH_MAPPING_EVIDENCE_REQUIRED")
            if item["position_id"] in sources:
                raise ValueError("DUPLICATE_POSITION_CORRECTION")
            sources.add(item["position_id"])
            row = conn.execute("SELECT * FROM positions WHERE id=?", (item["position_id"],)).fetchone()
            if row is None or row_fingerprint(row) != item["expected_fingerprint"]:
                raise ValueError("POSITION_CAS_MISMATCH")
            account = item.get("account_id")
            broker = item.get("broker")
            if (broker not in {"tiger", "snowball", "futu", "guojin"}
                    or not isinstance(account, str) or not account.strip() or len(account) > 50
                    or account in {"pending", "unknown", "__unconfigured__"}):
                raise ValueError("POSITION_OWNER_UNPROVEN")
            if (row["portfolio_id"] != portfolio_id or row["currency"] != item.get("currency")
                    or not is_cash_balance(dict(row))):
                raise ValueError("CASH_IDENTITY_UNPROVEN")
            if (row["broker"], row["broker_account_id"]) not in {(None, None), (broker, account)}:
                raise ValueError("POSITION_OWNER_UNPROVEN")
            target = cash_position(broker, account, item["currency"], 0, datetime.now(timezone.utc))
            identity = (portfolio_id, broker, account, target.symbol, item["currency"])
            if identity in targets or any(
                    is_cash_balance(dict(other)) and (other["currency"] == item["currency"] or other["symbol"] == target.symbol)
                    for other in conn.execute("SELECT * FROM positions WHERE portfolio_id=? AND broker=? AND broker_account_id=? AND id<>?",
                        (portfolio_id, broker, account, row["id"]))):
                raise ValueError("CASH_IDENTITY_COLLISION_REQUIRES_REVIEW")
            if conn.execute("SELECT 1 FROM positions WHERE broker=? AND broker_account_id=? AND portfolio_id<>?",
                    (broker, account, portfolio_id)).fetchone() or conn.execute(
                    "SELECT 1 FROM position_snapshot_runs WHERE broker=? AND account_id=? AND portfolio_id<>?",
                    (broker, account, portfolio_id)).fetchone():
                raise ValueError("ACCOUNT_PORTFOLIO_AMBIGUOUS")
            targets.add(identity)
            result.append({**item, "symbol": target.symbol, "ticker": target.raw_symbol})
        return result
    finally:
        conn.row_factory = previous_factory


def apply_corrections(conn, corrections):
    previous_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    for item in corrections:
        row = conn.execute("SELECT * FROM positions WHERE id=?", (item["position_id"],)).fetchone()
        if row is None or row_fingerprint(row) != item["expected_fingerprint"]:
            raise ValueError("POSITION_CAS_MISMATCH")
        if item.get("kind") == "cash_owner":
            conn.execute("UPDATE positions SET symbol=?,ticker=?,broker=?,broker_account_id=?,sync_source='api' WHERE id=?",
                (item["symbol"], item["ticker"], item["broker"], item["account_id"], item["position_id"]))
        else:
            conn.execute("UPDATE positions SET symbol=?, ticker=? WHERE id=?",
                         (item["symbol"], item["ticker"], item["position_id"]))
    # Independently verify the affected identities in the post-correction clone
    # and again in the live migration transaction. Digest/inventory is not a
    # business uniqueness guarantee. Never merge/delete/overwrite a collision.
    for item in corrections:
        row = conn.execute("SELECT * FROM positions WHERE id=?", (item["position_id"],)).fetchone()
        count = conn.execute("SELECT count(*) FROM positions WHERE portfolio_id=? AND broker=? AND broker_account_id=? AND sync_source=? AND symbol=? AND currency=?",
            (row["portfolio_id"], row["broker"], row["broker_account_id"], row["sync_source"], row["symbol"], row["currency"])).fetchone()[0]
        if count != 1:
            raise ValueError("POSITION_IDENTITY_COLLISION_REQUIRES_REVIEW")
    conn.row_factory = previous_factory


def make_plan(database, contract_path, corrections=()):
    contract = verify(database, contract_path, integrity=True)
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as source:
        source.execute("BEGIN")
        before = inventory(source)
        statements = ddl_plan(source)
        existing = sum(int(any(row[1] == col for row in source.execute(f"PRAGMA table_info({table})")))
                       for table, cols in SCHEMA_COLUMNS.items() for col in cols)
        expected = sum(len(cols) for cols in SCHEMA_COLUMNS.values())
        if existing not in {0, expected} or (existing == expected and statements):
            raise ValueError("PARTIAL_B0_SCHEMA_REQUIRES_RECEIPT_RECOVERY")
        if MIGRATION_STAGE in {"u1","u2","b2"} and corrections:
            raise ValueError(MIGRATION_STAGE.upper()+"_HAS_NO_DATA_CORRECTIONS")
        corrections = (cash_owner_plan if MIGRATION_STAGE == "u0" else identity_plan)(
            source, corrections, contract["portfolio_id"])
        with closing(sqlite3.connect(":memory:")) as clone:
            source.backup(clone)
            for statement in statements:
                clone.execute(statement)
            apply_corrections(clone, corrections)
            verify_shape(clone)
            target_digest = schema_digest(clone)
            target_inventory = inventory(clone)
        return {"schema": f"wealthpilot.{MIGRATION_STAGE}-migration.v1", "database_path": str(Path(database).resolve()),
                "file_identity": contract["file_identity"], "portfolio_id": contract["portfolio_id"],
                "old_schema_sha256": contract["schema_sha256"], "target_schema_sha256": target_digest,
                "old_contract_sha256": hashlib.sha256(Path(contract_path).read_bytes()).hexdigest(),
                "before_inventory": before, "target_inventory": target_inventory,
                "ddl": statements, "corrections": corrections,
                "business_writes_reenabled": False}


def apply_plan(database, contract_path, plan, backup_path, receipt_path, *, writers_stopped=False):
    if not writers_stopped:
        raise ValueError("STOP_ALL_WRITERS_AND_COORDINATE_READERS_FIRST")
    if Path(receipt_path).exists():
        raise ValueError("RECEIPT_EXISTS_USE_VERIFY_OR_EXPLICIT_RECOVERY")
    current = make_plan(database, contract_path, plan["corrections"])
    if current != plan:
        raise ValueError("MIGRATION_PLAN_CHANGED")
    contract = load_contract(contract_path)
    manifest = backup(database, backup_path)
    receipt = {**plan, "status": "PREPARED", "backup_path": str(Path(backup_path).resolve()),
               "backup_sha256": manifest["sha256"], "old_contract": dict(contract),
               "created_at": datetime.now(timezone.utc).isoformat()}
    write_private(receipt_path, receipt)
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=rw", uri=True)) as conn:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("BEGIN IMMEDIATE")
        try:
            if schema_digest(conn) != plan["old_schema_sha256"] or inventory(conn) != plan["before_inventory"]:
                raise ValueError("MIGRATION_SOURCE_CHANGED")
            for statement in plan["ddl"]:
                conn.execute(statement)
            apply_corrections(conn, plan["corrections"])
            if schema_digest(conn) != plan["target_schema_sha256"] or inventory(conn) != plan["target_inventory"]:
                raise ValueError("MIGRATION_TARGET_MISMATCH")
            verify_shape(conn)
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("INTEGRITY_FAILED")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    # In-place DDL preserves inode. A crash here leaves PREPARED receipt and
    # strict contract mismatch; old/new services both remain blocked, never auto resign.
    check_file(database, contract)
    receipt["status"] = "DDL_COMMITTED"
    write_private(receipt_path, receipt)
    contract["schema_sha256"] = plan["target_schema_sha256"]
    contract[RECEIPT_KEY] = str(Path(receipt_path).resolve())
    write_private(contract_path, contract)
    verify(database, contract_path, integrity=True)
    receipt["status"] = "VERIFIED"
    write_private(receipt_path, receipt)
    return receipt


def _receipt_stage(receipt):
    if receipt.get("schema") != f"wealthpilot.{MIGRATION_STAGE}-migration.v1":
        raise ValueError("MIGRATION_STAGE_RECEIPT_MISMATCH")


def resume_contract(database, contract_path, receipt_path, *, writers_stopped=False):
    receipt = private_read(receipt_path)
    _receipt_stage(receipt)
    if not writers_stopped or receipt["business_writes_reenabled"] or receipt["status"] not in {"PREPARED", "DDL_COMMITTED"}:
        raise ValueError("RECOVERY_REQUIRES_STOPPED_UNREOPENED_WRITERS")
    contract = load_contract(contract_path)
    if contract not in (receipt["old_contract"], {**receipt["old_contract"],
            "schema_sha256": receipt["target_schema_sha256"],
            RECEIPT_KEY: str(Path(receipt_path).resolve())}):
        raise ValueError("RECOVERY_CONTRACT_CHANGED")
    check_file(database, receipt["old_contract"])
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if schema_digest(conn) != receipt["target_schema_sha256"] or inventory(conn) != receipt["target_inventory"]:
            raise ValueError("RECOVERY_TARGET_UNPROVEN")
        verify_shape(conn)
    contract = {**receipt["old_contract"], "schema_sha256": receipt["target_schema_sha256"],
                RECEIPT_KEY: str(Path(receipt_path).resolve())}
    write_private(contract_path, contract)
    verify(database, contract_path, integrity=True)
    receipt["status"] = "VERIFIED"
    write_private(receipt_path, receipt)


def restore_prewrite(database, contract_path, receipt_path, preserved_backup, *, writers_stopped=False):
    """Explicit pre-write rollback only; never permitted after writer reenabling."""
    from snapshot import verify as verify_backup
    receipt = private_read(receipt_path)
    _receipt_stage(receipt)
    if not writers_stopped or receipt["business_writes_reenabled"] or receipt["status"] != "VERIFIED":
        raise ValueError("PREWRITE_RESTORE_FORBIDDEN_AFTER_WRITER_ENABLE")
    verify(database, contract_path, integrity=True)
    saved = Path(receipt["backup_path"])
    manifest = private_read(str(saved) + ".manifest.json")
    if manifest["sha256"] != receipt["backup_sha256"]:
        raise ValueError("ORIGINAL_BACKUP_CHANGED")
    verify_backup(saved, manifest)
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        if inventory(conn) != receipt["target_inventory"]:
            raise ValueError("BUSINESS_WRITES_OR_UNKNOWN_CHANGES_REFUSE_RESTORE")
    backup(database, preserved_backup)  # immutable evidence of the migrated state
    with closing(sqlite3.connect(saved.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=rw", uri=True)) as destination:
            source.backup(destination)  # preserve the original inode and 0600 mode
            destination.execute("PRAGMA journal_mode=WAL")
            destination.execute("PRAGMA synchronous=FULL")
            if schema_digest(destination) != receipt["old_schema_sha256"] or inventory(destination) != receipt["before_inventory"]:
                raise ValueError("RESTORE_VERIFICATION_FAILED_KEEP_SERVICES_STOPPED")
    write_private(contract_path, receipt["old_contract"])
    verify(database, contract_path, integrity=True)
    receipt["status"] = "RESTORED_BEFORE_WRITES"
    write_private(receipt_path, receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["plan", "apply", "verify", "resume-contract", "mark-writers-enabled", "restore-prewrite"])
    parser.add_argument("--stage", choices=["b0","u0","u1","u2","b2"], default="b0",
                        help="U0/U1/U2/B2 must be explicitly selected; never implicit in B0")
    parser.add_argument("--database", required=True)
    parser.add_argument("--contract", required=True)
    parser.add_argument("--plan")
    parser.add_argument("--receipt")
    parser.add_argument("--backup")
    parser.add_argument("--corrections")
    parser.add_argument("--writers-stopped", action="store_true")
    args = parser.parse_args()
    select_stage(args.stage)
    if args.mode == "plan":
        if not args.plan or Path(args.plan).exists():
            raise ValueError("NEW_PLAN_PATH_REQUIRED")
        corrections = private_read(args.corrections) if args.corrections else []
        write_private(args.plan, make_plan(args.database, args.contract, corrections))
    elif args.mode == "apply":
        if not all((args.plan, args.receipt, args.backup)):
            raise ValueError("PLAN_RECEIPT_BACKUP_REQUIRED")
        apply_plan(args.database, args.contract, private_read(args.plan), args.backup, args.receipt,
                   writers_stopped=args.writers_stopped)
    elif args.mode == "restore-prewrite":
        if not args.receipt or not args.backup:
            raise ValueError("RECEIPT_AND_NEW_PRESERVATION_BACKUP_REQUIRED")
        restore_prewrite(args.database, args.contract, args.receipt, args.backup, writers_stopped=args.writers_stopped)
    elif args.mode == "resume-contract":
        resume_contract(args.database, args.contract, args.receipt, writers_stopped=args.writers_stopped)
    else:
        verify(args.database, args.contract, integrity=True)
        with closing(sqlite3.connect(Path(args.database).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            verify_shape(conn)
        if args.mode == "mark-writers-enabled":
            receipt = private_read(args.receipt)
            _receipt_stage(receipt)
            if receipt["status"] != "VERIFIED" or receipt["target_schema_sha256"] != load_contract(args.contract)["schema_sha256"]:
                raise ValueError("VERIFIED_RECEIPT_REQUIRED")
            receipt["business_writes_reenabled"] = True  # MUST precede any writer restart
            write_private(args.receipt, receipt)
    print(f"{MIGRATION_STAGE.upper()} migration command completed; no service or broker operation performed")


if __name__ == "__main__":
    main()
