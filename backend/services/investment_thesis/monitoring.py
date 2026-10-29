"""Opt-in E1 review discovery with durable deduplication and no business actions.

This module only reads immutable E1 thesis/review records and writes append-only
monitor/task receipts in the same private database.  It never runs PEER, fetches
events, sends notifications, refreshes assets, or creates orders.
"""
from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import AwareDatetime, Field, model_validator

from .contracts import ID, Record, canonical
from .store import ThesisStore, digest


MONITORING_SCHEMA_VERSION = 2
MONITORING_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS e1_monitor_events (
       portfolio_id INTEGER NOT NULL, monitor_id TEXT NOT NULL, event_index INTEGER NOT NULL,
       request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
       PRIMARY KEY(portfolio_id, monitor_id, event_index), UNIQUE(portfolio_id, request_id));""",
    """CREATE TABLE IF NOT EXISTS e1_review_task_events (
       portfolio_id INTEGER NOT NULL, event_key TEXT NOT NULL, event_index INTEGER NOT NULL,
       request_id TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
       PRIMARY KEY(portfolio_id, event_key, event_index), UNIQUE(portfolio_id, request_id));""",
    """CREATE TRIGGER IF NOT EXISTS e1_monitor_events_no_update BEFORE UPDATE ON e1_monitor_events
       BEGIN SELECT RAISE(ABORT,'E1_IMMUTABLE'); END;""",
    """CREATE TRIGGER IF NOT EXISTS e1_monitor_events_no_delete BEFORE DELETE ON e1_monitor_events
       BEGIN SELECT RAISE(ABORT,'E1_IMMUTABLE'); END;""",
    """CREATE TRIGGER IF NOT EXISTS e1_review_task_events_no_update BEFORE UPDATE ON e1_review_task_events
       BEGIN SELECT RAISE(ABORT,'E1_IMMUTABLE'); END;""",
    """CREATE TRIGGER IF NOT EXISTS e1_review_task_events_no_delete BEFORE DELETE ON e1_review_task_events
       BEGIN SELECT RAISE(ABORT,'E1_IMMUTABLE'); END;""",
)


def migrate_monitoring(store: ThesisStore):
    """Explicit, local migration. Existing research rows are never rewritten."""
    with store.connection(write=True) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version not in {1, MONITORING_SCHEMA_VERSION}:
            raise ValueError("E1_STORE_SCHEMA_INVALID")
        for statement in MONITORING_SCHEMA:
            db.execute(statement)
        db.execute(f"PRAGMA user_version={MONITORING_SCHEMA_VERSION}")
    return {"schema_version": "wealthpilot.4.3.e1.v2", "monitoring_initialized": True}


EventType = Literal["OFFICIAL_FINANCIAL_FILING", "FORMAL_REGULATORY_EVENT"]


class MonitorRegistration(Record):
    monitor_id: str = Field(pattern=ID)
    thesis_id: str = Field(pattern=ID)
    thesis_revision: int = Field(gt=0, strict=True)
    source_review_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    issuer_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    event_types: list[EventType] = Field(default_factory=list, max_length=10)
    enabled_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=2000)


class FrozenDisclosureEvent(Record):
    event_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,160}$")
    economic_event_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,160}$")
    event_type: EventType
    symbol: str = Field(min_length=1, max_length=50)
    issuer_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,120}$")
    description: str = Field(min_length=1, max_length=4000)
    source_ref: str = Field(min_length=1, max_length=2000)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    published_at: AwareDatetime
    known_at: AwareDatetime
    fetched_at: AwareDatetime

    @model_validator(mode="after")
    def source_and_clock(self):
        parsed = urlsplit(self.source_ref)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or any(k.lower() in {"apikey", "api_key", "token", "access_token", "password"}
                       for k, _ in parse_qsl(parsed.query))):
            raise ValueError("E1_EVENT_SOURCE_INVALID")
        if parsed.hostname not in {"www.sec.gov", "data.sec.gov"}:
            raise ValueError("E1_EVENT_SOURCE_NOT_ELIGIBLE")
        if not self.published_at <= self.known_at <= self.fetched_at:
            raise ValueError("E1_EVENT_CLOCK_INVALID")
        return self


class TaskTransition(Record):
    state: Literal["NOTIFIED", "HANDLED", "DEFERRED"]
    changed_at: AwareDatetime
    reason: str = Field(min_length=1, max_length=2000)
    defer_until: AwareDatetime | None = None
    delivery_channel: Literal["SIMULATED_LOCAL", "NONE"] = "NONE"

    @model_validator(mode="after")
    def defer_contract(self):
        if (self.state == "DEFERRED") != (self.defer_until is not None):
            raise ValueError("E1_DEFER_UNTIL_REQUIRED")
        if self.state == "NOTIFIED" and self.delivery_channel != "SIMULATED_LOCAL":
            raise ValueError("E1_PRODUCTION_NOTIFICATION_NOT_WIRED")
        return self


def _read_payload(row):
    if row is None:
        return None
    value = json.loads(row["payload"])
    if digest(value) != row["sha256"]:
        raise ValueError("E1_MONITOR_RECORD_HASH_MISMATCH")
    return {**value, "sha256": row["sha256"]}


def _aware(value, code="E1_AS_OF_REQUIRED"):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(code)
    return value


class ReviewMonitorStore:
    """Append-only monitor/task state in an explicitly migrated E1 private store."""

    def __init__(self, store: ThesisStore):
        self.store = store

    def _schema(self, db):
        if db.execute("PRAGMA user_version").fetchone()[0] != MONITORING_SCHEMA_VERSION:
            raise ValueError("E1_MONITORING_NOT_INITIALIZED")

    def _latest_monitor(self, db, monitor_id):
        return _read_payload(db.execute(
            "SELECT * FROM e1_monitor_events WHERE portfolio_id=? AND monitor_id=? "
            "ORDER BY event_index DESC LIMIT 1", (self.store.portfolio_id, monitor_id)).fetchone())

    def _latest_task(self, db, event_key):
        return _read_payload(db.execute(
            "SELECT * FROM e1_review_task_events WHERE portfolio_id=? AND event_key=? "
            "ORDER BY event_index DESC LIMIT 1", (self.store.portfolio_id, event_key)).fetchone())

    @staticmethod
    def _request(db, portfolio_id, request_id, fingerprint, table):
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            raise ValueError("E1_REQUEST_ID_REQUIRED")
        for candidate in ("e1_monitor_events", "e1_review_task_events"):
            row = db.execute(f"SELECT * FROM {candidate} WHERE portfolio_id=? AND request_id=?",
                             (portfolio_id, request_id)).fetchone()
            if row:
                if candidate != table or row["fingerprint"] != fingerprint:
                    raise ValueError("E1_REQUEST_ID_CONFLICT")
                return _read_payload(row)
        return None

    def enable(self, raw, request_id):
        registration = MonitorRegistration.model_validate(raw)
        with self.store.connection(write=True) as db:
            self._schema(db)
            thesis = self.store._version(db, registration.thesis_id, registration.thesis_revision)
            head = db.execute("SELECT head_revision FROM theses WHERE portfolio_id=? AND thesis_id=?",
                              (self.store.portfolio_id, registration.thesis_id)).fetchone()[0]
            if head != registration.thesis_revision:
                raise ValueError("E1_MONITOR_CURRENT_REVISION_REQUIRED")
            if thesis["content"]["status"] not in {"ACTIVE", "WATCH"}:
                raise ValueError("E1_MONITOR_ACTIVE_THESIS_REQUIRED")
            row = db.execute("SELECT * FROM decision_reviews WHERE portfolio_id=? AND thesis_id=? "
                "AND review_id=? AND event_index=1", (self.store.portfolio_id, registration.thesis_id,
                                                       registration.source_review_id)).fetchone()
            review = _read_payload(row)
            if not review or review.get("state") != "COMPLETED" or review["thesis_revision"] != registration.thesis_revision:
                raise ValueError("E1_MONITOR_COMPLETED_REVIEW_REQUIRED")
            draft = review["review"]
            if not draft.get("next_review_at") and not (draft.get("next_review_event") and registration.event_types):
                raise ValueError("E1_MONITOR_TRIGGER_REQUIRED")
            if registration.event_types and not draft.get("next_review_event"):
                raise ValueError("E1_MONITOR_EVENT_CONDITION_REQUIRED")
            content = registration.model_dump(mode="json")
            payload = dict(schema_version="wealthpilot.4.3.e1.monitor.v1", state="ACTIVE",
                portfolio_id=self.store.portfolio_id, event_index=0, registration=content,
                due_at=draft.get("next_review_at"), event_condition=draft.get("next_review_event"),
                security=thesis["content"]["security"], thesis_sha256=thesis["sha256"],
                source_review_sha256=review["sha256"], recorded_at=registration.enabled_at.isoformat(),
                last_review_at=draft["review_as_of"],
                execution_authorized=False, notification_status="NOT_WIRED")
            fingerprint = digest({"operation": "ENABLE", "registration": content})
            prior = self._request(db, self.store.portfolio_id, request_id, fingerprint, "e1_monitor_events")
            if prior:
                return prior
            if self._latest_monitor(db, registration.monitor_id):
                raise ValueError("E1_MONITOR_ALREADY_EXISTS")
            db.execute("INSERT INTO e1_monitor_events VALUES(?,?,?,?,?,?,?)",
                (self.store.portfolio_id, registration.monitor_id, 0, request_id, fingerprint,
                 canonical(payload), digest(payload)))
            return {**payload, "sha256": digest(payload)}

    def cancel(self, monitor_id, *, changed_at, reason, request_id):
        _aware(changed_at)
        if (not isinstance(monitor_id, str) or not re.fullmatch(ID, monitor_id)
                or not isinstance(reason, str) or not 1 <= len(reason) <= 2000):
            raise ValueError("E1_MONITOR_CANCEL_INVALID")
        with self.store.connection(write=True) as db:
            self._schema(db)
            fingerprint = digest({"operation": "CANCEL", "monitor_id": monitor_id,
                                  "changed_at": changed_at.isoformat(), "reason": reason})
            prior = self._request(db, self.store.portfolio_id, request_id, fingerprint, "e1_monitor_events")
            if prior:
                return prior
            current = self._latest_monitor(db, monitor_id)
            if not current:
                raise ValueError("E1_MONITOR_NOT_FOUND")
            payload = {**current, "state": "CANCELLED", "event_index": current["event_index"] + 1,
                       "changed_at": changed_at.isoformat(), "change_reason": reason,
                       "execution_authorized": False, "notification_status": "NOT_WIRED"}
            payload.pop("sha256", None)
            db.execute("INSERT INTO e1_monitor_events VALUES(?,?,?,?,?,?,?)",
                (self.store.portfolio_id, monitor_id, payload["event_index"], request_id, fingerprint,
                 canonical(payload), digest(payload)))
            return {**payload, "sha256": digest(payload)}

    def _insert_task(self, db, task):
        current = self._latest_task(db, task["event_key"])
        if current:
            return current, False
        request_id = "e1-task-" + task["event_key"]
        fingerprint = digest(task)
        db.execute("INSERT INTO e1_review_task_events VALUES(?,?,?,?,?,?,?)",
            (self.store.portfolio_id, task["event_key"], 0, request_id, fingerprint,
             canonical(task), digest(task)))
        return {**task, "sha256": digest(task)}, True

    def scan(self, *, as_of, events=()):
        as_of = _aware(as_of)
        parsed, rejected = [], []
        for raw in events:
            try:
                parsed.append(FrozenDisclosureEvent.model_validate(raw))
            except Exception:
                event_id = raw.get("event_id") if isinstance(raw, dict) else None
                rejected.append({"event_id": event_id, "reason": "EVENT_EVIDENCE_INVALID"})
        generated, existing, skipped = [], [], []
        with self.store.connection(write=True) as db:
            self._schema(db)
            rows = db.execute("SELECT m.* FROM e1_monitor_events m WHERE m.portfolio_id=? AND m.event_index=("
                "SELECT MAX(x.event_index) FROM e1_monitor_events x WHERE x.portfolio_id=m.portfolio_id "
                "AND x.monitor_id=m.monitor_id) ORDER BY m.monitor_id", (self.store.portfolio_id,)).fetchall()
            policies = [_read_payload(row) for row in rows]
            for policy in policies:
                registration = policy["registration"]
                monitor_id = registration["monitor_id"]
                if policy["state"] != "ACTIVE":
                    skipped.append({"monitor_id": monitor_id, "reason": "MONITOR_CANCELLED"})
                    continue
                thesis = self.store._version(db, registration["thesis_id"], registration["thesis_revision"])
                head = db.execute("SELECT head_revision FROM theses WHERE portfolio_id=? AND thesis_id=?",
                                  (self.store.portfolio_id, registration["thesis_id"])).fetchone()[0]
                if head != registration["thesis_revision"]:
                    skipped.append({"monitor_id": monitor_id, "reason": "THESIS_REVISION_ADVANCED_REOPT_IN_REQUIRED"})
                    continue
                if thesis["content"]["status"] not in {"ACTIVE", "WATCH"}:
                    skipped.append({"monitor_id": monitor_id, "reason": "THESIS_NO_LONGER_ACTIVE"})
                    continue
                if policy.get("due_at"):
                    due_at = datetime.fromisoformat(policy["due_at"])
                    if due_at <= as_of:
                        event_key = digest({"thesis_id": registration["thesis_id"],
                            "revision": registration["thesis_revision"], "due_at": policy["due_at"],
                            "trigger_type": "DUE"})
                        task = self._task(policy, thesis, event_key, "DUE", as_of,
                            {"due_at": policy["due_at"], "reason": "NEXT_REVIEW_AT_REACHED"})
                        value, created = self._insert_task(db, task)
                        (generated if created else existing).append(value)
                eligible_for_policy = []
                for event in parsed:
                    if event.known_at > as_of or event.published_at > as_of or event.fetched_at > as_of:
                        rejected.append({"event_id": event.event_id, "monitor_id": monitor_id,
                                         "reason": "EVENT_FUTURE_AT_SCAN_TIME"})
                        continue
                    if (event.symbol != thesis["content"]["security"]["symbol"]
                            or event.issuer_id != registration["issuer_id"]):
                        rejected.append({"event_id": event.event_id, "monitor_id": monitor_id,
                                         "reason": "EVENT_IDENTITY_MISMATCH"})
                        continue
                    if event.event_type not in registration["event_types"]:
                        rejected.append({"event_id": event.event_id, "monitor_id": monitor_id,
                                         "reason": "EVENT_TYPE_NOT_OPTED_IN"})
                        continue
                    eligible_for_policy.append(event)
                    event_key = digest({"thesis_id": registration["thesis_id"],
                        "revision": registration["thesis_revision"],
                        "economic_event_id": event.economic_event_id, "trigger_type": "EVENT"})
                    task = self._task(policy, thesis, event_key, "EVENT", as_of,
                        {"event_id": event.event_id, "economic_event_id": event.economic_event_id,
                         "event_type": event.event_type, "description": event.description,
                         "source_ref": event.source_ref, "source_sha256": event.source_sha256,
                         "published_at": event.published_at.isoformat(), "known_at": event.known_at.isoformat(),
                         "fetched_at": event.fetched_at.isoformat()})
                    value, created = self._insert_task(db, task)
                    (generated if created else existing).append(value)
                if registration["event_types"] and not eligible_for_policy:
                    skipped.append({"monitor_id": monitor_id, "reason": "NO_ELIGIBLE_EVENT_SOURCE"})
        return dict(schema_version="wealthpilot.4.3.e1.scan.v1", as_of=as_of.isoformat(),
            generated=generated, existing=existing, skipped=skipped, rejected_events=rejected,
            notification_status="NOT_WIRED", analysis_run=False, research_records_written=0,
            execution_authorized=False)

    def _task(self, policy, thesis, event_key, trigger_type, as_of, trigger):
        registration = policy["registration"]
        return dict(schema_version="wealthpilot.4.3.e1.review-task.v1", event_key=event_key,
            event_index=0, state="PENDING", portfolio_id=self.store.portfolio_id,
            monitor_id=registration["monitor_id"], thesis_id=registration["thesis_id"],
            thesis_revision=registration["thesis_revision"], thesis_sha256=thesis["sha256"],
            symbol=thesis["content"]["security"]["symbol"], issuer_id=registration["issuer_id"],
            thesis_summary=thesis["content"]["event"], trigger_type=trigger_type, trigger=trigger,
            discovered_at=as_of.isoformat(), source_review_id=registration["source_review_id"],
            last_review_at=policy["last_review_at"],
            next_action="Read the immutable thesis/review; optionally run original PEER; preview a new E1 Review; wait for explicit human confirmation before save.",
            notification_status="NOT_WIRED", delivery_channel="NONE", execution_authorized=False,
            research_record_saved=False, order_created=False)

    def transition(self, event_key, raw, request_id):
        transition = TaskTransition.model_validate(raw)
        with self.store.connection(write=True) as db:
            self._schema(db)
            fingerprint = digest({"operation": "TASK_TRANSITION", "event_key": event_key,
                                  "transition": transition.model_dump(mode="json")})
            prior = self._request(db, self.store.portfolio_id, request_id, fingerprint,
                                  "e1_review_task_events")
            if prior:
                return prior
            current = self._latest_task(db, event_key)
            if not current:
                raise ValueError("E1_REVIEW_TASK_NOT_FOUND")
            if current["state"] == "HANDLED":
                raise ValueError("E1_REVIEW_TASK_ALREADY_HANDLED")
            payload = {**current, "event_index": current["event_index"] + 1,
                "state": transition.state, "changed_at": transition.changed_at.isoformat(),
                "state_reason": transition.reason,
                "defer_until": transition.defer_until.isoformat() if transition.defer_until else None,
                "delivery_channel": transition.delivery_channel,
                "notification_status": "SIMULATED_LOCAL" if transition.state == "NOTIFIED" else "NOT_WIRED",
                "execution_authorized": False, "research_record_saved": False, "order_created": False}
            payload.pop("sha256", None)
            db.execute("INSERT INTO e1_review_task_events VALUES(?,?,?,?,?,?,?)",
                (self.store.portfolio_id, event_key, payload["event_index"], request_id, fingerprint,
                 canonical(payload), digest(payload)))
            return {**payload, "sha256": digest(payload)}

    def actionable(self, *, as_of):
        as_of = _aware(as_of)
        with self.store.connection() as db:
            self._schema(db)
            rows = db.execute("SELECT t.* FROM e1_review_task_events t WHERE t.portfolio_id=? AND t.event_index=("
                "SELECT MAX(x.event_index) FROM e1_review_task_events x WHERE x.portfolio_id=t.portfolio_id "
                "AND x.event_key=t.event_key) ORDER BY t.event_key", (self.store.portfolio_id,)).fetchall()
            values = [_read_payload(row) for row in rows]
        return [v for v in values if v["state"] == "PENDING"
                or v["state"] == "DEFERRED" and datetime.fromisoformat(v["defer_until"]) <= as_of]

    def counts(self):
        with self.store.connection() as db:
            self._schema(db)
            return {table: db.execute(f"SELECT COUNT(*) FROM {table} WHERE portfolio_id=?",
                                      (self.store.portfolio_id,)).fetchone()[0]
                    for table in ("e1_monitor_events", "e1_review_task_events")}
