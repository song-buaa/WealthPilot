"""Normal L1 deadline and retained facts. Frozen sources and SDK MockTransport only."""
import asyncio
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import time
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from app.analysis_runtime import Policy, budget_context, current, policy
from app.llm_config import SOL, LUNA, LLMCallError, chat_completion, request_options
from app.llm_observation import analysis_run, peer_stage, safe_metadata
from pocs.chatgpt_skills.analysis import analyze
from tests.test_analysis_skills import analysis_db, peer  # noqa: F401
from tests.test_llm_observation import completion
from tests.test_p0_quality_semantics import pdd_record, AT
from backend.services.market_data.evidence import assess
from backend.services.market_data.output_policy import facts_only_result


def facts():
    r=pdd_record()
    return assess({},symbol='PDD:US',research=[r],at=AT,disclosure_verifier={r['record_id']:r}.get)


def test_one_policy_and_normal_sync_contract_stay_separate(monkeypatch,tmp_path):
    for key in ('WEALTHPILOT_L1_ANALYSIS_TIMEOUT_SECONDS','WEALTHPILOT_L1_SOL_READ_TIMEOUT_SECONDS'):
        monkeypatch.delenv(key,raising=False)
    assert policy().total_seconds==600 and policy().ipc_seconds==660
    assert policy().sol_read_seconds==180
    assert request_options(SOL,timeout=60)['timeout']==90  # original sync API
    p=tmp_path/'private.json';p.write_text(json.dumps({'WEALTHPILOT_L1_ANALYSIS_TIMEOUT_SECONDS':'700','WEALTHPILOT_L1_SOL_READ_TIMEOUT_SECONDS':'200','PRIVATE_KEY':'never expose'}))
    assert policy(p).ipc_seconds==760 and policy(p).sol_read_seconds==200
    with budget_context():
        timeout=request_options(SOL)['timeout']
        assert timeout.read==180 and timeout.connect==timeout.pool==10 and timeout.write==30
        assert request_options(LUNA,timeout=45)["timeout"].read==45
    monkeypatch.setenv('WEALTHPILOT_L1_ANALYSIS_TIMEOUT_SECONDS','nan')
    with pytest.raises(ValueError,match='ANALYSIS_TIME_POLICY_INVALID'):policy()


@pytest.mark.parametrize('success',[True,False])
def test_SDK_retry_uses_remaining_deadline_and_no_third_attempt(success):
    attempts=[]
    def receive(request):
        attempts.append(dict(request.extensions['timeout']))
        if len(attempts)==1 or not success:
            time.sleep(.025)
            raise httpx.ReadTimeout('PRIVATE_PROVIDER_BODY',request=request)
        return httpx.Response(200,json=completion(SOL))
    with openai.OpenAI(api_key='synthetic-not-a-key',http_client=httpx.Client(transport=httpx.MockTransport(receive))) as client:
        client._calculate_retry_timeout=lambda *args:0  # remove SDK test backoff only
        with analysis_run() as (trace,_),budget_context(Policy(total_seconds=1,sol_read_seconds=180)):
            if success:chat_completion(client,model=SOL,messages=[dict(role='user',content='PRIVATE_PROMPT')])
            else:
                with pytest.raises(LLMCallError,match='LLM_TIMEOUT'):chat_completion(client,model=SOL,messages=[])
            data=safe_metadata(trace.snapshot('completed' if success else 'failed'))
    assert len(attempts)==2 and attempts[1]['read']<attempts[0]['read']
    row=data['calls'][0]
    assert row['transport_attempt_count']==2 and row['sdk_retry_count']==1
    assert row['timeout_read_ms']<=900
    assert row['input_byte_count']>=row['input_character_count']
    assert 'PRIVATE' not in json.dumps(data)
    if not success:assert row['usage_status']=='UNKNOWN'


def test_terminal_flag_prevents_late_SDK_retry_and_frozen_metadata_mutation():
    attempts=[]
    def receive(request):
        attempts.append(1);time.sleep(.15)
        raise httpx.ReadTimeout('private',request=request)
    async def run():
        with openai.OpenAI(api_key='synthetic-not-a-key',http_client=httpx.Client(transport=httpx.MockTransport(receive))) as client:
            client._calculate_retry_timeout=lambda *args:0
            with analysis_run() as (trace,_):
                with budget_context(Policy(total_seconds=2)):
                    task=asyncio.create_task(asyncio.to_thread(chat_completion,client,model=SOL,messages=[]))
                    await asyncio.sleep(.04)
                frozen=trace.snapshot('timeout')
                with pytest.raises(LLMCallError,match='LLM_TIMEOUT'):await task
                assert trace.snapshot()==frozen
                return frozen
    frozen=asyncio.run(run())
    assert len(attempts)==1 and frozen['calls'][0]['transport_attempt_count']==1


