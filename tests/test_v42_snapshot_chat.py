"""Web snapshots use a child-only immutable DB and the existing final policies."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from backend.services import snapshot_chat


@pytest.fixture(autouse=True)
def isolated_conversation_io(monkeypatch):
    from backend.services import decision_service
    monkeypatch.setattr(decision_service, "get_conversation_history", lambda *args: [])
    monkeypatch.setattr(decision_service, "save_conversation_turn", lambda *args, **kwargs: None)


def event(kind, data):
    return snapshot_chat._sse(kind, data)


def view():
    return dict(schema_version="wealthpilot.asset-view.v1", pipeline="u1.1", state="ready",
        portfolio_id=1, snapshot_id="asset-view:v1:1", captured_at=datetime.now(timezone.utc).isoformat(),
        data_quality={"issues":[], "can_propose_rebalance":False}, context_current=True)


def collect(stream):
    async def run():
        return [chunk async for chunk in stream]
    return asyncio.run(run())


def parse(chunks):
    return [(chunk.splitlines()[0][7:], json.loads(chunk.splitlines()[1][6:])) for chunk in chunks]


def test_parent_uses_server_paths_and_only_safe_explain_projection(monkeypatch, tmp_path):
    from app import database
    from backend.services import decision_service
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "server-private.sqlite"))
    monkeypatch.setenv("WEALTHPILOT_SQLITE_CONTRACT", str(tmp_path / "server-contract.json"))
    calls = []
    async def invoke(payload):
        calls.append(payload)
        return dict(snapshot_id=payload["snapshot_id"], portfolio_id=1, answer="已有合格证据支持的专业推论。",
                    analysis_result={"decisionType":"hold", "analysisQualification":{"L3":{"allowed":False}}},
                    owner_assessments=[], intent={"primary_intent":"PositionDecision"})
    monkeypatch.setattr(snapshot_chat, "_invoke_worker", invoke)
    chunks = parse(collect(snapshot_chat.stream_snapshot_chat("分析", "test-snapshot-parent", 1, view())))
    assert len(calls) == 1
    assert calls[0]["database_path"] == database.DB_PATH
    assert calls[0]["contract"] == str(tmp_path / "server-contract.json")
    done = next(data for kind, data in chunks if kind == "done")
    assert done["actionable"] is False
    assert done["analysisSnapshot"] == "asset-view:v1:1"
    stored = decision_service.get_decision_explain("test-snapshot-parent", done["decision_id"])
    assert stored["analysis_result"] == done["decisionResult"]
    assert stored["llm"]["raw_output"] == ""
    assert stored["data"] == {"snapshot_id":"asset-view:v1:1", "portfolio_id":1}
    decision_service.clear_session("test-snapshot-parent")


def test_no_worker_for_wrong_portfolio_or_expired_view(monkeypatch):
    async def forbidden(payload):
        pytest.fail("Invalid views must not run the worker")
    monkeypatch.setattr(snapshot_chat, "_invoke_worker", forbidden)
    wrong = parse(collect(snapshot_chat.stream_snapshot_chat("分析", "test", 2, view())))
    assert [kind for kind, _ in wrong] == ["error"]
    expired = view()
    expired["captured_at"] = "2000-01-01T00:00:00+00:00"
    assert parse(collect(snapshot_chat.stream_snapshot_chat("分析", "test", 1, expired)))[0][0] == "error"


def test_parent_drops_worker_result_if_snapshot_expires_or_response_changes_scope(monkeypatch):
    source = view()
    async def wrong(payload):
        return dict(snapshot_id="asset-view:v1:2", portfolio_id=1, answer="unsafe", analysis_result={})
    monkeypatch.setattr(snapshot_chat, "_invoke_worker", wrong)
    result = parse(collect(snapshot_chat.stream_snapshot_chat("分析", "test", 1, source)))
    assert not any(kind in {"text", "done"} for kind, _ in result)
    async def expired(payload):
        source["captured_at"] = "2000-01-01T00:00:00+00:00"
        return dict(snapshot_id=source["snapshot_id"], portfolio_id=1, answer="stale", analysis_result={})
    monkeypatch.setattr(snapshot_chat, "_invoke_worker", expired)
    result = parse(collect(snapshot_chat.stream_snapshot_chat("分析", "test", 1, source)))
    assert not any(kind in {"text", "done"} for kind, _ in result)


def test_worker_process_environment_and_timeout_cleanup(monkeypatch, tmp_path):
    calls = []
    class Process:
        returncode = None
        killed = False
        async def communicate(self, payload):
            raise asyncio.TimeoutError()
        def kill(self):
            self.killed = True
        async def wait(self):
            self.returncode = -9
    child = Process()
    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return child
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("ENABLE_IBKR_LIVE_TRADING", "true")
    monkeypatch.setenv("BROKER_MODE", "live")
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(snapshot_chat._invoke_worker({"database_path":str(tmp_path / "private.sqlite")}))
    assert child.killed
    env = calls[0][1]["env"]
    assert env["ENABLE_IBKR_LIVE_TRADING"] == "false"
    assert env["IBKR_READ_ONLY_MODE"] == "true"
    assert env["BROKER_MODE"] == "mock"
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert "backend.main" not in " ".join(calls[0][0])


def test_observation_crosses_worker_done_explain_and_existing_history_channel(monkeypatch):
    from app.llm_observation import AnalysisObservation
    from backend.services import decision_service
    saved=[]
    async def invoke(payload):
        metadata=AnalysisObservation(payload['analysis_run_id']).snapshot('completed')
        metadata['raw_output']='PRIVATE_RAW_OBSERVATION'
        return dict(snapshot_id=payload['snapshot_id'],portfolio_id=1,answer='原有证据过滤解释',
            analysis_result={'actionable':False,'analysisQualification':{'L3':{'allowed':False}}},
            owner_assessments=[],llm_observation=metadata)
    monkeypatch.setattr(snapshot_chat,'_invoke_worker',invoke)
    monkeypatch.setattr(decision_service,'save_conversation_turn',lambda *args,**kwargs:saved.append(kwargs))
    events=parse(collect(snapshot_chat.stream_snapshot_chat('分析','observed-web',1,view())))
    done=next(data for kind,data in events if kind=='done')
    observation=done['llm_observation']
    assert len(observation['analysis_run_id'])==32
    assert 'PRIVATE_RAW' not in json.dumps(events)
    explained=decision_service.get_decision_explain('observed-web',done['decision_id'])
    assert explained['llm_observation']==observation==saved[0]['assistant_metadata']['llm_observation']
    assert not done['actionable'] and not done['decisionResult']['analysisQualification']['L3']['allowed']
    decision_service.clear_session('observed-web')


def test_web_timeout_metadata_never_claims_no_model_requests(monkeypatch):
    async def invoke(payload):raise asyncio.TimeoutError()
    monkeypatch.setattr(snapshot_chat,'_invoke_worker',invoke)
    events=parse(collect(snapshot_chat.stream_snapshot_chat('分析','timeout-web',1,view())))
    error=next(data for kind,data in events if kind=='error')
    observation=error['llm_observation']
    assert observation['status']=='timeout' and observation['collection_status']=='UNAVAILABLE'
    assert observation['call_count'] is None and observation['totals']['estimated_cost_usd'] is None
    assert not any(kind in {'text','done'} for kind,_ in events)


def test_real_frozen_read_isolated_from_newer_live_rows_and_cannot_write(monkeypatch, tmp_path):
    from app import database
    from pocs.chatgpt_skills import analysis
    from backend.services import decision_service_v3, position_analysis
    from backend.services.market_data import output_policy
    from sqlalchemy import text
    from sqlalchemy.exc import DatabaseError
    root = tmp_path / "cache"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("WEALTHPILOT_ANALYSIS_CACHE_DIR", str(root))
    cache = root / ("a" * 32 + ".sqlite")
    with sqlite3.connect(cache) as db:
        db.execute("CREATE TABLE portfolios (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO portfolios VALUES(1,'frozen-at-capture')")
    cache.chmod(0o600)
    raw_view = view()
    raw_view["internal"] = dict(cache_key="a" * 32, database_sha256=hashlib.sha256(cache.read_bytes()).hexdigest())
    live = tmp_path / "live.sqlite"
    with sqlite3.connect(live) as db:
        db.execute("CREATE TABLE portfolios (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO portfolios VALUES(1,'newer-live-row')")
        db.execute("CREATE TABLE wealth_snapshots (id INTEGER, portfolio_id INTEGER, analysis_request_id TEXT, payload_json TEXT)")
        db.execute("INSERT INTO wealth_snapshots VALUES(1,1,'prepared',?)", (json.dumps(raw_view),))
    before = live.read_bytes()
    actual_readonly = analysis.readonly_business_database
    @contextmanager
    def restored_boundary(path, **kwargs):
        # Production child holds RO until exit; the in-process test restores its
        # own engine after checking the exact same SQLite/authorizer behavior.
        with actual_readonly(path, worker_process=False):
            yield
    monkeypatch.setattr(analysis, "readonly_business_database", restored_boundary)
    original_engine = database._engine
    observed = []
    async def original_peer(question, conversation, pid, **kwargs):
        assert kwargs["analysis_only"] is True
        assert kwargs["conversation_history"] == [{"role":"user", "content":"先前讨论的阶段目标"}]
        assert kwargs["analysis_context"]["snapshot_id"] == raw_view["snapshot_id"]
        with database.get_session() as session:
            observed.append(session.execute(text("SELECT name FROM portfolios WHERE id=1")).scalar())
            with pytest.raises(DatabaseError):
                session.execute(text("UPDATE portfolios SET name='forbidden' WHERE id=1"))
        plan = SimpleNamespace(route="position_single", intent={"primary_intent":"PositionDecision"})
        executed = SimpleNamespace(loaded_data=SimpleNamespace())
        expressed = SimpleNamespace(chat_answer="UNFILTERED_LLM_987654", structured_payload={"quantity":987654})
        kwargs["result_callback"](plan, executed, expressed, None)
        yield event("text", {"delta":"UNFILTERED_LLM_987654"})
        yield event("done", {"decision_id":"original"})
    monkeypatch.setattr(decision_service_v3, "run_chat_stream_v3", original_peer)
    monkeypatch.setattr(position_analysis, "prepare", lambda *a, **kw: {"snapshot_id":kw["asset_view"]["snapshot_id"]})
    def enforce(expressed, assessment):
        expressed.chat_answer = "Filtered original PEER explanation"
        expressed.structured_payload = {"snapshot_id":assessment["snapshot_id"], "quantity":None}
    monkeypatch.setattr(output_policy, "enforce", enforce)
    payload = dict(database_path=str(live), contract=None, portfolio_id=1, snapshot_id=raw_view["snapshot_id"],
        message="分析持仓", conversation_id="isolated", conversation_history=[{"role":"user", "content":"先前讨论的阶段目标"}], options={})
    payload['analysis_run_id']='b'*32
    result = asyncio.run(snapshot_chat._run_frozen_chat(payload))
    assert result['llm_observation']['analysis_run_id']=='b'*32
    assert observed == ["frozen-at-capture"]
    assert result["answer"] == "Filtered original PEER explanation"
    assert "987654" not in json.dumps(result)
    assert live.read_bytes() == before
    assert database._engine is original_engine
    payload["portfolio_id"] = 2
    with pytest.raises(ValueError, match="ASSET_VIEW_NOT_FOUND"):
        asyncio.run(snapshot_chat._run_frozen_chat(payload))


def test_api_routes_snapshot_to_child_and_normal_chat_to_original(monkeypatch):
    from backend.api import decision
    from app import asset_views
    calls = []
    raw_view = view()
    monkeypatch.setattr(asset_views, "load_view", lambda *args: raw_view)
    async def child(*args, **kwargs):
        calls.append("snapshot")
        yield event("done", {"actionable":False})
    async def original(*args, **kwargs):
        calls.append("original")
        yield event("done", {"actionable":False})
    monkeypatch.setattr(snapshot_chat, "stream_snapshot_chat", child)
    monkeypatch.setattr(decision.svc, "run_chat_stream", original)
    async def run():
        snapshot = await decision.chat(decision.ChatRequest(message="分析", conversation_id="a", snapshot_id=raw_view["snapshot_id"]))
        _ = [chunk async for chunk in snapshot.body_iterator]
        normal = await decision.chat(decision.ChatRequest(message="分析", conversation_id="a"))
        _ = [chunk async for chunk in normal.body_iterator]
    asyncio.run(run())
    assert calls == ["snapshot", "original"]


def test_actual_one_shot_worker_fails_closed_without_creating_database(tmp_path, monkeypatch):
    monkeypatch.delenv("WEALTHPILOT_SQLITE_CONTRACT", raising=False)
    missing = tmp_path / "missing-private.sqlite"
    payload = dict(database_path=str(missing), contract=None, portfolio_id=1,
        snapshot_id="asset-view:v1:1", message="分析", conversation_id="isolated-child", conversation_history=[], options={})
    with pytest.raises(ValueError, match="SNAPSHOT_ANALYSIS_UNAVAILABLE"):
        asyncio.run(snapshot_chat._invoke_worker(payload))
    assert not missing.exists()


def test_parent_preserves_original_precision_veto(monkeypatch):
    from app import asset_views
    from copy import deepcopy
    source = view()
    async def invoke(payload):
        assert source["data_quality"]["can_propose_rebalance"] is False
        return dict(snapshot_id=source["snapshot_id"], portfolio_id=1, answer="safe", analysis_result={}, owner_assessments=[])
    monkeypatch.setattr(snapshot_chat, "_invoke_worker", invoke)
    def attempted_upgrade(raw):
        output = deepcopy(raw)
        output["data_quality"]["can_propose_rebalance"] = True
        return output
    monkeypatch.setattr(asset_views, "public_view", attempted_upgrade)
    results = parse(collect(snapshot_chat.stream_snapshot_chat("分析", "test-veto", 1, source)))
    assert any(kind == "done" for kind, _ in results)
    assert source["data_quality"]["can_propose_rebalance"] is False


def test_parent_rejects_lost_amount_qualification(monkeypatch):
    from backend.services import decision_service_v3
    from copy import deepcopy
    source = view()
    source["data_quality"]["analysis_eligibility"] = {"B":{"allowed":True}}
    calls = []
    def clock_requalification(raw):
        output = deepcopy(raw)
        output["data_quality"]["analysis_eligibility"]["B"]["allowed"] = not calls
        calls.append(True)
        return output
    monkeypatch.setattr(decision_service_v3, "_final_analysis_view", clock_requalification)
    async def invoke(payload):
        return dict(snapshot_id=source["snapshot_id"], portfolio_id=1, answer="stale amount", analysis_result={}, owner_assessments=[])
    monkeypatch.setattr(snapshot_chat, "_invoke_worker", invoke)
    results = parse(collect(snapshot_chat.stream_snapshot_chat("分析", "test-expired-b", 1, source)))
    assert not any(kind in {"done", "text"} for kind, _ in results)


def test_ipc_guard_rechecks_research_technical_and_fiscal_field_clocks():
    from datetime import timedelta
    from backend.services.market_data.evidence import record
    from tests.test_v42_evidence import AT, verified, trusted_technical
    research = record("research", verified("research"), at=AT, symbol="LI:US")
    assert research["quality"] == "valid"
    assessment = {"symbol":"LI:US", "records":[research]}
    assert snapshot_chat._owner_evidence_current([assessment], 1, at=AT)
    assert not snapshot_chat._owner_evidence_current([assessment], 1, at=AT+timedelta(days=91))
    technical = record("technical", trusted_technical("LI:US"), at=AT, symbol="LI:US")
    assert technical["quality"] == "valid"
    assert snapshot_chat._owner_evidence_current([{"symbol":"LI:US", "records":[technical]}], 1, at=AT)
    assert not snapshot_chat._owner_evidence_current([{"symbol":"LI:US", "records":[technical]}], 1, at=AT+timedelta(days=8))
    from tests.test_v42_fundamental_clocks import statements, project
    raw, _ = project(*statements(published=True))
    fund = record("fundamentals", raw, at=AT, symbol="LI:US")
    assert fund["field_quality"]["free_cash_flow_ttm"]["quality"] == "valid"
    assert snapshot_chat._owner_evidence_current([{"symbol":"LI:US", "records":[fund]}], 1, at=AT)
    assert not snapshot_chat._owner_evidence_current([{"symbol":"LI:US", "records":[fund]}], 1, at=AT+timedelta(days=40))


def test_parent_drops_newly_expired_owner_citation(monkeypatch):
    from backend.services.market_data.evidence import record
    from tests.test_v42_evidence import AT, verified
    source = view()
    research = record("research", verified("research"), at=AT, symbol="LI:US")
    research["values"]["published_at"] = "2000-01-01"
    async def invoke(payload):
        return dict(snapshot_id=source["snapshot_id"], portfolio_id=1, answer="old inference", analysis_result={},
            owner_assessments=[{"symbol":"LI:US", "records":[research]}])
    monkeypatch.setattr(snapshot_chat, "_invoke_worker", invoke)
    results = parse(collect(snapshot_chat.stream_snapshot_chat("分析", "test-expired-cite", 1, source)))
    assert not any(kind in {"done", "text"} for kind, _ in results)


def test_ipc_projection_rechecks_auto_repayment_scope_and_portfolio_target(monkeypatch):
    from backend.services import allocation_scenarios
    observed = []
    def requalify(raw, legs):
        observed.extend(legs)
        return {"context_current":True, "data_quality":{"analysis_eligibility":{"B":{"allowed":False}}}}
    monkeypatch.setattr(allocation_scenarios, "qualification_view", requalify)
    legs = [{"destination":"repay_cny_loan"}]
    projected = {"exitPlan":{"account_alternatives":[{"legs":legs, "scenario":{"mode":"qualified_static_scenario"}}]}}
    assert not snapshot_chat._projection_current(projected, view(), {}, 1)
    assert observed == legs
    observed.clear()
    assert not snapshot_chat._projection_current({}, view(), {}, 1, amount_scope={"legs":legs, "allowed":True})
    assert observed == legs
    observed.clear()
    assert snapshot_chat._projection_current({}, view(), {}, 1, amount_scope={"legs":legs, "allowed":False})
    assert observed == []
    expired = {"allocation_context":{"proposed_stage_targets":{"valid_until":"2000-01-01T00:00:00Z"}}}
    assert not snapshot_chat._projection_current(expired, view(), {}, 1)


def test_followup_history_uses_original_store_and_only_filtered_answer_is_saved(monkeypatch):
    from backend.services import decision_service
    histories = [{"role":"user", "content":"先前讨论标的经营变化"},
                 {"role":"assistant", "content":"先前经过证据过滤的专业说明"}]
    read_calls, saved, child_requests = [], [], []
    def history(conversation_id):
        read_calls.append(conversation_id)
        return list(histories)
    def save(*args, **kwargs):
        saved.append((args, kwargs))
        histories.extend([{"role":"user", "content":args[1]}, {"role":"assistant", "content":args[2]}])
        return 42
    async def child(payload):
        child_requests.append(payload)
        return dict(snapshot_id=payload["snapshot_id"], portfolio_id=1,
            answer="本轮已过滤且有证据引用的专业解释", analysis_result={"professional_explanation":[]},
            owner_assessments=[], intent={"primary_intent":"PositionDecision"}, raw_output="UNSAFE_RAW_987654")
    monkeypatch.setattr(decision_service, "get_conversation_history", history)
    monkeypatch.setattr(decision_service, "save_conversation_turn", save)
    monkeypatch.setattr(snapshot_chat, "_invoke_worker", child)
    first = parse(collect(snapshot_chat.stream_snapshot_chat("继续解释经营变化", "snapshot-followup", 1, view())))
    _ = collect(snapshot_chat.stream_snapshot_chat("再解释风险", "snapshot-followup", 1, view()))
    assert read_calls == ["snapshot-followup", "snapshot-followup"]
    assert len(child_requests[0]["conversation_history"]) == 2
    assert len(child_requests[1]["conversation_history"]) == 4
    assert all(args[2] == "本轮已过滤且有证据引用的专业解释" for args, _ in saved)
    assert "987654" not in json.dumps(saved)
    assert saved[0][1]["assistant_metadata"] == {"analysis_snapshot":"asset-view:v1:1", "analysis_only":True}
    done = next(data for kind, data in first if kind == "done")
    assert done["assistant_message_id"] == 42
    decision_service.clear_session("snapshot-followup")


def test_chat_save_failure_does_not_expose_raw_or_break_filtered_answer(monkeypatch):
    from backend.services import decision_service
    def save(*args, **kwargs):
        raise RuntimeError("isolated fake storage failure")
    async def child(payload):
        return dict(snapshot_id=payload["snapshot_id"], portfolio_id=1, answer="filtered professional explanation",
            analysis_result={}, owner_assessments=[])
    monkeypatch.setattr(decision_service, "save_conversation_turn", save)
    monkeypatch.setattr(snapshot_chat, "_invoke_worker", child)
    result = parse(collect(snapshot_chat.stream_snapshot_chat("分析", "save-failure", 1, view())))
    assert next(data for kind, data in result if kind == "text")["delta"] == "filtered professional explanation"
    assert next(data for kind, data in result if kind == "done")["assistant_message_id"] is None
    decision_service.clear_session("save-failure")
