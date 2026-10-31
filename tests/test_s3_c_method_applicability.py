"""Synthetic boundaries only; actual SEC acceptance is frozen separately."""
from copy import deepcopy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.services.s3_method_research import compare_methods, identity_projection
from backend.services.market_data.method_applicability import applicability
from backend.services.instruments.issuers import resolve_sec_issuer, sec_ticker_matches
from tests.test_s3_b_quality_evidence import quality_assessment
from tests.test_s3_quality_valuation import request, params


def source(symbol='AAA:US', sic='3571'):
    a=quality_assessment(symbol)
    a['quality_scope'].update(sic=sic,source_url='https://data.sec.gov/submissions/CIK0000000001.json')
    a['method_identity']=dict(symbol=symbol,cik='0000000001',sec_ticker=symbol[:-3],
        registered_equity_tickers=[symbol[:-3]],submissions_tickers=[symbol[:-3]],
        directory_sha256='b'*64,submissions_sha256='a'*64)
    return a


def run(a, p=None):
    r=request();r['companies']={a['symbol']:p or params('.3','40')}
    return compare_methods(r,{a['symbol']:a})['cards'][0]


def test_nonfinancial_complete_evidence_reuses_quality_and_scenario_model():
    a=source();before=deepcopy(a);c=run(a)
    assert c==run(a) and a==before
    assert c['comparison_status']=='COMPARABLE' and c['group']=='优先考虑'
    assert not c['quality']['priority_blockers']
    assert c['method_applicability']['quality_screen']['status']=='APPLICABLE'


@pytest.mark.parametrize('sic',['6000','6331','6999'])
def test_financial_positive_earnings_never_enter_ordinary_model(sic):
    c=run(source(sic=sic))
    assert c['valuation_status']=='NOT_APPLICABLE' and c['group'] is None
    assert not c['scenarios'] and 'financial_inputs' not in c['quality']
    assert c['quality']['fundamental_deterioration']['status']=='NOT_ESTABLISHED'
    assert c['method_applicability']['alternative_method']['status']=='INSUFFICIENT_EVIDENCE'
    assert c['quality']['capital_efficiency']['roe']['status']=='NOT_APPLICABLE'


@pytest.mark.parametrize('fault',['sic','scope_clock','scope_hash','scope_url','class','identity','directory','issuer','utility'])
def test_source_and_identity_gaps_do_not_create_comparable_returns(fault):
    a=source()
    if fault=='sic':a['quality_scope'].pop('sic')
    if fault=='scope_clock':a['quality_scope']['fetched_at']='2027-01-01T00:00:00+00:00'
    if fault=='scope_hash':a['quality_scope'].pop('source_document_sha256')
    if fault=='scope_url':a['quality_scope']['source_url']='https://untrusted.invalid/submissions'
    if fault=='class':a['method_identity']['registered_equity_tickers'].append('AAA-A')
    if fault=='identity':a['method_identity']['symbol']='WRONG:US'
    if fault=='directory':a['method_identity'].pop('directory_sha256')
    if fault=='issuer':a['quality_scope']['issuer_type']='foreign_private'
    if fault=='utility':a['quality_scope']['sic']='4911'
    c=run(a)
    assert not c['scenarios'] and c['group'] is None and c['rank'] is None


@pytest.mark.parametrize('fault',['unit','currency','period','source_conflict','negative_eps','zero_eps','future_fact'])
def test_unqualified_or_nonpositive_financial_inputs_cannot_create_value(fault):
    a=source();rows=a['verified_field_records']+a['historical_field_records']
    if fault=='source_conflict':a['source_conflicts']=['conflict']
    elif fault in {'negative_eps','zero_eps'}:
        for row in rows:row['values'][row['field']]=-1 if fault=='negative_eps' else 0
    elif fault=='future_fact':rows[0]['fetched_at']='2027-01-01T00:00:00+00:00'
    elif fault=='period':rows[0]['fiscal_period_start']='2027-01-01'
    else:rows[0][fault]='CNY' if fault=='currency' else 'millions'
    c=run(a)
    assert not c['scenarios'] and c['group'] is None


def test_mixed_pool_partial_failure_keeps_companies_and_does_not_reward_pe_presence():
    r=request();r['companies']={'AAA:US':params('.3','40'),'FIN:US':params('.3','40'),'MISS:US':None}
    a={'AAA:US':source(),'FIN:US':source('FIN:US','6331')}
    result=compare_methods(r,a)
    assert result==compare_methods(r,a)
    cards={c['symbol']:c for c in result['cards']}
    assert cards['AAA:US']['rank']==1
    assert all(cards[s]['rank'] is None and cards[s]['group'] is None for s in ('FIN:US','MISS:US'))
    assert result['ranking']['not_comparable']==['FIN:US','MISS:US']


def test_no_assumptions_preserves_quality_without_inventing_forecast():
    r=request();r['companies']={'AAA:US':None}
    c=compare_methods(r,{'AAA:US':source()})['cards'][0]
    assert c['quality']['floors']['financial_sustainability']['status']=='通过'
    assert not c['scenarios'] and c['assumptions'] is None and c['group'] is None


