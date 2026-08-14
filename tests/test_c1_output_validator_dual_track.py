"""
v3.8.6 C1: wp-output-validator 双轨单测。

验证 flag off（直连）和 flag on（invoke_skill）对同一组输入返回逐字段一致的 ValidationResult。
覆盖 3 种 action: pass / retry / fallback。
"""
import os
import pytest
from dataclasses import dataclass
from typing import Optional


# ── Mock LLMResult / GenericLLMResult ────────────────────────────


@dataclass
class MockLLMResult:
    """模拟 PositionDecision 的 LLMResult。"""
    decision: str = "HOLD"
    reasoning: list = None
    risk: list = None
    strategy: list = None
    chat_answer: str = "这是一段足够长的投资建议文本，超过二十个字。"
    raw_output: str = ""
    error: Optional[str] = None
    structured_result: Optional[dict] = None
    decision_corrected: bool = False
    original_decision: Optional[str] = None

    def __post_init__(self):
        if self.reasoning is None:
            self.reasoning = ["理由1"]
        if self.risk is None:
            self.risk = ["风险1"]
        if self.strategy is None:
            self.strategy = ["策略1"]

    @property
    def is_fallback(self) -> bool:
        return self.error is not None

    @property
    def decision_cn(self) -> str:
        return self.decision


@dataclass
class MockGenericLLMResult:
    """模拟 PortfolioReview 的 GenericLLMResult。"""
    intent_type: str = "portfolio_review"
    chat_answer: str = "这是一段足够长的组合评估文本，超过二十个字。"
    raw_payload: dict = None
    raw_output: str = ""
    error: Optional[str] = None

    def __post_init__(self):
        if self.raw_payload is None:
            self.raw_payload = {}

    @property
    def is_fallback(self) -> bool:
        return self.error is not None


# ── 辅助：跑两轨对比 ────────────────────────────────────────────


def _run_both_tracks(llm_result, intent_type: str):
    """分别显式 legacy off / 默认 production on 调一次。"""
    from backend.graph.decision_validator import validate_decision_output

    # flag off: 直连
    os.environ["WP_USE_SKILL_OUTPUT_VALIDATOR"] = "0"
    vr_off = validate_decision_output(result=llm_result, intent_type=intent_type)

    # 默认 production 路径: invoke_skill
    os.environ.pop("WP_USE_SKILL_OUTPUT_VALIDATOR", None)
    try:
        from backend.skills import invoke_skill
        vr_on = invoke_skill("wp-output-validator", result=llm_result, intent_type=intent_type)
    finally:
        os.environ.pop("WP_USE_SKILL_OUTPUT_VALIDATOR", None)

    return vr_off, vr_on


def _assert_vr_equal(vr_off, vr_on, label: str):
    """断言两个 ValidationResult 逐字段一致。"""
    assert vr_off.passed == vr_on.passed, f"{label}: passed mismatch {vr_off.passed} vs {vr_on.passed}"
    assert vr_off.action == vr_on.action, f"{label}: action mismatch {vr_off.action} vs {vr_on.action}"
    assert vr_off.intent_type == vr_on.intent_type, f"{label}: intent_type mismatch"
    off_rules = [(f.rule, f.message, f.severity) for f in vr_off.failures]
    on_rules = [(f.rule, f.message, f.severity) for f in vr_on.failures]
    assert off_rules == on_rules, f"{label}: failures mismatch\n  off={off_rules}\n  on={on_rules}"


# ── 测试用例 ────────────────────────────────────────────────────


def test_dual_track_pass_position_decision():
    """PositionDecision 正常 pass：两轨一致。"""
    result = MockLLMResult(decision="HOLD", reasoning=["理由"], risk=["风险"])
    vr_off, vr_on = _run_both_tracks(result, "PositionDecision")
    _assert_vr_equal(vr_off, vr_on, "pass_position")
    assert vr_off.passed is True
    assert vr_off.action == "pass"


def test_dual_track_retry_decision_invalid():
    """PositionDecision decision 不合法 → retry：两轨一致。"""
    result = MockLLMResult(decision="INVALID_DECISION", reasoning=["理由"], risk=["风险"])
    vr_off, vr_on = _run_both_tracks(result, "PositionDecision")
    _assert_vr_equal(vr_off, vr_on, "retry_invalid")
    assert vr_off.passed is False
    assert vr_off.action == "retry"
    assert any(f.rule == "decision_invalid" for f in vr_off.failures)


def test_dual_track_fallback_is_fallback():
    """LLM fallback（error 非空）→ fallback：两轨一致。"""
    result = MockLLMResult(error="LLM timeout")
    vr_off, vr_on = _run_both_tracks(result, "PositionDecision")
    _assert_vr_equal(vr_off, vr_on, "fallback")
    assert vr_off.passed is False
    assert vr_off.action == "fallback"
    assert any(f.rule == "is_fallback" for f in vr_off.failures)


