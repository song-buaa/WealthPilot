"""
v3.8.7 C2: wp-retrieve-principles 双轨单测。

验证:
1. user_query bug 已修（general 投资关键词命中时触发 retrieve）
2. flag off（直连）和 flag on（invoke_skill + 适配）返回同类型同内容
3. flag 默认关 / 路径切换正确
"""
import os
import pytest
from unittest.mock import patch, MagicMock

from backend.agents.executing_agent import (
    ExecutingAgent,
    _use_skill_retrieve_principles,
    _adapt_retrieve_result,
)
from backend.agents.contracts import PlanningOutput, AgentTaskStatus


def _make_general_planning(query_keyword: str = "再平衡") -> tuple:
    """构造 general 路由 + 投资关键词命中的输入。"""
    planning = PlanningOutput(
        route="general",
        intent={"primary_intent": "Education", "confidence": 0.95},
        selected_skills=["wp-retrieve-principles", "wp-reasoning"],
    )
    user_query = f"{query_keyword}的纪律是什么"
    return planning, user_query


# ── 1. user_query bug 修复验证 ──────────────────────────────────

def test_general_invest_keyword_triggers_retrieve():
    """修 bug 后，general 投资关键词命中时 invoked_skills 含 wp-retrieve-principles。"""
    planning, user_query = _make_general_planning("再平衡")
    agent = ExecutingAgent()
    out = agent.run(planning, user_query=user_query)
    assert "wp-retrieve-principles" in out.invoked_skills
    sr = out.skill_results.get("wp-retrieve-principles", {})
    assert sr.get("triggered_by") == "investment_keyword_match"


def test_general_non_invest_skips_retrieve():
    """非投资关键词，retrieve 不触发。"""
    planning = PlanningOutput(
        route="general",
        intent={"primary_intent": "Education", "confidence": 0.5},
        selected_skills=["wp-retrieve-principles", "wp-reasoning"],
    )
    agent = ExecutingAgent()
    out = agent.run(planning, user_query="今天天气怎么样")
    assert "wp-retrieve-principles" not in out.invoked_skills
    sr = out.skill_results.get("wp-retrieve-principles", {})
    assert sr.get("skipped") is True


# ── 2. 双轨等价 ────────────────────────────────────────────────

def test_dual_track_same_type_and_count():
    """flag off 和 flag on 返回的 retrieved_principles 同类型(RetrievedChunk)、同条数。"""
    from backend.knowledge.schemas import RetrievedChunk

    planning, user_query = _make_general_planning("再平衡")

    results = {}
    for flag_label, flag_val in [("OFF", ""), ("ON", "1")]:
        if flag_val:
            os.environ["WP_USE_SKILL_RETRIEVE_PRINCIPLES"] = flag_val
        else:
            os.environ.pop("WP_USE_SKILL_RETRIEVE_PRINCIPLES", None)
        agent = ExecutingAgent()
        out = agent.run(planning, user_query=user_query)
        rp = getattr(out.loaded_data, "retrieved_principles", [])
        results[flag_label] = rp

    os.environ.pop("WP_USE_SKILL_RETRIEVE_PRINCIPLES", None)

    rp_off = results["OFF"]
    rp_on = results["ON"]

    assert len(rp_off) == len(rp_on), f"count mismatch: {len(rp_off)} vs {len(rp_on)}"
    assert len(rp_off) > 0, "both should have results"

    for i, (off, on) in enumerate(zip(rp_off, rp_on)):
        assert isinstance(off, RetrievedChunk), f"[{i}] off is {type(off)}"
        assert isinstance(on, RetrievedChunk), f"[{i}] on is {type(on)}"
        assert hasattr(on, "content"), f"[{i}] on missing content attr"
        assert off.content == on.content, f"[{i}] content mismatch"
        assert off.source_type == on.source_type, f"[{i}] source_type mismatch"


# ── 3. 适配函数 ────────────────────────────────────────────────

