"""v3.8.1 对账层单元测试。覆盖 PRD 第 4 节全部 6 个 case + 边界用例。"""
import pytest
from unittest.mock import patch
from backend.agents.skill_reconcile import reconcile_executing_skills, ReconcileReport


# ── PRD 第 4 节 Case 1: position_single 主干 ──

def test_case1_position_single_main():
    """position_single 主干（有 ticker）：3 个稳定差异。"""
    selected = [
        "wp-fetch-holdings", "wp-fetch-research", "wp-retrieve-principles",
        "wp-check-discipline", "wp-generate-signals",
        "wp-reasoning", "wp-citation-rules", "wp-output-validator",
    ]
    invoked = [
        "wp-load-context", "wp-check-discipline", "wp-generate-signals",
        "wp-fetch-realtime-quote", "wp-fetch-fundamentals",
        "wp-fetch-capital-flow", "wp-fetch-kline",
    ]
    r = reconcile_executing_skills("position_single", selected, invoked)

    assert r.route == "position_single"
    assert r.is_consistent is False
    assert r.has_unknown is False

    assert r.declared_exec == sorted([
        "wp-fetch-holdings", "wp-fetch-research", "wp-retrieve-principles",
        "wp-check-discipline", "wp-generate-signals",
    ])
    assert r.invoked_exec == sorted([
        "wp-load-context", "wp-check-discipline", "wp-generate-signals",
    ])
    assert r.matched == sorted(["wp-check-discipline", "wp-generate-signals"])
    assert r.declared_not_invoked == sorted([
        "wp-fetch-holdings", "wp-fetch-research", "wp-retrieve-principles",
    ])
    assert r.invoked_not_declared == ["wp-load-context"]
    assert r.pseudo_observed == sorted([
        "wp-fetch-capital-flow", "wp-fetch-fundamentals",
        "wp-fetch-kline", "wp-fetch-realtime-quote",
    ])


# ── PRD 第 4 节 Case 2: position 新建仓分支 ──

def test_case2_position_new_entry():
    """新建仓分支：全部 5 个 declared 未 invoke，2 个伪标记进 pseudo。"""
    selected = [
        "wp-fetch-holdings", "wp-fetch-research", "wp-retrieve-principles",
        "wp-check-discipline", "wp-generate-signals",
        "wp-reasoning", "wp-citation-rules", "wp-output-validator",
    ]
    invoked = [
        "wp-load-context", "m8-new-entry-analysis", "wp-check-discipline-partial",
    ]
    r = reconcile_executing_skills("position_single", selected, invoked)

    assert r.is_consistent is False
    assert r.has_unknown is False
    assert r.invoked_exec == ["wp-load-context"]
    assert r.matched == []
    assert r.declared_not_invoked == sorted([
        "wp-check-discipline", "wp-fetch-holdings", "wp-fetch-research",
        "wp-generate-signals", "wp-retrieve-principles",
    ])
    assert r.invoked_not_declared == ["wp-load-context"]
    assert r.pseudo_observed == sorted([
        "m8-new-entry-analysis", "wp-check-discipline-partial",
    ])


# ── PRD 第 4 节 Case 3: portfolio · PortfolioReview ──

def test_case3_portfolio_review():
    """PortfolioReview：fetch-research 条件 append，一致 1 个。"""
    selected = [
        "wp-fetch-holdings", "wp-fetch-research", "wp-retrieve-principles",
        "wp-calc-allocation-deviation", "wp-propose-allocation",
        "wp-reasoning", "wp-citation-rules", "wp-output-validator",
    ]
    invoked = ["wp-load-context", "wp-fetch-research"]
    r = reconcile_executing_skills("portfolio", selected, invoked)

    assert r.is_consistent is False
    assert r.has_unknown is False
    assert r.declared_exec == sorted([
        "wp-fetch-holdings", "wp-fetch-research", "wp-retrieve-principles",
    ])
    assert r.invoked_exec == sorted(["wp-fetch-research", "wp-load-context"])
    assert r.matched == ["wp-fetch-research"]
    assert r.declared_not_invoked == sorted([
        "wp-fetch-holdings", "wp-retrieve-principles",
    ])
    assert r.invoked_not_declared == ["wp-load-context"]


# ── PRD 第 4 节 Case 4: portfolio · PerformanceAnalysis / AssetAllocation ──

def test_case4_portfolio_other_intents():
    """PerformanceAnalysis/AssetAllocation：不 append fetch-research。"""
    selected = [
        "wp-fetch-holdings", "wp-fetch-research", "wp-retrieve-principles",
        "wp-calc-allocation-deviation", "wp-propose-allocation",
        "wp-reasoning", "wp-citation-rules", "wp-output-validator",
    ]
    invoked = ["wp-load-context"]
    r = reconcile_executing_skills("portfolio", selected, invoked)

    assert r.is_consistent is False
    assert r.has_unknown is False
    assert r.invoked_exec == ["wp-load-context"]
    assert r.matched == []
    assert r.declared_not_invoked == sorted([
        "wp-fetch-holdings", "wp-fetch-research", "wp-retrieve-principles",
    ])
    assert r.invoked_not_declared == ["wp-load-context"]


