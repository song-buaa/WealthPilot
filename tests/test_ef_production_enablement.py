"""Fenced production-mode contracts over TEST_ONLY databases, with no external calls."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import sqlite3

import pytest

from app import asset_views
from backend.services.investment_policy import runtime
from backend.services.investment_policy.contracts import digest
from backend.services.investment_thesis import cli
from backend.services.investment_thesis.portfolio_store import PortfolioStore, require_schema
from backend.services.investment_thesis.store import ThesisStore
from pocs.poc03_mac_mini import snapshot
from tests.test_e_portfolio_governance import (  # noqa: F401
    active_v2, b3, client, clock, confirm, isolated, item, rehearsal, source,
)
from tests.test_investment_thesis import record_review, saved_pair


PREFIX = "/api/allocation/governance"


def test_production_sources_and_confirmation(isolated, client, monkeypatch):
    store, view, db, policy, _, source, scenario = isolated
    monkeypatch.setenv("WEALTHPILOT_E1_PORTFOLIO_MODE", "PRODUCTION")
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    monkeypatch.setenv("WEALTHPILOT_E1_PORTFOLIO_ACCESS", "CONFIRM")
    assert client.get(PREFIX).status_code == 404  # General Web app has no private contract.
    client.app.state.portfolio_governance_scope = 7
    assert client.post(PREFIX + "/risk-preview", json=source).json()["detail"] == "E1_PORTFOLIO_SOURCE_SCOPE_MISMATCH"

    from app import shared_sqlite
    monkeypatch.setattr(shared_sqlite, "verify", lambda path, contract: {"portfolio_id": 7})
    monkeypatch.setenv("WEALTHPILOT_SQLITE_CONTRACT", "test-fixture-contract")
    assert client.post(PREFIX + "/risk-preview", json=source).json()["detail"] == "E1_PORTFOLIO_PRODUCTION_EVIDENCE_REQUIRED"
    view["views"].setdefault("get_wealth_overview", {})["synthetic"] = False
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE wealth_snapshots SET payload_json=?", (asset_views.dumps(view),))
    source["snapshot_sha256"] = scenario["snapshot_sha256"] = digest(view)
    assert client.post(PREFIX + "/risk-preview", json=source).json()["detail"] == "E1_PORTFOLIO_PRODUCTION_EVIDENCE_REQUIRED"

    original_resolve = runtime.resolve

    def production_release(portfolio_id):
        value = original_resolve(portfolio_id)
        value["release"] = {**value["release"], "synthetic": False, "environment": "PRODUCTION"}
        return value

    monkeypatch.setattr(runtime, "resolve", production_release)
    candidate = client.post(PREFIX + "/risk-preview", json=source)
    assert candidate.status_code == 200, candidate.text
    assert candidate.json()["qualification"]["status"] == "QUALIFIED"
    assert client.post(PREFIX + "/risk-preview", json={**source, "snapshot_sha256": "f" * 64}).json()["detail"] == (
        "E1_PORTFOLIO_SNAPSHOT_CHANGED")
    assert client.post(PREFIX + "/risk-preview", json={**source, "policy_bundle_sha256": "f" * 64}).json()["detail"] == (
        "E1_PORTFOLIO_POLICY_CHANGED")
    data = {**source, "bundle_id": "test-plan", "request_id": "test-write", "expected_revision": 0,
            "scenario": scenario, "selected_path": "A",
            "items": [item("plan", "PLANNED_TRANSITION_HOLD", stage_index=1)]}
    before = store.path.read_bytes(), db.read_bytes(), policy.read_bytes()
    monkeypatch.setenv("WEALTHPILOT_E1_PORTFOLIO_ACCESS", "READ")
    assert client.get(PREFIX).json()["capabilities"] == ["read"]
    assert client.post(PREFIX + "/preview", json=data).status_code == 403
    monkeypatch.setenv("WEALTHPILOT_E1_PORTFOLIO_ACCESS", "PREVIEW")
    reply = client.post(PREFIX + "/preview", json=data)
    assert reply.status_code == 200, reply.text
    preview = reply.json()
    request = confirm(preview, "plan")
    assert client.post(PREFIX + "/commit", json=request).status_code == 403
    monkeypatch.setenv("WEALTHPILOT_E1_PORTFOLIO_ACCESS", "CONFIRM")
    assert client.post(PREFIX + "/commit", json=request).status_code == 403
    assert client.post(PREFIX + "/commit", json=request, headers={"origin": "http://other"}).status_code == 403
    local = {"origin": "http://testserver"}
    assert client.post(PREFIX + "/commit", json={**request, "confirmed": False}, headers=local).status_code == 409
    assert before == (store.path.read_bytes(), db.read_bytes(), policy.read_bytes())
    reply = client.post(PREFIX + "/commit", json=request, headers=local)
    assert reply.status_code == 200, reply.text
    assert reply.json()["saved"] and not reply.json()["execution_authorized"]
    assert before[1:] == (db.read_bytes(), policy.read_bytes())

    from backend.graph.tools import call_tool
    assert call_tool("get_portfolio_review_followups") == {
        key: value for key, value in client.get(PREFIX).json().items() if key != "capabilities"
    }
    latest = deepcopy(view)
    after_decision = datetime.fromisoformat(reply.json()["record"]["recorded_at"]) + timedelta(minutes=1)
    from backend.services.investment_policy import store as policy_clock

    monkeypatch.setattr(policy_clock, "utc_now", after_decision.isoformat)
    monkeypatch.setattr(runtime, "utc_now", after_decision.isoformat)
    latest.update(snapshot_id="asset-view:v1:2", captured_at=after_decision.isoformat())
    latest["data_quality"].pop("investable_owner_evidence", None)
    latest["data_quality"].pop("investable_scope_evidence", None)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO wealth_snapshots VALUES (2, 7, 'TEST_ONLY', ?)", (asset_views.dumps(latest),))
    reconciliation = {"snapshot_id": latest["snapshot_id"], "snapshot_sha256": digest(latest)}

    result = client.post(PREFIX + "/reconcile", json=reconciliation)
    assert result.status_code == 200, result.text
    assert result.json()["current_investable_denominator_cny"] is None
    assert result.json()["execution_authorized"] is False
    assert call_tool("get_portfolio_review_followups", reconciliation=reconciliation) == result.json()


def test_explicit_migration_and_verified_restore_preserve_original_e1(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("WEALTHPILOT_E1_PORTFOLIO_MODE", raising=False)
    store = ThesisStore(tmp_path / "private" / "e1.sqlite", 7)
    store.init()
    from tests.test_investment_thesis import sample
    saved_pair(store)
    original = store.get("synthetic-meituan")
    review = sample("meituan-review")
    review.update(action_intent="HOLD", requested_verdict="WATCH",
                  not_run_reason="TEST_ONLY manual review")
    record_review(store, review, "old-review")
    reviews = store.reviews(original["thesis_id"])
    assert reviews
    backup = tmp_path / "backups" / "e1-before.sqlite"
    args = ["--store", str(store.path), "--portfolio-id", "7", "migrate-portfolio-production", "--backup", str(backup)]
    with store.connection() as db:
        with pytest.raises(ValueError, match="E1_PORTFOLIO_SCHEMA_REQUIRED"):
            require_schema(db)
    with pytest.raises(SystemExit):
        cli.main(args)
    assert not backup.exists()
    assert cli.main([*args, "--services-stopped"]) == 0
    assert '"migration_applied":true' in capsys.readouterr().out
    assert PortfolioStore(store).query()["records"] == []
    assert store.get(original["thesis_id"]) == original
    assert cli.main([*args, "--services-stopped"]) == 2  # Existing backup cannot be overwritten.
    manifest = json.loads(backup.with_suffix(".sqlite.manifest.json").read_text())
    restored = store.path.parent / "restored.sqlite"
    snapshot.restore(backup, restored, manifest, services_stopped=True)
    restored.chmod(0o600)  # E1 writer mode, after verifying the original restore.
    prior = ThesisStore(restored, 7)
    assert prior.get(original["thesis_id"]) == original
    assert prior.reviews(original["thesis_id"]) == store.reviews(original["thesis_id"]) == reviews
    with prior.connection() as db:
        with pytest.raises(ValueError, match="E1_PORTFOLIO_SCHEMA_REQUIRED"):
            require_schema(db)
