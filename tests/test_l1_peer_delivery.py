"""Original PEER/ACTIVE/MCP delivery on isolated synthetic evidence, no real model."""
import asyncio
import hashlib
import json
from tests.test_policy_integration import active  # noqa: F401
from tests.test_analysis_skills import analysis_db, peer  # noqa: E402,F401
from backend.agents.reviewing_agent import ReviewingAgent as _L1Reviewer
_REAL_L1_REVIEW = _L1Reviewer.run


def test_l1_research_sdk_to_real_peer_active_and_mcp_final_contract(active,analysis_db,peer,monkeypatch):
    """Full original orchestration; synthetic sources/policy and SDK MockTransport.

    This is an engineering delivery test, never a real Sol quality assessment.
    """
    import httpx,openai
    from datetime import datetime,timezone
    from backend.agents import executing_agent,expressing_agent
    from intent_engine import _llm_client
    from app.llm_config import chat_completion,SOL
    from pocs.chatgpt_skills.analysis import analyze,AnalysisResult
    from pocs.chatgpt_skills.position_jobs import report_projection
    now=datetime.now(timezone.utc).isoformat();original=executing_agent.invoke_skill
    source=dict(symbol='AAPL:US',source='vendor',record_id='fixture:l1-qualified-business',
        published_at=now,data_as_of=now,fetched_at=now,period='current',
        text='经营与现金转化需要复核竞争投入，尚不足以确认投资假设。')
    def invoke(name,**kw):
        value=original(name,**kw)
        if name=='wp-load-context':value.loaded_data.research_evidence=[source]
        return value
    monkeypatch.setattr(executing_agent,'invoke_skill',invoke)
    monkeypatch.setattr(_L1Reviewer,'run',_REAL_L1_REVIEW)
    requests=[]
    def receive(request):
        body=json.loads(request.content);requests.append(body)
        paragraph=dict(topic='business',basis='inference',scope='research_only',
            text='经营改善仍需现金转化支持，竞争投入可能影响利润持续性；应以正式经营数据复核长期假设。',
            evidence_refs=[source['record_id']])
        return httpx.Response(200,json=dict(id='offline',object='chat.completion',created=0,model=SOL,
            choices=[dict(index=0,finish_reason='stop',message=dict(role='assistant',content=json.dumps({'professional_explanation':[paragraph]})))],
            usage=dict(prompt_tokens=1000,completion_tokens=100,total_tokens=1100)))
    client=openai.OpenAI(api_key='synthetic',http_client=httpx.Client(transport=httpx.MockTransport(receive)))
    monkeypatch.setattr(_llm_client,'get_client',lambda:client)
    monkeypatch.setattr(expressing_agent,'chat_completion',chat_completion)
    before=hashlib.sha256(analysis_db.read_bytes()).hexdigest()
    try:
        result=asyncio.run(analyze(analysis_db,7,'analyze_position',dict(question='研究经营与长期持有的反证'),synthetic=True,async_l1=True))
    finally:client.close()
    assert len(requests)==1 and requests[0]['max_completion_tokens']==32768
    assert requests[0]['reasoning_effort']=='medium'
    assert result['professional_analysis_complete'],result.get('issues')
    assert result['stage_diagnostics']['expressing']=='completed'
    assert result['stage_diagnostics']['reviewing']=='completed'
    assert result['stage_diagnostics']['final_acceptance']=='accepted_final'
    out=result['professional_result']['analysis_result']
    assert out['professional_explanation'] and out['policy_reference']['bundle_id']==active[1]['bundle_id']
    assert result['evidence']['l1_context_diagnostics']['status']=='accepted'
    assert not result['execution_authorized'] and not result['order_created']
    report=report_projection(AnalysisResult.model_validate(result)).professional_result['analysis_result']
    assert report['professional_explanation']==out['professional_explanation']
    assert report['policy_reference']==out['policy_reference']
    assert report['conditionalValuation']==out['conditionalValuation']
    assert hashlib.sha256(analysis_db.read_bytes()).hexdigest()==before
