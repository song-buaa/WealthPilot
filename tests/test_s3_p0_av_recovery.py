"""Synthetic boundaries for the observed quota/failed-SPLITS incident; no API calls."""
import hashlib
import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from backend.services.market_data import av_fundamentals_service as av
from backend.services.execution_plan.kline_provider import AVKlineProvider
from backend.services.s3_pool_capture import capture_pool
from tests.test_s3_d_real_pool import daily

DAILY = {'Information': 'Our standard API rate limit is 25 requests per day. Please subscribe to a premium plan.'}
MINUTE = {'Note': 'API call frequency exceeded: 75 requests per minute. Premium plans are available.'}
SPLITS = {'symbol': 'AAA', 'data': [{'effective_date': '2020-08-31', 'split_factor': '4.0000'}]}


@pytest.mark.parametrize('payload,status,scope', [
    (DAILY, 'rate_limited', 'daily'), (MINUTE, 'rate_limited', 'minute'),
    ({'Information': 'This is a premium endpoint.'}, 'premium', None),
    ({'Information': 'Premium research is useful; contact support.'}, 'provider_notice', None),
    ({'Error Message': 'The parameter apikey is invalid or missing.'}, 'invalid_key', None),
    ({'Information': 'Invalid API key PRIVATE_SENTINEL; premium plans exist.'}, 'invalid_key', None),
    ({'Error Message': 'Invalid API call.'}, 'provider_error', None),
    ({'_av_error': 'rate_limited'}, 'rate_limited', None),
    ({'symbol': 'AAA', 'data': []}, 'available', None),
    ({**DAILY, **SPLITS}, 'rate_limited', 'daily'),
])
def test_response_reason_is_not_a_premium_word_search(payload, status, scope):
    d = av.response_diagnostic('SPLITS', payload, 'PRIVATE_SENTINEL')
    assert d['status'] == status and d['rate_limit_scope'] == scope
    assert 'PRIVATE_SENTINEL' not in json.dumps(d)


class Response(io.BytesIO):
    status = 200
    def __init__(self, payload): super().__init__(json.dumps(payload).encode())
    def geturl(self): return 'https://www.alphavantage.co/query'


@pytest.mark.parametrize('payload,status', [(DAILY,'rate_limited'),(MINUTE,'rate_limited'),
    ({'Information':'API key is invalid'},'invalid_key'),({'Information':'Contact support'},'provider_notice')])
def test_non_access_failures_never_retry_compact(monkeypatch, payload, status):
    import backend.core.demo_mode as demo
    monkeypatch.setattr(demo, 'PUBLIC_DEMO_MODE', False)
    monkeypatch.setenv('ALPHA_VANTAGE_API_KEY', 'PRIVATE_SENTINEL')
    seen=[]
    def get(url, **kwargs):
        seen.append(parse_qs(urlsplit(url).query)); return Response(payload)
    monkeypatch.setattr(av.urllib.request, 'urlopen', get)
    p=AVKlineProvider(); assert p.get_kline('AAA:US', 'US') is None
    assert len(seen)==1 and seen[0]['outputsize']==['full']
    assert p.diagnostics['attempts'][0]['status']==status
    assert 'PRIVATE_SENTINEL' not in json.dumps(p.diagnostics)


