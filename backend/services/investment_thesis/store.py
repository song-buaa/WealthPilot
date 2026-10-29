"""Explicit private SQLite research store; no imports of business DB or providers."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

from .contracts import ThesisDraft, canonical, now, evidence_qualification

APP_ID = 0x45314231
SCHEMA = """
CREATE TABLE theses (
 portfolio_id INTEGER NOT NULL, thesis_id TEXT NOT NULL, head_revision INTEGER NOT NULL,
 PRIMARY KEY(portfolio_id, thesis_id));
CREATE TABLE thesis_versions (
 portfolio_id INTEGER NOT NULL, thesis_id TEXT NOT NULL, revision INTEGER NOT NULL,
 request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(portfolio_id, thesis_id, revision), UNIQUE(portfolio_id, request_id));
CREATE TABLE decision_reviews (
 portfolio_id INTEGER NOT NULL, review_id TEXT NOT NULL, event_index INTEGER NOT NULL,
 thesis_id TEXT NOT NULL, revision INTEGER NOT NULL, request_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(portfolio_id, review_id, event_index), UNIQUE(portfolio_id, request_id, event_index),
 FOREIGN KEY(portfolio_id, thesis_id, revision) REFERENCES thesis_versions(portfolio_id, thesis_id, revision));
CREATE TRIGGER thesis_versions_no_update BEFORE UPDATE ON thesis_versions BEGIN SELECT RAISE(ABORT,'E1_IMMUTABLE'); END;
CREATE TRIGGER thesis_versions_no_delete BEFORE DELETE ON thesis_versions BEGIN SELECT RAISE(ABORT,'E1_IMMUTABLE'); END;
CREATE TRIGGER reviews_no_update BEFORE UPDATE ON decision_reviews BEGIN SELECT RAISE(ABORT,'E1_IMMUTABLE'); END;
CREATE TRIGGER reviews_no_delete BEFORE DELETE ON decision_reviews BEGIN SELECT RAISE(ABORT,'E1_IMMUTABLE'); END;
"""


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def version_diff(old, new):
    """Structural diff, keyed claim/evidence lists; no generated narrative."""
    result = {}
    for key in sorted(set(old) | set(new)):
        if key in {"claims", "evidence"}:
            identity = "claim_id" if key == "claims" else "evidence_id"
            a = {i[identity]: i for i in old.get(key, [])}
            b = {i[identity]: i for i in new.get(key, [])}
            if a != b:
                result[key] = dict(added=[b[i] for i in sorted(b.keys()-a.keys())],
                    removed=[a[i] for i in sorted(a.keys()-b.keys())],
                    changed=[dict(id=i, before=a[i], after=b[i]) for i in sorted(a.keys() & b.keys()) if a[i] != b[i]])
        elif old.get(key) != new.get(key):
            result[key] = dict(before=old.get(key), after=new.get(key))
    return result


class ThesisStore:
    def __init__(self, path, portfolio_id):
        if not path or isinstance(portfolio_id, bool) or not isinstance(portfolio_id, int) or portfolio_id <= 0:
            raise ValueError("E1_EXPLICIT_STORE_AND_PORTFOLIO_REQUIRED")
        self.path = Path(os.path.abspath(os.path.expanduser(path)))
        self.portfolio_id = portfolio_id
        self._check_path()

    def _check_path(self):
        for p in (self.path, *self.path.parents):
            if p.is_symlink():
                raise ValueError("E1_SYMLINK_REJECTED")
        # Do not open or modify the business DB; compare path/identity only.
        defaults = [Path(__file__).resolve().parents[3] / "data" / "wealthpilot.db"]
        if os.environ.get("WEALTHPILOT_DB_PATH"):
            defaults.append(Path(os.environ["WEALTHPILOT_DB_PATH"]).absolute())
        for p in defaults:
            if self.path == p or (self.path.exists() and p.exists() and os.path.samefile(self.path, p)):
                raise ValueError("E1_BUSINESS_DB_FORBIDDEN")

    def _private(self, path, mode, *, directory=False):
        info = path.lstat()
        correct = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        if not correct or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != mode:
            raise ValueError("E1_PRIVATE_PATH_REQUIRED")

    def init(self):
        self._check_path()
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        self._private(self.path.parent, 0o700, directory=True)
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with sqlite3.connect(self.path) as db:
            db.executescript(SCHEMA)
            db.execute(f"PRAGMA application_id={APP_ID}")
            db.execute("PRAGMA user_version=1")
        return {"schema_version": "wealthpilot.4.3.e1.v1", "initialized": True}

    @contextmanager
    def connection(self, *, write=False):
        self._check_path()
        self._private(self.path.parent, 0o700, directory=True)
        self._private(self.path, 0o600)
        uri = self.path.as_uri() + ("?mode=rw" if write else "?mode=ro")
        db = sqlite3.connect(uri, uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            if (db.execute("PRAGMA application_id").fetchone()[0] != APP_ID
                    or db.execute("PRAGMA user_version").fetchone()[0] not in {1, 2}):
                raise ValueError("E1_STORE_SCHEMA_INVALID")
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("PRAGMA query_only=ON")
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
            raise ValueError("E1_RECORD_NOT_FOUND")
        value = json.loads(row["payload"])
        if digest(value) != row["sha256"]:
            raise ValueError("E1_RECORD_HASH_MISMATCH")
        return {**value, "sha256": row["sha256"]}

    def _request(self, db, request_id, fingerprint, kind):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("E1_REQUEST_ID_REQUIRED")
        for table in ("thesis_versions", "decision_reviews"):
            row = db.execute(f"SELECT * FROM {table} WHERE portfolio_id=? AND request_id=? ORDER BY rowid DESC LIMIT 1",
                             (self.portfolio_id, request_id)).fetchone()
            if row:
                if table != kind or row["fingerprint"] != fingerprint:
                    raise ValueError("E1_REQUEST_ID_CONFLICT")
                return self._read(row)
        return None

    def save(self, raw, expected_revision, request_id, *, _db=None):
        draft = ThesisDraft.model_validate(raw)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("E1_EXPECTED_REVISION_REQUIRED")
        content = draft.model_dump(mode="json")
        fingerprint = digest(dict(content=content, expected_revision=expected_revision))
        with (nullcontext(_db) if _db is not None else self.connection(write=True)) as db:
            prior = self._request(db, request_id, fingerprint, "thesis_versions")
            if prior:
                return prior
            row = db.execute("SELECT head_revision FROM theses WHERE portfolio_id=? AND thesis_id=?",
                             (self.portfolio_id, draft.thesis_id)).fetchone()
            head = row[0] if row else 0
            if head != expected_revision:
                raise ValueError("E1_REVISION_CONFLICT")
            previous = self._version(db, draft.thesis_id, head) if head else None
            first = self._version(db, draft.thesis_id, 1) if head else None
            if first and any(first["content"][k] != content[k] for k in ("user_original_judgment", "original_judgment_kind")):
                raise ValueError("E1_ORIGINAL_JUDGMENT_IMMUTABLE_USE_REFLECTION")
            recorded = now().isoformat()
            payload = dict(thesis_id=draft.thesis_id, portfolio_id=self.portfolio_id, revision=head+1,
                parent_revision=head or None, created_at=first["created_at"] if first else recorded,
                recorded_at=recorded, content=content,
                diff=version_diff(previous["content"] if previous else {}, content),
                evidence_qualification=[evidence_qualification(e, draft.decision_as_of) for e in draft.evidence] if draft.decision_as_of else [],
                current_qualification="NOT_RECHECKED", execution_authorized=False)
            db.execute("INSERT INTO thesis_versions VALUES(?,?,?,?,?,?,?)",
                (self.portfolio_id, draft.thesis_id, head+1, request_id, fingerprint, canonical(payload), digest(payload)))
            if row:
                db.execute("UPDATE theses SET head_revision=? WHERE portfolio_id=? AND thesis_id=? AND head_revision=?",
                           (head+1, self.portfolio_id, draft.thesis_id, head))
            else:
                db.execute("INSERT INTO theses VALUES(?,?,?)", (self.portfolio_id, draft.thesis_id, 1))
            return {**payload, "sha256": digest(payload)}

    def _version(self, db, thesis_id, revision):
        if revision is None:
            row = db.execute("SELECT head_revision FROM theses WHERE portfolio_id=? AND thesis_id=?",
                             (self.portfolio_id, thesis_id)).fetchone()
            if not row:
                raise ValueError("E1_RECORD_NOT_FOUND")
            revision = row[0]
        return self._read(db.execute("SELECT * FROM thesis_versions WHERE portfolio_id=? AND thesis_id=? AND revision=?",
                                    (self.portfolio_id, thesis_id, revision)).fetchone())

    def get(self, thesis_id, revision=None):
        with self.connection() as db:
            return self._version(db, thesis_id, revision)

    def by_symbol(self, symbol):
        with self.connection() as db:
            rows = db.execute("SELECT v.* FROM thesis_versions v JOIN theses t USING(portfolio_id,thesis_id) "
                "WHERE v.portfolio_id=? AND v.revision=t.head_revision ORDER BY v.rowid DESC", (self.portfolio_id,))
            return [value for row in rows if (value := self._read(row))["content"]["security"]["symbol"] == symbol]

    def history(self, thesis_id):
        with self.connection() as db:
            self._version(db, thesis_id, None)
            return [self._read(r) for r in db.execute("SELECT * FROM thesis_versions WHERE portfolio_id=? AND thesis_id=? ORDER BY revision",
                                                    (self.portfolio_id, thesis_id))]

    def begin_review(self, draft, fingerprint, request_id, *, _db=None):
        with (nullcontext(_db) if _db is not None else self.connection(write=True)) as db:
            prior = self._request(db, request_id, fingerprint, "decision_reviews")
            if prior:
                return prior, False
            thesis = self._version(db, draft.thesis_id, draft.thesis_revision)
            payload = dict(review_id=uuid.uuid4().hex, thesis_id=draft.thesis_id, thesis_revision=draft.thesis_revision,
                portfolio_id=self.portfolio_id, state="RUNNING", review=draft.model_dump(mode="json"),
                recorded_at=now().isoformat(), thesis_sha256=thesis["sha256"],
                execution_authorized=False, current_qualification="NOT_RECHECKED")
            self._insert_review(db, payload, request_id, fingerprint, 0)
            return payload, True

    def _insert_review(self, db, payload, request_id, fingerprint, index):
        db.execute("INSERT INTO decision_reviews VALUES(?,?,?,?,?,?,?,?,?)",
            (self.portfolio_id, payload["review_id"], index, payload["thesis_id"], payload["thesis_revision"],
             request_id, fingerprint, canonical(payload), digest(payload)))

    def finish_review(self, started, outcome, request_id, fingerprint, *, _db=None):
        with (nullcontext(_db) if _db is not None else self.connection(write=True)) as db:
            existing = db.execute("SELECT * FROM decision_reviews WHERE portfolio_id=? AND review_id=? ORDER BY event_index DESC LIMIT 1",
                                  (self.portfolio_id, started["review_id"])).fetchone()
            if not existing or existing["request_id"] != request_id or existing["fingerprint"] != fingerprint:
                raise ValueError("E1_REVIEW_CLAIM_MISMATCH")
            if existing["event_index"] != 0:
                return self._read(existing)
            payload = {**started, **outcome, "completed_at": now().isoformat()}
            self._insert_review(db, payload, request_id, fingerprint, 1)
            return {**payload, "sha256": digest(payload)}

    def reviews(self, thesis_id, review_id=None):
        with self.connection() as db:
            self._version(db, thesis_id, None)
            rows = db.execute("SELECT r.* FROM decision_reviews r WHERE r.portfolio_id=? AND r.thesis_id=? "
                "AND r.event_index=(SELECT MAX(event_index) FROM decision_reviews x WHERE x.portfolio_id=r.portfolio_id AND x.review_id=r.review_id) "
                "ORDER BY r.rowid DESC", (self.portfolio_id, thesis_id))
            values = [self._read(row) for row in rows if review_id is None or row["review_id"] == review_id]
            if review_id and not values:
                raise ValueError("E1_RECORD_NOT_FOUND")
            return values
