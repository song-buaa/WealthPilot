"""Bounded L1 projection and real SDK transport, no paid/production requests."""
import asyncio
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import os
import subprocess
import sys
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from app.llm_config import SOL, chat_completion, l1_research_options, request_options
from app.llm_observation import analysis_run, reconcile_research_stages
from backend.agents.contracts import ExecutionOutput, PlanningOutput, AgentTaskStatus
from backend.agents.expressing_agent import ExpressingAgent
from backend.services.market_data import l1_context as ctx
from backend.services.market_data.output_policy import explanation_projection, _evidence_index


def facts():
    from tests.test_l1_reliability import facts as original
    return original()


def qualitative():
    from tests.test_v42_professional_explanation import assessment
    return assessment()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))


def test_pack_covers_topics_refs_and_exact_values_without_changing_authority():
    a=facts();before=deepcopy(a);pack,diagnostic=ctx.project(a)
    assert a==before
    fields={r['field']:r for r in pack['financial_operating_cashflow']}
    assert {'revenue_quarter','net_income_quarter','operating_cash_flow_quarter'}<=set(fields)
    assert fields['revenue_quarter']['values']['revenue_quarter']==112358000000
    assert pack['record_quality']=='valid'
    assert pack['competition_counterevidence'] and pack['sources']
    refs={r['evidence_ref'] for key in ('financial_operating_cashflow','valuation_inputs','competition_counterevidence','technical_events') for r in pack[key]}
    assert refs<=set(_evidence_index(a))
    for row in fields.values():
        assert row['source_ref'] in pack['sources']
        if row.get('dependency_set'):assert row['dependency_set'] in pack['dependencies']
    assert diagnostic['dependency_refs_present']
    index=_evidence_index(a)
    for row in fields.values():
        for key in ('accounting_basis','fiscal_period_start','ordinary_shares_per_ads','issuer_type'):
            if index[row['evidence_ref']].get(key) is not None:
                assert row[key]==index[row['evidence_ref']][key]


def test_seed_mock_conflict_and_other_symbol_cannot_enter_pack():
    a=facts()
    for key in ('verified_field_records','historical_field_records','quality_field_records'):
        for row in a.get(key,[]):row['quality']='conflict'
    for row in a['records']:row['is_seed']=True
    p,_=ctx.project(a)
    assert not p['financial_operating_cashflow'] and not p['competition_counterevidence']


def test_context_budget_refuses_required_evidence_instead_of_cutting_topic(monkeypatch):
    a=qualitative();a['records'][0]['values']['text']='required counterevidence '*50000
    with pytest.raises(ctx.ContextBudgetExceeded) as caught:
        ctx.messages(a,'original intent',policy={},holding={})
    assert caught.value.diagnostic['status']=='budget_exceeded'
    assert a['records'][0]['values']['text'].endswith('counterevidence ')


def test_missing_tokenizer_uses_safe_byte_upper_bound(monkeypatch):
    monkeypatch.setattr(ctx,'_encoder',lambda:None)
    assert ctx.measure('中文')['tokens']==6
    assert ctx.measure('中文')['counting']=='utf8_byte_upper_bound'


