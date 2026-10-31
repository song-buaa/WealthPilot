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
import socket
import stat
import sys
import tempfile
import uuid

from common import ROOT, digest, private_dir, private_json, require, safe_cli, write_json


def _closed_source_binding(path):
    """Filesystem checks only: never open SQLite, checkpoint or touch sidecars."""
    path = Path(path).absolute()
    require(path.resolve(strict=True) == path and not any(p.is_symlink() for p in (path, *path.parents)),
            "CLOSED_SOURCE_SYMLINK_REFUSED")
    for suffix in ("-wal", "-shm", "-journal"):
        p = Path(str(path)+suffix)
        require(not p.exists() and not p.is_symlink(), "CLOSED_SOURCE_SIDECAR_PRESENT")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and before.st_uid == os.getuid() and before.st_mode & 0o077 == 0,
                "CLOSED_SOURCE_PRIVATE_FILE_REQUIRED")
        header = stream.read(100)
        require(header[:16] == b"SQLite format 3\x00" and header[18:20] == b"\x02\x02",
                "CLOSED_SOURCE_WAL_HEADER_REQUIRED")
        stream.seek(0)
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
        after = os.fstat(stream.fileno())
    def identity(info):
        return [info.st_dev, info.st_ino, info.st_uid, info.st_size,
                info.st_mtime_ns, info.st_ctime_ns, info.st_mode, info.st_nlink]
    require(identity(before) == identity(after) == identity(path.lstat()), "CLOSED_SOURCE_CHANGED")
    for suffix in ("-wal", "-shm", "-journal"):
        p = Path(str(path)+suffix)
        require(not p.exists() and not p.is_symlink(), "CLOSED_SOURCE_SIDECAR_PRESENT")
    return {"path": str(path), "identity": identity(after), "sha256": sha}


def closed_source_evidence(path, *, closure_evidence_ref, all_clients_stopped=False,
                           automatic_restart_disabled=False, no_unmerged_wal=False):
    """Operator attests actual closure; absence of files alone is NOT proof.

    The caller must keep all clients stopped and their restart mechanisms disabled
    throughout probe/backup. This is not a process controller or authentication.
    """
    require(all_clients_stopped is True and automatic_restart_disabled is True
            and no_unmerged_wal is True and isinstance(closure_evidence_ref, str)
            and bool(closure_evidence_ref.strip()), "CLOSED_SOURCE_CLOSURE_EVIDENCE_REQUIRED")
    return {"schema": "poc03.closed-sqlite-source.v1", "host": socket.gethostname(),
            "all_clients_stopped": True, "automatic_restart_disabled": True, "no_unmerged_wal": True,
            "closure_evidence_ref": closure_evidence_ref, "source": _closed_source_binding(path)}


def verify_closed_source(path, evidence):
    require(isinstance(evidence, dict) and evidence.get("schema") == "poc03.closed-sqlite-source.v1"
            and evidence.get("host") == socket.gethostname()
            and evidence.get("all_clients_stopped") is True
            and evidence.get("automatic_restart_disabled") is True
            and evidence.get("no_unmerged_wal") is True
            and isinstance(evidence.get("closure_evidence_ref"), str)
            and bool(evidence["closure_evidence_ref"].strip()), "CLOSED_SOURCE_CLOSURE_EVIDENCE_REQUIRED")
    require(_closed_source_binding(path) == evidence.get("source"), "CLOSED_SOURCE_BINDING_MISMATCH")


class _ClosedReadConnection(sqlite3.Connection):
    def close(self):
        try:
            super().close()
        finally:
            verify_closed_source(self.closed_path, self.closed_evidence)


def read_connection(path, *, closed_source=None):
    if closed_source is None:
        # Original live-WAL/default behavior, never retry with immutable on error.
        conn = sqlite3.connect(Path(path).resolve(strict=True).as_uri() + "?mode=ro", uri=True)
    else:
        verify_closed_source(path, closed_source)
        conn = sqlite3.connect(Path(path).absolute().as_uri()+"?mode=ro&immutable=1", uri=True,
                               factory=_ClosedReadConnection)
        conn.closed_path, conn.closed_evidence = path, closed_source
    try:
        conn.execute("PRAGMA query_only=ON")
        return conn
    except BaseException:
        conn.close()
        raise


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


def backup(source, destination, *, closed_source=None):
    destination = Path(destination).absolute()
    private_dir(destination.parent)
    require(not destination.exists(), "BACKUP_DESTINATION_EXISTS")
    require(not Path(str(destination) + ".manifest.json").exists(), "BACKUP_MANIFEST_ALREADY_EXISTS")
    fd, name = tempfile.mkstemp(dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        # Do not add kwargs to existing callers' monkeypatched/default readers.
        reader = read_connection(source) if closed_source is None else read_connection(source, closed_source=closed_source)
        with closing(reader) as read:
            read.execute("BEGIN")
            if closed_source is not None:
                require(read.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "SOURCE_INTEGRITY_FAILED")
                source_fk = read.execute("PRAGMA foreign_key_check").fetchall()
            source_inventory = inventory(read)  # pins the source snapshot, including committed WAL.
            with closing(sqlite3.connect(temporary)) as write:
                read.backup(write)
                write.execute("PRAGMA journal_mode=DELETE")
                require(write.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "BACKUP_INTEGRITY_FAILED")
                require(inventory(write) == source_inventory, "BACKUP_CONTENT_MISMATCH")
                if closed_source is not None:
                    require(write.execute("PRAGMA foreign_key_check").fetchall() == source_fk,
                            "BACKUP_FOREIGN_KEY_MISMATCH")
            read.rollback()
        with temporary.open("rb") as data:
            os.fsync(data.fileno())
        if closed_source is not None:
            verify_closed_source(source, closed_source)  # before publishing any backup/manifest
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
    # U0 valuation references read native CASH from the same database snapshot.
    # Credentials are never installed or used by this read-only operation.
    result = read(database, int(config["portfolio_id"]), cash_configured=bool(tiger))
    require(result["status"] != "unavailable" and all(result[k] is not None for k in
            ("total_assets_minor", "total_liabilities_minor", "net_worth_minor")), "SOURCE_VALUATION_UNAVAILABLE")
    return {"schema": "poc03.valuation-reference.v2", "database_sha256": digest(database),
            "portfolio_id": int(config["portfolio_id"]), "result": result,
            "cash_reference": {"configured": bool(tiger), "source": "stored_native_cash"},
            "fx_degraded_at_capture": probe.fx_degraded}


def check_reference(database, reference):
    require(digest(database) == reference["database_sha256"], "REFERENCE_DATABASE_MISMATCH")
    read, portfolio, _ = business_modules()
    cash = reference["cash_reference"]
    require(not cash.get("has_details"), "LEGACY_EXTERNAL_CASH_REFERENCE_REQUIRES_RECAPTURE")
    result = read(database, int(reference["portfolio_id"]), cash_configured=cash["configured"])
    checks = {k: result[k] == reference["result"][k] for k in ("total_assets_minor", "total_liabilities_minor", "net_worth_minor")}
    require(all(checks.values()), "MIGRATED_AMOUNTS_MISMATCH")
    return {"amounts_equal": checks, "cash_source": "stored_native_cash",
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
