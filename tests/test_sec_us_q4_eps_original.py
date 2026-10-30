"""AAPL/NVDA GAAP Q4 EPS from unchanged SEC originals, without network."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from research_v2 import sec_disclosures as sec
from research_v2.official_disclosures import parse_us_gaap_diluted_eps
from backend.services.market_data.evidence import assess
from backend.services.market_data.financial_research import build


ROOT = Path(__file__).parent / 'fixtures/sec_us_q4_eps'
MANIFEST = json.loads((ROOT / 'manifest.json').read_text())
AT = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
CASES = (
    ('AAPL', '0000320193', '02-CIK0000320193.json', '03-CIK0000320193.json',
     '14-0000320193-25-000077.txt', '0000320193-25-000077', '2025-09-27', 1.85, 8.72),
    ('NVDA', '0001045810', '25-CIK0001045810.json', '26-CIK0001045810.json',
     '39-0001045810-26-000019.txt', '0001045810-26-000019', '2026-01-25', 1.76, 7.91),
)


def original(name):
    body = gzip.decompress((ROOT / (name + '.gz')).read_bytes())
    assert len(body) == MANIFEST[name]['bytes']
    assert hashlib.sha256(body).hexdigest() == MANIFEST[name]['sha256']
    return body, MANIFEST[name]['fetched_at']


def source(case):
    symbol, cik, submission_name, facts_name, filing_name, accession, _, _, _ = case
    directory = sec._json(original('01-company_tickers.json'))
    submissions = sec._json(original(submission_name))
    issuer = sec.resolve_sec_issuer(symbol + ':US', directory, submissions)
    assert issuer and issuer['cik'] == cik and issuer['issuer_type'] == 'domestic'
    filings = sec.discover_filings(issuer, submissions)
    filing = next(f for f in filings if f['accession'] == accession)
    identity_digest = hashlib.sha256(original('01-company_tickers.json')[0] + original(submission_name)[0]).hexdigest()
    rows = sec.parse_companyfacts(original(facts_name), issuer, filings, identity_digest=identity_digest)
    return issuer, filing, identity_digest, rows, original(filing_name)


@pytest.mark.parametrize('case', CASES, ids=['AAPL', 'NVDA'])
def test_real_q4_gaap_eps_enters_evidence_and_ttm(case):
    symbol, _, _, _, filing_name, _, end, expected, ttm = case
    issuer, filing, identity_digest, rows, body = source(case)
    context = sec._domestic_fiscal_eps_context(rows, issuer)
    assert end in context
    record = sec.parse_filing(body, issuer, filing, identity_digest=identity_digest,
                              fiscal_eps_context=context)
    assert record and record['source_verification']['document_sha256'] == MANIFEST[filing_name]['sha256']
    q4 = [o for o in record['financial_observations']
          if o['source_field'] == 'dilutedEPSOrdinary' and o['fiscal_period_end'] == end]
    assert len(q4) == 1 and q4[0]['value'] == expected
    assert q4[0]['currency'] == 'USD' and q4[0]['unit'] == 'currency_per_ordinary_share'
    assert q4[0]['fiscal_period_start'] == context[end]['start']
    assert len(q4[0]['period_start_source_refs']) == 2
    assert q4[0]['proof_available_at'] == context[end]['proof_available_at']
    assert q4[0]['document_type'] == 'EX-99.1' and q4[0]['source_locator'].startswith('DOCUMENT[1]/')
    if symbol == 'NVDA':
        assert any(issue['issue'] == 'INVALID_TABLE_SKIPPED'
                   for issue in record['financial_observation_issues'])
    records = rows + [record]
    proof = {r['record_id']: deepcopy(r) for r in records}
    a = assess({}, symbol=symbol + ':US', research=deepcopy(records), at=AT, disclosure_verifier=proof.get)
    fields = [r for r in a['verified_field_records'] + a['historical_field_records']
              if r['field'] == 'diluted_eps_ordinary_quarter' and r['period'] == end]
    assert len(fields) == 1 and fields[0]['values']['diluted_eps_ordinary_quarter'] == expected
    assert fields[0]['published_at'] == max(record['published_at'], context[end]['proof_available_at'])
    assert fields[0]['publication_links'][0]['original_filing_published_at'] == record['published_at']
    derived = {r['field']: r for r in build(a)['records']}
    assert derived['diluted_eps_ttm']['values']['diluted_eps_ttm'] == pytest.approx(ttm)
    assert 'pe_ttm' not in derived and 'observed_price' not in derived


@pytest.mark.parametrize('case', CASES, ids=['AAPL', 'NVDA'])
def test_q4_rejects_missing_or_conflicting_independent_period_proof(case):
    issuer, filing, identity_digest, rows, body = source(case)
    context = sec._domestic_fiscal_eps_context(rows, issuer)
    assert sec.parse_filing(body, issuer, filing, identity_digest=identity_digest) is None
    wrong = deepcopy(context)
    wrong[case[6]]['annual_eps'] += 0.01
    record = sec.parse_filing(body, issuer, filing, identity_digest=identity_digest,
                              fiscal_eps_context=wrong)
    assert record is None or not any(o['source_field'] == 'dilutedEPSOrdinary' and
                                      o['fiscal_period_end'] == case[6]
                                      for o in record['financial_observations'])
    altered = deepcopy(rows)
    for row in altered:
        row['financial_observations'] = [o for o in row['financial_observations']
            if not (o['source_field'] == 'dilutedEPSOrdinary' and o['period_basis'] == 'annual')]
    assert case[6] not in sec._domestic_fiscal_eps_context(altered, issuer)


def test_nvda_non_gaap_summary_cannot_supply_eps():
    import re
    body, _ = original('39-0001045810-26-000019.txt')
    exhibit = next(d for d in re.findall(r'<DOCUMENT>(.*?)</DOCUMENT>', body.decode(), re.I | re.S)
                   if '<TYPE>EX-99.1' in d[:100])
    soup = BeautifulSoup(exhibit, 'html.parser')
    non_gaap = BeautifulSoup(str(soup.find_all('table')[1]), 'html.parser')
    assert '1.62' in non_gaap.get_text(' ', strip=True)
    assert parse_us_gaap_diluted_eps(non_gaap, '2026-02-25T21:31:25+00:00', {'2026-01-25': {
        'start': '2025-10-27', 'annual_eps': 4.9,
        'prior_ref': 'official:prior', 'annual_ref': 'official:annual',
        'proof_available_at': '2026-02-26T00:00:00+00:00'}}) == []


@pytest.mark.parametrize('case', CASES, ids=['AAPL', 'NVDA'])
def test_original_exhibit_identity_remains_exact(case):
    issuer, filing, identity_digest, rows, body = source(case)
    context = sec._domestic_fiscal_eps_context(rows, issuer)
    wrong = deepcopy(filing)
    wrong['accession'] = wrong['accession'][:-1] + ('0' if wrong['accession'][-1] != '0' else '1')
    assert sec.parse_filing(body, issuer, wrong, identity_digest=identity_digest,
                            fiscal_eps_context=context) is None


@pytest.mark.parametrize('case', CASES, ids=['AAPL', 'NVDA'])
def test_offline_collect_discovers_real_q4_without_other_originals(case, monkeypatch):
    from backend.services.market_data import cache
    from backend.core import demo_mode
    monkeypatch.setenv('PUBLIC_DEMO_MODE', 'false')
    monkeypatch.delenv('AV_DEV_MOCK', raising=False)
    monkeypatch.setattr(demo_mode, 'PUBLIC_DEMO_MODE', False)
    symbol, cik, submission_name, facts_name, filing_name, _, end, expected, _ = case
    issuer, filing, _, _, _ = source(case)
    available = {
        sec.DIRECTORY: original('01-company_tickers.json'),
        issuer['submissions_url']: original(submission_name),
        f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json': original(facts_name),
        filing['full_url']: original(filing_name),
    }
    calls = []
    def frozen_fetch(url):
        calls.append(url)
        return available.get(url)
    saved = {}
    monkeypatch.setattr(sec, '_fetch', frozen_fetch)
    monkeypatch.setattr(cache, 'get_cached', saved.get)
    monkeypatch.setattr(cache, 'set_cached', lambda key, value, ttl: saved.setdefault(key, value))
    records, verifier = sec.collect(symbol + ':US')
    assert filing['full_url'] in calls
    q4 = [o for row in records for o in row['financial_observations']
          if o['source_field'] == 'dilutedEPSOrdinary' and o['fiscal_period_end'] == end
          and o['period_basis'] == 'quarterly']
    assert len(q4) == 1 and q4[0]['value'] == expected
    assert verifier(next(row['record_id'] for row in records if row['source_url'] == filing['full_url']))
