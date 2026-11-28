"""Synthetic parser/contract boundaries, not real-company acceptance."""
from copy import deepcopy
from decimal import Decimal
import pytest
from research_v2 import sec_disclosures as sec
from tests.test_s3_p2_operating_model import fixture
from backend.services.s3_operating_research import evaluate


def annual(monkeypatch):
    monkeypatch.setattr(sec,'verify_filing_envelope',lambda *a:dict(source_ref='https://www.sec.gov/source',source_sha256='a'*64,published_at='2026-02-01T10:00:00+00:00',fetched_at='2026-11-03T00:00:00+00:00'))
    issuer=dict(cik='123',issuer_type='domestic',registered_equity_tickers=['AAA'])
    filing=dict(form='10-K',report_date='2025-12-31',filed='2026-02-01',primary_url='https://www.sec.gov/main.htm',accession='synthetic')
    body='''<DOCUMENT><TYPE>10-K
<FILENAME>main.htm
<SEQUENCE>1
<html xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:us-gaap="http://fasb.org/us-gaap/2025" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">
<xbrli:context id="annual"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">123</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:unit id="per-share"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>
<ix:nonFraction name="us-gaap:EarningsPerShareDiluted" contextRef="annual" unitRef="per-share" decimals="2">7.25</ix:nonFraction></html></DOCUMENT>'''
    return body,issuer,filing


def test_asfiled_current_year_annual_only(monkeypatch):
    body,issuer,filing=annual(monkeypatch)
    result=sec.asfiled_annual_eps((body.encode(),'2026-11-03T00:00:00Z'),issuer,filing)
    assert Decimal(result['diluted_eps'])==Decimal('7.25')
    assert result['period']=='2025-12-31' and result['status']=='ASFILED_ANNUAL_NOT_TTM_OR_NORMALIZED'


@pytest.mark.parametrize('fault',['currency','unit','namespace','context_namespace','cik','period','duration','segment','negative','nan','continuation','conflict','classes','future_report','scale','fake_measure','mixed_unit','duplicate_identifier','bad_grouping','double_negative','explicit_plus_sign','envelope'])
def test_asfiled_rejects_unsupported_vintage_or_unit(monkeypatch,fault):
    body,issuer,filing=annual(monkeypatch)
    if fault=='currency':body=body.replace('iso4217:USD','iso4217:CNY')
    elif fault=='unit':body=body.replace('xbrli:shares','xbrli:pure')
    elif fault=='namespace':body=body.replace('http://fasb.org/us-gaap/2025','https://fake.invalid/us-gaap/2025')
    elif fault=='context_namespace':body=body.replace('http://www.xbrl.org/2003/instance','https://fake.invalid/instance')
    elif fault=='cik':body=body.replace('>123<','>999<')
    elif fault=='period':filing['report_date']='2024-12-31'
    elif fault=='duration':body=body.replace('2025-01-01','2025-10-01')
    elif fault=='segment':body=body.replace('</xbrli:entity>','<xbrli:segment>dimension</xbrli:segment></xbrli:entity>')
    elif fault=='negative':body=body.replace('>7.25<','>-7.25<')
    elif fault=='nan':body=body.replace('>7.25<','>NaN<')
    elif fault=='continuation':body=body.replace('decimals="2"','decimals="2" continuedAt="elsewhere"')
    elif fault=='conflict':body=body.replace('</html>','<ix:nonFraction name="us-gaap:EarningsPerShareDiluted" contextRef="annual" unitRef="per-share">8.25</ix:nonFraction></html>')
    elif fault=='classes':issuer['registered_equity_tickers'].append('BBB')
    elif fault=='future_report':filing['filed']='2025-12-30'
    elif fault=='scale':body=body.replace('decimals="2"','decimals="2" scale="10000000"')
    elif fault=='fake_measure':body=body.replace('<xbrli:measure>','<fake:measure xmlns:fake="https://fake.invalid/instance">').replace('</xbrli:measure>','</fake:measure>')
    elif fault=='mixed_unit':body=body.replace('</xbrli:divide>','</xbrli:divide><xbrli:measure>iso4217:USD</xbrli:measure>')
    elif fault=='duplicate_identifier':body=body.replace('</xbrli:entity>','<xbrli:identifier scheme="http://www.sec.gov/CIK">999</xbrli:identifier></xbrli:entity>')
    elif fault=='bad_grouping':body=body.replace('>7.25<','>7,,2.5<')
    elif fault=='double_negative':body=body.replace('decimals="2"','decimals="2" sign="-"').replace('>7.25<','>-7.25<')
    elif fault=='explicit_plus_sign':body=body.replace('decimals="2"','decimals="2" sign="+"')
    elif fault=='envelope':monkeypatch.setattr(sec,'verify_filing_envelope',lambda *a:None)
    with pytest.raises(ValueError):sec.asfiled_annual_eps((body.encode(),'2026-11-03T00:00:00Z'),issuer,filing)


def test_future_entitlement_keeps_quality_but_withholds_valuation_and_rank():
    q,d,a=fixture();before=deepcopy(q)
    code='KNOWN_FUTURE_CLASS_DISTRIBUTION_REQUIRES_ENTITLEMENT_BRIDGE'
    d['companies']['AAA:US']['return_unit_qualification']=code
    result=evaluate(q,d,a);first=next(c for c in result['cards'] if c['symbol']=='AAA:US')
    assert first['quality']==before['cards'][0]['quality']
    assert first['comparison_status']=='NOT_COMPARABLE' and first['scenarios']=={} and first['rank'] is None
    assert first['failure_reason']==code and result['comparable_count']==1
    assert result==evaluate(q,d,a) and q==before


