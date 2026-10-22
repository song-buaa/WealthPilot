"""Three-stock acceptance on synthetic inputs and original PEER composition."""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from backend.agents.contracts import PlanningOutput, ExecutionOutput, ExpressionOutput
from backend.services.position_analysis import prepare, exit_plan, report
from backend.services.market_data.output_policy import enforce
from tests.test_v42_evidence import AT, trusted_technical, verified
from tests.test_v42_scenarios import view, target, first_steps, leg


def setup_case(symbol):
    from backend.services.market_data.schema import MarketDataBundle, FundamentalsData
    v=view()
    identities={1:"LI:US",2:"LI:US",3:"2015.HGT:HK",4:"2015.SGT:HK",5:"3690.HGT:HK",6:"PDD:US"}
    for group in v["issuer_exposure"]["companies"]:
        for m in group["members"]:
            m.update(symbol=identities[m["id"]],quantity=1000)
            m["security_symbol"]="3690:HK" if m["id"]==5 else "2015:HK" if m["id"] in (3,4) else m["symbol"]
    if symbol=="LI:US":
        v["data_quality"]["security_inputs"].append(dict(id=1,security_symbol="LI:US",currency="USD",
            original_currency="USD",current_price=11.35,fx_rate_to_cny=7,fx_quality="current",market_value_cny=120000))
    loaded=SimpleNamespace(target_position=SimpleNamespace(symbol=symbol),research=[],retrieved_research_views=[],
        retrieved_principles=[],rules={"min_cash_pct":.2,"max_single_position":.4},
        rules_provenance={"basis":"configured_rule_file"})
    fund=FundamentalsData(symbol=symbol,pe_ttm=8.5 if symbol=="PDD:US" else 18.2,low_52w=13.2,
        data_as_of="seed:2026-06-11")
    market=MarketDataBundle(symbol=symbol,fundamentals=fund,technical=trusted_technical(symbol))
    executed=ExecutionOutput(loaded_data=loaded,market_data=market)
    plan=PlanningOutput(route="position_single",planner_trace=dict(status="fallback",fallback_type="JSONDecodeError"))
    t=target();t["issuers"]+=[dict(issuer_id="PDD_HOLDINGS",target_weight=0,direction="gradual_exit"),
                             dict(issuer_id="MEITUAN",target_weight=0,direction="gradual_exit")]
    t["funding_policy"]={"account_tiger":"offshore_fixed_income","account_guojin":"repay_cny_loan"}
    return v,executed,plan,t


@pytest.mark.parametrize("symbol,issuer",[("LI:US","LI_AUTO"),("PDD:US","PDD_HOLDINGS"),("3690:HK","MEITUAN")])
def test_three_stock_limited_exit_reports(symbol,issuer):
    v,executed,planner,t=setup_case(symbol)
    original=deepcopy(v)
    assessment=prepare(planner,executed,asset_view=v,
        options=dict(target_versions=[t],scenario_legs=first_steps()),at=AT)
    plan=exit_plan(assessment,at=AT)
    text=report(assessment,plan,"trim" if issuer=="LI_AUTO" else "exit")
    assert plan["issuer_id"]==issuer and plan["snapshot_id"]==v["snapshot_id"]
    assert plan["target_allocation"]["version"]==t["version"]
    assert not plan["execution_authorized"] and not plan["manual_trade_preparation_allowed"]
    assert plan["price_trigger"] is None
    assert all(row["quantity"] is None for row in plan["tranches"])
    assert all(marker in text for marker in ("①","②","③","④","⑤","⑥","⑦"))
    assert "Seed" in text and "复核" in text and "L4未授权" in text
    if issuer=="LI_AUTO":
        assert len(plan["account_holdings"])==4
        assert "PRICE_OUTSIDE_52W_RANGE" in assessment["conflicts"]
        assert "TARGET_DISCIPLINE_CASH_CONFLICT" in assessment["issues"]
    else:
        assert plan["tranches"][0]["stage"]=="operating_review"
    assert v==original