def test_pdd_facts_only_preserves_clocks_and_exact_fields_without_decision():
    original=facts();before=deepcopy(original)
    kept=facts_only_result(original,at=AT)
    assert kept
    a,rows,text=kept
    assert {r['field'] for r in rows if r['kind']=='fundamental_field'}=={'revenue_quarter','net_income_quarter','operating_cash_flow_quarter'}
    assert all(r['published_at']=='2026-08-25T07:20:02-04:00' for r in rows)
    assert '专业推断未完成' in text and '112358000000' in text
    assert original==before
    assert facts_only_result(original,at=AT.replace(year=2027)) is None  # no clock rejuvenation
    assert 'HOLD' not in text and 'exitPlan' not in text


@pytest.mark.parametrize('kind',['timeout','failure','review_timeout','no_facts'])
def test_analyze_keeps_completed_facts_only_not_unreviewed_model_text(analysis_db,monkeypatch,kind):
    from pocs.chatgpt_skills import analysis
    from backend.services import decision_service_v3 as svc
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return AT
    monkeypatch.setattr(analysis,'datetime',Clock)
    receipt=facts() if kind!='no_facts' else assess({},symbol='PDD:US',at=AT)
    async def stream(*args,**kw):
        kw['stage_callback'](NS(route='position_single'),NS(position_evidence=receipt))
        if kind=='review_timeout':kw['stage_callback'](NS(route='position_single'),NS(position_evidence=receipt),stage='expressing_complete')
        yield svc._sse('text',{'delta':'UNREVIEWED_PRIVATE_MODEL_BUY_123'})
        if kind=='failure':raise RuntimeError('PRIVATE_RAW_PROVIDER_ERROR')
        raise TimeoutError()
    monkeypatch.setattr(svc,'run_chat_stream_v3',stream)
    before=hashlib.sha256(analysis_db.read_bytes()).hexdigest()
    result=asyncio.run(analyze(analysis_db,7,'analyze_position',dict(question='frozen',symbol='PDD:US'),synthetic=True,async_l1=True))
    assert result['status']==('unavailable' if kind=='no_facts' else 'partial')
    assert not result['professional_analysis_complete'] and not result['execution_authorized'] and not result['order_created']
    assert 'PRIVATE' not in json.dumps(result) and 'UNREVIEWED' not in result['conclusion']
    assert hashlib.sha256(analysis_db.read_bytes()).hexdigest()==before
    if kind!='no_facts':
        assert sum(r['kind']=='fundamental_field' for r in result['evidence']['qualified_facts'])==3
        assert result['professional_result']['analysis_result']['professional_explanation']==[]
        assert result['missing_stages']==(['reviewing'] if kind=='review_timeout' else ['expressing','reviewing'])
        assert 'decisionType' not in result['professional_result']['analysis_result']


@pytest.mark.parametrize('symbol',['LI:US','3690:HK'])
def test_qualified_technical_periods_survive_without_financial_facts(symbol):
    from dataclasses import asdict
    from tests.test_p0_hk_calendar import bars
    from backend.services.execution_plan.kline_provider import KlineResult
    from backend.services.market_data.technical_evidence import from_bars
    technical=from_bars(symbol,KlineResult(bars(),'broker'),at=AT) if symbol.endswith(':HK') else None
    if technical is None:
        from tests.test_v42_real_research import frozen
        import pandas as pd
        src=frozen('sources-LI-US.json');at=datetime.fromisoformat(src['finished_at'])
        technical=from_bars(symbol,KlineResult(pd.DataFrame(src['technical']['series']['day']['bars']),src['technical']['source']),at=at)
    else:at=AT
    a=assess({'technical':asdict(technical)},symbol=symbol,at=at)
    result=facts_only_result(a,at=at)
    assert result and result[0]['technical_levels'].keys()=={'day','week'}
    assert 'RSI14' in result[2] and 'MA20' in result[2]
    assert not any(r['kind']=='fundamental_field' for r in result[1])


def test_normal_complete_peer_retains_professional_result(analysis_db,peer):
    result=asyncio.run(analyze(analysis_db,7,'analyze_position',dict(question='frozen'),synthetic=True,async_l1=True))
    assert result['status']=='partial'  # existing data gaps, not an inference failure
    assert result['professional_analysis_complete']
    assert not result['missing_stages'] and result['professional_result']['analysis_result'].get('mode')!='facts_only'
    assert not result['execution_authorized']