@pytest.mark.parametrize('new_entry,pure,objective',[(False,False,'evaluate'),(True,True,'evaluate'),(False,False,'exit')])
def test_same_l1_sdk_path_preserves_intent_and_final_filters(monkeypatch,new_entry,pure,objective):
    from intent_engine import _llm_client
    from backend.services.investment_policy import runtime
    a=qualitative()
    from backend.services.position_analysis import research_intent
    a['research_intent']=research_intent({})
    a['research_intent'].update(objective=objective,no_holding_research=pure)
    sent=[]
    def receive(req):
        value=json.loads(req.content);sent.append(value)
        assert value['max_completion_tokens']==32768 and value['reasoning_effort']=='medium'
        assert 'max_tokens' not in value
        candidate={'topic':'business','basis':'inference','scope':'research_only',
            'text':'经营改善仍需现金转化支持，应核对竞争投入能否持续；缺少可核验结果时不能推断竞争优势。','evidence_refs':['fixture:research']}
        return httpx.Response(200,json={'id':'test','object':'chat.completion','created':0,'model':SOL,
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':json.dumps({'professional_explanation':[candidate]})}}],
            'usage':{'prompt_tokens':500,'completion_tokens':100,'total_tokens':600}})
    client=openai.OpenAI(api_key='synthetic',http_client=httpx.Client(transport=httpx.MockTransport(receive)))
    monkeypatch.setattr(_llm_client,'get_client',lambda:client)
    monkeypatch.setattr(runtime,'context',lambda *a,**kw:dict(mode='POLICY_ACTIVE',policy_reference={'bundle_sha256':'a'*64},rules={'no_orders':True}))
    loaded=NS(is_new_entry=new_entry,target_position=NS(ticker='LI',name='LI',quantity=0 if new_entry else 100,currency='USD'))
    executed=ExecutionOutput(analysis_mode='l1_research',research_question='研究经营与退出的条件，不预设交易',position_evidence=a,loaded_data=loaded)
    plan=PlanningOutput(route='position_single',intent={'primary_intent':'PositionDecision'})
    async def run():
        agent=ExpressingAgent()
        with analysis_run() as (trace,_):
            async for _ in agent.run_streaming(plan,executed,'EXPANDED_PRIVATE_DUPLICATE'*1000,[{'role':'assistant','content':'OLD_RAG'}]):pass
            return agent.last_output,trace.snapshot('completed')
    out,observation=asyncio.run(run());client.close()
    assert out.status==AgentTaskStatus.COMPLETED and len(sent)==1
    assert out.structured_payload['professional_explanation']
    assert 'EXPANDED_PRIVATE_DUPLICATE' not in json.dumps(sent) and 'OLD_RAG' not in json.dumps(sent)
    assert out.structured_payload['decisionType']==('watch' if pure else 'exit' if objective=='exit' else 'hold')
    assert not out.actionable and not out.structured_payload['exitPlan']['execution_authorized']
    assert observation['calls'][0]['output_token_budget']==32768
    assert observation['calls'][0]['reasoning_effort']=='medium'
    assert executed.research_context_diagnostics['status']=='accepted'


def test_truncated_observation_is_not_not_started_or_accepted():
    result={'scenario':'analyze_position','stage_diagnostics':{'expressing':'not_started','reviewing':'completed'},
        'llm_observation':{'calls':[{'peer_stage':'expressing','status':'failed','error_category':'truncated'}]},
        'professional_analysis_complete':False,'professional_result':{}}
    reconcile_research_stages(result)
    assert result['stage_diagnostics']['expressing']=='truncated'
    assert result['stage_diagnostics']['final_acceptance']=='incomplete'


def test_exact_budget_only_changes_l1_call():
    assert request_options(**l1_research_options())['max_completion_tokens']==32768
    assert request_options(SOL,max_tokens=4096)['max_completion_tokens']==8192
    assert request_options(SOL)['reasoning_effort']=='medium'


def test_real_public_sec_bytes_enter_pack_and_keep_loss_cashflow_counterevidence():
    from tests.test_sec_real_original import parse,AT as SEC_AT
    from backend.services.market_data.evidence import assess
    row=parse();a=assess({},symbol='LI:US',at=SEC_AT,research=[row],disclosure_verifier={row['record_id']:row}.get)
    p,_=ctx.project(a)
    fields={r['field']:r for r in p['financial_operating_cashflow']}
    assert fields['net_income_quarter']['values']['net_income_quarter']==-1705257000
    assert fields['operating_cash_flow_quarter']['values']['operating_cash_flow_quarter']==15025000
    assert a['qualification']['L3']['allowed'] is False


