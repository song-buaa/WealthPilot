"""Real SDK 2.29 with frozen HTTP only; metadata never carries business text."""
import asyncio
import json
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from app.llm_config import SOL, LUNA, LLMCallError, chat_completion, parse_response
from app.llm_observation import (AnalysisObservation, analysis_run, current, observed_result,
                                 peer_stage, safe_metadata, unavailable)


def completion(model=LUNA, **updates):
    value = dict(id="private-provider-id", object="chat.completion", created=0, model=model,
        service_tier="default", usage=dict(prompt_tokens=100, completion_tokens=30,
            total_tokens=130, completion_tokens_details=dict(reasoning_tokens=10)),
        choices=[dict(index=0, finish_reason="stop", message=dict(role="assistant", content="PRIVATE_MODEL_TEXT"))])
    value.update(updates)
    return value


def sdk(receive):
    return openai.OpenAI(api_key="PRIVATE_API_KEY", http_client=httpx.Client(transport=httpx.MockTransport(receive)))


@pytest.mark.parametrize("model,effort", [(SOL,"medium"), (LUNA,"none"), (LUNA,"low")])
def test_request_role_effort_usage_and_reference_cost(model, effort):
    def receive(request):
        body = json.loads(request.content)
        assert body["reasoning_effort"] == effort
        assert body["store"] is False
        return httpx.Response(200, json=completion(model))
    @peer_stage("expressing")
    def express(client):
        return chat_completion(client, model=model, reasoning_effort=effort,
            messages=[dict(role="user", content="PRIVATE_ASSET_AND_PROMPT")])
    with sdk(receive) as client, analysis_run() as (trace, owner):
        assert express(client).choices[0].message.content == "PRIVATE_MODEL_TEXT"
        value = trace.snapshot("completed")
    row = value["calls"][0]
    assert owner and row["peer_stage"] == "expressing"
    assert row["role"] == ("main" if model == SOL else "light")
    assert row["requested_model"] == row["returned_model"] == model
    assert row["reasoning_effort"] == effort and row["returned_reasoning_effort"] == "UNKNOWN"
    assert (row["input_tokens"], row["output_tokens"], row["reasoning_tokens"]) == (100,30,10)
    assert row["sdk_retry_count"] == 0 and row["transport_attempt_count"] == 1
    assert row["cost"]["estimated_cost_usd"] == (0.0005 if model == SOL else 0.000025)
    assert value["totals"]["estimated_cost_usd"] == row["cost"]["estimated_cost_usd"]
    assert value["totals"]["actual_bill"] == "UNKNOWN"
    assert not any(secret in json.dumps(value) for secret in ("PRIVATE", "private-provider-id", "messages", "Authorization"))
    assert safe_metadata(value) == value
    assert current() is None


def test_multiple_peer_stages_and_thread_context_share_one_run():
    @peer_stage("planning")
    def plan(client):
        chat_completion(client, model=LUNA, messages=[])
    @peer_stage("reviewing")
    def review(client):
        chat_completion(client, model=LUNA, reasoning_effort="low", messages=[])
    @peer_stage("expressing")
    async def express(client):
        await asyncio.to_thread(chat_completion, client, model=SOL, messages=[])
        yield "same original explanation"
    async def run():
        with sdk(lambda req:httpx.Response(200,json=completion(json.loads(req.content)["model"]))) as client:
            with analysis_run() as (trace, _):
                await asyncio.to_thread(plan, client)
                assert [x async for x in express(client)] == ["same original explanation"]
                await asyncio.to_thread(review, client)
                return trace.snapshot("completed")
    value = asyncio.run(run())
    assert [c["peer_stage"] for c in value["calls"]] == ["planning", "expressing", "reviewing"]
    assert value["call_count"] == 3 and value["totals"]["input_tokens"] == 300
    assert value["totals"]["transport_attempt_count"] == 3
    assert value["totals"]["reasoning_tokens"] == 30


def test_sdk_retry_success_is_two_transports_one_call_and_partial_cost():
    seen = []
    def receive(req):
        seen.append(req.headers["x-stainless-retry-count"])
        return httpx.Response(500, json={"error":{"message":"PRIVATE_BODY"}}) if len(seen)==1 else httpx.Response(200,json=completion())
    with sdk(receive) as client, analysis_run() as (trace,_):
        chat_completion(client,model=LUNA,messages=[])
        value = trace.snapshot("completed")
    assert seen == ["0", "1"]
    assert value["call_count"] == 1 and value["calls"][0]["sdk_retry_count"] == 1
    assert value["totals"]["transport_attempt_count"] == 2
    assert value["totals"]["estimated_cost_usd"] is None
    assert value["totals"]["cost_status"] == "UNKNOWN"  # Earlier attempts may also be billed.


