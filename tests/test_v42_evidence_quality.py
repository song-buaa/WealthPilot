"""Evidence-chain regressions: frozen clocks and synthetic transports, never live IO."""
import copy
import json
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
from unittest.mock import patch

import pandas as pd
import pytest

from backend.services.market_data import av_fundamentals_service as av
from backend.services.market_data.evidence import assess, record, safe_market
from backend.services.market_data.output_policy import _evidence_index, explanation_projection, explanation_prompt
from backend.services.market_data.schema import FundamentalsData, MarketDataBundle
from backend.services.market_data.technical_evidence import from_bars
from backend.services.execution_plan.kline_provider import KlineResult, AVKlineProvider
from tests.test_v42_fundamental_clocks import statements

AT=datetime(2026,10,25,0,17,22,tzinfo=timezone.utc)


def raw_financial(published=True):
    income,cash=statements(published=published)
    values,meta,_=av.statement_metrics('LI',income,cash,fetched_at=AT.isoformat())
    return dict(symbol='LI:US',source='alphavantage',record_id='av:public-statement',
                fetched_at=AT.isoformat(),field_provenance=meta,**values)


@pytest.mark.parametrize('name',av.AV_KEY_NAMES)
def test_every_worker_key_name_resolves_without_credential_logging(name):
    with patch.dict('os.environ',{name:'PRIVATE_SENTINEL'},clear=True):
        assert av.get_next_av_key()=='PRIVATE_SENTINEL'
        assert av.av_identity(av.get_next_av_key())['configuration_slot']==name
        assert 'PRIVATE_SENTINEL' not in json.dumps(av.av_identity(av.get_next_av_key()))


def test_live_priority_never_rotates_to_a_different_account():
    with patch.dict('os.environ',{'ALPHA_VANTAGE_API_KEY':'canonical','AV_API_KEY_1':'old'},clear=True):
        assert [av.get_next_av_key() for _ in range(4)]==['canonical']*4
        import os
        os.environ['ALPHA_VANTAGE_API_KEY']='replacement'
        assert av.get_next_av_key()=='replacement'
    with patch.dict('os.environ',{'AV_API_KEY_1':'first','AV_API_KEY_2':'second'},clear=True):
        assert [av.get_next_av_key() for _ in range(4)]==['first']*4


def test_operator_AV_file_cannot_inherit_another_parent_account(tmp_path):
    from pocs.chatgpt_skills.worker import load_analysis_environment
    file=tmp_path/'operator.json';file.write_text(json.dumps({'AV_API_KEY':'new'}))
    with patch.dict('os.environ',{'ALPHA_VANTAGE_API_KEY':'parent'},clear=True):
        load_analysis_environment(file)
        assert av.get_next_av_key()=='new'


def test_success_cache_is_bound_to_configuration_and_original_clock(monkeypatch):
    cache={};calls=[]
    monkeypatch.setattr(av,'get_cached',cache.get)
    monkeypatch.setattr(av,'set_cached',lambda k,v,t:cache.__setitem__(k,v))
    monkeypatch.setattr(av,'_av_get',lambda f,s,k:calls.append(k) or {'Symbol':s})
    _,first=av._endpoint('OVERVIEW','LI','one')
    _,cached=av._endpoint('OVERVIEW','LI','one')
    _,changed=av._endpoint('OVERVIEW','LI','two')
    assert calls==['one','two'] and cached['cache_hit'] and not changed['cache_hit']
    assert first['fetched_at']==cached['fetched_at']


def test_full_compact_diagnostics_never_switch_key_or_expose_provider_error(monkeypatch):
    import backend.core.demo_mode as demo
    monkeypatch.setattr(demo,'PUBLIC_DEMO_MODE',False)
    seen=[]
    class Response:
        def __init__(self,payload):self.payload=payload
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return json.dumps(self.payload).encode()
    def get(url,**kwargs):
        from urllib.parse import urlparse,parse_qs
        q=parse_qs(urlparse(url).query);seen.append(q)
        return Response({'Information':'Premium endpoint'} if len(seen)==1 else {'Time Series (Daily)':{'2026-10-23':{'1. open':'1','2. high':'2','3. low':'1','4. close':'2','5. volume':'10'}}})
    monkeypatch.setattr(av.urllib.request,'urlopen',get)
    monkeypatch.setattr('time.sleep',lambda *_:None)
    with patch.dict('os.environ',{'ALPHA_VANTAGE_API_KEY':'PRIVATE_SENTINEL','AV_API_KEY_1':'wrong-account'},clear=True):
        provider=AVKlineProvider();result=provider.get_kline('LI:US','US')
    assert [q['apikey'] for q in seen]==[['PRIVATE_SENTINEL']]*2
    assert [x['request_mode'] for x in result.diagnostics['attempts']]==['full','compact']
    assert result.diagnostics['attempts'][0]['status']=='premium'
    assert result.diagnostics['attempts'][1]['row_count']==1
    assert 'PRIVATE_SENTINEL' not in json.dumps(result.diagnostics)


