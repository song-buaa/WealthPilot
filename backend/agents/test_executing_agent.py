"""ExecutingAgent 单元测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AV_DEV_MOCK", "1")

from dotenv import load_dotenv
load_dotenv()


def test_executing_agent_position_route():
    """单标路径通过确定性 Skill 输出完成，不依赖 DB、LLM 或行情。"""
    from unittest.mock import patch
    from backend.agents import get_executing_agent
    from backend.agents.contracts import AgentTaskStatus, PlanningOutput
    from backend.graph.tools import (
        DisciplineCheckOutput,
        GenerateSignalsOutput,
        LoadDecisionContextOutput,
    )
    from decision_engine.data_loader import InvestmentRules, LoadedData, PositionInfo, UserProfile

    target = PositionInfo(
        name="测试基金", ticker="", asset_class="权益", weight=0.10,
        market_value_cny=100_000, cost_price=80_000, current_price=0,
        profit_loss_rate=0.25,
    )
    loaded = LoadedData(
        profile=UserProfile(), positions=[target], target_position=target,
        rules=InvestmentRules(
            max_single_position=0.40,
            max_equity_pct=0.80,
            min_cash_pct=0.10,
            max_leverage_ratio=1.20,
        ),
        total_assets=1_000_000,
    )
    plan_out = PlanningOutput(
        route="position_single",
        intent={"asset": "测试基金", "action_type": "持有评估", "confidence": 0.95},
        selected_skills=["wp-load-context", "wp-check-discipline", "wp-generate-signals"],
        portfolio_id=1,
    )
    skill_outputs = {
        "wp-load-context": LoadDecisionContextOutput(loaded_data=loaded),
        "wp-check-discipline": DisciplineCheckOutput(
            violation=False, warning=None, current_weight=0.10,
            max_position=0.40, position_ratio=0.25, rule_details=["仓位健康"],
        ),
        "wp-generate-signals": GenerateSignalsOutput(
            asset_name="测试基金", position_signal="合理", fundamental_signal="中性",
            sentiment_signal="中性", event_uncertainty="低", event_direction="中性",
        ),
    }

    executing = get_executing_agent()
    with (
        patch("backend.core.demo_mode.PUBLIC_DEMO_MODE", False),
        patch(
            "backend.agents.executing_agent.invoke_skill",
            side_effect=lambda name, **_: skill_outputs[name],
        ),
    ):
        exec_out = executing.run(plan_out, user_query="测试基金还能持有吗？")

    assert exec_out.status == AgentTaskStatus.COMPLETED
    assert not exec_out.aborted
    assert exec_out.loaded_data is loaded
    assert exec_out.rule_result.current_weight == 0.10
    assert exec_out.signal_result.position_signal == "合理"
    assert exec_out.invoked_skills[:3] == [
        "wp-load-context", "wp-check-discipline", "wp-generate-signals",
    ]


def test_executing_agent_passthrough():
    """General 路径构造轻量上下文，非关键词查询不触发知识检索。"""
    from backend.agents import get_executing_agent
    from backend.agents.contracts import AgentTaskStatus, PlanningOutput

    plan_out = PlanningOutput(route="general")
    exec_out = get_executing_agent().run(plan_out, user_query="什么是夏普比率？")

    assert exec_out.status == AgentTaskStatus.COMPLETED
    assert exec_out.loaded_data is not None
    assert exec_out.loaded_data.positions == []
    assert exec_out.skill_results["wp-retrieve-principles"]["skipped"] is True


def test_executing_agent_task_id_propagation():
    """task_id 从 PlanningOutput 传到 ExecutionOutput。"""
    from backend.agents import get_executing_agent
    from backend.agents.contracts import PlanningOutput

    plan_out = PlanningOutput(task_id="task_test_propagation", route="clarify")
    exec_out = get_executing_agent().run(plan_out, user_query="测试")

    assert exec_out.task_id == plan_out.task_id


def test_executing_agent_portfolio_route():
    """组合路径通过确定性上下文完成，不读取个人数据库。"""
    from unittest.mock import patch
    from backend.agents import get_executing_agent
    from backend.agents.contracts import AgentTaskStatus, PlanningOutput
    from backend.graph.tools import LoadDecisionContextOutput
    from decision_engine.data_loader import InvestmentRules, LoadedData, PositionInfo, UserProfile

    position = PositionInfo(
        name="测试资产", ticker="", asset_class="权益", weight=0.25,
        market_value_cny=250_000, cost_price=200_000, current_price=0,
        profit_loss_rate=0.25,
    )
    loaded = LoadedData(
        profile=UserProfile(), positions=[position], target_position=None,
        rules=InvestmentRules(
            max_single_position=0.40,
            max_equity_pct=0.80,
            min_cash_pct=0.10,
            max_leverage_ratio=1.20,
        ),
        total_assets=1_000_000,
    )
    plan_out = PlanningOutput(
        route="portfolio",
        intent={"primary_intent": "PerformanceAnalysis"},
        selected_skills=["wp-load-context"],
        portfolio_id=1,
    )

    with patch(
        "backend.agents.executing_agent.invoke_skill",
        return_value=LoadDecisionContextOutput(loaded_data=loaded),
    ):
        exec_out = get_executing_agent().run(plan_out, user_query="我的组合表现如何？")

    assert exec_out.status == AgentTaskStatus.COMPLETED
    assert exec_out.loaded_data is loaded
    assert exec_out.loaded_data.research == []
    assert exec_out.invoked_skills == ["wp-load-context"]


def test_executing_agent_with_real_planning_output():
    """用真实 Agent contracts 验证 Planning → Executing，无 DB/LLM/行情。"""
    from unittest.mock import patch
    from backend.agents import get_planning_agent, get_executing_agent
    from backend.graph.tools import (
        DisciplineCheckOutput,
        GenerateSignalsOutput,
        LoadDecisionContextOutput,
    )
    from decision_engine.data_loader import (
        InvestmentRules,
        LoadedData,
        PositionInfo,
        UserProfile,
    )

    planning = get_planning_agent()
    orchestrator_result = {
        "intent_payload": {
            "primary_intent": "PositionDecision",
            "asset": "特斯拉",
            "action_type": "持有评估",
            "confidence": 0.95,
        },
        "route": "position_single",
        "sse_handler": "position_single",
        "planner_rationale": "deterministic contract fixture",
    }
    with patch.object(planning, "_invoke_orchestrator", return_value=orchestrator_result):
        plan_out = planning.run(
            user_query="特斯拉该不该落袋为安？",
            conversation_id="test_e2e_planning_executing",
            portfolio_id=1,
        )

    target = PositionInfo(
        name="特斯拉",
        ticker="",
        asset_class="权益",
        weight=0.10,
        market_value_cny=100_000,
        cost_price=80_000,
        current_price=0,
        profit_loss_rate=0.25,
    )
    loaded = LoadedData(
        profile=UserProfile(),
        positions=[target],
        target_position=target,
        rules=InvestmentRules(
            max_single_position=0.40,
            max_equity_pct=0.80,
            min_cash_pct=0.10,
            max_leverage_ratio=1.20,
        ),
        total_assets=1_000_000,
    )

    assert plan_out.intent is not None, "intent is None"
    assert isinstance(plan_out.intent, dict), "intent not dict"

    executing = get_executing_agent()
    skill_outputs = {
        "wp-load-context": LoadDecisionContextOutput(
            loaded_data=loaded,
            has_required_data=True,
            has_data_errors=False,
            target_position_found=True,
        ),
        "wp-check-discipline": DisciplineCheckOutput(
            violation=False,
            warning=None,
            current_weight=0.10,
            max_position=0.40,
            position_ratio=0.25,
            rule_details=["仓位健康"],
        ),
        "wp-generate-signals": GenerateSignalsOutput(
            asset_name="特斯拉",
            position_signal="合理",
            fundamental_signal="中性",
            sentiment_signal="中性",
            event_uncertainty="低",
            event_direction="中性",
        ),
    }

    with patch(
        "backend.agents.executing_agent.invoke_skill",
        side_effect=lambda name, **_: skill_outputs[name],
    ):
        exec_out = executing.run(plan_out, "特斯拉该不该落袋为安？")

    assert not exec_out.aborted, f"ExecutingAgent ABORT: {exec_out.abort_reason}"
    assert exec_out.loaded_data is not None, "loaded_data None"
    assert exec_out.loaded_data.target_position is not None, "target_position None"

    assert exec_out.loaded_data.target_position.name == "特斯拉"
    assert exec_out.rule_result.current_weight == 0.10
    assert exec_out.signal_result.position_signal == "合理"


if __name__ == "__main__":
    test_executing_agent_position_route()
    test_executing_agent_passthrough()
    test_executing_agent_task_id_propagation()
    test_executing_agent_portfolio_route()
    test_executing_agent_with_real_planning_output()
    print("\n🎉 ExecutingAgent 5/5 测试通过")
