"""Policy-only adapter to the existing shared SQLite owner, no pooled handles.

Reuses the existing private shared-sqlite contract (path/device/inode/schema/
portfolio). This is an SQL capability boundary, not a sandbox against arbitrary
Python code or a malicious process with the same OS identity.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3

from app.shared_sqlite import load_contract, check_file, schema_digest
from .contracts import digest
from .parser import PolicyError
from .store import PolicyStore, SCHEMA

TABLES = {"policy_bundles", "policy_events", "policy_releases"}
TRIGGERS = {f"{t}_no_{op}" for t in TABLES for op in ("update", "delete", "replace")}
QUERIES = (
    "SELECT * FROM policy_bundles WHERE scope=? AND bundle_id=?",
    "SELECT * FROM policy_bundles WHERE scope=? AND request_id=?",
    "SELECT coalesce(max(version),0) FROM policy_bundles WHERE scope=?",
    "SELECT coalesce(max(sequence),0)+1 FROM policy_events WHERE scope=?",
    "SELECT * FROM policy_events WHERE scope=? AND request_id=?",
    "SELECT * FROM policy_events WHERE scope=? AND bundle_id=? ORDER BY sequence DESC LIMIT 1",
    "SELECT * FROM policy_events WHERE scope=? ORDER BY sequence",
    "SELECT * FROM policy_events WHERE scope=? AND bundle_id=?",
    "SELECT * FROM policy_events WHERE scope=? AND bundle_id=? AND state='APPROVED'",
    "SELECT * FROM policy_events WHERE scope=?",
    "SELECT * FROM policy_releases WHERE scope=? ORDER BY sequence",
    "SELECT * FROM policy_releases WHERE scope=? ORDER BY sequence DESC",
    "SELECT * FROM policy_releases WHERE scope=? AND sequence<=? ORDER BY sequence",
    "SELECT philosophy,policy FROM policy_bundles WHERE scope=? AND bundle_id=?",
    "INSERT INTO policy_bundles VALUES(?,?,?,?,?,?,?,?,?)",
    "INSERT INTO policy_events VALUES(?,?,?,?,?,?,?)",
    "INSERT INTO policy_releases VALUES(?,?,?,?,?)",
)


def normalized(sql):
    return " ".join(sql.split()).upper()


ALLOWED = {normalized(q) for q in QUERIES}


def policy_schema():
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(SCHEMA)
        return {r[0]: r[1] for r in db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'policy_%'")}
    finally:
        db.close()


class _Rows:
    __slots__ = ("_rows", "_offset")
    def __init__(self, rows): self._rows, self._offset = rows, 0
    def fetchone(self):
        if self._offset >= len(self._rows): return None
        row = self._rows[self._offset]; self._offset += 1
        return row
    def fetchall(self):
        rows = self._rows[self._offset:]; self._offset = len(self._rows)
        return rows
    def __iter__(self): return iter(self.fetchall())


class _PolicyConnection:
    __slots__ = ("_db", "_state", "_scope", "_check")
    def __init__(self, db, state, scope, check):
        self._db, self._state, self._scope, self._check = db, state, scope, check
    def execute(self, sql, parameters=()):
        state = object.__getattribute__(self,"_state")
        db = object.__getattribute__(self,"_db")
        scope = object.__getattribute__(self,"_scope")
        check = object.__getattribute__(self,"_check")
        if state["closed"] or state["denied"]:
            raise PolicyError("POLICY_CONNECTION_CLOSED_OR_TAINTED")
        if (not isinstance(sql,str) or normalized(sql) not in ALLOWED
                or not isinstance(parameters,(tuple,list)) or not parameters or parameters[0] != scope):
            state["denied"] = True
            raise PolicyError("POLICY_SQL_CAPABILITY_DENIED")
        try:
            check()
            return _Rows(db.execute(sql, parameters).fetchall())
        except sqlite3.Error:
            state["denied"] = True
            raise PolicyError("POLICY_SQL_OPERATION_REJECTED") from None
        except BaseException:
            state["denied"] = True
            raise
    def __getattribute__(self, name):
        if name != "execute":
            object.__getattribute__(self,"_state")["denied"] = True
            raise PolicyError("POLICY_CONNECTION_CAPABILITY_DENIED")
        return object.__getattribute__(self,name)


class CoLocatedPolicyStore(PolicyStore):
    """Explicit identity contract, not a boolean bypass of the private Store."""
    def __init__(self, path, contract_path):
        self.path = Path(os.path.abspath(os.path.expanduser(path)))
        self.contract_path = Path(os.path.abspath(os.path.expanduser(contract_path)))
        self.contract = load_contract(self.contract_path)
        self.contract_sha = digest(self.contract)
        self.scope = f"portfolio:{self.contract['portfolio_id']}"
        self._check()

    def _check(self):
        if digest(load_contract(self.contract_path)) != self.contract_sha:
            raise PolicyError("POLICY_DATABASE_CONTRACT_CHANGED")
        check_file(self.path, self.contract)
        # WEALTHPILOT_DB_PATH may intentionally point at a frozen U1 analysis
        # view in a worker. Policy identity stays pinned to the original shared
        # contract; never rebind policy storage to that transient view.
        original = os.environ.get("WEALTHPILOT_SQLITE_CONTRACT")
        if original and (not Path(original).exists() or not os.path.samefile(self.contract_path,original)):
            raise PolicyError("POLICY_SHARED_CONTRACT_OWNER_MISMATCH")
        if self.path.stat().st_nlink != 1:
            raise PolicyError("POLICY_DATABASE_HARDLINK_REJECTED")
        e1 = os.environ.get("WEALTHPILOT_E1_DB_PATH")
        if e1 and Path(e1).exists() and os.path.samefile(self.path,e1):
            raise PolicyError("E1_DATABASE_IS_NOT_POLICY_BUSINESS_DATABASE")

    def init_candidate(self):
        raise PolicyError("COLOCATED_SCHEMA_REQUIRES_SEPARATE_AUTHORIZED_MIGRATION")

    def publisher_identity(self):
        self._check()
        return digest({"policy_store":str(self.path), "file_identity":self.contract["file_identity"],
                       "scope":self.scope, "binding":"shared-sqlite-policy.v1"})

    def _identity(self, scope, request_id):
        PolicyStore._identity(scope, request_id)
        if scope != self.scope: raise PolicyError("POLICY_SCOPE_MISMATCH")

    @contextmanager
    def connection(self, *, write=False):
        self._check()
        # No ORM/session pool and no fallback to business writer_connection().
        db = sqlite3.connect(self.path.as_uri()+("?mode=rw" if write else "?mode=ro"),uri=True,timeout=5,cached_statements=0)
        state = {"denied":False,"closed":False,"control":False}
        def authorize(action,a,b,database,source):
            ok = False
            if action == sqlite3.SQLITE_SELECT: ok = True
            elif action == sqlite3.SQLITE_READ:
                ok = database == "main" and a in TABLES and (source is None or source in TRIGGERS)
            elif action == sqlite3.SQLITE_INSERT:
                ok = write and database == "main" and a in TABLES and source is None
            elif action == sqlite3.SQLITE_FUNCTION: ok = b in {"coalesce","max","raise"}
            elif action == sqlite3.SQLITE_TRANSACTION: ok = state["control"] and a in {"BEGIN","COMMIT","ROLLBACK"}
            if not ok: state["denied"] = True
            return sqlite3.SQLITE_OK if ok else sqlite3.SQLITE_DENY
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            if not write: db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            # Trusted preflight reads SCHEMA METADATA ONLY, never portfolio rows.
            if (db.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
                    or db.execute("PRAGMA application_id").fetchone()[0] == 0x45314231
                    or schema_digest(db) != self.contract["schema_sha256"]):
                raise PolicyError("POLICY_SHARED_DATABASE_IDENTITY_OR_SCHEMA_MISMATCH")
            objects = dict(db.execute("SELECT name,sql FROM sqlite_master WHERE name LIKE 'policy_%'"))
            if objects != policy_schema(): raise PolicyError("POLICY_SCHEMA_NOT_EXACTLY_APPROVED")
            names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "portfolios" not in names: raise PolicyError("EXPECTED_BUSINESS_DATABASE_REQUIRED")
            self._check()
            db.row_factory = sqlite3.Row
            db.set_authorizer(authorize)
            yield _PolicyConnection(db,state,self.scope,self._check)
            self._check()
            if state["denied"]: raise PolicyError("POLICY_CONNECTION_TAINTED_ROLLBACK")
            state["control"] = True
            if write: db.commit()
            else: db.rollback()
        except BaseException:
            state["control"] = True
            db.rollback()
            raise
        finally:
            state["closed"] = True
            db.close()


def configured_store(path, *, shared_contract=None):
    """Existing behavior unchanged unless a full identity contract is supplied."""
    if shared_contract:
        return CoLocatedPolicyStore(path, shared_contract)
    return PolicyStore(path)