def test_existing_private_pdd_reference_pack_no_model_call():
    path=os.getenv('WEALTHPILOT_TEST_FROZEN_PDD')
    if not path:pytest.skip('existing private development evidence opt-in')
    full=json.load(open(path));a=full['evidence']['position_evidence'];before=deepcopy(a)
    _,d=ctx.messages(a,'研究长期盈利与退出条件',policy={},holding={'state':'stored_holding_requires_freshness_check'})
    assert a==before and d['estimated_with_frame_reserve']<=40000
    assert d['dependency_refs_present'] and d['selected_records']<d['eligible_records']
    assert d['provider_token_count']=='UNKNOWN_UNTIL_RESPONSE'


def test_source_url_only_evidence_keeps_original_identity():
    a=qualitative();row=a['records'][0];row.pop('record_id',None);row['source_url']='https://example.org/official-record'
    p,_=ctx.project(a)
    assert p['competition_counterevidence'][0]['evidence_ref']==row['source_url']
    assert ctx.compact_facts_text(a)


def test_user_history_is_preserved_as_assumption_not_assistant_facts():
    payload,d=ctx.messages(qualitative(),'当前问题',policy={},holding={},history=[
        {'role':'user','content':'长期目标是逐步退出'}, {'role':'assistant','content':'UNVERIFIED_ASSISTANT_RAG'},
        {'role':'user','content':'长期目标是逐步退出'}])
    value=json.loads(payload[1]['content'])
    assert value['request_policy_holding']['unverified_user_research_context']==['长期目标是逐步退出']
    assert 'UNVERIFIED_ASSISTANT_RAG' not in json.dumps(payload)


def test_calculated_numeric_binding_is_present_and_still_owner_filtered():
    a=facts();pack,_=ctx.project(a)
    row=next(r for r in pack['financial_operating_cashflow'] if r.get('field')=='revenue_yoy_pct')
    assert 'revenue_yoy_pct' in row['numeric_fields']
    candidate=dict(topic='business',basis='inference',scope='research_only',
        text='收入同比为{{n1}}，仍需核对利润与现金转化，不能单凭收入改善确认投资逻辑。',
        evidence_refs=[row['evidence_ref']],numbers=[dict(id='n1',evidence_ref=row['evidence_ref'],field='revenue_yoy_pct')])
    accepted,_=explanation_projection([candidate],a)
    assert accepted and '{{n1}}' not in accepted[0]['text']
    candidate['numbers'][0]['field']='fabricated_metric'
    assert not explanation_projection([candidate],a)[0]


def test_over_budget_l1_never_initializes_a_model_client(monkeypatch):
    from intent_engine import _llm_client
    from backend.services.investment_policy import runtime
    monkeypatch.setattr(ctx,'HARD_TOKENS',1)
    monkeypatch.setattr(_llm_client,'get_client',lambda:pytest.fail('must not send oversized request'))
    monkeypatch.setattr(runtime,'context',lambda *a,**k:dict(mode='LEGACY'))
    e=ExecutionOutput(analysis_mode='l1_research',research_question='研究',position_evidence=qualitative(),
        loaded_data=NS(is_new_entry=False,target_position=None))
    p=PlanningOutput(route='position_single',intent={'primary_intent':'PositionDecision'})
    async def run():
        agent=ExpressingAgent()
        async for _ in agent.run_streaming(p,e,'unused'):pass
        return agent.last_output
    out=asyncio.run(run())
    assert out.status==AgentTaskStatus.FAILED and out.error=='L1_CONTEXT_BUDGET_EXCEEDED'
    assert e.research_context_diagnostics['status']=='budget_exceeded'


def test_tokenizer_asset_path_does_not_reset_model_policy_or_load_keys(tmp_path,monkeypatch):
    from pocs.chatgpt_skills.worker import load_analysis_environment
    monkeypatch.setenv('TIKTOKEN_CACHE_DIR','original-test-cache')
    monkeypatch.setenv('WEALTHPILOT_LLM_MAIN_EFFORT','medium')
    monkeypatch.delenv('WEALTHPILOT_OPENAI_API_KEY',raising=False)
    config=tmp_path/'operator.json';config.write_text(json.dumps({'TIKTOKEN_CACHE_DIR':str(tmp_path/'vocab'),'WEALTHPILOT_OPENAI_API_KEY':'PRIVATE'}))
    load_analysis_environment(config,model_policy_only=True)
    assert os.environ['TIKTOKEN_CACHE_DIR']==str(tmp_path/'vocab')
    assert os.environ['WEALTHPILOT_LLM_MAIN_EFFORT']=='medium'
    assert 'WEALTHPILOT_OPENAI_API_KEY' not in os.environ


