"""Final MCP boundary regressions on isolated SQLite and frozen model outputs."""
import asyncio
from copy import deepcopy
import json

import pytest

from tests.test_analysis_skills import analysis_db, peer  # noqa: F401
from tests.test_v42_scenarios import view, first_steps
from pocs.chatgpt_skills.analysis import analyze


@pytest.mark.parametrize('route', ['position_multi', 'portfolio'])
def test_mcp_route_mismatch_discards_unqualified_text_and_payload(analysis_db, monkeypatch, route):
    from backend.services import decision_service_v3 as svc
    async def wrong_route(*args, **kwargs):
        yield svc._sse('intent', dict(primary_intent='PositionDecision', planner_route=route))
        yield svc._sse('text', dict(delta='SELL 999999 shares at 123456'))
        yield svc._sse('done', dict(conclusion_level='multi_asset', nested=dict(quantity=999999)))
    monkeypatch.setattr(svc, 'run_chat_stream_v3', wrong_route)
    result = asyncio.run(analyze(analysis_db, 7, 'analyze_position', {'question':'fixture'}, synthetic=True))
    assert result['status'] == 'unavailable'
    assert 'ANALYSIS_ROUTE_MISMATCH' in result['issues']
    assert result['professional_result'] == {}
    assert '999999' not in json.dumps(result)


def fixed_view():
    result = view()
    result['data_quality'].update(issues=[], qualification_scope={'symbol':None,'analysis_scope':'investments'})
    result['views'].update(wealth_summary={}, portfolio_summary={})
    return result


def test_restricted_portfolio_replaces_model_numbers_in_final_mcp(analysis_db, peer, monkeypatch):
    from decision_engine import llm_engine
    from decision_engine.llm_engine import GenericLLMResult
    from app import asset_views
    monkeypatch.setattr(asset_views, 'public_view', lambda value, **kwargs: deepcopy(value))
    monkeypatch.setattr(llm_engine, 'review_portfolio', lambda *args, **kwargs:
        GenericLLMResult(intent_type='PortfolioReview', chat_answer='SELL 999999 at 123456', raw_output='999999',
                         raw_payload={'chat_answer':'SELL 999999', 'key_findings':['SELL 999999'],
                                      'nested': {'quantity':999999}}))
    result = asyncio.run(analyze(analysis_db, 7, 'review_portfolio',
        {'question':'fixture', 'snapshot_id':'asset-view:v1:58', 'scenario_legs':first_steps()},
        synthetic=True, asset_view=fixed_view()))
    assert '999999' not in json.dumps(result)
    assert 'PRECISE_PORTFOLIO_OUTPUT_BLOCKED' in result['issues']
    assert result['professional_result']['analysis_result']['can_propose_rebalance'] is False
    assert result['evidence']['allocation_scenarios']['mode'] == 'illustrative_unqualified'


def test_portfolio_scenarios_use_final_not_initial_amount_qualification(analysis_db, peer, monkeypatch):
    from app import asset_views
    original = fixed_view()
    original['data_quality']['analysis_eligibility']['B']['allowed'] = True
    original['data_quality']['can_propose_rebalance'] = True
    def source_clock_elapsed(value, **kwargs):
        value = deepcopy(value)
        value['data_quality']['analysis_eligibility']['B']['allowed'] = False
        value['data_quality']['can_propose_rebalance'] = False
        value['data_quality']['issues'] = ['SOURCE_EXPIRED_DURING_MODEL']
        return value
    monkeypatch.setattr(asset_views, 'public_view', source_clock_elapsed)
    result = asyncio.run(analyze(analysis_db, 7, 'review_portfolio',
        {'question':'fixture', 'snapshot_id':'asset-view:v1:58', 'scenario_legs':first_steps()},
        synthetic=True, asset_view=original))
    assert result['evidence']['allocation_scenarios']['mode'] == 'illustrative_unqualified'
    assert result['data_quality']['asset_view']['analysis_eligibility']['B']['allowed'] is False
    assert original['data_quality']['analysis_eligibility']['B']['allowed'] is True


def test_incomplete_mcp_stream_discards_early_model_text(analysis_db, monkeypatch):
    from backend.services import decision_service_v3 as svc
    async def failed(*args, **kwargs):
        yield svc._sse('intent', dict(primary_intent='PortfolioReview', planner_route='portfolio'))
        yield svc._sse('text', dict(delta='SELL 999999 shares at 123456'))
        yield svc._sse('error', dict(code='fixture_failure'))
    monkeypatch.setattr(svc, 'run_chat_stream_v3', failed)
    result = asyncio.run(analyze(analysis_db, 7, 'review_portfolio', {'question':'fixture'}, synthetic=True))
    assert result['status'] == 'unavailable'
    assert result['professional_result'] == {}
    assert '999999' not in json.dumps(result)


