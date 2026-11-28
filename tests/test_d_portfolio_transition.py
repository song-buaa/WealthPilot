"""TEST_ONLY frozen PRD §7 oracle and D/B3/C compatibility checks."""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.services.allocation_scenarios import TransitionRejected, TransitionRequest
from backend.services.allocation_service import project_frozen_transition
from backend.services.investment_policy.contracts import digest
from backend.services.investment_policy.etf_observation import ETFObservation
from backend.services.investment_policy.parser import PolicyError
from tests.test_allocation_snapshot_errors import client  # noqa: F401
from tests.test_c_concentration import AT, STAMP, TESLA, active, frozen, holdings, position, view  # noqa: F401

D = Decimal


def action(kind, ident, amount, **fields):
    return {"kind": kind, "action_id": ident, "amount": str(amount), "fee": "0", "outcome": "APPLIED",
            "assumption": "TEST_ONLY constant marks, fees and settlement as specified", **fields}


def stage(label, *actions):
    return {"label": label, "actions": list(actions)}


@pytest.fixture
def oracle(frozen, active):
    active.ensure_current = lambda: None
    q = frozen["data_quality"]
    q["security_inputs"][0]["market_value_cny"] = "1191400"
    for ticker, value, currency, vehicle, economic in [
        ("PDD:US", "77400", "USD", "ADR", "EQUITY"),
        ("3690:HK", "151650", "HKD", "COMMON_STOCK", "EQUITY"),
        ("2015:HK", "52950", "HKD", "COMMON_STOCK", "EQUITY"),
        ("2015:HK", "246624.18", "HKD", "COMMON_STOCK", "EQUITY"),
        ("TSLA:US", "85542.52", "USD", "COMMON_STOCK", "EQUITY"),
        ("AAPL:US", "288318.80", "USD", "COMMON_STOCK", "EQUITY"),
        ("IB01:LSE", "228000", "USD", "ETF", "FIXED_INCOME"),
        ("SPY:US", "60914.50", "USD", "ETF", "EQUITY"),
    ]:
        row = position(frozen, ticker, value, currency=currency, vehicle=vehicle)
        row.update(fx_rate_to_cny="1", economic_asset_class=economic)
        # Separate legacy arithmetic testing from v3 target semantics.
        if ticker.endswith((":HK", ":LSE")):
            row.update(price_time_basis="close", price_as_of=AT.date().isoformat())
    frozen["views"]["get_manual_wealth_items"]["items"].append(
        dict(id=1, kind="liability", item_type="loan", currency="CNY", current_value="1699000",
             effective_included_in_net_worth=True, value_as_of=AT.date().isoformat(), last_verified_at=STAMP))
    frozen["totals"].update(total_assets="1191400", total_liabilities="1699000")
    q["accounts"].append({**q["accounts"][0], "account_ref": "account_fixture_2"})
    # B3 needs real target identity for the fixture's Alphabet/TSLA holdings.
    raw = deepcopy(active.context["snapshot"])
    direct = raw["allocation"]["direct_equity"]
    weights = list(direct.values())
    direct.clear()
    direct.update(zip(["AAPL", "TSLA", "GOOG", "MSFT", "META", "BRK.B", "unallocated"], weights))
    from backend.services.investment_policy.contracts import parse_snapshot
    policy = parse_snapshot(raw)
    active.context["snapshot"] = policy.model_dump(mode="json")
    active.context["policy_reference"]["structured_sha256"] = digest(policy)

    stages = [
        stage("S1", action("SELL_TO_CASH", "pdd", 77400, position_ref="position:2"),
              action("BUY_WITH_CASH", "buy", 77400, cash_ref="action:pdd", symbol="IB01:LSE")),
        stage("S2", action("SELL_TO_CASH", "meituan", 151650, position_ref="position:3"),
              action("TRANSFER_CASH", "mt_cny", 151650, cash_ref="action:meituan",
                     destination_account="account_fixture_2", destination_currency="CNY", destination_per_source="1"),
              action("REPAY_DEBT_FROM_CASH", "repay_mt", 151650, cash_ref="action:mt_cny", loan_ref="wealth:1")),
        stage("S3", action("SELL_TO_CASH", "li", 52950, position_ref="position:4"),
              action("TRANSFER_CASH", "li_cny", 52950, cash_ref="action:li",
                     destination_account="account_fixture_2", destination_currency="CNY", destination_per_source="1"),
              action("REPAY_DEBT_FROM_CASH", "repay_li", 52950, cash_ref="action:li_cny", loan_ref="wealth:1")),
    ]
    paths = {"A": stages, "B": deepcopy(stages)}
    paths["B"][0]["actions"][1]["outcome"] = "FAILED"
    return frozen, active, paths


