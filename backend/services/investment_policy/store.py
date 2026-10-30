"""SQLite migration candidate: immutable sources/snapshots and lifecycle receipts.

Uses stdlib SQLite like the existing private research store, without importing a
business DB, knowledge index or backend lifecycle. P1 has NO activation writer.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat

from .contracts import canonical, digest
from .parser import PolicyError, derive

SCHEMA = """
CREATE TABLE policy_bundles (
 scope TEXT NOT NULL, version INTEGER NOT NULL, bundle_id TEXT NOT NULL,
 request_id TEXT NOT NULL, request_sha TEXT NOT NULL, payload TEXT NOT NULL,
 payload_sha TEXT NOT NULL, philosophy BLOB NOT NULL, policy BLOB NOT NULL,
 PRIMARY KEY(scope, version), UNIQUE(scope,bundle_id), UNIQUE(scope,request_id));
CREATE TABLE policy_events (
 scope TEXT NOT NULL, sequence INTEGER NOT NULL, bundle_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('DRAFT','VALIDATED','APPROVED')),
 request_id TEXT NOT NULL, payload TEXT NOT NULL, payload_sha TEXT NOT NULL,
 PRIMARY KEY(scope,sequence), UNIQUE(scope,request_id),
 FOREIGN KEY(scope,bundle_id) REFERENCES policy_bundles(scope,bundle_id));
CREATE TABLE policy_releases (
 scope TEXT NOT NULL, sequence INTEGER NOT NULL, bundle_id TEXT NOT NULL,
 payload TEXT NOT NULL, payload_sha TEXT NOT NULL,
 PRIMARY KEY(scope,sequence),
 FOREIGN KEY(scope,bundle_id) REFERENCES policy_bundles(scope,bundle_id));
