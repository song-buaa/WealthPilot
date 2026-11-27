"""Additive portfolio records on the original private E1 SQLite owner."""

from .contracts import canonical
from .store import digest

TABLES = ("portfolio_decision_revisions", "portfolio_risk_revisions")
SCHEMA = (
    """CREATE TABLE IF NOT EXISTS portfolio_decision_revisions (
        portfolio_id INTEGER NOT NULL, bundle_id TEXT NOT NULL, revision INTEGER NOT NULL,
        request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, review_due_at TEXT,
        payload TEXT NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY (portfolio_id, bundle_id, revision), UNIQUE (portfolio_id, request_id))""",
    """CREATE TABLE IF NOT EXISTS portfolio_risk_revisions (
        portfolio_id INTEGER NOT NULL, risk_key TEXT NOT NULL, revision INTEGER NOT NULL,
        bundle_id TEXT NOT NULL, bundle_revision INTEGER NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
        PRIMARY KEY (portfolio_id, risk_key, revision),
        FOREIGN KEY (portfolio_id, bundle_id, bundle_revision)
        REFERENCES portfolio_decision_revisions (portfolio_id, bundle_id, revision))""",
    """CREATE TRIGGER IF NOT EXISTS portfolio_decisions_no_replace BEFORE INSERT ON portfolio_decision_revisions
        WHEN EXISTS (SELECT 1 FROM portfolio_decision_revisions WHERE portfolio_id=NEW.portfolio_id
            AND ((bundle_id=NEW.bundle_id AND revision=NEW.revision) OR request_id=NEW.request_id))
        BEGIN SELECT RAISE(ABORT,'E1_IMMUTABLE'); END""",
    """CREATE TRIGGER IF NOT EXISTS portfolio_risks_no_replace BEFORE INSERT ON portfolio_risk_revisions
        WHEN EXISTS (SELECT 1 FROM portfolio_risk_revisions WHERE portfolio_id=NEW.portfolio_id
                    AND risk_key=NEW.risk_key AND revision=NEW.revision)
        BEGIN SELECT RAISE(ABORT,'E1_IMMUTABLE'); END""",
    """CREATE INDEX IF NOT EXISTS portfolio_decision_due
        ON portfolio_decision_revisions (portfolio_id, review_due_at)""",
)