def run(oracle, **options):
    source, policy, paths = oracle
    value = TransitionRequest.model_validate(dict(paths=paths, snapshot_id=source["snapshot_id"],
        snapshot_sha256=digest(source), policy_bundle_sha256="a"*64, **options))
    return project_frozen_transition(7, source, value, session=policy, at=AT)


def test_prd_exact_s0_through_s3_with_actual_b3_and_c(oracle):
    before = deepcopy(oracle[0])
    value = run(oracle)
    assert value["basis"] == "QUALIFIED_BASELINE_SCENARIO", value["baseline_issues"]
    a, b = value["paths"]["A"], value["paths"]["B"]
    for index, (i, loan, fixed) in enumerate([
        ("1191400", "1699000", "228000"), ("1191400", "1699000", "305400"),
        ("1039750", "1547350", "305400"), ("986800", "1494400", "305400"),
    ]):
        row = a[index]
        assert D(row["investment_assets_cny"]) == D(i)
        assert D(row["liabilities_cny"]) == D(loan)
        assert D(row["net_assets_cny"]) == -D("507600")
        assert D(row["allocation"]["secondary"]["ib01"]["amount_cny"]) == D(fixed)
        tsla = row["concentration"]["issuers"][TESLA]
        expected = D("85542.52") * 100 / D(i)
        assert D(tsla["known_lower_bound_pct"]).quantize(D(".0001")) == expected.quantize(D(".0001"))
        assert tsla["policy_hits"] == []
        assert row["cash_flow_conservation_verified"]
    assert TESLA in a[2]["newly_breached_issuers"] and TESLA not in a[3]["newly_breached_issuers"]
    assert a[2]["concentration"]["issuers"][TESLA]["scenario_risk_status"] == "HUMAN_REVIEW"
    assert D(a[0]["concentration"]["issuers"][TESLA]["repayment_threshold"]["r_trigger_cny"]) == D("122118.50")
    assert D(b[3]["allocation"]["secondary"]["ib01"]["amount_cny"]) == D("228000")
    assert D(b[3]["allocation"]["secondary"]["cash"]["amount_cny"]) == D("77400")
    assert (D(b[3]["allocation"]["secondary"]["ib01"]["current_ratio"]) * 100).quantize(D(".0001")) == D("23.1050")
    assert (D(b[3]["allocation"]["secondary"]["cash"]["current_ratio"]) * 100).quantize(D(".0001")) == D("7.8435")
    assert b[1]["actions"][1]["blocked_reason"] == "SCENARIO_ACTION_FAILED"
    assert not value["execution_authorized"] and not value["order_created"] and oracle[0] == before


def test_in_flight_fx_then_arrival_one_lot_and_excluded_reserve(oracle):
    source, _, paths = oracle
    manual = source["views"]["get_manual_wealth_items"]["items"]
    manual.append(dict(id=2, kind="asset", item_type="bank_cash", currency="CNY", current_value="100",
                       value_as_of=AT.date().isoformat(), last_verified_at=STAMP, effective_included_in_net_worth=True))
    evidence = source["data_quality"]["investable_scope_evidence"]
    evidence["coverage"]["manual_assets"].update(status="PRESENT", asset_refs=["wealth:2"])
    evidence["coverage"]["deduplication"]["asset_refs"].append("wealth:2")
    evidence["dispositions"]["wealth:2"] = dict(state="EXCLUDED", reason="DEBT_RESERVE", evidence_refs=["test:reserve"])
    source["totals"]["total_assets"] = "1191500"
    transfer = dict(destination_account="account_fixture_2", destination_currency="CNY", destination_per_source=".9")
    paths.clear()
    paths["A"] = [
        stage("S1", action("SELL_TO_CASH", "sale", 100, position_ref="position:2", fee="2"),
              action("TRANSFER_CASH", "sent", 97, cash_ref="action:sale", outcome="IN_FLIGHT", fee="1", **transfer),
              action("REPAY_DEBT_FROM_CASH", "blocked", 30, cash_ref="action:sent", loan_ref="wealth:1")),
        stage("S2", action("TRANSFER_CASH", "arrival", 97, cash_ref="action:sent", **transfer),
              action("REPAY_DEBT_FROM_CASH", "repay", 30, cash_ref="action:arrival", loan_ref="wealth:1")),
        stage("S3", action("REPAY_DEBT_FROM_CASH", "reserve", 100, cash_ref="wealth:2", loan_ref="wealth:1"),
              action("REPAY_DEBT_FROM_CASH", "salary", 10, external_cashflow="SALARY_CASHFLOW_REPAY", loan_ref="wealth:1")),
    ]
    _, departure, arrival, after = run(oracle)["paths"]["A"]
    assert departure["cash"]["action:sent"]["state"] == "TRANSFER_IN_FLIGHT"
    assert "action:arrival" not in departure["cash"]
    assert departure["actions"][-1]["blocked_reason"] == "SCENARIO_CASH_NOT_AVAILABLE"
    assert D(departure["investment_assets_cny"]) == D("1191397")
    assert D(arrival["cash"]["action:arrival"]["amount"]) == D("57.3")
    assert "action:sent" not in arrival["cash"]
    assert D(arrival["cumulative_fx_effect_cny"]) == D("-9.7")
    assert D(arrival["investment_assets_cny"]) == D("1191357.3")
    assert after["investment_assets_cny"] == arrival["investment_assets_cny"]
    assert D(after["liabilities_cny"]) == D("1698860")
    assert D(after["net_assets_cny"]) == -D("507502.7")


