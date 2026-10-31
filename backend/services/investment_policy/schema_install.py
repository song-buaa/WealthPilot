"""Explicit operator-only policy DDL and forward-only shared-owner cutover.

No startup hook, service controller, Publisher or business imports. A reviewed
private authorization is an operator attestation, not cryptographic human auth.
Processes must remain stopped throughout; SQLite WAL does not fence old readers.
"""
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile

from app.shared_sqlite import check_file, load_contract, private_file, schema_digest, verify
from .colocated import TABLES, policy_schema
from .contracts import digest
from .parser import PolicyError
from .schema_rehearsal import inventory, migration_authorizer, _objects
from .store import clock


COMPONENTS = {"web", "backend", "mcp", "business_worker", "other_database_writers"}


def require(ok, code):
    if not ok:
        raise PolicyError(code)


def file_sha(path):
    return hashlib.sha256(private_file(path).read_bytes()).hexdigest()


def read_json(path):
    path = private_file(path, 0o600)
    require(path.stat().st_nlink == 1, "INSTALL_RECEIPT_HARDLINK")
    return json.loads(path.read_text())


def write_json(path, value, *, replace=False):
    """Durable private output, never follow links; atomic contract replacement."""
    path = Path(path).absolute()
    parent = path.parent
    require(parent.resolve(strict=True) == parent and parent.stat().st_uid == os.getuid()
            and parent.stat().st_mode & 0o777 == 0o700, "INSTALL_PRIVATE_DIRECTORY_REQUIRED")
    if replace:
        private_file(path, 0o600)
    else:
        require(not path.exists() and not path.is_symlink(), "INSTALL_OUTPUT_ALREADY_EXISTS")
    fd, temporary = tempfile.mkstemp(prefix=".policy-install-", dir=parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)  # exclusive publication, no overwritten receipt
            os.unlink(temporary)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def audit(db):
    require(db.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "INSTALL_INTEGRITY_FAILED")
    require(not db.execute("PRAGMA foreign_key_check").fetchall(), "INSTALL_FK_FAILED")
    return {"tables": inventory(db), "objects": _objects(db), "schema_sha256": schema_digest(db)}


