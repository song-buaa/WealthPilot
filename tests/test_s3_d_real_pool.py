"""Synthetic boundaries only. Actual provider acceptance is frozen externally."""
from copy import deepcopy
from decimal import Decimal
import json
import pytest

from backend.services.s3_pool_research import compare_pool, candidate_parameters
from backend.services.s3_method_research import compare_methods
from backend.services.execution_plan.kline_provider import decode_av_daily, decode_av_splits
from tests.test_s3_c_method_applicability import source
from tests.test_s3_quality_valuation import request, params


def qualified_price():return {'status':'QUALIFIED_RAW_LISTED_CLOSE','price_date':'2026-10-29'}


def pool(symbols=('AAA:US','BBB:US')):
    r=request();r['companies']={s:None for s in symbols}
    a={s:source(s) for s in symbols}
    return r,a,{s:qualified_price() for s in symbols}


def test_candidates_are_explicit_repeatable_and_not_guidance_or_policy():
    r,a,p=pool();before=deepcopy(a)
    result,resolved=compare_pool(r,a,p)
    assert result==compare_pool(r,a,p)[0] and before==a
    assert result['comparable_count']==2
    assert not result['execution_authorized'] and not result['order_created']
    for card in result['cards']:
        c=card['assumption_context']
        assert c['management_guidance']['status']=='NOT_USED'
        assert set(c['parameters'])=={'earnings_growth','annual_share_change','terminal_pe','dividend_payout','required_return'}
        assert all(n['kind']=='RESEARCH_ASSUMPTION' and n['evidence_refs'] for n in c['parameters'].values())
        assert resolved.companies[card['symbol']]
    assert compare_pool(resolved,a,p)[0]==result


def test_missing_price_never_loses_generated_tsla_style_parameters():
    r,a,p=pool();p['BBB:US']={'status':'UNKNOWN','reason':'TTM_EPS_COMPONENTS_OR_LISTED_SHARE_BASIS_UNKNOWN'}
    result,resolved=compare_pool(r,a,p);cards={c['symbol']:c for c in result['cards']}
    assert result['comparable_count']==1
    assert cards['BBB:US']['assumptions'] and resolved.companies['BBB:US']
    assert not cards['BBB:US']['scenarios'] and cards['BBB:US']['rank'] is None
    assert cards['AAA:US']['comparison_status']=='COMPARABLE'
    assert compare_pool(resolved,a,p)[0]==result


def test_financial_company_never_enters_the_earnings_or_candidate_path():
    r,a,p=pool(('AAA:US','FIN:US'));a['FIN:US']=source('FIN:US','6331')
    result,_=compare_pool(r,a,p);c=next(c for c in result['cards'] if c['symbol']=='FIN:US')
    assert not c['scenarios'] and c['rank'] is None and c['assumptions'] is None
    assert c['quality']['capital_efficiency']['roe']['status']=='NOT_APPLICABLE'


def test_explicit_override_changes_values_and_within_group_rank_without_policy_changes():
    r,a,p=pool()
    base,resolved=compare_pool(r,a,p)
    modified=resolved.model_dump(mode='json')
    modified['companies']['AAA:US']=params('0','5')
    modified['assumption_context']['AAA:US']['version']='operator-stress-v2'
    for note in modified['assumption_context']['AAA:US']['parameters'].values():
        note['rationale']='Explicit synthetic stress assumptions for deterministic rank boundary test, not facts.'
    changed,_=compare_pool(modified,a,p)
    left=next(c for c in base['cards'] if c['symbol']=='AAA:US');right=next(c for c in changed['cards'] if c['symbol']=='AAA:US')
    assert left['scenarios']['base']!=right['scenarios']['base']
    assert (left['group'],left['rank'])!=(right['group'],right['rank'])
    assert str(resolved.downside_floor)=='-0.1' and str(resolved.tie_band)=='0.02'


@pytest.mark.parametrize('fault',['guidance','fact','unknown_ref','missing_note','fabricated_observation'])
def test_unqualified_assumption_metadata_is_not_an_investment_fact(fault):
    r,a,p=pool(('AAA:US',));_,resolved=compare_pool(r,a,p);r=resolved.model_dump(mode='json');c=r['assumption_context']['AAA:US']
    if fault=='guidance':c['management_guidance']['status']='VERIFIED'
    if fault=='fact':c['parameters']['earnings_growth']['kind']='GAAP_FACT'
    if fault=='unknown_ref':c['parameters']['earnings_growth']['evidence_refs']=['fabricated']
    if fault=='missing_note':c['parameters'].pop('terminal_pe')
    if fault=='fabricated_observation':c['facts'][0]['revenue_yoy']='100'
    result,_=compare_pool(r,a,p)
    assert result['comparable_count']==0 and not result['cards'][0]['scenarios']