def setup_capture(monkeypatch, tmp_path, splits, *, price=None):
    """Only SEC discovery is synthetic; original AV decoding/capture is executed."""
    from research_v2 import sec_disclosures as sec
    from backend.services import s3_method_research as methods
    from backend.services.instruments import issuers
    seed=tmp_path/'seed'; seed.mkdir()
    entry={}; requests=[]
    def source(kind,payload,url,**extra):
        path=seed/(kind+'.json'); path.write_text(json.dumps(payload))
        value=dict(path=path.name,url=url,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            fetched_at='2026-10-31T08:00:00+00:00',**extra)
        entry[kind]=value;requests.append(value)
    sub='https://data.sec.gov/submissions/CIK0000000001.json'
    source('directory',{'0':{'ticker':'AAA','cik_str':1}},sec.DIRECTORY)
    source('submissions',{},sub)
    source('companyfacts',{},'https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json')
    source('price',price or daily(),'https://www.alphavantage.co/query',symbol='AAA:US',function='TIME_SERIES_DAILY',mode='compact')
    source('splits',splits,'https://www.alphavantage.co/query',symbol='AAA:US',function='SPLITS')
    manifest=seed/'manifest.json';manifest.write_text(json.dumps(dict(schema_version='s3-d.sources.v1',companies={'AAA:US':entry},requests=requests)))
    before={p.name:p.read_bytes() for p in seed.iterdir()}
    monkeypatch.setattr(issuers,'sec_ticker_matches',lambda *a:[{'cik_str':1}])
    monkeypatch.setattr(methods,'identity_projection',lambda *a:({'submissions_url':sub}, {'issuer_type':'domestic','sic':'3571'},{}))
    monkeypatch.setattr(sec,'discover_filings',lambda *a:[])
    def forbidden(*a,**k):raise AssertionError('Unexpected SEC request')
    monkeypatch.setattr(sec,'_fetch',forbidden)
    monkeypatch.setenv('ALPHA_VANTAGE_API_KEY','PRIVATE_SENTINEL')
    return seed,before


def test_failed_frozen_splits_are_reacquired_and_old_artifact_untouched(monkeypatch,tmp_path):
    seed,before=setup_capture(monkeypatch,tmp_path,DAILY)
    seen=[]
    def get(url,**kwargs):
        q=parse_qs(urlsplit(url).query);seen.append(q);assert q['function']==['SPLITS'];return Response(SPLITS)
    monkeypatch.setattr(av.urllib.request,'urlopen',get)
    result=capture_pool(['AAA:US'],tmp_path/'new',reuse=seed)
    m=json.loads(result.read_text());e=m['companies']['AAA:US']
    assert len(seen)==1 and e['splits'].get('path') and e['splits_diagnostic']['status']=='available'
    assert e['reuse_diagnostics'][0]['action']=='REJECTED_REUSE_REACQUIRE'
    assert e['reuse_diagnostics'][0]['rate_limit_scope']=='daily'
    assert {p.name:p.read_bytes() for p in seed.iterdir()}==before
    assert 'PRIVATE_SENTINEL' not in result.read_text()


def test_fresh_error_is_audited_but_not_reusable_split_source(monkeypatch,tmp_path):
    seed,before=setup_capture(monkeypatch,tmp_path,DAILY)
    monkeypatch.setattr(av.urllib.request,'urlopen',lambda *a,**k:Response(DAILY))
    result=capture_pool(['AAA:US'],tmp_path/'new',reuse=seed)
    m=json.loads(result.read_text());e=m['companies']['AAA:US']
    assert not e['splits'].get('path') and e['splits']['status']=='rate_limited'
    assert e['splits_diagnostic']['rate_limit_scope']=='daily'
    assert any(r.get('function')=='SPLITS' and r.get('path') for r in m['requests'])
    from backend.services.s3_pool_research import _capture_diagnostics
    diag=_capture_diagnostics(e)
    assert any(r['kind']=='splits' and r['status']=='rate_limited' for r in diag['missing_sources'])


def test_valid_frozen_sources_do_not_fetch_or_refresh_clock(monkeypatch,tmp_path):
    seed,before=setup_capture(monkeypatch,tmp_path,SPLITS)
    def forbidden(*a,**k):raise AssertionError('Qualified source was fetched again')
    monkeypatch.setattr(av.urllib.request,'urlopen',forbidden)
    result=capture_pool(['AAA:US'],tmp_path/'new',reuse=seed)
    e=json.loads(result.read_text())['companies']['AAA:US']
    assert e['splits']['fetched_at']=='2026-10-31T08:00:00+00:00'
    assert {p.name:p.read_bytes() for p in seed.iterdir()}==before


