"""Cached Explain rechecks owner clocks without loading assets or calling providers."""
from copy import deepcopy
from datetime import timedelta
import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.services import decision_service, explain_policy
from tests.test_v42_evidence import AT, verified
from backend.services.market_data.evidence import assess


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    from app import asset_views
    clock = {"at": AT}
    monkeypatch.setattr(explain_policy, "_CONTEXTS", {})
    monkeypatch.setattr(decision_service, "_DECISION_STORE", {})
    monkeypatch.setattr(decision_service, "_ALLOC_EXPLAIN_STORE", {})
    monkeypatch.setattr(decision_service, "_now", lambda: clock["at"], raising=False)
    monkeypatch.setattr(explain_policy, "_now", lambda: clock["at"])
    monkeypatch.setattr(asset_views, "now", lambda: clock["at"])
    monkeypatch.setattr(decision_service, "save_conversation_turn", lambda *a, **kw: None)
    return clock


def view():
    return dict(portfolio_id=7,snapshot_id="asset-view:v1:58",captured_at=AT.isoformat(),
        context_current=True,data_quality=dict(issues=[],can_propose_rebalance=True,
            analysis_eligibility={"B":dict(allowed=True)}))


def owner():
    return assess(symbol="LI:US", at=AT, research=[verified("research", text="经营结构有待持续验证。")])


def projected(*, precise=True):
    return dict(decisionType="trim",professional_explanation=[{"text":"old-narrative-987654"}],
        analysisQualification={"L1":{"allowed":True},"L2":{"allowed":precise},"L3":{"allowed":precise}},
        exitPlan=dict(price_trigger={"observation_band":[987654]},tranches=[{"quantity":987654}]))


def cached(value=None):
    value = deepcopy(value or projected())
    return dict(decision_id="d",conversation_id="c",analysis_result=value,
        llm=dict(chat_answer="old-narrative-987654",structured_result=value,reasoning=["987654"],strategy=["987654"]))


def remember(*, assessments=None, asset_view="default", payload=None):
    explain_policy.remember("c","d",portfolio_id=7,assessments=[owner()] if assessments is None else assessments,
        projected=payload or projected(),asset_view=view() if asset_view=="default" else asset_view)


def assert_historical(result):
    assert result["analysisFreshness"]["status"] == "historical"
    assert result["actionable"] is False
    assert "987654" not in json.dumps(result)
    assert "quantity" not in json.dumps(result) and "price_trigger" not in json.dumps(result)
    assert not any(level["allowed"] for level in result["analysis_result"]["analysisQualification"].values())
    assert result["llm"]["reasoning"] == result["llm"]["risk"] == result["llm"]["strategy"] == []
    assert "历史分析记录" in result["llm"]["chat_answer"]


def test_current_report_keeps_original_projection_with_server_freshness(isolated):
    original = cached()
    remember()
    result=explain_policy.project("c","d",original)
    assert result["analysis_result"] == original["analysis_result"]
    assert result["analysisFreshness"]["status"] == "current"
    assert result["analysisFreshness"]["generated_at"] == AT.isoformat()
    assert "analysisFreshness" not in original


def test_expired_same_view_drops_all_old_numeric_and_narrative_fields(isolated):
    remember()
    isolated["at"] += timedelta(hours=1)
    result=explain_policy.project("c","d",cached())
    assert_historical(result)
    assert result["analysisSnapshot"] == "asset-view:v1:58"
    assert result["data"]["source_metadata"][0]["record_id"] == "fixture:research"


def test_l1_without_snapshot_rechecks_source_clocks(isolated):
    low=projected(precise=False)
    low.pop("exitPlan")
    remember(asset_view=None,payload=low)
    assert explain_policy.project("c","d",cached(low))["analysisFreshness"]["status"] == "current"
    isolated["at"] += timedelta(days=100)
    assert_historical(explain_policy.project("c","d",cached(low)))


def test_missing_same_view_cannot_retain_active_l2_l3(isolated):
    remember(asset_view=None)
    assert_historical(explain_policy.project("c","d",cached()))


