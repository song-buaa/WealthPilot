"""Offline Web multi-position final-output qualification regressions."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.agents.contracts import ExpressionOutput, PlanningOutput
from backend.services import decision_service_v3 as service
from decision_engine.llm_engine import LLMResult
from tests.test_v42_evidence import AT
from tests.test_v42_exit_plan import setup_case


def test_final_clock_recheck_cannot_upgrade_original_precision_veto(monkeypatch):
    from app import asset_views
    view = {"captured_at":AT.isoformat(),"data_quality":{"can_propose_rebalance":False}}
    def upgrade(value):
        result = deepcopy(value)
        result["data_quality"]["can_propose_rebalance"] = True
        return result
    monkeypatch.setattr(asset_views,"public_view",upgrade)
    assert service._final_analysis_view(view)["data_quality"]["can_propose_rebalance"] is False
    assert view["data_quality"]["can_propose_rebalance"] is False


@pytest.mark.parametrize("with_snapshot", [False, True])
def test_portfolio_policy_removes_nested_model_trade_instructions(with_snapshot):
    from backend.services.market_data.output_policy import enforce_portfolio
    from decision_engine.llm_engine import GenericLLMResult
    from tests.test_v42_scenarios import view
    output=ExpressionOutput(structured_payload={"nested":{"quantity":987654}},
        llm_result=GenericLLMResult("PortfolioReview","SELL 987654",raw_payload={"quantity":987654}))
    enforce_portfolio(output,asset_view=view() if with_snapshot else None)
    assert "987654" not in str(output)
    assert output.structured_payload["can_propose_rebalance"] is False
    assert output.actionable is False


def test_portfolio_policy_keeps_formal_discipline_owner_and_target_conflicts():
    from backend.services.market_data.output_policy import enforce_portfolio
    from tests.test_v42_scenarios import target
    output=ExpressionOutput()
    proposed=target()
    # Keep the synthetic proposal current independently from the test wall clock.
    from datetime import datetime, timedelta, timezone
    now=datetime.now(timezone.utc)
    proposed.update(confirmed_at=(now-timedelta(days=1)).isoformat(),valid_until=(now+timedelta(days=1)).isoformat())
    loaded=SimpleNamespace(rules={"min_cash_pct":.05},rules_provenance={"basis":"configured_rule_file","digest":"fixture"})
    enforce_portfolio(output,options={"target_versions":[proposed]},loaded=loaded)
    context=output.structured_payload["allocation_context"]
    assert context["formal_discipline"]==loaded.rules
    assert "TARGET_DISCIPLINE_CASH_CONFLICT" in context["warnings"]
    assert context["confirmed_stage_targets"] is None


@pytest.mark.parametrize("expire_at_final", [False, True])
def test_multi_final_reports_cannot_escape_single_position_policy(monkeypatch, expire_at_final):
    from app import asset_views
    from backend.services import decision_service, position_analysis
    from decision_engine import llm_engine

    source_view, _, _, _ = setup_case("LI:US")
    source_view["captured_at"] = AT.isoformat()
    source_view["data_quality"]["analysis_eligibility"]["B"]["allowed"] = True
    options = {"target_versions": [], "scenario_legs": []}
    plan = PlanningOutput(route="position_multi", multi_assets=["LI:US", "PDD:US"],
        intent={"primary_intent": "PositionDecision"}, planner_trace={"status": "fallback"})
    executions = {}
    calls = []
    real_prepare = position_analysis.prepare

    def prepare(single, executed, *, asset_view=None, options=None):
        result = real_prepare(single, executed, asset_view=asset_view, options=options, at=AT)
        calls.append((executed.market_data.symbol, asset_view["snapshot_id"], options,
                      result["qualification"]["L2"]["allowed"]))
        return result

    def execute(single, question):
        _, executed, _, _ = setup_case(single.intent["asset"])
        executions[single.intent["asset"]] = executed
        return executed

    class Expressing:
        async def run_streaming(self, single, executed, question, history):
            assert executed.position_evidence
            self.last_output = ExpressionOutput(llm_result=LLMResult(
                "SELL", [], [], [], chat_answer="SELL 987654 shares at 987654",
                structured_result={"decisionType": "exit", "confidence": .99,
                                   "limit_price": 987654, "quantity": 987654}))
            yield "UNSAFE_STREAM_987654"

    async def collect_pattern(scope, targets):
        return None, {"bundle_hashes": ["isolated-pattern"]}

    def final_view(view):
        result = deepcopy(view)
        result["context_current"] = not expire_at_final
        return result

    def forbidden_compare(*args, **kwargs):
        raise AssertionError("Unqualified second model must never run")

    saved = []
    monkeypatch.setattr(service, "get_executing_agent", lambda: SimpleNamespace(run=execute))
    monkeypatch.setattr(service, "get_expressing_agent", Expressing)
    monkeypatch.setattr(service, "_collect_decision_pattern_evidence", collect_pattern)
    monkeypatch.setattr(position_analysis, "prepare", prepare)
    monkeypatch.setattr(asset_views, "public_view", final_view)
    monkeypatch.setattr(llm_engine, "compare_multi_assets", forbidden_compare)
    monkeypatch.setattr(decision_service, "save_conversation_turn", lambda *a, **kw: saved.append(a[2]))
    monkeypatch.setattr(decision_service, "_DECISION_STORE", {})

    async def run():
        return [event async for event in service._handle_position_multi(
            plan, plan.multi_assets, "比较减仓价量", "isolated-multi", 1, "isolated-id",
            analysis_context=source_view, analysis_options=options)]

    events = asyncio.run(run())
    decoded = [(event.splitlines()[0][7:], json.loads(event.splitlines()[1][6:])) for event in events]
    text = "".join(data["delta"] for kind, data in decoded if kind == "text")
    done = next(data for kind, data in decoded if kind == "done")
    assert "987654" not in text
    assert "①" in text and "⑦" in text and "TECHNICAL_UNAVAILABLE" not in text
    assert done["actionable"] is False
    assert done["pattern_evidence"] == {"bundle_hashes": ["isolated-pattern"]}
    assert len(done["position_qualifications"]) == 2
    assert len(calls) == 4 and all(c[1] == source_view["snapshot_id"] and c[2] is options for c in calls)
    assert all(c[3] is True for c in calls[:2])
    assert all(c[3] is (not expire_at_final) for c in calls[2:])
    assert saved == [text]
    for stored in decision_service._DECISION_STORE["isolated-multi"].values():
        assert "987654" not in stored.llm.chat_answer + str(stored.llm.structured_result)
        assert stored._position_evidence["snapshot_id"] == source_view["snapshot_id"]
        assert stored._market_data.fundamentals is None
        assert not stored.llm.structured_result["analysisQualification"]["L3"]["allowed"]