def test_dual_track_pass_portfolio_review():
    """PortfolioReview（GenericLLMResult）正常 pass：两轨一致。"""
    result = MockGenericLLMResult()
    vr_off, vr_on = _run_both_tracks(result, "PortfolioReview")
    _assert_vr_equal(vr_off, vr_on, "pass_portfolio")
    assert vr_off.passed is True
    assert vr_off.action == "pass"


def test_dual_track_retry_chat_answer_empty():
    """chat_answer 为空 → retry：两轨一致。"""
    result = MockGenericLLMResult(chat_answer="")
    vr_off, vr_on = _run_both_tracks(result, "PortfolioReview")
    _assert_vr_equal(vr_off, vr_on, "retry_empty")
    assert vr_off.passed is False
    assert vr_off.action == "retry"


def test_flag_on_by_default():
    """生产契约：默认不设环境变量时走 Skill。"""
    os.environ.pop("WP_USE_SKILL_OUTPUT_VALIDATOR", None)
    from backend.agents.reviewing_agent import _use_skill_output_validator
    assert _use_skill_output_validator() is True


def test_flag_on_when_set():
    """设置环境变量=1，flag 返回 True。"""
    os.environ["WP_USE_SKILL_OUTPUT_VALIDATOR"] = "1"
    try:
        from backend.agents.reviewing_agent import _use_skill_output_validator
        assert _use_skill_output_validator() is True
    finally:
        os.environ.pop("WP_USE_SKILL_OUTPUT_VALIDATOR", None)


# ── (b) flag 真的切换执行路径 ────────────────────────────────────


def _make_reviewing_inputs():
    """构造 ReviewingAgent._run_hard_validation 所需的三个 input。"""
    from backend.agents.contracts import (
        PlanningOutput, ExpressionOutput, ReviewOutput,
    )
    plan = PlanningOutput(
        route="position_single",
        intent={"primary_intent": "PositionDecision", "confidence": 0.9},
    )
    expr = ExpressionOutput()
    expr.llm_result = MockLLMResult(decision="HOLD", reasoning=["理由"], risk=["风险"])
    out = ReviewOutput()
    return out, plan, expr


def test_explicit_zero_calls_direct_not_invoke_skill():
    """显式 legacy off → 直连 validator；默认仍保持 Skill 路径。"""
    from unittest.mock import patch, MagicMock
    from backend.agents.reviewing_agent import ReviewingAgent
    from backend.graph.decision_validator import ValidationResult, ValidationFailure

    os.environ["WP_USE_SKILL_OUTPUT_VALIDATOR"] = "0"
    out, plan, expr = _make_reviewing_inputs()

    mock_vr = ValidationResult(passed=True, failures=[], action="pass", intent_type="PositionDecision")

    with patch(
        "backend.graph.decision_validator.validate_decision_output",
        return_value=mock_vr,
    ) as mock_direct, patch(
        "backend.skills.invoke_skill",
        return_value=mock_vr,
    ) as mock_skill:
        agent = ReviewingAgent()
        agent._run_hard_validation(out, plan, expr)

        mock_direct.assert_called_once()
        mock_skill.assert_not_called()
        # 确认传参正确
        call_kwargs = mock_direct.call_args.kwargs
        assert call_kwargs["result"] is expr.llm_result
        assert call_kwargs["intent_type"] == "PositionDecision"
    os.environ.pop("WP_USE_SKILL_OUTPUT_VALIDATOR", None)


def test_flag_on_calls_invoke_skill_not_direct():
    """flag on → invoke_skill 被调（skill 名 wp-output-validator），直连没被调。"""
    from unittest.mock import patch, MagicMock
    from backend.agents.reviewing_agent import ReviewingAgent
    from backend.graph.decision_validator import ValidationResult, ValidationFailure

    os.environ["WP_USE_SKILL_OUTPUT_VALIDATOR"] = "1"
    try:
        out, plan, expr = _make_reviewing_inputs()

        mock_vr = ValidationResult(passed=True, failures=[], action="pass", intent_type="PositionDecision")

        with patch(
            "backend.graph.decision_validator.validate_decision_output",
            return_value=mock_vr,
        ) as mock_direct, patch(
            "backend.skills.invoke_skill",
            return_value=mock_vr,
        ) as mock_skill:
            agent = ReviewingAgent()
            agent._run_hard_validation(out, plan, expr)

            mock_skill.assert_called_once()
            mock_direct.assert_not_called()
            # 确认 skill 名和传参
            call_args = mock_skill.call_args
            assert call_args.args[0] == "wp-output-validator"
            assert call_args.kwargs["result"] is expr.llm_result
            assert call_args.kwargs["intent_type"] == "PositionDecision"
    finally:
        os.environ.pop("WP_USE_SKILL_OUTPUT_VALIDATOR", None)
