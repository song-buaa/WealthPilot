"""L2 policy tests using fictional sources, approvals, positions and funding."""
from decimal import Decimal as D
from hashlib import sha256
import json
import re
from types import SimpleNamespace

import pytest

from backend.services.investment_policy import consumers, parser, runtime
from backend.services.investment_policy.btc_governance import (
    ADMISSION_EVIDENCE, ExposureEvidence, eligibility, exposure_diagnostic, strategy_admission,
)
from backend.services.investment_policy.configuration import from_snapshot
from backend.services.investment_policy.contracts import MetricEvidence, digest, parse_snapshot
from backend.services.investment_policy.evaluation import evaluate_snapshot
from backend.services.investment_policy.fast_release import preview
from backend.services.investment_policy.parser import PolicyError, derive
from backend.services.investment_policy.service import PolicyService
from backend.services.investment_policy.store import utc_now
from tests.test_policy_config_fast_release import (
    CODE, SCOPE, isolated, policy_mcp, run_publish, sign,  # noqa: F401
)
from tests.test_policy_publisher import approval, installation
from tests.test_policy_v3_targets import AMENDMENT_APPROVAL, hashes, raw_amendment

DRAFT = "BTC治理内容批准：DRAFT_CONTENT"
SIGNED_FIXTURE = "BTC治理内容批准：APPROVED_CONTENT | 2023-01-01 | https://github.com/song-buaa/WealthPilot/issues/90001"
GOLD = {
    "4.3": "BTC是唯一。无杠杆BTC现货。策略0%—100%。当前没有任何具体量化策略。USDT。",
    "5.1": "TEST ONLY Settlement cash.\n\nTEST ONLY No yield.",
    "5.2": "存量USDT、工资结余。不能重复认定为新增投资本金。",
    "5.3": "包括1倍永续。不得新增信用贷用于BTC投资。可研究衍生品数据。",
    "6.2": "BTC合并经济敞口、潜在最大BTC敞口。同一资金及订单占用只计一次。"
           "\n\nTEST ONLY Table.\n\nTEST ONLY Buy reservations.\n\nTEST ONLY Qualified actual exposure.",
    "9.3": "覆盖多个市场周期。独立样本外。Walk-forward。模拟盘或Shadow。交易成本、滑点、流动性。"
           "风险敞口、最大回撤。未来函数。Product Owner独立明确批准。",
}


@pytest.fixture(autouse=True)
def synthetic_source_grammar(monkeypatch):
    # Independently pinned fictional paragraphs exercise the same source gate.
    monkeypatch.setattr(parser, "BTC_SOURCE_FRAGMENT_SHA256", {
        section: sha256(fragment.encode()).hexdigest() for section, fragment in GOLD.items()
    })


def test_reject_contradictory_btc_prose_with_recomputed_source_hash():
    raw = raw_btc()
    raw["policy"] = raw["policy"].replace("包括1倍永续".encode(), "包括1倍永续均可使用".encode())
    with pytest.raises(PolicyError, match="BTC_GOVERNANCE_SOURCE_FRAGMENT_MISMATCH: 5.3"):
        derive(**raw, expected_hashes=hashes(raw), policy_version=4)


def btc_baseline():
    raw = raw_amendment(AMENDMENT_APPROVAL)
    doc = raw["policy"].decode().replace("严格超过5% | WARNING", "严格超过3% | WARNING").replace(
        "达到或超过6% | HUMAN_REVIEW", "达到或超过4% | HUMAN_REVIEW")
    return raw | {"policy":doc.encode()}


def raw_btc(stamp=DRAFT):
    raw = btc_baseline()
    doc = raw["policy"].decode()
    # Add to the synthetic fixture without editing existing source clauses.
    for section in ("6.2", "5.3", "5.2", "5.1"):
        heading = re.search(rf"^### {re.escape(section)} [^\n]+\n", doc, re.M)
        # This minimal legacy fixture has no 5.2. Insert it between siblings.
        if heading is None and section == "5.2":
            doc = doc.replace("### 5.3 ", f"### 5.2 Fixture\n\n{GOLD[section]}\n\n### 5.3 ", 1)
            continue
        start = heading.end()
        next_head = re.search(r"^#{2,3} ", doc[start:], re.M)
        end = start + next_head.start()
        doc = doc[:end].rstrip("\n") + "\n\n" + GOLD[section] + "\n\n" + doc[end:]
    doc += f"\n### 4.3 TEST ONLY\n{GOLD['4.3']}\n\n### 9.3 TEST ONLY\n{GOLD['9.3']}\n"
    doc += f"BTC治理合同：wp.policy.btc.v1\n{stamp}\n"
    return raw | {"policy":doc.encode()}