def test_portfolio_missing_snapshot_cannot_return_free_model_trade_instructions(analysis_db, peer, monkeypatch):
    from decision_engine import llm_engine
    from decision_engine.llm_engine import GenericLLMResult
    monkeypatch.setattr(llm_engine, 'review_portfolio', lambda *args, **kwargs:
        GenericLLMResult(intent_type='PortfolioReview', chat_answer='SELL 999999 at 123456',
                         raw_payload={'nested': {'quantity':999999}}))
    result = asyncio.run(analyze(analysis_db, 7, 'review_portfolio', {'question':'fixture'}, synthetic=True))
    assert '999999' not in json.dumps(result)
    assert result['professional_result']['analysis_result']['can_propose_rebalance'] is False
    assert result['evidence']['allocation']['snapshot']


def test_d1_precision_alone_does_not_certify_portfolio_l3(analysis_db, peer, monkeypatch):
    from decision_engine import llm_engine
    from decision_engine.llm_engine import GenericLLMResult
    from app import asset_views
    monkeypatch.setattr(asset_views, 'public_view', lambda value, **kwargs: deepcopy(value))
    monkeypatch.setattr(llm_engine, 'review_portfolio', lambda *args, **kwargs:
        GenericLLMResult(intent_type='PortfolioReview', chat_answer='SELL 999999 at 123456',
                         raw_payload={'nested': {'quantity':999999}}))
    v = fixed_view()
    v['data_quality'].update(can_propose_rebalance=True, analysis_eligibility={'B':{'allowed':True}, 'C':{'allowed':True}})
    result = asyncio.run(analyze(analysis_db, 7, 'review_portfolio',
        {'question':'fixture','snapshot_id':'asset-view:v1:58'}, synthetic=True, asset_view=v))
    assert '999999' not in json.dumps(result)
    assert result['professional_result']['analysis_result']['can_propose_rebalance'] is False


@pytest.mark.parametrize('expired_context', [False, True])
def test_position_nested_trace_requalifies_at_final_mcp_boundary(analysis_db, monkeypatch, expired_context):
    from types import SimpleNamespace
    from backend.services import decision_service_v3 as svc
    from backend.services.position_analysis import prepare
    from backend.services.market_data.output_policy import enforce
    from backend.agents.contracts import ExpressionOutput
    from pocs.chatgpt_skills import analysis as adapter
    from app import asset_views
    from tests.test_v42_exit_plan import setup_case
    from tests.test_v42_evidence import AT
    from tests.test_v42_scenarios import first_steps
    v, executed, planner, target = setup_case('LI:US')
    v['data_quality'].update(can_propose_rebalance=True, analysis_eligibility={'B':{'allowed':True}},
                             issues=[], qualification_scope={'symbol':'LI:US','analysis_scope':'investments'})
    v['views'].update(wealth_summary={}, portfolio_summary={})
    options = dict(scenario_legs=first_steps())
    executed.position_evidence = prepare(planner, executed, asset_view=v, options=options, at=AT)
    assert executed.position_evidence['qualification']['L2']['allowed']
    assert executed.position_evidence['scenarios']['mode'] == 'qualified_static_scenario'
    expressed = ExpressionOutput(structured_payload={'decisionType':'trim', 'confidence':.9})
    enforce(expressed, executed.position_evidence)
    initial_plan = deepcopy(executed.position_evidence)
    def capture_evidence(result, captured, portfolio_id):
        result.evidence['position_evidence'] = adapter.encode(captured['executed'].position_evidence)
        result.professional_result = {'analysis_result':adapter.encode(captured['expressed'].structured_payload)}
    monkeypatch.setattr(adapter, 'captured_evidence', capture_evidence)
    async def completed(*args, **kwargs):
        kwargs['result_callback'](planner, executed, expressed, SimpleNamespace(passed=True))
        yield svc._sse('intent', dict(primary_intent='PositionDecision', planner_route='position_single'))
        yield svc._sse('text', {'delta':expressed.chat_answer})
        yield svc._sse('done', {'conclusion_level':'analysis'})
    monkeypatch.setattr(svc, 'run_chat_stream_v3', completed)
    def elapsed(value, **kwargs):
        result = deepcopy(value)
        result['context_current'] = not expired_context
        result['data_quality']['analysis_eligibility']['B']['allowed'] = False
        result['data_quality']['can_propose_rebalance'] = False
        result['data_quality']['issues'] = ['SOURCE_EXPIRED_DURING_ADAPTER']
        return result
    monkeypatch.setattr(asset_views, 'public_view', elapsed)
    result = asyncio.run(analyze(analysis_db, 7, 'analyze_position',
        dict(question='fixture', symbol='LI:US', snapshot_id=v['snapshot_id'], scenario_legs=first_steps()),
        synthetic=True, asset_view=v))
    final = result['evidence']['position_evidence']
    assert final['snapshot_id'] == v['snapshot_id']
    assert not final['qualification']['L2']['allowed']
    assert not final['qualification']['L3']['allowed']
    assert final['scenarios']['mode'] == 'illustrative_unqualified'
    assert initial_plan['qualification']['L2']['allowed']
    assert initial_plan['scenarios']['mode'] == 'qualified_static_scenario'
    if expired_context:
        assert result['status'] == 'unavailable'
        assert result['professional_result'] == {}
        assert 'ANALYSIS_CONTEXT_EXPIRED' in result['issues']
    else:
        payload = result['professional_result']['analysis_result']
        assert not payload['analysisQualification']['L2']['allowed']
        assert all(row['amount_cny'] is None and row['quantity'] is None
                   for row in payload['exitPlan']['tranches'])