def test_quality_warning_not_overridden_by_large_scenario_return():
    r,a,p=pool(('AAA:US',));a['AAA:US']['quality_field_records']=[x for x in a['AAA:US']['quality_field_records'] if x['field']!='sbc']
    _,resolved=compare_pool(r,a,p);r=resolved.model_dump(mode='json');r['companies']['AAA:US']=params('.3','40')
    result,_=compare_pool(r,a,p);c=result['cards'][0]
    assert Decimal(c['scenarios']['base']['annualized_return'])>Decimal('.1')
    assert c['group']=='继续观察' and c['quality']['priority_blockers']


def test_failed_company_keeps_other_comparable_results():
    r,a,p=pool();a.pop('BBB:US');result,_=compare_pool(r,a,p)
    assert result['comparable_count']==1 and len(result['cards'])==2
    assert next(c for c in result['cards'] if c['symbol']=='BBB:US')['rank'] is None


def daily():
    return {'Meta Data':{'2. Symbol':'AAA','3. Last Refreshed':'2026-10-30','5. Time Zone':'US/Eastern'},
        'Time Series (Daily)':{'2026-10-30':{'1. open':'100','2. high':'110','3. low':'90','4. close':'105','5. volume':'1000'}}}


@pytest.mark.parametrize('fault',['symbol','timezone','clock','adjusted','boolean','not_dict'])
def test_price_owner_strict_frozen_identity_does_not_accept_wrong_basis(fault):
    d=daily()
    if fault=='symbol':d['Meta Data']['2. Symbol']='BBB'
    if fault=='timezone':d['Meta Data']['5. Time Zone']='Asia/Shanghai'
    if fault=='clock':d['Meta Data']['3. Last Refreshed']='2027-01-01'
    if fault=='adjusted':d['Time Series (Daily)']['2026-10-30']['5. adjusted close']=d['Time Series (Daily)']['2026-10-30'].pop('5. volume')
    if fault=='boolean':d['Time Series (Daily)']['2026-10-30']['4. close']=True
    if fault=='not_dict':d=[]
    with pytest.raises((ValueError,KeyError,TypeError)):decode_av_daily(d,'AAA:US',strict_identity=True)


def test_daily_decoder_shared_with_legacy_owner_retains_raw_close():
    d=daily();r=decode_av_daily(d,'AAA:US',strict_identity=True)
    assert r.bars['close'].tolist()==[105] and r.source=='av'
    legacy=deepcopy(d);legacy.pop('Meta Data');assert decode_av_daily(legacy,'AAA:US').bars.equals(r.bars)


@pytest.mark.parametrize('payload',[{},[],{'symbol':'BBB','data':[]},{'symbol':'AAA','data':[{'effective_date':'2026-10-30','split_factor':'0'}]},
    {'symbol':'AAA','data':[{'effective_date':'2026-10-30','split_factor':'nan'}]}])
def test_split_owner_rejects_missing_wrong_or_invalid_source(payload):
    with pytest.raises((ValueError,KeyError,TypeError,ArithmeticError)):decode_av_splits(payload,'AAA:US')


def test_pool_has_fixed_precision_independent_of_callers_decimal_context():
    from decimal import localcontext
    r,a,p=pool();expected=compare_pool(r,a,p)[0]
    with localcontext() as context:
        context.prec=7
        assert compare_pool(r,a,p)[0]==expected


def test_price_flag_cannot_bridge_a_different_observation_day():
    r,a,p=pool();p['BBB:US']['price_date']='2026-10-28'
    result,_=compare_pool(r,a,p)
    assert result['comparable_count']==1
    c=next(c for c in result['cards'] if c['symbol']=='BBB:US')
    assert c['rank'] is None and c['research_input_failure']=='PRICE_DATE_QUALIFICATION_MISMATCH'


def test_frozen_roundtrip_and_exact_repeated_inputs_are_identical(monkeypatch,tmp_path):
    from decimal import getcontext,localcontext
    from backend.services import s3_pool_research as service
    from backend.services.quality_valuation_research import save_bundle,read_bundle
    r,a,p=pool()
    def frozen(root,request):
        assert getcontext().prec==34
        return deepcopy(a),{},deepcopy(p),{}
    monkeypatch.setattr(service,'frozen_assessments',frozen)
    with localcontext() as context:
        context.prec=7
        b,files=service.make_bundle(tmp_path,r)
    again,_=service.make_bundle(tmp_path,r)
    assert b==again
    path=save_bundle(tmp_path/'archive',b,files)/'bundle.json'
    assert read_bundle(path)==b and service.replay(path)['verified']
    assert save_bundle(tmp_path/'archive',again,files)==path.parent


@pytest.mark.parametrize('field',['management_guidance','parameters'])
def test_malformed_context_fails_only_one_company(field):
    r,a,p=pool();_,resolved=compare_pool(r,a,p);r=resolved.model_dump(mode='json')
    r['assumption_context']['AAA:US'][field]=[]
    result,_=compare_pool(r,a,p)
    assert result['comparable_count']==1 and len(result['cards'])==2
    assert next(c for c in result['cards'] if c['symbol']=='AAA:US')['rank'] is None