def test_l2_draft_preserves_targets_rules_and_old_approvals():
    old = btc_baseline()
    v3 = derive(**old, expected_hashes=hashes(old), policy_version=3)
    raw = raw_btc()
    v4 = derive(**raw, expected_hashes=hashes(raw), policy_version=4)
    assert v4.content_status == "DRAFT_CONTENT"
    assert v3.content_status == "APPROVED_CONTENT"
    assert from_snapshot(v3) == from_snapshot(v4)
    assert v3.source_hashes["philosophy"] == v4.source_hashes["philosophy"]
    norm = lambda snap:[r.model_dump(exclude={"source_clause", "policy_version"}) for r in snap.rules]
    assert norm(v3) == norm(v4)
    assert len(v4.rules) == 8 and parse_snapshot(v4.model_dump(mode="json")) == v4


@pytest.mark.parametrize("stamp", [
    "", SIGNED_FIXTURE.replace("2023-01-01", "2021-01-01"),
    SIGNED_FIXTURE.replace("/90001", "/126"),
])
def test_missing_contract_or_nonindependent_approval_fails(stamp):
    raw = raw_btc(stamp)
    with pytest.raises(ValueError):
        derive(**raw, expected_hashes=hashes(raw), policy_version=4)


@pytest.mark.parametrize(("asset", "tool", "purpose", "verified", "credit", "expected"), [
    ("BTC", "UNLEVERAGED_SPOT", "INVESTMENT", True, False, "POLICY_ELIGIBLE"),
    ("BTC", "UNLEVERAGED_SPOT", "INVESTMENT", False, False, "UNKNOWN"),
    ("BTC", "UNLEVERAGED_SPOT", "INVESTMENT", True, None, "UNKNOWN"),
    ("BTC", "UNLEVERAGED_SPOT", "INVESTMENT", True, True, "HARD_BLOCK"),
    ("BTC", "PERPETUAL", "INVESTMENT", True, False, "HARD_BLOCK"),  # includes 1x
    ("BTC", "OPTION", "INVESTMENT", True, False, "HARD_BLOCK"),
    ("BTC", "BORROW", "INVESTMENT", True, False, "HARD_BLOCK"),
    ("BTC", "UNLEVERAGED_SPOT", "STRATEGY_LIVE", True, False, "NOT_AUTHORIZED"),
    ("ETH", "UNLEVERAGED_SPOT", "INVESTMENT", True, False, "NOT_ELIGIBLE"),
    ("SOL", "UNLEVERAGED_SPOT", "INVESTMENT", True, False, "NOT_ELIGIBLE"),
    ("BNB", "UNLEVERAGED_SPOT", "INVESTMENT", True, False, "NOT_ELIGIBLE"),
    ("SOL", "PERPETUAL", "RESEARCH", False, False, "RESEARCH_ONLY"),
    ("USDT", "SETTLEMENT", "INVESTMENT", True, False, "SETTLEMENT_ONLY_QUALIFICATION_REQUIRED"),
    ("USDT", "YIELD", "INVESTMENT", True, False, "NOT_AUTHORIZED"),
])
def test_tool_and_scope_eligibility(asset, tool, purpose, verified, credit, expected):
    result = eligibility(asset=asset, instrument=tool, purpose=purpose, identity_verified=verified,
                         new_consumer_credit=credit)
    assert result["status"] == expected and result["execution_authorized"] is False


@pytest.mark.parametrize(("fraction", "status"), [
    ("0", "READY_FOR_HUMAN_REVIEW"), ("1", "READY_FOR_HUMAN_REVIEW"),
    ("-0.1", "OUTSIDE_POLICY_POSITION_RANGE"), ("1.01", "OUTSIDE_POLICY_POSITION_RANGE"),
])
def test_strategy_admission_boundaries(fraction, status):
    complete = strategy_admission(qualified_evidence=ADMISSION_EVIDENCE, btc_fraction=D(fraction))
    assert complete["status"] == status
    assert complete["live_authorization"] == "NOT_AUTHORIZED"
    incomplete = strategy_admission(qualified_evidence=ADMISSION_EVIDENCE[:-1], btc_fraction=D(0))
    assert incomplete["status"] == "UNKNOWN" and incomplete["missing_evidence"] == [ADMISSION_EVIDENCE[-1]]


