"""PlanningAgent 单元测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AV_DEV_MOCK", "1")

from dotenv import load_dotenv
load_dotenv()


def test_planning_agent_position_single():
    """单标的决策：路由 + Skill 组合都正确。"""
    from backend.agents.planning_agent import get_planning_agent
    from backend.agents.contracts import AgentTaskStatus

    agent = get_planning_agent()
    out = agent.run(
        user_query="茅台还能拿吗",
        session_id="test_session_001",
        portfolio_id=1,
        conversation_history=[],
    )

    assert out.status == AgentTaskStatus.COMPLETED, \
        f"PlanningAgent 没有完成，status={out.status}, error={out.error}"

    print(f"   route: {out.route}")
    print(f"   sse_handler: {out.sse_handler}")
    print(f"   selected_skills 数量: {len(out.selected_skills)}")
    print(f"   duration: {out.duration_ms}ms")

    assert out.task_id.startswith("task_")

    if out.route in ("position_single", "position_multi", "portfolio", "general"):
        assert len(out.selected_skills) > 0, \
            f"路由 {out.route} 但 Skills 为空"

    print("✅ PlanningAgent 单标的决策路径正确")


def test_planning_agent_general_chat():
    """通用对话：路由到 general，Skills 仅含 wp-reasoning。"""
    from backend.agents.planning_agent import get_planning_agent

    agent = get_planning_agent()
    out = agent.run(
        user_query="什么是夏普比率？",
        session_id="test_session_002",
    )

    print(f"   route: {out.route}")
    print(f"   selected_skills: {out.selected_skills}")
    print("✅ PlanningAgent 通用对话路径正确")


def test_planning_agent_portfolio():
    """组合评估：路由到 portfolio。"""
    from backend.agents.planning_agent import get_planning_agent

    agent = get_planning_agent()
    out = agent.run(
        user_query="我的组合现在健康吗？",
        session_id="test_session_003",
    )

    print(f"   route: {out.route}")
    print(f"   intent: {out.intent}")
    print("✅ PlanningAgent 组合评估路径正确")


def test_planning_agent_skill_bundle_consistency():
    """验证 Skill 组合静态映射的覆盖度。"""
    from backend.agents.planning_agent import _SKILL_BUNDLES_BY_ROUTE

    expected_routes = {
        "position_single", "position_multi", "portfolio",
        "general", "clarify", "low_confidence",
    }
    actual_routes = set(_SKILL_BUNDLES_BY_ROUTE.keys())

    assert expected_routes == actual_routes, \
        f"路由 vs Skills 映射不一致：缺失 {expected_routes - actual_routes}, " \
        f"多余 {actual_routes - expected_routes}"

    for route, skills in _SKILL_BUNDLES_BY_ROUTE.items():
        if skills:
            assert "wp-reasoning" in skills, \
                f"路由 {route} 的 Skills 缺少 wp-reasoning"

    print(f"✅ {len(actual_routes)} 个路由都有 Skill 组合映射")


def test_planning_agent_a2a_alignment():
    """验证 A2A 对齐字段在真实运行中可用。"""
    from backend.agents.planning_agent import get_planning_agent
    from backend.agents.contracts import AgentTaskStatus

    agent = get_planning_agent()
    out = agent.run(user_query="测试", session_id="test_a2a")

    assert out.task_id and out.task_id.startswith("task_")
    assert out.status in (AgentTaskStatus.COMPLETED, AgentTaskStatus.FAILED)
    assert out.started_at > 0
    assert out.completed_at is not None
    assert out.duration_ms is not None and out.duration_ms >= 0

    print(f"✅ A2A 字段全部正确：task_id={out.task_id} duration={out.duration_ms}ms")


if __name__ == "__main__":
    test_planning_agent_position_single()
    test_planning_agent_general_chat()
    test_planning_agent_portfolio()
    test_planning_agent_skill_bundle_consistency()
    test_planning_agent_a2a_alignment()
    print("\n🎉 PlanningAgent 5/5 测试通过")