@pytest.mark.parametrize("code,kind,retries", [(401,"authentication",0), (403,"model_access",0),
    (429,"quota",1), (500,"provider",1), ("timeout","timeout",1), ("network","network",1)])
def test_errors_retries_and_unknown_usage_do_not_echo_provider(code,kind,retries):
    def receive(req):
        if code == "timeout":
            raise httpx.ReadTimeout("PRIVATE_TIMEOUT", request=req)
        if code == "network":
            raise httpx.ConnectError("PRIVATE_NETWORK", request=req)
        return httpx.Response(code,json={"error":{"message":"PRIVATE_PROVIDER_BODY", "code":"insufficient_quota"}})
    with sdk(receive) as client, analysis_run() as (trace,_), pytest.raises(LLMCallError):
        try:
            chat_completion(client,model=SOL,messages=[])
        finally:
            value = trace.snapshot("failed")
    row=value["calls"][0]
    assert row["error_category"] == kind and row["sdk_retry_count"] == retries
    assert row["transport_attempt_count"] == retries+1
    assert row["usage_status"] == row["cost"]["status"] == "UNKNOWN"
    assert row["input_tokens"] is None and "PRIVATE" not in json.dumps(value)


@pytest.mark.parametrize("changes,kind", [({"model":SOL},"model_mismatch"),
    ({"choices":[dict(index=0,finish_reason="length",message=dict(role="assistant",content="PRIVATE_PARTIAL"))]},"truncated"),
    ({"choices":[dict(index=0,finish_reason="stop",message=dict(role="assistant",content=""))]},"empty_output")])
def test_returned_model_and_billable_usage_survive_rejected_outputs(changes,kind):
    with sdk(lambda req:httpx.Response(200,json=completion(**changes))) as client, analysis_run() as (trace,_):
        with pytest.raises(LLMCallError):chat_completion(client,model=LUNA,messages=[])
        value=trace.snapshot("failed")
    row=value["calls"][0]
    assert row["error_category"] == kind and row["output_tokens"] == 30
    if kind == "model_mismatch":assert row["cost"]["status"] == "UNKNOWN"
    assert "PRIVATE" not in json.dumps(value)


def test_responses_parse_usage_with_original_pydantic_output():
    from pydantic import BaseModel
    class Answer(BaseModel):
        ok: bool
    body=dict(id="private-response",object="response",created_at=0,model=LUNA,service_tier="default",
        status="completed",reasoning={"effort":"low"},
        usage=dict(input_tokens=80,output_tokens=22,total_tokens=102,output_tokens_details=dict(reasoning_tokens=12)),
        output=[dict(id="private-message",type="message",role="assistant",status="completed",
                     content=[dict(type="output_text",text='{"ok":true}',annotations=[])])])
    with sdk(lambda req:httpx.Response(200,json=body)) as client, analysis_run() as (trace,_):
        result=parse_response(client,model=LUNA,reasoning_effort="low",input="PRIVATE_PROMPT",text_format=Answer)
        assert result.output_parsed.ok
        value=trace.snapshot("completed")
    assert value["calls"][0]["returned_reasoning_effort"] == "low"
    assert value["totals"]["reasoning_tokens"] == 12


@pytest.mark.parametrize("change", [{"usage":None},{"service_tier":"flex"},{"model":"PRIVATE_MODEL_RESPONSE"}])
def test_unknown_usage_price_or_returned_identity_is_not_zero_or_a_quote(change):
    with sdk(lambda req:httpx.Response(200,json=completion(**change))) as client, analysis_run() as (trace,_):
        try:chat_completion(client,model=LUNA,messages=[])
        except LLMCallError:pass
        value=trace.snapshot("completed")
    assert value["totals"]["cost_status"] == "UNKNOWN"
    assert value["totals"]["estimated_cost_usd"] is None
    assert "PRIVATE_MODEL_RESPONSE" not in json.dumps(value)