def test_independent_CNY_financials_survive_valuation_conflict_and_negative_FCF():
    raw=raw_financial();raw['valuation_currency']='CNY'
    a=assess({'fundamentals':raw},symbol='LI:US',at=AT)
    assert a['records'][0]['quality']=='valid'  # no unverified valuation fields present
    assert len(a['verified_field_records'])==5
    assert a['records'][0]['values']['net_income_ttm']>0>a['records'][0]['values']['free_cash_flow_ttm']
    assert 'EARNINGS_CASH_FLOW_DIVERGENCE_REQUIRES_REVIEW' in a['conflicts']
    ref=next(r['record_id'] for r in a['verified_field_records'] if r['field']=='free_cash_flow_ttm')
    candidate=dict(topic='risk',basis='inference',text='利润与自由现金流的背离需要结合资本投入及回款节奏解释，不能仅凭利润改善认定盈利质量已修复。',evidence_refs=[ref])
    assert explanation_projection([candidate],a)[0]
    assert all(not a['qualification'][level]['allowed'] for level in ('L2','L3','L4'))


def official_releases(raw):
    output=[]
    basis={'totalRevenue':'total_revenue','operatingCashflow':'operating_cash_flow','capitalExpenditures':'positive_capital_cash_outflow'}
    for i,row in enumerate(raw['field_provenance']['revenue_ttm']['observations']):
        end=row['fiscalDateEnding'];published=(datetime.fromisoformat(end)+timedelta(days=45)).date().isoformat()
        output.append(dict(symbol='LI:US',source='official_disclosure',source_url='https://ir.lixiang.com/results',record_id=f'official:{i}',
            published_at=published,data_as_of=published,fetched_at=AT.isoformat(),period=end,content='原公开财报中可定位的经营事实。',
            official_disclosure={'issuer_id':'LI_AUTO','source_url':'https://ir.lixiang.com/results'},
            financial_observations=[dict(source_field=k,value=row[k],currency='CNY',unit='currency_units',unit_scale=1,period_basis='quarterly',
                fiscal_period_end=end,accounting_basis=basis[k],source_locator='cash-flow table / named line') for k in ('totalRevenue',)]))
    return output


def verified_source_lookup(releases):
    # Simulate the independent server source owner, not anything on a card.
    # The frozen original publication and line observations are pinned separately.
    import hashlib
    sources=copy.deepcopy(releases)
    for source in sources:
        original=json.dumps({'publication':source['published_at'], 'lines':source['financial_observations']},sort_keys=True).encode()
        source['source_verification']=dict(document_sha256=hashlib.sha256(original).hexdigest(),
            publication_locator='frozen original publication header')
    return {source['record_id']:source for source in sources}.get


def test_disclosure_join_requires_all_matching_components_and_leaves_unknown_fields_unknown():
    raw=raw_financial(False);releases=official_releases(raw)
    before=assess({'fundamentals':raw},symbol='LI:US',at=AT)
    assert not before['verified_field_records']
    a=assess({'fundamentals':raw},research=releases,symbol='LI:US',at=AT,disclosure_verifier=verified_source_lookup(releases))
    revenue=next(r for r in a['verified_field_records'] if r['field']=='revenue_ttm')
    assert revenue['values']['revenue_ttm']==raw['revenue_ttm']
    assert not any(r['field']=='net_income_ttm' for r in a['verified_field_records'])
    links=a['records'][0]['field_quality']['revenue_ttm']['provenance']['publication_links']
    assert len(links)==4 and len({r['fiscal_period_end'] for r in links})==4
    assert all(len(r['source_document_sha256'])==64 and r['publication_locator'] for r in links)
    partial=assess({'fundamentals':raw},research=releases[:1],symbol='LI:US',at=AT,disclosure_verifier=verified_source_lookup(releases))
    assert [r['field'] for r in partial['verified_field_records']]==['revenue_quarter']
    assert raw['field_provenance']['revenue_ttm']['published_at'] is None  # no source mutation


