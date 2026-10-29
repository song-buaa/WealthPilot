"""Isolated P0 semantics: no models, broker, AV or production state."""
from types import SimpleNamespace
from unittest.mock import patch
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from research_v2 import sec_disclosures as sec
from backend.services.market_data.evidence import assess

AT=datetime(2026,10,29,12,tzinfo=timezone.utc)
ROOT=Path(__file__).parent/'fixtures/pdd_sec_q2_2026'


def pdd_original():
    body=gzip.decompress((ROOT/'0001104659-26-100534.txt.gz').read_bytes())
    manifest=json.loads((ROOT/'manifest.json').read_text())
    assert hashlib.sha256(body).hexdigest()==manifest['captured_sha256']
    assert body.rstrip().endswith(b'</SEC-DOCUMENT>')
    return body


def pdd_record(body=None):
    body=body or pdd_original();h=re.search(rb'<SEC-HEADER>(.*?)</SEC-HEADER>',body,re.S)[1].decode()
    cik=re.search(r'CENTRAL INDEX KEY:\s*(\d+)',h)[1]
    acc=re.search(r'ACCESSION NUMBER:\s*([^\n]+)',h)[1].strip()
    accepted=re.search(r'<ACCEPTANCE-DATETIME>(\d{14})',h)[1]
    clock=datetime.strptime(accepted,'%Y%m%d%H%M%S').replace(tzinfo=sec.ZoneInfo('America/New_York'))
    filename=re.search(rb'<DOCUMENT>\s*<TYPE>6-K\s*<SEQUENCE>1\s*<FILENAME>([^\n]+)',body)[1].decode().strip()
    base=f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace("-", "")}/'
    issuer=dict(symbol='PDD:US',issuer_id='PDD_HOLDINGS',cik=cik,name='PDD Holdings Inc.',issuer_type='foreign_private',identity_source=sec.DIRECTORY,submissions_url='https://data.sec.gov/submissions/CIK'+cik+'.json')
    filing=dict(accession=acc,form='6-K',filed=clock.date().isoformat(),report_date=clock.date().isoformat(),published_at=clock.isoformat(),publication_locator='SEC-HEADER/ACCEPTANCE-DATETIME',primary_url=base+filename,full_url=base+acc+'.txt')
    # Explicit offline processing clock. No claim of original network fetched_at.
    return sec.parse_filing((body,AT.isoformat()),issuer,filing,identity_digest='a'*64)


def test_real_PDD_quarter_fields_use_actual_SEC_clock_and_exact_native_cells():
    r=pdd_record();assert r
    rows={o['source_field']:o for o in r['financial_observations'] if o['fiscal_period_end']=='2026-06-30'}
    assert {k:rows[k]['value']*rows[k]['unit_scale'] for k in ('totalRevenue','netIncome','operatingCashflow')}=={'totalRevenue':112358000000,'netIncome':27182000000,'operatingCashflow':25670000000}
    assert rows['operatingIncome']['value']*rows['operatingIncome']['unit_scale']==27764000000
    assert rows['dilutedEPSADS']['value']==18.45 and rows['dilutedEPSADS']['unit_scale']==1
    assert r['published_at']=='2026-08-25T07:20:02-04:00'  # filing clock, not IR Aug 24 or fiscal end
    assert 'capitalExpenditures' not in rows
    assert rows['operatingCashflow']['precision_corroboration']['reported_value']==25700000000
    assert any(x['source_field']=='operatingCashflow' and x['period']=='2026-06-30' and x['issue']=='STATEMENT_COLUMN_AMBIGUITY_CORROBORATED' for x in r['financial_observation_issues'])
    a=assess({},symbol='PDD:US',research=[r],at=AT,disclosure_verifier={r['record_id']:deepcopy(r)}.get)
    assert {f['field'] for f in a['verified_field_records']}=={'revenue_quarter','net_income_quarter','operating_cash_flow_quarter','operating_income_quarter','net_income_attributable_parent_quarter','diluted_eps_ads_quarter','diluted_eps_ordinary_quarter'}
    assert a['qualification']['L1']['allowed']
    assert all(not a['qualification'][l]['allowed'] for l in ('L2','L3','L4'))