def exposure(**overrides):
    values = dict(as_of="2026-11-27T10:00:00+08:00", denominator_cny="10000",
        denominator_qualified=True, positions_complete=True, funding_complete=True, orders_complete=True,
        positions=[
            dict(identity="long",capital_id="long-lot",value_cny="1000",qualified=True,asset="BTC",sleeve="LONG_TERM"),
            dict(identity="bot-btc",capital_id="strategy-lot",value_cny="200",qualified=True,asset="BTC",sleeve="STRATEGY"),
            dict(identity="bot-cash",capital_id="strategy-cash",value_cny="300",qualified=True,asset="USDT",sleeve="STRATEGY"),
        ], strategy_budget_cny="500",
        strategy_budget_evidence=dict(as_of="2026-11-27T10:00:00+08:00",
            authorization_ref="TEST-approval", ownership_ref="TEST-ledger",
            authorized_cny="500", occupied_principal_cny="200", remaining_deployable_cny="300",
            position_ids=["bot-btc", "bot-cash"], funding_balances_cny={"strategy-cash":"300"}),
        uninvested_funding=[dict(identity="cash-1",capital_id="strategy-cash",
                                 sleeve="STRATEGY",value_cny="300",qualified=True)],
        open_orders=[
            dict(identity="buy",value_cny="100",qualified=True,side="BUY",sleeve="STRATEGY",funding_id="cash-1"),
            dict(identity="sell",value_cny="50",qualified=True,side="SELL")])
    return ExposureEvidence.model_validate(values | overrides)


def test_combined_actual_and_potential_order_and_funding_dedup():
    data = exposure()
    duplicate = exposure(open_orders=data.open_orders + (data.open_orders[0],),
                         positions=data.positions + (data.positions[1],),
                         uninvested_funding=data.uninvested_funding * 2)
    result = exposure_diagnostic(duplicate)
    assert result["actual_cny"] == 1200 and result["potential_cny"] == 1500
    assert result["usdt_known_cny"] == 300 and result["strategy_budget_cny"] == 500
    assert result["open_buy_known_cny"] == 100 and not result["execution_authorized"]
    assert result["actual_ratio"] == D(".12") and result["potential_ratio"] == D(".15")


def test_coverage_conflicts_stay_unknown_and_sells_do_not_clear_risk():
    data = exposure()
    orphan = data.open_orders[0].model_copy(update={"funding_id":"missing"})
    assert exposure_diagnostic(exposure(open_orders=[orphan]))["potential_cny"] is None
    assert exposure_diagnostic(exposure(orders_complete=False))["potential_cny"] is None
    assert exposure_diagnostic(exposure(funding_complete=False))["potential_ratio"] is None
    result = exposure_diagnostic(exposure(denominator_qualified=False))
    assert result["actual_cny"] == 1200 and result["actual_ratio"] is None
    conflict = data.positions[1].model_copy(update={"value_cny":D(500)})
    result = exposure_diagnostic(exposure(positions=data.positions + (conflict,)))
    assert result["actual_cny"] is None and result["actual_known_lower_bound_cny"] == 1000
    assert result["potential_cny"] is None


@pytest.mark.parametrize("update", [
    {"strategy_budget_evidence":None},
    {"strategy_budget_cny":None},
    {"strategy_budget_cny":"100"},
])
def test_strategy_requires_budget_evidence_independent_of_qualified_cash(update):
    result = exposure_diagnostic(exposure(**update))
    assert result["actual_cny"] == 1200
    assert result["potential_status"] == "UNKNOWN" and result["potential_cny"] is None


@pytest.mark.parametrize("conflict", [
    {"authorized_cny":"100"},
    {"occupied_principal_cny":"201"},
    {"remaining_deployable_cny":"299"},
    {"remaining_deployable_cny":"301", "occupied_principal_cny":"199"},
    {"position_ids":["bot-cash"]},
    {"funding_balances_cny":{"other-cash":"300"}},
    {"as_of":"2026-11-27T09:59:00+08:00"},
])
def test_inconsistent_owner_budget_receipt_keeps_only_actual(conflict):
    data = exposure()
    receipt = data.strategy_budget_evidence.model_dump() | conflict
    result = exposure_diagnostic(exposure(strategy_budget_evidence=receipt))
    assert result["actual_cny"] == 1200
    assert result["potential_cny"] is None


