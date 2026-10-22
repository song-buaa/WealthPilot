"""Automatically selected account plans on synthetic frozen inputs, no providers."""
from copy import deepcopy
from dataclasses import asdict

import pytest

from backend.services.position_analysis import prepare, exit_plan, native_quantity
from backend.services.exit_candidates import build_candidates
from tests.test_v42_exit_plan import setup_case
from tests.test_v42_evidence import AT, verified


def auto_case(symbol):
    view,executed,planner,target=setup_case(symbol)
    view['data_quality']['analysis_eligibility']['B']['allowed']=True
    view['views']['get_manual_wealth_items']['items'][0]['item_type']='consumer_loan'
    target['issuers'][0]['phases']=[.20,.125]
    return view,executed,planner,target


@pytest.mark.parametrize('symbol',['LI:US','PDD:US','3690:HK'])
def test_complete_account_candidates_without_manual_scenario_legs(symbol):
    view,executed,planner,target=auto_case(symbol)
    original=deepcopy(view)
    assessment=prepare(planner,executed,asset_view=view,options={'target_versions':[target]},at=AT)
    plan=exit_plan(assessment,at=AT)
    alternatives=plan['account_alternatives']
    assert alternatives
    actual=[row for candidate in alternatives for row in candidate['tranches'] if row.get('position_id')]
    assert actual and all(r['account_ref'] and r['funds_destination'] and r['currency'] in {'USD','HKD'} for r in actual)
    assert all(r['quantity'] is None and r['price_trigger'] is None for r in actual)
    assert all(r['amount_cny'] is not None for r in actual)
    for candidate in alternatives:
        assert not candidate['execution_authorized'] and not candidate['order_created']
        assert all(step['conservation_verified'] for step in candidate['scenario']['steps'])
    if symbol=='LI:US':
        assert len(alternatives)==6
        assert {r['funds_destination'] for r in actual}=={'offshore_fixed_income','repay_cny_loan'}
        assert {r['stage'] for r in actual}>={1,2,3}
    else:
        assert alternatives[0]['stage_policy']=='system_default_three_reviews'
        assert alternatives[0]['scenario']['steps'][-1]['company_values_cny'][plan['issuer_id']]<.011
    assert view==original


def test_unknown_assets_keep_directional_rows_and_no_precise_values():
    v,e,p,t=auto_case('3690:HK')
    v['data_quality']['analysis_eligibility']['B']['allowed']=False
    plan=exit_plan(prepare(p,e,asset_view=v,options={'target_versions':[t]},at=AT),at=AT)
    assert plan['account_alternatives']
    assert all(r.get('amount_cny') is None and r.get('quantity') is None
               for c in plan['account_alternatives'] for r in c['tranches'])


@pytest.mark.parametrize('ambiguous',[False,True])
def test_auto_repayment_never_selects_larger_mortgage_or_ambiguous_credit_loan(ambiguous):
    v,e,p,t=auto_case('3690:HK')
    items=v['views']['get_manual_wealth_items']['items']
    items.append({**items[0],'id':8,'item_type':'mortgage','current_value':10000000})
    if ambiguous:
        items.append({**items[0],'id':9})
    candidates=build_candidates(v,'MEITUAN',t,amount_qualified=True)
    if ambiguous:
        assert candidates[0]['scenario'] is None
        assert 'UNIQUE_CONSUMER_LOAN_SELECTION_REQUIRED' in candidates[0]['blockers']
        assert all(r['amount_cny'] is None for r in candidates[0]['tranches'])
    else:
        assert all(r['loan_item_id']==7 for r in candidates[0]['tranches'] if r.get('position_id'))


def test_broker_policy_and_snapshot_isolation():
    v,e,p,t=auto_case('LI:US')
    v['data_quality']['accounts']=[dict(account_ref='account_'+b,broker='snowball' if b=='ibkr' else b) for b in ('tiger','ibkr','guojin')]
    t['funding_policy']={'broker:tiger':'offshore_fixed_income','broker:ibkr':'offshore_fixed_income','broker:guojin':'repay_cny_loan'}
    plans=build_candidates(v,'LI_AUTO',t,amount_qualified=True)
    assert len(plans)==6
    assert all(leg['snapshot_id']==v['snapshot_id'] for c in plans for leg in c['legs'])
    assert all(s['conservation_verified'] for c in plans for s in c['scenario']['steps'])


def quantity_assessment(symbol='LI:US'):
    v,e,p,t=auto_case(symbol)
    a=prepare(p,e,asset_view=v,options={'target_versions':[t]},at=AT)
    a['qualification']['L3']['allowed']=True  # isolate native sizing from global gates
    a['records']=[dict(kind='quote',quality='valid',source='broker',record_id='quote:LI',data_as_of=AT.isoformat(),
        values={'current_price':20,'currency':'USD'})]
    unit=dict(source='broker_contract',record_id='contract',data_as_of=AT.isoformat(),fetched_at=AT.isoformat(),
        symbol='LI:US',currency='USD',quantity_increment=1,account_ref='account_tiger')
    cost={**unit,'record_id':'fee-estimate','account_ref':'account_tiger','total_cny':0}
    a['allocation_context']['security_inputs']=[dict(id=1,original_currency='USD',fx_rate_to_cny=7,fx_quality='current',
        trade_unit_evidence=unit,exit_cost_evidence=cost)]
    return a


