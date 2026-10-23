"""Offline SDK wire contracts and model-policy regression; no external I/O."""
import json
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from app.llm_config import (LUNA, SOL, LLMCallError, chat_completion, model_for,
                            parse_response, request_options)


def ab_runner():
    # U1 deliberately adds backend/ to sys.path, which also owns a scripts
    # package. Load this CLI by file so test collection order cannot pick it.
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "scripts/gpt6_model_ab.py"
    spec = importlib.util.spec_from_file_location("wp_gpt6_ab_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sdk(handler):
    return openai.OpenAI(api_key="offline-only", http_client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("model,effort", [(SOL, "medium"), (LUNA, "none")])
def test_real_sdk_serializes_role_reasoning_and_completion_budget(model, effort):
    seen = []
    def receive(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id":"offline", "object":"chat.completion", "created":0,
            "model":model, "choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":"{}"}}]})
    with sdk(receive) as client:
        response = chat_completion(client, model=model, messages=[{"role":"user","content":"JSON only"}],
                                   max_tokens=200, temperature=.3, response_format={"type":"json_object"})
    assert response.choices[0].message.content == "{}"
    body = seen[0]
    assert body["reasoning_effort"] == effort and not body["store"]
    assert "max_tokens" not in body and body["max_completion_tokens"] == (8192 if model == SOL else 200)
    assert ("temperature" in body) == (model == LUNA)
    assert body["response_format"] == {"type":"json_object"}


def test_high_and_luna_low_omit_all_unsupported_sampling(monkeypatch):
    monkeypatch.setenv("WEALTHPILOT_LLM_MAIN_EFFORT", "high")
    for model in (SOL, LUNA):
        extra = {"reasoning_effort":"low"} if model == LUNA else {}
        options = request_options(model, temperature=.3, top_p=.5, logprobs=True, top_logprobs=1, **extra)
        assert options["reasoning_effort"] == ("high" if model == SOL else "low")
        assert not {"temperature", "top_p", "logprobs", "top_logprobs"}.intersection(options)
        assert options["max_completion_tokens"] >= 4096


@pytest.mark.parametrize("effort", ["none", "minimal", "invalid"])
def test_invalid_sol_effort_rejected_before_network(effort):
    with pytest.raises(LLMCallError, match="REASONING_EFFORT_UNSUPPORTED"):
        chat_completion(NS(), model=SOL, reasoning_effort=effort)


def test_rollback_requires_explicit_profile_and_cannot_be_normal_fallback(monkeypatch):
    monkeypatch.delenv("WEALTHPILOT_LLM_PROFILE", raising=False)
    assert model_for("main") == SOL and model_for("light") == LUNA
    with pytest.raises(LLMCallError, match="LEGACY_PROFILE_REQUIRED"):
        request_options("gpt-4.1", max_tokens=200)
    monkeypatch.setenv("WEALTHPILOT_LLM_MAIN_MODEL", "gpt-4.1")
    with pytest.raises(ValueError, match="MODEL_PROFILE_MISMATCH"):
        model_for("main")
    monkeypatch.delenv("WEALTHPILOT_LLM_MAIN_MODEL")
    monkeypatch.setenv("WEALTHPILOT_LLM_PROFILE", "legacy-4.1")
    assert model_for("main") == "gpt-4.1" and model_for("light") == "gpt-4.1-mini"
    assert request_options(model_for("main"), max_tokens=200)["max_tokens"] == 200


@pytest.mark.parametrize("status,kind,count", [(401,"authentication",1),(403,"model_access",1),
    (404,"model_access",1),(400,"invalid_request",1),(429,"rate_limit",2),(500,"provider",2)])