@pytest.mark.parametrize('change',['issuer','currency','unit','basis','date','value','url','locator'])
def test_disclosure_join_rejects_false_matches(change):
    raw=raw_financial(False);releases=official_releases(raw);verifier=verified_source_lookup(releases);r=releases[0];o=r['financial_observations'][0]
    if change=='issuer':r['official_disclosure']['issuer_id']='MEITUAN'
    elif change=='currency':o['currency']='USD'
    elif change=='unit':o['unit']='shares'
    elif change=='basis':o['accounting_basis']='net_vehicle_revenue'
    elif change=='date':o['fiscal_period_end']='2026-03-31'
    elif change=='value':o['value']='999999'
    elif change=='url':r['source_url']='https://not-lixiang.com/results'
    else:o['source_locator']=''
    a=assess({'fundamentals':raw},research=releases,symbol='LI:US',at=AT,disclosure_verifier=verifier)
    assert not any(x['field']=='revenue_ttm' for x in a['verified_field_records'])


def daily_history(n=100):
    import exchange_calendars as calendars
    sessions=calendars.get_calendar('XNYS').sessions_in_range('2025-01-01','2026-10-23')[-n:]
    frame=pd.DataFrame([dict(date=str(d.date()),open=100+i/10,high=102+i/10,low=99+i/10,close=101+i/10,volume=10000+i) for i,d in enumerate(sessions)])
    return from_bars('LI:US',KlineResult(frame,'av'),at=AT)


def test_daily_only_L1_prompt_and_final_cannot_claim_weekly_confluence():
    technical=daily_history();a=assess({'technical':technical},symbol='LI:US',at=AT)
    assert a['records'][0]['quality']=='partial' and a['technical_levels'].keys()=={'day'}
    assert 'INSUFFICIENT_BARS' in a['records'][0]['period_quality']['week']['issues']
    ref=a['verified_period_records'][0]['record_id']
    assert ref in explanation_prompt(a)
    p=dict(topic='technical_trend',basis='inference',text='日线动量修复仍需量能持续配合，短期变化尚不足以确认经营改善，应复核趋势延续性。',evidence_refs=[ref])
    assert explanation_projection([p],a)[0]
    p['text']='日线和周线形成多周期共振，趋势修复已有较强确定性，应结合量能继续观察。'
    assert explanation_projection([p],a)[1]['rejected']=={'unverified_timeframe':1}
    assert 'TECHNICAL_VERIFIED_EVIDENCE_REQUIRED' in a['qualification']['L3']['blockers']
    assert all(not a['qualification'][level]['allowed'] for level in ('L2','L3','L4'))


@pytest.mark.parametrize('change',['gap','indicator','old','seed'])
def test_bad_periods_never_acquire_L1_references(change):
    raw=asdict(daily_history(300))
    if change=='gap':raw['series']['day']['bars'].pop(40)
    elif change=='indicator':
        for row in raw['series'].values():row['indicators']['ma20']+=10
    elif change=='old':raw['provenance']['data_as_of']='2024-01-01'
    else:raw['source']='seed';raw['provenance']['source']='seed'
    a=assess({'technical':raw},symbol='LI:US',at=AT)
    assert not _evidence_index(a)


def test_explicit_news_fetch_time_is_distinct_from_ingestion_and_not_rejuvenated():
    from research_v2.repository import evidence_records
    facts=NS(source_type='alpha_vantage_news',raw_facts={'time_published':'20261023T120000','fetched_at':'2026-10-23T12:01:00+00:00','summary':'已披露的产品变化及其不确定性。'},source_refs=[NS(ref_type='url',ref_value='https://example.org/news')],primary_symbol='LI:US',affected_symbols=['LI:US'],ingested_at=AT)
    card=NS(card_id='stored:news',status='active',facts=facts,narrative=NS(event_type='news'))
    row=evidence_records([card])[0]
    assert row['published_at']=='2026-10-23T12:00:00+00:00'
    assert row['fetched_at']!=row['ingested_at']
    assert record('research',row,at=AT,symbol='LI:US')['quality']=='valid'
    del facts.raw_facts['fetched_at']
    assert evidence_records([card])[0]['fetched_at'] is None
    del facts.raw_facts['time_published']
    assert evidence_records([card])[0]['published_at'] is None


def test_qualified_card_is_not_hidden_by_twenty_unusable_old_cards(monkeypatch):
    from research_v2 import repository
    old=[dict(symbol='LI:US',source='legacy',record_id=str(i),content='没有来源时钟的旧卡。') for i in range(20)]
    good=dict(symbol='LI:US',source='news',record_id='dated',content='有明确出处和日期的公开新闻。',source_url='https://example.org/news',published_at='2026-10-23',data_as_of='2026-10-23',fetched_at=AT.isoformat(),period='event:2026-10-23')
    monkeypatch.setattr(repository,'query_cards',lambda *a,**kw:old+[good])
    monkeypatch.setattr(repository,'evidence_records',lambda cards:cards)
    rows=repository.query_evidence_records(None,'LI:US',at=AT)
    assert len(rows)==20 and rows[0]['record_id']=='dated'


