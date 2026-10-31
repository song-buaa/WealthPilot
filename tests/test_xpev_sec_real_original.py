"""Frozen, unedited public XPEV 6-K: source qualification and row identity."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from research_v2 import sec_disclosures as sec
from backend.services.market_data.evidence import assess
from backend.services.market_data.financial_research import build
from backend.services.market_data.output_policy import explanation_prompt, explanation_projection


ORIGINAL = Path(__file__).parent / 'fixtures/xpev_sec_q2_2026/0001193125-26-364050.txt.gz'
SHA256 = '9062a8f5df8da1f4e8f2e8734e6820315489ed3ea820b4ee1dbc33249d375152'
CLOCK = '2026-10-30T12:00:00+00:00'  # Explicit offline processing clock; historical fetch time UNKNOWN.
BASE = 'https://www.sec.gov/Archives/edgar/data/1810997/000119312526364050/'


def original():
    body = gzip.decompress(ORIGINAL.read_bytes())
    assert len(body) == 530332 and hashlib.sha256(body).hexdigest() == SHA256
    return body


def identity():
    issuer = dict(symbol='XPEV:US', issuer_id='XPENG', cik='0001810997', name='XPeng Inc.',
                  issuer_type='foreign_private', identity_source=sec.DIRECTORY,
                  submissions_url='https://data.sec.gov/submissions/CIK0001810997.json')
    filing = dict(accession='0001193125-26-364050', form='6-K', filed='2026-08-25',
                  report_date='2026-08-25', published_at='2026-08-25T06:03:30-04:00',
                  publication_locator='SEC-HEADER/ACCEPTANCE-DATETIME',
                  primary_url=BASE+'d75446d6k.htm', full_url=BASE+'0001193125-26-364050.txt')
    return issuer, filing


def parse(body=None, issuer=None, filing=None):
    i, f = identity()
    return sec.parse_filing((original() if body is None else body, CLOCK),
                            issuer or i, filing or f, identity_digest='a'*64)


def test_real_original_exhibit_qualifies_three_operating_fields_and_shared_calculation():
    body = original()
    row = parse(body)
    assert row and row['source_verification']['document_sha256'] == SHA256
    observed = {o['source_field']: o for o in row['financial_observations']
                if o['fiscal_period_end'] == '2026-06-30'}
    assert {name: observed[name]['value'] * observed[name]['unit_scale'] for name in
            ('vehicleDeliveries', 'vehicleSales', 'vehicleMargin')} == {
                'vehicleDeliveries': 103295, 'vehicleSales': 17046476000, 'vehicleMargin': 12.1}
    assert all(observed[name]['document_filename'] == 'd75446dex991.htm' and
               observed[name]['document_type'] == 'EX-99.1' and
               observed[name]['fiscal_period_end'] == '2026-06-30' for name in observed)
    assert observed['vehicleSales']['currency'] == 'CNY'
    a = assess({}, symbol='XPEV:US', research=[deepcopy(row)],
               at=datetime(2026, 10, 30, 12, tzinfo=timezone.utc),
               disclosure_verifier={row['record_id']: deepcopy(row)}.get)
    fields = {r['field'] for r in a['verified_field_records']}
    assert {'vehicle_deliveries_quarter', 'vehicle_sales_quarter', 'vehicle_margin_pct'} <= fields
    calculated = {r['field']: r for r in build(a)['records']}
    assert calculated['vehicle_revenue_per_delivery_approx']['values'][
        'vehicle_revenue_per_delivery_approx'] == pytest.approx(17046476000 / 103295)
    assert hashlib.sha256(body).hexdigest() == SHA256


def test_real_original_group_financial_bridge_and_comparative_columns_reach_peer():
    row = parse()
    assert row and row['source_verification']['document_sha256'] == SHA256
    a = assess({}, symbol='XPEV:US', research=[deepcopy(row)],
               at=datetime(2026, 10, 30, 12, tzinfo=timezone.utc),
               disclosure_verifier={row['record_id']: deepcopy(row)}.get)
    fields = {(r['field'], r['period']): r for r in
              a['verified_field_records'] + a['historical_field_records']}
    expected = {
        ('revenue_quarter', '2026-06-30'): 19743593000,
        ('operating_income_quarter', '2026-06-30'): -1142530000,
        ('net_income_quarter', '2026-06-30'): -1337064000,
        ('vehicle_sales_quarter', '2026-06-30'): 17046476000,
        ('vehicle_sales_quarter', '2025-06-30'): 16883696000,
        ('vehicle_deliveries_quarter', '2025-06-30'): 103181,
        ('vehicle_margin_pct', '2025-06-30'): 14.3,
    }
    for key, value in expected.items():
        assert fields[key]['values'][key[0]] == value
        assert fields[key]['publication_links'][0]['source_document_sha256'] == SHA256
        assert fields[key]['publication_links'][0]['document_type'] == 'EX-99.1'
        assert 'table[30]' not in fields[key]['publication_links'][0]['source_locator']
    assert not any(r['field'].startswith('diluted_eps_') for r in fields.values())
    sales = fields['vehicle_sales_quarter', '2026-06-30']['publication_links'][0]
    assert sales['precision_corroboration']['relation'] == 'same_exhibit_rounded_summary'
    assert sales['precision_corroboration']['summary_value'] == 17050000000
    metrics = {r['field']: r for r in build(a)['records']}
    assert metrics['revenue_yoy_pct']['values']['revenue_yoy_pct'] == pytest.approx(
        (19743593 / 18274405 - 1) * 100)
    assert metrics['revenue_qoq_pct']['values']['revenue_qoq_pct'] == pytest.approx(
        (19743593 / 13033781 - 1) * 100)
    assert metrics['vehicle_sales_yoy_pct']['values']['vehicle_sales_yoy_pct'] == pytest.approx(
        (17046476 / 16883696 - 1) * 100)
    assert metrics['vehicle_sales_qoq_pct']['values']['vehicle_sales_qoq_pct'] == pytest.approx(
        (17046476 / 10999321 - 1) * 100)
    assert metrics['gross_margin_pct']['values']['gross_margin_pct'] == pytest.approx(
        (19743593 - 15660111) / 19743593 * 100)
    assert 'free_cash_flow_quarter' not in metrics
    assert 'pe_ttm' not in metrics
    a['financial_research'] = build(a)
    prompt = explanation_prompt(a)
    for name in ('revenue_quarter', 'revenue_qoq_pct', 'operating_income_quarter', 'net_income_quarter',
                 'vehicle_sales_yoy_pct', 'vehicle_margin_change_pp', 'gross_margin_pct'):
        assert name in prompt
    refs = [metrics[name]['record_id'] for name in
            ('revenue_yoy_pct', 'vehicle_deliveries_yoy_pct', 'vehicle_margin_change_pp', 'gross_margin_pct')]
    candidate = dict(topic='business', basis='inference',
        text='集团收入同比{{n1}}，同期交付同比{{n2}}；车辆毛利率同比变化{{n3}}，集团综合毛利率为{{n4}}。收入与交付并非同一确认口径，产品结构和成本驱动仍待正式披露核验。',
        evidence_refs=refs, numbers=[dict(id=f'n{i}', evidence_ref=ref, field=field)
            for i, (ref, field) in enumerate(zip(refs,
                ('revenue_yoy_pct', 'vehicle_deliveries_yoy_pct', 'vehicle_margin_change_pp', 'gross_margin_pct')), 1)])
    accepted, diagnostics = explanation_projection([candidate], a)
    assert diagnostics['accepted_count'] == 1
    assert all(mark in accepted[0]['text'] for mark in ('集团收入同比', '同期交付同比', '车辆毛利率同比', '集团综合毛利率'))


def test_same_exhibit_summary_disagreement_does_not_get_rounded_into_exact_sales():
    altered = original().replace(b'RMB17.05', b'RMB17.55')
    assert altered != original()
    row = parse(body=altered)
    sales = [o for o in row['financial_observations']
             if o['source_field'] == 'vehicleSales' and o['fiscal_period_end'] == '2026-06-30']
    assert not sales  # The original filing's conflicting pair fails closed for this field.
    a = assess({}, symbol='XPEV:US', research=[deepcopy(row)],
               at=datetime(2026, 10, 30, 12, tzinfo=timezone.utc),
               disclosure_verifier={row['record_id']: deepcopy(row)}.get)
    metrics = {r['field'] for r in build(a)['records']}
    assert 'vehicle_sales_yoy_pct' not in metrics
    assert 'gross_margin_pct' in metrics  # Unrelated group income statement survives.


def test_offline_collect_to_peer_replays_real_original_with_identity_metadata(monkeypatch):
    from backend.core import demo_mode
    from backend.services.market_data import cache
    monkeypatch.setattr(demo_mode, 'PUBLIC_DEMO_MODE', False)
    monkeypatch.delenv('AV_DEV_MOCK', raising=False)
    directory = {'0': dict(cik_str=1810997, ticker='XPEV', title='XPeng Inc.')}
    submissions = dict(cik=1810997, tickers=['XPEV'], name='XPeng Inc.', filings=dict(recent={
        'accessionNumber': ['0001193125-26-364050'], 'form': ['6-K'],
        'filingDate': ['2026-08-25'], 'reportDate': ['2026-08-25'],
        'primaryDocument': ['d75446d6k.htm'], 'acceptanceDateTime': ['2026-08-25T10:03:30Z']}))
    _, filing = identity()
    sources = {sec.DIRECTORY: json.dumps(directory).encode(),
               'https://data.sec.gov/submissions/CIK0001810997.json': json.dumps(submissions).encode(),
               'https://data.sec.gov/api/xbrl/companyfacts/CIK0001810997.json':
                   json.dumps(dict(cik=1810997, facts={})).encode(),
               filing['full_url']: original()}
    calls = []
    def frozen_fetch(url):
        calls.append(url)
        return (sources[url], CLOCK) if url in sources else None
    monkeypatch.setattr(sec, '_fetch', frozen_fetch)
    monkeypatch.setattr(cache, 'get_cached', lambda _: None)
    monkeypatch.setattr(cache, 'set_cached', lambda *args: None)
    rows, verifier = sec.collect('XPEV:US')
    assert filing['full_url'] in calls
    real = next(r for r in rows if r['source_verification']['document_sha256'] == SHA256)
    a = assess({}, symbol='XPEV:US', research=rows,
               at=datetime(2026, 10, 30, 12, tzinfo=timezone.utc),
               disclosure_verifier=verifier)
    assert any(r['field'] == 'revenue_quarter' for r in a['verified_field_records'])
    a['financial_research'] = build(a)
    assert 'gross_margin_pct' in explanation_prompt(a)


@pytest.mark.parametrize('mutation', ('wrong_row', 'wrong_cell', 'multiple_links',
                                      'wrong_href', 'wrong_type', 'wrong_filename',
                                      'wrong_cik', 'wrong_accession', 'wrong_clock'))
def test_real_original_exhibit_identity_stays_exact(mutation):
    body = original()
    issuer, filing = identity()
    if mutation == 'wrong_row':
        body = body.replace(b'Exhibit&nbsp;99.1', b'Exhibit&nbsp;98.1', 1)
    elif mutation == 'wrong_cell':
        body = body.replace(b'Exhibit&nbsp;99.1', b'Other attachment', 1)
    elif mutation == 'multiple_links':
        body = body.replace(b'<A HREF="d75446dex991.htm">',
                            b'<A HREF="other.htm">Other</A><A HREF="d75446dex991.htm">', 1)
    elif mutation == 'wrong_href':
        body = body.replace(b'HREF="d75446dex991.htm"', b'HREF="other.htm"', 1)
    elif mutation == 'wrong_type':
        body = body.replace(b'<TYPE>EX-99.1', b'<TYPE>GRAPHIC', 1)
    elif mutation == 'wrong_filename':
        body = body.replace(b'<FILENAME>d75446dex991.htm', b'<FILENAME>other.htm', 1)
    elif mutation == 'wrong_cik':
        issuer['cik'] = '0001810998'
    elif mutation == 'wrong_accession':
        filing['accession'] = '0001193125-26-364051'
    else:
        filing['published_at'] = '2026-08-25T07:03:30-04:00'
    row = parse(body, issuer, filing)
    assert row is None or not any(o['source_field'].startswith('vehicle') for o in row['financial_observations'])


def test_exhibit_number_in_another_cover_row_cannot_authorize_a_link():
    from bs4 import BeautifulSoup
    cover = BeautifulSoup('''<table><tr><td>Exhibit 99.1</td><td>Other attachment</td></tr>
      <tr><td>Other document</td><td><a href="d75446dex991.htm">Press Release</a></td></tr></table>''',
      'html.parser')
    assert not sec._declared_exhibit(cover, 'd75446dex991.htm', 'EX-99.1')