@pytest.mark.parametrize('tool', ['analyze_position', 'review_portfolio'])
def test_original_peer_cited_explanation_survives_final_mcp_without_snapshot(analysis_db, peer, monkeypatch, tool):
    """Real PEER + adapter, source fixture only; no second model or asset write."""
    from datetime import datetime, timedelta, timezone
    import hashlib
    from backend.agents import executing_agent
    from decision_engine import llm_engine
    from decision_engine.llm_engine import GenericLLMResult, LLMResult
    from tests.test_v42_professional_explanation import TEXT
    now = datetime.now(timezone.utc)
    source = dict(symbol='AAPL:US', source='vendor', record_id='fixture:current-research',
        published_at=(now-timedelta(hours=1)).isoformat(), data_as_of=(now-timedelta(hours=1)).isoformat(),
        fetched_at=now.isoformat(), period='current', text='产品组合与现金转化需要对照竞争压力评估。')
    original = executing_agent.invoke_skill
    def invoke(name, **kwargs):
        result = original(name, **kwargs)
        if name == 'wp-load-context':
            result.loaded_data.research_evidence = [deepcopy(source)]
        return result
    monkeypatch.setattr(executing_agent, 'invoke_skill', invoke)
    explanation = dict(topic='investment_thesis', basis='inference', text=TEXT,
                       evidence_refs=['fixture:current-research'])
    model_calls = []
    def reason(*args, **kwargs):
        model_calls.append('position')
        return LLMResult('HOLD', ['unsafe 987654'], [], [], chat_answer='unsafe 987654',
            structured_result={'decisionType':'hold', 'professional_explanation':[explanation]})
    def portfolio(*args, **kwargs):
        model_calls.append('portfolio')
        return GenericLLMResult(intent_type='PortfolioReview', chat_answer='unsafe 987654',
            raw_payload={'chat_answer':'unsafe 987654','professional_explanation':[explanation]})
    monkeypatch.setattr(llm_engine, 'reason', reason)
    monkeypatch.setattr(llm_engine, 'review_portfolio', portfolio)
    before = hashlib.sha256(analysis_db.read_bytes()).hexdigest()
    result = asyncio.run(analyze(analysis_db, 7, tool, {'question':'fixture'}, synthetic=True))
    assert TEXT in result['conclusion']
    assert '987654' not in json.dumps(result)
    projected = result['professional_result']['analysis_result']
    assert projected['professional_explanation'][0]['basis'] == 'inference'
    assert projected['professional_explanation'][0]['sources'][0]['symbol'] == 'AAPL:US'
    assert len(model_calls) == 1
    assert hashlib.sha256(analysis_db.read_bytes()).hexdigest() == before


def test_portfolio_automatic_repayment_rechecks_full_loan_amount_scope(monkeypatch):
    from backend.agents.contracts import ExpressionOutput
    from backend.services.market_data.output_policy import enforce_portfolio
    from backend.services import allocation_scenarios
    from tests.test_v42_exit_plan import setup_case
    from tests.test_v42_account_candidates import auto_case
    v, executed, _, t = auto_case('3690:HK')
    v['data_quality']['analysis_eligibility']['B']['allowed'] = True
    calls = []
    original = allocation_scenarios.qualification_view
    def loan_scope(view, legs, **kwargs):
        calls.append(deepcopy(legs))
        result = original(view, legs, **kwargs)
        if any(leg['destination'] == 'repay_cny_loan' for leg in legs):
            result = deepcopy(result)
            result['data_quality']['analysis_eligibility']['B']['allowed'] = False
        return result
    monkeypatch.setattr(allocation_scenarios, 'qualification_view', loan_scope)
    out = ExpressionOutput(structured_payload={})
    enforce_portfolio(out, asset_view=v, options={'target_versions':[t]}, loaded=executed.loaded_data)
    result = out.structured_payload
    assert any(legs for legs in calls)
    assert result['exit_candidates']
    assert not result['amount_qualified']
    assert all(row['amount_cny'] is None for candidate in result['exit_candidates'] for row in candidate['tranches'])
    assert all(not c['scenario'] or c['scenario']['mode'] == 'illustrative_unqualified' for c in result['exit_candidates'])
