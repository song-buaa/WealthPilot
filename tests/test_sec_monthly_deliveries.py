"""Replay the two retained SEC originals from PR #92 through the current L1 owners."""
from copy import deepcopy
from datetime import datetime, timedelta
import gzip
from hashlib import sha256
import json
from pathlib import Path

from bs4 import BeautifulSoup
import pytest

from backend.services.market_data.evidence import assess
from backend.services.market_data.l1_context import project
from backend.services.market_data.output_policy import captured_facts
from research_v2 import sec_disclosures as sec
from research_v2.official_disclosures import parse_monthly_vehicle_delivery


ROOT = Path(__file__).parent / 'fixtures/monthly_deliveries'
CASES = json.loads((ROOT / 'manifest.json').read_text())


@pytest.mark.parametrize('case,count,period', zip(CASES, (29025, 41256), ('2026-10-31', '2026-09-30')))
def test_frozen_official_delivery_through_current_collection_and_l1(monkeypatch, case, count, period):
    from backend.core import demo_mode
    from backend.services.market_data import cache
    info, filing = case['issuer'], case['filing']
    body = gzip.decompress((ROOT / case['file']).read_bytes())
    assert sha256(body).hexdigest() == case['sha256']
    clock = case['fetched_at']
    ticker = info['sec_ticker']
    directory = {'0': dict(ticker=ticker, cik_str=int(info['cik']), title=info['name'])}
    submissions = dict(cik=info['cik'], name=info['name'], tickers=[ticker], filings=dict(recent=dict(
        accessionNumber=[filing['accession']], form=['6-K'], filingDate=[filing['filed']],
        reportDate=[filing['report_date']], acceptanceDateTime=[filing['accepted_at']],
        primaryDocument=[filing['primary_url'].rsplit('/', 1)[-1]])))
    frozen = {sec.DIRECTORY: json.dumps(directory).encode(),
              info['submissions_url']: json.dumps(submissions).encode(),
              f"https://data.sec.gov/api/xbrl/companyfacts/CIK{info['cik']}.json": b'{"facts":{}}',
              filing['full_url']: body}
    monkeypatch.setattr(demo_mode, 'PUBLIC_DEMO_MODE', False)
    monkeypatch.delenv('AV_DEV_MOCK', raising=False)
    monkeypatch.setattr(sec, '_fetch', lambda url: (frozen[url], clock))
    monkeypatch.setattr(cache, 'get_cached', lambda key: None)
    monkeypatch.setattr(cache, 'set_cached', lambda *args: None)
    monkeypatch.setattr(sec, '_access_failed', lambda: False)
    rows, verify = sec.collect(case['symbol'], current={})
    assert len(rows) == 1
    row = rows[0]
    assert row['source_verification']['document_sha256'] == case['sha256']
    at = datetime.fromisoformat(clock) + timedelta(seconds=1)
    quality = assess({}, symbol=case['symbol'], research=rows, at=at, disclosure_verifier=verify)
    qualified = quality['verified_field_records']
    monthly = next(r for r in qualified if r['field'] == 'vehicle_deliveries_month')
    assert (monthly['period'], monthly['scope'], monthly['unit']) == (period, 'monthly', 'vehicles')
    assert monthly['values']['vehicle_deliveries_month'] == count
    assert not any(r['field']=='vehicle_deliveries_quarter' for r in qualified)
    pack = project(quality)
    assert str(count) in json.dumps(pack) and monthly['record_id'] in json.dumps(pack)
    _, final = captured_facts(quality, at=at)
    assert monthly['record_id'] in {r['record_id'] for r in final}

    # Untrusted changes to value or source retain no authority.
    altered = deepcopy(row)
    altered['financial_observations'][0]['value'] += 1
    rejected = assess({}, symbol=case['symbol'], research=[altered], at=at, disclosure_verifier=verify)
    assert not any(r['field']=='vehicle_deliveries_month' for r in rejected['verified_field_records'])
    # Another qualified filing claiming a different count for the same month
    # leaves the current monthly slot ambiguous.
    altered['record_id'] += ':conflict'
    conflict = assess({}, symbol=case['symbol'], research=[row, altered], at=at,
                      disclosure_verifier={row['record_id']: row, altered['record_id']: altered}.get)
    assert not any(r['field']=='vehicle_deliveries_month' for r in conflict['verified_field_records'])


@pytest.mark.parametrize('text,published', [
    ('Li Auto Inc. October 2026 update. XPENG delivered 30,000 vehicles in October 2026.', '2026-11-02'),
    ('Li Auto Inc. Li Auto delivered 29,025 vehicles in October 2026.', '2026-10-29'),
    ('Li Auto Inc. Li Auto delivered 29,025 vehicles in October 2026. Li Auto delivered 29,026 vehicles in October 2026.', '2026-11-02'),
    ('Li Auto Inc. In October 2026, Li Auto delivered 29,025 vehicles in September 2026.', '2026-11-02'),
])
def test_monthly_parser_rejects_unqualified_counts(text, published):
    assert not parse_monthly_vehicle_delivery(BeautifulSoup(text, 'html.parser'), published, 'Li Auto Inc.')