def test_compact_facts_is_explicit_l1_opt_in_with_unchanged_full_facts():
    from backend.services.market_data.output_policy import facts_only_result
    from tests.test_l1_reliability import AT
    a=facts();before=deepcopy(a)
    legacy=facts_only_result(a,at=AT);compact=facts_only_result(a,at=AT,compact=True)
    assert a==before
    assert legacy[0]==compact[0] and legacy[1]==compact[1]
    assert compact[0]['financial_research']['valuation_sensitivity']==[]
    assert len(compact[2])<len(legacy[2])
    assert '专业推断未完成' in compact[2]


def test_exact_hypothesis_duplicate_is_shared_without_merging_different_assumptions():
    a=qualitative();a['research_intent']={'conditional_valuation':{'growth':'.1'}}
    a['conditional_valuation']={'assumptions':{'growth':'.1'}};original=deepcopy(a)
    p,_=ctx.project(a)
    assert a==original
    assert p['constraints_intent']['research_intent']['conditional_valuation']=={'context_reference':'conditional_valuation.assumptions'}
    assert p['conditional_valuation']['assumptions']==original['conditional_valuation']['assumptions']
    a['conditional_valuation']['assumptions']['growth']='.2'
    p,_=ctx.project(a)
    assert p['constraints_intent']['research_intent']['conditional_valuation']=={'growth':'.1'}


def test_owner_quarterly_scope_does_not_depend_on_derived_metrics(monkeypatch):
    from backend.services.market_data import financial_research
    a=facts()
    monkeypatch.setattr(financial_research,'build',lambda value:{'records':[]})
    eligible=_evidence_index(a)
    raw={ref:r for ref,r in eligible.items() if r.get('scope')=='quarterly' and r.get('field')=='revenue_quarter'}
    assert raw
    pack,_=ctx.project(a)
    kept={r['evidence_ref'] for r in pack['financial_operating_cashflow']}
    latest=max(r['period'] for r in raw.values())
    assert all((r.get('record_id') or r['source_url']) in kept for r in raw.values() if r['period']==latest)


def expand_tables(groups):
    result=deepcopy(groups)
    for section in ctx.RECORD_SECTIONS:
        table=result[section]
        if isinstance(table,dict):
            assert table['null_cells']=='absent_field'
            result[section]=[{key:value for key,value in zip(table['columns'],row) if value is not None}
                             for row in table['rows']]
    return result


def test_soft_target_triggers_lossless_tables_without_dropping_any_topic(monkeypatch):
    full,_=ctx.project(facts())
    full['financial_operating_cashflow'][0]['values']['unknown_nested_value']=None
    assert expand_tables(ctx._record_tables(full))==full
    a=qualitative()
    row=deepcopy(a['records'][0]);row['record_id']='fixture:additional-qualified-context'
    row.pop('source_url',None);a['records'].append(row)
    before=deepcopy(a)
    monkeypatch.setattr(ctx,'TARGET_TOKENS',1000000)
    raw,first=ctx.messages(a,'仅研究经营与条件回报',policy={'rules':[{'no_orders':True}]},holding={})
    monkeypatch.setattr(ctx,'TARGET_TOKENS',1)
    compact,last=ctx.messages(a,'仅研究经营与条件回报',policy={'rules':[{'no_orders':True}]},holding={})
    assert first['record_encoding']=='objects' and last['record_encoding']=='columns_rows_v1'
    assert expand_tables(json.loads(compact[1]['content']))==json.loads(raw[1]['content'])
    assert last['saved_reference_tokens']>0 and last['selected_records']==first['selected_records']
    assert a==before