def test_pdd_higher_scope_and_currency_not_silently_certified():
    r=pdd_record();bad=deepcopy(r);bad['published_at']='2026-06-30'
    a=assess({},symbol='PDD:US',research=[bad],at=AT,disclosure_verifier={r['record_id']:r}.get)
    assert not a['verified_field_records']
    six=pdd_original().replace(b'three months',b'six months')
    x=pdd_record(six)
    assert x is None or not x['financial_observations']


def test_keyword_optimism_is_not_a_financial_or_personal_rule_pass():
    from backend.graph.tools import execute_generate_signals,execute_check_discipline
    from decision_engine.rule_engine import RuleResult
    from decision_engine.signal_engine import SignalResult,EventSignal
    from app.discipline.config import _DEFAULT_RULES
    loaded=SimpleNamespace(full_discipline_rules=deepcopy(_DEFAULT_RULES))
    rules=RuleResult(current_weight=.127,max_position=.4,position_ratio=.127/.4,violation=False,warning=None,rule_details=['单一持仓正常：当前12.7%，上限40.0%。'])
    sig=SignalResult(position_signal='合理',fundamental_signal='正面',sentiment_signal='中性',event_signal=EventSignal('低','中性'))
    with patch('decision_engine.data_loader.load',return_value=loaded),patch('decision_engine.rule_engine.check',return_value=rules),patch('decision_engine.signal_engine.generate',return_value=sig):
        d=execute_check_discipline('美团');s=execute_generate_signals('美团')
    assert d.violation==rules.violation and d.max_position==.4 and d.current_weight==.127
    assert d.personal_discipline_status=='UNVERIFIED' and '个人纪律未核验' in d.warning
    assert all('单一持仓正常' not in line for line in d.rule_details)
    assert d.raw_rule_details==rules.rule_details
    assert s.fundamental_signal=='UNKNOWN' and s.raw_fundamental_signal=='正面'
    assert s.position_signal.startswith('UNKNOWN') and s.raw_position_signal=='合理'
    assert _DEFAULT_RULES==loaded.full_discipline_rules  # no 6%/8% chat rule persisted


def test_exact_exhibit_identity_does_not_accept_similar_numbers():
    body=pdd_original().replace(b'Exhibit 99.1&mdash;',b'Exhibit 99.10&mdash;')
    # Actual primary uses a named exhibit and description; different exhibit ID
    # must fail even when its file bytes and financial values are unchanged.
    if body==pdd_original():
        body=pdd_original().replace(b'Exhibit 99.1',b'Exhibit 99.10')
    assert body!=pdd_original()
    assert pdd_record(body) is None


def test_native_collect_recovers_pdd_without_request_dates_or_manual_fields(monkeypatch):
    from backend.core import demo_mode
    from backend.services.market_data import cache
    from backend.graph.tools import execute_assess_position_evidence
    from tests.test_sec_financial_sources import directory
    monkeypatch.setattr(demo_mode,'PUBLIC_DEMO_MODE',False)
    monkeypatch.delenv('AV_DEV_MOCK',raising=False)
    monkeypatch.setattr(cache,'_cache',cache.TTLCache())
    original=pdd_record();meta=original['official_disclosure'];acc=meta['accession']
    sub=dict(cik=meta['cik'],name='PDD Holdings Inc.',tickers=['PDD'],filings=dict(recent=dict(
        accessionNumber=[acc,'0001104659-25-000001'],form=['6-K','20-F'],
        filingDate=['2026-08-25','2025-04-01'],reportDate=['2026-08-25','2024-12-31'],
        primaryDocument=['tm2623874d1_6k.htm','annual.htm'],acceptanceDateTime=[original['published_at'],'2025-04-01T12:00:00Z'])))
    calls=[]
    def fetch(url):
        calls.append(url)
        if url==sec.DIRECTORY:return json.dumps(directory()).encode(),AT.isoformat()
        if '/submissions/' in url:return json.dumps(sub).encode(),AT.isoformat()
        if '/companyfacts/' in url:return json.dumps({'cik':1737806,'facts':{}}).encode(),AT.isoformat()
        if url==original['source_url']:return pdd_original(),AT.isoformat()
        return None
    monkeypatch.setattr(sec,'_fetch',fetch)
    a=execute_assess_position_evidence(symbol='PDD:US',market_data={},research=[],at=AT)
    assert {'revenue_quarter','net_income_quarter','operating_cash_flow_quarter'} <= {f['field'] for f in a['verified_field_records']}
    assert 'OFFICIAL_FIELD_OPERATINGCASHFLOW_STATEMENT_COLUMN_AMBIGUITY_CORROBORATED' in a['issues']
    assert a['qualification']['L1']['allowed']
    assert len(calls)==4  # no annual original needed after independent quarters