def test_native_quantity_needs_fresh_account_unit_cost_fx_and_quote_evidence():
    a=quantity_assessment()
    step=dict(position_id=1,account_ref='account_tiger',sale_value_cny=14000,costs_cny=0,mode='qualified_static_scenario')
    assert native_quantity(a,step,at=AT)[0]==100
    for field in ('trade_unit_evidence','exit_cost_evidence','fx_rate_to_cny'):
        altered=deepcopy(a);altered['allocation_context']['security_inputs'][0].pop(field)
        assert native_quantity(altered,step,at=AT)[0] is None
    a['allocation_context']['security_inputs'][0]['trade_unit_evidence']['data_as_of']='2026-01-01'
    assert native_quantity(a,step,at=AT)[0] is None


def test_cumulative_native_quantities_cannot_oversell_a_position():
    a=quantity_assessment()
    member=a['allocation_context']['company_exposure']['members'][0]
    member['quantity']=150
    step=dict(position_id=1,issuer_id='LI_AUTO',account_ref='account_tiger',sale_value_cny=14000,
        costs_cny=0,destination='offshore_fixed_income')
    a.pop('exit_candidates',None)
    a['scenarios']={'mode':'qualified_static_scenario','steps':[step,step]}
    plan=exit_plan(a,at=AT)
    rows=[r for r in plan['tranches'] if r.get('position_id')==1]
    assert rows[0]['quantity']==100 and rows[1]['quantity'] is None
    assert 'CUMULATIVE_QUANTITY_EXCEEDS_VERIFIED_HOLDING' in rows[1]['quantity_blockers']


def test_server_confirmed_targets_enable_only_evidence_qualified_l3(tmp_path,monkeypatch):
    from backend.services.stage_targets import StageTargetStore
    from backend.services.position_analysis import report
    v,e,p,t=auto_case('LI:US')
    v['portfolio_id']=7
    v['data_quality']['can_propose_rebalance']=True
    e.loaded_data.rules={}
    e.loaded_data.retrieved_principles=['available']
    e.loaded_data.research_evidence=[verified('research'),verified('event',metadata={'evidence_kind':'event'})]
    market=asdict(e.market_data)
    market['fundamentals']=verified('fundamentals')
    market['quote']=verified('quote',current_price=20,currency='USD',price_basis='close')
    market['quote']['data_as_of']='2026-10-21'
    e.market_data=market
    store=StageTargetStore(tmp_path/'confirmed.json')
    candidate=store.create_candidate(t,portfolio_id=7,source='isolated synthetic target',at=AT)
    monkeypatch.setenv('WEALTHPILOT_STAGE_TARGETS_PATH',str(store.path))
    assert not prepare(p,e,asset_view=v,at=AT)['qualification']['L3']['allowed']
    store.activate(t['version'],portfolio_id=7,expected_digest=candidate['digest'],operator='fixture human',
        reference='isolated fixture confirmation',explicit_confirmation=True,at=AT)
    a=prepare(p,e,asset_view=v,at=AT)
    assert a['qualification']['L3']['allowed'],a['infoNeeded']
    plan=exit_plan(a,at=AT)
    assert plan['price_trigger'] and plan['target_allocation']['confirmation_status']=='confirmed'
    assert all(r.get('quantity') is None for c in plan['account_alternatives'] for r in c['tranches'])  # no unit/cost evidence
    text=report(a,plan,'trim')
    assert '服务端已确认目标版本' in text and '尚无可信用户确认' not in text
    v['data_quality']['can_propose_rebalance']=False
    assert not prepare(p,e,asset_view=v,at=AT)['qualification']['L3']['allowed']


def test_hong_kong_native_unit_is_separate_from_us_units_and_cny_valuation():
    a=quantity_assessment('3690:HK')
    a['symbol']='3690:HK'
    a['records'][0]['values']={'current_price':20,'currency':'HKD'}
    security=a['allocation_context']['security_inputs'][0]
    security.update(id=5,original_currency='HKD',fx_rate_to_cny=.92)
    security['trade_unit_evidence'].update(symbol='3690:HK',currency='HKD',quantity_increment=100,account_ref='account_guojin')
    security['exit_cost_evidence'].update(symbol='3690:HK',account_ref='account_guojin')
    step=dict(position_id=5,account_ref='account_guojin',sale_value_cny=14000,costs_cny=0,mode='qualified_static_scenario')
    assert native_quantity(a,step,at=AT)[0]==700
    security['trade_unit_evidence']['quantity_increment']=.5
    assert native_quantity(a,step,at=AT)[0] is None
    security['trade_unit_evidence']['quantity_increment']=100
    a['records'][0]['values']['currency']='CNY'
    assert native_quantity(a,step,at=AT)[0] is None


def test_li_automatically_compares_passive_concentration_after_other_exit_targets():
    v,e,p,t=auto_case('LI:US')
    plans=build_candidates(v,'LI_AUTO',t,amount_qualified=True)
    after=[c for c in plans if c.get('prerequisite_exits')]
    assert len(after)==3
    assert {leg['issuer_id'] for leg in after[0]['prerequisite_exits']}=={'MEITUAN','PDD_HOLDINGS'}
    prefix_size=len(after[0]['prerequisite_exits'])
    passive=after[0]['scenario']['steps'][prefix_size-1]
    assert passive['company_weights']['LI_AUTO']>v['issuer_exposure']['companies'][0]['market_value_cny']/1213000
    assert all(s['conservation_verified'] for c in after for s in c['scenario']['steps'])
    assert all(not c['execution_authorized'] for c in after)