def test_safe_error_classes_and_bounded_sdk_retry(status, kind, count, caplog):
    calls = []
    def receive(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(status, json={"error":{"message":"PRIVATE_PAYLOAD_SENTINEL","type":"invalid_request_error"}})
    with sdk(receive) as client, pytest.raises(LLMCallError) as caught:
        chat_completion(client, model=LUNA, messages=[{"role":"user","content":"PRIVATE_PROMPT_SENTINEL"}])
    assert caught.value.kind == kind and calls == [LUNA] * count
    assert "PRIVATE_" not in str(caught.value) + caplog.text


@pytest.mark.parametrize("finish,content,refusal,kind", [("length","partial",None,"truncated"),
    ("stop","",None,"empty_output"),("stop",None,"cannot comply","refusal")])
def test_partial_or_refused_output_never_becomes_a_business_success(finish,content,refusal,kind):
    result = NS(choices=[NS(finish_reason=finish, message=NS(content=content,refusal=refusal))])
    client = NS(chat=NS(completions=NS(create=lambda **kwargs:result)))
    with pytest.raises(LLMCallError) as caught:
        chat_completion(client, model=LUNA)
    assert caught.value.kind == kind


def test_responses_strict_schema_preserved_with_reasoning_budget():
    from pydantic import BaseModel
    class Extracted(BaseModel):
        asset: str
    seen = []
    def receive(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id":"resp_fixture", "object":"response", "created_at":0,
            "model":LUNA, "status":"completed", "output":[{"id":"msg_fixture","type":"message", "role":"assistant",
            "status":"completed", "content":[{"type":"output_text","text":"{\"asset\":\"LI\"}","annotations":[]}]}]})
    with sdk(receive) as client:
        result = parse_response(client, model=LUNA, reasoning_effort="low", max_tokens=2000,
                                input="Extract asset as JSON: LI", text_format=Extracted)
    assert result.output_parsed.asset == "LI"
    assert seen[0]["reasoning"] == {"effort":"low"}
    assert seen[0]["text"]["format"]["strict"] is True
    assert seen[0]["max_output_tokens"] >= 4096


def test_ab_budget_bounds_every_attempt_and_forbids_tools_and_other_endpoints():
    Budget = ab_runner().Budget
    budget = Budget(calls=1,dollars=.01)
    request = httpx.Request("POST","https://api.openai.com/v1/chat/completions",
                           json={"model":LUNA,"max_completion_tokens":100,"messages":[]})
    budget.before_send(request)
    with pytest.raises(RuntimeError,match="AB_BUDGET_EXHAUSTED"):
        budget.before_send(request)
    assert len(budget.attempts) == 1 and budget.reserved <= .01
    assert budget.exhausted
    with pytest.raises(RuntimeError,match="AB_ENDPOINT_NOT_ALLOWED"):
        Budget().before_send(httpx.Request("POST","https://example.com/v1/chat/completions",json={}))
    with pytest.raises(RuntimeError,match="AB_UNBOUNDED_REQUEST"):
        Budget().before_send(httpx.Request("POST","https://api.openai.com/v1/chat/completions",
            json={"model":LUNA,"max_completion_tokens":100,"tools":[{"type":"web_search"}]}))
    with pytest.raises(RuntimeError,match="AB_BUDGET_EXHAUSTED"):
        Budget(dollars=.001).before_send(httpx.Request("POST","https://api.openai.com/v1/chat/completions",
            json={"model":SOL,"max_completion_tokens":8192}))


def test_ab_freezes_real_provider_quality_and_never_manufactures_operating_evidence():
    assessment = ab_runner().assessment
    for symbol in ("LI:US","PDD:US","3690:HK"):
        value = assessment(symbol)
        assert not value.get("verified_field_records")
        assert all(not value["qualification"][level]["allowed"] for level in ("L2","L3","L4"))
        assert not assessment(symbol,True)["records"]


@pytest.mark.parametrize("status,parsed,blocks,kind", [
    ("incomplete",None,[],"truncated"),
    ("completed",None,[NS(content=[NS(type="refusal")])],"refusal"),
    ("completed",None,[],"empty_output"),
])
def test_responses_non_success_cannot_become_a_parsed_trade_intent(status,parsed,blocks,kind):
    value = NS(status=status,output_parsed=parsed,output=blocks)
    client = NS(responses=NS(parse=lambda **kwargs:value))
    with pytest.raises(LLMCallError) as caught:
        parse_response(client,model=LUNA)
    assert caught.value.kind == kind


def test_different_returned_model_cannot_masquerade_as_upgrade():
    value = NS(model="gpt-4.1",choices=[NS(message=NS(content="{}"))])
    client = NS(chat=NS(completions=NS(create=lambda **kwargs:value)))
    with pytest.raises(LLMCallError,match="MODEL_MISMATCH"):
        chat_completion(client,model=SOL)