def test_missing_three_observation_rows_cannot_use_one_disclosure_for_TTM():
    raw=raw_financial(False);releases=official_releases(raw)
    verifier=verified_source_lookup(releases)
    raw['field_provenance']['revenue_ttm']['observations']=raw['field_provenance']['revenue_ttm']['observations'][:1]
    a=assess({'fundamentals':raw},research=releases,symbol='LI:US',at=AT,disclosure_verifier=verifier)
    assert not any(x['field']=='revenue_ttm' for x in a['verified_field_records'])


def test_daily_only_enters_original_PEER_and_same_final_filter(monkeypatch):
    import asyncio
    from backend.agents.expressing_agent import ExpressingAgent
    from backend.agents.contracts import ExpressionOutput
    from decision_engine import llm_engine
    from tests.test_v42_exit_plan import setup_case
    _,executed,plan,_=setup_case('LI:US')
    technical=daily_history();executed.market_data=MarketDataBundle(symbol='LI:US',technical=technical)
    executed.position_evidence=assess(executed.market_data,symbol='LI:US',at=AT)
    a=executed.position_evidence;ref=a['verified_period_records'][0]['record_id'];captured=[]
    import unicodedata
    text=unicodedata.normalize('NFKC','日线趋势的修复仍需要量能持续配合，短期动量改善不能直接证明经营基本面已经反转。')
    def reason(question,*args,**kwargs):
        captured.append(question)
        return llm_engine.LLMResult('HOLD',[],[],[],structured_result={'professional_explanation':[
            dict(topic='technical_trend',basis='inference',text=text,evidence_refs=[ref])]})
    monkeypatch.setattr(llm_engine,'reason',reason)
    out=ExpressionOutput()
    async def run():return [s async for s in ExpressingAgent()._express_position_decision(out,plan,executed,'解释趋势',[])]
    chunks=asyncio.run(run())
    assert len(captured)==1 and ref in captured[0]
    assert text in ''.join(chunks) and not out.actionable
    assert out.structured_payload['professional_explanation'][0]['evidence_refs']==[ref]


def test_partial_technical_citations_rechecked_at_Web_and_Explain_boundary():
    from backend.services.snapshot_chat import _owner_evidence_current
    a=assess({'technical':daily_history()},symbol='LI:US',at=AT)
    assert _owner_evidence_current([a],1,at=AT)
    a['records'][0]['values']['series']['day']['indicators']['ma20']+=100
    assert not _owner_evidence_current([a],1,at=AT)


def test_research_adapter_uses_unified_alias_and_safe_diagnostics(monkeypatch):
    from research_v2.adapters import alpha_vantage as news
    news._AV_RESEARCH_CACHE.clear();seen=[]
    def get(url,params,timeout):
        seen.append(dict(params))
        return NS(raise_for_status=lambda:None,json=lambda:{'feed':[{'time_published':'20261023T120000'}]})
    monkeypatch.setattr('requests.get',get)
    with patch.dict('os.environ',{'AV_API_KEY':'PRIVATE_SENTINEL'},clear=True):
        first=news._call_av_api('NEWS_SENTIMENT',tickers='LI')
        second=news._call_av_api('NEWS_SENTIMENT',tickers='LI')
    assert len(seen)==1 and seen[0]['apikey']=='PRIVATE_SENTINEL'
    assert first['_wp_diagnostic']['configuration_slot']=='AV_API_KEY'
    assert first['_wp_diagnostic']['status']=='available'
    assert second['_wp_diagnostic']['cache_hit']
    assert first['_wp_diagnostic']['fetched_at']==second['_wp_diagnostic']['fetched_at']
    assert 'PRIVATE_SENTINEL' not in json.dumps(first['_wp_diagnostic'])


@pytest.mark.parametrize('change',['fetch_missing','fetch_future','identity_missing','market_mismatch'])
def test_period_recalculation_cannot_upgrade_invalid_source_envelope(change):
    raw=asdict(daily_history())
    if change=='fetch_missing':raw['provenance'].pop('fetched_at')
    elif change=='fetch_future':raw['provenance']['fetched_at']='2027-01-01T00:00:00+00:00'
    elif change=='identity_missing':raw['provenance'].pop('record_id')
    else:raw['series']['day']['market']='HK'
    a=assess({'technical':raw},symbol='LI:US',at=AT)
    assert not _evidence_index(a)