def test_refusal_card_and_render_do_not_inherit_parent_target_prices():
    from backend.services.quality_valuation_research import markdown
    from backend.services.s3_operating_research import OperatingScenario
    q,d,a=fixture()
    parent=next(c for c in q['cards'] if c['symbol']=='AAA:US')
    parent.update(conditional_value_range=['1234567.89','9876543.21'],formula='STALE_PARENT_FORMULA',
                  assumption_context=dict(status='STALE_PARENT_PROJECTION'))
    d['companies']['AAA:US']['return_unit_qualification']='KNOWN_FUTURE_CLASS_DISTRIBUTION_REQUIRES_ENTITLEMENT_BRIDGE'
    result=evaluate(q,d,a);card=next(c for c in result['cards'] if c['symbol']=='AAA:US')
    assert card['assumptions'] is None
    assert not {'conditional_value_range','formula','assumption_context'} & card.keys()
    assert card['unexecuted_operating_profile']['status']=='UNEXECUTED_NOT_CURRENT_VALUATION'
    assert OperatingScenario.model_validate(card['unexecuted_operating_profile']['profile']['base'])==OperatingScenario.model_validate(d['companies']['AAA:US']['base'])
    assert card['historical_benchmark_ref']['parent_artifact_id']==d['parent_artifact_id']
    rendered=markdown(result)
    assert all(value not in rendered for value in ['1234567.89','9876543.21','STALE_PARENT_FORMULA','STALE_PARENT_PROJECTION'])


@pytest.mark.parametrize('fault,reason',[('none',None),('future_bar','HISTORICAL_PRICE_NOT_CLOSED_AT_RESEARCH_CLOCK'),('split','SPLIT_AFFECTED_FISCAL_TO_PRICE_WINDOW'),('source_future','P2_CALIBRATION_SOURCE_HASH_OR_CLOCK'),('ohlcv','HISTORICAL_OHLCV_REJECTED')])
def test_calibration_price_clock_split_and_ohlcv(tmp_path,monkeypatch,fault,reason):
    import json,hashlib
    from backend.services import s3_method_research
    from backend.services.s3_operating_research import calibration_context,Profile
    _,d,_=fixture();profile=deepcopy(d['companies']['AAA:US']);entry={}
    def put(kind,data,**extra):
        body=json.dumps(data).encode();path=kind+'.json';(tmp_path/path).write_bytes(body)
        entry[kind]=dict(path=path,sha256=hashlib.sha256(body).hexdigest(),fetched_at='2026-11-03T03:00:00Z',**extra)
    put('directory',{});put('submissions',{})
    day='2026-11-03' if fault=='future_bar' else '2026-11-02'
    put('price',{'Meta Data':{'2. Symbol':'AAA','5. Time Zone':'US/Eastern','3. Last Refreshed':day},'Time Series (Daily)':{day:{'1. open':'100','2. high':'1' if fault=='ohlcv' else '110','3. low':'90','4. close':'105','5. volume':'10000'}}},function='TIME_SERIES_DAILY',symbol='AAA:US')
    put('splits',{'symbol':'AAA','data':[{'effective_date':'2026-06-01','split_factor':'2'}] if fault=='split' else []},function='SPLITS',symbol='AAA:US')
    if fault=='source_future':entry['price']['fetched_at']='2026-11-04T00:00:00Z'
    raw=b'synthetic annual document';(tmp_path/'annual.txt').write_bytes(raw);sha=hashlib.sha256(raw).hexdigest()
    url='https://www.sec.gov/Archives/synthetic.txt'
    entry['originals']=[dict(path='annual.txt',sha256=sha,url=url,fetched_at='2026-11-03T03:00:00Z')]
    profile['source_notes']=[dict(path='annual.txt',sha256=sha,url=url,begin=0,end=len(raw),excerpt_sha256=sha,summary='Synthetic annual source used only for deterministic qualification boundaries.',role='HISTORICAL_PE_ANCHOR')]
    proof=dict(source_ref=url,source_sha256=sha,published_at='2026-10-30T20:30:00Z',fetched_at='2026-11-03T03:00:00Z')
    monkeypatch.setattr(s3_method_research,'identity_projection',lambda *a:({},None,None))
    monkeypatch.setattr(sec,'discover_filings',lambda *a:[dict(full_url=url)])
    monkeypatch.setattr(sec,'verify_filing_envelope',lambda *a:proof)
    monkeypatch.setattr(sec,'asfiled_annual_eps',lambda *a:dict(**proof,start='2025-10-01',period='2026-09-30',diluted_eps='5'))
    result=calibration_context(tmp_path,{'companies':{'AAA:US':entry}},'AAA:US',Profile.model_validate(profile),'2026-11-03T04:00:00Z')
    h=result['historical_pe']
    if reason:
        assert not h['samples'] and h['rejected'][0]['reason']==reason and h['status']=='UNKNOWN'
    else:
        assert h['samples'][0]['pe']=='21.00000000' and h['status']=='PARTIAL_SPARSE_ANNUAL_ANCHORS'
