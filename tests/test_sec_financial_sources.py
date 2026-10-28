"""Offline SEC contracts; synthetic financial amounts, real issuer identifiers.

The real LI Q2 layout is the reviewed frozen public IR fixture, not a synthetic
claim of a newly downloaded SEC response. No network or paid model is needed.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from backend.services.instruments.issuers import resolve_sec_issuer, IDENTITIES
from backend.services.market_data.evidence import assess
from backend.services.market_data.output_policy import explanation_prompt, explanation_projection
from research_v2 import sec_disclosures as sec
from tests.test_v42_evidence_quality import raw_financial

AT = datetime(2026,10,28,tzinfo=timezone.utc)
DIGEST = 'a'*64
ISSUERS = {'AAPL':(320193,'Apple Inc.','10-Q'), 'LI':(1791706,'Li Auto Inc.','6-K'),
           'PDD':(1737806,'PDD Holdings Inc.','20-F')}


def directory():
    return {str(i):dict(ticker=t,cik_str=c,title=n) for i,(t,(c,n,f)) in enumerate(ISSUERS.items())}


def submissions(ticker='AAPL',form=None):
    cik,name,default=ISSUERS[ticker];form=form or default
    return dict(cik=str(cik),name=name,tickers=[ticker],filings=dict(recent=dict(
        accessionNumber=['0001104659-26-101065'],form=[form],filingDate=['2026-08-26'],
        acceptanceDateTime=['2026-08-26T11:57:57.000Z'],reportDate=['2026-06-30'],
        primaryDocument=['statement.htm'])))


def identity(ticker='AAPL',form=None):
    sub=submissions(ticker,form)
    regime=deepcopy(sub)
    if form=='8-K': regime['filings']['recent']['form'].append('10-Q')
    issuer=resolve_sec_issuer(ticker+':US',directory(),regime)
    return issuer,sec.discover_filings(issuer,sub)


def facts(ticker='AAPL',form='10-Q',start='2026-04-01',end='2026-06-30',currency='USD'):
    issuer,filings=identity(ticker,form)
    concepts={}
    for concept,value in [('RevenueFromContractWithCustomerExcludingAssessedTax',100),('ProfitLoss',20),
                          ('NetCashProvidedByUsedInOperatingActivities',-30),('PaymentsToAcquirePropertyPlantAndEquipment',10)]:
        concepts[concept]=dict(units={currency:[dict(accn=filings[0]['accession'],form=form,filed='2026-08-26',start=start,end=end,val=value)]})
    data=dict(cik=int(issuer['cik']),facts={'us-gaap':concepts})
    return (json.dumps(data).encode(),AT.isoformat()),issuer,filings


def records(source=None,**kwargs):
    src,issuer,filings=facts(**kwargs)
    return sec.parse_companyfacts(source or src,issuer,filings,identity_digest=DIGEST)


def evaluate(rows,raw=None,symbol='AAPL:US'):
    return assess({'fundamentals':raw},symbol=symbol,research=deepcopy(rows),at=AT,
                  disclosure_verifier={r['record_id']:deepcopy(r) for r in rows}.get)


@pytest.mark.parametrize('ticker,kind,expected',[('AAPL','domestic','SEC_CIK_0000320193'),('LI','foreign_private','LI_AUTO'),('PDD','foreign_private','PDD_HOLDINGS')])
def test_exact_issuer_resolution_reuses_owner_without_position_changes(ticker,kind,expected):
    before=deepcopy(IDENTITIES);issuer,filings=identity(ticker)
    assert issuer['issuer_type']==kind and issuer['issuer_id']==expected
    assert filings[0]['full_url'].startswith('https://www.sec.gov/Archives/edgar/data/'+str(int(issuer['cik']))+'/')
    assert IDENTITIES==before


@pytest.mark.parametrize('change',['cik','ticker','duplicate','name_only','mixed_regime','non_us','malformed'])
def test_identity_never_guesses_by_similar_name(change):
    sub=submissions();dirs=directory();symbol='AAPL:US'
    if change=='cik':sub['cik']='1737806'
    if change=='ticker':sub['tickers']=['AAP']
    if change=='duplicate':dirs['999']=deepcopy(dirs['0'])
    if change=='name_only':dirs['0']['ticker']='UNRELATED'
    if change=='mixed_regime':sub['filings']['recent']['form']+=['20-F']
    if change=='non_us':symbol='AAPL:HK'
    if change=='malformed':sub['cik']=None
    assert resolve_sec_issuer(symbol,dirs,sub) is None


@pytest.mark.parametrize('ticker,form',[('AAPL','10-Q'),('AAPL','10-K'),('AAPL','8-K'),('LI','6-K'),('PDD','20-F')])
def test_supported_filing_forms_have_exact_accession_discovery(ticker,form):
    issuer,filings=identity(ticker,form);assert len(filings)==1
    assert filings[0]['form']==form
    bad=submissions(ticker,form);bad['filings']['recent']['primaryDocument']=['../escape.htm']
    assert sec.discover_filings(issuer,bad)==[]


def test_companyfacts_enters_original_registry_and_L1_with_no_AV_fields():
    rows=records();result=evaluate(rows)
    fields={r['field']:r for r in result['verified_field_records']}
    assert set(fields)=={'revenue_quarter','net_income_quarter','operating_cash_flow_quarter','capital_expenditures_quarter'}
    assert fields['operating_cash_flow_quarter']['values']['operating_cash_flow_quarter']==-30
    assert fields['revenue_quarter']['currency']=='USD'
    assert 'operating_cash_flow_quarter' in explanation_prompt(result)
    refs=[fields[n]['record_id'] for n in ('net_income_quarter','operating_cash_flow_quarter')]
    text='盈利与经营现金流方向背离，需要核查回款与营运资本对现金转化的影响，单期利润不能证明盈利质量改善。'
    accepted,_=explanation_projection([dict(topic='business',basis='inference',text=text,evidence_refs=refs)],result)
    assert accepted and all(not result['qualification'][n]['allowed'] for n in ('L2','L3','L4'))
    assert not any(n.endswith('_ttm') for n in fields)


def test_annual_10K_and_20F_remain_annual_not_quarter_or_TTM():
    for ticker,form in [('AAPL','10-K'),('PDD','20-F')]:
        rows=records(ticker=ticker,form=form,start='2025-07-01')
        result=evaluate(rows,symbol=ticker+':US')
        assert {r['scope'] for r in result['verified_field_records']}=={'annual'}
        assert all(r['field'].endswith('_annual') for r in result['verified_field_records'])
        assert not result['qualification']['L3']['allowed']


@pytest.mark.parametrize('change',['cik','accession','filed','form','ytd','instant','bad_unit','negative_capex','future','missing_start'])
def test_unmatched_XBRL_fact_is_not_promoted(change):
    source,issuer,filings=facts();data=json.loads(source[0]);key='RevenueFromContractWithCustomerExcludingAssessedTax'
    unit=data['facts']['us-gaap'][key]['units'];row=unit['USD'][0]
    if change=='cik':data['cik']=1
    if change=='accession':row['accn']='0000000000-00-000001'
    if change=='filed':row['filed']='2026-08-25'
    if change=='form':row['form']='10-K'
    if change=='ytd':row['start']='2026-01-01'
    if change=='instant':row.pop('start')
    if change=='bad_unit':unit['USD/shares']=unit.pop('USD')
    if change=='negative_capex':
        key='PaymentsToAcquirePropertyPlantAndEquipment';data['facts']['us-gaap'][key]['units']['USD'][0]['val']=-10
    if change=='future':row['end']='2027-06-30'
    if change=='missing_start':row['start']=None
    rows=sec.parse_companyfacts((json.dumps(data).encode(),source[1]),issuer,filings,identity_digest=DIGEST)
    observations=[o for r in rows for o in r['financial_observations']]
    field=sec.CONCEPTS['us-gaap',key][0]
    assert not any(o['source_field']==field for o in observations)


def test_parent_net_income_is_explicit_and_cannot_supply_consolidated_TTM_clock():
    source,issuer,filings=facts(ticker='LI',form='6-K',currency='CNY');data=json.loads(source[0])
    data['facts']['us-gaap']['NetIncomeLoss']=data['facts']['us-gaap'].pop('ProfitLoss')
    rows=sec.parse_companyfacts((json.dumps(data).encode(),source[1]),issuer,filings,identity_digest=DIGEST)
    result=evaluate(rows,raw_financial(False),symbol='LI:US')
    assert any(r['field']=='net_income_attributable_parent_quarter' for r in result['verified_field_records'])
    assert not any(r['field']=='net_income_ttm' for r in result['verified_field_records'])
    assert not any(c['field']=='net_income_ttm' for c in result['source_conflicts'])


def full_submission(issuer,filing,document):
    return f'''<SEC-HEADER><ACCEPTANCE-DATETIME>20260826075757
ACCESSION NUMBER: {filing['accession']}
CONFORMED SUBMISSION TYPE: {filing['form']}
COMPANY DATA:
COMPANY CONFORMED NAME: {issuer['name']}
CENTRAL INDEX KEY: {issuer['cik']}
FILING VALUES:
</SEC-HEADER><DOCUMENT><TYPE>EX-99.1
<TEXT>{document}</TEXT></DOCUMENT>'''.encode()


def inline_document(cik='0000320193',start='2026-04-01',currency='USD',value='100',extra=''):
    return f'''<html xmlns:us-gaap="http://fasb.org/us-gaap/2026"><body><xbrli:context id="q"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">{cik}</xbrli:identifier></xbrli:entity>
<xbrli:period><xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period>{extra}</xbrli:context>
<xbrli:unit id="u"><xbrli:measure>iso4217:{currency}</xbrli:measure></xbrli:unit>
<ix:nonFraction name="us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax" contextRef="q" unitRef="u" scale="6">{value}</ix:nonFraction></body></html>'''


def test_restricted_original_inline_attachment_recovers_missing_AV_field():
    issuer,filings=identity();doc=inline_document();row=sec.parse_filing((full_submission(issuer,filings[0],doc),AT.isoformat()),issuer,filings[0],identity_digest=DIGEST)
    assert row['financial_observations'][0]['value']==100000000
    result=evaluate([row]);assert result['verified_field_records'][0]['field']=='revenue_quarter'
    assert 'DOCUMENT[0]' in result['verified_field_records'][0]['publication_links'][0]['source_locator']


@pytest.mark.parametrize('change',['other_issuer','wrong_period','segment','currency','header','accession','no_attachment','unknown_concept','clock'])
def test_ambiguous_or_unstructured_attachments_remain_unknown(change):
    issuer,filings=identity();doc=inline_document()
    if change=='other_issuer':doc=inline_document(cik='0001737806')
    if change=='wrong_period':doc=inline_document(start='2026-01-01')
    if change=='segment':doc=inline_document(extra='<xbrli:scenario>division</xbrli:scenario>')
    if change=='currency':doc=inline_document(currency='USD/shares')
    if change=='no_attachment':doc='<html>Unstructured earnings narrative with self-reported numbers.</html>'
    if change=='unknown_concept':doc=doc.replace('us-gaap:','custom:')
    body=full_submission(issuer,filings[0],doc)
    if change=='header':body=body.replace(b'CENTRAL INDEX KEY: 0000320193',b'CENTRAL INDEX KEY: 0001737806')
    if change=='accession':body=body.replace(b'0001104659-26-101065',b'0001104659-26-101066')
    if change=='clock':body=body.replace(b'20260826075757',b'20260826085757')
    row=sec.parse_filing((body,AT.isoformat()),issuer,filings[0],identity_digest=DIGEST)
    assert row is None or not row['financial_observations']


def test_real_LI_Q2_four_fields_reuse_existing_layout_inside_verified_6K():
    issuer,filings=identity('LI');doc=Path('tests/fixtures/li_official_q2_2026/en.html').read_text()
    # Real SEC exhibit uses ordinary text headings rather than IR h1/title. The
    # SEC envelope supplies identity and filing clock independently of that layout.
    row=sec.parse_filing((full_submission(issuer,filings[0],doc),AT.isoformat()),issuer,filings[0],identity_digest=DIGEST)
    assert row
    actual={o['source_field']:o['value']*o['unit_scale'] for o in row['financial_observations'] if o['fiscal_period_end']=='2026-06-30'}
    assert actual==dict(totalRevenue=25666890000,netIncome=-1705257000,operatingCashflow=15025000,capitalExpenditures=1315790000)
    assert actual['operatingCashflow']-actual['capitalExpenditures']==-1300765000
    assert row['published_at']=='2026-08-26T11:57:57+00:00'
    result=evaluate([row],symbol='LI:US');assert len(result['verified_field_records'])==4
    assert not any(r['field'].endswith('_ttm') for r in result['verified_field_records'])


def test_unverified_card_cannot_manufacture_generic_SEC_identity():
    row=records()[0]
    assert not assess({},symbol='AAPL:US',research=[row],at=AT)['verified_field_records']
    forged=deepcopy(row);forged['financial_observations'][0]['value']=999
    assert not evaluate([forged])['source_conflicts']  # no AV facts to compare
    result=assess({},symbol='AAPL:US',research=[forged],at=AT,disclosure_verifier={row['record_id']:row}.get)
    assert not result['verified_field_records']


def test_AV_missing_clock_conflict_and_currency_mismatch_are_field_local():
    raw=raw_financial(False)
    rows=records(ticker='LI',form='6-K',currency='CNY')
    # Current quarter observation matches existing fixture period; revenue differs.
    result=evaluate(rows,raw,symbol='LI:US')
    assert any(c['field']=='net_income_ttm' for c in result['source_conflicts'])
    assert not result['qualification']['L3']['allowed']
    assert raw['field_provenance']['net_income_ttm']['published_at'] is None
    usd=records(ticker='LI',form='6-K',currency='USD')
    other=evaluate(usd,raw,symbol='LI:US')
    assert not other['source_conflicts']
    assert not any(r['field'].endswith('_ttm') for r in other['verified_field_records'])


@pytest.mark.parametrize('url',['https://data.sec.gov/submissions/CIK0000320193.json?redirect=x',
 'https://evil.test/Archives/edgar/data/320193/000110465926101065/a.htm',
 'https://www.sec.gov/Archives/edgar/data/320193/000110465926101065/../a.htm',
 'https://user@data.sec.gov/submissions/CIK0000320193.json',
 'https://www.sec.gov/Archives/edgar/data/320193/000110465926101065/a.pdf'])
def test_SEC_URL_boundary(url):assert not sec.allowed_url(url)


def test_unified_collector_uses_current_API_identity_bounded_reads_and_cache(monkeypatch):
    from backend.core import demo_mode
    from backend.services.market_data.cache import _cache
    _cache.clear();monkeypatch.setattr(demo_mode,'PUBLIC_DEMO_MODE',False);monkeypatch.delenv('AV_DEV_MOCK',raising=False)
    src,issuer,filings=facts();calls=[]
    def fetch(url):
        calls.append(url)
        if url==sec.DIRECTORY:return json.dumps(directory()).encode(),AT.isoformat()
        if '/submissions/' in url:return json.dumps(submissions()).encode(),AT.isoformat()
        if '/companyfacts/' in url:return src
        pytest.fail('Complete CompanyFacts should not fetch attachments')
    monkeypatch.setattr(sec,'_fetch',fetch)
    from research_v2.official_disclosures import collect
    rows,verifier=collect('AAPL:US');assert rows and len(calls)==3
    assert verifier(rows[0]['record_id'])==rows[0]
    again,_=collect('AAPL:US');assert again==rows and len(calls)==3
    monkeypatch.setattr(demo_mode,'PUBLIC_DEMO_MODE',True)
    assert collect('AAPL:US')[0]==[] and len(calls)==3
    _cache.clear()


def test_PEER_skill_collects_SEC_when_AV_entirely_unavailable(monkeypatch):
    from backend.graph.tools import execute_assess_position_evidence
    rows=records();calls=[]
    def collect(symbol,candidates):
        calls.append(symbol);return rows,{r['record_id']:r for r in rows}.get
    monkeypatch.setattr('research_v2.official_disclosures.collect',collect)
    result=execute_assess_position_evidence(market_data={'fundamentals':None},symbol='AAPL:US',at=AT)
    assert calls==['AAPL:US'] and len(result['verified_field_records'])==4
    assert result['qualification']['L1']['allowed'] and not result['qualification']['L3']['allowed']


@pytest.mark.parametrize('payload',[{}, {'cik':320193,'facts':[]}, {'cik':320193,'facts':{'us-gaap':[]}},
    {'cik':320193,'facts':{'us-gaap':{'ProfitLoss':{'units':{'USD':[None,'untrusted']}}}}}])
def test_malformed_SEC_response_gracefully_degrades(payload):
    issuer,filings=identity()
    assert sec.parse_companyfacts((json.dumps(payload).encode(),AT.isoformat()),issuer,filings,identity_digest=DIGEST)==[]


def test_conflicting_standard_concepts_do_not_pick_the_AV_matching_value():
    source,issuer,filings=facts();data=json.loads(source[0])
    alias=deepcopy(data['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax'])
    alias['units']['USD'][0]['val']=999
    data['facts']['us-gaap']['Revenues']=alias
    rows=sec.parse_companyfacts((json.dumps(data).encode(),source[1]),issuer,filings,identity_digest=DIGEST)
    assert not any(o['source_field']=='totalRevenue' for r in rows for o in r['financial_observations'])
    assert any(o['source_field']=='netIncome' for r in rows for o in r['financial_observations'])


def test_recent_event_only_filings_cannot_hide_standard_facts():
    source,issuer,filings=facts()
    event=deepcopy(filings[0]);event.update(accession='0001104659-26-101066',form='8-K')
    rows=sec.parse_companyfacts(source,issuer,[event]*5+filings,identity_digest=DIGEST)
    assert len(rows)==1 and rows[0]['official_disclosure']['form']=='10-Q'


def test_SEC_collection_unavailable_does_not_call_models_or_follow_candidate_URL(monkeypatch):
    from backend.core import demo_mode
    from backend.services.market_data.cache import _cache
    from research_v2.official_disclosures import collect
    _cache.clear();monkeypatch.setattr(demo_mode,'PUBLIC_DEMO_MODE',False);monkeypatch.delenv('AV_DEV_MOCK',raising=False)
    calls=[]
    monkeypatch.setattr(sec,'_fetch',lambda url:calls.append(url))
    assert collect('PDD:US',[{'source_url':'https://evil.test','cik':'0001737806'}])[0]==[]
    assert calls==[sec.DIRECTORY]
    _cache.clear()


def test_original_document_contexts_and_units_cannot_leak_between_exhibits():
    issuer,filings=identity();doc=inline_document()
    context,number=doc.split('<ix:nonFraction',1)
    body=full_submission(issuer,filings[0],context)+b'<DOCUMENT><TEXT><ix:nonFraction'+number.encode()+b'</TEXT></DOCUMENT>'
    assert sec.parse_filing((body,AT.isoformat()),issuer,filings[0],identity_digest=DIGEST) is None


def test_missing_AV_clock_links_only_all_matching_quarters_never_annual_or_other_basis():
    from tests.test_official_disclosures import matched_originals
    raw=raw_financial(False);ir=matched_originals(raw)
    # Existing original AV/quarter linkage still works; a SEC annual does not
    # independently certify the missing four quarterly components.
    result=evaluate(ir,raw,symbol='LI:US')
    assert any(r['field']=='revenue_ttm' for r in result['verified_field_records'])
    annual=records(ticker='LI',form='20-F',start='2025-07-01',currency='CNY')
    assert not any(r['field'].endswith('_ttm') for r in evaluate(annual,raw,symbol='LI:US')['verified_field_records'])


def test_inline_concept_namespace_cannot_self_claim_a_standard_prefix():
    issuer,filings=identity()
    doc=inline_document().replace('http://fasb.org/us-gaap/2026','https://untrusted.test/us-gaap')
    assert sec.parse_filing((full_submission(issuer,filings[0],doc),AT.isoformat()),issuer,filings[0],identity_digest=DIGEST) is None
