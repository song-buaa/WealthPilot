"""Public SEC notice authenticity + synthetic eligibility boundaries; no network."""
from copy import deepcopy
from pathlib import Path
import shutil
import pytest
from backend.services.market_data import method_applicability as method
from backend.services.s3_method_research import compare_methods
from backend.services.s3_pool_research import compare_pool
from backend.services.quality_valuation_research import markdown
from tests.test_s3_c_method_applicability import source
from tests.test_s3_quality_valuation import request,params

AT='2026-11-03T07:00:00+00:00'

def issuer(symbol='META:US',at=AT,cik='0001326801'):
    a=source(symbol);a['as_of']=at;a['method_identity']['cik']=cik
    a['quality_scope']['source_url']='https://data.sec.gov/submissions/CIK'+cik+'.json'
    a['technical_levels']['day']['end_at']='2026-11-02'
    return a


def test_reviewed_real_sec_notice_identity_bytes_and_clocks():
    a=issuer();before=deepcopy(a);r=method.applicability(a)
    assert a==before and r['quality_screen']['status']=='APPLICABLE'
    assert r['earnings_model']['status']=='INSUFFICIENT_EVIDENCE'
    n=r['return_unit_qualification'];assert len(n['evidence'])==2
    assert n['evidence'][0]['accession']=='0001628280-26-066700'
    assert n['evidence'][1]['accession']=='0001193125-26-438017'
    assert n['manifest_sha256']==method.NOTICE_MANIFEST_SHA256
    assert len(n['unresolved'])==3 and 'never' in n['release_condition']


@pytest.mark.parametrize('at,expected',[('2026-10-20T20:00:00Z','APPLICABLE'),('2026-10-22T20:00:00Z','INSUFFICIENT_EVIDENCE'),('2026-11-03T04:01:00Z','INSUFFICIENT_EVIDENCE'),('2027-02-01T20:00:00Z','INSUFFICIENT_EVIDENCE')])
def test_notice_does_not_backfill_or_expire_on_effective_date(at,expected):
    result=method.applicability(issuer(at=at));assert result['earnings_model']['status']==expected
    if at<'2026-10-21':assert 'return_unit_qualification' not in result
    if at in ['2026-10-22T20:00:00Z','2026-11-03T04:01:00Z']:
        n=result['return_unit_qualification'];assert n['evidence']==[] and 'unresolved' not in n
        assert n['reason']=='CAPITAL_STRUCTURE_NOTICE_NOT_QUALIFIED_AT_DECISION_AT'


@pytest.mark.parametrize('fault',['manifest','raw','deflate','missing','symlink'])
def test_known_notice_source_failure_cannot_reenable_valuation(tmp_path,monkeypatch,fault):
    for name in method.NOTICE_FILES:shutil.copyfile(method.NOTICE_ROOT/name,tmp_path/name)
    monkeypatch.setattr(method,'NOTICE_ROOT',tmp_path)
    p=tmp_path/(method.NOTICE_FILES[0] if fault=='manifest' else method.NOTICE_FILES[1])
    if fault=='deflate':
        p.write_bytes(p.read_bytes()[:10]+b'\xff'*20)
    elif fault in ['manifest','raw']:p.write_bytes(p.read_bytes()+b'tampered')
    else:
        p.unlink()
        if fault=='symlink':p.symlink_to(tmp_path/method.NOTICE_FILES[2])
    app=method.applicability(issuer());assert app['quality_screen']['status']=='APPLICABLE'
    assert app['earnings_model']['status']=='INSUFFICIENT_EVIDENCE'
    assert app['return_unit_qualification']['evidence']==[]


@pytest.mark.parametrize('symbol,cik',[('AAPL:US','0000320193'),('MSFT:US','0000789019'),('NVDA:US','0001045810'),('META:US','0000000001')])
def test_notice_bound_to_cik_not_ticker_or_global_scope(symbol,cik):
    r=method.applicability(issuer(symbol,cik=cik))
    assert r['earnings_model']['status']=='APPLICABLE' and 'return_unit_qualification' not in r