def test_adapt_retrieve_result_converts_dict_to_chunks():
    """_adapt_retrieve_result 把 dict 转回 list[RetrievedChunk]。"""
    from backend.knowledge.schemas import RetrievedChunk

    raw = {
        "chunks": [
            {
                "content": "测试内容",
                "source_type": "allocation_principles",
                "source_channel": "local_principles",
                "parent_doc_path": "test.md",
                "chunk_index": 0,
                "semantic_score": 0.9,
            }
        ],
        "total_retrieved": 1,
    }
    result = _adapt_retrieve_result(raw)
    assert len(result) == 1
    assert isinstance(result[0], RetrievedChunk)
    assert result[0].content == "测试内容"
    assert result[0].source_type == "allocation_principles"


def test_adapt_retrieve_result_empty():
    """空 chunks 不报错。"""
    result = _adapt_retrieve_result({"chunks": [], "total_retrieved": 0})
    assert result == []


# ── 4. flag 行为 ────────────────────────────────────────────────

def test_flag_off_by_default():
    os.environ.pop("WP_USE_SKILL_RETRIEVE_PRINCIPLES", None)
    assert _use_skill_retrieve_principles() is False


def test_flag_on_when_set():
    os.environ["WP_USE_SKILL_RETRIEVE_PRINCIPLES"] = "1"
    try:
        assert _use_skill_retrieve_principles() is True
    finally:
        os.environ.pop("WP_USE_SKILL_RETRIEVE_PRINCIPLES", None)


# ── 5. 路径切换 spy ─────────────────────────────────────────────

def test_flag_off_calls_direct_store():
    """flag off → KnowledgeStore.retrieve 被调，invoke_skill 没被调。"""
    os.environ.pop("WP_USE_SKILL_RETRIEVE_PRINCIPLES", None)
    planning, user_query = _make_general_planning("再平衡")

    mock_chunks = [MagicMock(spec=["content", "source_type"])]

    with patch(
        "backend.agents.executing_agent.invoke_skill",
    ) as mock_skill, patch(
        "backend.knowledge.store.KnowledgeStore.get_instance",
    ) as mock_store_cls:
        mock_store = MagicMock()
        mock_store.is_ready.return_value = True
        mock_store.retrieve.return_value = mock_chunks
        mock_store_cls.return_value = mock_store

        agent = ExecutingAgent()
        out = agent.run(planning, user_query=user_query)

        mock_store.retrieve.assert_called_once()
        # invoke_skill 可能被 ExecutingAgent 其他地方调用，只检查 wp-retrieve-principles 没被调
        for call in mock_skill.call_args_list:
            assert call.args[0] != "wp-retrieve-principles", \
                "flag off 不应调 invoke_skill('wp-retrieve-principles')"


def test_flag_on_calls_invoke_skill():
    """flag on → invoke_skill('wp-retrieve-principles') 被调。"""
    os.environ["WP_USE_SKILL_RETRIEVE_PRINCIPLES"] = "1"
    try:
        planning, user_query = _make_general_planning("再平衡")

        mock_raw = {"chunks": [
            {"content": "c", "source_type": "allocation_principles",
             "source_channel": "local_principles", "parent_doc_path": "t.md",
             "chunk_index": 0, "semantic_score": 0.8}
        ], "total_retrieved": 1}

        with patch(
            "backend.agents.executing_agent.invoke_skill",
            return_value=mock_raw,
        ) as mock_skill:
            agent = ExecutingAgent()
            out = agent.run(planning, user_query=user_query)

            # 找到 wp-retrieve-principles 的调用
            rp_calls = [c for c in mock_skill.call_args_list
                        if c.args and c.args[0] == "wp-retrieve-principles"]
            assert len(rp_calls) == 1, f"expected 1 call, got {len(rp_calls)}"
            assert rp_calls[0].kwargs["query"] == user_query
            assert rp_calls[0].kwargs["source_types"] == ["allocation_principles"]
            assert rp_calls[0].kwargs["top_k"] == 3
    finally:
        os.environ.pop("WP_USE_SKILL_RETRIEVE_PRINCIPLES", None)
