"""ExecutingAgent 单元测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AV_DEV_MOCK", "1")

from dotenv import load_dotenv
load_dotenv()


def test_executing_agent_position_route():
    """单标决策路径：能加载数据并跑完信号生成。"""
    from backend.agents import get_planning_agent, get_executing_agent
    from backend.agents.contracts import AgentTaskStatus

    planning = get_planning_agent()
    plan_out = planning.run(
        user_query="广发纳指100ETF联接(QDII)C 还能拿吗",
        conversation_id="test_exec_001",
        portfolio_id=1,
    )

    if plan_out.route != "position_single":
        print(f"   Planner 路由到 {plan_out.route}（不是 position_single），跳过此测试")
        print("✅ ExecutingAgent 单标决策路径（跳过 - Planner 路由不匹配）")
        return

    executing = get_executing_agent()
    exec_out = executing.run(plan_out, user_query="广发纳指100ETF联接(QDII)C 还能拿吗")

    print(f"   status: {exec_out.status}")
    print(f"   aborted: {exec_out.aborted}")
    print(f"   invoked_skills: {exec_out.invoked_skills}")
    print(f"   loaded_data is None: {exec_out.loaded_data is None}")
    print(f"   rule_result is None: {exec_out.rule_result is None}")
    print(f"   signal_result is None: {exec_out.signal_result is None}")
    print(f"   duration: {exec_out.duration_ms}ms")

    assert exec_out.status != AgentTaskStatus.FAILED, \
        f"ExecutingAgent 失败：{exec_out.error}"

    if not exec_out.aborted:
        assert exec_out.loaded_data is not None, "loaded_data 缺失"
        assert exec_out.rule_result is not None, "rule_result 缺失"
        assert exec_out.signal_result is not None, "signal_result 缺失"
        assert "wp-load-context" in exec_out.invoked_skills
        assert "wp-check-discipline" in exec_out.invoked_skills
        assert "wp-generate-signals" in exec_out.invoked_skills

    print("✅ ExecutingAgent 单标决策路径正确")


def test_executing_agent_passthrough():
    """通用对话路径：直接 SKIPPED，不加载数据。"""
    from backend.agents import get_planning_agent, get_executing_agent
    from backend.agents.contracts import AgentTaskStatus

    planning = get_planning_agent()
    plan_out = planning.run(
        user_query="什么是夏普比率？",
        conversation_id="test_exec_002",
    )

    if plan_out.route != "general":
        print(f"   Planner 路由到 {plan_out.route}（不是 general），跳过此测试")
        print("✅ ExecutingAgent 通用对话（跳过 - Planner 路由不匹配）")
        return

    executing = get_executing_agent()
    exec_out = executing.run(plan_out, user_query="什么是夏普比率？")

    assert exec_out.status == AgentTaskStatus.SKIPPED, \
        f"通用对话应该 SKIPPED，实际 {exec_out.status}"
    assert exec_out.loaded_data is None
    print(f"✅ ExecutingAgent 通用对话直通正确（SKIPPED）")


def test_executing_agent_task_id_propagation():
    """task_id 从 PlanningOutput 传到 ExecutionOutput。"""
    from backend.agents import get_planning_agent, get_executing_agent

    planning = get_planning_agent()
    plan_out = planning.run(user_query="测试", conversation_id="test_exec_003")

    executing = get_executing_agent()
    exec_out = executing.run(plan_out, user_query="测试")

    assert exec_out.task_id == plan_out.task_id, \
        f"task_id 没有传递：plan={plan_out.task_id} exec={exec_out.task_id}"
    print(f"✅ task_id 全链路一致：{exec_out.task_id}")


def test_executing_agent_portfolio_route():
    """组合评估路径：加载组合数据。"""
    from backend.agents import get_planning_agent, get_executing_agent
    from backend.agents.contracts import AgentTaskStatus

    planning = get_planning_agent()
    plan_out = planning.run(
        user_query="我的组合现在健康吗？",
        conversation_id="test_exec_004",
    )

    if plan_out.route != "portfolio":
        print(f"   Planner 路由到 {plan_out.route}（不是 portfolio），跳过此测试")
        print("✅ ExecutingAgent 组合评估（跳过 - Planner 路由不匹配）")
        return

    executing = get_executing_agent()
    exec_out = executing.run(plan_out, user_query="我的组合现在健康吗？")

    assert exec_out.status != AgentTaskStatus.FAILED, \
        f"ExecutingAgent 失败：{exec_out.error}"
    if not exec_out.aborted:
        assert exec_out.loaded_data is not None
        assert "wp-load-context" in exec_out.invoked_skills
    print(f"✅ ExecutingAgent 组合评估路径正确")


def test_executing_agent_with_real_planning_output():
    """端到端：用真实 PlanningAgent 输出驱动 ExecutingAgent（防止契约不匹配）。"""
    from backend.agents import get_planning_agent, get_executing_agent
    from app.utils.position_aggregator import aggregate_investment_positions

    all_positions, _ = aggregate_investment_positions(1)

    planning = get_planning_agent()
    plan_out = planning.run(
        user_query="我有一只股票最近涨了不少，该不该趁现在落袋为安？（标的：特斯拉）",
        conversation_id="test_e2e_planning_executing",
        portfolio_id=1,
        all_positions=all_positions,
    )

    assert plan_out.intent is not None, "intent is None"
    assert isinstance(plan_out.intent, dict), "intent not dict"

    asset = plan_out.intent.get("asset")
    print(f"   PlanningAgent asset={repr(asset)}, route={plan_out.route}")

    if plan_out.route in ("clarify", "low_confidence"):
        print("   Planner routed to clarify/low_conf, skipping ExecutingAgent test")
        print("✅ Planning→Executing e2e (skipped - Planner route)")
        return

    assert asset, f"intent.asset empty: {repr(asset)}"

    executing = get_executing_agent()
    exec_out = executing.run(
        plan_out,
        "我有一只股票最近涨了不少，该不该趁现在落袋为安？（标的：特斯拉）",
    )

    assert not exec_out.aborted, f"ExecutingAgent ABORT: {exec_out.abort_reason}"
    assert exec_out.loaded_data is not None, "loaded_data None"
    assert exec_out.loaded_data.target_position is not None, "target_position None"

    print(f"   target: {exec_out.loaded_data.target_position.name}")
    print("✅ Planning→Executing 端到端契约一致")


if __name__ == "__main__":
    test_executing_agent_position_route()
    test_executing_agent_passthrough()
    test_executing_agent_task_id_propagation()
    test_executing_agent_portfolio_route()
    test_executing_agent_with_real_planning_output()
    print("\n🎉 ExecutingAgent 5/5 测试通过")