@pytest.mark.parametrize("kind", ["timeout","model_access","quota","invalid_request","truncated"])
def test_original_position_and_portfolio_fallbacks_handle_adapter_errors(monkeypatch,kind):
    from decision_engine import llm_engine as engine
    from app.llm_config import failure_message
    def unavailable(*args,**kwargs):
        raise LLMCallError(kind)
    monkeypatch.setattr(engine,"_build_payload",lambda *a,**kw:{})
    monkeypatch.setattr(engine,"build_decision_context",lambda *a,**kw:None)
    monkeypatch.setattr(engine,"format_context_prompt",lambda *a,**kw:"")
    monkeypatch.setattr(engine,"_get_client",unavailable)
    loaded=NS(retrieved_principles=[],raw_portfolio=None)
    result=engine.reason("fixture",loaded,NS(),NS(),NS())
    assert result.decision == "HOLD" and result.error == failure_message(LLMCallError(kind))
    assert "未知错误" not in result.error and "LLM_" not in result.error
    result=engine._call_generic_llm("PortfolioReview","fixture",{})
    assert result.raw_payload == {} and result.error == failure_message(LLMCallError(kind))


def test_ab_exhaustion_survives_shared_error_sanitization():
    module=ab_runner()
    budget=module.Budget(dollars=.000001)
    def create(**kwargs):
        budget.before_send(httpx.Request("POST","https://api.openai.com/v1/chat/completions",json=kwargs))
        raise AssertionError("budget must block dispatch")
    client=NS(chat=NS(completions=NS(create=create)))
    row=module.run_case(client,budget,"LI:US","planning",LUNA)
    assert row["status"] == "unavailable" and row["error"] == "budget_exhausted"
    assert budget.exhausted and not budget.attempts


def test_ab_cli_stops_and_records_incomplete_instead_of_success(monkeypatch,tmp_path):
    module=ab_runner()
    output=tmp_path/"budget-report.json"
    monkeypatch.setenv("WP_MODEL_AB_ALLOW_LIVE","1")
    monkeypatch.setenv("WEALTHPILOT_OPENAI_API_KEY","offline-only")
    monkeypatch.setattr("sys.argv",["gpt6_model_ab.py","--live","--output",str(output)])
    calls=[]
    def exhausted(client,budget,symbol,stage,model,empty):
        calls.append(stage);budget.exhausted=True
        return dict(symbol=symbol,stage=stage,requested_model=model,status="unavailable",error="budget_exhausted")
    monkeypatch.setattr(module,"run_case",exhausted)
    module.main()
    report=json.loads(output.read_text())
    assert report["status"] == "INCOMPLETE_BUDGET_EXHAUSTED"
    assert report["http_attempts"] == 0 and len(report["rows"]) == 1 and len(calls) == 1


@pytest.mark.parametrize("policy,expected", [
    ({"WEALTHPILOT_LLM_PROFILE":"legacy-4.1"}, ("gpt-4.1","gpt-4.1-mini",None,None)),
    ({"WEALTHPILOT_LLM_MAIN_EFFORT":"high","WEALTHPILOT_LLM_LIGHT_EFFORT":"low"}, (SOL,LUNA,"high","low")),
])
def test_mcp_worker_policy_precedes_earlier_read_imports_without_trade_permissions(tmp_path,policy,expected):
    import os
    import subprocess
    import sys
    database=tmp_path/"placeholder.sqlite";database.write_text("")
    config=tmp_path/"operator-analysis.json"
    config.write_text(json.dumps(dict(policy,ENABLE_IBKR_LIVE_TRADING="true",BROKER_MODE="live")))
    # Stub only read/analysis owners. Exercise the real worker argument parsing,
    # startup, env-file filter, two-operation order and JSON-line transport.
    code='''
import sys,types
def models():
 from app.config import MODEL_MAIN,MODEL_LIGHT
 from app.llm_config import request_options
 import os
 return dict(models=[MODEL_MAIN,MODEL_LIGHT,request_options(MODEL_MAIN).get("reasoning_effort"),request_options(MODEL_LIGHT).get("reasoning_effort")],live=os.getenv("ENABLE_IBKR_LIVE_TRADING"),broker=os.getenv("BROKER_MODE"))
reader=types.ModuleType("pocs.poc02_wealth_mcp.reader")
reader.read_overview=lambda *a,**kw:models()
sys.modules[reader.__name__]=reader
analysis=types.ModuleType("pocs.chatgpt_skills.analysis")
async def analyze(*a,**kw):return models()
analysis.analyze=analyze
sys.modules[analysis.__name__]=analysis
from pocs.chatgpt_skills import worker
sys.argv=["worker","--frozen-database",sys.argv[1],"--analysis-env-file",sys.argv[2]]
worker.main()
'''
    env={k:os.environ[k] for k in ("PATH","HOME","TMPDIR") if k in os.environ}
    env.update(PYTHON_DOTENV_DISABLED="1",PUBLIC_DEMO_MODE="true",BROKER_MODE="mock",
               ENABLE_IBKR_LIVE_TRADING="false",WEALTHPILOT_DB_PATH=str(database),
               WEALTHPILOT_LLM_PROFILE="legacy-4.1",WEALTHPILOT_LLM_MAIN_MODEL=SOL,
               WEALTHPILOT_LLM_LIGHT_MODEL=LUNA,WEALTHPILOT_LLM_MAIN_EFFORT="none")
    payload='\n'.join(json.dumps(dict(operation=op,arguments={})) for op in ("get_wealth_overview","analyze_position"))+'\n'
    completed=subprocess.run([sys.executable,"-c",code,str(database),str(config)],input=payload,
        capture_output=True,text=True,check=True,env=env,cwd=Path(__file__).resolve().parents[1],timeout=30)
    results=[json.loads(line) for line in completed.stdout.splitlines()]
    assert len(results)==2 and all(row["ok"] for row in results),results
    for row in results:
        assert tuple(row["result"]["models"])==expected
        assert row["result"]["live"]=="false" and row["result"]["broker"]=="mock"


