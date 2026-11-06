"""Post-model delivery guards; synthetic ACTIVE/Evidence, SDK MockTransport only."""
import asyncio
import hashlib
import json
import socket
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from tests.test_policy_integration import active  # noqa: F401
from tests.test_analysis_skills import analysis_db, peer  # noqa: F401
from tests.test_l1_peer_delivery import _REAL_L1_REVIEW


@pytest.mark.parametrize('fault', ['none', 'legacy', 'tampered_reference', 'invalid_numeric', 'expired', 'policy_conflict', 'post_error', 'post_timeout'])
def test_qualified_delivery_and_post_review_faults(active, analysis_db, peer, monkeypatch, fault):
    import httpx, openai
    from app.llm_config import SOL, chat_completion
    from app.llm_observation import safe_metadata
    from backend.agents import executing_agent, expressing_agent
    from backend.agents.reviewing_agent import ReviewingAgent
    from backend.services import position_analysis
    from backend.services.market_data import output_policy
    from backend.services.investment_policy.runtime import PolicySession
    from backend.services.investment_policy.parser import PolicyError
    from intent_engine import _llm_client
    from pocs.chatgpt_skills.analysis import analyze, AnalysisResult
    from pocs.chatgpt_skills.position_jobs import report_projection

    if fault == 'legacy':
        monkeypatch.delenv('WEALTHPILOT_POLICY_DB_PATH', raising=False)
        monkeypatch.delenv('WEALTHPILOT_POLICY_SCOPE', raising=False)
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **kw: pytest.fail('NETWORK_FORBIDDEN'))
    from backend.core import demo_mode
    monkeypatch.setattr(demo_mode, 'PUBLIC_DEMO_MODE', True)
    monkeypatch.setattr(demo_mode, 'DEMO_ALLOW_MARKET_DATA', False)
    source = dict(symbol='AAPL:US', source='vendor', record_id='fixture:post-model-qualified',
        published_at=datetime.now(timezone.utc).isoformat(), data_as_of=datetime.now(timezone.utc).isoformat(),
        fetched_at=datetime.now(timezone.utc).isoformat(), period='current', text='现金转化与竞争投入需要持续复核。')
    invoke = executing_agent.invoke_skill
    def load(name, **kw):
        value = invoke(name, **kw)
        if name == 'wp-load-context': value.loaded_data.research_evidence = [source.copy()]
        return value
    monkeypatch.setattr(executing_agent, 'invoke_skill', load)
    reviewed = []
    def review(self, *args, **kwargs):
        out = _REAL_L1_REVIEW(self, *args, **kwargs)
        assert out.action == 'pass'
        reviewed.append(True)
        return out
    monkeypatch.setattr(ReviewingAgent, 'run', review)
    # The original final prepare/enforce still runs. No bypass of evidence rules.
    prepare = position_analysis.prepare
    def requalify(*args, **kwargs):
        if reviewed and fault == 'post_error': raise ValueError('injected_postprocessing_failure')
        if reviewed and fault == 'post_timeout': time.sleep(3)
        if reviewed and fault == 'expired':
            args[1].loaded_data.research_evidence = [dict(source,
                published_at='2000-01-01T00:00:00+00:00', data_as_of='2000-01-01T00:00:00+00:00')]
        return prepare(*args, **kwargs)
    monkeypatch.setattr(position_analysis, 'prepare', requalify)
    enforce = output_policy.enforce
    def audited_enforce(output, assessment, **kwargs):
        if reviewed and fault == 'tampered_reference':
            for paragraph in output.structured_payload['professional_explanation']:
                paragraph['evidence_refs'] = ['unqualified:tampered']
        if reviewed and fault == 'invalid_numeric':
            for paragraph in output.structured_payload['professional_explanation']:
                paragraph['text'] = '未经绑定的目标价123美元，立即买入100股。'
        result = enforce(output, assessment, **kwargs)
        # Large deterministic display text must not cause invisible paced events.
        output.chat_answer += '\n' + '合格事实附录。' * 40000
        return result
    monkeypatch.setattr(output_policy, 'enforce', audited_enforce)
    async def forbid_paced_display(*a, **kw):
        pytest.fail('ASYNC_L1_MUST_NOT_GENERATE_DISCARDED_DISPLAY_CHUNKS')
        yield ''
    monkeypatch.setattr(expressing_agent, '_emit_text_chunks', forbid_paced_display)
    checks = []
    ensure = PolicySession.ensure_current
    def check(self):
        checks.append(True)
        if reviewed and fault == 'policy_conflict': raise PolicyError('POLICY_VERSION_CONFLICT')
        return ensure(self)
    monkeypatch.setattr(PolicySession, 'ensure_current', check)
    sent = []
    def receive(request):
        sent.append(json.loads(request.content))
        paragraphs = [dict(topic=t, basis='inference', scope='research_only',
            text='经营改善需要现金转化支持，竞争投入可能削弱盈利持续性；应以正式数据复核长期假设。',
            evidence_refs=[source['record_id']]) for t in
            ('business','competition','investment_thesis','risk','exit_rationale','review_condition')]
        return httpx.Response(200, json=dict(id='offline', object='chat.completion', created=0, model=SOL,
            choices=[dict(index=0, finish_reason='stop', message=dict(role='assistant', content=json.dumps(
                {'professional_explanation':paragraphs})))], usage=dict(prompt_tokens=1000, completion_tokens=200, total_tokens=1200)))
    client = openai.OpenAI(api_key='synthetic', http_client=httpx.Client(transport=httpx.MockTransport(receive)))
    monkeypatch.setattr(_llm_client, 'get_client', lambda: client)
    monkeypatch.setattr(expressing_agent, 'chat_completion', chat_completion)
    if fault == 'post_timeout': monkeypatch.setenv('WEALTHPILOT_L1_ANALYSIS_TIMEOUT_SECONDS', '2')
    before = hashlib.sha256(analysis_db.read_bytes()).hexdigest()
    started = time.monotonic()
    try:
        result = asyncio.run(analyze(analysis_db, 7, 'analyze_position', dict(question='研究经营及长期风险，不执行交易'),
                                    synthetic=True, async_l1=True))
    finally: client.close()
    AnalysisResult.model_validate(result)
    paragraphs = result['professional_result'].get('analysis_result', {}).get('professional_explanation', [])
    assert len(sent) == 1 and reviewed
    assert sent[0]['max_completion_tokens'] == 32768 and sent[0]['reasoning_effort'] == 'medium'
    assert len(checks) < 100  # Stage-bound, independent of the 280k character appendix.
    assert not result['execution_authorized'] and not result['order_created']
    assert hashlib.sha256(analysis_db.read_bytes()).hexdigest() == before
    assert time.monotonic() - started < 30  # Would exceed 140s with the old display pacing.
    if fault in {'none', 'legacy'}:
        assert len(paragraphs) == 6 and result['professional_analysis_complete']
        assert result['stage_diagnostics']['delivery'] == 'accepted_final'
        projected = report_projection(AnalysisResult.model_validate(result))
        assert projected.professional_result['analysis_result']['professional_explanation'] == paragraphs
        safe = safe_metadata(result['llm_observation'])
        timings = {r['peer_stage']:r for r in safe['stage_timings']}
        if fault == 'legacy':
            assert result['conclusion'] and 'policy_reference' not in result['scope']
            return
        assert all(timings[k]['status'] == 'completed' for k in
            ('post_review_requalification', 'peer_active_finalization', 'result_callback',
             'result_receipt', 'adapter_requalification', 'adapter_active_finalization'))
        assert timings['adapter_active_finalization']['accepted_count'] == 6
        assert timings['adapter_active_finalization']['initial_candidate_count'] == 6
    else:
        assert not paragraphs and not result['professional_analysis_complete']
    if fault == 'post_error':
        assert result['stage_diagnostics']['expressing'] == 'completed'
        assert result['stage_diagnostics']['postprocessing'] == 'failed'
        assert result['stage_diagnostics']['delivery'] == 'incomplete'
    if fault == 'post_timeout':
        assert 'ANALYSIS_TIMEOUT' in result['issues']
        assert result['evidence'].get('qualified_facts')
        assert result['professional_result']['analysis_result']['mode'] in {'facts_only','policy_qualified_research'}
        assert result['stage_diagnostics']['expressing'] == 'completed'
        assert result['stage_diagnostics']['model_generation'] == 'completed'
        assert result['stage_diagnostics']['postprocessing'] == result['stage_diagnostics']['delivery'] == 'timeout'


