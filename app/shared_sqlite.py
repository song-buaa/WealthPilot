"""Opt-in local WAL contract. No business calculations or schema migrations.

The private receipt binds a live file (device/inode), schema and portfolio, not
its changing contents. It is an operator boundary, not protection against a
malicious process running as the same OS user. Backup hashes remain immutable.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat


def require(condition, code):
    if not condition:
        raise ValueError(code)


def private_file(path, mode=None):
    path = Path(path).absolute()
    require(not path.is_symlink() and path.resolve(strict=True) == path,
            "SHARED_PATH_SYMLINK_REFUSED")
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and
            info.st_mode & 0o077 == 0, "SHARED_PRIVATE_OWNER_REQUIRED")
    if mode is not None:
        require(stat.S_IMODE(info.st_mode) == mode, "SHARED_DATABASE_MODE_0600_REQUIRED")
    return path


def schema_digest(connection):
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
    return hashlib.sha256(json.dumps(rows, separators=(",", ":")).encode()).hexdigest()


def load_contract(path):
    path = private_file(path)
    contract = json.loads(path.read_text())
    require(contract.get("schema") == "wealthpilot.shared-sqlite.v1" and
            contract.get("mode") == "shared_wal" and
            type(contract.get("portfolio_id")) is int and contract["portfolio_id"] > 0,
            "SHARED_CONTRACT_INVALID")
    return contract


def check_file(database, contract):
    path = private_file(database, 0o600)
    require(str(path) == contract["database_path"], "SHARED_DATABASE_PATH_MISMATCH")
    parent = path.parent.stat()
    require(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) == 0o700,
            "SHARED_DIRECTORY_MODE_0700_REQUIRED")
    info = path.stat()
    require([info.st_dev, info.st_ino] == contract["file_identity"],
            "SHARED_DATABASE_IDENTITY_MISMATCH")
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            private_file(sidecar)
    return path


def check_connection(connection, contract, *, integrity=False):
    require(connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal",
            "SHARED_WAL_REQUIRED")
    require(schema_digest(connection) == contract["schema_sha256"], "SHARED_SCHEMA_MISMATCH")
    require(connection.execute("SELECT id FROM portfolios WHERE id=?",
                               (contract["portfolio_id"],)).fetchone() is not None,
            "SHARED_PORTFOLIO_MISSING")
    if integrity:
        require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)],
                "SHARED_INTEGRITY_FAILED")


def verify(database, contract_path, *, connection=None, integrity=False):
    contract = load_contract(contract_path)
    path = check_file(database, contract)
    if connection is not None:
        check_connection(connection, contract, integrity=integrity)
    else:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            check_connection(conn, contract, integrity=integrity)
    check_file(database, contract)
    return contract


def writer_connection(database, contract_path):
    """Explicit existing-file RW connection for the one restricted Web writer.

    SQLite serializes short write transactions. No auto-creation, init_db or
    journal-mode changes here; prepare-shared performs the controlled cutover.
    """
    contract = load_contract(contract_path)
    path = check_file(database, contract)
    conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True,
                           timeout=5, check_same_thread=False)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=FULL")
        check_connection(conn, contract)
        check_file(database, contract)
        return conn
    except Exception:
        conn.close()
        raise