def test_mcp_runtime_key_refresh_cannot_mix_cached_models_with_a_new_profile(monkeypatch,tmp_path):
    import os
    from pocs.chatgpt_skills.worker import load_analysis_environment
    monkeypatch.setenv("WEALTHPILOT_LLM_PROFILE","legacy-4.1")
    monkeypatch.setenv("WEALTHPILOT_LLM_MAIN_EFFORT","low")
    monkeypatch.setenv("WEALTHPILOT_OPENAI_API_KEY","before")
    config=tmp_path/"operator.json"
    config.write_text(json.dumps({"WEALTHPILOT_LLM_PROFILE":"gpt6", "WEALTHPILOT_LLM_MAIN_EFFORT":"high",
                                 "WEALTHPILOT_OPENAI_API_KEY":"after"}))
    load_analysis_environment(config)
    assert os.environ["WEALTHPILOT_OPENAI_API_KEY"]=="after"
    assert os.environ["WEALTHPILOT_LLM_PROFILE"]=="legacy-4.1"
    assert os.environ["WEALTHPILOT_LLM_MAIN_EFFORT"]=="low"


def test_provider_only_file_preserves_parent_policy_and_rejects_invalid_policy_types(monkeypatch,tmp_path):
    import os
    from pocs.chatgpt_skills.worker import load_analysis_environment
    monkeypatch.setenv("WEALTHPILOT_LLM_PROFILE","legacy-4.1")
    config=tmp_path/"operator.json"
    config.write_text(json.dumps({"WEALTHPILOT_STAGE_TARGETS_PATH":"synthetic-not-opened"}))
    load_analysis_environment(config,model_policy_only=True)
    assert os.environ["WEALTHPILOT_LLM_PROFILE"]=="legacy-4.1"
    config.write_text(json.dumps({"WEALTHPILOT_LLM_PROFILE":123}))
    with pytest.raises(ValueError,match="ANALYSIS_MODEL_POLICY_INVALID"):
        load_analysis_environment(config,model_policy_only=True)


def test_normal_business_calls_have_no_gpt41_literal_or_direct_sdk_bypass():
    import ast
    root = Path(__file__).resolve().parents[1]
    for directory in ("app", "backend", "decision_engine", "intent_engine", "research_v2"):
        for path in (root / directory).rglob("*.py"):
            if path.name.startswith("test_") or "tests" in path.parts or path.name == "llm_config.py":
                continue
            tree = ast.parse(path.read_text())
            assert not any(isinstance(node, ast.Constant) and isinstance(node.value,str)
                and node.value.startswith("gpt-4.1") for node in ast.walk(tree)), str(path)
            # Existing web search and embeddings are separately audited provider tools.
            if path.name == "data_loader.py":
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr == "create":
                    assert not ast.unparse(node.func).endswith("chat.completions.create"), str(path)
