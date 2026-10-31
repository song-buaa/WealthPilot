"""Explicit single-security research must keep the PEER route and intent aligned."""
import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from backend.agents.contracts import AgentTaskStatus, ExecutionOutput, PlanningOutput
from backend.agents.planning_agent import align_explicit_single_research
from backend.agents.expressing_agent import ExpressingAgent, _resolve_intent_type
from pocs.chatgpt_skills.analysis import analyze
from tests.test_analysis_skills import analysis_db, peer  # noqa: F401


def plan(intent="Education", route="position_single", asset="XPEV", confidence=.94):
    value = PlanningOutput(route=route, intent={"primary_intent": intent,
                                                "asset": asset, "confidence": confidence})
    value.mark_completed()
    return value


@pytest.mark.parametrize("symbol,question,expected", [
    ("XPEV:US", "我没有持仓，也没有买入意图，只研究小鹏汽车经营和财务。", "PositionDecision"),
    (None, "仅研究XPEV的经营与估值，不预设建仓。", "PositionDecision"),
    (None, "什么是市盈率？", "Education"),
])
def test_planning_alignment_is_narrow(symbol, question, expected):
    value = plan()
    align_explicit_single_research(value, symbol, question)
    assert value.intent["primary_intent"] == expected
    if expected == "PositionDecision":
        assert _resolve_intent_type(value) == "PositionDecision"
        assert value.planner_trace["intent_alignment"] == "explicit_single_research"


@pytest.mark.parametrize("route,asset,confidence,multi,clarify", [
    ("general", "XPEV", .94, [], False),
    ("position_multi", "XPEV", .94, ["XPEV", "LI"], False),
    ("portfolio", "XPEV", .94, [], False),
    ("position_single", None, .94, ["LI", "XPEV"], False),
    ("position_single", "XPEV", .49, [], False),
    ("position_single", "XPEV", .94, [], True),
])
def test_incompatible_plans_are_not_promoted(route, asset, confidence, multi, clarify):
    value = plan(route=route, asset=asset, confidence=confidence)
    value.multi_assets = multi
    value.needs_clarification = clarify
    align_explicit_single_research(value, "XPEV:US", "研究小鹏汽车财务")
    assert value.intent["primary_intent"] == "Education"


def test_unaligned_position_cannot_use_general_expression():
    assert _resolve_intent_type(plan()) == "RouteIntentMismatch"
    assert _resolve_intent_type(plan(route="general")) == "Education"


def test_unaligned_position_fails_before_general_model(monkeypatch):
    from decision_engine import llm_engine
    monkeypatch.setattr(llm_engine, "chat", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("generic education model was called")))
    async def run():
        agent = ExpressingAgent()
        chunks = [chunk async for chunk in agent.run_streaming(
            plan(), ExecutionOutput(), "仅研究XPEV经营")]
        return agent.last_output, chunks
    result, chunks = asyncio.run(run())
    assert result.status == AgentTaskStatus.FAILED
    assert chunks and "无法识别" in chunks[0]


def test_explicit_symbol_does_not_promote_another_company():
    value = plan(asset="LI")
    align_explicit_single_research(value, "XPEV:US", "只研究小鹏汽车财务")
    assert value.intent["primary_intent"] == "Education"


def test_explicit_symbol_supplies_missing_planner_asset():
    value = plan(asset=None)
    align_explicit_single_research(value, "XPEV:US", "仅研究小鹏汽车财务")
    assert value.intent["primary_intent"] == "PositionDecision"
    assert value.intent["asset"] == "XPEV:US"


@pytest.mark.parametrize("asset,question", [
    ("LI", "研究已持仓LI的长期经营风险"),
    ("PDD", "研究PDD逐步退出的条件"),
    ("XPEV", "明确评估买入XPEV并建仓"),
])
def test_existing_position_decision_intents_remain_unchanged(asset, question):
    value = plan(intent="PositionDecision", asset=asset)
    align_explicit_single_research(value, asset + ":US", question)
    assert value.intent["primary_intent"] == "PositionDecision"
    assert "intent_alignment" not in value.planner_trace
    assert _resolve_intent_type(value) == "PositionDecision"