def test_no_retry_send_when_first_attempt_consumes_remaining_time():
    sent=[]
    def receive(req):
        sent.append(1);current().deadline=time.monotonic()-1
        raise httpx.ReadTimeout('private',request=req)
    with openai.OpenAI(api_key='synthetic',http_client=httpx.Client(transport=httpx.MockTransport(receive))) as client:
        with budget_context(Policy(total_seconds=2)):
            with pytest.raises(LLMCallError,match='LLM_TIMEOUT'):chat_completion(client,model=SOL,messages=[])
    assert len(sent)==1


def test_responses_uses_same_budget_and_safe_persisted_timing():
    from pydantic import BaseModel
    from app.llm_config import LUNA,parse_response
    from pocs.chatgpt_skills.diagnostics import diagnostic,safe_diagnostic
    class Answer(BaseModel):ok:bool
    body=dict(id='synthetic',object='response',created_at=0,model=LUNA,service_tier='default',status='completed',
        output=[dict(id='synthetic',type='message',role='assistant',status='completed',
                     content=[dict(type='output_text',text='{"ok":true}',annotations=[])])])
    def receive(req):
        assert req.extensions['timeout']['read']<=20
        return httpx.Response(200,json=body)
    @peer_stage('planning')
    def plan(client):return parse_response(client,model=LUNA,reasoning_effort='low',input='PRIVATE',text_format=Answer)
    with openai.OpenAI(api_key='synthetic',http_client=httpx.Client(transport=httpx.MockTransport(receive))) as client:
        with analysis_run() as (trace,_),budget_context():
            assert plan(client).output_parsed.ok
            meta=trace.snapshot('completed')
    event=diagnostic(meta['analysis_run_id'],'mcp_return',status='partial',observation=meta)
    event['model_call_timings'][0]['PRIVATE']='do not persist'
    safe=safe_diagnostic(event,meta['analysis_run_id'])
    row=safe['model_call_timings'][0]
    assert row['requested_model']==row['returned_model']==LUNA
    assert row['message_count']==1 and row['transport_attempt_count']==1
    assert row['end_offset_ms']>=row['start_offset_ms']
    assert safe['peer_stage_timings'][0]['peer_stage']=='planning'
    assert 'PRIVATE' not in json.dumps(safe)


def test_operator_runtime_values_cross_worker_without_loading_credentials(monkeypatch,tmp_path):
    from pocs.chatgpt_skills.worker import load_analysis_environment
    monkeypatch.delenv('WEALTHPILOT_OPENAI_API_KEY',raising=False)
    for key in ('WEALTHPILOT_L1_ANALYSIS_TIMEOUT_SECONDS','WEALTHPILOT_L1_SOL_READ_TIMEOUT_SECONDS'):
        monkeypatch.setenv(key,'600' if 'ANALYSIS_TIMEOUT' in key else '180')
    p=tmp_path/'operator.json';p.write_text(json.dumps({'WEALTHPILOT_L1_ANALYSIS_TIMEOUT_SECONDS':700,
        'WEALTHPILOT_L1_SOL_READ_TIMEOUT_SECONDS':'200','WEALTHPILOT_OPENAI_API_KEY':'PRIVATE'}))
    parent=policy(p)
    load_analysis_environment(p,model_policy_only=True)
    assert policy()==parent
    assert 'WEALTHPILOT_OPENAI_API_KEY' not in __import__('os').environ


def test_original_peer_finishes_after_old_240_second_deadline_with_virtual_clock(analysis_db,peer,monkeypatch):
    """Advance only the event-loop clock; no long sleep or network call."""
    from decision_engine import llm_engine
    original=llm_engine.reason
    offset=[0]
    def reason(*a,**kw):
        result=original(*a,**kw)
        offset[0]+=250
        return result
    monkeypatch.setattr(llm_engine,'reason',reason)
    async def run(async_l1):
        loop=asyncio.get_running_loop();real=loop.time;offset[0]=0
        loop.time=lambda:real()+offset[0]
        try:return await analyze(analysis_db,7,'analyze_position',dict(question='frozen'),synthetic=True,async_l1=async_l1)
        finally:loop.time=real
    old=asyncio.run(run(False))
    assert 'ANALYSIS_TIMEOUT' in old['issues'] and not old['professional_analysis_complete']
    new=asyncio.run(run(True))
    assert 'ANALYSIS_TIMEOUT' not in new['issues'] and new['professional_analysis_complete']