"""
for _table in ("policy_bundles", "policy_events", "policy_releases"):
    for _operation in ("UPDATE", "DELETE"):
        SCHEMA += f"CREATE TRIGGER {_table}_no_{_operation.lower()} BEFORE {_operation} ON {_table} BEGIN SELECT RAISE(ABORT,'POLICY_IMMUTABLE'); END;\n"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def clock(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError()
        return result
    except (AttributeError, TypeError, ValueError) as exc:
        raise PolicyError("TIMEZONE_AWARE_CLOCK_REQUIRED") from exc


class PolicyStore:
    def __init__(self, path):
        if not path:
            raise PolicyError("EXPLICIT_PRIVATE_STORE_REQUIRED")
        self.path = Path(os.path.abspath(os.path.expanduser(path)))
        if any(p.is_symlink() for p in (self.path, *self.path.parents)):
            raise PolicyError("SYMLINK_STORE_REJECTED")
        repo = Path(__file__).resolve().parents[3]
        if self.path.is_relative_to(repo):
            raise PolicyError("PRIVATE_POLICY_OUTSIDE_REPOSITORY_REQUIRED")
        protected = [repo / 'data' / 'wealthpilot.db']
        for key in ("WEALTHPILOT_DB_PATH", "WEALTHPILOT_E1_DB_PATH"):
            if os.environ.get(key):
                protected.append(Path(os.environ[key]).expanduser().absolute())
        if any(self.path == p or (self.path.exists() and p.exists() and os.path.samefile(self.path, p)) for p in protected):
            raise PolicyError("EXISTING_BUSINESS_OR_E1_STORE_FORBIDDEN_IN_P1")

    def init_candidate(self):
        """Explicit isolated schema rehearsal; refuses every existing file."""
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(self.path.parent.stat().st_mode) != 0o700:
            raise PolicyError("PRIVATE_DIRECTORY_REQUIRED")
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with sqlite3.connect(self.path) as db:
            db.executescript(SCHEMA)
        return {"schema": "wp.policy.v1", "active": False}

    @contextmanager
    def connection(self, *, write=False):
        # Recheck symlinks/identity/permissions at each operation; reads cannot create a DB.
        PolicyStore(self.path)
        info = self.path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or stat.S_IMODE(self.path.parent.stat().st_mode) != 0o700):
            raise PolicyError("PRIVATE_STORE_REQUIRED")
        db = sqlite3.connect(self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE" if write else "PRAGMA query_only=ON")
            yield db
            if write:
                db.commit()
        except Exception:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _read(row):
        if row is None:
            raise PolicyError("POLICY_RECORD_NOT_FOUND")
        value = json.loads(row["payload"])
        if digest(value) != row["payload_sha"]:
            raise PolicyError("POLICY_RECORD_HASH_MISMATCH")
        return value

    def _bundle(self, db, scope, bundle_id):
        row = db.execute("SELECT * FROM policy_bundles WHERE scope=? AND bundle_id=?", (scope, bundle_id)).fetchone()
        value = self._read(row)
        actual = derive(row["philosophy"], row["policy"], expected_hashes=value["source_hashes"], policy_version=value["bundle_version"])
        if (value["structured_sha256"] != digest(actual) or value["snapshot"] != actual.model_dump(mode="json")
                or value["scope"] != scope or value["bundle_id"] != bundle_id):
            raise PolicyError("MARKDOWN_DERIVATIVE_MISMATCH")
        return value

    @staticmethod
    def _identity(scope, request_id):
        if not isinstance(scope, str) or not scope.strip() or not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise PolicyError("SCOPE_AND_REQUEST_ID_REQUIRED")

    def stage(self, *, scope, version, philosophy_path, policy_path, expected_hashes,
              request_id, expected_head_version, created_at, supplied_snapshot=None):
        self._identity(scope, request_id)
        clock(created_at)
        paths = {"philosophy": Path(philosophy_path), "policy": Path(policy_path)}
        raw = {role: path.read_bytes() for role, path in paths.items()}
        snapshot = derive(**raw, expected_hashes=expected_hashes, policy_version=version)
        if clock(created_at).date().isoformat() < snapshot.content_approved_on:
            raise PolicyError("BUNDLE_PREDATES_CONTENT_APPROVAL")
        if supplied_snapshot is not None and canonical(supplied_snapshot) != canonical(snapshot):
            raise PolicyError("MARKDOWN_DERIVATIVE_MISMATCH")
        request_sha = digest(dict(scope=scope, version=version, hashes=expected_hashes, created_at=created_at,
                                  names={k: p.name for k, p in paths.items()}, expected_head_version=expected_head_version))
        with self.connection(write=True) as db:
            prior = db.execute("SELECT * FROM policy_bundles WHERE scope=? AND request_id=?", (scope, request_id)).fetchone()
            if prior:
                if prior["request_sha"] != request_sha:
                    raise PolicyError("REQUEST_ID_CONFLICT")
                return self._bundle(db, scope, prior["bundle_id"])
            head = db.execute("SELECT coalesce(max(version),0) FROM policy_bundles WHERE scope=?", (scope,)).fetchone()[0]
            if head != expected_head_version or version != head + 1:
                raise PolicyError("POLICY_VERSION_CAS_CONFLICT")
            bundle_id = "policy-" + digest(dict(scope=scope, version=version, request_sha=request_sha))[:24]
            value = dict(bundle_id=bundle_id, bundle_version=version, scope=scope, source_hashes=expected_hashes,
                         source_files={k: p.name for k, p in paths.items()}, structured_sha256=digest(snapshot),
                         snapshot=snapshot.model_dump(mode="json"), created_at=created_at,
                         content_approved_on=snapshot.content_approved_on, content_status="APPROVED_CONTENT",
                         deployment_status="NOT_DEPLOYED", effective_at=None, predecessor_version=head or None)
            db.execute("INSERT INTO policy_bundles VALUES(?,?,?,?,?,?,?,?,?)", (scope, version, bundle_id, request_id,
                request_sha, canonical(value), digest(value), raw["philosophy"], raw["policy"]))
            self._append(db, scope, bundle_id, "DRAFT", request_id, created_at, None)
            return value

    def _append(self, db, scope, bundle_id, state, request_id, at, approval_ref):
        seq = db.execute("SELECT coalesce(max(sequence),0)+1 FROM policy_events WHERE scope=?", (scope,)).fetchone()[0]
        value = dict(scope=scope, bundle_id=bundle_id, state=state, request_id=request_id, recorded_at=at,
                     approval_ref=approval_ref, sequence=seq)
        db.execute("INSERT INTO policy_events VALUES(?,?,?,?,?,?,?)", (scope, seq, bundle_id, state, request_id, canonical(value), digest(value)))
        return value

    def transition(self, *, scope, bundle_id, state, expected_state, request_id, at, approval_ref=None):
        self._identity(scope, request_id)
        clock(at)
        if (expected_state, state) not in {("DRAFT", "VALIDATED"), ("VALIDATED", "APPROVED")}:
            raise PolicyError("P1_ACTIVATION_FORBIDDEN_OR_INVALID_TRANSITION")
        if state == "APPROVED" and not (isinstance(approval_ref, str) and approval_ref.strip()):
            raise PolicyError("STRUCTURED_APPROVAL_REFERENCE_REQUIRED")
        with self.connection(write=True) as db:
            value = self._bundle(db, scope, bundle_id)
            if clock(at) < clock(value["created_at"]):
                raise PolicyError("LIFECYCLE_CLOCK_REVERSED")
            prior = db.execute("SELECT * FROM policy_events WHERE scope=? AND request_id=?", (scope, request_id)).fetchone()
            if prior:
                old = self._read(prior)
                if any(old[k] != v for k, v in dict(bundle_id=bundle_id, state=state, recorded_at=at, approval_ref=approval_ref).items()):
                    raise PolicyError("REQUEST_ID_CONFLICT")
                return old
            row = db.execute("SELECT * FROM policy_events WHERE scope=? AND bundle_id=? ORDER BY sequence DESC LIMIT 1", (scope, bundle_id)).fetchone()
            old = self._read(row)
            if old["state"] != expected_state:
                raise PolicyError("LIFECYCLE_CAS_CONFLICT")
            if clock(at) < clock(old["recorded_at"]):
                raise PolicyError("LIFECYCLE_CLOCK_REVERSED")
            return self._append(db, scope, bundle_id, state, request_id, at, approval_ref)

    def read_bundle(self, scope, bundle_id):
        with self.connection() as db:
            return self._bundle(db, scope, bundle_id)

    def history(self, scope):
        with self.connection() as db:
            return [self._read(row) for row in db.execute("SELECT * FROM policy_events WHERE scope=? ORDER BY sequence", (scope,))]

    def release_history(self, scope):
        """Activation/supersession/rollback timeline, read only; empty in P1."""
        with self.connection() as db:
            return [self._read(row) for row in db.execute("SELECT * FROM policy_releases WHERE scope=? ORDER BY sequence", (scope,))]
