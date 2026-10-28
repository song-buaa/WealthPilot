"""Exact archived public SEC bytes, never a synthesized envelope or edited data.

Historical network fetched_at was not saved. It remains UNKNOWN in the manifest.
Qualification-contract tests explicitly supply a frozen OFFLINE processing clock;
that test input is not asserted to be the historic network download time.
"""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re

import pytest

from research_v2 import sec_disclosures as sec
from backend.services.market_data.evidence import assess
from backend.services.market_data.output_policy import explanation_prompt,explanation_projection

ROOT=Path(__file__).parent/'fixtures/li_sec_q2_2026'
AT=datetime(2026,10,28,12,tzinfo=timezone.utc)
OFFLINE_TEST_PROCESSING_CLOCK=AT.isoformat()
EXPECTED=dict(totalRevenue=25666890000,netIncome=-1705257000,operatingCashflow=15025000,capitalExpenditures=1315790000)


def original():
    b=gzip.decompress((ROOT/'0001104659-26-101065.txt.gz').read_bytes())
    assert len(b)==520544 and hashlib.sha256(b).hexdigest()=='9b3170dbdde2e9a37417f4132d619f390ec799569b68e1908fa3824d00528865'
    return b


def metadata(body=None,symbol='LI:US',issuer_id='LI_AUTO'):
    body=body or original();text=body.decode()
    h=re.search(r'<SEC-HEADER>(.*?)</SEC-HEADER>',text,re.S)[1]
    cik=re.search(r'CENTRAL INDEX KEY:\s*(\d+)',h)[1]
    acc=re.search(r'ACCESSION NUMBER:\s*([^\n]+)',h)[1].strip()
    form=re.search(r'CONFORMED SUBMISSION TYPE:\s*([^\n]+)',h)[1].strip()
    accepted=re.search(r'<ACCEPTANCE-DATETIME>(\d{14})',h)[1]
    clock=datetime.strptime(accepted,'%Y%m%d%H%M%S').replace(tzinfo=sec.ZoneInfo('America/New_York'))
    filename=re.search(r'<DOCUMENT>\s*<TYPE>6-K\s*<SEQUENCE>1\s*<FILENAME>([^\n]+)',text)[1].strip()
    base=f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace("-", "")}/'
    issuer=dict(symbol=symbol,issuer_id=issuer_id,cik=cik,name='Li Auto Inc.',issuer_type='foreign_private',
        identity_source=sec.DIRECTORY,submissions_url='https://data.sec.gov/submissions/CIK'+cik+'.json')
    filing=dict(accession=acc,form=form,filed=clock.date().isoformat(),report_date=clock.date().isoformat(),
        published_at=clock.isoformat(),publication_locator='SEC-HEADER/ACCEPTANCE-DATETIME',primary_url=base+filename,full_url=base+acc+'.txt')
    return issuer,filing


def parse(body=None,issuer=None,filing=None):
    body=body or original();i,f=metadata()
    return sec.parse_filing((body,OFFLINE_TEST_PROCESSING_CLOCK),issuer or i,filing or f,identity_digest='a'*64)


def test_original_hash_before_and_after_parser_and_four_exact_native_fields():
    body=original();before=hashlib.sha256(body).hexdigest();row=parse(body)
    assert row and row['source_verification']['document_sha256']==before
    facts={o['source_field']:o for o in row['financial_observations'] if o['fiscal_period_end']=='2026-06-30'}
    assert {k:v['value']*v['unit_scale'] for k,v in facts.items()}==EXPECTED
    assert len(row['financial_observations'])==12  # three actually reported periods
    assert {o['currency'] for o in row['financial_observations']}=={'CNY'}
    assert facts['capitalExpenditures']['reported_value']==-1315790
    assert facts['capitalExpenditures']['sign_normalization']['reported_free_cash_flow']==-1300765
    assert EXPECTED['operatingCashflow']-EXPECTED['capitalExpenditures']==-1300765000
    assert row['published_at']=='2026-08-26T07:57:57-04:00'
    assert all(o['document_type']=='EX-99.1' and o['document_filename']=='tm2624080d1_ex99-1.htm' for o in facts.values())
    assert all('DOCUMENT[1]' in o['source_locator'] and 'table[' in o['source_locator'] for o in facts.values())
    assert hashlib.sha256(body).hexdigest()==before


