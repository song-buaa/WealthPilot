"""Frozen public HKEX originals: issuer, publication and quarter qualification."""
from datetime import datetime, timezone
from pathlib import Path
from copy import deepcopy
import gzip

from research_v2 import official_disclosures
from backend.services.market_data.evidence import assess
from backend.services.market_data.financial_research import build


FIXTURES = Path(__file__).parent / 'fixtures' / 'official_hkex'
FETCHED_AT = '2026-11-03T09:00:00+00:00'
DECISION_AT = datetime(2026, 11, 3, 10, tzinfo=timezone.utc)


def _collect(monkeypatch):
    index = gzip.decompress((FIXTURES / 'meituan_index.html.gz').read_bytes())
    pdf = (FIXTURES / 'meituan_2026_q2.pdf').read_bytes()
    monkeypatch.setattr(official_disclosures, '_hkex_fetch',
                        lambda url: (index if url == official_disclosures._HKEX_MEITUAN_INDEX else pdf,
                                     FETCHED_AT))
    return official_disclosures.collect_hkex('3690:HK')


def test_public_original_enters_original_evidence_owner(monkeypatch):
    rows, verifier = _collect(monkeypatch)
    assert len(rows) == 1
    assert rows[0]['source_verification']['document_sha256'] == '7ce5fad275ba07f815774eb6ce7364e05318fdbc96263a610a560ed29a3c9504'
    assert rows[0]['published_at'] == '2026-08-28T16:30:00+08:00'
    assert '管理层' in rows[0]['content'] and '不能证明集团各区域利润' in rows[0]['content']
    result = assess({'symbol':'3690:HK'}, symbol='3690:HK', research=rows,
                    disclosure_verifier=verifier, at=DECISION_AT)
    fields = {row['field']: row for row in result['verified_field_records']}
    assert fields['revenue_quarter']['values']['revenue_quarter'] == 104_643_044_000
    assert fields['operating_income_quarter']['values']['operating_income_quarter'] == 2_691_166_000
    assert fields['net_income_quarter']['values']['net_income_quarter'] == 2_155_222_000
    assert fields['operating_cash_flow_quarter']['values']['operating_cash_flow_quarter'] == 9_733_444_000
    assert fields['core_local_commerce_revenue_quarter']['values']['core_local_commerce_revenue_quarter'] == 71_530_555_000
    assert fields['new_initiatives_operating_income_quarter']['values']['new_initiatives_operating_income_quarter'] == -1_738_915_000
    assert 'capital_expenditures_quarter' not in fields
    assert 'free_cash_flow_quarter' not in fields
    assert any(row['field'] == 'revenue_quarter' and row['period'] == '2025-06-30'
               for row in result['historical_field_records'])
    assert all(row['publication_links'][0]['source_document_sha256'] == rows[0]['source_verification']['document_sha256']
               for row in result['verified_field_records'])
    derived = {row['field']: row for row in build(result)['records']}
    assert round(derived['revenue_yoy_pct']['values']['revenue_yoy_pct'],2) == 14.38
    assert round(derived['core_local_commerce_operating_margin_pct']['values']['core_local_commerce_operating_margin_pct'],2) == 7.92
    assert derived['new_initiatives_operating_margin_pct']['values']['new_initiatives_operating_margin_pct'] < 0
    assert 'pe_ttm' not in derived
    from backend.services.market_data.output_policy import facts_only_result
    projected=facts_only_result(result,at=DECISION_AT,professional_delivered=False)
    assert projected and '专业推断未完成' in projected[2]
    assert '完整来源、时钟与原始字段见结构化 Evidence' in projected[2]
    assert len(projected[1]) >= len(fields)


def test_self_reported_or_wrong_security_cannot_claim_official_status(monkeypatch):
    rows, verifier = _collect(monkeypatch)
    original = rows[0]
    variants = [dict(original, symbol='LI:US'),
                dict(original, source_url='https://example.org/claim.pdf'),
                dict(original, published_at='2026-08-01T00:00:00+08:00')]
    for card in variants:
        result = assess({'symbol':'3690:HK'}, symbol='3690:HK', research=[card],
                        disclosure_verifier=verifier, at=DECISION_AT)
        assert not result['verified_field_records']
    result = assess({'symbol':'3690:HK'}, symbol='3690:HK', research=rows,
                    disclosure_verifier=None, at=DECISION_AT)
    assert not result['verified_field_records']
    assert official_disclosures.collect_hkex('LI:US')[0] == []