def test_same_capital_cannot_be_allocated_across_sleeves_or_reused_from_a_position():
    data = exposure()
    strategy_cash = data.uninvested_funding[0]
    # A different row ID cannot make the same cash available twice.
    alias = strategy_cash.model_copy(update={"identity":"cash-alias", "sleeve":"LONG_TERM"})
    deployed = alias.model_copy(update={"capital_id":"long-lot"})
    stolen = strategy_cash.model_copy(update={"sleeve":"LONG_TERM"})
    for funding in ((strategy_cash, alias), (strategy_cash, deployed), (stolen,)):
        result = exposure_diagnostic(exposure(uninvested_funding=funding))
        assert result["actual_cny"] == 1200 and result["potential_cny"] is None
    collision = data.positions[0].model_copy(update={"capital_id":"strategy-lot"})
    result = exposure_diagnostic(exposure(positions=(collision, *data.positions[1:])))
    assert result["actual_cny"] == 1200 and result["potential_cny"] is None
    wrong_sleeve = data.open_orders[0].model_copy(update={"sleeve":"LONG_TERM"})
    overbook = data.open_orders[0].model_copy(update={"identity":"buy-2", "value_cny":D(250)})
    for orders in ((wrong_sleeve,), (data.open_orders[0], overbook)):
        assert exposure_diagnostic(exposure(open_orders=orders))["potential_cny"] is None


def test_zero_strategy_allocation_keeps_long_term_potential():
    data = exposure()
    available = data.uninvested_funding[0].model_copy(
        update={"capital_id":"long-cash", "sleeve":"LONG_TERM"})
    buy = data.open_orders[0].model_copy(update={"sleeve":"LONG_TERM"})
    result = exposure_diagnostic(exposure(positions=data.positions[:1],
        strategy_budget_cny=0, strategy_budget_evidence=None,
        uninvested_funding=[available], open_orders=[buy]))
    assert result["actual_cny"] == 1000 and result["potential_cny"] == 1300


def test_passive_price_gain_does_not_consume_fresh_budget_or_authorize_execution():
    data = exposure()
    appreciated = data.positions[1].model_copy(update={"value_cny":D(700)})
    result = exposure_diagnostic(exposure(positions=(data.positions[0], appreciated, data.positions[2])))
    assert result["strategy_btc_known_cny"] == 700 > result["strategy_budget_cny"]
    assert result["actual_cny"] == 1700 and result["potential_cny"] == 2000
    assert not result["execution_authorized"]


@pytest.mark.parametrize(("weight","expected"), [(".03", []), (".03001", ["btc.warning"]),
    (".04", ["btc.warning","btc.human_review"])])
def test_existing_btc_threshold_operators_use_actual_only(weight,expected):
    raw = raw_btc()
    snapshot = derive(**raw, expected_hashes=hashes(raw), policy_version=4)
    now = utc_now()
    metric = MetricEvidence(value=D(weight), complete=True, fresh=True, identity_verified=True,
                            denominator_qualified=True, as_of=now)
    from datetime import timedelta
    result = evaluate_snapshot(snapshot, metrics={"btc_weight":metric}, issuer_symbol=None, as_of=now,
                               max_age=timedelta(minutes=5), behavior_facts={})
    assert [r["rule_id"] for r in result["triggers"]] == expected
    assert not result["auto_trade_allowed"]