def test_all_inapplicable_pool_has_no_investment_tier():
    r=request();r['companies']={'FIN:US':None}
    c=compare_methods(r,{'FIN:US':source('FIN:US','6331')})['cards'][0]
    assert c['group'] is None and c['comparison_status']=='NOT_COMPARABLE'


def test_sec_registry_dot_class_identity_does_not_assert_eps_conversion():
    directory={'0':dict(ticker='DUAL-A',cik_str=1),'1':dict(ticker='DUAL-B',cik_str=1)}
    sub=dict(cik=1,name='Synthetic dual issuer',tickers=['DUAL-A','DUAL-B'],sic='6331',filings=dict(recent=dict(form=['10-K'])))
    assert resolve_sec_issuer('DUAL.B:US',directory,sub)['cik']=='0000000001'
    _,scope,i=identity_projection('DUAL.B:US',(json.dumps(directory).encode(),'2026-10-01T00:00:00+00:00'),(json.dumps(sub).encode(),'2026-10-01T00:00:00+00:00'))
    assert i['sec_ticker']=='DUAL-B' and i['registered_equity_tickers']==['DUAL-A','DUAL-B']
    assert 'ordinary_shares_per_ads' not in i and 'conversion_ratio' not in i
    directory['2']=dict(ticker='DUAL.B',cik_str=2)
    assert resolve_sec_issuer('DUAL.B:US',directory,sub) is None
    assert not sec_ticker_matches('DUAL.B:HK',directory)


def test_frozen_bundle_read_replay_and_independent_process(tmp_path):
    from backend.services.quality_valuation_research import digest, view_hashes, save_bundle, read_bundle
    from backend.services.s3_method_research import replay
    r=request();r['companies']={'AAA:US':params('.3','40'),'FIN:US':None}
    assessments={'AAA:US':source(),'FIN:US':source('FIN:US','6331')}
    result=compare_methods(r,assessments)
    payload=dict(request=r,assessments=assessments,input_failures={},method_files={},source_files={},
                 result=result,view_hashes=view_hashes(result))
    b=dict(artifact_id=digest(payload),payload=payload)
    path=save_bundle(tmp_path,b,{})/'bundle.json'
    assert read_bundle(path)==b and replay(path)['verified']
    completed=subprocess.run([sys.executable,'scripts/s3_independent_recompute.py',str(path)],capture_output=True,text=True)
    assert completed.returncode==0,completed.stderr
    assert json.loads(completed.stdout)['scenario_and_sensitivity_cases']==11
    # Content-addressed data and output views are both immutable.
    cardpath=path.parent/'cards/AAA_US.json';cardpath.write_text('{}')
    with pytest.raises(ValueError,match='ARCHIVED_VIEW_HASH_MISMATCH'):read_bundle(path)


@pytest.mark.parametrize('failure',['missing_facts','future_submissions','url_conflict','raw_tamper'])
def test_supplement_source_qualification_and_partial_identity(tmp_path,failure):
    from backend.services.s3_method_research import make_bundle
    import hashlib
    def raw(path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
        return hashlib.sha256(path.read_bytes()).hexdigest()
    root=tmp_path/'p1';official=tmp_path/'official'
    r=request();r['companies']={'FIN:US':None}
    r['source_manifest_sha256']=raw(root/'P1_ACCEPTANCE_MANIFEST.json',dict(decision_at=r['decision_at'],symbols={}))
    raw(root/'SEC_ONLY_HANDOFF/requests.json',[])
    d=dict(ticker='FIN',cik_str=1)
    sub=dict(cik=1,name='Synthetic financial issuer',tickers=['FIN'],sic='6331',filings=dict(recent=dict(form=['10-K'])))
    entry={}
    for kind,url,value in [('directory','https://www.sec.gov/files/company_tickers.json',{'0':d}),
          ('submissions','https://data.sec.gov/submissions/CIK0000000001.json',sub)]:
        name=kind+'.json';sha=raw(official/name,value)
        entry[kind]=dict(path=name,sha256=sha,url=url,fetched_at='2026-10-01T00:00:00+00:00')
    entry['companyfacts']=dict(status='FAILED')
    if failure=='future_submissions':entry['submissions']['fetched_at']='2027-01-01T00:00:00+00:00'
    if failure=='url_conflict':entry['submissions']['url']='https://data.sec.gov/submissions/CIK0000000002.json'
    if failure=='raw_tamper':(official/'submissions.json').write_text('{}')
    r['official_manifest_sha256']=raw(official/'manifest.json',dict(companies={'FIN:US':entry}))
    b,_=make_bundle(root,r,official);c=b['payload']['result']['cards'][0]
    assert not c['scenarios'] and c['group'] is None
    if failure=='missing_facts':
        assert c['method_applicability']['earnings_model']['status']=='NOT_APPLICABLE'
        assert c['source_failure']=='SEC_COMPANYFACTS_SOURCE_UNAVAILABLE'
    else:
        assert c['method_applicability']['earnings_model']['status']=='INSUFFICIENT_EVIDENCE'
