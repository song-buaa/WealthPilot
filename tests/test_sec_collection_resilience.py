"""Offline native collect regression, reusing the hash-verified public 6-K."""
from copy import deepcopy
import io
import json
from unittest.mock import Mock

import pytest

from research_v2 import sec_disclosures as sec
from tests.test_sec_real_original import original, metadata, AT, EXPECTED
from tests.test_sec_financial_sources import directory
from backend.services.market_data.evidence import assess


def test_network_read_has_no_fixed_byte_ceiling(monkeypatch):
    monkeypatch.setenv('WEALTHPILOT_SEC_ORGANIZATION','Synthetic Offline Operator')
    monkeypatch.setenv('WEALTHPILOT_SEC_CONTACT_EMAIL','offline-fixture@operator.org')
    body=b'x'*(16*1024*1024+1)
    response=Mock();response.geturl.return_value=sec.DIRECTORY;response.status=200
    response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
    response.read=Mock(wraps=io.BytesIO(body).read)
    opener=Mock();opener.open.return_value=response
    monkeypatch.setattr(sec.urllib.request,'build_opener',lambda *a:opener)
    source=sec._fetch(sec.DIRECTORY)
    assert source[0]==body
    response.read.assert_called_once_with()
    assert opener.open.call_args.kwargs['timeout']==8


def test_parser_accepts_original_larger_than_previous_ceiling():
    # Padding is explicitly synthetic; unchanged genuine fixture is tested below.
    issuer,filing=metadata()
    body=original()+b' '*(16*1024*1024)
    record=sec.parse_filing((body,AT.isoformat()),issuer,filing,identity_digest='a'*64)
    assert {o['source_field']:o['value']*o['unit_scale'] for o in record['financial_observations']
            if o['fiscal_period_end']=='2026-06-30'}==EXPECTED


@pytest.mark.parametrize('failure',['network','invalid_original','parse_exception','large_file','partial_preserved'])
def test_native_collect_prioritizes_quarter_and_preserves_real_four_fields(monkeypatch,failure):
    from backend.core import demo_mode
    from backend.services.market_data import cache
    monkeypatch.setattr(demo_mode,'PUBLIC_DEMO_MODE',False)
    monkeypatch.delenv('AV_DEV_MOCK',raising=False)
    monkeypatch.setattr(cache,'_cache',cache.TTLCache())
    issuer,q2=metadata()
    # Synthetic Submissions metadata drives the normal resolver/discovery; Q2
    # parsing consumes the exact original 520,544 public bytes, never injected facts.
    sub=dict(cik=issuer['cik'],name=issuer['name'],tickers=['LI'],filings=dict(recent={
        'accessionNumber':['0001104659-26-101067',q2['accession'],'0001104659-26-101066'],
        'form':['6-K','6-K','20-F'],
        'filingDate':['2026-10-27',q2['filed'],'2026-08-25'],
        'reportDate':['2025-12-31','2026-06-30','2026-08-25'],
        'primaryDocument':['annual.htm',q2['primary_url'].rsplit('/',1)[1],'event.htm'],
        'acceptanceDateTime':['2026-10-27T12:00:00Z',q2['published_at'],'2026-08-25T12:00:00Z']}))
    recent=sub['filings']['recent']
    for n in range(8):
        additions=dict(accessionNumber=f'0001104659-26-2000{n:02}',form='6-K',
            filingDate=f'2026-09-{28-n:02}',reportDate=f'2026-09-{28-n:02}',
            primaryDocument=f'event{n}.htm',acceptanceDateTime=f'2026-09-{28-n:02}T12:00:00Z')
        for key,value in additions.items():recent[key].insert(n+1,value)
    calls=[]
    def fetch(url):
        calls.append(url)
        if url==sec.DIRECTORY:return json.dumps(directory()).encode(),AT.isoformat()
        if '/submissions/' in url:return json.dumps(sub).encode(),AT.isoformat()
        if '/companyfacts/' in url:return json.dumps({'cik':1791706,'facts':{}}).encode(),AT.isoformat()
        if url==q2['full_url']:return original(),AT.isoformat()
        if failure=='network':
            sec._diagnose(url,'timeout',attempted=True)
            return None
        if failure=='large_file':return b'x'*(16*1024*1024+1),AT.isoformat()
        return b'invalid filing',AT.isoformat()
    monkeypatch.setattr(sec,'_fetch',fetch)
    if failure=='partial_preserved':
        preserved=sec.parse_filing((original(),AT.isoformat()),issuer,q2,identity_digest='a'*64)
        preserved['financial_observations']=[o for o in preserved['financial_observations']
            if o['source_field']=='totalRevenue']
        monkeypatch.setattr(sec,'parse_companyfacts',lambda *a,**kw:[preserved])
    native_parse=sec.parse_filing
    def parse(source,issuer,filing,**kwargs):
        if (failure=='parse_exception' and filing['accession']!=q2['accession']) or failure=='partial_preserved':
            raise ValueError('SYNTHETIC_PRIVATE_MESSAGE')
        return native_parse(source,issuer,filing,**kwargs)
    monkeypatch.setattr(sec,'parse_filing',parse)
    rows,verifier=sec.collect('LI:US')
    assert calls[12]==q2['full_url']
    assert len(calls)==13  # quarterly facts survive; annual original is no prerequisite
    assessment=assess({},symbol='LI:US',research=deepcopy(rows),at=AT,disclosure_verifier=verifier)
    fields=assessment['verified_field_records']
    expected_fields=({'revenue_quarter'} if failure=='partial_preserved' else
                     {'revenue_quarter','net_income_quarter','operating_cash_flow_quarter','capital_expenditures_quarter'})
    assert {f['field'] for f in fields}==expected_fields
    assert all(f['period']=='2026-06-30' for f in fields)
    assert all(not assessment['qualification'][level]['allowed'] for level in ('L2','L3','L4'))
    assert not any('PRIVATE' in str(d) for d in sec.source_diagnostics())
    before=len(calls)
    assert sec.collect('LI:US')[0]==rows and len(calls)==before


def test_complete_companyfacts_quarter_cannot_skip_newer_8K(monkeypatch):
    from backend.core import demo_mode
    from backend.services.market_data import cache
    from tests.test_sec_financial_sources import facts, submissions
    monkeypatch.setattr(demo_mode,'PUBLIC_DEMO_MODE',False)
    monkeypatch.delenv('AV_DEV_MOCK',raising=False)
    monkeypatch.setattr(cache,'_cache',cache.TTLCache())
    sub=submissions('AAPL')
    newer=dict(accessionNumber='0001104659-26-200001',form='8-K',filingDate='2026-10-20',
        reportDate='2026-10-20',primaryDocument='newer.htm',acceptanceDateTime='2026-10-20T12:00:00Z')
    for key,value in newer.items():sub['filings']['recent'][key].insert(0,value)
    source,_,_=facts()
    calls=[]
    def fetch(url):
        calls.append(url)
        if url==sec.DIRECTORY:return json.dumps(directory()).encode(),AT.isoformat()
        if '/submissions/' in url:return json.dumps(sub).encode(),AT.isoformat()
        if '/companyfacts/' in url:return source
        return b'unsupported newer event',AT.isoformat()
    monkeypatch.setattr(sec,'_fetch',fetch)
    rows,_=sec.collect('AAPL:US')
    assert len(calls)==4 and calls[-1].endswith('/0001104659-26-200001.txt')
    assert {o['source_field'] for r in rows for o in r['financial_observations']}==set(EXPECTED)