def migrate_portfolio_governance(store, *, verify_before=None):
    """Explicit opt-in, additive DDL in an already-initialized private E1 store."""
    with store.connection(write=True) as db:
        if verify_before is not None:
            verify_before(db)
        for statement in SCHEMA:
            db.execute(statement)
        for table in TABLES:
            for operation in ("UPDATE", "DELETE"):
                db.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_no_{operation.lower()}
                    BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'E1_IMMUTABLE'); END""")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("E1_PORTFOLIO_SCHEMA_INTEGRITY")
    return {"schema_version": "wealthpilot.e1.portfolio.v1", "migration_applied": True}


def require_schema(db):
    count = db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN (?,?)", TABLES).fetchone()[0]
    if count != len(TABLES):
        raise ValueError("E1_PORTFOLIO_SCHEMA_REQUIRED")


class PortfolioStore:
    def __init__(self, thesis_store):
        self.thesis_store = thesis_store
        self.portfolio_id = thesis_store.portfolio_id

    def connection(self, *, write=False):
        return self.thesis_store.connection(write=write)

    def request(self, db, request_id):
        row = db.execute("SELECT * FROM portfolio_decision_revisions WHERE portfolio_id=? AND request_id=?",
                         (self.portfolio_id, request_id)).fetchone()
        return self.thesis_store._read(row) if row else None

    def head(self, db, bundle_id):
        row = db.execute("SELECT * FROM portfolio_decision_revisions WHERE portfolio_id=? AND bundle_id=? "
                         "ORDER BY revision DESC LIMIT 1", (self.portfolio_id, bundle_id)).fetchone()
        return self.thesis_store._read(row) if row else None

    def risk_heads(self, db):
        rows = db.execute("SELECT r.* FROM portfolio_risk_revisions r JOIN "
                          "(SELECT risk_key,MAX(revision) AS revision FROM portfolio_risk_revisions "
                          "WHERE portfolio_id=? GROUP BY risk_key) h USING(risk_key,revision) WHERE r.portfolio_id=?",
                          (self.portfolio_id, self.portfolio_id))
        return {r["risk_key"]: self.thesis_store._read(r) for r in rows}

    def research(self, db, link):
        if link.review_id:
            row = db.execute("SELECT * FROM decision_reviews WHERE portfolio_id=? AND thesis_id=? AND revision=? "
                             "AND review_id=? AND event_index=1", (self.portfolio_id, link.thesis_id,
                                                                link.revision, link.review_id)).fetchone()
            record = self.thesis_store._read(row)
            if record.get("state") != "COMPLETED":
                raise ValueError("E1_PORTFOLIO_RESEARCH_REVIEW_INCOMPLETE")
        else:
            record = self.thesis_store._version(db, link.thesis_id, link.revision)
        if record["sha256"] != link.sha256:
            raise ValueError("E1_PORTFOLIO_RESEARCH_HASH_MISMATCH")
        return record

    def insert(self, db, decision, events):
        payload = canonical(decision)
        if len(payload.encode()) > 24 * 1024 * 1024:
            raise ValueError("E1_PORTFOLIO_EVIDENCE_TOO_LARGE")
        db.execute("INSERT INTO portfolio_decision_revisions VALUES (?,?,?,?,?,?,?,?)",
                   (self.portfolio_id, decision["bundle_id"], decision["revision"], decision["request_id"],
                    decision["confirmation_receipt"]["preview_fingerprint"],
                    decision["review_due_at"] if decision["bundle_state"] != "REVOKED" else None,
                    payload, digest(decision)))
        for event in events:
            db.execute("INSERT INTO portfolio_risk_revisions VALUES (?,?,?,?,?,?,?)",
                       (self.portfolio_id, event["risk_key"], event["revision"], decision["bundle_id"],
                        decision["revision"], canonical(event), digest(event)))

    def query(self, *, bundle_id=None, risk_key=None, revision=None, limit=20, at=None, include_evidence=False):
        with self.connection() as db:
            require_schema(db)
            if risk_key:
                rows = db.execute("SELECT * FROM portfolio_risk_revisions WHERE portfolio_id=? AND risk_key=? "
                                  "ORDER BY revision DESC LIMIT ?", (self.portfolio_id, risk_key, limit))
                return {"records": [], "risk_events": [self.thesis_store._read(row) for row in rows],
                        "risk_followups": [], "followups": [], "execution_authorized": False}
            if bundle_id:
                sql = "SELECT * FROM portfolio_decision_revisions WHERE portfolio_id=? AND bundle_id=?"
                params = [self.portfolio_id, bundle_id]
                if revision is not None:
                    sql += " AND revision=?"
                    params.append(revision)
                rows = db.execute(sql + " ORDER BY revision DESC LIMIT ?", (*params, limit))
            else:
                rows = db.execute("SELECT v.* FROM portfolio_decision_revisions v JOIN "
                                  "(SELECT bundle_id,MAX(revision) AS revision FROM portfolio_decision_revisions "
                                  "WHERE portfolio_id=? GROUP BY bundle_id) h USING(bundle_id,revision) "
                                  "WHERE v.portfolio_id=? ORDER BY v.review_due_at,v.bundle_id LIMIT ?",
                                  (self.portfolio_id, self.portfolio_id, limit))
            records = [self.thesis_store._read(row) for row in rows]
            if not include_evidence:
                records = [{k: v for k, v in record.items() if k != "evidence_archive"} for record in records]
            risks = [] if bundle_id else list(self.risk_heads(db).values())
            due = []
            if at is not None and not bundle_id:
                rows = db.execute("SELECT v.* FROM portfolio_decision_revisions v JOIN "
                                  "(SELECT bundle_id,MAX(revision) AS revision FROM portfolio_decision_revisions "
                                  "WHERE portfolio_id=? GROUP BY bundle_id) h USING(bundle_id,revision) "
                                  "WHERE v.portfolio_id=? AND v.review_due_at<=? ORDER BY v.review_due_at,v.bundle_id LIMIT ?",
                                  (self.portfolio_id, self.portfolio_id, at, limit))
                fields = ("decision_bundle_ref", "decision_revision_ref", "base_snapshot_ref", "scenario_ref",
                          "expected_checkpoints", "original_policy_reference", "review_due_at", "evidence_requirements")
                due = [{key: record[key] for key in fields} for row in rows
                       if (record := self.thesis_store._read(row))]
            risk_items = sorted(risks, key=lambda item: (item["review_due_at"], item["risk_key"]))[:limit]
            risk_due = [item for item in risk_items if at is not None and item["review_due_at"] <= at
                        and item["state"] not in {"RESOLVED"}]
            return {"records": records, "risk_events": risk_items, "risk_followups": risk_due, "followups": due,
                    "execution_authorized": False}
