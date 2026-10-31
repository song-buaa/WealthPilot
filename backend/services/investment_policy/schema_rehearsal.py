"""Install only policy-prefixed objects on a verified, private OFFLINE COPY.

No source backup writes, business imports, restore-to-production or initialization
fallback. Source must be a verified standalone SQLite Backup API export. For
production installation a separate runbook/authorization is required.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from .contracts import digest
from .parser import PolicyError
from .store import SCHEMA, PolicyStore


def inventory(db):
    """Same row-multiset algorithm as existing poc03.snapshot, no row disclosure."""
    result = {}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
        quoted = '"' + name.replace('"', '""') + '"'
        rows = [hashlib.sha256(json.dumps(list(r), default=lambda b: {"bytes": b.hex()},
                    ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
                for r in db.execute("SELECT * FROM " + quoted)]
        result[name] = {"rows": len(rows), "digest": hashlib.sha256("".join(sorted(rows)).encode()).hexdigest()}
    return result


def statements():
    pending = ""
    for line in SCHEMA.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending.strip()
            pending = ""
    if pending.strip():
        raise PolicyError("POLICY_DDL_INCOMPLETE")


def _objects(db):
    return {r[0]: r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'")}


def _execute(db, statement):
    db.execute(statement)


def migration_authorizer(action, a, b, database, source):
    """Only the reviewed policy DDL phase; inventory is a separate audit phase."""
    tables = {"policy_bundles", "policy_events", "policy_releases"}
    triggers = {f"{t}_no_{op}" for t in tables for op in ("update", "delete", "replace")}
    allowed = False
    if action == sqlite3.SQLITE_CREATE_TABLE: allowed = a in tables
    elif action == sqlite3.SQLITE_CREATE_TRIGGER: allowed = a in triggers and b in tables
    elif action == sqlite3.SQLITE_CREATE_INDEX: allowed = b in tables and a.startswith("sqlite_autoindex_policy_")
    elif action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE}: allowed = a == "sqlite_master"
    elif action == sqlite3.SQLITE_READ: allowed = a == "sqlite_master" or a in tables
    elif action == sqlite3.SQLITE_SELECT: allowed = True
    elif action == sqlite3.SQLITE_FUNCTION: allowed = b == "raise"
    return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY


def install_on_isolated_copy(*, backup, manifest, target, rehearsal_root):
    """Caller explicitly supplies COPY and Backup API manifest; never copies live WAL."""
    backup, target, root = (Path(p).absolute() for p in (backup, target, rehearsal_root))
    for path in (backup, target, root):
        if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
            raise PolicyError("SYMLINK_REHEARSAL_PATH_FORBIDDEN")
    backup, target, root = (p.resolve(strict=True) for p in (backup, target, root))
    if (not target.is_relative_to(root) or root == target or backup.is_relative_to(root)
            or os.path.samefile(backup, target) or target.stat().st_nlink != 1
            or not stat.S_ISREG(target.stat().st_mode) or target.stat().st_uid != os.getuid()
            or stat.S_IMODE(root.stat().st_mode) != 0o700
            or stat.S_IMODE(target.parent.stat().st_mode) != 0o700
            or stat.S_IMODE(target.stat().st_mode) != 0o600):
        raise PolicyError("SEPARATE_PRIVATE_OFFLINE_COPY_REQUIRED")
    PolicyStore(target)  # includes business/E1 path and repository rejection.
    if manifest.get("schema") != "poc03.sqlite-backup.v1" or manifest.get("integrity") != "ok":
        raise PolicyError("VERIFIED_SQLITE_BACKUP_REQUIRED")
    if hashlib.sha256(backup.read_bytes()).hexdigest() != manifest.get("sha256"):
        raise PolicyError("BACKUP_DIGEST_MISMATCH")
    if any(Path(str(target)+suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise PolicyError("CLOSE_OFFLINE_COPY_BEFORE_MIGRATION")
    with closing(sqlite3.connect(backup.as_uri()+"?mode=ro", uri=True)) as src:
        src.execute("PRAGMA query_only=ON")
        if src.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise PolicyError("STANDALONE_BACKUP_REQUIRED")
        if src.execute("PRAGMA integrity_check").fetchall() != [("ok",)] or inventory(src) != manifest["tables"]:
            raise PolicyError("BACKUP_VERIFICATION_FAILED")
        if src.execute("PRAGMA foreign_key_check").fetchall():
            raise PolicyError("SOURCE_BACKUP_FOREIGN_KEY_VIOLATIONS")
        source_objects = _objects(src)
    # Derive authoritative object definitions using only an in-memory SQLite DB.
    with closing(sqlite3.connect(":memory:")) as expected:
        expected.executescript(SCHEMA)
        required = _objects(expected)
    with closing(sqlite3.connect(target.as_uri()+"?mode=rw", uri=True)) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            before, objects_before = inventory(db), _objects(db)
            if (any(objects_before.get(k) != v for k,v in source_objects.items())
                    or any(k not in source_objects and k not in required for k in objects_before)):
                raise PolicyError("OFFLINE_COPY_SCHEMA_DIFFERS_FROM_BACKUP")
            if any(before.get(k) != v for k,v in manifest["tables"].items()):
                raise PolicyError("OFFLINE_COPY_DIFFERS_FROM_VERIFIED_BACKUP")
            if any(k not in manifest["tables"] and k not in {"policy_bundles", "policy_events", "policy_releases"} for k in before):
                raise PolicyError("UNEXPECTED_COPY_TABLE")
            db.set_authorizer(migration_authorizer)
            try:
                for name, sql in required.items():
                    if name in objects_before:
                        if " ".join(objects_before[name].split()) != " ".join(sql.split()):
                            raise PolicyError("INCOMPATIBLE_POLICY_SCHEMA")
                    else:
                        _execute(db, sql)
            finally:
                db.set_authorizer(None)  # private audit/commit only; never yielded
            after = inventory(db)
            if any(after[k] != v for k,v in before.items()) or any(_objects(db).get(k) != v for k,v in objects_before.items()):
                raise PolicyError("PREEXISTING_HISTORY_OR_SCHEMA_CHANGED")
            if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise PolicyError("COPY_INTEGRITY_FAILED")
            fk_before = db.execute("PRAGMA foreign_key_check").fetchall()
            if fk_before:
                raise PolicyError("COPY_FOREIGN_KEY_CHECK_FAILED")
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {"status": "ISOLATED_SCHEMA_PASS", "schema": "wp.policy.v1", "before": before, "after": after,
            "added_objects": sorted(set(required)-set(objects_before)), "source_backup_sha256": manifest["sha256"],
            "preexisting_history_unchanged": True, "schema_objects_sha256": digest(required),
            "production_migration_executed": False}