def read_audit(path):
    with closing(sqlite3.connect(private_file(path).as_uri()+"?mode=ro", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        return audit(db)


def verified_backup(path, manifest, *, fresh=True):
    require(manifest.get("schema") == "poc03.sqlite-backup.v1"
            and manifest.get("integrity") == "ok" and file_sha(path) == manifest.get("sha256"),
            "INSTALL_VERIFIED_BACKUP_REQUIRED")
    if fresh:
        age = (datetime.now(timezone.utc)-clock(manifest["created_at"])).total_seconds()
        require(0 <= age <= 86400, "INSTALL_FRESH_BACKUP_REQUIRED")
    with closing(sqlite3.connect(private_file(path).as_uri()+"?mode=ro", uri=True)) as source:
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")
        require(source.execute("PRAGMA journal_mode").fetchone()[0] == "delete",
                "INSTALL_STANDALONE_BACKUP_REQUIRED")
        before = audit(source)
        require(before["tables"] == manifest["tables"], "INSTALL_BACKUP_TABLE_MISMATCH")
        # Actual isolated restore via Backup API, not an asserted boolean.
        with closing(sqlite3.connect(":memory:")) as restored:
            source.backup(restored)
            require(audit(restored) == before, "INSTALL_RESTORE_FAILED")
    return before


def identity(path):
    path = private_file(path, 0o600)
    require(path.stat().st_nlink == 1, "INSTALL_DATABASE_HARDLINK")
    st = path.stat()
    return {"path": str(path), "file_identity": [st.st_dev, st.st_ino], "uid": st.st_uid}


def make_plan(*, database, contract_path, backup, manifest, e1_database, e1_backup,
              e1_manifest, code_sha):
    """Read-only plan. All business digests stay in the private handoff directory."""
    require(isinstance(code_sha, str) and len(code_sha) == 40
            and all(c in "0123456789abcdef" for c in code_sha), "INSTALL_CODE_SHA_REQUIRED")
    contract = verify(database, contract_path, integrity=True)
    require(identity(database)["file_identity"] != identity(e1_database)["file_identity"], "INSTALL_E1_MUST_BE_SEPARATE")
    require(not os.path.samefile(database, backup) and not os.path.samefile(e1_database, e1_backup),
            "INSTALL_BACKUP_MUST_BE_SEPARATE")
    before = verified_backup(backup, manifest)
    e1 = verified_backup(e1_backup, e1_manifest)
    require(read_audit(database) == before and read_audit(e1_database) == e1,
            "INSTALL_NEW_MATCHING_BACKUPS_REQUIRED")
    required = policy_schema()
    require(not any(k.startswith("policy_") for k in before["objects"]), "INSTALL_POLICY_SCHEMA_ALREADY_PRESENT")
    with closing(sqlite3.connect(":memory:")) as expected:
        # Derive the post-DDL fingerprint using the restored backup, not invented SQL.
        with closing(sqlite3.connect(private_file(backup).as_uri()+"?mode=ro", uri=True)) as src:
            src.backup(expected)
        for sql in required.values():
            expected.execute(sql)
        after_schema = schema_digest(expected)
    return {"schema": "wp.policy-schema-install.v1", "created_at": datetime.now(timezone.utc).isoformat(),
            "host": socket.gethostname(), "uid": os.getuid(), "code_sha": code_sha,
            "database": identity(database), "e1_database": identity(e1_database),
            "contract_path": str(private_file(contract_path)), "contract": contract,
            "before": before, "e1_before": e1, "required_objects": required,
            "after_schema_sha256": after_schema, "backup_sha256": manifest["sha256"],
            "e1_backup_sha256": e1_manifest["sha256"], "restore_verified": True,
            "backups": [{"path": str(private_file(backup)), "manifest": manifest},
                        {"path": str(private_file(e1_backup)), "manifest": e1_manifest}],
            "policy_active": False, "production_installation_authorized": False}


def _authorization(plan, authorization, now, *, recovery=False):
    require(authorization.get("schema") == "wp.policy-schema-authorization.v1"
            and authorization.get("operation") == ("RESUME_POLICY_SCHEMA" if recovery else "INSTALL_POLICY_SCHEMA")
            and authorization.get("decision") == "APPROVED"
            and authorization.get("human_confirmation") is True
            and authorization.get("environment") in {"PRODUCTION", "ISOLATED_REHEARSAL"}
            and authorization.get("approval_id") and authorization.get("approved_by")
            and authorization.get("user_authorization_reference")
            and authorization.get("plan_sha256") == digest(plan), "INSTALL_EXPLICIT_AUTHORIZATION_REQUIRED")
    start, end = clock(authorization["approved_at"]), clock(authorization["expires_at"])
    require(start <= now <= end and (end-start).total_seconds() <= 86400,
            "INSTALL_AUTHORIZATION_EXPIRED")
    if not recovery:
        created = clock(plan["created_at"])
        require(0 <= (now-created).total_seconds() <= 86400, "INSTALL_FRESH_PLAN_REQUIRED")
    q = authorization.get("quiescence", {})
    require(q.get("all_database_clients_stopped") is True and q.get("active_peer_workers") == 0
            and set(q.get("components", [])) == COMPONENTS
            and q.get("stop_receipt_sha256") and isinstance(q.get("stopped_pids"), list),
            "INSTALL_STOPPED_CLIENTS_RECEIPT_REQUIRED")
    for pid in q["stopped_pids"]:
        require(type(pid) is int and pid > 0, "INSTALL_INVALID_STOPPED_PID")
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        raise PolicyError("INSTALL_DATABASE_CLIENT_STILL_RUNNING")
    require(plan["host"] == socket.gethostname() and plan["uid"] == os.getuid(), "INSTALL_DEVICE_OR_OWNER_MISMATCH")


def _live(plan, db, *, after=False):
    require(identity(plan["database"]["path"]) == plan["database"], "INSTALL_DATABASE_IDENTITY_CHANGED")
    require(identity(plan["e1_database"]["path"]) == plan["e1_database"], "INSTALL_E1_IDENTITY_CHANGED")
    require(read_audit(plan["e1_database"]["path"]) == plan["e1_before"], "INSTALL_E1_CHANGED")
    current = audit(db)
    require(db.execute("PRAGMA journal_mode").fetchone()[0] == "wal", "INSTALL_SHARED_WAL_REQUIRED")
    objects = {**plan["before"]["objects"], **plan["required_objects"]} if after else plan["before"]["objects"]
    tables = dict(plan["before"]["tables"])
    if after:
        empty = {"rows": 0, "digest": hashlib.sha256(b"").hexdigest()}
        tables.update({name: empty for name in TABLES})
    require(current["objects"] == objects and current["tables"] == tables,
            "INSTALL_HISTORY_OR_SCHEMA_CHANGED")
    require(current["schema_sha256"] == (plan["after_schema_sha256"] if after else plan["contract"]["schema_sha256"]),
            "INSTALL_SCHEMA_FINGERPRINT_MISMATCH")
    check_file(plan["database"]["path"], plan["contract"])


def _ddl(db, required):
    """No caller-controlled SQL; only authoritative SCHEMA, under original guard."""
    require(required == policy_schema(), "INSTALL_DDL_NOT_AUTHORITATIVE")
    db.set_authorizer(migration_authorizer)
    try:
        for sql in required.values():
            db.execute(sql)
    finally:
        db.set_authorizer(None)


def execute(*, plan, authorization, receipt_directory, recover=False):
    """Separate DDL transaction, then atomic contract update; recover forward only.

    A durable prepared journal precedes COMMIT. On power loss/contract failure,
    re-run --recover with the same approved plan. Never drop tables or restore DB.
    """
    _authorization(plan, authorization, datetime.now(timezone.utc), recovery=recover)
    require(plan["required_objects"] == policy_schema(), "INSTALL_DDL_NOT_AUTHORITATIVE")
    require(len(plan["backups"]) == 2, "INSTALL_TWO_BACKUPS_REQUIRED")
    for item, expected in zip(plan["backups"], (plan["before"], plan["e1_before"])):
        require(verified_backup(item["path"], item["manifest"], fresh=not recover) == expected,
                "INSTALL_BACKUP_CHANGED")
    folder = Path(receipt_directory).absolute()
    require(folder.resolve(strict=True) == folder and folder.stat().st_uid == os.getuid()
            and folder.stat().st_mode & 0o777 == 0o700, "INSTALL_PRIVATE_DIRECTORY_REQUIRED")
    journal, completed = folder/"prepared.json", folder/"completed.json"
    key = {"plan": plan, "authorization_sha256": digest(authorization)}
    lock = folder/"installer.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        private_file(lock, 0o600)
        require(lock.stat().st_nlink == 1, "INSTALL_LOCK_HARDLINK")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if recover:
            prepared = read_json(journal)
            require(prepared.get("plan") == plan
                    and authorization.get("prepared_sha256") == digest(prepared),
                    "INSTALL_RECOVERY_AUTHORIZATION_MISMATCH")
            key = prepared  # immutable original approval/plan, never rewritten
        if completed.exists():
            require(read_json(journal) == key, "INSTALL_REQUEST_CONFLICT")
            verify(plan["database"]["path"], plan["contract_path"], integrity=True)
            with closing(sqlite3.connect(Path(plan["database"]["path"]).as_uri()+"?mode=ro", uri=True)) as db:
                db.execute("BEGIN")
                _live(plan, db, after=True)
            return read_json(completed)
        if journal.exists():
            require(recover and read_json(journal) == key, "INSTALL_RECOVERY_REQUIRED")
        else:
            require(not recover, "INSTALL_PREPARED_RECEIPT_MISSING")
            write_json(journal, key)
        if recover:
            recovery_receipt = folder/("recovery-"+digest(authorization)+".json")
            if not recovery_receipt.exists():
                write_json(recovery_receipt, authorization)
        old = plan["contract"]
        new = {**old, "schema_sha256": plan["after_schema_sha256"]}
        current = load_contract(plan["contract_path"])
        require(current in (old, new), "INSTALL_OWNER_CONTRACT_CHANGED")
        check_file(plan["database"]["path"], old)
        with closing(sqlite3.connect(Path(plan["database"]["path"]).as_uri()+"?mode=rw", uri=True,
                                     timeout=0, cached_statements=0)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            try:
                if schema_digest(db) == old["schema_sha256"]:
                    require(current == old, "INSTALL_CONTRACT_AHEAD_OF_DATABASE")
                    _live(plan, db)
                    _ddl(db, plan["required_objects"])
                else:
                    require(recover, "INSTALL_UNEXPECTED_PREINSTALLED_SCHEMA")
                _live(plan, db, after=True)
                require(load_contract(plan["contract_path"]) == current, "INSTALL_OWNER_CONTRACT_CHANGED")
                db.commit()
            except BaseException:
                db.rollback()
                raise
        # If this step fails the DB is committed but all clients MUST stay stopped.
        # Recovery verifies every byte of schema/data before forward rebinding.
        require(load_contract(plan["contract_path"]) == current, "INSTALL_OWNER_CONTRACT_CHANGED")
        if current != new:
            write_json(plan["contract_path"], new, replace=True)
        verify(plan["database"]["path"], plan["contract_path"], integrity=True)
        result = {"status": "POLICY_SCHEMA_INSTALLED_NOT_ACTIVE", "plan_sha256": digest(plan),
                  "code_sha": plan["code_sha"], "schema_before": old["schema_sha256"],
                  "schema_after": new["schema_sha256"], "objects": sorted(plan["required_objects"]),
                  "original_history_unchanged": True, "e1_unchanged": True,
                  "policy_rows": 0, "policy_active": False, "services_restarted": False,
                  "environment": authorization["environment"], "completed_at": datetime.now(timezone.utc).isoformat()}
        write_json(completed, result)
        return result
    finally:
        os.close(fd)
