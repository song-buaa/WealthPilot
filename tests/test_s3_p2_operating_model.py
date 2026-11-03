"""Synthetic boundary tests, never real-company business acceptance."""
from copy import deepcopy
from decimal import Decimal as D
import pytest

from tests.test_s3_b_quality_evidence import quality_assessment
from tests.test_s3_quality_valuation import request,params
from backend.services.market_data.financial_research import operating_ttm_bridge,build
from backend.services.market_data.quality_valuation import operating_scenario,scenario,compare
from backend.services.s3_operating_research import evaluate


def sources(symbol='AAA:US'):
    a=quality_assessment(symbol)
    template=next(r for r in a['quality_field_records'] if r['field']=='net_income')
    bases={'revenue':'total_revenue','net_income':'net_income_attributable_parent','operating_income':'operating_income'}
    for start,end,scope,fields in [
        ('2025-01-01','2025-12-31','annual',dict(revenue=1000,net_income=160,operating_income=200)),
        ('2025-01-01','2025-06-30','year_to_date',dict(revenue=300,net_income=50,operating_income=60)),
        ('2026-01-01','2026-06-30','year_to_date',dict(revenue=420))]:
        for f,v in fields.items():
            row=deepcopy(template);row.update(field=f,values={f:v},record_id=f'synthetic-{symbol}-{f}-{start}-{end}',
                fiscal_period_start=start,period=end,scope=scope,accounting_basis=bases[f])
            a['quality_field_records'].append(row)
    return a


def case():
    return dict(revenue_growth=['.1']*5,terminal_operating_margin='.3',terminal_conversion='.8',
        annual_share_change='0',terminal_pe='20',dividend_payout='0')


def fixture():
    assessments={s:sources(s) for s in ('AAA:US','BBB:US')};r=request()
    r['companies']={s:params('.1','20') for s in assessments}
    q=compare(r,assessments);profiles={}
    for c in q['cards']:
        c['comparison_status']='COMPARABLE'
        refs=list(c['quality']['evidence_catalog'])[:1]
        profiles[c['symbol']]=dict(cautious={**case(),'revenue_growth':['0']*5,'terminal_pe':'10'},base=case(),
            optimistic={**case(),'revenue_growth':['.3']*5,'terminal_pe':'30'},
            business_thesis='Synthetic company operating thesis; not a real issuer or verified investment forecast.',
            notes={k:dict(kind='RESEARCH_ASSUMPTION',rationale='Synthetic explicit operating assumption with qualified context, never a financial prediction.',evidence_refs=refs)
                for k in ('revenue','operating_margin','conversion','shares','terminal_pe')},
            risks=['Synthetic cycle risk','Synthetic cash risk'],review_conditions=['Review margins','Review revenue'],
            current_guidance='UNKNOWN_NO_QUALIFIED_CURRENT_GUIDANCE',
            source_notes=[dict(path='synthetic.txt',sha256='a'*64,url='https://www.sec.gov/synthetic',begin=0,end=1,excerpt_sha256='b'*64,
                summary='Synthetic source note only, not actual SEC financial evidence.',role='HISTORICAL_BUSINESS_CONTEXT')])
    d=dict(version='s3-p2.research-dossier.v1',parent_artifact_id='a'*64,research_recorded_at='2026-11-03T00:00:00Z',companies=profiles)
    return q,d,assessments


def test_owner_bridge_exact_same_window_and_no_eps_reconstruction():
    a=sources();before=deepcopy(a);b=operating_ttm_bridge(a)
    assert b['start']=='2025-07-01' and b['period']=='2026-06-30'
    assert {k:D(v['value']) for k,v in b['flows'].items()}==dict(revenue=1120,net_income=210,operating_income=260)
    assert b['eps']==next(r for r in build(a)['records'] if r['field']=='diluted_eps_ttm')
    assert a==before
    forged=deepcopy(b['eps']);forged['values']['diluted_eps_ttm']=100
    with pytest.raises(ValueError,match='OWNER_EPS'):operating_ttm_bridge(a,forged)


@pytest.mark.parametrize('fault',['prior_missing','fy_missing','prior_boundary','current_boundary','currency','unit','future','link_future','conflict','nonpositive'])
def test_bridge_rejects_bad_window_or_source(fault):
    a=sources();rows=a['quality_field_records']
    target=next(r for r in rows if r['field']=='revenue' and r['scope']=='annual')
    if fault=='prior_missing':a['quality_field_records']=[r for r in rows if not (r['field']=='revenue' and r['fiscal_period_start']=='2025-01-01' and r['scope']=='year_to_date')]
    elif fault=='fy_missing':rows.remove(target)
    elif fault=='prior_boundary':next(r for r in rows if r['field']=='revenue' and r['scope']=='year_to_date' and r['fiscal_period_start']=='2025-01-01')['period']='2025-06-29'
    elif fault=='current_boundary':next(r for r in rows if r['field']=='revenue' and r['scope']=='year_to_date' and r['fiscal_period_start']=='2026-01-01')['fiscal_period_start']='2026-01-02'
    elif fault=='currency':target['currency']='CNY'
    elif fault=='unit':target['unit']='millions'
    elif fault=='future':target['published_at']='2027-01-01T00:00:00Z'
    elif fault=='link_future':target['publication_links'][0]['fetched_at']='2027-01-01T00:00:00Z'
    elif fault=='conflict':
        duplicate=deepcopy(target);duplicate['values']['revenue']=1001;rows.append(duplicate)
    else:target['values']['revenue']=-1000
    with pytest.raises(ValueError):operating_ttm_bridge(a)