def test_reuse_hash_or_identity_conflict_is_not_silently_replaced(monkeypatch,tmp_path):
    seed,before=setup_capture(monkeypatch,tmp_path,SPLITS)
    (seed/'splits.json').write_text('{}')
    def forbidden(*a,**k):raise AssertionError('Tampering retried a provider')
    monkeypatch.setattr(av.urllib.request,'urlopen',forbidden)
    result=capture_pool(['AAA:US'],tmp_path/'new',reuse=seed)
    assert json.loads(result.read_text())['companies']['AAA:US']['capture_failure']=='REUSE_SOURCE_HASH_MISMATCH'


@pytest.mark.parametrize('fault',['metadata_symbol','body_symbol','duplicate_event','negative_factor'])
def test_reused_split_identity_and_factor_conflicts_remain_blocking(monkeypatch,tmp_path,fault):
    seed,before=setup_capture(monkeypatch,tmp_path,SPLITS)
    p=seed/'manifest.json';m=json.loads(p.read_text())
    if fault=='metadata_symbol':m['companies']['AAA:US']['splits']['symbol']='BBB:US'
    else:
        raw=json.loads((seed/'splits.json').read_text())
        if fault=='body_symbol':raw['symbol']='BBB'
        elif fault=='duplicate_event':raw['data']*=2
        else:raw['data'][0]['split_factor']='-1'
        (seed/'splits.json').write_text(json.dumps(raw))
        m['companies']['AAA:US']['splits']['sha256']=hashlib.sha256((seed/'splits.json').read_bytes()).hexdigest()
    p.write_text(json.dumps(m))
    def forbidden(*a,**k):raise AssertionError('Identity/factor conflict silently retried')
    monkeypatch.setattr(av.urllib.request,'urlopen',forbidden)
    result=capture_pool(['AAA:US'],tmp_path/'new',reuse=seed)
    assert json.loads(result.read_text())['companies']['AAA:US'].get('capture_failure')


@pytest.mark.parametrize('kind',['price','splits'])
def test_fresh_request_failure_with_no_path_can_recover_next_capture(monkeypatch,tmp_path,kind):
    seed,before=setup_capture(monkeypatch,tmp_path,SPLITS)
    p=seed/'manifest.json';m=json.loads(p.read_text());m['companies']['AAA:US'][kind]={'status':'rate_limited'};p.write_text(json.dumps(m))
    import backend.core.demo_mode as demo
    monkeypatch.setattr(demo,'PUBLIC_DEMO_MODE',False)
    def get(url,**kwargs):
        q=parse_qs(urlsplit(url).query)
        return Response(SPLITS if q['function']==['SPLITS'] else daily())
    monkeypatch.setattr(av.urllib.request,'urlopen',get)
    result=capture_pool(['AAA:US'],tmp_path/'new',reuse=seed)
    assert json.loads(result.read_text())['companies']['AAA:US'][kind].get('path')


def test_http_429_split_refusal_retains_safe_owner_diagnostic(monkeypatch,tmp_path):
    from urllib.error import HTTPError
    seed,before=setup_capture(monkeypatch,tmp_path,DAILY)
    def limited(*a,**k):raise HTTPError('https://www.alphavantage.co/query?apikey=PRIVATE_SENTINEL',429,'limited',{},None)
    monkeypatch.setattr(av.urllib.request,'urlopen',limited)
    result=capture_pool(['AAA:US'],tmp_path/'new',reuse=seed);m=json.loads(result.read_text());e=m['companies']['AAA:US']
    assert e['splits']['status']=='rate_limited' and not e['splits'].get('path')
    assert m['requests'][-1]['http_status']==429
    assert 'PRIVATE_SENTINEL' not in result.read_text()


def test_fresh_split_identity_error_is_not_relabelled_as_network_failure(monkeypatch,tmp_path):
    seed,before=setup_capture(monkeypatch,tmp_path,DAILY)
    monkeypatch.setattr(av.urllib.request,'urlopen',lambda *a,**k:Response({'symbol':'BBB','data':[]}))
    result=capture_pool(['AAA:US'],tmp_path/'new',reuse=seed)
    e=json.loads(result.read_text())['companies']['AAA:US']
    assert not e['splits'].get('path') and e['splits']['status']=='invalid_response'
    assert e['splits_diagnostic']['qualification_error']=='AV_SPLITS_QUALIFICATION_REJECTED'