def test_direct_method_entry_rejects_high_return_override_and_keeps_quality():
    a=issuer();r=request();r.update(decision_at=AT,companies={'META:US':params('.3','40')})
    card=compare_methods(r,{'META:US':a})['cards'][0]
    assert card['quality']['financial_inputs'] and card['quality']['floors']
    assert card['comparison_status']=='NOT_COMPARABLE' and card['valuation_status']=='INSUFFICIENT_EVIDENCE'
    assert card['assumptions'] is None and card.get('assumption_context') is None and card['cross_checks']=={}
    assert card['scenarios']=={} and card['rank'] is None and card['group'] is None
    assert not {'conditional_value_range','formula'} & card.keys()


def test_pool_prevents_generated_or_supplied_parameters_and_preserves_others():
    symbols=['AAPL:US','MSFT:US','NVDA:US','META:US'];a={s:issuer(s,cik='0001326801' if s=='META:US' else '0000000001') for s in symbols}
    r=request();r.update(decision_at=AT,companies={s:None for s in symbols})
    p={s:dict(status='QUALIFIED_RAW_LISTED_CLOSE',price_date='2026-11-02') for s in symbols}
    result,resolved=compare_pool(r,a,p);assert result['comparable_count']==3
    c=next(x for x in result['cards'] if x['symbol']=='META:US')
    assert c['assumptions'] is None and c['assumption_context'] is None and resolved.companies['META:US'] is None
    assert 'META:US' not in resolved.assumption_context
    assert not c['scenarios'] and not c['cross_checks'] and c['rank'] is None
    assert not {'conditional_value_range','formula','high_sensitivity','sensitivity_span'} & c.keys()
    line=next(l for l in markdown(result).splitlines() if l.startswith('| META:US'))
    assert line.count('UNKNOWN')>=3
    baseline=deepcopy(r);baseline['companies'].pop('META:US')
    other,_=compare_pool(baseline,a,p)
    for card in other['cards']:
        actual=next(x for x in result['cards'] if x['symbol']==card['symbol'])
        for key in ['quality','scenarios','assumptions','sensitivity','rank','group']:assert actual[key]==card[key]
    assert compare_pool(resolved,a,p)[0]==result
    supplied=resolved.model_dump(mode='json');supplied['companies']['META:US']=params('.3','40');supplied['assumption_context']['META:US']={'fake':'no override'}
    changed,_=compare_pool(supplied,a,p);assert changed==result


@pytest.mark.parametrize('tamper',['none','delete_notice_and_promote','promote_status','inject_parent_price'])
def test_independent_verifier_requires_notice_even_after_outer_rehash(tmp_path,tamper):
    import json,hashlib
    import importlib.util
    script=Path(__file__).resolve().parents[1]/'scripts/s3_independent_recompute.py'
    spec=importlib.util.spec_from_file_location('meta_independent_verifier',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    verify,canonical=module.verify,module.canonical
    a=issuer();r=request();r.update(decision_at=AT,companies={'META:US':None})
    result=compare_methods(r,{'META:US':a});p=dict(result=result,request=r,assessments={'META:US':a},view_hashes={},source_files={})
    for name in method.NOTICE_FILES:
        relative='methods/research_v2/qualified_notices/'+name
        target=tmp_path/'sources'/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(method.NOTICE_ROOT/name,target)
        p['source_files'][relative]=hashlib.sha256(target.read_bytes()).hexdigest()
    card=result['cards'][0]
    if tamper=='delete_notice_and_promote':
        card['method_applicability'].pop('return_unit_qualification')
        card['method_applicability']['earnings_model']['status']='APPLICABLE'
    elif tamper=='promote_status':card['method_applicability']['earnings_model']['status']='APPLICABLE'
    elif tamper=='inject_parent_price':card['conditional_value_range']=['500','2000']
    b=dict(payload=p,artifact_id=hashlib.sha256(canonical(p).encode()).hexdigest())
    path=tmp_path/'bundle.json';path.write_text(json.dumps(b))
    if tamper=='none':assert verify(path)['independent']
    else:
        with pytest.raises(AssertionError):verify(path)