def test_weekly_only_cannot_claim_unverified_daily_or_multi_period_evidence():
    raw=asdict(daily_history(300));raw['series']['day']['indicators']['ma20']+=1
    a=assess({'technical':raw},symbol='LI:US',at=AT)
    ref=a['verified_period_records'][0]['record_id']
    assert a['verified_period_records'][0]['period']=='week'
    for text in ('日线趋势改善具有持续性，但仍需观察量能和经营变化之间的差异。','多周期共振已经逐渐形成，仍需结合交易量变化复核趋势是否延续。'):
        candidate=dict(topic='technical_trend',basis='inference',text=text,evidence_refs=[ref])
        assert not explanation_projection([candidate],a)[0]


@pytest.mark.parametrize("claimed", [{}, {"quality":"valid"}, {"source_verified":True, "verified_by":"user"}, {"source_verification":{"document_sha256":"a"*64,"publication_locator":"header"}}])
def test_unverified_official_card_cannot_self_certify_disclosure_clock(claimed):
    from research_v2.repository import evidence_records
    raw=raw_financial(False);releases=official_releases(raw);rows=[]
    for release in releases:
        payload=dict(release,**claimed)
        facts=NS(primary_symbol='LI:US',affected_symbols=['LI:US'],source_type='manual_import',
            source_refs=[NS(ref_type='url',ref_value=release['source_url'])],raw_facts=payload,ingested_at=AT)
        card=NS(card_id=release['record_id'],status='active',facts=facts,narrative=NS(event_type='fundamental_snapshot'))
        rows.extend(evidence_records([card]))
    a=assess({'fundamentals':raw},research=rows,symbol='LI:US',at=AT)
    assert not a['verified_field_records']
    assert not a['records'][0]['field_quality']['revenue_ttm']['provenance'].get('publication_links')
    assert a['records'][0]['field_quality']['revenue_ttm']['provenance']['published_at'] is None


@pytest.mark.parametrize("change", ["missing", "date", "locator", "hash", "observation"])
def test_independent_source_verification_must_cover_exact_publication_and_values(change):
    raw=raw_financial(False);releases=official_releases(raw)
    original_lookup=verified_source_lookup(releases)
    source=copy.deepcopy(original_lookup(releases[0]['record_id']))
    if change=='missing': source=None
    elif change=='date': source['published_at']='2026-01-01'
    elif change=='locator': source['source_verification']['publication_locator']=''
    elif change=='hash': source['source_verification']['document_sha256']='claimed'
    else: source['financial_observations'][0]['value']=1
    def verifier(record_id):
        return source if record_id==releases[0]['record_id'] else original_lookup(record_id)
    a=assess({'fundamentals':raw},research=releases,symbol='LI:US',at=AT,disclosure_verifier=verifier)
    assert not a['verified_field_records']


def test_unverified_card_rejection_preserves_independent_financials_periods_and_gates():
    raw=raw_financial();releases=official_releases(raw)
    market={'fundamentals':raw,'technical':daily_history(300)}
    baseline=assess(market,symbol='LI:US',at=AT)
    actual=assess(market,research=releases,symbol='LI:US',at=AT)
    assert actual['verified_field_records']==baseline['verified_field_records']
    assert actual['verified_period_records']==baseline['verified_period_records']
    for level in ('L2','L3','L4'):
        assert not actual['qualification'][level]['allowed']


def test_repository_candidates_join_only_when_independent_source_owner_agrees():
    from research_v2.repository import evidence_records
    raw=raw_financial(False);releases=official_releases(raw);rows=[]
    for release in releases:
        facts=NS(primary_symbol='LI:US',affected_symbols=['LI:US'],source_type='manual_import',
            source_refs=[NS(ref_type='url',ref_value=release['source_url'])],raw_facts=release,ingested_at=AT)
        rows.extend(evidence_records([NS(card_id=release['record_id'],status='active',facts=facts,
            narrative=NS(event_type='fundamental_snapshot'))]))
    assert not assess({'fundamentals':raw},research=rows,symbol='LI:US',at=AT)['verified_field_records']
    verified=assess({'fundamentals':raw},research=rows,symbol='LI:US',at=AT,
        disclosure_verifier=verified_source_lookup(releases))
    assert [r['field'] for r in verified['verified_field_records']]==['revenue_ttm','revenue_quarter']
    assert all(not verified['qualification'][level]['allowed'] for level in ('L2','L3','L4'))
