"""Real SDK/HTTPX with MockTransport: zero network or private data."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from app.llm_acceptance import Budget, AdmissionDenied, guarded_transport
from app.llm_config import SOL, LUNA, chat_completion, parse_response, LLMCallError
from app.llm_observation import analysis_run
from tests.test_llm_observation import completion


def body(**changes):
    return dict(model=LUNA, messages=[dict(role="user", content="PRIVATE_INPUT")],
        max_completion_tokens=40, store=False, service_tier="default", **changes)


def request(value=None, url="https://api.openai.com/v1/chat/completions"):
    return httpx.Request("POST", url, json=body() if value is None else value)


def claim(budget, trace, symbol="LI:US"):
    budget.claim(symbol, trace.analysis_run_id)


def test_real_sdk_transport_and_business_retries_share_exact_24_cap(tmp_path):
    sends=[]
    def receive(req):
        sends.append(req)
        return httpx.Response(500,json={"error":{"message":"PRIVATE_PROVIDER_BODY"}})
    with openai.OpenAI(api_key="PRIVATE_KEY", http_client=httpx.Client(transport=httpx.MockTransport(receive))) as client:
        budget=Budget(tmp_path/'ledger')
        with guarded_transport(budget), analysis_run() as (trace,_):
            claim(budget,trace)
            for _ in range(15):  # 30 SDK attempts requested by business retries.
                with pytest.raises(LLMCallError):
                    chat_completion(client,model=LUNA,messages=[dict(role="user",content="PRIVATE_INPUT")],max_tokens=10)
        assert len(sends)==budget.snapshot()['attempt_count']==24
        assert all(json.loads(req.content)['service_tier']=='default' for req in sends)
        assert 'PRIVATE' not in (tmp_path/'ledger').read_text()


@pytest.mark.parametrize('endpoint', ['chat','responses'])
def test_cost_reservation_refuses_before_http_and_never_refunds_failures(tmp_path,endpoint):
    budget=Budget(tmp_path/'ledger',max_usd='.00001')
    sends=[]
    with httpx.Client(transport=httpx.MockTransport(lambda req:sends.append(req))) as client:
        with guarded_transport(budget), analysis_run() as (trace,_):
            claim(budget,trace)
            payload=body() if endpoint=='chat' else dict(model=LUNA,input='x',max_output_tokens=40,store=False,service_tier='default')
            with pytest.raises(AdmissionDenied,match='BUDGET_EXHAUSTED'):
                client.send(request(payload, 'https://api.openai.com/v1/'+('chat/completions' if endpoint=='chat' else 'responses')))
    assert sends==[] and budget.snapshot()['attempt_count']==0


@pytest.mark.parametrize('changes',[
    {'model':'gpt-4.1'}, {'model':'unknown'}, {'max_completion_tokens':None},
    {'max_completion_tokens':True}, {'n':2}, {'stream':True}, {'store':True},
    {'service_tier':'auto'}, {'service_tier':'fast'}, {'tools':[{'type':'web_search'}]},
    {'previous_response_id':'private'}, {'prompt_cache_options':{'retention':'12h'}},
    {'messages':[{'role':'user','content':[{'type':'image_url','image_url':{'url':'private'}}]}]},
    {'messages':'x'*128001}, {'extra_unknown_billing':'x'},
])
def test_unknown_prices_modes_caps_and_modalities_never_send(tmp_path,changes):
    budget=Budget(tmp_path/'ledger')
    payload=body();payload.update(changes)
    with analysis_run() as (trace,_):
        claim(budget,trace)
        with pytest.raises(AdmissionDenied):budget.before_send(request(payload))
    assert budget.snapshot()['attempt_count']==0


@pytest.mark.parametrize('url',[
    'https://api.perplexity.ai/chat/completions','http://api.openai.com/v1/chat/completions',
    'https://api.openai.com/v1/embeddings','https://api.openai.com/v1/chat/completions?x=1',
    'https://api.openai.com.evil/v1/chat/completions','https://api.openai.com:444/v1/responses'])
def test_unpriced_or_redirected_path_is_denied(tmp_path,url):
    budget=Budget(tmp_path/'ledger')
    with analysis_run() as (trace,_):
        claim(budget,trace)
        with pytest.raises(AdmissionDenied):budget.before_send(request(url=url))


def test_concurrent_threads_and_budget_instances_share_durable_reservations(tmp_path):
    first=Budget(tmp_path/'ledger');second=Budget(tmp_path/'ledger')
    with analysis_run() as (trace,_):
        claim(first,trace);second.runs.add(trace.analysis_run_id)
        def attempt(i):
            try:(first if i%2 else second).before_send(request());return True
            except AdmissionDenied:return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            results=list(pool.map(lambda pair:pair[0].run(attempt,pair[1]),[(copy_context(),i) for i in range(60)]))
    assert sum(results)==first.snapshot()['attempt_count']==second.snapshot()['attempt_count']==24
    assert float(first.snapshot()['reserved_usd'])<=3
    with pytest.raises(AdmissionDenied):Budget(tmp_path/'ledger',max_attempts=23)
    with analysis_run() as (trace,_), pytest.raises(AdmissionDenied,match='ALREADY_CLAIMED'):
        claim(first,trace)


def test_unassociated_background_closed_and_child_paths_are_rejected(tmp_path):
    import subprocess
    budget=Budget(tmp_path/'ledger')
    with guarded_transport(budget):
        with pytest.raises(AdmissionDenied):budget.before_send(request())
        with analysis_run() as (trace,_):
            claim(budget,trace);trace.snapshot('timeout')
            with pytest.raises(AdmissionDenied):budget.before_send(request())
        with pytest.raises(AdmissionDenied,match='CHILD_PROCESS'):
            subprocess.Popen(['never-run'])
    assert budget.snapshot()['attempt_count']==0


def test_real_responses_parse_and_async_transport_use_same_budget(tmp_path):
    class Parsed(BaseModel):
        answer:str
    sends=[]
    def receive(req):
        sends.append(req)
        return httpx.Response(200,json=dict(id='private',object='response',created_at=0,status='completed',
            model=LUNA,service_tier='default',usage=dict(input_tokens=2,output_tokens=3,total_tokens=5),
            output=[dict(id='private',type='message',role='assistant',status='completed',
                content=[dict(type='output_text',text='{"answer":"ok"}',annotations=[])])]))
    budget=Budget(tmp_path/'ledger')
    with guarded_transport(budget), analysis_run() as (trace,_):
        claim(budget,trace)
        with openai.OpenAI(api_key='PRIVATE_KEY',http_client=httpx.Client(transport=httpx.MockTransport(receive))) as client:
            result=parse_response(client,model=LUNA,input='PRIVATE_INPUT',text_format=Parsed,max_tokens=20)
            assert result.output_parsed.answer=='ok'
        async def send():
            async with httpx.AsyncClient(transport=httpx.MockTransport(receive)) as client:
                await client.send(request(dict(model=LUNA,input='x',max_output_tokens=20,store=False,service_tier='default'),
                    'https://api.openai.com/v1/responses'))
        asyncio.run(send())
    assert len(sends)==budget.snapshot()['attempt_count']==2


def test_original_sdk_timeout_retries_count_before_sending(tmp_path):
    sends=[]
    def timeout(req):
        sends.append(req);raise httpx.ReadTimeout('PRIVATE_TIMEOUT',request=req)
    budget=Budget(tmp_path/'ledger')
    with guarded_transport(budget), analysis_run() as (trace,_):
        claim(budget,trace)
        with openai.OpenAI(api_key='PRIVATE_KEY',http_client=httpx.Client(transport=httpx.MockTransport(timeout))) as client:
            with pytest.raises(LLMCallError,match='TIMEOUT'):
                chat_completion(client,model=SOL,max_tokens=20,messages=[dict(role='user',content='x')])
    assert len(sends)==budget.snapshot()['attempt_count']==2
    assert float(budget.snapshot()['reserved_usd'])>0


def test_ledger_safety_unknown_usage_and_restart_never_restore_allowance(tmp_path):
    budget=Budget(tmp_path/'ledger')
    with analysis_run() as (trace,_):
        claim(budget,trace);budget.before_send(request())
    restarted=Budget(tmp_path/'ledger')
    assert restarted.snapshot()['attempt_count']==1
    assert restarted.snapshot()['actual_bill']=='UNKNOWN'
    (tmp_path/'alias').symlink_to(tmp_path/'ledger')
    with pytest.raises(OSError):Budget(tmp_path/'alias')
    (tmp_path/'ledger').write_text('{}')
    with pytest.raises(AdmissionDenied,match='LEDGER_INVALID'):Budget(tmp_path/'ledger')


@pytest.mark.parametrize('symbol',['3690.HGT','3690.SGT','3690.HGT:HK','3690.SGT:HK'])
def test_native_channel_market_projection_uses_source_type_not_channel_alone(symbol):
    from backend.utils.symbol import position_market_symbol
    values=dict(symbol=symbol,ticker=symbol.split(':')[0],broker='guojin',
        broker_security_type='QNX_NATIVE_POSITION',vehicle_type='COMMON_STOCK',currency='CNY',sync_source='api')
    position=SimpleNamespace(**values);before=vars(position).copy()
    assert position_market_symbol(position)=='3690:HK'
    assert vars(position)==before
    for change in [{'broker':'tiger'},{'broker_security_type':None},{'broker_security_type':'STK'},
                   {'vehicle_type':'FUND'},{'vehicle_type':'CASH'}]:
        assert position_market_symbol(SimpleNamespace(**(values|change))) is None
    for invalid in ('3690.HGT:US','3690.SGT:CN',123,0,False):
        assert position_market_symbol(SimpleNamespace(**(values|{'symbol':invalid}))) is None


def test_raw_native_row_enters_original_peer_registry_without_identity_mutation(analysis_db,peer,monkeypatch):
    from sqlalchemy import create_engine,text
    from backend.services import demo_market_service
    import backend.core.demo_mode as demo
    monkeypatch.setattr(demo,'PUBLIC_DEMO_MODE',True)
    monkeypatch.setattr(demo,'DEMO_ALLOW_MARKET_DATA',False)
    from pocs.chatgpt_skills.analysis import analyze
    engine=create_engine('sqlite:///'+str(analysis_db))
    with engine.begin() as db:
        db.execute(text("UPDATE positions SET symbol='3690.HGT:HK',ticker='3690.HGT',broker='guojin',broker_security_type='QNX_NATIVE_POSITION',vehicle_type='COMMON_STOCK',currency='CNY',sync_source='api' WHERE ticker='AAPL'"))
    seen=[]
    monkeypatch.setattr(demo_market_service,'fetch_demo_quote',lambda symbol:seen.append(symbol))
    monkeypatch.setattr(demo_market_service,'fetch_demo_kline',lambda symbol:seen.append(symbol))
    result=asyncio.run(analyze(analysis_db,7,'analyze_position',{'question':'fixture'},synthetic=True))
    assert seen and set(seen)=={'3690:HK'}, (result.get('issues'),peer)
    with engine.connect() as db:
        assert db.execute(text("SELECT symbol,ticker,quantity,market_value_cny FROM positions WHERE broker='guojin'")).one()==('3690.HGT:HK','3690.HGT',100,10000)
    engine.dispose()


from tests.test_analysis_skills import analysis_db, peer  # noqa: F401,E402


def test_original_peer_multistage_calls_and_three_stocks_share_campaign(analysis_db,peer,monkeypatch,tmp_path):
    """Original orchestration+RO DB+final filter; synthetic prompts use real SDK IO."""
    from backend.agents.planning_agent import PlanningAgent
    from backend.agents.reviewing_agent import ReviewingAgent
    from decision_engine import llm_engine
    from pocs.chatgpt_skills.analysis import analyze
    from app.llm_observation import peer_stage
    sends=[]
    def receive(req):
        payload=json.loads(req.content);sends.append(payload)
        return httpx.Response(200,json=completion(payload['model']))
    client=openai.OpenAI(api_key='PRIVATE_KEY',http_client=httpx.Client(transport=httpx.MockTransport(receive)))
    original_plan,original_reason,original_review=PlanningAgent.run,llm_engine.reason,ReviewingAgent.run
    @peer_stage('planning')
    def planning(*args,**kwargs):
        chat_completion(client,model=LUNA,max_tokens=15,messages=[dict(role='user',content='synthetic planning')])
        return original_plan(*args,**kwargs)
    @peer_stage('expressing')
    def expressing(*args,**kwargs):
        chat_completion(client,model=SOL,max_tokens=15,messages=[dict(role='user',content='synthetic expression')])
        return original_reason(*args,**kwargs)
    @peer_stage('reviewing')
    def reviewing(*args,**kwargs):
        chat_completion(client,model=LUNA,reasoning_effort='low',max_tokens=15,
            messages=[dict(role='user',content='synthetic review')])
        return original_review(*args,**kwargs)
    monkeypatch.setattr(PlanningAgent,'run',planning);monkeypatch.setattr(llm_engine,'reason',expressing)
    monkeypatch.setattr(ReviewingAgent,'run',reviewing)
    budget=Budget(tmp_path/'ledger')
    with client, guarded_transport(budget):
        for symbol in ('LI:US','PDD:US','3690:HK'):
            with analysis_run() as (trace,_):
                claim(budget,trace,symbol)
                result=asyncio.run(analyze(analysis_db,7,'analyze_position',{'question':'fixture'},synthetic=True))
                observation=trace.snapshot('completed')
                assert result['order_created'] is False
                assert {'planning','expressing','reviewing'} <= {c['peer_stage'] for c in observation['calls']}
    assert len(sends)==budget.snapshot()['attempt_count']==9
    assert len(set(a['analysis_run_id'] for a in budget.snapshot()['attempts']))==3


def test_campaign_reuses_frozen_view_gate_and_expired_view_spends_nothing(monkeypatch,tmp_path):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('v42_peer_acceptance',Path(__file__).parents[1]/'scripts/v42_peer_acceptance.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    run_campaign=module.run_campaign
    from app import asset_views
    monkeypatch.setattr(asset_views,'load_view',lambda *a:{'captured_at':'2000-01-01T00:00:00+00:00'})
    budget=Budget(tmp_path/'ledger')
    with guarded_transport(budget), pytest.raises(ValueError,match='ANALYSIS_CONTEXT_EXPIRED'):
        asyncio.run(run_campaign('unused',7,'asset-view:v1:65',budget))
    assert budget.snapshot()['attempt_count']==0 and budget.snapshot()['analyses']=={}


def test_shared_ledger_across_three_real_processes(tmp_path):
    import subprocess,sys
    path=tmp_path/'ledger';Budget(path)
    code='''
import sys,httpx
from app.llm_acceptance import Budget,AdmissionDenied
from app.llm_observation import analysis_run
b=Budget(sys.argv[1])
with analysis_run() as (t,_):
 b.claim(sys.argv[2],t.analysis_run_id)
 for _ in range(12):
  try:b.before_send(httpx.Request('POST','https://api.openai.com/v1/chat/completions',json=dict(model='gpt-6-luna',messages=[dict(role='user',content='synthetic')],max_tokens=10,store=False,service_tier='default')))
  except AdmissionDenied:pass
'''
    children=[subprocess.Popen([sys.executable,'-c',code,str(path),symbol]) for symbol in ('LI:US','PDD:US','3690:HK')]
    assert all(child.wait(timeout=30)==0 for child in children)
    assert Budget(path).snapshot()['attempt_count']==24


def test_redirect_and_outside_adapter_paths_cannot_escape_admission(tmp_path):
    sends=[]
    def redirect(req):
        sends.append(str(req.url))
        return httpx.Response(307,headers={'Location':'https://api.openai.com/v1/embeddings'})
    budget=Budget(tmp_path/'ledger')
    with httpx.Client(transport=httpx.MockTransport(redirect),follow_redirects=True) as client:
        with guarded_transport(budget), analysis_run() as (trace,_):
            claim(budget,trace)
            with pytest.raises(AdmissionDenied):client.send(request())
    assert len(sends)==budget.snapshot()['attempt_count']==1
    assert budget.snapshot()['denials']['ENDPOINT_UNSUPPORTED']==1


def test_concurrent_cost_limit_is_checked_before_dispatch(tmp_path):
    budget=Budget(tmp_path/'ledger')
    payload=body();payload.update(model=SOL,max_completion_tokens=128000)
    with analysis_run() as (trace,_):
        claim(budget,trace)
        def send():
            try:budget.before_send(request(payload));return True
            except AdmissionDenied:return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda ctx:ctx.run(send),[copy_context() for _ in range(30)]))
    assert sum(results)==2 and budget.snapshot()['attempt_count']==2
    assert float(budget.snapshot()['reserved_usd'])<=3


def test_lifetime_guard_denies_late_sends_and_does_not_suppress_errors(tmp_path):
    import subprocess,sys
    code='''
import sys,httpx
from app.llm_acceptance import Budget,guarded_transport,AdmissionDenied
from app.llm_observation import analysis_run
b=Budget(sys.argv[1])
try:
 with guarded_transport(b,process_lifetime=True):
  raise ValueError('expected')
except ValueError:pass
else:raise AssertionError('suppressed')
with analysis_run() as (t,_):
 b.runs.add(t.analysis_run_id)
 try:
  httpx.Client().post('https://api.openai.com/v1/chat/completions',json={})
 except AdmissionDenied:pass
 else:raise AssertionError('late send escaped')
assert b.snapshot()['attempt_count']==0
'''
    subprocess.run([sys.executable,'-c',code,str(tmp_path/'ledger')],check=True,timeout=30)


def test_alternate_network_and_process_paths_are_denied_before_io(tmp_path):
    import os,socket,urllib.request,requests
    budget=Budget(tmp_path/'ledger')
    with guarded_transport(budget):
        with pytest.raises(AdmissionDenied,match='NETWORK_PATH'):
            with socket.socket() as connection:connection.connect(('127.0.0.1',9))
        with pytest.raises(AdmissionDenied,match='PROVIDER_PATH'):
            urllib.request.urlopen('https://api.openai.com/v1/responses',data=b'{}')
        with pytest.raises(AdmissionDenied,match='PROVIDER_PATH'):
            requests.post('https://api.openai.com/v1/chat/completions',json={})
        if hasattr(os,'posix_spawn'):
            with pytest.raises(AdmissionDenied,match='CHILD_PROCESS'):
                os.posix_spawn('/usr/bin/true',['true'],dict(os.environ))
    assert budget.snapshot()['attempt_count']==0


def test_only_reviewed_free_av_gets_can_use_the_non_model_route(tmp_path,monkeypatch):
    import urllib.request
    calls=[]
    monkeypatch.setattr(urllib.request.OpenerDirector,'open',lambda self,url,data=None,timeout=None:calls.append(url))
    budget=Budget(tmp_path/'ledger')
    with guarded_transport(budget):
        urllib.request.urlopen('https://www.alphavantage.co/query?function=CASH_FLOW&symbol=LI&apikey=PRIVATE')
        with pytest.raises(AdmissionDenied,match='PROVIDER_PATH'):
            urllib.request.urlopen('https://www.alphavantage.co/query?function=UNKNOWN&apikey=PRIVATE')
    assert len(calls)==1 and budget.snapshot()['attempt_count']==0
    assert 'PRIVATE' not in (tmp_path/'ledger').read_text()


def test_concurrent_first_start_creates_one_complete_durable_ledger(tmp_path):
    with ThreadPoolExecutor(max_workers=12) as pool:
        budgets=list(pool.map(lambda _:Budget(tmp_path/'ledger'),range(30)))
    assert all(b.snapshot()['attempt_count']==0 for b in budgets)
    assert (tmp_path/'ledger.lock').stat().st_mode & 0o777 == 0o600


def test_original_optional_futu_probe_degrades_without_connecting(tmp_path):
    from backend.agents.executing_agent import _is_futu_opend_available
    with guarded_transport(Budget(tmp_path/'ledger')):
        assert _is_futu_opend_available() is False


def test_campaign_keeps_original_final_explanation_contract_not_raw_payload():
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('v42_peer_receipt',Path(__file__).parents[1]/'scripts/v42_peer_acceptance.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    decision={'professional_explanation':[{'text':'filtered explanation'}],
        'analysisQualification':{'L3':{'allowed':False}},'explanationDiagnostics':{'kept_count':1}}
    result=dict(status='partial',issues=['missing evidence'],conclusion='final filtered conclusion',
        professional_result={'decisionResult':decision,'rawText':'PRIVATE_RAW'},evidence={'PRIVATE_ASSETS':'never copy'})
    receipt=module.final_record('LI:US',result,{'analysis_run_id':'0'*32})
    assert receipt['professional_explanation']==decision['professional_explanation']
    assert receipt['analysis_qualification']['L3']['allowed'] is False
    assert receipt['explanation_diagnostics']=={'kept_count':1}
    assert 'PRIVATE' not in json.dumps(receipt)
