"""Frozen public provider observations from the first real acceptance, never live IO."""
import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.test_analysis_skills import analysis_db, peer  # noqa: F401

FIXTURES = Path(__file__).parent / 'fixtures' / 'v42_real_research'


def frozen(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def av_offline(monkeypatch):
    from backend.services.market_data import av_fundamentals_service as av
    import backend.core.demo_mode as demo
    import time
    calls = []
    def get(url, **kwargs):
        from urllib.parse import parse_qs, urlsplit
        from io import BytesIO
        query = parse_qs(urlsplit(url).query)
        function, ticker = query['function'][0], query['symbol'][0]
        calls.append((function, ticker))
        if function == 'OVERVIEW':
            payload = {'Information': frozen('overview-failure-classification.json')['notice']}
        else:
            payload = frozen(f'direct-av-{ticker}-{function}.json')['payload']
        return BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(demo, 'PUBLIC_DEMO_MODE', False)
    monkeypatch.setattr(av.urllib.request, 'urlopen', get)
    monkeypatch.setattr(av, '_wp_symbol_to_av_ticker', lambda symbol: symbol.split(':')[0])
    monkeypatch.setattr(av, 'get_cached', lambda key: None)
    monkeypatch.setattr(av, 'set_cached', lambda *args: None)
    monkeypatch.setattr(av, 'get_next_av_key', lambda: 'isolated')
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    return av, calls


@pytest.mark.parametrize('ticker', ['LI', 'PDD'])
def test_premium_overview_does_not_short_circuit_real_statements(av_offline, ticker):
    av, calls = av_offline
    result = av.fetch_fundamentals(ticker + ':US')
    assert [f for f, _ in calls] == ['OVERVIEW', 'INCOME_STATEMENT', 'CASH_FLOW']
    assert result.source == 'alphavantage' and result.pe_ttm is None
    assert result.revenue_ttm is not None and result.operating_cash_flow_ttm is not None
    assert result.published_at is None and result.valuation_observed_at is None


def test_hgt_position_uses_existing_canonical_market_entry(analysis_db, peer, monkeypatch):
    from backend.services import demo_market_service
    import backend.core.demo_mode as demo
    monkeypatch.setattr(demo, 'PUBLIC_DEMO_MODE', True)
    monkeypatch.setattr(demo, 'DEMO_ALLOW_MARKET_DATA', False)
    from pocs.chatgpt_skills.analysis import analyze
    engine = create_engine('sqlite:///' + str(analysis_db))
    with engine.begin() as connection:
        connection.execute(text("UPDATE positions SET ticker='3690.HGT', symbol='3690:HK', currency='CNY' WHERE ticker='AAPL'"))
    engine.dispose()
    seen = []
    monkeypatch.setattr(demo_market_service, 'fetch_demo_quote', lambda symbol: seen.append(('quote', symbol)))
    monkeypatch.setattr(demo_market_service, 'fetch_demo_kline', lambda symbol: seen.append(('kline', symbol)))
    asyncio.run(analyze(analysis_db, 7, 'analyze_position', {'question':'fixture'}, synthetic=True))
    assert ('kline', '3690:HK') in seen
    assert all(symbol == '3690:HK' for _, symbol in seen)


@pytest.mark.parametrize('notice,status', [
    ({'Information':'This is a premium endpoint.'}, 'premium'),
    ({'Note':'Our standard API call frequency is limited'}, 'rate_limited'),
    ({'Information':'Please contact support'}, 'provider_notice'),
    ({'_av_error':'network_error'}, 'network_error'),
    ({'Error Message':'Invalid API call'}, 'provider_error'), ({}, 'missing'),
])
def test_av_error_categories_are_not_conflated(notice, status):
    from backend.services.market_data.av_fundamentals_service import _response_status
    assert _response_status('OVERVIEW', notice) == status


def test_transport_error_never_logs_secret_url(monkeypatch, caplog):
    from backend.services.market_data import av_fundamentals_service as av
    def broken(*args, **kwargs):
        raise OSError('https://example/?apikey=PRIVATE_SENTINEL')
    monkeypatch.setattr(av.urllib.request, 'urlopen', broken)
    assert av._av_get('OVERVIEW', 'LI', 'PRIVATE_SENTINEL') == {'_av_error':'network_error'}
    assert 'PRIVATE_SENTINEL' not in caplog.text


def test_successful_statement_cache_preserves_clocks_while_overview_recovers(av_offline, monkeypatch):
    av, calls = av_offline
    cache = {'av_fundamentals:LI': av._load_seed_fundamentals('LI')}
    monkeypatch.setattr(av, 'get_cached', cache.get)
    monkeypatch.setattr(av, 'set_cached', lambda key, value, ttl: cache.__setitem__(key, value))
    first = av.fetch_fundamentals('LI:US')
    assert first.provenance['endpoints']['OVERVIEW']['status'] == 'premium'
    monkeypatch.setattr(av, '_av_get', lambda *args: {'Symbol':'LI', 'PERatio':'5', 'Currency':'USD'})
    second = av.fetch_fundamentals('LI:US')
    assert second.pe_ttm == 5 and second.revenue_ttm == first.revenue_ttm
    assert second.provenance['endpoints']['INCOME_STATEMENT']['cache_hit']
    assert second.field_provenance['revenue_ttm']['fetched_at'] == first.field_provenance['revenue_ttm']['fetched_at']
    assert second.field_provenance['pe_ttm']['valuation_observed_at'] is None


def test_cashflow_reading_independent_of_income_and_zero_capex_not_trusted():
    from backend.services.market_data.av_fundamentals_service import statement_metrics
    from backend.services.market_data.evidence import record
    cash = frozen('direct-av-PDD-CASH_FLOW.json')
    values, provenance, missing = statement_metrics('PDD', None, cash['payload'], fetched_at=cash['finished_at'])
    assert values['operating_cash_flow_ttm'] == 111895091000
    assert values['capital_expenditures_ttm'] == 0
    assert 'free_cash_flow_ttm' not in values and 'ZERO_CAPEX_UNVERIFIED' in missing
    # Even a future adapter filling real publication dates cannot certify zero CapEx.
    for meta in provenance.values():
        meta['published_at'] = '2026-08-24'
    from datetime import datetime
    row = record('fundamentals', dict(symbol='PDD:US', field_provenance=provenance, **values),
                 symbol='PDD:US', at=datetime.fromisoformat(cash['finished_at']))
    assert row['field_quality']['capital_expenditures_ttm']['quality'] != 'valid'
    assert 'ZERO_CAPEX_UNVERIFIED' in row['field_quality']['capital_expenditures_ttm']['issues']


@pytest.mark.parametrize('unverified_card', [False, True])
def test_li_official_revenue_conflict_retains_both_observations(av_offline, unverified_card):
    from dataclasses import asdict
    from datetime import datetime, timezone
    from backend.services.market_data.evidence import assess
    av, _ = av_offline
    raw = asdict(av.fetch_fundamentals('LI:US'))
    # Explicit public disclosure observation, not a narrative LLM KPI.
    official = dict(symbol='LI:US', source='official_disclosure', record_id='LI:2026Q2:official',
        source_url='https://ir.lixiang.com/zh-hant/news-releases/news-release-details/li-auto-inc-announces-unaudited-second-quarter-2026-financial',
        data_as_of='2026-08-26', published_at='2026-08-26', fetched_at='2026-10-23T04:00:00+00:00',
        period='2026Q2', text='Total revenues from the official consolidated income statement.',
        financial_observations=[dict(source_field='totalRevenue', value=25666890000,
            currency='CNY', fiscal_period_end='2026-06-30', period_basis='quarterly', rounding_unit=1000)])
    if unverified_card:
        official['quality'] = 'unverified_until_source_checks'
    result = assess({'fundamentals':raw}, research=[official], symbol='LI:US',
                    at=datetime(2026,10,23,5,tzinfo=timezone.utc))
    if unverified_card:
        assert not result['source_conflicts']
        assert 'FINANCIAL_OBSERVATIONS_UNVERIFIED' in result['issues']
        assert 'FINANCIAL_SOURCE_CONFLICT' not in result['conflicts']
        return
    assert 'FINANCIAL_SOURCE_CONFLICT' in result['conflicts']
    conflict = next(c for c in result['source_conflicts'] if c['field'] == 'revenue_ttm')
    assert conflict['provider_value'] == 25594433485 and conflict['official_value'] == 25666890000
    assert not result['qualification']['L3']['allowed']
    assert result['verified_field_records'] == []  # unknown AV publication is not backfilled


@pytest.mark.parametrize('symbol,valid', [('LI:US',True),('PDD:US',True),('3690:HK',False)])
def test_frozen_real_technical_completeness_is_not_relaxed(symbol, valid):
    from datetime import datetime
    from backend.services.market_data.evidence import assess
    source = frozen('sources-' + symbol.replace(':','-') + '.json')
    a = assess({'technical':source['technical']}, symbol=symbol, at=datetime.fromisoformat(source['finished_at']))
    technical = next(row for row in a['records'] if row['kind'] == 'technical')
    assert (technical['quality'] == 'valid') is valid
    assert a['qualification']['L1']['allowed']
    assert all(not a['qualification'][level]['allowed'] for level in ('L2','L3','L4'))
    if not valid:
        dates = {row['date'] for row in source['technical']['series']['day']['bars']}
        assert '2026-04-10' not in dates and '2026-04-13' not in dates


@pytest.mark.parametrize('basis', ['technical', 'operating'])
@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('symbol', ['LI:US', 'PDD:US'])
def test_original_model_call_retains_real_technical_inference_without_fundamentals(monkeypatch, symbol, legacy, caplog, basis):
    from datetime import datetime
    from types import SimpleNamespace as NS
    from backend.agents.contracts import ExecutionOutput, ExpressionOutput, PlanningOutput
    from backend.agents.expressing_agent import ExpressingAgent
    from backend.services.market_data.schema import TechnicalData, MarketDataBundle
    from backend.services.market_data.evidence import assess
    from backend.services.market_data.output_policy import enforce
    from decision_engine import llm_engine
    source = frozen('sources-' + symbol.replace(':','-') + '.json')
    market = MarketDataBundle(symbol=symbol, technical=TechnicalData(**source['technical']))
    a = assess(market, symbol=symbol, at=datetime.fromisoformat(source['finished_at']))
    if basis == 'operating':
        from tests.test_v42_evidence import verified
        evidence = verified('research', text='产品结构改善，但竞争促销可能影响利润与现金转化。')
        evidence['symbol'] = symbol
        a = assess(market, research=[evidence], symbol=symbol, at=datetime.fromisoformat(source['finished_at']))
    ref = next(r['record_id'] for r in a['records'] if r['kind'] == ('technical' if basis == 'technical' else 'research'))
    import unicodedata
    prose = unicodedata.normalize('NFKC', '短期修复与较长周期的趋势仍可能分化，需观察量能持续性；这只能反映交易行为，不能据此确认经营改善或投资逻辑恢复。')
    if basis == 'operating':
        prose = '若产品结构的改善可以持续转化为经营现金流，盈利质量可能增强；仍需关注竞争促销的侵蚀，不能仅凭短期表现确认投资逻辑。'
        prose = unicodedata.normalize('NFKC', prose)
    candidate = dict(topic='technical_trend' if basis == 'technical' else 'investment_thesis',
                     basis='inference', text=prose, evidence_refs=[ref])
    result = dict(decisionType='hold', confidence=.3, rationale=['old reasoning'], riskPoints=['old risk'],
                  evidenceSources=['research'], infoNeeded=['fundamentals unavailable'],
                  recommendedAction={'detail':'unsafe 987654'}, professional_explanation=[candidate])
    if legacy:
        result.pop('rationale')  # known old-parser extension-loss path
    captured = []
    def create(**kwargs):
        captured.append(kwargs)
        return NS(choices=[NS(message=NS(content=json.dumps(result, ensure_ascii=False)))])
    monkeypatch.setattr(llm_engine, '_get_client', lambda: NS(chat=NS(completions=NS(create=create))))
    def no_context(*args, **kwargs):
        raise ValueError('isolated context')
    monkeypatch.setattr(llm_engine, 'build_decision_context', no_context)
    position = NS(symbol=symbol, ticker=symbol.split(':')[0], currency='USD', name='Fixture', weight=.1,
                  market_value_cny=100, profit_loss_rate=0, platforms=[], asset_class='股票')
    loaded = NS(target_position=position, positions=[position], profile=NS(risk_level='moderate', goal='fixture'),
        total_assets=1000, rules=NS(max_single_position=.4, min_cash_pct=.2), raw_portfolio=None,
        research=['EXCLUDED_SEED_SENTINEL'], retrieved_research_views=[], retrieved_principles=[], is_new_entry=False)
    execution = ExecutionOutput(loaded_data=loaded, market_data=market)
    execution.position_evidence = a
    execution.rule_result = NS(violation=False, warning=None)
    execution.signal_result = NS(to_dict=lambda: {}, position_signal='合理', fundamental_signal='中性')
    out = ExpressionOutput()
    async def collect():
        return [chunk async for chunk in ExpressingAgent()._express_position_decision(out,
            PlanningOutput(intent={'asset':'Fixture'}), execution, '解释趋势与局限', [])]
    chunks = asyncio.run(collect())
    assert len(captured) == 1  # original PEER model, never an extra model call
    messages = str(captured[0]['messages'])
    assert ref in messages and 'technical_trend' in messages and 'indicators' in messages
    assert 'EXCLUDED_SEED_SENTINEL' not in messages
    assert prose in ''.join(chunks) and '987654' not in str(out)
    assert out.structured_payload['explanationDiagnostics']['legacy_extension_recovered'] is legacy
    assert not a['qualification']['L3']['allowed'] and not out.actionable
    enforce(out, a)  # MCP/Web/Explain subsequent projection stays identical
    assert out.structured_payload['professional_explanation'][0]['text'] == prose
    final = ExpressionOutput(llm_result=out.llm_result, structured_payload=out.structured_payload)
    enforce(final, a)
    assert final.explanation_diagnostics['legacy_extension_recovered'] is legacy
    assert final.explanation_diagnostics['generation']['accepted_count'] == 1
    assert '987654' not in caplog.text and prose not in caplog.text


def test_explanation_diagnostic_reasons_are_bounded_and_content_free():
    from backend.services.market_data.output_policy import explanation_projection
    from tests.test_v42_professional_explanation import assessment, paragraph
    a = assessment()
    candidates = [paragraph(), dict(topic='private text'), dict(topic='business',basis='fact'),
        dict(topic='risk',basis='inference',text='PRIVATE_ASSET 100000',evidence_refs=['fixture:research']),
        dict(topic='risk',basis='inference',text=paragraph()['text'],evidence_refs=['SECRET_ACCOUNT'])]
    accepted, d = explanation_projection(candidates, a)
    assert len(accepted) == 1
    assert d['rejected'] == dict(topic_invalid=1,basis_invalid=1,text_not_safe_qualitative=1,reference_not_usable=1)
    assert not any(word in json.dumps(d) for word in ('private text','PRIVATE_ASSET','SECRET_ACCOUNT'))


def test_reviewer_uses_new_contract_not_empty_legacy_risk(monkeypatch):
    from backend.agents.contracts import ExecutionOutput, ExpressionOutput, PlanningOutput
    from backend.agents.reviewing_agent import ReviewingAgent
    from backend.services.market_data.output_policy import enforce
    from decision_engine.llm_engine import LLMResult
    from tests.test_v42_professional_explanation import assessment, paragraph
    a = assessment(); execution = ExecutionOutput(); execution.position_evidence = a
    out = ExpressionOutput(llm_result=LLMResult('HOLD', [], [], []),
        structured_payload={'professional_explanation':[paragraph()]})
    enforce(out, a)
    def no_scoring(*args):
        pytest.fail('4.2 quality review must not substitute a model score')
    reviewer = ReviewingAgent()
    monkeypatch.setattr(reviewer, '_run_llm_scoring', no_scoring)
    review = reviewer.run(PlanningOutput(intent={'primary_intent':'PositionDecision'}), execution, out)
    assert review.hard_validation_passed and out.llm_result.risk == []
    out.structured_payload['professional_explanation'] = []
    review = reviewer.run(PlanningOutput(), execution, out)
    assert not review.hard_validation_passed and review.action == 'retry'
    assert review.failed_rules == ['professional_explanation_missing_with_usable_evidence']


@pytest.mark.parametrize('failure', ['numeric', 'no_evidence', 'invented_ref', 'wrong_topic'])
def test_legacy_candidate_recovery_cannot_reopen_final_bypass(failure):
    from backend.agents.contracts import ExpressionOutput
    from backend.services.market_data.evidence import assess
    from backend.services.market_data.output_policy import enforce
    from decision_engine.llm_engine import LLMResult
    from tests.test_v42_professional_explanation import assessment, paragraph
    a = assessment(); p = paragraph()
    if failure == 'numeric': p['text'] += '建议卖出一百股。'
    elif failure == 'no_evidence': a = assess(symbol='LI:US')
    elif failure == 'invented_ref': p['evidence_refs'] = ['model-forged']
    else: p['topic'] = 'technical_trend'  # research cannot certify OHLCV
    raw = json.dumps({'professional_explanation':[p], 'strategy':['unsafe 987654']}, ensure_ascii=False)
    out = ExpressionOutput(llm_result=LLMResult('HOLD',[],[],[],raw_output=raw), raw_text=raw)
    enforce(out, a)
    assert out.structured_payload['professional_explanation'] == []
    assert out.explanation_diagnostics['legacy_extension_recovered']
    assert '987654' not in str(out) and '一百股' not in str(out)
    enforce(out, a)
    assert out.structured_payload['professional_explanation'] == []
    assert not out.structured_payload['analysisQualification']['L3']['allowed']


@pytest.mark.parametrize('code,status', [(429,'rate_limited'),(403,'access_denied'),(500,'provider_error')])
def test_av_http_status_is_not_mislabeled_as_transport(monkeypatch, code, status):
    import urllib.error
    from backend.services.market_data import av_fundamentals_service as av
    def error(*args, **kwargs):
        raise urllib.error.HTTPError('https://private.invalid/?apikey=PRIVATE_SENTINEL',code,'private',{},None)
    monkeypatch.setattr(av.urllib.request, 'urlopen', error)
    assert av._response_status('CASH_FLOW', av._av_get('CASH_FLOW','PDD','isolated')) == status


@pytest.mark.parametrize('expire_final', [False, True])
def test_mcp_portfolio_final_boundary_retains_verified_fields_and_rechecks(monkeypatch, analysis_db, peer, expire_final):
    from copy import deepcopy
    from backend.services import position_analysis
    from backend.services.market_data.evidence import assess
    from decision_engine import llm_engine
    from tests.test_v42_fundamental_clocks import statements, project, AT
    from tests.test_v42_professional_explanation import TEXT
    from pocs.chatgpt_skills.analysis import analyze
    income, cash = statements(published=True)
    for row in cash['quarterlyReports']:
        row['capitalExpenditures'] = '5'
    raw, _ = project(income, cash)
    raw.update(symbol='AAPL:US', pe_ttm=999)
    a = assess({'fundamentals':raw}, symbol='AAPL:US', at=AT)
    assert a['records'][0]['quality'] == 'partial'
    ref = a['verified_field_records'][0]['record_id']
    p = dict(topic='investment_thesis', basis='inference', text=TEXT, evidence_refs=[ref])
    calls = []
    def prepare(*args, **kwargs):
        calls.append(True)
        result = deepcopy(a)
        if expire_final and len(calls) >= 3:
            result['verified_field_records'] = []
        return [result]
    monkeypatch.setattr(position_analysis, 'prepare_portfolio', prepare)
    monkeypatch.setattr(llm_engine, 'review_portfolio', lambda *args, **kwargs:
        llm_engine.GenericLLMResult('PortfolioReview', 'unsafe 987654', raw_payload={'professional_explanation':[p]}))
    result = asyncio.run(analyze(analysis_db, 7, 'review_portfolio', {'question':'fixture'}, synthetic=True))
    final = result['professional_result']['analysis_result']
    assert len(calls) >= 3
    assert (TEXT in result['conclusion']) is not expire_final
    assert final['explanationDiagnostics']['generation']['accepted_count'] == 1
    assert final['explanationDiagnostics']['final']['accepted_count'] == (0 if expire_final else 1)
    assert '987654' not in json.dumps(result) and not final['execution_authorized']


@pytest.mark.parametrize('different_currency', [False, True])
def test_each_valid_cash_window_survives_income_window_mismatch(different_currency):
    from backend.services.market_data.av_fundamentals_service import statement_metrics
    income = frozen('direct-av-LI-INCOME_STATEMENT.json')['payload']
    cash = frozen('direct-av-LI-CASH_FLOW.json')['payload']
    cash['quarterlyReports'] = cash['quarterlyReports'][1:]
    if different_currency:
        for row in cash['quarterlyReports']: row['reportedCurrency'] = 'USD'
    values, provenance, missing = statement_metrics('LI', income, cash, fetched_at='2026-10-23T03:01:00+00:00')
    assert 'INCOME_CASH_FLOW_PERIOD_OR_CURRENCY_MISMATCH' in missing
    assert values['operating_cash_flow_ttm'] == sum(float(row['operatingCashflow']) for row in cash['quarterlyReports'][:4])
    assert values['free_cash_flow_ttm'] == values['operating_cash_flow_ttm']-values['capital_expenditures_ttm']
    assert provenance['revenue_ttm']['fiscal_period_end'] == '2026-06-30'
    assert provenance['operating_cash_flow_ttm']['fiscal_period_end'] == '2026-03-31'
    assert provenance['operating_cash_flow_ttm']['currency'] == ('USD' if different_currency else 'CNY')


def test_cached_endpoints_never_sleep_or_refetch(av_offline, monkeypatch):
    import time
    av, calls = av_offline
    cache = {}; sleeps = []
    monkeypatch.setattr(av, 'get_cached', cache.get)
    monkeypatch.setattr(av, 'set_cached', lambda key, value, ttl: cache.__setitem__(key,value))
    monkeypatch.setattr(time, 'sleep', sleeps.append)
    monkeypatch.setattr(av, '_av_get', lambda function, *args: {'Symbol':'LI'} if function=='OVERVIEW'
        else frozen(f'direct-av-LI-{function}.json')['payload'])
    av.fetch_fundamentals('LI:US')
    assert sleeps == [1.5,1.5]
    sleeps.clear()
    monkeypatch.setattr(av, '_av_get', lambda *args: pytest.fail('cache must not refetch'))
    av.fetch_fundamentals('LI:US')
    assert not sleeps


def test_explanation_failure_obeys_original_retry_cap(monkeypatch):
    from backend.agents.contracts import ExecutionOutput, ExpressionOutput, PlanningOutput
    from backend.agents.reviewing_agent import ReviewingAgent
    from backend.services.market_data.output_policy import enforce
    from decision_engine.llm_engine import LLMResult
    from tests.test_v42_professional_explanation import assessment
    a = assessment(); execution = ExecutionOutput(); execution.position_evidence = a
    out = ExpressionOutput(llm_result=LLMResult('HOLD', [], [], []))
    enforce(out,a)
    reviewer = ReviewingAgent()
    monkeypatch.setattr(reviewer, '_run_llm_scoring', lambda *args: pytest.fail('no extra model'))
    review = reviewer.run(PlanningOutput(), execution, out, retry_count=3)
    assert review.action == 'fallback' and review.jump_step is None
    assert 'professional_explanation_missing_with_usable_evidence' in review.failed_rules