def test_report_deduplicates_display_only_and_labels_annual_basis_currency():
    from backend.services.position_analysis import exit_plan,report
    r=pdd_record();a=assess({},symbol='PDD:US',research=[r],at=AT,disclosure_verifier={r['record_id']:r}.get)
    a['verified_field_records'] += deepcopy(a['verified_field_records'])
    yearly=deepcopy(a['verified_field_records'][0]);yearly.update(field='revenue_annual',scope='annual',period='2025-12-31')
    yearly['values']={'revenue_annual':100,'currency':'CNY'}
    a['verified_field_records'].append(yearly)
    before=deepcopy(a)
    text=report(a,exit_plan(a,at=AT),'watch')
    assert text.count('季度字段事实 revenue_quarter')==1
    assert '年度经营基线（非最新季度）' in text
    assert '经营财务币种 CNY' in text
    assert a==before  # no source records deleted or relabelled


def test_ambiguous_currency_without_matching_official_precision_stays_unknown():
    body=pdd_original()
    changed=body.replace(b'RMB25.7',b'RMB20.7')
    assert changed!=body
    row=pdd_record(changed)
    assert not any(o['source_field']=='operatingCashflow' and o['fiscal_period_end']=='2026-06-30' for o in row['financial_observations'])
    assert any(x['issue']=='AMBIGUOUS_STATEMENT_COLUMNS' for x in row['financial_observation_issues'])


def test_cashflow_corroboration_requires_unique_period_currency_and_exact_column():
    original=pdd_original()
    changes=[
        (b'second quarter ended June',b'second quarter ended September'),
        (b'RMB25.7',b'US$25.7'),
        # Two exact table values inside the same rounding interval are ambiguous.
        (b'3,783',b'25,680'),
    ]
    for old,new in changes:
        body=original.replace(old,new)
        assert body!=original
        row=pdd_record(body)
        assert row is not None
        assert not any(o['source_field']=='operatingCashflow' and o['fiscal_period_end']=='2026-06-30' for o in row['financial_observations'])
        # Other independently valid fields survive cash-flow uncertainty.
        assert {'totalRevenue','netIncome'} <= {o['source_field'] for o in row['financial_observations']}


def test_real_quarter_facts_reach_original_PEER_context_and_final_projection():
    from backend.agents.contracts import ExpressionOutput
    from backend.services.market_data.output_policy import explanation_prompt,enforce
    row=pdd_record()
    a=assess({},symbol='PDD:US',research=[row],at=AT,disclosure_verifier={row['record_id']:row}.get)
    refs=[r['record_id'] for r in a['verified_field_records']]
    prompt=explanation_prompt(a)
    assert all(ref in prompt for ref in refs)
    text=unicodedata.normalize('NFKC','盈利与经营现金流共同反映经营质量，仍需结合回款节奏和竞争投入复核现金转化的可持续性。')
    out=ExpressionOutput(structured_payload={'decisionType':'watch','professional_explanation':[
        dict(topic='business',basis='inference',text=text,evidence_refs=refs[:3])]})
    enforce(out,a)
    assert out.structured_payload['professional_explanation'][0]['text']==text
    assert text in out.chat_answer
    assert not out.actionable
    assert all(not a['qualification'][level]['allowed'] for level in ('L2','L3','L4'))
    a['verified_field_records']=[]
    # Parent research record is stale; removing qualified fields must remove prose.
    enforce(out,a)
    assert not out.structured_payload['professional_explanation']
