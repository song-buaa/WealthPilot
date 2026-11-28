"""F with chained TEST_ONLY D oracle, E confirmation and new original U1."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import sqlite3

import pytest

from app import asset_views
from backend.services.investment_policy.contracts import digest
from backend.services.investment_thesis.portfolio import PortfolioGovernance
from backend.services.investment_thesis.portfolio_sources import read_portfolio_sources
from backend.services.investment_thesis.portfolio_store import migrate_portfolio_governance
from backend.services.investment_thesis.reconciliation import reconcile_portfolio
from backend.services.investment_thesis.reconciliation_contract import ReconciliationRequest
from backend.services.investment_thesis.reconciliation_evidence import execution_for_trade
from backend.services.investment_thesis.snapshot_delta import SnapshotFacts, compare_positions, compare_cash, compare_debt
from backend.services.investment_thesis.store import ThesisStore
from tests import test_c_concentration as c
from tests import test_d_portfolio_transition as d
from tests.test_allocation_snapshot_errors import client  # noqa: F401
from tests.test_b2_history import history_db  # noqa: F401
from tests.test_e_portfolio_governance import confirm, item

AT = d.AT
D = Decimal


@pytest.fixture
def chain(tmp_path, monkeypatch):
    with pytest.MonkeyPatch.context() as local:
        frozen = c.frozen.__wrapped__(c.view.__wrapped__(local))
    old, pinned, paths = d.oracle.__wrapped__(frozen, c.active.__wrapped__())
    q = old["data_quality"]
    q["security_inputs"][4]["account_ref"] = "account_fixture_2"
    for account in q["accounts"]:
        account.update(broker="tiger", positions_as_of=AT.isoformat(), cash_as_of=AT.isoformat(), fill_sync_pending=False)
    for row in q["security_inputs"]:
        if row["vehicle_type"] != "CASH":
            row.update(quantity=row["market_value_cny"], current_price="1")
    context = pinned.context
    context["release"] = {"synthetic":True}
    clock = [AT]
    from backend.services.investment_policy import runtime, store
    from backend.services.investment_thesis import contracts
    monkeypatch.setattr(runtime, "resolve", lambda _: context)
    monkeypatch.setattr(runtime, "utc_now", lambda: clock[0].isoformat())
    monkeypatch.setattr(store, "utc_now", lambda: clock[0].isoformat())
    monkeypatch.setattr(contracts, "now", lambda: clock[0])
    monkeypatch.setenv("WEALTHPILOT_E1_PORTFOLIO_MODE", "ISOLATED")

    db = tmp_path/"frozen-fixtures.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE wealth_snapshots(id INTEGER PRIMARY KEY, portfolio_id INTEGER, "
                     "analysis_request_id TEXT, payload_json TEXT)")
    def publish(view):
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO wealth_snapshots VALUES (?,7,?,?)",
                         (asset_views.snapshot_number(view["snapshot_id"]), "TEST_ONLY", asset_views.dumps(view)))

    publish(old)
    thesis = ThesisStore(tmp_path/"e1"/"governance.sqlite", 7)
    thesis.init()
    migrate_portfolio_governance(thesis)
    monkeypatch.setenv("WEALTHPILOT_E1_DB_PATH", str(thesis.path))
    import app.database
    monkeypatch.setattr(app.database, "DB_PATH", db)

    common = dict(snapshot_id=old["snapshot_id"], snapshot_sha256=digest(old), policy_bundle_sha256="a"*64)
    preview_request = {**common,"bundle_id":"oracle", "request_id":"oracle-once", "expected_revision":0,
        "scenario":{**common,"paths":paths}, "selected_path":"A",
        "items":[item(f"stage-{index}", "PLANNED_TRANSITION_HOLD", stage_index=index) for index in (1,2,3)]}
    bridge = PortfolioGovernance(thesis, lambda req: read_portfolio_sources(db, 7, req), wall_clock=lambda: AT)
    preview = bridge.preview(preview_request)
    sim = preview["payload"]["simulation"]["paths"]
    assert [D(stage["investment_assets_cny"]) for stage in sim["A"]] == [1191400,1191400,1039750,986800]
    assert sim["A"][2]["concentration"]["issuers"][c.TESLA]["scenario_risk_status"] == "HUMAN_REVIEW"
    assert D(sim["B"][3]["allocation"]["secondary"]["cash"]["amount_cny"]) == 77400
    saved = bridge.commit(confirm(preview,"stage-1","stage-2","stage-3"))["record"]
    assert all(event["actual_risk"]["subject"] != c.TESLA or event["actual_risk"]["rule"]["action"] != "HUMAN_REVIEW"
               for event in bridge.store.query()["risk_events"])
    assert len(saved["expected_checkpoints"]) == 3

    latest = deepcopy(old)
    new_at = AT + timedelta(days=1)
    clock[0] = new_at
    latest.update(snapshot_id="asset-view:v1:2", captured_at=new_at.isoformat())
    q = latest["data_quality"]
    q["investable_scope_evidence"].update(snapshot_id=latest["snapshot_id"], as_of=new_at.isoformat())
    q["investable_owner_evidence"]["valid_until"] = (new_at+timedelta(days=1)).isoformat()
    for proof in q["investable_scope_evidence"]["coverage"].values():
        proof["as_of"] = new_at.isoformat()
    for account in q["accounts"]:
        account.update(account_synced_at=new_at.isoformat(), positions_as_of=new_at.isoformat(), cash_as_of=new_at.isoformat())
    for row in q["security_inputs"]:
        row.update(positions_as_of=new_at.isoformat(), cash_as_of=new_at.isoformat(),
                   price_as_of=new_at.date().isoformat(), fx_rate_date=new_at.date().isoformat())
    q["security_inputs"][0].update(market_value_cny="94050", original_value="94050")
    q["security_inputs"][1].update(quantity="0", market_value_cny="0")
    q["security_inputs"][2].update(quantity="0", market_value_cny="0")
    latest["totals"].update(total_assets="1056400", total_liabilities="1564000")
    # S5 has the verified new principal of the first repayment, but the next
    # planned Li Auto sale did not occur and no payment funding link exists.
    latest["views"]["get_manual_wealth_items"]["items"][0].update(current_value="1564000",
        value_as_of=new_at.date().isoformat(), last_verified_at=new_at.isoformat())
    publish(latest)
    request = ReconciliationRequest(snapshot_id=latest["snapshot_id"], snapshot_sha256=digest(latest))
    return thesis, db, bridge, old, latest, request, context, clock, publish


def test_s0_through_s5_real_pipeline_and_consumer_equality(chain, client):
    thesis, db, bridge, _, _, request, _, _, _ = chain
    bytes_before = thesis.path.read_bytes(), db.read_bytes()
    stored_before = bridge.store.query(bundle_id="oracle", revision=1, include_evidence=True)
    value = reconcile_portfolio(thesis, db, request)
    assert value["decision_state"] == "CURRENT"
    assert value["decision_revision_ref"] == "oracle:1"
    assert D(value["current_investable_denominator_cny"]) == 1056400
    assert any(risk["status"] == "NEW_FORMAL_HIT" and risk["current_fact"]["subject"] == c.TESLA
               and risk["current_fact"]["rule"]["action"] == "HUMAN_REVIEW" for risk in value["risk_reassessment"].values())
    states = {row["action_id"]:row for row in value["checkpoints"][2]["actions"]}
    assert states["pdd"]["observation_status"] == "CHECKPOINT_REACHED"
    assert states["pdd"]["execution"]["status"] == "EVIDENCE_PENDING"
    assert states["buy"]["observation_status"] == "NOT_OBSERVED"
    assert states["mt_cny"]["execution"]["issue"] == "TRANSFER_OR_SETTLEMENT_EVIDENCE_PENDING"
    assert states["repay_mt"]["observation_status"] == "PARTIAL_PROGRESS_OBSERVED"
    # S2 target is evaluated against S2; the S3 debt target is cumulative.
    assert D(value["debt_deltas"]["wealth:1"]["principal_delta_cny"]) == -135000
    assert states["li"]["observation_status"] == "NOT_OBSERVED"
    assert states["repay_li"]["observation_status"] == "NOT_OBSERVED"
    assert D(value["checkpoints"][2]["current_minus_planned_denominator_cny"]) == 69600
    assert D(value["checkpoints"][2]["budget_amount_differences_cny"]["ib01"]) == -77400
    assert not value["execution_authorized"]
    wire = request.model_dump(mode="json")
    response = client.post("/api/allocation/governance/reconcile", json=wire)
    assert response.status_code == 200, response.text
    assert response.json() == value
    from backend.graph.tools import call_tool
    assert call_tool("get_portfolio_review_followups", reconciliation=wire) == value
    assert reconcile_portfolio(thesis, db, request) == value
    assert stored_before == bridge.store.query(bundle_id="oracle", revision=1, include_evidence=True)
    assert (thesis.path.read_bytes(), db.read_bytes()) == bytes_before


def test_stale_debt_unknown_denominator_and_changed_policy(chain):
    thesis, db, _, old, latest, _, policy, clock, publish = chain
    latest = deepcopy(latest)
    latest["snapshot_id"] = "asset-view:v1:3"
    latest["data_quality"]["investable_scope_evidence"]["snapshot_id"] = latest["snapshot_id"]
    latest["data_quality"].pop("investable_owner_evidence")
    latest["views"]["get_manual_wealth_items"] = old["views"]["get_manual_wealth_items"]
    policy["policy_reference"]["bundle_sha256"] = "b"*64
    publish(latest)
    request = ReconciliationRequest(snapshot_id=latest["snapshot_id"], snapshot_sha256=digest(latest))
    result = reconcile_portfolio(thesis, db, request)
    assert result["policy_changed"] and result["decision_state"] == "REVIEW_REQUIRED" and not result["operative_plan"]
    assert result["current_investable_denominator_cny"] is None
    assert result["debt_deltas"]["wealth:1"]["issue"] == "DEBT_EVIDENCE_PENDING"
    assert result["current_concentration"]["status"] == "CONDITIONAL_RISK"
    assert all(row["status"] == "UNRESOLVED" for row in result["risk_reassessment"].values())
    assert result["original_policy_reference"]["bundle_sha256"] == "a"*64


def test_price_transfer_split_and_failed_sync_are_independent(chain):
    _, _, _, old, new, _, _, _, _ = chain
    def compare(a, b):
        return compare_positions(SnapshotFacts(a), SnapshotFacts(b), {}, {})
    # SPY price move changes dollars while quantity remains unchanged.
    price_only = deepcopy(new)
    price_only["data_quality"]["security_inputs"][-1]["market_value_cny"] = "70000"
    assert compare(old,price_only)[("account_fixture","SPY:US","USD")]["attribution"] == "PRICE_OR_FX_ONLY"
    split = deepcopy(new)
    split["data_quality"]["security_inputs"][-1].update(quantity="121829",current_price=".5")
    assert compare(old,split)[("account_fixture","SPY:US","USD")]["issue"] == "CORPORATE_ACTION_RECONCILIATION_REQUIRED"
    moved = deepcopy(new)
    moved["data_quality"]["security_inputs"][-1]["account_ref"] = "account_fixture_2"
    assert compare(old,moved)[("account_fixture","SPY:US","USD")]["attribution"] == "POSSIBLE_ACCOUNT_TRANSFER"
    failed = deepcopy(new)
    failed["data_quality"]["accounts"][0]["last_attempt_status"] = "failed"
    assert compare(old,failed)[("account_fixture","PDD:US","USD")]["current_quantity"] is None
    cash = compare_cash(SnapshotFacts(old), SnapshotFacts(new))
    assert cash[("account_fixture","CNY")]["delta_native"] == 94050
    assert cash[("account_fixture","CNY")]["arrival_proof"] == "EVIDENCE_PENDING"


def test_exact_linked_fills_and_sync_are_separate():
    at = AT + timedelta(hours=1)
    row = {"kind":"EXECUTION", "local_order_id":"old-order", "account_ref":"history-x", "broker":"tiger",
        "symbol":"PDD:US", "currency":"USD", "side":"SELL", "time_precision":"instant",
        "executed_at":(at-timedelta(minutes=10)).isoformat(), "quantity":"100", "price":"774", "multiplier":"1",
        "external_id":"EX-A", "instrument_key":"listing:PDD:US:USD:STK", "sec_type":"STK", "source":"broker_fixture",
        "evidence_sources":[{"source_sha256":"a"*64}]}
    order = {"account_ref":"account_fixture","history_account_ref":"history-x","broker":"tiger",
        "symbol":"PDD:US","side":"SELL","status":"filled","filled_qty":100,
        "sync_status":"SYNC_PENDING","observed_at":at.isoformat()}
    key = ("account_fixture","PDD:US","USD")
    evidence = {"history":[row],"orders":{"old-order":order},"issues":[]}
    def verify():
        return execution_for_trade({"pdd":"old-order"},evidence,"pdd",key,"SELL",D(77400),AT,at)
    assert verify()["status"] == "VERIFIED_EXECUTED" and verify()["sync_status_at_readback"] == "SYNC_PENDING"
    second_identity = {**row, "external_id":"EX-B", "instrument_key":"conid:123"}
    evidence["history"].append(second_identity)
    assert verify()["issue"] == "LINKED_EXECUTION_NATIVE_IDENTITY_CONFLICT"
    evidence["history"].pop()
    order["filled_qty"] = 0
    assert verify()["issue"] == "LINKED_EXECUTION_OWNER_CONFLICT"
    order["filled_qty"] = 50
    row["quantity"] = "50"
    assert verify()["status"] == "PARTIALLY_OBSERVED"
    order["disputed"] = True
    assert verify()["issue"] == "LINKED_EXECUTION_OWNER_CONFLICT"
    order["disputed"] = False
    row["account_ref"] = "different"
    assert verify()["status"] == "COVERAGE_UNKNOWN"
    evidence["history"] = []
    order.update(status="cancelled", filled_qty=0)
    assert verify()["status"] == "NOT_EXECUTED"


def test_historical_revoked_or_due_decisions_do_not_disguise_current_plan(chain):
    thesis, db, bridge, old, _, request, _, clock, _ = chain
    due = clock[0] + timedelta(days=4)
    assert reconcile_portfolio(thesis, db, request, at=due)["decision_state"] == "REVIEW_DUE"
    bridge_request = dict(snapshot_id=old["snapshot_id"], snapshot_sha256=digest(old), policy_bundle_sha256="a"*64,
        bundle_id="oracle", request_id="explicit-revoke", expected_revision=1, items=[item("revoke","REVOKE_DECISION")])
    revoke = bridge.preview(bridge_request)
    bridge.commit(confirm(revoke, "revoke"))
    chosen = request.model_copy(update={"bundle_id":"oracle"})
    assert reconcile_portfolio(thesis, db, chosen)["decision_state"] == "REVOKED"
    historic = reconcile_portfolio(thesis, db, chosen.model_copy(update={"revision":1}))
    assert historic["decision_state"] == "SUPERSEDED" and historic["checkpoints"] == []
    with pytest.raises(ValueError, match="DECISION_NOT_FOUND"):
        reconcile_portfolio(thesis, db, request)


def test_original_history_and_order_owner_read_only(history_db):
    from sqlalchemy.orm import Session
    from app.models import Position
    from backend.services.action.models import OrderRecord
    from backend.services.action.ownership import stored_account_ref
    from backend.services.investment_thesis.reconciliation_evidence import read_execution_evidence
    from backend.services.investment_thesis.snapshot_delta import instant
    from tests.test_b2_history import persist, execution

    start, end = map(instant, ("2024-01-02T14:00:00+00:00","2024-01-02T17:00:00+00:00"))
    trade = execution(order_ref="proof-order")
    with Session(history_db.engine) as session:
        session.add(OrderRecord(id="proof-order", strategy_id="fixture", portfolio_id=7,
            broker_name="ibkr", broker_account_id="fixture", broker_order_id="100", symbol="IB01:LSE",
            side="BUY", quantity=10, filled_quantity=10, status="filled",
            last_synced_at=start+timedelta(hours=2)))
        session.commit()
        account = stored_account_ref(7, session.query(Position).filter_by(portfolio_id=7).one())
    persist(history_db, [trade])
    before = history_db.path.read_bytes()
    evidence = read_execution_evidence(history_db.path, 7, start, end, ["proof-order"])
    proof = execution_for_trade({"buy":"proof-order"}, evidence, "buy", (account,"IB01:LSE","USD"), "BUY",
                                D(1000), start, end)
    assert proof["status"] == "VERIFIED_EXECUTED", (proof,evidence)
    assert proof["executions"][0]["execution_ref"] == trade["external_id"]
    assert proof["sync_status_at_readback"] != "SYNCED"
    assert history_db.path.read_bytes() == before
    evidence["history"][0]["instrument_key"] = "conid:123"
    mismatched = execution_for_trade({"buy":"proof-order"}, evidence, "buy", (account,"IB01:LSE","USD"), "BUY",
                                     D(1000), start, end)
    assert mismatched["issue"] == "LINKED_EXECUTION_EVIDENCE_CONFLICT"


def test_outside_plan_and_duplicate_order_references_rejected(chain):
    thesis, db, _, _, _, request, _, _, _ = chain
    with pytest.raises(ValueError, match="OUTSIDE_PLAN"):
        reconcile_portfolio(thesis, db, request.model_copy(update={"order_links":{"transfer-only":"actual-order"}}))
    with pytest.raises(ValueError, match="ORDER_REFERENCE_REUSED"):
        ReconciliationRequest.model_validate({**request.model_dump(), "order_links":{"pdd":"same","buy":"same"}})