def test_parent_timeout_and_late_thread_completion_do_not_claim_zero_usage():
    trace=AnalysisObservation()
    call=trace.begin({"model":SOL},"chat")
    before=trace.snapshot("timeout")
    trace.complete(call,result=NS(model=SOL),elapsed_ms=100)
    assert trace.snapshot() == before
    assert before["collection_status"] == "PARTIAL" and before["totals"]["input_tokens"] is None
    lost=unavailable(trace.analysis_run_id,status="timeout",elapsed_ms=240000)
    assert lost["call_count"] is None and lost["totals"]["input_tokens"] is None
    assert safe_metadata(lost) == lost


def test_no_observed_calls_and_non_text_functions_have_unknown_cost():
    empty=AnalysisObservation().snapshot('failed')
    assert empty['call_count']==0 and empty['totals']['input_tokens'] is None
    assert empty['totals']['estimated_cost_usd'] is None and empty['totals']['cost_status']=='UNKNOWN'
    with sdk(lambda req:httpx.Response(200,json=completion())) as client, analysis_run() as (trace,_):
        chat_completion(client,model=LUNA,messages=[],functions=[{'name':'fixture','parameters':{'type':'object'}}])
        value=trace.snapshot('completed')
    assert value['calls'][0]['usage_status']=='KNOWN' and value['calls'][0]['cost']['status']=='UNKNOWN'


def test_original_trade_intent_parser_attribution_is_planning_without_execution(monkeypatch):
    from backend.services.trade_intent import parser
    monkeypatch.setattr(parser,'is_actionable_trade_candidate',lambda message:True)
    class Provider:
        def extract(self,*args):
            with sdk(lambda req:httpx.Response(200,json=completion())) as client:
                chat_completion(client,model=LUNA,messages=[])
            return NS(is_trade_intent=False)
    with analysis_run() as (trace,_):
        assert parser.parse_trade_intent('synthetic intent',provider=Provider()) is None
        value=trace.snapshot('completed')
    assert value['calls'][0]['peer_stage']=='planning'


def test_closed_projection_strips_content_and_rejects_private_run_id():
    value=AnalysisObservation().snapshot("completed")
    value.update(prompt="PRIVATE",answer="PRIVATE",account="PRIVATE",execution_authorized=True)
    assert "PRIVATE" not in json.dumps(safe_metadata(value))
    value["analysis_run_id"]="PRIVATE_ACCOUNT_ID"
    assert safe_metadata(value) is None


def test_ipc_cost_is_recomputed_and_unknown_usage_cannot_become_a_quote():
    trace=AnalysisObservation();call=trace.begin({'model':LUNA},'chat')
    trace.complete(call,result=NS(model=LUNA,usage=NS(prompt_tokens=100,completion_tokens=30),service_tier='default'),
        raw=NS(retries_taken=0),standard_text=True)
    value=trace.snapshot('completed')
    value['calls'][0]['cost']['estimated_cost_usd']=999999
    cleaned=safe_metadata(value)
    assert cleaned['calls'][0]['cost']['estimated_cost_usd']==0.000025
    value['calls'][0]['input_tokens']=None
    cleaned=safe_metadata(value)
    assert cleaned['calls'][0]['cost']['status']=='UNKNOWN'
    assert cleaned['totals']['estimated_cost_usd'] is None


def test_malformed_sdk_responses_body_is_invalid_output_not_provider_failure():
    from pydantic import BaseModel
    class Answer(BaseModel):ok: bool
    body=dict(id='fixture',object='response',created_at=0,model=LUNA,status='completed',output=None,
        service_tier='default',usage=dict(input_tokens=80,output_tokens=22,total_tokens=102))
    with sdk(lambda req:httpx.Response(200,json=body)) as client, analysis_run() as (trace,_):
        with pytest.raises(LLMCallError) as error:
            parse_response(client,model=LUNA,reasoning_effort='low',input='PRIVATE',text_format=Answer)
        value=trace.snapshot('failed')
    assert error.value.kind=='invalid_output'
    assert value['calls'][0]['error_category']=='invalid_output'
    assert value['calls'][0]['input_tokens']==80


def test_invalid_effort_is_a_rejected_call_without_transport():
    with analysis_run() as (trace,_):
        with pytest.raises(LLMCallError):chat_completion(NS(),model=SOL,reasoning_effort='none')
        value=trace.snapshot('failed')
    row=value['calls'][0]
    assert row['status']=='rejected' and row['error_category']=='reasoning_effort_unsupported'
    assert row['transport_attempt_count']==0 and row['sdk_retry_count']==0
    assert safe_metadata(value)==value