def test_recovery_rechecks_old_qualification_and_cannot_create_new_refs():
    from datetime import timedelta
    a=facts()
    # Simulate an initially allowed gate; recovery must use existing current
    # formulas rather than carry it past an expired quote/context.
    a['qualification']['L3']={'allowed':True,'blockers':[]}
    rebuilt,rows,_=facts_only_result(a,at=AT)
    assert not rebuilt['qualification']['L3']['allowed']
    assert 'SNAPSHOT_REQUIRED' in rebuilt['qualification']['L3']['blockers'] or rebuilt['qualification']['L3']['blockers']
    assert {r['record_id'] for r in rows} <= {r['record_id'] for r in [*a['records'],*a['verified_field_records']]}
    # Research and event have different original TTLs. An expired event must
    # never be reclassified into a still-valid 90-day research fact.
    from tests.test_v42_evidence import verified
    event=verified('event',text='A dated public event source, not model output.')
    event['symbol']='PDD:US';event['data_as_of']=event['published_at']=event['fetched_at']=(AT-timedelta(days=6)).isoformat()
    b=assess({},symbol='PDD:US',events=[event],at=AT)
    assert facts_only_result(b,at=AT)
    assert facts_only_result(b,at=AT+timedelta(days=2)) is None


def test_sync_stage_exception_keeps_original_failure_behavior(analysis_db,monkeypatch):
    from backend.services import decision_service_v3 as svc
    async def stream(*a,**kw):
        assert kw['stage_callback'] is None
        raise RuntimeError('synthetic')
        yield
    monkeypatch.setattr(svc,'run_chat_stream_v3',stream)
    with pytest.raises(RuntimeError,match='synthetic'):
        asyncio.run(analyze(analysis_db,7,'analyze_position',dict(question='frozen'),synthetic=True))


def test_terminal_flag_also_blocks_followup_HTTPX_redirect_send():
    sent=[]
    def receive(req):
        sent.append(1)
        current().finished.set()
        return httpx.Response(307,headers={'location':'https://api.openai.com/v1/chat/completions'})
    with openai.OpenAI(api_key='synthetic',http_client=httpx.Client(transport=httpx.MockTransport(receive),follow_redirects=True)) as client:
        with analysis_run() as (trace,_),budget_context():
            with pytest.raises(LLMCallError,match='LLM_TIMEOUT'):chat_completion(client,model=SOL,messages=[])
            meta=trace.snapshot('timeout')
    assert len(sent)==1
    assert meta['calls'][0]['transport_attempt_count']==1 and meta['calls'][0]['sdk_retry_count']==0


def test_aggregate_technical_ref_cannot_grant_unqualified_period_authority():
    from dataclasses import asdict
    from tests.test_p0_hk_calendar import bars
    from backend.services.market_data.technical_evidence import from_bars
    from backend.services.execution_plan.kline_provider import KlineResult
    from backend.services.market_data.output_policy import captured_facts
    t=from_bars('3690:HK',KlineResult(bars(),'broker'),at=AT)
    a=assess({'technical':asdict(t)},symbol='3690:HK',at=AT)
    row=next(r for r in a['records'] if r['kind']=='technical')
    row['period_quality']['week']['quality']='unknown'
    a['qualification']['L3']['allowed']=False
    # Even inconsistent aggregate metadata cannot replace the period receipt.
    restored,_=captured_facts(a,at=AT)
    assert 'week' not in restored['technical_levels']
    assert not restored['qualification']['L3']['allowed']


def test_whole_runtime_deadline_and_expired_startup_are_bounded():
    from app.analysis_runtime import budgeted_analysis
    touched=[]
    @budgeted_analysis
    async def work(**kwargs):
        touched.append(True);await asyncio.sleep(.2)
    async def run():
        with budget_context(Policy(total_seconds=.06)):
            with pytest.raises(TimeoutError):await work(async_l1=True)
        assert touched==[True]
        touched.clear()
        with budget_context(Policy(total_seconds=.06),deadline=time.monotonic()-1):
            with pytest.raises(TimeoutError):await work(async_l1=True)
        assert not touched
    asyncio.run(run())