def test_post_model_metadata_is_closed_and_separates_parse_failure():
    from app.llm_observation import analysis_run, post_model_stage, safe_metadata, reconcile_research_stages
    output = SimpleNamespace(explanation_diagnostics={'generation':{'candidate_count':6,'accepted_count':6},
        'final':{'candidate_count':6,'accepted_count':0,'rejected':{'reference_not_usable':6,'PRIVATE_RESPONSE':99}},
        'prompt':'PRIVATE_PROMPT'})
    with analysis_run() as (trace, owner):
        with pytest.raises(TimeoutError):
            with post_model_stage('post_review_requalification', output): raise TimeoutError('PRIVATE_EXCEPTION')
        value = safe_metadata(trace.snapshot('timeout'))
    encoded = json.dumps(value)
    assert 'PRIVATE_' not in encoded
    assert value['stage_timings'][0]['status'] == 'timeout'
    assert 'accepted_count' not in value['stage_timings'][0]  # Final gate never completed.
    assert value['stage_timings'][0]['initial_accepted_count'] == 6
    result = dict(scenario='analyze_position', professional_analysis_complete=False,
        stage_diagnostics={'expressing':'failed','reviewing':'not_started'}, issues=['PEER_EXPRESSION_FAILED'],
        llm_observation={'calls':[{'peer_stage':'expressing','status':'completed','error_category':None}]})
    reconcile_research_stages(result)
    assert result['stage_diagnostics']['model_generation'] == 'completed'
    assert result['stage_diagnostics']['expressing'] == 'failed'  # Model return is not successful JSON/projection.
    assert result['stage_diagnostics']['delivery'] == 'incomplete'