def test_xpev_failed_production_pair_now_reaches_professional_path(analysis_db, peer, monkeypatch):
    """Reproduce Education + position_single, then traverse the original PEER adapter."""
    from backend.agents.planning_agent import PlanningAgent
    from decision_engine import data_loader
    from backend.agents import expressing_agent
    from backend.services.market_data import av_fundamentals_service
    from decision_engine import llm_engine
    from backend.core import demo_mode
    from research_v2 import official_disclosures, sec_disclosures
    from intent_engine import _llm_client

    monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    monkeypatch.setenv("AV_DEV_MOCK", "1")
    monkeypatch.setattr(demo_mode, "PUBLIC_DEMO_MODE", False)
    monkeypatch.setattr(official_disclosures, "collect", lambda *a, **k: ([], lambda *a, **k: None))
    monkeypatch.setattr(sec_disclosures, "source_diagnostics", lambda: [])
    monkeypatch.setattr(_llm_client, "get_client", lambda: object())
    monkeypatch.setattr(llm_engine, "chat", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("Education general chat must not run for XPEV research")))

    def wrong_intent(self, question, conversation, pid, history, positions):
        value = plan()
        value.portfolio_id = pid
        return value

    monkeypatch.setattr(PlanningAgent, "run", wrong_intent)
    monkeypatch.setattr(data_loader, "_try_load_new_entry", lambda asset, positions: {
        "virtual_position": data_loader.PositionInfo(
            name="XPEV", ticker="XPEV", symbol="XPEV:US", asset_class="equity",
            weight=0, market_value_cny=0, cost_price=0, current_price=0,
            profit_loss_rate=0, is_virtual=True),
        "av_fundamentals": None, "market_not_supported_message": None, "warning": None,
    })
    monkeypatch.setattr(av_fundamentals_service, "fetch_fundamentals", lambda *a, **k: None)
    monkeypatch.setattr(expressing_agent, "chat_completion", lambda *a, **k: NS(
        choices=[NS(message=NS(content=json.dumps({"chat_answer": "研究公司经营与风险。",
            "professional_explanation": []}, ensure_ascii=False)))]))
    result = asyncio.run(analyze(analysis_db, 7, "analyze_position", {
        "question": "我没有持仓，也没有买入意图，只研究小鹏汽车经营与财务。",
        "symbol": "XPEV:US"}, synthetic=True, async_l1=True))
    assert result["intent"]["primary_intent"] == "PositionDecision"
    assert result["intent"]["planner_route"] == "position_single"
    assert "ANALYSIS_ROUTE_MISMATCH" not in result["issues"]
    assert result["professional_analysis_complete"] is True
    assert result["stage_diagnostics"]["expressing"] == "completed"
    assert result["stage_diagnostics"]["reviewing"] == "completed"
    assert result["professional_result"]["analysis_result"]["decisionType"] == "watch"
    assert result["professional_result"]["analysis_result"]["exitPlan"]["tranches"] == []
    assert result["execution_authorized"] is False and result["order_created"] is False
    assert "买入意图" not in result["conclusion"] or "没有买入意图" in result["conclusion"]


def test_completed_stages_but_wrong_route_reports_qualification_failure(analysis_db, monkeypatch):
    from backend.services import decision_service_v3 as svc

    async def incompatible(*args, **kwargs):
        kwargs["stage_callback"](plan(), NS(position_evidence={"records": []}))
        kwargs["stage_callback"](plan(), NS(position_evidence={"records": []}), stage="expressing_complete")
        kwargs["stage_callback"](plan(), NS(position_evidence={"records": []}), stage="reviewing_complete")
        yield svc._sse("intent", dict(primary_intent="Education", planner_route="position_single"))
        yield svc._sse("text", dict(delta="Untrusted generic answer"))
        yield svc._sse("done", dict(conclusion_level="general_chat"))

    monkeypatch.setattr(svc, "run_chat_stream_v3", incompatible)
    result = asyncio.run(analyze(analysis_db, 7, "analyze_position", {
        "question": "仅研究XPEV", "symbol": "XPEV:US"}, synthetic=True, async_l1=True))
    assert result["status"] == "unavailable"
    assert result["missing_stages"] == ["professional_result_qualification"]
    assert result["stage_diagnostics"]["professional_result"] == "rejected"
    assert result["stage_diagnostics"]["expressing"] == "completed"
    assert "Untrusted" not in json.dumps(result)


@pytest.mark.parametrize("error,expected", [
    ("planning_failed", ("failed", "not_started", "not_started")),
    ("execution_failed", ("completed", "failed", "not_started")),
    ("expression_failed", ("completed", "completed", "failed")),
])
def test_failed_stage_is_distinguished_from_missing_receipt(analysis_db, monkeypatch, error, expected):
    from backend.services import decision_service_v3 as svc

    async def stopped(*args, **kwargs):
        yield svc._sse("stage", dict(stage="intent"))
        if error != "planning_failed":
            yield svc._sse("intent", dict(primary_intent="PositionDecision", planner_route="position_single"))
            yield svc._sse("stage", dict(stage="loading"))
        if error == "expression_failed":
            kwargs["stage_callback"](plan("PositionDecision"), NS(position_evidence={}))
            yield svc._sse("stage", dict(stage="reasoning"))
        yield svc._sse("error", dict(code=error))

    monkeypatch.setattr(svc, "run_chat_stream_v3", stopped)
    result = asyncio.run(analyze(analysis_db, 7, "analyze_position", {
        "question": "研究XPEV", "symbol": "XPEV:US"}, synthetic=True, async_l1=True))
    states = result["stage_diagnostics"]
    assert (states["planning"], states["executing"], states["expressing"]) == expected
    assert result["status"] == "unavailable"
    assert result["professional_result"] == {}
    assert result["execution_authorized"] is False and result["order_created"] is False


def test_no_stage_observation_is_not_reported_as_completion(analysis_db, monkeypatch):
    from backend.services import decision_service_v3 as svc

    async def stopped(*args, **kwargs):
        yield svc._sse("error", dict(code="internal_error"))

    monkeypatch.setattr(svc, "run_chat_stream_v3", stopped)
    result = asyncio.run(analyze(analysis_db, 7, "analyze_position", {
        "question": "研究XPEV", "symbol": "XPEV:US"}, synthetic=True, async_l1=True))
    assert result["stage_diagnostics"]["planning"] == "not_started"
    assert result["missing_stages"] == ["planning_not_started"]