def test_unknown_real_denominator_cannot_be_promoted(oracle):
    oracle[0]["data_quality"].pop("investable_owner_evidence")
    value = run(oracle, assumed_denominator_cny="1191400")
    stage = value["paths"]["A"][2]
    assert value["basis"] == "ILLUSTRATIVE_ASSUMPTION"
    assert stage["concentration"]["status"] == "CONDITIONAL_RISK"
    detail = stage["concentration"]["issuers"][TESLA]
    assert detail["known_lower_bound_pct"] is None and detail["policy_hits"] == []
    assert D(detail["illustrative_lower_bound_pct"]) > 8
    assert not stage["newly_breached_issuers"]


@pytest.mark.parametrize("amount, error", [("77401", "INSUFFICIENT_CASH"), ("2000000", "INSUFFICIENT_CASH")])
def test_cash_and_loan_limits(oracle, amount, error):
    oracle[2]["A"][0]["actions"][1]["amount"] = amount
    with pytest.raises(TransitionRejected, match=error):
        run(oracle)
    oracle[2]["A"][0]["actions"][1]["amount"] = "77400"
    oracle[0]["views"]["get_manual_wealth_items"]["items"][0]["current_value"] = "100"
    with pytest.raises(TransitionRejected, match="REPAYMENT_EXCEEDS"):
        run(oracle)


def test_etf_reuses_formal_scope_and_separate_observed_projection(oracle, monkeypatch):
    from backend.services.investment_policy import concentration_adapter
    spy = holdings("SPY:US", {"GOOG:US": ".02", "GOOGL:US": ".03"})
    observation = ETFObservation.model_validate(dict(symbol="SPY:US", holdings_as_of=AT.date(), known_at=AT,
        source="https://example.invalid/test", source_sha256="c"*64, parser_version="test", issuer_version="test",
        listing_sha256="d"*64, issuer_weights_pct={"SEC_CIK_0001652044": "20"}, disclosure_rows=3,
        unmapped_equity_weight_pct="1", disclosed_weight_pct="99"))
    monkeypatch.setattr(concentration_adapter, "_read_etf_evidence",
        lambda *a, **k: ({"SPY:US": spy}, {"SPY:US": digest(spy)}, None, {"SPY:US": D("100")}, {"SPY:US": observation}))
    source, _, paths = oracle
    source["data_quality"]["security_inputs"][-1].update(quantity="60914.50", fx_rate_to_cny="1")
    paths["A"][0]["actions"][1]["symbol"] = "SPY:US"
    result = run(oracle)
    a0, a1, *_ = result["paths"]["A"]
    issuer = "SEC_CIK_0001652044"
    assert D(a0["concentration"]["issuers"][issuer]["indirect_known_cny"]) == D("3045.725")
    assert a0["concentration"]["issuers"][issuer]["indirect_known_cny"] == a1["concentration"]["issuers"][issuer]["indirect_known_cny"]
    assert a1["concentration"]["issuers"][issuer]["proven_upper_bound_pct"] is None
    est = a1["concentration"]["etf_observation"]["issuers"][issuer]
    assert D(est["indirect_observed_cny"]["SPY:US"]) == (D("60914.50") + D("77400")) * D(".20")
    assert est["indirect_observed_cny"]["QQQ:US"] is None
    assert source["data_quality"]["security_inputs"][-1]["symbol"] == "SPY:US"


