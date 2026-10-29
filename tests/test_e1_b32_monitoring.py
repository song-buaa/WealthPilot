"""B3-2 uses frozen inputs and never starts providers, notifications or orders."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import socket

import pytest

from backend.services.investment_thesis.monitoring import ReviewMonitorStore, migrate_monitoring
from backend.services.investment_thesis.monitoring_cli import main as cli_main
from backend.services.investment_thesis.service import record_review
from backend.services.investment_thesis.store import ThesisStore


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "docs/v4.3/examples/e1"
EVENT_FIXTURE = ROOT / "tests/fixtures/e1_b32/pdd_sec_q2_event.json"
SEC_FIXTURE = ROOT / "tests/fixtures/pdd_sec_q2_2026/0001104659-26-100534.txt.gz"


def sample(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text())


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("WEALTHPILOT_STAGE_TARGETS_PATH", raising=False)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: pytest.fail("network forbidden"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("network forbidden"))


@pytest.fixture
def setup(tmp_path):
    store = ThesisStore(tmp_path / "private" / "research.db", 7)
    store.init()
    thesis = sample("new-case-v1")
    thesis.update(thesis_id="e1-b32-test-pdd", event="E1-B3-2 TEST ONLY: frozen PDD filing review fixture")
    thesis["security"] = dict(symbol="PDD:US", currency="USD",
        identity_reference="https://www.sec.gov/Archives/edgar/data/1737806/", identity_gap=None)
    store.save(thesis, 0, "b32-thesis-v1")
    review = sample("meituan-review")
    review.update(thesis_id=thesis["thesis_id"], thesis_revision=1,
        review_as_of="2026-10-29T00:00:00+00:00", action_intent="RESEARCH",
        requested_verdict="UNKNOWN", rationale="E1-B3-2 TEST ONLY; no new facts",
        evidence_ids=["support"], price_evidence_ids=[], not_run_reason="No PEER run for monitoring test",
        next_review_at="2026-11-01T09:00:00+08:00",
        next_review_event="Next eligible official financial filing for the evidenced issuer")
    saved_review = record_review(store, review, "b32-review-v1")
    before = dict(history=deepcopy(store.history(thesis["thesis_id"])),
                  reviews=deepcopy(store.reviews(thesis["thesis_id"])))
    migrate_monitoring(store)
    monitor = ReviewMonitorStore(store)
    registration = dict(monitor_id="b32-pdd-monitor", thesis_id=thesis["thesis_id"], thesis_revision=1,
        source_review_id=saved_review["review_id"], issuer_id="PDD_HOLDINGS",
        event_types=["OFFICIAL_FINANCIAL_FILING"], enabled_at="2026-10-29T00:05:00+00:00",
        reason="Explicit TEST ONLY opt-in for due date and official filing review")
    enabled = monitor.enable(registration, "b32-enable")
    return store, monitor, registration, enabled, before


def assert_research_unchanged(store, before):
    assert store.history("e1-b32-test-pdd") == before["history"]
    assert store.reviews("e1-b32-test-pdd") == before["reviews"]


def test_due_scan_deduplicates_transitions_and_restart(setup):
    store, monitor, _, _, before = setup
    early = monitor.scan(as_of=datetime.fromisoformat("2026-11-01T00:59:59+00:00"))
    assert early["generated"] == [] and early["notification_status"] == "NOT_WIRED"
    due = monitor.scan(as_of=datetime.fromisoformat("2026-11-01T01:00:00+00:00"))
    assert len(due["generated"]) == 1 and due["generated"][0]["trigger_type"] == "DUE"
    task = due["generated"][0]
    assert task["state"] == "PENDING" and not task["research_record_saved"]
    again = ReviewMonitorStore(store).scan(as_of=datetime.fromisoformat("2026-11-01T02:00:00+00:00"))
    assert not again["generated"] and any(row["event_key"] == task["event_key"] for row in again["existing"])
    notified = monitor.transition(task["event_key"], dict(state="NOTIFIED",
        changed_at="2026-11-01T02:01:00+00:00", reason="local display only",
        delivery_channel="SIMULATED_LOCAL"), "b32-local-notified")
    assert notified["notification_status"] == "SIMULATED_LOCAL"
    assert monitor.transition(task["event_key"], dict(state="NOTIFIED",
        changed_at="2026-11-01T02:01:00+00:00", reason="local display only",
        delivery_channel="SIMULATED_LOCAL"), "b32-local-notified") == notified
    assert monitor.actionable(as_of=datetime.fromisoformat("2026-11-01T02:01:30+00:00")) == []
    handled = monitor.transition(task["event_key"], dict(state="HANDLED",
        changed_at="2026-11-01T02:02:00+00:00", reason="manual test completed"), "b32-handled")
    assert handled["state"] == "HANDLED" and monitor.actionable(
        as_of=datetime.fromisoformat("2026-11-02T00:00:00+00:00")) == []
    final = ReviewMonitorStore(store).scan(as_of=datetime.fromisoformat("2026-11-02T00:00:00+00:00"))
    assert not final["generated"]
    assert monitor.counts() == {"e1_monitor_events": 1, "e1_review_task_events": 3}
    assert_research_unchanged(store, before)


def test_frozen_official_event_identity_time_and_economic_dedup(setup):
    store, monitor, _, _, before = setup
    events = json.loads(EVENT_FIXTURE.read_text())
    raw = gzip.decompress(SEC_FIXTURE.read_bytes())
    assert hashlib.sha256(raw).hexdigest() == events[0]["source_sha256"]
    result = monitor.scan(as_of=datetime.fromisoformat("2026-10-29T01:00:00+00:00"), events=events)
    assert len(result["generated"]) == 1 and result["generated"][0]["trigger_type"] == "EVENT"
    assert result["generated"][0]["trigger"]["known_at"] == "2026-08-25T07:20:02-04:00"
    duplicate = deepcopy(events[0]); duplicate["event_id"] += ".mirror"
    same = ReviewMonitorStore(store).scan(as_of=datetime.fromisoformat("2026-10-29T01:00:00+00:00"),
                                          events=[duplicate])
    assert not same["generated"] and len(same["existing"]) == 1
    mismatch = deepcopy(events[0]); mismatch.update(event_id="mismatch", symbol="LI:US")
    future = deepcopy(events[0]); future.update(event_id="future",
        known_at="2026-11-30T00:00:00+00:00", fetched_at="2026-11-30T00:01:00+00:00")
    invalid = deepcopy(events[0]); invalid.pop("source_sha256")
    unofficial = deepcopy(events[0]); unofficial.update(event_id="unofficial",
        source_ref="https://example.com/unverified-event")
    rejected = monitor.scan(as_of=datetime.fromisoformat("2026-10-29T01:00:00+00:00"),
                            events=[mismatch, future, invalid, unofficial])
    assert {row["reason"] for row in rejected["rejected_events"]} == {
        "EVENT_IDENTITY_MISMATCH", "EVENT_FUTURE_AT_SCAN_TIME", "EVENT_EVIDENCE_INVALID"}
    assert_research_unchanged(store, before)


def test_cancel_version_advance_scope_time_and_defer(setup):
    store, monitor, registration, enabled, before = setup
    assert monitor.enable(registration, "b32-enable") == enabled
    with pytest.raises(ValueError, match="AS_OF_REQUIRED"):
        monitor.scan(as_of=datetime(2026, 11, 1))
    due = monitor.scan(as_of=datetime.fromisoformat("2026-11-01T01:00:00+00:00"))["generated"][0]
    deferred = monitor.transition(due["event_key"], dict(state="DEFERRED",
        changed_at="2026-11-01T01:01:00+00:00", reason="wait for next evidence",
        defer_until="2026-11-03T00:00:00+00:00"), "b32-defer")
    assert deferred["state"] == "DEFERRED"
    assert monitor.actionable(as_of=datetime.fromisoformat("2026-11-02T00:00:00+00:00")) == []
    assert monitor.actionable(as_of=datetime.fromisoformat("2026-11-03T00:00:00+00:00"))[0]["event_key"] == due["event_key"]
    cancelled = monitor.cancel("b32-pdd-monitor", changed_at=datetime.fromisoformat("2026-11-03T00:01:00+00:00"),
                               reason="explicit opt-out", request_id="b32-cancel")
    assert cancelled["state"] == "CANCELLED"
    assert monitor.cancel("b32-pdd-monitor", changed_at=datetime.fromisoformat("2026-11-03T00:01:00+00:00"),
                          reason="explicit opt-out", request_id="b32-cancel") == cancelled
    assert any(row["reason"] == "MONITOR_CANCELLED" for row in monitor.scan(
        as_of=datetime.fromisoformat("2026-11-04T00:00:00+00:00"))["skipped"])
    assert ReviewMonitorStore(ThesisStore(store.path, 8)).scan(
        as_of=datetime.fromisoformat("2026-11-04T00:00:00+00:00"))["generated"] == []
    assert_research_unchanged(store, before)


def test_revision_advance_requires_new_opt_in(setup):
    store, monitor, _, _, _ = setup
    v2 = deepcopy(store.get("e1-b32-test-pdd")["content"])
    v2["user_reflection"] = "new version requires a new review and explicit monitor opt-in"
    store.save(v2, 1, "b32-thesis-v2")
    result = monitor.scan(as_of=datetime.fromisoformat("2026-11-02T00:00:00+00:00"),
                          events=json.loads(EVENT_FIXTURE.read_text()))
    assert result["generated"] == []
    assert result["skipped"] == [{"monitor_id": "b32-pdd-monitor",
                                   "reason": "THESIS_REVISION_ADVANCED_REOPT_IN_REQUIRED"}]


def test_closed_thesis_cannot_be_opted_in(setup):
    store, monitor, registration, _, _ = setup
    closed = deepcopy(store.get("e1-b32-test-pdd")["content"])
    closed.update(status="CLOSED", user_reflection="monitoring explicitly ended")
    store.save(closed, 1, "b32-closed-v2")
    review = sample("meituan-review")
    review.update(thesis_id="e1-b32-test-pdd", thesis_revision=2,
        review_as_of="2026-10-30T00:00:00+00:00", action_intent="RESEARCH",
        requested_verdict="UNKNOWN", rationale="closed thesis review", evidence_ids=["support"],
        price_evidence_ids=[], not_run_reason="No analysis for closed thesis",
        next_review_at="2026-11-10T00:00:00+00:00", next_review_event=None)
    saved = record_review(store, review, "b32-closed-review")
    registration.update(monitor_id="b32-closed-monitor", thesis_revision=2,
                        source_review_id=saved["review_id"], event_types=[])
    with pytest.raises(ValueError, match="ACTIVE_THESIS_REQUIRED"):
        monitor.enable(registration, "b32-enable-closed")


def test_explicit_migration_and_cli_do_not_install_scheduler(tmp_path, capsys):
    store = ThesisStore(tmp_path / "private" / "research.db", 7); store.init()
    args = ["--store", str(store.path), "--portfolio-id", "7"]
    assert cli_main(args + ["init"]) == 0
    assert json.loads(capsys.readouterr().out)["monitoring_initialized"] is True
    assert cli_main(args + ["list", "--as-of", "2026-10-29T00:00:00+00:00"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM theses").fetchone()[0] == 0