def test_operating_path_settlement_and_reverse_condition():
    b=operating_ttm_bridge(sources());p=case();p['terminal_operating_margin']=b['operating_margin'];p['terminal_conversion']=b['net_to_operating_conversion']
    value,compiled=operating_scenario(8,100,b,p,'.1');old=scenario(8,100,compiled,'.1')
    assert value['terminal_price']==old['terminal_price'] and value['dividends_cash']=='0.00000000'
    implied=value['implied_revenue_cagr_for_required_return'];p['revenue_growth']=[implied]*5
    inverted,_=operating_scenario(8,100,b,p,'.1')
    assert abs(D(inverted['annualized_return'])-D('.1'))<D('.00000002')
    # Repurchases affect only relative shares, not a second distribution term.
    p['annual_share_change']='-.01';repurchased,_=operating_scenario(8,100,b,p,'.1')
    assert D(repurchased['terminal_price'])>D(inverted['terminal_price']) and D(repurchased['dividends_cash'])==0


@pytest.mark.parametrize('fault',['payout','length','negative_growth','nan','margin','conversion','negative_price'])
def test_no_fake_operating_numbers(fault):
    b=operating_ttm_bridge(sources());p=case();price=100
    if fault=='payout':p['dividend_payout']='.1'
    elif fault=='length':p['revenue_growth'].pop()
    elif fault=='negative_growth':p['revenue_growth'][0]='-.51'
    elif fault=='nan':p['revenue_growth'][0]='NaN'
    elif fault=='margin':p['terminal_operating_margin']='0'
    elif fault=='conversion':p['terminal_conversion']='1.3'
    else:price=-1
    with pytest.raises((ValueError,ArithmeticError)):operating_scenario(8,price,b,p,'.1')


def test_repeatable_joint_rank_matches_full_dossier_recalculation():
    q,d,a=fixture();before=deepcopy((q,d,a));r=evaluate(q,d,a)
    assert r==evaluate(q,d,a) and (q,d,a)==before
    assert r['cards'][0]['assumption_context']['version']=='s3-p2.research-dossier.v1'
    c=r['cards'][0];assert c['quality']==q['cards'][0]['quality'] and len(c['joint_sensitivity'])==9
    variant=next(v for v in c['joint_sensitivity'] if v['revenue_growth_shift']=='.03' and v['terminal_pe_shift']=='.2')
    changed=deepcopy(d);changed['companies'][c['symbol']]['base']=variant['operating_parameters']
    actual=evaluate(q,changed,a)
    assert variant['after']=={c['symbol']:[c['group'],c['rank']] for c in actual['cards']}
    assert not r['execution_authorized'] and not r['order_created']


def test_single_company_missing_bridge_keeps_other_result_and_original_quality():
    q,d,a=fixture();a['AAA:US']['quality_field_records']=[]
    r=evaluate(q,d,a);assert r['comparable_count']==1
    bad=next(c for c in r['cards'] if c['symbol']=='AAA:US');good=next(c for c in r['cards'] if c['symbol']=='BBB:US')
    assert not bad['scenarios'] and bad['group'] is None and bad['rank'] is None and good['scenarios']


def test_forged_assumption_references_stay_unknown_not_silent_priority():
    q,d,a=fixture();d['companies']['AAA:US']['notes']['revenue']['evidence_refs']=['not-in-registry']
    r=evaluate(q,d,a);assert r['comparable_count']==1 and r['cards'][0]['failure_reason']=='P2_ASSUMPTION_EVIDENCE_REJECTED'


def test_major_quality_risk_blocks_even_high_returns():
    q,d,a=fixture();q['cards'][0]['quality']['major_risks']=['SYNTHETIC_CONFIRMED_MAJOR_RISK']
    c=evaluate(q,d,a)['cards'][0]
    assert c['group']=='暂不优先' and c['classification_reasons']['major_quality_risks']


def test_missing_joint_cell_independently_prevents_priority():
    q,d,a=fixture();b=operating_ttm_bridge(a['AAA:US']);profile=d['companies']['AAA:US']
    for name,g in [('cautious','.90'),('base','.98'),('optimistic','.99')]:
        profile[name].update(revenue_growth=[g]*5,terminal_operating_margin=b['operating_margin'],terminal_conversion=b['net_to_operating_conversion'],terminal_pe='30')
    c=evaluate(q,d,a)['cards'][0]
    assert c['scenarios'] and c['joint_qualification']['complete'] is False
    assert c['research_method_blockers']==['JOINT_SENSITIVITY_INCOMPLETE'] and c['group']!='优先考虑'
    assert c['sensitivity_complete'] and c['sensitivity_threshold_robust']
    assert 'JOINT_SENSITIVITY_INCOMPLETE' in c['rank_reason']
    assert c['classification_reasons']['research_method_blockers']==c['research_method_blockers']


def test_p2_diff_compares_actual_dossier_not_unchanged_parent_parameters(tmp_path):
    import json
    from backend.services.quality_valuation_research import digest,difference
    payload=dict(request=dict(companies={'AAA:US':{'old_p1':'unchanged'}}),assessments={},method_files={},
        dossier=dict(companies={'AAA:US':{'terminal_pe':'20'}}),source_files={},view_hashes={},
        result=dict(cards=[dict(symbol='AAA:US',group='暂不优先',rank=1)]))
    before=tmp_path/'before.json';after=tmp_path/'after.json'
    before.write_text(json.dumps(dict(artifact_id=digest(payload),payload=payload)))
    payload['dossier']['companies']['AAA:US']['terminal_pe']='18'
    after.write_text(json.dumps(dict(artifact_id=digest(payload),payload=payload)))
    diff=difference(before,after)
    assert diff['assumptions_changed'] and not diff['data_changed'] and not diff['pool_changed']
