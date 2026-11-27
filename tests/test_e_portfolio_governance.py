"""E isolation acceptance with original B/C/D, ACTIVE and private E1 SQLite."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
import json
import hashlib
import sqlite3

import pytest

from backend.services.investment_policy.contracts import digest as policy_digest
from backend.services.investment_policy.runtime import current
from backend.services.investment_thesis.portfolio import PortfolioGovernance, _event_plan
from backend.services.investment_thesis.portfolio_contracts import DecisionItem
from backend.services.investment_thesis.portfolio_sources import read_portfolio_sources
from backend.services.investment_thesis.portfolio_store import PortfolioStore, migrate_portfolio_governance
from backend.services.investment_thesis.store import ThesisStore
from backend.services.wealth_service import freeze_investable_scope
from tests.test_allocation_snapshot_errors import client  # noqa: F401
from tests.test_b2_allocation import AT, owner_file, publish, source  # noqa: F401
from tests.test_b3_allocation import b3  # noqa: F401
from tests.test_investment_thesis import sample
from tests.test_policy_b3_versioned_scope import active_v2, clock, rehearsal  # noqa: F401


def item(ident, kind, **fields):
    return {"item_id": ident, "kind": kind, "reason": "TEST_ONLY human judgment",
            "review_due_at": (AT + timedelta(days=4)).isoformat(), "expires_at": (AT + timedelta(days=6)).isoformat(),
            "revoke_when": "If qualified evidence changes", **fields}


def confirm(preview, *selected):
    return {"request_id": preview["request_id"], "preview_token": preview["preview_token"],
            "preview_fingerprint": preview["preview_fingerprint"], "selected_item_ids": selected,
            "confirmed": True, "confirmation": "保存这次组合决定"}


@pytest.fixture
def isolated(tmp_path, monkeypatch, active_v2, b3):
    view, classes = b3
    # Real C computation and strategic two-node breach from a synthetic book.
    positions = view["data_quality"]["security_inputs"]
    positions[4].update(symbol="TSLA:US", market_value_cny="400")
    positions[6]["market_value_cny"] = "100"
    owner_file(view, tmp_path/"owner.json", monkeypatch, manual_classes=classes)
    freeze_investable_scope(view)
    db = tmp_path/"frozen.sqlite"
    publish(view, db, monkeypatch)
    from tests.test_c_concentration import holdings
    official = holdings("SPY:US", {"AAPL:US":".05"})
    manifest = tmp_path / "isolated-etf.json"
    body = json.dumps({"schema_version":"wealthpilot.etf-evidence.v1","portfolio_id":7,
                       "records":[official.model_dump(mode="json")]}).encode()
    manifest.write_bytes(body)
    manifest.chmod(0o600)
    monkeypatch.setenv("WEALTHPILOT_ETF_EVIDENCE_FILE", str(manifest))
    monkeypatch.setenv("WEALTHPILOT_ETF_EVIDENCE_SHA256", hashlib.sha256(body).hexdigest())
    store = ThesisStore(tmp_path/"research"/"e1.sqlite", 7)
    store.init()
    legacy = store.save(sample("meituan-v1"), 0, "legacy-1")
    assert migrate_portfolio_governance(store)["migration_applied"]
    assert migrate_portfolio_governance(store)["migration_applied"]
    monkeypatch.setenv("WEALTHPILOT_E1_PORTFOLIO_MODE", "ISOLATED")
    monkeypatch.setenv("WEALTHPILOT_E1_DB_PATH", str(store.path))
    from backend.api import portfolio_governance
    portfolio_governance._bridge.cache_clear()
    monkeypatch.setattr(portfolio_governance, "PortfolioGovernance",
        lambda store, source: PortfolioGovernance(store, source, wall_clock=lambda: AT))
    reference = current(7).context["policy_reference"]
    binding = dict(snapshot_id=view["snapshot_id"], snapshot_sha256=policy_digest(view),
                   policy_bundle_sha256=reference["bundle_sha256"])
    scenario = dict(**binding, paths={name: [{"label":"S1", "actions": [{
        "kind":"SELL_TO_CASH", "action_id":"sale", "position_ref":"position:5", "amount":"1", "fee":"0",
        "outcome":outcome, "assumption":"TEST_ONLY available or rejected"
    }]}] for name, outcome in (("A", "APPLIED"), ("B", "FAILED"))})
    return store, view, db, active_v2[0].path, legacy, binding, scenario


def test_real_http_selection_frozen_history_and_atomic_risk(isolated, client, monkeypatch):
    store, view, db, policy, legacy, binding, scenario = isolated
    prefix = "/api/allocation/governance"
    before = (db.read_bytes(), policy.read_bytes())
    facts = client.post(prefix+"/risk-preview", json=binding)
    assert facts.status_code == 200, facts.text
    candidates = facts.json()["actual_risk_candidates"]
    assert any(row["subject"] == "SEC_CIK_0001318605" for row in candidates.values())
    strategic = [key for key, val in candidates.items() if val["subject"] == "STRATEGIC_ALLOCATION"]
    assert len(strategic) == 1 and len(candidates[strategic[0]]["triggered_classes"]) == 2
    request = {**binding, "bundle_id": "review", "request_id": "first", "expected_revision": 0,
        "scenario": scenario, "selected_path":"A", "items": [
        item("plan", "PLANNED_TRANSITION_HOLD", stage_index=1),
        item("open", "RISK_EVENT", risk_key=strategic[0], event_action="OPEN"),
        item("exception", "FORMAL_POLICY_EXCEPTION", risk_key=strategic[0]),
        item("blocked", "OPERATIONAL_BLOCKED", alternative_path="B"),
        item("later", "CONDITIONAL_HOLD", research=[dict(thesis_id=legacy["thesis_id"],
                                                        revision=1, sha256=legacy["sha256"])]),
    ]}
    preview = client.post(prefix+"/preview", json=request)
    assert preview.status_code == 200, preview.text
    first = preview.json()
    assert "evidence_archive" not in first["payload"]["simulation"]
    assert client.post(prefix+"/commit", json={**confirm(first, "plan"), "confirmed": False}).status_code == 409
    assert client.post(prefix+"/commit", json={**confirm(first), "confirmation": "取消"}).json()["cancelled"]
    assert client.get(prefix).json()["records"] == []
    second = client.post(prefix+"/preview", json=request).json()
    good = confirm(second, "plan", "open", "exception", "blocked")
    original = PortfolioStore.insert
    def aborted(self, conn, decision, events):
        original(self, conn, decision, events)
        raise ValueError("E1_TEST_ATOMIC_FAILURE")
    with monkeypatch.context() as patch:
        patch.setattr(PortfolioStore, "insert", aborted)
        assert client.post(prefix+"/commit", json=good).status_code == 409
    rolled_back = client.get(prefix).json()
    assert rolled_back["records"] == rolled_back["risk_events"] == []
    response = client.post(prefix+"/commit", json=good)
    assert response.status_code == 200, response.text
    assert client.post(prefix+"/commit", json=good).json() == response.json()
    assert client.post(prefix+"/commit", json=confirm(second, "later")).status_code == 409
    assert client.post(prefix+"/preview", json={**request, "bundle_id": "different"}).status_code == 409
    row = response.json()["record"]
    assert [entry["proposal"]["item_id"] for entry in row["decisions"]] == ["plan", "open", "exception", "blocked"]
    assert row["decisions"][2]["temporary_exception"]["status"] == "PENDING_HUMAN_DECISION"
    assert not row["decisions"][2]["policy_exception_active"]
    assert row["decisions"][3]["operational_blockage_basis"] == "HUMAN_REPORTED"
    assert [event["state"] for event in row["risk_revisions"]] == ["NEW", "REVIEW_OPEN"]
    check = row["expected_checkpoints"][0]
    assert check["share_quantities"] is None and check["status"] == "PLANNED_NOT_EXECUTION_FACT"
    assert any(r["symbol"] == "TSLA:US" for r in check["holdings"])
    history = client.get(prefix+"?bundle_id=review&revision=1").json()["records"][0]
    archived = history["evidence_archive"]
    assert policy_digest(archived["snapshot"]) == binding["snapshot_sha256"]
    assert archived["snapshot"] == view
    assert archived["scenario"]["evidence_archive"]["normalized_input"]["paths"]["A"][0]["label"] == "S1"
    assert archived["scenario"]["evidence_archive"]["code_sha256"]["allocation_scenarios.py"]
    formal = archived["scenario"]["evidence_archive"]["frozen_etf_evidence"]
    assert formal["formal_pins"]["SPY:US"] == policy_digest(formal["formal"]["SPY:US"])
    monkeypatch.setenv("WEALTHPILOT_ETF_EVIDENCE_SHA256", "f"*64)
    assert client.get(prefix+"?bundle_id=review&revision=1").json()["records"][0] == history
    assert (db.read_bytes(), policy.read_bytes()) == before
    assert store.get(legacy["thesis_id"], 1) == legacy

    from backend.graph.tools import call_tool
    assert call_tool("get_portfolio_review_followups", request={"bundle_id":"review", "revision":1})["records"][0] == history
    risk_history = client.get(prefix + "?risk_key=" + strategic[0]).json()["risk_events"]
    assert [event["state"] for event in risk_history] == ["REVIEW_OPEN", "NEW"]
    due = PortfolioStore(store).query(at=(AT+timedelta(days=5)).isoformat())["followups"][0]
    assert { "decision_bundle_ref", "decision_revision_ref", "base_snapshot_ref", "scenario_ref",
             "expected_checkpoints", "original_policy_reference", "review_due_at", "evidence_requirements"} == set(due)
    revoked_request = {**binding, "bundle_id":"review", "request_id":"revoke", "expected_revision":1,
                       "items":[item("revoke", "REVOKE_DECISION")]}
    p = client.post(prefix+"/preview", json=revoked_request).json()
    assert client.post(prefix+"/commit", json=confirm(p, "revoke")).json()["record"]["bundle_state"] == "REVOKED"
    assert PortfolioStore(store).query(at=(AT+timedelta(days=5)).isoformat())["followups"] == []
    assert client.get(prefix+"?bundle_id=review&revision=1").json()["records"][0] == history
    assert client.get(prefix).json()["risk_events"][0]["state"] == "REVIEW_OPEN"

    # Isolated SQLite backup restores both domains, including prior versions.
    backup = store.path.parent / "restored.sqlite"
    with sqlite3.connect(backup) as dst, store.connection() as src:
        src.backup(dst)
    backup.chmod(0o600)
    assert ThesisStore(backup, 7).get(legacy["thesis_id"], 1) == legacy
    assert PortfolioStore(ThesisStore(backup, 7)).query(bundle_id="review", revision=1, include_evidence=True)["records"][0] == history
    with store.connection(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute("INSERT OR REPLACE INTO portfolio_decision_revisions SELECT * FROM portfolio_decision_revisions")
        with pytest.raises(sqlite3.IntegrityError, match="IMMUTABLE"):
            conn.execute("INSERT OR REPLACE INTO portfolio_risk_revisions SELECT * FROM portfolio_risk_revisions")
    monkeypatch.delenv("WEALTHPILOT_E1_PORTFOLIO_MODE")
    assert client.get(prefix).status_code == 404


def test_unknown_source_concurrency_expiry_and_policy_etf_repreview(isolated):
    store, _, db, _, _, binding, scenario = isolated
    changing = {"version": "v1", "policy_changed": False}

    def read(request):
        if changing["policy_changed"]:
            raise ValueError("E1_PORTFOLIO_POLICY_CHANGED")
        evidence = read_portfolio_sources(db, 7, request)
        evidence["qualification"]["status"] = "UNKNOWN"
        evidence["risk_facts"] = {}
        evidence["scenario"]["evidence_archive"]["source_version_test"] = changing["version"]
        return evidence

    monotonic = [100.]
    bridge = PortfolioGovernance(store, read, wall_clock=lambda: AT, clock=lambda: monotonic[0])
    request = {**binding, "bundle_id":"research", "request_id":"not-real", "expected_revision":0,
               "scenario":scenario, "selected_path":"A", "items":[item("plan","PLANNED_TRANSITION_HOLD",stage_index=1)]}
    preview = bridge.preview(request)
    changing["version"] = "v2"
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        bridge.commit(confirm(preview, "plan"))
    assert bridge.store.query()["records"] == []
    changing["policy_changed"] = True
    with pytest.raises(ValueError, match="POLICY_CHANGED"):
        bridge.commit(confirm(preview, "plan"))
    changing["policy_changed"] = False
    monotonic[0] = 2000.
    with pytest.raises(ValueError, match="PREVIEW_EXPIRED"):
        bridge.commit(confirm(preview, "plan"))
    fresh = bridge.preview(request)
    data = confirm(fresh, "plan")
    with ThreadPoolExecutor(2) as pool:
        a, b = list(pool.map(bridge.commit, [data, data]))
    assert a == b and a["record"]["decisions"][0]["disposition_state"] == "RESEARCH_DECISION_WITH_ASSUMPTIONS"
    assert not a["record"]["risk_revisions"]
    assert PortfolioGovernance(store, read, wall_clock=lambda: AT).commit(data) == a
    historic = bridge.store.query(bundle_id="research", revision=1, include_evidence=True)["records"][0]
    changing.update(version="v3", policy_changed=True)
    assert historic == bridge.store.query(bundle_id="research", revision=1, include_evidence=True)["records"][0]
    assert historic["evidence_archive"]["scenario"]["evidence_archive"]["source_version_test"] == "v2"


def test_risk_lifecycle_dedup_and_no_unknown_clear():
    risk = "a" * 64
    base = {"state":"REVIEW_OPEN", "expires_at":(AT+timedelta(days=2)).isoformat(),
            "review_due_at":(AT+timedelta(days=1)).isoformat(), "policy_bundle_sha256":"p"}
    hit = {"hit": True}
    def transition(action, current=base, fact=hit, at=AT):
        action_item = DecisionItem.model_validate(item("state","RISK_EVENT",risk_key=risk,event_action=action))
        return _event_plan(action_item, current, fact, "p", qualified=True, at=at)
    assert transition("OPEN", current=None) == ["NEW", "REVIEW_OPEN"]
    assert transition("OPEN") == []
    assert transition("MONITOR", {**base, "state":"DECISION_RECORDED"}) == ["MONITORING"]
    with pytest.raises(ValueError, match="CLEAR_EVIDENCE_REQUIRED"):
        transition("RESOLVE", fact=None)
    assert transition("RESOLVE", fact={"hit":False}) == ["RESOLVED"]
    assert transition("EXPIRE", at=AT+timedelta(days=3), fact=None) == ["EXPIRED"]
    assert transition("REOPEN", current={**base,"state":"EXPIRED"}) == ["REOPENED", "REVIEW_OPEN"]
    assert transition("OPEN", at=AT+timedelta(days=1)) == ["REOPENED", "REVIEW_OPEN"]
    assert transition("BLOCK") == ["BLOCKED_WITH_REASON"]
    assert transition("OPEN", {**base, "state":"BLOCKED_WITH_REASON"}) == []
    with pytest.raises(ValueError, match="NOT_EXPIRABLE"):
        transition("EXPIRE", current={**base, "policy_bundle_sha256":"old"}, at=AT+timedelta(days=3))


def test_missing_issuer_clearance_requires_bounded_real_inventory():
    from backend.services.investment_thesis.portfolio_sources import clear_absent_risk
    rule = {"rule_id":"issuer.review", "metric":"issuer_weight", "threshold":".08", "operator":"gte"}
    prior = {"policy_bundle_sha256":"p", "actual_risk":{"subject":"TSLA", "rule":rule, "hit":True}}
    source = {"policy":{"reference":{"bundle_sha256":"p"}, "structured_bundle":{"rules":[rule]}},
              "actual_concentration":{"status":"DIAGNOSTIC","denominator_cny":"10","unknown_coverage":False,"issuers":{}}}
    assert clear_absent_risk(source, prior)["proved_upper_bound_pct"] == "0"
    source["actual_concentration"]["unknown_coverage"] = True
    assert clear_absent_risk(source, prior) is None