def test_sensitivity_annual_arrays_are_display_duplicates_not_missing_endpoints():
    source={'scenarios':{'base':{'years':[{'year':1,'eps':'4'}],'terminal_price':'80'}},
            'sensitivity':[{'parameter':'growth','delta':'.01','result':
                {'years':[{'year':1,'eps':'5'}],'terminal_price':'90','annualized_return':'.1'}},
                {'result':'UNKNOWN'}]}
    before=deepcopy(source);result=ctx._conditional(source)
    assert source==before
    assert result['scenarios']['base']=={'terminal_price':'80','annual_detail_in_full_evidence':True}
    assert result['sensitivity'][0]['result']=={'terminal_price':'90','annualized_return':'.1','annual_detail_in_full_evidence':True}
    assert result['sensitivity'][1]==source['sensitivity'][1]


def test_four_quarter_core_fields_survive_without_any_derived_dependencies(monkeypatch):
    from backend.services.market_data import financial_research
    a=facts();sample=next(r for r in _evidence_index(a).values() if r.get('field')=='operating_income_quarter')
    for key in ('records','quality_field_records','historical_field_records','verified_period_records'):
        a[key]=[]
    a['conditional_valuation']={};a['verified_field_records']=[]
    dates=['2026-06-30','2026-03-31','2025-12-31','2025-09-30','2024-06-30']
    for period in dates:
        row=deepcopy(sample);row.update(record_id='fixture:quarter:'+period,period=period,scope='quarterly')
        row.pop('source_url',None);a['verified_field_records'].append(row)
    monkeypatch.setattr(financial_research,'build',lambda value:{'records':[]})
    pack,_=ctx.project(a)
    assert {r['period'] for r in pack['financial_operating_cashflow']}==set(dates[:4])


def test_longest_legal_user_input_is_rejected_before_model_initialization(monkeypatch):
    from pocs.chatgpt_skills.analysis import AnalysisRequest
    from intent_engine import _llm_client
    from backend.services.investment_policy import runtime
    request=AnalysisRequest(question='公司经营研究'*1000,
        history=[{'role':'user','content':(str(i)+'研究假设'*3000)[:12000]} for i in range(16)])
    assert len(request.question)==6000 and len(request.history)==16
    monkeypatch.setattr(_llm_client,'get_client',lambda:pytest.fail('must not send oversized legal input'))
    monkeypatch.setattr(runtime,'context',lambda *a,**kw:{'mode':'LEGACY'})
    e=ExecutionOutput(analysis_mode='l1_research',research_question=request.question,
        research_history=[r.model_dump() for r in request.history],position_evidence=qualitative(),
        loaded_data=NS(is_new_entry=False,target_position=None))
    async def run():
        agent=ExpressingAgent()
        async for _ in agent.run_streaming(PlanningOutput(route='position_single',intent={'primary_intent':'PositionDecision'}),e,'unused'):pass
        return agent.last_output
    result=asyncio.run(run())
    assert result.error=='L1_CONTEXT_BUDGET_EXCEEDED'
    assert e.research_context_diagnostics['estimated_with_frame_reserve']>40000
    assert '公司经营研究' not in json.dumps(e.research_context_diagnostics,ensure_ascii=False)


@pytest.mark.parametrize('corrupted',[False,True])
def test_cold_worker_missing_or_corrupted_vocabulary_never_downloads(tmp_path,corrupted):
    import hashlib
    cache=tmp_path/'vocab';cache.mkdir()
    if corrupted:(cache/hashlib.sha1(ctx.VOCAB_URL.encode()).hexdigest()).write_bytes(b'not the official vocabulary')
    code="""import socket
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN'))
from backend.services.market_data import l1_context as c
assert c._encoder() is None
assert c.measure('中文')==dict(bytes=6,tokens=6,counting='utf8_byte_upper_bound')
"""
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR') if k in os.environ}
    env.update(PYTHONPATH=str(Path(__file__).resolve().parents[1]),PYTHON_DOTENV_DISABLED='1',TIKTOKEN_CACHE_DIR=str(cache))
    result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr.decode()