def test_request_binding_and_web_peer_use_same_service(oracle, client, monkeypatch):
    import app.asset_views
    from backend.graph.tools import call_tool
    from backend.services.investment_policy import runtime, store
    source, policy, paths = oracle
    monkeypatch.setattr(runtime, "current", lambda _: policy)
    monkeypatch.setattr(store, "utc_now", lambda: AT.isoformat())
    monkeypatch.setattr(app.asset_views, "load_view", lambda *a: source)
    payload = dict(paths=paths, snapshot_id=source["snapshot_id"], snapshot_sha256=digest(source), policy_bundle_sha256="a"*64)
    response = client.post("/api/allocation/simulate-transition", json=payload)
    assert response.status_code == 200, response.text
    assert response.json() == call_tool("simulate_portfolio_transition", request=payload)
    assert client.post("/api/allocation/simulate-transition", json={**payload, "snapshot_sha256": "9"*64}).status_code == 409
    with pytest.raises(PolicyError, match="POLICY_VERSION_CONFLICT"):
        call_tool("simulate_portfolio_transition", request={**payload, "policy_bundle_sha256": "9"*64})


def test_configuration_v3_scoped_hit_has_no_formal_event(oracle):
    from tests.test_policy_v3_targets import AMENDMENT_APPROVAL, parse, raw_amendment
    source, session, _ = oracle
    policy = parse(raw_amendment(AMENDMENT_APPROVAL))
    session.context["snapshot"] = policy.model_dump(mode="json")
    ref = session.context["policy_reference"]
    ref.update(bundle_version=3, structured_sha256=digest(policy))
    session.context.update(source_hashes=policy.source_hashes, release=dict(
        bundle_id=ref["bundle_id"], scope=ref["scope"], bundle_sha256=ref["bundle_sha256"],
        synthetic=False, environment="ISOLATED_REHEARSAL"))
    result = run(oracle)
    s0 = result["paths"]["A"][0]["allocation"]
    assert s0["strategic"]["growth_and_alternatives"]["target_ratio"] == "0.7"
    assert s0["strategic"]["stable_pool"]["target_ratio"] == "0.3"
    assert {"NVDA", "ANT"} <= {key.split("（")[0] for key in s0["direct_equity"]["issuers"]}
    scoped = s0["strategic_review"]
    assert scoped["status"] == "SCENARIO_REVIEW_THRESHOLD_HIT"
    assert set(scoped["scenario_hit_classes"]) == {"growth_and_alternatives", "stable_pool"}
    assert not scoped["triggered_classes"] and "review_id" not in scoped

    source["data_quality"]["investable_scope_evidence"]["coverage"]["accounts"]["evidence_refs"] += [
        "position:1:US_DIRECT_EQUITY", "position:1:US_DIRECT_EQUITY:source:fixture"]
    # Keep nonzero conflicting cash in the original source row.
    cash = source["data_quality"]["security_inputs"][0]
    cash["market_value_cny"] = "1"
    partial = run(oracle)["paths"]["A"][0]
    assert partial["allocation"]["status"] == "PARTIAL"
    assert "B3_CLASSIFICATION_UNVERIFIED" in partial["concentration"]["issues"]
    assert partial["cash"]["position:1"]["state"] == "UNKNOWN"



def test_apple_to_alphabet_and_malformed_scope_remains_research(oracle):
    source, _, paths = oracle
    paths.clear()
    paths["A"] = [stage("Swap", action("SELL_TO_CASH", "apple", 100, position_ref="position:7"),
        action("BUY_WITH_CASH", "goog", 100, cash_ref="action:apple", symbol="GOOG:US"))]
    s0, after = run(oracle)["paths"]["A"]
    assert after["investment_assets_cny"] == s0["investment_assets_cny"]
    assert D(after["concentration"]["issuers"]["SEC_CIK_0001652044"]["direct_cny"]) == 100
    assert D(after["concentration"]["issuers"]["SEC_CIK_0000320193"]["direct_cny"]) == D("288218.80")

    source["data_quality"]["investable_scope_evidence"]["coverage"]["accounts"]["evidence_refs"] = "invalid-list"
    unknown = run(oracle, assumed_denominator_cny="1191400", assumed_fx_to_cny={"USD": "1"},
                  assumed_amounts_cny={"position:7": "288318.80"})["paths"]["A"][-1]
    assert unknown["allocation"]["status"] == "PARTIAL"
    assert unknown["concentration"]["status"] == "CONDITIONAL_RISK"
