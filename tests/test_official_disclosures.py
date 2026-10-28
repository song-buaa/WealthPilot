"""Original-source fixtures are synthetic; no public network or paid services."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from research_v2.official_disclosures import parse_original, allowed_url, collect
from backend.services.market_data.evidence import assess
from backend.services.market_data.output_policy import explanation_prompt, explanation_projection
from tests.test_v42_evidence_quality import raw_financial

AT = datetime(2026, 10, 25, 0, 17, 22, tzinfo=timezone.utc)
URL = 'https://ir.lixiang.com/news-releases/news-release-details/li-auto-inc-reports-financial-results'


def document(end='June 30, 2026', published='August 15, 2026', revenue='100', income='20', cash='(30)', capex='10'):
    return f'''<html><head><title>Li Auto Inc. Reports Financial Results</title></head>
    <body><div class="field--name-field-nir-news-date">{published}</div>
    <table><tr><th colspan="2">In thousands — Three Months Ended</th></tr>
    <tr><td></td><td>{end}</td></tr><tr><td></td><td>RMB</td></tr>
    <tr><td>Total revenues</td><td>{revenue}</td></tr>
    <tr><td>Net income</td><td>{income}</td></tr>
    <tr><td>Net cash provided by operating activities</td><td>{cash}</td></tr>
    <tr><td>Capital expenditures</td><td>{capex}</td></tr></table></body></html>'''.encode()


def source(body=None):
    return parse_original(body or document(), URL, AT.isoformat())


def evaluate(rows, raw=None):
    rows = deepcopy(rows)
    return assess({'fundamentals':raw or raw_financial(False)},symbol='LI:US',research=rows,at=AT,
                  disclosure_verifier={r['record_id']:deepcopy(r) for r in rows}.get)


def test_original_quarter_enters_L1_context_and_qualitative_explanation_without_TTM_upgrade():
    row=source();assert row and len(row['financial_observations'])==4
    a=evaluate([row]);facts=a['verified_field_records']
    assert {r['field'] for r in facts}=={'revenue_quarter','net_income_quarter','operating_cash_flow_quarter','capital_expenditures_quarter'}
    assert len({r['record_id'] for r in facts})==4
    assert next(r for r in facts if r['field']=='operating_cash_flow_quarter')['values']['operating_cash_flow_quarter']==-30000
    assert 'revenue_quarter' in explanation_prompt(a)
    refs=[r['record_id'] for r in facts if r['field'] in ('net_income_quarter','operating_cash_flow_quarter')]
    candidate=dict(topic='business',basis='inference',text='利润与经营现金流方向背离，需要结合回款与营运资本变化检验盈利质量；不能仅凭账面盈利确认经营改善。',evidence_refs=refs)
    assert explanation_projection([candidate],a)[0]
    assert a['qualification']['L1']['allowed']
    assert all(not a['qualification'][l]['allowed'] for l in ('L2','L3','L4'))
    assert not any(r['field'].endswith('_ttm') for r in facts)


@pytest.mark.parametrize('change', ['date_missing','future','issuer','period','currency','units','duplicate','malformed','annual'])
def test_unproven_original_fields_remain_unknown(change):
    body=document()
    if change=='date_missing':body=body.replace(b'field--name-field-nir-news-date',b'unknown')
    if change=='future':body=document(published='August 15, 2027')
    if change=='issuer':body=body.replace(b'Li Auto Inc.',b'Unrelated Company')
    if change=='period':body=body.replace(b'Three Months',b'Six Months')
    if change=='currency':body=body.replace(b'RMB',b'USD')
    if change=='units':body=body.replace(b'In thousands',b'Units unknown')
    if change=='duplicate':body=body.replace(b'</table>',b'<tr><td>Total revenues</td><td>999</td></tr></table>')
    if change=='malformed':body=body.replace(b'100</td>',b'not-a-number</td>')
    if change=='annual':body=body.replace(b'Three Months Ended',b'Year Ended')
    r=source(body)
    if change in {'duplicate','malformed'}:
        assert r and 'totalRevenue' not in {o['source_field'] for o in r['financial_observations']}
    else:assert r is None


def test_card_self_claims_never_supply_source_authority():
    forged=source();forged['financial_observations'][0]['value']=999999
    assert not assess({'fundamentals':raw_financial(False)},symbol='LI:US',research=[forged],at=AT)['verified_field_records']
    independent=source()
    a=assess({'fundamentals':raw_financial(False)},symbol='LI:US',research=[forged],at=AT,
             disclosure_verifier={independent['record_id']:independent}.get)
    assert not a['verified_field_records']


def test_fetch_is_server_owned_bounded_cached_and_preserves_source_clock(monkeypatch):
    from backend.core import demo_mode
    from backend.services.market_data.cache import _cache
    import research_v2.official_disclosures as owner
    _cache.clear();monkeypatch.setattr(demo_mode,'PUBLIC_DEMO_MODE',False);monkeypatch.delenv('AV_DEV_MOCK',raising=False)
    calls=[]
    def fetch(url):
        calls.append(url)
        if url==owner.IR_INDEX:return f'<a href="{URL}">result</a>'.encode(),AT.isoformat()
        return document(), AT.isoformat()
    monkeypatch.setattr(owner,'_fetch',fetch)
    rows,verifier=collect('LI:US',[dict(source_url=URL,published_at='2099-01-01')])
    assert rows and verifier(rows[0]['record_id'])['published_at']=='2026-08-15'
    again,_=collect('LI:US');assert again==rows and len(calls)==2
    assert rows[0]['fetched_at']==AT.isoformat()
    monkeypatch.setattr(demo_mode,'PUBLIC_DEMO_MODE',True)
    assert collect('LI:US')[0]==[]
    assert len(calls)==2
    _cache.clear()


@pytest.mark.parametrize('url',['http://ir.lixiang.com/news-releases/news-release-details/a','https://ir.lixiang.com.evil.test/a',
    'https://user@ir.lixiang.com/news-releases/news-release-details/a','https://127.0.0.1/a',
    'https://www.sec.gov/Archives/edgar/data/not-a-cik/001.txt','https://ir.lixiang.com/news-releases/news-release-details/a?redirect=evil'])
def test_url_boundary(url):assert not allowed_url(url)


def test_sec_original_binds_filer_identity_path_acceptance_and_document_hash():
    url='https://www.sec.gov/Archives/edgar/data/12345/000000000026000001.txt'
    header=b'''<SEC-HEADER><ACCEPTANCE-DATETIME>20260815123000
    COMPANY DATA:
    COMPANY CONFORMED NAME: Li Auto Inc.
    CENTRAL INDEX KEY: 0000012345
    FILING VALUES:
    CONFORMED SUBMISSION TYPE: 6-K
    </SEC-HEADER>'''
    row=parse_original(header+document(),url,AT.isoformat())
    assert row and row['published_at']=='2026-08-15'
    assert 'SEC-HEADER' in row['source_verification']['publication_locator']
    assert parse_original(header.replace(b'Li Auto Inc.',b'Other Company')+document(),url,AT.isoformat()) is None
    assert parse_original(header+document(),url.replace('/12345/','/67890/'),AT.isoformat()) is None
    assert parse_original(document(),url,AT.isoformat()) is None


def matched_originals(raw):
    from datetime import timedelta
    rows=[]
    for q in raw['field_provenance']['revenue_ttm']['observations']:
        end=datetime.fromisoformat(q['fiscalDateEnding'])
        date=end.strftime('%B %d, %Y');published=(end+timedelta(days=45)).strftime('%B %d, %Y')
        # Fixture units are thousands; AV units are CNY units.
        body=document(end=date,published=published,revenue='0.1',income='0.01',cash='0.02',capex='0.03')
        rows.append(parse_original(body,URL,AT.isoformat()))
    assert all(rows)
    return rows


def test_all_four_independent_originals_required_for_each_TTM_dependency():
    raw=raw_financial(False);rows=matched_originals(raw)
    a=evaluate(rows,raw);fields={r['field']:r for r in a['verified_field_records']}
    assert {'revenue_ttm','net_income_ttm','operating_cash_flow_ttm','capital_expenditures_ttm','free_cash_flow_ttm'} <= fields.keys()
    assert fields['revenue_ttm']['values']['revenue_ttm']==400
    assert fields['free_cash_flow_ttm']['values']['free_cash_flow_ttm']==-40
    assert len(fields['free_cash_flow_ttm']['publication_links'])==8
    assert len({r['fiscal_period_end'] for r in fields['revenue_ttm']['publication_links']})==4
    partial=evaluate(rows[:1],raw)
    assert {r['field'] for r in partial['verified_field_records']}=={'revenue_quarter','net_income_quarter','operating_cash_flow_quarter','capital_expenditures_quarter'}
    assert raw['field_provenance']['revenue_ttm']['published_at'] is None


def test_conflict_stays_field_local_and_valid_quarter_survives():
    raw=raw_financial(False);rows=matched_originals(raw)
    # Independently parsed source revenue differs, while other source facts agree.
    rows[0]=source(document(revenue='999',income='0.01',cash='0.02',capex='0.03'))
    a=evaluate(rows,raw);fields={r['field'] for r in a['verified_field_records']}
    assert 'revenue_ttm' not in fields and 'net_income_ttm' in fields and 'free_cash_flow_ttm' in fields
    assert 'revenue_quarter' in fields
    assert any(c['field']=='revenue_ttm' for c in a['source_conflicts'])
    assert raw['revenue_ttm']==400


def test_actual_tool_chain_injects_server_source_lookup_not_card_claim(monkeypatch):
    from backend.graph.tools import execute_assess_position_evidence
    import research_v2.official_disclosures as owner
    row=source();calls=[]
    def collect_sources(symbol,candidates):
        calls.append(symbol);return [row],{row['record_id']:deepcopy(row)}.get
    monkeypatch.setattr(owner,'collect',collect_sources)
    result=execute_assess_position_evidence(market_data={'fundamentals':raw_financial(False)},symbol='LI:US',at=AT)
    assert calls==['LI:US'] and any(r['field']=='net_income_quarter' for r in result['verified_field_records'])
    assert all(not result['qualification'][l]['allowed'] for l in ('L2','L3','L4'))


def test_mixed_quarter_and_YTD_columns_keep_only_the_quarter():
    body=document()
    a=body.index(b'<table>');b=body.index(b'</table>')+len(b'</table>')
    body=body[:a]+b'''<table><tr><th colspan="3">In thousands</th></tr>
    <tr><td></td><td>Three Months Ended</td><td>Six Months Ended</td></tr>
    <tr><td></td><td>June 30, 2026</td><td>June 30, 2026</td></tr>
    <tr><td></td><td>RMB</td><td>RMB</td></tr>
    <tr><td>Net cash provided by operating activities</td><td>(30)</td><td>999</td></tr></table>'''+body[b:]
    row=source(body)
    cash=row['financial_observations']
    assert len(cash)==1 and cash[0]['source_field']=='operatingCashflow' and cash[0]['value']==-30


def test_stale_quarter_is_not_revived_by_fetch_time():
    row=source(document(end='March 31, 2025',published='May 15, 2025'))
    assert row and not evaluate([row])['verified_field_records']


def test_publication_headers_must_agree_and_no_date_is_inferred_from_URL():
    body=document().replace(b'</head>',b'<meta property="article:published_time" content="2026-08-16T00:00:00Z"></head>')
    assert source(body) is None
    assert parse_original(document(published=''), URL+'/20260815', AT.isoformat()) is None


def test_original_release_time_retains_real_timezone_clock():
    row=source(document(published='August 15, 2026 at 4:30 AM EDT'))
    assert row and row['published_at']=='2026-08-15T04:30:00-04:00'
    assert 'timezone-aware timestamp' in row['source_verification']['publication_locator']


def test_verified_quarter_survives_final_shared_policy_without_precise_actions():
    from backend.agents.contracts import ExpressionOutput
    from backend.services.market_data.output_policy import enforce
    from decision_engine.llm_engine import LLMResult
    a=evaluate([source()]);ref=next(r['record_id'] for r in a['verified_field_records'] if r['field']=='operating_cash_flow_quarter')
    text='经营现金流仍需结合回款和营运资本变动检验,账面盈利并不能单独证明现金转化能力已经改善。'
    out=ExpressionOutput(llm_result=LLMResult('SELL',['unsafe 999999'],[],[]),structured_payload={
        'decisionType':'trim','professional_explanation':[dict(topic='business',basis='inference',text=text,evidence_refs=[ref])]})
    enforce(out,a);enforce(out,a)
    assert out.structured_payload['professional_explanation'][0]['text']==text
    assert '999999' not in str(out) and not out.actionable
    assert out.structured_payload['exitPlan']['manual_trade_preparation_allowed'] is False
    assert 'operating_cash_flow_quarter' in out.chat_answer


def test_invalid_date_suffix_is_not_accepted_as_ISO_publication():
    body=document().replace(b'</head>',b'<meta property="article:published_time" content="2026-08-15-forged"></head>')
    assert source(body) is None


@pytest.mark.parametrize('currency',[b'US$',b'USD',b'HK$',b'$',b'EUR',b'unknown'])
def test_USD_translation_column_cannot_borrow_global_RMB_unit_header(currency):
    body=document().replace(b'In thousands', b'RMB in thousands').replace(b'<td>RMB</td>',b'<td>'+currency+b'</td>')
    assert source(body) is None


@pytest.mark.parametrize('value',['2026-10-25T23:00:00Z','October 25, 2026 at 4:30 AM EDT','2026-10-25T01:00:00'])
def test_same_day_future_or_timezone_unknown_publication_never_qualifies(value):
    body=document(published=value)
    assert source(body) is None


def test_TTM_uses_latest_real_clock_across_timezones_not_string_order():
    raw=raw_financial(False);rows=[]
    for i,q in enumerate(raw['field_provenance']['revenue_ttm']['observations']):
        clock='2026-08-15T04:00:00-04:00' if i==0 else '2026-08-15T10:00:00+04:00'
        body=document(end=datetime.fromisoformat(q['fiscalDateEnding']).strftime('%B %d, %Y'),
            published=clock,revenue='0.1',income='0.01',cash='0.02',capex='0.03')
        rows.append(source(body))
    a=evaluate(rows,raw)
    field=next(r for r in a['verified_field_records'] if r['field']=='revenue_ttm')
    assert field['published_at']=='2026-08-15T04:00:00-04:00'
