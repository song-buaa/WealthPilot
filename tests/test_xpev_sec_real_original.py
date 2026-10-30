"""Frozen, unedited public XPEV 6-K: source qualification and row identity."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
from pathlib import Path

import pytest

from research_v2 import sec_disclosures as sec
from backend.services.market_data.evidence import assess
from backend.services.market_data.financial_research import build


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
    observed = {o['source_field']: o for o in row['financial_observations']}
    assert {name: observed[name]['value'] for name in
            ('vehicleDeliveries', 'vehicleSales', 'vehicleMargin')} == {
                'vehicleDeliveries': 103295, 'vehicleSales': 17050000000, 'vehicleMargin': 12.1}
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
        'vehicle_revenue_per_delivery_approx'] == pytest.approx(17050000000 / 103295)
    assert hashlib.sha256(body).hexdigest() == SHA256


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
