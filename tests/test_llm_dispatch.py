"""
v3.8.5 C0: llm_dispatch 机制单测。

验证 SkillsLoader._invoke_llm_dispatch 的三条路径:
1. general_chat → chat() 跑通，返回 str，与直调 llm_engine.chat 结果一致
2. 未支持 template_id 抛 NotImplementedError，报错含 "待 C6"
3. 缺 entry_point 的伪 meta 抛 ValueError
"""
import pytest
from unittest.mock import patch, MagicMock

from backend.skills.loader import SkillsLoader, SkillMeta


# ── 1. general_chat 跑通 ──────────────────────────────────────────────

def test_llm_dispatch_general_chat_returns_str():
    """invoke wp-reasoning general_chat → 返回 str，与直调 chat() 一致。"""
    fake_reply = "夏普比率是衡量风险调整后收益的指标。"

    with patch("decision_engine.llm_engine.chat", return_value=fake_reply) as mock_chat:
        from backend.skills import invoke_skill

        result = invoke_skill(
            "wp-reasoning",
            prompt_template_id="general_chat",
            user_query="什么是夏普比率",
            context=None,
            principles_override=None,
        )

        assert isinstance(result, str)
        assert result == fake_reply
        mock_chat.assert_called_once_with(
            user_query="什么是夏普比率",
            context=None,
            principles_override=None,
        )


# ── 2. 未支持 template_id 抛 NotImplementedError ─────────────────────

@pytest.mark.parametrize("template_id", [
    "position_decision",
    "portfolio_review",
    "asset_allocation",
    "performance_analysis",
])
def test_llm_dispatch_unsupported_template_raises(template_id):
    """未支持的 template_id 抛 NotImplementedError，报错含 '待 C6'。"""
    from backend.skills import invoke_skill

    with pytest.raises(NotImplementedError, match="待 C6"):
        invoke_skill(
            "wp-reasoning",
            prompt_template_id=template_id,
            user_query="test",
        )


# ── 3. 缺 entry_point 抛 ValueError ─────────────────────────────────

def test_llm_dispatch_missing_entry_point_raises():
    """entry_point 为空的 llm_dispatch meta 抛 ValueError。"""
    loader = SkillsLoader()
    meta = SkillMeta(
        name="fake-llm-skill",
        description="test",
        type="llm_dispatch",
        entry_point=None,  # 缺失
    )

    with pytest.raises(ValueError, match="缺少 entry_point"):
        loader._invoke_llm_dispatch(meta, prompt_template_id="general_chat")


# ── 4. 缺 prompt_template_id 抛 ValueError ──────────────────────────

def test_llm_dispatch_missing_template_id_raises():
    """调用时不传 prompt_template_id 抛 ValueError。"""
    loader = SkillsLoader()
    meta = SkillMeta(
        name="fake-llm-skill",
        description="test",
        type="llm_dispatch",
        entry_point="decision_engine.llm_engine",
    )

    with pytest.raises(ValueError, match="缺少 prompt_template_id"):
        loader._invoke_llm_dispatch(meta, user_query="test")