def test_pdf_non_ifrs_and_six_month_values_do_not_become_quarters():
    original = (FIXTURES / 'meituan_2026_q2.pdf').read_bytes()
    rows = official_disclosures._hkex_quarter_observations(original)
    assert all(row['period_basis'] == 'quarterly' for row in rows)
    assert not any(row['value'] in {195_681_950, 4_098_050, 2_523_716}
                   for row in rows)
    assert all(row['fiscal_period_end'] in {'2026-06-30','2025-06-30'} for row in rows)


def test_hkex_cash_column_order_and_management_claims_fail_closed():
    from pypdf import PdfReader
    from io import BytesIO
    original = (FIXTURES / 'meituan_2026_q2.pdf').read_bytes()
    pages = PdfReader(BytesIO(original)).pages
    cash = pages[22].extract_text()
    assert official_disclosures._hkex_cash_quarter(cash) == 9_733_444
    reversed_columns = cash.replace('Three Months\nEnded', 'TEMP\nEnded', 1).replace(
        'Six Months\nEnded', 'Three Months\nEnded', 1).replace('TEMP\nEnded', 'Six Months\nEnded', 1)
    assert official_disclosures._hkex_cash_quarter(reversed_columns) is None
    commentary=' '.join((pages[i].extract_text() or '') for i in (5,6,7))
    assert official_disclosures._hkex_business_context_text(commentary)
    assert official_disclosures._hkex_business_context_text(
        commentary.replace('the business achieved stable profitability', 'the business incurred losses')) is None


def test_hkex_currency_unit_headers_are_required(monkeypatch):
    from pypdf import PdfReader
    from io import BytesIO
    from types import SimpleNamespace
    original=(FIXTURES / 'meituan_2026_q2.pdf').read_bytes()
    actual=PdfReader(BytesIO(original))
    texts=[page.extract_text() or '' for page in actual.pages]
    texts[0]=texts[0].replace('(RMB in thousands, except for percentages)', '(RMB in millions, except for percentages)')
    fake=[SimpleNamespace(extract_text=lambda value=text: value) for text in texts]
    monkeypatch.setattr('pypdf.PdfReader',lambda _:SimpleNamespace(pages=fake))
    assert official_disclosures._hkex_quarter_observations(original)==[]
    texts[0]=texts[0].replace('(RMB in millions, except for percentages)', '(RMB in thousands, except for percentages)')
    texts[2]=texts[2].replace('(RMB in thousands)', '(RMB in millions)')
    fake=[SimpleNamespace(extract_text=lambda value=text: value) for text in texts]
    rows=official_disclosures._hkex_quarter_observations(original)
    assert rows and not any(row['source_field'].startswith(('coreLocal','newInitiatives')) for row in rows)
    texts[2]=(actual.pages[2].extract_text() or '').replace(
        'Core Local\nCommerce\nNew\nInitiatives',
        'New\nInitiatives\nCore Local\nCommerce')
    fake=[SimpleNamespace(extract_text=lambda value=text: value) for text in texts]
    rows=official_disclosures._hkex_quarter_observations(original)
    assert rows and not any(row['source_field'].startswith(('coreLocal','newInitiatives')) for row in rows)


def test_existing_skill_tool_collects_hkex_without_caller_cards(monkeypatch):
    monkeypatch.setenv('PUBLIC_DEMO_MODE','false')
    monkeypatch.setenv('AV_DEV_MOCK','1')
    rows,verifier=_collect(monkeypatch)
    monkeypatch.setattr(official_disclosures,'collect_hkex',lambda symbol:(rows,verifier))
    from backend.graph.tools import execute_assess_position_evidence
    result=execute_assess_position_evidence(symbol='3690:HK',market_data={'symbol':'3690:HK'},
                                            research=[],at=DECISION_AT)
    assert {r['field'] for r in result['verified_field_records']} >= {
        'revenue_quarter','operating_income_quarter','net_income_quarter',
        'core_local_commerce_revenue_quarter','new_initiatives_operating_income_quarter'}