def test_late_synchronous_completion_cannot_return_professional_success(monkeypatch):
    from app.analysis_runtime import budgeted_analysis
    from pocs.chatgpt_skills import analysis
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return AT
    monkeypatch.setattr(analysis,'datetime',Clock)
    value=dict(synthetic=True,scenario='analyze_position',status='ok',issues=[],queried_at=AT.isoformat(),
        conclusion='UNREVIEWED_PROFESSIONAL_BUY_123',professional_result={'decisionType':'buy'},
        professional_analysis_complete=True,evidence={'position_evidence':facts()})
    @budgeted_analysis(on_expired=analysis.deadline_result)
    async def work(**kwargs):
        time.sleep(.08)  # deliberately block the event loop beyond its deadline
        return value
    async def run():
        with budget_context(Policy(total_seconds=.05)):
            return await work(async_l1=True)
    result=asyncio.run(run())
    assert result['status']=='partial' and not result['professional_analysis_complete']
    assert result['professional_result']['analysis_result']['mode']=='facts_only'
    assert 'UNREVIEWED' not in result['conclusion']
    assert 'ANALYSIS_TIMEOUT' in result['issues']


def test_malformed_unicode_counts_are_unknown_and_do_not_abort_model_boundary():
    from app.llm_observation import AnalysisObservation
    t=AnalysisObservation()
    row=t.begin({'model':SOL,'messages':[{'role':'user','content':'\ud800'}]},'chat')
    assert row['message_count']==1 and row['input_character_count']>0 and row['input_byte_count'] is None
    cyclic=[];cyclic.append(cyclic)
    other=t.begin({'model':SOL,'messages':cyclic},'chat')
    assert other['input_character_count'] is None
    sent=[]
    def receive(req):
        sent.append(True);return httpx.Response(200,json=completion(SOL))
    with openai.OpenAI(api_key='synthetic',http_client=httpx.Client(transport=httpx.MockTransport(receive))) as client,analysis_run() as (trace,_):
        trace.begin=lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('observation only'))
        assert chat_completion(client,model=SOL,messages=[]).choices
        meta=trace.snapshot('completed')
        assert meta['collection_status']=='UNAVAILABLE' and meta['call_count'] is None
    assert sent


def test_valid_SDK_timeout_objects_and_none_are_deadline_clamped():
    with budget_context():
        assert request_options(LUNA,timeout=httpx.Timeout(60,read=45))['timeout'].read==45
        assert request_options(LUNA,timeout=None)['timeout'].read==20
        timeout=request_options(SOL,timeout=httpx.Timeout(None))['timeout']
        assert timeout.read==180 and timeout.connect==10
    assert isinstance(request_options(SOL,timeout=httpx.Timeout(60))['timeout'],httpx.Timeout)


def test_outer_deadline_during_stream_cleanup_still_retains_receipt(analysis_db,monkeypatch):
    from pocs.chatgpt_skills import analysis
    from backend.services import decision_service_v3 as svc
    receipt=facts()
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return AT
    monkeypatch.setattr(analysis,'datetime',Clock)
    async def stream(*a,**kw):
        kw['stage_callback'](NS(route='position_single'),NS(position_evidence=receipt))
        try:await asyncio.sleep(10)
        finally:await asyncio.sleep(.2)  # outer deadline interrupts this cleanup
        yield
    monkeypatch.setattr(svc,'run_chat_stream_v3',stream)
    async def run():
        with budget_context(Policy(total_seconds=.2)):
            return await analyze(analysis_db,7,'analyze_position',dict(question='frozen'),synthetic=True,async_l1=True)
    result=asyncio.run(run())
    assert result['status']=='partial' and not result['professional_analysis_complete']
    assert result['professional_result']['analysis_result']['mode']=='facts_only'
    assert 'ANALYSIS_TIMEOUT' in result['issues']
    assert result['evidence']['qualified_facts']


def test_result_assembly_failure_cannot_keep_success_status(analysis_db,peer,monkeypatch):
    from pocs.chatgpt_skills import analysis
    from backend.services import position_analysis
    receipt=facts()
    monkeypatch.setattr(position_analysis,'prepare',lambda *a,**kw:receipt)
    monkeypatch.setattr(analysis,'captured_evidence',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('private assembly error')))
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return AT
    monkeypatch.setattr(analysis,'datetime',Clock)
    result=asyncio.run(analyze(analysis_db,7,'analyze_position',dict(question='frozen'),synthetic=True,async_l1=True))
    assert result['status']=='partial' and not result['professional_analysis_complete']
    assert result['professional_result']['analysis_result']['mode']=='facts_only'
    assert 'PROFESSIONAL_STAGE_FAILURE' in result['issues']
    assert 'private assembly' not in json.dumps(result)