def test_qualified_plan_uses_reproducible_price_band_and_rule_quantity():
    from backend.services.market_data.evidence import assess
    v,executed,planner,t=setup_case("LI:US")
    v["data_quality"].update(can_propose_rebalance=True,analysis_eligibility={"B":dict(allowed=True)})
    v["data_quality"]["security_inputs"] += [dict(id=1,security_symbol="LI:US",original_currency="USD",
        fx_rate_to_cny=7,fx_quality="current",market_value_cny=120000)]
    data=asdict(executed.market_data)
    data["fundamentals"]=verified("fundamentals",pe_ttm=20,valuation_currency="USD")
    data["quote"]=verified("quote",current_price=20,currency="USD",price_basis="close")
    data["quote"]["data_as_of"]="2026-10-21"
    assessment=assess(data,symbol="LI:US",at=AT,asset_view=v,
        research=[verified("research")],events=[verified("event")],principles_available=True,
        planner=dict(status="success",fallback_type=None))
    from backend.services.allocation_scenarios import target_context, simulate, select_targets
    context=target_context(v,"LI_AUTO",[t],{},[],at=AT)
    context["security_inputs"]=v["data_quality"]["security_inputs"]
    assessment["allocation_context"]=context
    assessment["scenarios"]=simulate(v,[leg("LI_AUTO",1,"account_tiger","offshore_fixed_income",14000)],
        amount_qualified=True,target=select_targets([t],at=AT))
    result=exit_plan(assessment,at=AT)
    assert assessment["qualification"]["L3"]["allowed"]
    assert result["price_trigger"]["currency"]=="USD"
    row=[r for r in result["tranches"] if r["stage"]=="scenario"][0]
    assert row["quantity"]==100 and row["amount_cny"]==14000
    assert not result["manual_trade_preparation_allowed"]
    # A US observation band never becomes a Hong Kong channel's price trigger.
    assessment["scenarios"]=simulate(v,[leg("LI_AUTO",3,"account_guojin","repay_cny_loan",14000)],
        amount_qualified=True,target=select_targets([t],at=AT))
    cross=exit_plan(assessment,at=AT)
    cross_row=[r for r in cross["tranches"] if r["stage"]=="scenario"][0]
    assert cross_row["price_trigger"] is None and cross_row["quantity"] is None


def test_model_cannot_self_certify_indicators_or_invalid_confidence():
    from backend.services.market_data.evidence import assess
    data=dict(symbol="LI:US",technical=asdict(trusted_technical("LI:US")))
    data["technical"]["series"]["day"]["indicators"]["ma20"]=12345
    assessment=assess(data,at=AT)
    assert assessment["technical_levels"] is None
    output=ExpressionOutput(structured_payload={"confidence":float("nan"),"decisionType":["malformed"],
        "estimated_shares":99999,"limitPrice":99999},chat_answer="99999")
    enforce(output,assessment)
    assert output.structured_payload["confidence"]==0
    assert "99999" not in str(output.structured_payload)+output.chat_answer


def test_expired_context_downgrades_amounts_and_precise_fields():
    v,executed,planner,t=setup_case("LI:US")
    v["data_quality"].update(can_propose_rebalance=True,analysis_eligibility={"B":dict(allowed=True)})
    initial=prepare(planner,executed,asset_view=v,options=dict(target_versions=[t]),at=AT)
    assert initial["qualification"]["L2"]["allowed"]
    v["context_current"]=False
    final=prepare(planner,executed,asset_view=v,options=dict(target_versions=[t]),at=AT)
    assert not final["qualification"]["L2"]["allowed"]
    assert "ANALYSIS_CONTEXT_EXPIRED" in final["infoNeeded"]
    assert exit_plan(final,at=AT)["price_trigger"] is None


def test_scenario_request_is_bound_to_its_analysis_snapshot():
    from pocs.chatgpt_skills.analysis import AnalysisRequest
    raw=dict(question="fixture",snapshot_id="asset-view:v1:61",scenario_legs=first_steps())
    with pytest.raises(ValueError,match="CROSS_SNAPSHOT"):
        AnalysisRequest.model_validate(raw)


def test_original_professional_prompt_receives_issuer_targets_and_static_scenarios():
    from backend.services.market_data.output_policy import prompt_context
    v,executed,planner,t=setup_case("LI:US")
    assessment=prepare(planner,executed,asset_view=v,
        options=dict(target_versions=[t],scenario_legs=first_steps()),at=AT)
    text=prompt_context(assessment)
    assert "LI_AUTO" in text and "303800" in text
    assert t["version"] in text and "account_guojin" in text
    assert "illustrative_unqualified" in text
    assert "formal_discipline" in text and "system_defaults" in text
    assert "never orders" in text