def test_target_expiration_and_revocation_are_rechecked_without_assets(isolated,monkeypatch):
    import backend.services.stage_targets as targets
    low=projected(precise=False);low.pop("exitPlan")
    a=owner()
    target=dict(digest="server-confirmed",valid_until=(AT+timedelta(minutes=1)).isoformat())
    a["allocation_context"]={"confirmed_stage_targets":target}
    monkeypatch.setattr(targets,"resolve_trusted_targets",lambda *args,**kwargs:target)
    remember(asset_view=None,assessments=[a],payload=low)
    assert explain_policy.project("c","d",cached(low))["analysisFreshness"]["status"] == "current"
    monkeypatch.setattr(targets,"resolve_trusted_targets",lambda *args,**kwargs:None)
    assert_historical(explain_policy.project("c","d",cached(low)))
    monkeypatch.setattr(targets,"resolve_trusted_targets",lambda *args,**kwargs:target)
    isolated["at"] += timedelta(minutes=2)
    assert_historical(explain_policy.project("c","d",cached(low)))


def test_legacy_four_one_explain_is_unchanged_but_missing_four_two_context_is_historical():
    legacy={"decision_id":"legacy","llm":{"chat_answer":"original 4.1 report"}}
    assert explain_policy.project("c","legacy",legacy) is legacy
    assert_historical(explain_policy.project("c","d",cached()))


@pytest.mark.parametrize("snapshot",[False,True])
def test_alloc_store_get_explain_rechecks_and_clear_session_removes_context(isolated,snapshot):
    data=cached()
    if snapshot:
        data["analysisSnapshot"]="asset-view:v1:58"
    decision_service._ALLOC_EXPLAIN_STORE["c:d"]=data
    remember()
    assert decision_service.get_decision_explain("c","d")["analysisFreshness"]["status"]=="current"
    isolated["at"] += timedelta(hours=1)
    assert_historical(decision_service.get_decision_explain("c","d"))
    decision_service.clear_session("c")
    assert ("c","d") not in explain_policy._CONTEXTS


def test_original_single_store_attaches_owner_context_and_get_rechecks(isolated):
    from backend.agents.contracts import PlanningOutput, ExecutionOutput, ExpressionOutput
    from backend.services.decision_service_v3 import _write_stores_v3
    from decision_engine.llm_engine import LLMResult
    loaded=SimpleNamespace(target_position=None,positions=[],total_assets=1000,research=[],
        has_data_errors=False,retrieved_principles=[],retrieved_research_views=[])
    plan=PlanningOutput(route="position_single",portfolio_id=7,intent={"primary_intent":"PositionDecision"})
    executed=ExecutionOutput(loaded_data=loaded,position_evidence=owner())
    llm=LLMResult("SELL",["987654"],[],["987654"],chat_answer="987654",structured_result=projected())
    expressed=ExpressionOutput(llm_result=llm,structured_payload=projected(),chat_answer="987654")
    asyncio.run(_write_stores_v3("c","d",plan,executed,expressed,analysis_context=view()))
    assert ("c","d") in explain_policy._CONTEXTS
    assert decision_service.get_decision_explain("c","d")["analysisFreshness"]["status"]=="current"
    isolated["at"] += timedelta(hours=1)
    assert_historical(decision_service.get_decision_explain("c","d"))


def test_multi_projection_requires_all_owner_sources_to_remain_current(isolated):
    a,b=owner(),owner()
    b["symbol"]="PDD:US"
    b["records"][0]["symbol"]="PDD:US"
    b["records"][0]["values"]["symbol"]="PDD:US"
    payload={"mode":"multi_snapshot_analysis","items":[projected(),projected()]}
    remember(assessments=[a,b],payload=payload)
    assert explain_policy.project("c","d",cached(payload))["analysisFreshness"]["status"]=="current"
    # A current source cannot rescue another symbol's expired owner evidence.
    context=explain_policy._CONTEXTS[("c","d")]
    context["assessments"][1]["records"][0]["values"]["published_at"]="2026-01-01"
    assert_historical(explain_policy.project("c","d",cached(payload)))


def test_original_portfolio_store_retains_owner_metadata_for_read_time_recheck(isolated):
    from backend.agents.contracts import PlanningOutput, ExecutionOutput, ExpressionOutput
    from backend.services.decision_service_v3 import _write_stores_v3
    from decision_engine.llm_engine import GenericLLMResult
    loaded=SimpleNamespace(target_position=None,positions=[],total_assets=1000,research=[],
        retrieved_principles=[],retrieved_research_views=[])
    plan=PlanningOutput(route="portfolio",portfolio_id=7,intent={"primary_intent":"PortfolioReview"})
    executed=ExecutionOutput(loaded_data=loaded)
    executed.portfolio_evidence=[owner()]
    payload=dict(mode="restricted_portfolio_analysis",professional_explanation=[],can_propose_rebalance=False)
    llm=GenericLLMResult("PortfolioReview","old-narrative-987654",raw_payload=payload)
    expressed=ExpressionOutput(llm_result=llm,structured_payload=payload,chat_answer="old-narrative-987654")
    asyncio.run(_write_stores_v3("c","d",plan,executed,expressed,analysis_context=view()))
    assert ("c","d") in explain_policy._CONTEXTS
    assert decision_service.get_decision_explain("c","d")["analysisFreshness"]["status"]=="current"
    isolated["at"] += timedelta(hours=1)
    assert_historical(decision_service.get_decision_explain("c","d"))


