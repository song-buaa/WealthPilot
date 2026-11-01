"""Unchanged official US-tech originals plus strict adaptation boundaries; offline."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import gzip
import hashlib
import json
import re

from bs4 import BeautifulSoup
import pytest

from research_v2 import sec_disclosures as sec
from research_v2.official_disclosures import parse_us_gaap_diluted_eps
from backend.services.market_data.evidence import assess
from backend.services.market_data.financial_research import build

ROOT = Path(__file__).parent/'fixtures/s3_us_tech_eps'
MANIFEST = json.loads((ROOT/'manifest.json').read_text())
AT = datetime(2026, 11, 2, 1, tzinfo=timezone.utc)


def sources(symbol):
    metadata = MANIFEST[symbol]
    def load(key):
        m = metadata[key]
        body = gzip.decompress((ROOT/(m['path']+'.gz')).read_bytes())
        assert hashlib.sha256(body).hexdigest() == m['sha256']
        return body, m['fetched_at']
    directory, submissions = load('directory'), load('submissions')
    issuer = sec.resolve_sec_issuer(symbol, json.loads(directory[0]), json.loads(submissions[0]))
    filings = sec.discover_filings(issuer, json.loads(submissions[0]))
    filing = next(f for f in filings if f['full_url'] == metadata['original']['url'])
    identity = hashlib.sha256(directory[0]+submissions[0]).hexdigest()
    rows = sec.parse_companyfacts(load('companyfacts'), issuer, filings, identity_digest=identity)
    return issuer, filing, identity, rows, load('original')


@pytest.mark.parametrize('symbol,q4,ttm', [('META:US',8.88,29.33),('MSFT:US',4.81,19.16)])
def test_real_q4_enters_original_evidence_ttm_without_estimation(symbol,q4,ttm):
    issuer,filing,identity,rows,raw = sources(symbol)
    context = sec._domestic_fiscal_eps_context(rows,issuer)
    record = sec.parse_filing(raw,issuer,filing,identity_digest=identity,fiscal_eps_context=context)
    assert record and record['source_url'] == MANIFEST[symbol]['original']['url']
    assert any(o['source_field']=='dilutedEPSOrdinary' and o['value']==q4
               and o['period_basis']=='quarterly' and o['annual_eps_corroboration']
               and len(o['period_start_source_refs'])==2 for o in record['financial_observations'])
    rows += [record]
    lookup={r['record_id']:r for r in rows}
    a=assess({},symbol=symbol,research=deepcopy(rows),at=AT,disclosure_verifier=lookup.get)
    derived={r['field']:r for r in build(a)['records']}
    assert derived['diluted_eps_ttm']['values']['diluted_eps_ttm']==pytest.approx(ttm)
    assert 'pe_ttm' not in derived and 'observed_price' not in derived
    # No newly fetched document is usable before its public acceptance.
    before=datetime.fromisoformat(filing['published_at']).replace(year=2024)
    old=assess({},symbol=symbol,research=deepcopy(rows),at=before,disclosure_verifier=lookup.get)
    assert not any(r['period']>'2024-12-31' for r in old['verified_field_records'])


def test_goog_direct_class_c_does_not_borrow_class_a_or_make_q4():
    issuer,filing,identity,rows,raw=sources('GOOG:US')
    assert not any(o['source_field']=='dilutedEPSOrdinary' for r in rows for o in r['financial_observations'])
    record=sec.parse_filing(raw,issuer,filing,identity_digest=identity)
    eps=[o for o in record['financial_observations'] if o['source_field']=='dilutedEPSOrdinary']
    assert eps and all(o['listed_class_proof']['class_label']=='C' and o['listed_class_proof']['ticker']=='GOOG' for o in eps)
    assert any(o['value']==2.11 and o['fiscal_period_end']=='2026-09-30' for o in eps)
    assert all(o['fiscal_period_end']!='2025-12-31' for o in eps)
    altered=deepcopy(issuer);altered['sec_ticker']='GOOGL';altered['symbol']='GOOGL:US'
    other=sec._direct_listed_class_eps(raw[0],altered,filing,raw[1])
    assert other and all(o['listed_class_proof']['class_label']=='A' for o in other)
    assert not any(o['value']==2.11 and o['fiscal_period_end']=='2026-09-30' for o in other)


@pytest.mark.parametrize('links,expected', [
    ('<a href="e.htm">Press</a><a href="e.htm"> release</a>',True),
    ('<a href="e.htm">Press</a><a href="other.htm">release</a>',False),
    ('<a href="e.htm">Press</a><a href="#other">release</a>',False),
])
def test_formatted_link_requires_one_exact_document_destination(links,expected):
    cover=BeautifulSoup('<table><tr><td>99.1</td><td>'+links+'</td></tr></table>','html.parser')
    assert sec._declared_exhibit(cover,'e.htm','EX-99.1') is expected


def table_document(title='INCOME STATEMENTS',annual='8.00'):
    return f'''<h2>{title}</h2><p>(In millions, except per share amounts)</p><table>
<tr><td></td><td>Three Months Ended June 30,</td><td>Twelve Months Ended June 30,</td></tr>
<tr><td></td><td>2026</td><td>2026</td></tr>
<tr><td>Earnings per share:</td><td></td><td></td></tr>
<tr><td>Basic</td><td>$2.01</td><td>$8.01</td></tr>
<tr><td>Diluted</td><td>$2.00</td><td>${annual}</td></tr></table>'''


@pytest.mark.parametrize('fault', ['non_gaap','annual_mismatch','no_period_proof','future','no_dollar'])
def test_new_statement_layout_preserves_basis_and_period_guards(fault):
    ctx={'2026-06-30':dict(start='2026-04-01',annual_eps=8,prior_ref='q3',annual_ref='annual',proof_available_at='2026-07-30T12:00:00+00:00')}
    html=table_document();published='2026-07-30T12:00:00+00:00'
    assert parse_us_gaap_diluted_eps(BeautifulSoup(html,'html.parser'),published,ctx)[0]['value']==2
    if fault=='non_gaap':html=table_document('NON-GAAP INCOME STATEMENTS')
    if fault=='annual_mismatch':html=table_document(annual='9.00')
    if fault=='no_period_proof':ctx={}
    if fault=='future':published='2026-06-29T12:00:00+00:00'
    if fault=='no_dollar':html=html.replace('$','')
    assert parse_us_gaap_diluted_eps(BeautifulSoup(html,'html.parser'),published,ctx)==[]


def test_reused_earnings_exhibit_does_not_hide_missing_listed_class_originals(monkeypatch,tmp_path):
    from backend.services import s3_method_research as methods
    from backend.services.s3_pool_capture import capture_pool
    from tests.test_s3_p0_av_recovery import setup_capture,SPLITS
    seed,_=setup_capture(monkeypatch,tmp_path,SPLITS)
    m=json.loads((seed/'manifest.json').read_text())
    prior=seed/'earnings.txt';prior.write_bytes(b'existing official original')
    old_url='https://www.sec.gov/Archives/edgar/data/1/prior.txt'
    m['companies']['AAA:US']['originals']=[dict(url=old_url,path=prior.name,sha256=hashlib.sha256(prior.read_bytes()).hexdigest(),fetched_at='2026-10-31T08:00:00+00:00')]
    (seed/'manifest.json').write_text(json.dumps(m))
    sub='https://data.sec.gov/submissions/CIK0000000001.json'
    monkeypatch.setattr(methods,'identity_projection',lambda *a:({'submissions_url':sub,'registered_tickers':['AAA','BBB']},{'issuer_type':'domestic','sic':'3571'},{}))
    def filing(kind,n):return dict(form=kind,filed='2026-10-29',full_url=f'https://www.sec.gov/Archives/edgar/data/1/{n}.txt',accession=str(n))
    annual=filing('10-K',4);event=filing('8-K',0);event['full_url']=old_url
    monkeypatch.setattr(sec,'discover_filings',lambda *a:[event]+[filing('10-Q',n) for n in (1,2,3)]+[annual])
    seen=[]
    def fetch(url):seen.append(url);return b'new official raw source','2026-10-31T09:00:00+00:00'
    monkeypatch.setattr(sec,'_fetch',fetch)
    path=capture_pool(['AAA:US'],tmp_path/'capture',reuse=seed)
    result=json.loads(path.read_text())['companies']['AAA:US']
    assert 'capture_failure' not in result
    assert set(seen)=={filing('10-Q',n)['full_url'] for n in (1,2,3)}|{annual['full_url']}
    assert len(result['originals'])==5 and old_url not in seen


def test_in_table_title_cannot_override_explicit_adjacent_non_gaap_label():
    issuer,filing,_,rows,raw=sources('META:US')
    text=raw[0].decode();doc=re.findall(r'<DOCUMENT>(.*?)</DOCUMENT>',text,re.S)[1]
    soup=BeautifulSoup(doc,'html.parser')
    ctx=sec._domestic_fiscal_eps_context(rows,issuer)
    assert parse_us_gaap_diluted_eps(soup,filing['published_at'],ctx)
    table=next(t for t in soup.find_all('table') if 'CONDENSED CONSOLIDATED STATEMENTS OF INCOME' in t.get_text())
    label=soup.new_tag('h2');label.string='NON-GAAP RECONCILIATION';table.insert_before(label)
    assert parse_us_gaap_diluted_eps(soup,filing['published_at'],ctx)==[]


def test_long_structural_adjusted_heading_is_not_a_general_disclaimer():
    ctx={'2026-06-30':dict(start='2026-04-01',annual_eps=8,prior_ref='q3',annual_ref='annual',proof_available_at='2026-07-30T12:00:00+00:00')}
    html=table_document().replace('<table>','<h2>Example Company NON-GAAP RECONCILIATION of selected quarterly and annual adjusted financial performance measures presented for supplemental analysis</h2><table><tr><td colspan="3">CONSOLIDATED STATEMENTS OF INCOME</td></tr><tr><td colspan="3">In millions, except per share amounts</td></tr>')
    assert parse_us_gaap_diluted_eps(BeautifulSoup(html,'html.parser'),'2026-07-30T12:00:00+00:00',ctx)==[]