# ── PRD 第 4 节 Case 5: general · 投资关键词命中（唯一一致 case）──

def test_case5_general_investment_hit():
    """general 投资关键词命中：唯一 is_consistent=True。"""
    selected = ["wp-retrieve-principles", "wp-reasoning"]
    invoked = ["wp-retrieve-principles"]
    r = reconcile_executing_skills("general", selected, invoked)

    assert r.is_consistent is True
    assert r.has_unknown is False
    assert r.declared_exec == ["wp-retrieve-principles"]
    assert r.invoked_exec == ["wp-retrieve-principles"]
    assert r.matched == ["wp-retrieve-principles"]
    assert r.declared_not_invoked == []
    assert r.invoked_not_declared == []


# ── PRD 第 4 节 Case 6: general · 非投资话题 ──

def test_case6_general_non_investment():
    """general 非投资话题：invoked 空，declared_not_invoked = retrieve-principles。"""
    selected = ["wp-retrieve-principles", "wp-reasoning"]
    invoked = []
    r = reconcile_executing_skills("general", selected, invoked)

    assert r.is_consistent is False
    assert r.has_unknown is False
    assert r.declared_exec == ["wp-retrieve-principles"]
    assert r.invoked_exec == []
    assert r.matched == []
    assert r.declared_not_invoked == ["wp-retrieve-principles"]
    assert r.invoked_not_declared == []


# ── 边界用例：unknown skill 注入 ──

def test_unknown_skill_injection():
    """清单里塞一个映射表未覆盖的 wp-*，进 unknown_declared。"""
    selected = ["wp-not-in-map", "wp-check-discipline"]
    invoked = ["wp-check-discipline"]
    r = reconcile_executing_skills("position_single", selected, invoked)

    assert r.unknown_declared == ["wp-not-in-map"]
    assert r.has_unknown is True
    # executing diff 不受影响
    assert r.declared_exec == ["wp-check-discipline"]
    assert r.invoked_exec == ["wp-check-discipline"]
    assert r.is_consistent is True


def test_unknown_invoked_skill():
    """invoked 里塞一个映射表未覆盖的 wp-*，进 unknown_invoked。"""
    selected = []
    invoked = ["wp-mystery-skill"]
    r = reconcile_executing_skills("position_single", selected, invoked)

    assert r.unknown_invoked == ["wp-mystery-skill"]
    assert r.has_unknown is True


# ── 边界用例：None 入参 ──

def test_none_selected_skills():
    """selected_skills=None 不抛异常。"""
    r = reconcile_executing_skills("general", None, ["wp-retrieve-principles"])
    assert r.declared_exec == []
    assert r.invoked_exec == ["wp-retrieve-principles"]
    assert r.is_consistent is False  # invoked_not_declared 非空


def test_none_invoked_skills():
    """invoked_skills=None 不抛异常。"""
    r = reconcile_executing_skills("general", ["wp-retrieve-principles"], None)
    assert r.invoked_exec == []
    assert r.declared_exec == ["wp-retrieve-principles"]


def test_none_route():
    """route=None 不抛异常。"""
    r = reconcile_executing_skills(None, [], [])
    assert r.route == ""
    assert r.is_consistent is True


def test_all_none():
    """全部 None 不抛异常。"""
    r = reconcile_executing_skills(None, None, None)
    assert r.route == ""
    assert r.is_consistent is True
    assert r.has_unknown is False


# ── 异常不冒泡用例（Step 2：验证挂接的 try/except 生效）──

def test_reconcile_exception_does_not_crash_agent():
    """mock reconcile_executing_skills 抛异常，ExecutingAgent.run() 仍正常返回 out。

    走 general 路由（真实会触发对账的路径）。mock 掉 KnowledgeStore 避免 DB 依赖。
    验证：对账异常完全不影响主流程产出。
    """
    from backend.agents.executing_agent import ExecutingAgent
    from backend.agents.contracts import PlanningOutput, AgentTaskStatus

    agent = ExecutingAgent()

    # general 路由：不需要 DB/LLM，只需要 intent 里有 user_query
    planning = PlanningOutput(
        route="general",
        intent={"primary_intent": "Education", "user_query": "什么是再平衡", "confidence": 0.9},
        selected_skills=["wp-retrieve-principles", "wp-reasoning"],
    )

    # mock KnowledgeStore 避免 DB 依赖，mock reconcile 抛异常
    mock_store = type("MockStore", (), {"is_ready": lambda self: False})()
    with patch(
        "backend.agents.executing_agent.reconcile_executing_skills",
        side_effect=RuntimeError("deliberate test explosion"),
    ), patch(
        "backend.knowledge.store.KnowledgeStore.get_instance",
        return_value=mock_store,
    ):
        out = agent.run(planning, user_query="什么是再平衡")

    # run() 正常返回，不抛异常
    assert out is not None
    assert out.status == AgentTaskStatus.COMPLETED
    # general 路由正常完成时 invoked_skills 应已填充（投资关键词"再平衡"命中）
    assert "wp-retrieve-principles" in out.invoked_skills