def test_observed_result_internal_run_id_and_timeout_are_not_user_authority():
    @observed_result
    async def original(payload):
        assert current().analysis_run_id == payload["analysis_run_id"]
        raise TimeoutError("PRIVATE_ERROR")
    identifier="a"*32
    with pytest.raises(TimeoutError) as error:
        asyncio.run(original({"analysis_run_id":identifier}))
    assert error.value.llm_observation["analysis_run_id"] == identifier
    assert error.value.llm_observation["status"] == "timeout"
    assert current() is None


def test_observation_failure_cannot_change_professional_output(monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("PRIVATE_METADATA")
    with sdk(lambda req:httpx.Response(200,json=completion())) as client, analysis_run() as (trace,_):
        monkeypatch.setattr(trace,"complete",broken)
        result=chat_completion(client,model=LUNA,messages=[])
        assert result.choices[0].message.content=="PRIVATE_MODEL_TEXT"
        value=trace.snapshot("completed")
    assert value["calls"][0]["error_category"]=="observation_unavailable"
    assert value["totals"]["estimated_cost_usd"] is None
    assert "PRIVATE" not in json.dumps(value)


def test_offline_sdk_factory_injection_keeps_original_peer_compatibility(monkeypatch):
    result=NS(model=LUNA,choices=[NS(finish_reason='stop',message=NS(content='original offline explanation'))])
    fake=NS(chat=NS(completions=NS(create=lambda **kwargs:result)))
    monkeypatch.setattr(openai,'OpenAI',lambda **kwargs:fake)
    with analysis_run() as (trace,_):
        assert chat_completion(openai.OpenAI(),model=LUNA).choices[0].message.content=='original offline explanation'
        value=trace.snapshot('completed')
    assert value['calls'][0]['transport_attempt_count'] is None
    assert value['calls'][0]['status']=='completed'


def test_original_peer_stream_metadata_is_separate_and_concurrent_runs_isolated(monkeypatch):
    from backend.services import decision_service_v3 as peer
    from backend.services import decision_service
    async def original(question, conversation, **kwargs):
        @peer_stage("expressing")
        def express(client):
            chat_completion(client, model=SOL, messages=[])
        with sdk(lambda req:httpx.Response(200,json=completion(SOL))) as client:
            await asyncio.to_thread(express,client)
        decision_service._DECISION_STORE[conversation]={conversation:NS(_analysis_run_id=current().analysis_run_id)}
        yield peer._sse("text", {"delta":"unchanged verified explanation"})
        await asyncio.sleep(0)
        yield peer._sse("done", {"decision_id":conversation,"actionable":False,"analysisQualification":{"L3":{"allowed":False}}})
    monkeypatch.setattr(peer,"_run_chat_stream_v3",original)
    async def collect(name):
        return [json.loads(e.splitlines()[1][6:]) async for e in peer.run_chat_stream_v3("PRIVATE",name)]
    async def run():return await asyncio.gather(collect("observation-fixture-a"),collect("observation-fixture-b"))
    a,b=asyncio.run(run())
    for events in (a,b):
        assert events[0]=={"delta":"unchanged verified explanation"}
        done=events[1];assert done["actionable"] is False and not done["analysisQualification"]["L3"]["allowed"]
        observation=done["llm_observation"]
        assert observation["call_count"]==1 and observation["calls"][0]["peer_stage"]=="expressing"
        assert decision_service._DECISION_STORE[done["decision_id"]][done["decision_id"]]._llm_observation==observation
        decision_service.clear_session(done["decision_id"])
    assert a[1]["llm_observation"]["analysis_run_id"] != b[1]["llm_observation"]["analysis_run_id"]
    assert current() is None


def test_interleaved_peer_generators_do_not_leak_context_to_their_caller(monkeypatch):
    from backend.services import decision_service_v3 as peer
    seen=[]
    async def original(question,conversation):
        seen.append(current().analysis_run_id)
        yield peer._sse('text',{'delta':'unchanged'})
        yield peer._sse('done',{'actionable':False})
    monkeypatch.setattr(peer,'_run_chat_stream_v3',original)
    async def run():
        a,b=peer.run_chat_stream_v3('a','stream-a'),peer.run_chat_stream_v3('b','stream-b')
        await anext(a);assert current() is None
        await anext(b);assert current() is None
        await asyncio.create_task(a.aclose());assert current() is None
        await b.aclose();assert current() is None
    asyncio.run(run())
    assert len(set(seen))==2