def test_snapshot_parent_stores_same_final_owner_context_before_explain(isolated,monkeypatch):
    from backend.services import snapshot_chat
    async def invoke(payload):
        return dict(snapshot_id=payload["snapshot_id"],portfolio_id=7,answer="old-narrative-987654",
            analysis_result=projected(),owner_assessments=[owner()],intent={"primary_intent":"PositionDecision"})
    monkeypatch.setattr(snapshot_chat,"_invoke_worker",invoke)
    monkeypatch.setattr(decision_service,"get_conversation_history",lambda *args:[])
    async def run():
        return [chunk async for chunk in snapshot_chat.stream_snapshot_chat("分析","snapshot-c",7,view())]
    chunks=asyncio.run(run())
    done=next(json.loads(chunk.splitlines()[1][6:]) for chunk in chunks if chunk.startswith("event: done"))
    key=("snapshot-c",done["decision_id"])
    assert key in explain_policy._CONTEXTS
    context=explain_policy._CONTEXTS[key]
    assert context["asset_view"]["snapshot_id"]==view()["snapshot_id"]
    assert context["assessments"][0]["symbol"]=="LI:US"
    assert decision_service.get_decision_explain(*key)["analysisFreshness"]["status"]=="current"
    isolated["at"] += timedelta(hours=1)
    assert_historical(decision_service.get_decision_explain(*key))


def test_original_multi_store_attaches_context_for_every_leg(isolated,monkeypatch):
    from tests.test_v42_multi_policy import test_multi_final_reports_cannot_escape_single_position_policy
    test_multi_final_reports_cannot_escape_single_position_policy(monkeypatch,False)
    results=decision_service._DECISION_STORE["isolated-multi"]
    assert len(results)==2
    for decision_id in results:
        assert ("isolated-multi",decision_id) in explain_policy._CONTEXTS
        # Existing multi test uses symbol-only LoadedData doubles; the real
        # serializer requires the full holding contract. Context/LLM are real.
        results[decision_id].data = None
    isolated["at"] += timedelta(hours=1)
    for decision_id in results:
        assert_historical(decision_service.get_decision_explain("isolated-multi",decision_id))


def test_repayment_scope_expiry_downgrades_even_while_investment_view_remains_current(isolated,monkeypatch):
    import backend.services.allocation_scenarios as scenarios
    def scope(value,legs,*,at=None):
        assert legs==[{"destination":"repay_cny_loan"}]
        checked=deepcopy(value)
        checked["data_quality"]["analysis_eligibility"]["B"]["allowed"]=at < AT+timedelta(seconds=30)
        return checked
    monkeypatch.setattr(scenarios,"qualification_view",scope)
    explain_policy.remember("c","d",portfolio_id=7,assessments=[owner()],projected=projected(),
        asset_view=view(),amount_scope={"allowed":True,"legs":[{"destination":"repay_cny_loan"}]})
    assert explain_policy.project("c","d",cached())["analysisFreshness"]["status"]=="current"
    isolated["at"] += timedelta(seconds=60)
    assert_historical(explain_policy.project("c","d",cached()))


def test_current_four_two_explain_cannot_return_excluded_loader_rag_text():
    payload=dict(mode="restricted_portfolio_analysis",professional_explanation=[])
    original=dict(decision_id="d",data=dict(research=["Seed: sell 987654"],
        retrieved_research_views=[{"content":"old research 987654"}],
        retrieved_principles=[{"content":"unverified risk number 987654"}]),
        signals={"fundamental":"old-research-positive-987654","position":"偏高"},
        llm={"chat_answer":"已有来源绑定的专业报告。"},analysis_result=payload)
    remember(payload=payload)
    result=explain_policy.project("c","d",original)
    assert result["analysisFreshness"]["status"]=="current"
    assert "987654" not in json.dumps(result)
    assert result["data"]==dict(research=[],retrieved_research_views=[],retrieved_principles=[])
    assert result["signals"] == {"fundamental":"N/A","position":"偏高"}
    assert "987654" in json.dumps(original)  # no cached owner input mutation