def test_real_original_four_L1_refs_under_explicit_offline_clock_without_TTM_upgrade():
    row=parse();a=assess({},symbol='LI:US',research=[deepcopy(row)],at=AT,
        disclosure_verifier={row['record_id']:deepcopy(row)}.get)
    fields={r['field']:r for r in a['verified_field_records']}
    assert set(fields)=={'revenue_quarter','net_income_quarter','operating_cash_flow_quarter','capital_expenditures_quarter'}
    assert all(r['period']=='2026-06-30' and r['currency']=='CNY' for r in fields.values())
    assert 'net_income_quarter' in explanation_prompt(a)
    candidate=dict(topic='business',basis='inference',text='账面亏损与经营现金流偏弱共同提示盈利和现金转化承压，应结合营运资本与产品结构核查改善是否可持续。',
        evidence_refs=[fields[k]['record_id'] for k in ('net_income_quarter','operating_cash_flow_quarter')])
    assert explanation_projection([candidate],a)[0]
    assert a['qualification']['L1']['allowed']
    assert all(not a['qualification'][l]['allowed'] for l in ('L2','L3','L4'))


def test_historical_missing_fetch_clock_is_not_filled_from_file_or_request_start():
    manifest=json.loads((ROOT/'manifest.json').read_text())
    assert manifest['original_network_fetched_at'] is None
    assert manifest['request_started_at']=='2026-10-28T06:04:22.283703+00:00'
    i,f=metadata()
    assert sec.parse_filing((original(),None),i,f,identity_digest='a'*64) is None


@pytest.mark.parametrize('change',['cik','accession','form','acceptance','primary_filename','exhibit_type','exhibit_filename','exhibit_link','currency','period','units','capex_sign'])
def test_real_original_adversarial_mutations_fail_closed(change):
    body=original();i,f=metadata()
    if change=='cik':i['cik']='0001737806'
    if change=='accession':f['accession']='0001104659-26-101066'
    if change=='form':f['form']='20-F'
    if change=='acceptance':f['published_at']='2026-08-26T08:57:57-04:00'
    if change=='primary_filename':f['primary_url']=f['primary_url'].replace('tm2624080d1_6k.htm','other.htm')
    if change=='exhibit_type':body=body.replace(b'<TYPE>EX-99.1',b'<TYPE>GRAPHIC')
    if change=='exhibit_filename':body=body.replace(b'<FILENAME>tm2624080d1_ex99-1.htm',b'<FILENAME>other.htm')
    if change=='exhibit_link':body=body.replace(b'HREF="tm2624080d1_ex99-1.htm"',b'HREF="not-declared.htm"')
    if change=='currency':body=body.replace(b'RMB',b'XXX').replace(b'US$',b'XXX')
    if change=='period':body=body.replace(b'Three Months Ended',b'Six Months Ended')
    if change=='units':body=body.replace(b'in thousands',b'units UNKNOWN')
    if change=='capex_sign':body=body.replace(b'1,300,765',b'1,300,764')
    row=sec.parse_filing((body,OFFLINE_TEST_PROCESSING_CLOCK),i,f,identity_digest='a'*64)
    if change=='capex_sign':
        assert row and not any(o['source_field']=='capitalExpenditures' and o['fiscal_period_end']=='2026-06-30' for o in row['financial_observations'])
    else:assert row is None or not row['financial_observations']


def test_shared_statement_layout_has_no_LI_identity_precondition():
    # Semantics/layout reuse tested independently of the exact original identity.
    # This adversarial synthetic source does NOT count as the real-original test.
    from bs4 import BeautifulSoup
    from research_v2.official_disclosures import parse_statement_tables
    body=original().decode().replace('Li Auto Inc.','Unrelated Issuer')
    rows=parse_statement_tables(BeautifulSoup(body,'html.parser'),'2026-08-26',require_statements=True,currencies=('CNY','USD'))
    assert {o['source_field'] for o in rows}==set(EXPECTED)


def test_generic_USD_statement_works_for_domestic_issuer_without_LI_branch():
    from tests.test_sec_financial_sources import identity,full_submission
    i,filings=identity('AAPL');doc='''<html><p>Unaudited Condensed Consolidated Statements of Income</p>
<p>(All amounts in thousands)</p><table><tr><td></td><td>For the Three Months Ended</td></tr>
<tr><td></td><td>June 30, 2026</td></tr><tr><td></td><td>USD</td></tr>
<tr><td>Total revenues</td><td>100</td></tr></table></html>'''
    row=sec.parse_filing((full_submission(i,filings[0],doc),OFFLINE_TEST_PROCESSING_CLOCK),i,filings[0],identity_digest='a'*64)
    assert row and row['symbol']=='AAPL:US'
    assert row['financial_observations'][0]['value']==100 and row['financial_observations'][0]['currency']=='USD'


@pytest.mark.parametrize('cells',[['(20',')'],['(','20',')']])
def test_shared_parser_split_parentheses_are_numeric_only(cells):
    from research_v2.official_disclosures import _cell_amount
    rows=[['','June 30, 2026']+['']*(len(cells)-1),['','RMB']+['']*(len(cells)-1),['Net income']+cells]
    assert _cell_amount(rows,2,1)==-20
