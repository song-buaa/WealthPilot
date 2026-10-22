"""Synthetic v58-near static golden cases; never actual portfolio records."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from backend.services.allocation_scenarios import simulate, select_targets, target_context

AT=datetime(2026,10,22,tzinfo=timezone.utc)


def target():
    return dict(version="fixture-v1",stage="transition",confirmed_by_user=True,
        confirmed_at="2026-10-20T00:00:00+00:00",valid_until="2026-11-20T00:00:00+00:00",
        investment_cash_max=.01,issuers=[dict(issuer_id="LI_AUTO",target_weight=.25)])


def view():
    def group(issuer,rows):
        return dict(issuer_id=issuer,market_value_cny=sum(v for _,_,v in rows),
            members=[dict(id=i,account_ref=a,market_value_cny=v) for i,a,v in rows])
    return dict(snapshot_id="asset-view:v1:58",context_current=True,
        data_quality=dict(can_propose_rebalance=False,analysis_eligibility={"B":dict(allowed=False)},
            security_inputs=[dict(economic_asset_class="FIXED_INCOME",market_value_cny=228000),
                             dict(economic_asset_class="CASH",market_value_cny=10000)]),
        totals=dict(total_assets=1500000,total_liabilities=250000,net_worth=1250000),
        issuer_exposure=dict(investment_denominator_cny=1213000,companies=[
            group("LI_AUTO",[(1,"account_tiger",120000),(2,"account_ibkr",90000),
                             (3,"account_guojin",90000),(4,"account_guojin",3800)]),
            group("MEITUAN",[(5,"account_guojin",161400)]),
            group("PDD_HOLDINGS",[(6,"account_tiger",81500)])]),
        views={"get_manual_wealth_items":{"items":[dict(id=7,kind="LIABILITY",currency="CNY",
            current_value=250000,included_in_net_worth=True)]}})


def leg(issuer,position,account,destination,amount=None,weight=None,**kwargs):
    return dict(snapshot_id="asset-view:v1:58",issuer_id=issuer,position_id=position,account_ref=account,
        destination=destination,sale_value_cny=amount,target_weight=weight,fees_cny=0,tax_cny=0,fx_cost_cny=0,
        loan_item_id=7 if destination=="repay_cny_loan" else None,liquidity_floor_cny=1000,
        assumptions="Synthetic static CNY marking; zero costs assumed, transfer eligibility unverified",**kwargs)


def first_steps():
    return [leg("MEITUAN",5,"account_guojin","repay_cny_loan",161400),
            leg("PDD_HOLDINGS",6,"account_tiger","offshore_fixed_income",81500)]


@pytest.mark.parametrize("position,account,destination,expected",[
    (1,"account_tiger","offshore_fixed_income",40900),
    (2,"account_ibkr","offshore_fixed_income",40900),
    (3,"account_guojin","repay_cny_loan",54533.3333333333)])
def test_passive_li_concentration_and_different_funding_denominators(position,account,destination,expected):
    original=view();before=deepcopy(original)
    result=simulate(original,first_steps()+[leg("LI_AUTO",position,account,destination,weight=.25)],
        target=select_targets([target()],at=AT))
    assert result["mode"]=="illustrative_unqualified"
    second=result["steps"][1]
    assert second["investment_assets_cny"]==1051600
    assert second["company_weights"]["LI_AUTO"]==pytest.approx(303800/1051600)
    assert second["fixed_income_cny"]==309500
    assert "PASSIVE_OR_REMAINING_OVERWEIGHT:LI_AUTO" in second["warnings"]
    last=result["steps"][-1]
    assert last["sale_value_cny"]==pytest.approx(expected)
    assert last["company_weights"]["LI_AUTO"]==pytest.approx(.25)
    assert all(r["conservation_verified"] for r in result["steps"])
    assert last["net_assets_cny"]==1250000
    assert original==before  # No mutation of any frozen inputs / holding channels.


def test_costs_reduce_net_assets_and_repayment_uses_net_proceeds():
    legs=first_steps();legs[0].update(fees_cny=40,tax_cny=50,fx_cost_cny=10)
    result=simulate(view(),legs)
    first=result["steps"][0]
    assert first["liabilities_cny"]==88700
    assert first["net_assets_cny"]==1249900
    assert first["conservation_verified"]


@pytest.mark.parametrize("change,error",[
    ({"snapshot_id":"asset-view:v1:61"},"CROSS_SNAPSHOT"),
    ({"account_ref":"account_ibkr"},"ACCOUNT_ISSUER"),
    ({"issuer_id":"LI_AUTO"},"ACCOUNT_ISSUER"),
    ({"sale_value_cny":999999},"OUT_OF_RANGE"),
    ({"loan_item_id":99},"CNY_LOAN"),
    ({"liquidity_floor_cny":20000},"LIQUIDITY"),
])
def test_invalid_scenarios_fail_closed(change,error):
    raw=first_steps()[0];raw.update(change)
    with pytest.raises(ValueError,match=error):
        simulate(view(),[raw])


def test_reusing_same_position_cannot_oversell_and_debt_cap_is_enforced():
    raw=first_steps()[0]
    with pytest.raises(ValueError,match="OUT_OF_RANGE"):
        simulate(view(),[raw,raw])
    v=view();v["views"]["get_manual_wealth_items"]["items"][0]["current_value"]=10
    with pytest.raises(ValueError,match="EXCEEDS_DEBT"):
        simulate(v,[raw])


def test_targets_versioned_confirmed_expiring_separate_from_formal_rules():
    t=target();rules={"min_cash_pct":.2,"max_single_position":.4}
    context=target_context(view(),"LI_AUTO",[t],rules,[dict(default=True)],at=AT)
    assert context["confirmed_stage_targets"]["investment_cash_max"]==.01
    assert context["formal_discipline"]==rules
    assert context["warnings"]==["TARGET_DISCIPLINE_CASH_CONFLICT"]
    changed={**t,"investment_cash_max":.02}
    with pytest.raises(ValueError,match="CONTENT_CONFLICT"):
        select_targets([t,changed],at=AT)
    later={**changed,"version":"fixture-v2","confirmed_at":"2026-10-21T00:00:00+00:00"}
    with pytest.raises(ValueError,match="LINEAGE"):
        select_targets([t,later],at=AT)
    later["supersedes"]=t["version"]
    assert select_targets([t,later],at=AT)["version"]=="fixture-v2"
    assert select_targets([t],at=datetime(2027,1,1,tzinfo=timezone.utc)) is None
    with pytest.raises(ValueError):
        select_targets([{**t,"confirmed_by_user":False}],at=AT)


def test_single_symbol_d1_permission_cannot_bless_other_denominator_assets():
    from tests.test_d1_asset_quality import quality_view, AT as QUALITY_AT
    from app.asset_views import qualify_view
    from backend.services.allocation_scenarios import qualification_view
    v=quality_view()
    other=deepcopy(v["data_quality"]["security_inputs"][0])
    other.update(id=99,symbol="PDD:US",security_symbol="PDD:US",price_as_of=None)
    v["data_quality"]["security_inputs"].append(other)
    qualify_view(v,at=QUALITY_AT,symbol="LI:US")
    assert v["data_quality"]["analysis_eligibility"]["B"]["allowed"]
    v["data_quality"]["can_propose_rebalance"]=False
    widened=qualification_view(v,at=QUALITY_AT)
    assert not widened["data_quality"]["analysis_eligibility"]["B"]["allowed"]
    assert not widened["data_quality"]["can_propose_rebalance"]
    assert v["data_quality"]["analysis_eligibility"]["B"]["allowed"]


def test_default_rule_file_provenance_cannot_claim_formal_confirmation(tmp_path,monkeypatch):
    from app.discipline import config
    monkeypatch.setattr(config,"_RULES_FILE",tmp_path/"absent.json")
    result=config.describe_effective_rules(config._DEFAULT_RULES)
    assert result["basis"]=="system_default_fallback"
    assert result["user_confirmation"]=="not_inferred"
    import json
    config._RULES_FILE.write_text(json.dumps(config._DEFAULT_RULES))
    assert config.describe_effective_rules(config._DEFAULT_RULES)["basis"]=="configured_rule_file"
    assert config.describe_effective_rules({"different":True})["basis"]=="config_changed_or_fallback"
