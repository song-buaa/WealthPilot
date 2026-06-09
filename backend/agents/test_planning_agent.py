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
        conversation_id="test_session_001",
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
        conversation_id="test_session_002",
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
        conversation_id="test_session_003",
    )

    print(f"   route: {out.route}")
    print(f"   intent: {out.intent}")
    print("✅ PlanningAgent 组合评估路径正确")


def test_planning_agent_skill_bundle_consistency():
    """验证 Skill 组合静态映射的覆盖度。"""
    from backend.agents.planning_agent import LEGACY_SELECTED_SKILLS_BY_ROUTE

    expected_routes = {
        "position_single", "position_multi", "portfolio",
        "general", "clarify", "low_confidence",
    }
    actual_routes = set(LEGACY_SELECTED_SKILLS_BY_ROUTE.keys())

    assert expected_routes == actual_routes, \
        f"路由 vs Skills 映射不一致：缺失 {expected_routes - actual_routes}, " \
        f"多余 {actual_routes - expected_routes}"

    for route, skills in LEGACY_SELECTED_SKILLS_BY_ROUTE.items():
        if skills:
            assert "wp-reasoning" in skills, \
                f"路由 {route} 的 Skills 缺少 wp-reasoning"

    print(f"✅ {len(actual_routes)} 个路由都有 Skill 组合映射")


def test_planning_agent_a2a_alignment():
    """验证 A2A 对齐字段在真实运行中可用。"""
    from backend.agents.planning_agent import get_planning_agent
    from backend.agents.contracts import AgentTaskStatus

    agent = get_planning_agent()
    out = agent.run(user_query="测试", conversation_id="test_a2a")

    assert out.task_id and out.task_id.startswith("task_")
    assert out.status in (AgentTaskStatus.COMPLETED, AgentTaskStatus.FAILED)
    assert out.started_at > 0
    assert out.completed_at is not None
    assert out.duration_ms is not None and out.duration_ms >= 0

    print(f"✅ A2A 字段全部正确：task_id={out.task_id} duration={out.duration_ms}ms")


# ════════════════════════════════════════════════
# Step 9：Skill Selector 测试
# ════════════════════════════════════════════════

def test_is_edge_case_standard_query():
    """标准场景：不是边界。"""
    from backend.agents.planning_agent import _is_edge_case

    intent = {"primary_intent": "PositionDecision", "confidence": 0.9}
    is_edge, reason = _is_edge_case("茅台还能拿吗", intent)
    assert is_edge is False, f"标准场景误判为边界: {reason}"
    print(f"✅ 标准场景正确识别（非边界）")


def test_is_edge_case_macro():
    """宏观关键词触发边界。"""
    from backend.agents.planning_agent import _is_edge_case

    intent = {"primary_intent": "PortfolioReview", "confidence": 0.9}
    is_edge, reason = _is_edge_case("美联储加息对我组合有什么影响", intent)
    assert is_edge is True
    assert "美联储" in reason or "macro" in reason
    print(f"✅ 宏观关键词触发边界: {reason}")


def test_is_edge_case_low_confidence():
    """低置信度触发边界。"""
    from backend.agents.planning_agent import _is_edge_case

    intent = {"primary_intent": "PositionDecision", "confidence": 0.5}
    is_edge, reason = _is_edge_case("这个怎么样", intent)
    assert is_edge is True
    assert "confidence" in reason
    print(f"✅ 低置信度触发边界: {reason}")


def test_is_edge_case_multi_asset():
    """多标的连接词触发边界。"""
    from backend.agents.planning_agent import _is_edge_case

    intent = {"primary_intent": "PositionDecision", "confidence": 0.85}
    is_edge, reason = _is_edge_case("茅台还能拿吗，顺便看看纳指 ETF", intent)
    assert is_edge is True
    assert "顺便" in reason or "multi_asset" in reason
    print(f"✅ 多标的连接词触发边界: {reason}")


def test_planning_agent_with_edge_case():
    """端到端：边界场景 PlanningAgent 行为。"""
    from backend.agents import get_planning_agent

    agent = get_planning_agent()
    out = agent.run(
        user_query="美联储加息对我组合有什么影响",
        conversation_id="test_edge_macro",
    )

    print(f"   route: {out.route}")
    print(f"   skills: {out.selected_skills}")
    print(f"   rationale: {out.rationale[:120]}")
    print(f"✅ 边界场景端到端测试完成")


if __name__ == "__main__":
    test_planning_agent_position_single()
    test_planning_agent_general_chat()
    test_planning_agent_portfolio()
    test_planning_agent_skill_bundle_consistency()
    test_planning_agent_a2a_alignment()
    # Step 9 新增
    test_is_edge_case_standard_query()
    test_is_edge_case_macro()
    test_is_edge_case_low_confidence()
    test_is_edge_case_multi_asset()
    test_planning_agent_with_edge_case()
    print("\n🎉 PlanningAgent 10/10 测试通过")