def test_original_publisher_and_l1_keep_governance_then_authorized_rollback(isolated, tmp_path):
    pub, originals, _, key, trust = isolated
    def stage(raw, version):
        files = {}
        for role, blob in raw.items():
            files[role] = tmp_path / f"{role}-btc-{version}.md"
            files[role].write_bytes(blob)
        bundle = pub.store.stage(scope=SCOPE, version=version, philosophy_path=files["philosophy"],
            policy_path=files["policy"], expected_hashes=hashes(raw), request_id=f"btc-{version}",
            expected_head_version=version-1, created_at=utc_now())
        pub.store.transition(scope=SCOPE, bundle_id=bundle["bundle_id"], state="VALIDATED", expected_state="DRAFT",
                             request_id=f"btc-validate-{version}", at=utc_now())
        return bundle

    loaded = SimpleNamespace(target_position=SimpleNamespace(symbol="AAPL:US"))
    with runtime.bind(runtime.current(7)):
        assert runtime.peer_context(7, position_only=True, loaded=loaded) == runtime.context(
            7, consumer="peer", view="portfolio")
    draft = stage(raw_btc(), 4)
    auth = approval(pub.store,draft)
    auth["approved_at"] = utc_now()
    with pytest.raises(PolicyError, match="POLICY_CONTENT_APPROVAL_REQUIRED"):
        pub.approve(authorization=auth, request_id="should-not-sign")
    v4 = stage(raw_btc(SIGNED_FIXTURE), 5)
    old_ref = PolicyService(pub.store).get_active_policy_context(SCOPE)["policy_reference"]
    auth = approval(pub.store,v4,sequence=3,active=old_ref["bundle_sha256"])
    auth["approved_at"] = utc_now()
    pub.approve(authorization=auth,request_id="fixture-btc-l2-approve")
    inst = installation(v4,installed_at=utc_now())
    auth.update(operation="ACTIVATE",approved_at=utc_now(),installation_sha256=digest(inst))
    pub.release(authorization=auth,installation=inst,request_id="fixture-btc-l2-release")
    btc = runtime.context(7,view="position",symbol="BTC:CRYPTO")
    equity = runtime.context(7,view="position",symbol="AAPL:US")
    assert btc["btc_governance"]["eligible_instrument"] == "UNLEVERAGED_SPOT"
    assert "btc_governance" not in equity
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.discipline import router
    from backend.services.discipline_service import get_rules_config

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as web:
        web_context = web.get("/policy-context").json()
    backend = PolicyService(pub.store).get_active_policy_context(SCOPE)
    allocation = consumers.deviation(7).model_dump()
    discipline = get_rules_config()
    skill = consumers.discipline_tool("BTC:CRYPTO", 7)
    peer = json.loads(runtime.prompt(7).rsplit("\n", 1)[1])
    policy_ref = btc["policy_reference"]
    governance = btc["btc_governance"]
    assert all(row["policy_reference"] == policy_ref for row in (
        web_context, backend, allocation, discipline, peer, json.loads(skill.warning)))
    assert all(value == governance for value in (
        web_context["btc_governance"], backend["snapshot"]["btc_governance"],
        allocation["risk_diagnostic"]["btc_governance"], discipline["btc_governance"],
        json.loads(skill.warning)["btc_governance"], peer["btc_governance"]))
    assert allocation["status"] == "UNKNOWN" and not allocation["execution_authorized"]
    with policy_mcp() as mcp:
        if mcp:
            # Verify protocol projection of the bound fixture without requiring
            # the subprocess to use the test-only prose digests of this test.
            received = mcp(v4)
            assert received["policy_reference"] == policy_ref
            assert received["btc_governance"] == governance
    plan = SimpleNamespace(portfolio_id=7, route="position_single")
    loaded = SimpleNamespace(target_position=SimpleNamespace(symbol="AAPL:US"))
    executed = SimpleNamespace(loaded_data=loaded)
    expressed = SimpleNamespace(structured_payload={})
    consumers.finalize_output(plan, executed, expressed)
    assert loaded.policy_context["policy_reference"] == policy_ref
    assert "btc_governance" not in loaded.policy_context
    assert "btc_governance" not in json.loads(runtime.prompt(
        7, position_only=True, loaded=loaded).rsplit("\n", 1)[1])
    snapshot = parse_snapshot(v4["snapshot"])
    config = from_snapshot(snapshot).model_dump(mode="json")
    config["etf_internal"].update(SPY="0.31",QQQ="0.49")
    candidate,_ = preview(pub,SCOPE,config,trust["listings"],code_sha=CODE)
    assert candidate["inputs"]["inherited_policy"]["btc_governance"] == btc["btc_governance"]
    with pytest.raises(ValueError):
        preview(pub,SCOPE,config | {"btc_governance":{}},trust["listings"],code_sha=CODE)
    run_publish(pub,candidate,sign(key,candidate),trust,tmp_path)
    active = runtime.context(7,view="full")
    assert active["policy_reference"] == candidate["target"]
    assert {k:v for k,v in active["btc_governance"].items() if k!="source_clauses"} == {
        k:v for k,v in btc["btc_governance"].items() if k!="source_clauses"}
    initiator,_ = preview(pub,SCOPE,None,[],code_sha=CODE,rollback_to=v4["bundle_id"])
    run_publish(pub,initiator,sign(key,initiator),trust,tmp_path)
    assert runtime.context(7)["policy_reference"]==btc["policy_reference"]
    for row in originals:
        assert pub.store.read_bundle(SCOPE,row["bundle_id"]) == row
