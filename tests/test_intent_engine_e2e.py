"""IntentEngine orchestration contracts without network or user database access."""

from unittest.mock import patch

from intent_engine.context_manager import clear_session
from intent_engine.engine import run
from intent_engine.types import IntentEntities, IntentPayload, SubtaskResult


def _position_payload(asset: str | None) -> IntentPayload:
    return IntentPayload(
        primary_intent="PositionDecision",
        secondary_intents=[],
        subtasks=["thesis_review", "position_fit_check", "action_evaluation"],
        actions=["ANALYZE"],
        entities=IntentEntities(asset=asset, asset_normalized="LI:US" if asset else None),
        confidence=0.95,
    )


def _successful_subtasks() -> list[SubtaskResult]:
    return [
        SubtaskResult(subtask=name, status="success", content=f"{name} 已完成")
        for name in ("thesis_review", "position_fit_check", "action_evaluation")
    ]


def test_position_decision_e2e() -> None:
    """Current engine signature and orchestration preserve the PositionDecision contract."""
    conversation_id = "test_e2e_position_decision"
    clear_session(conversation_id)

    with (
        patch("intent_engine.engine.recognize", return_value=(_position_payload("理想汽车"), None)),
        patch("intent_engine.engine.subtask_runner.run", return_value=_successful_subtasks()),
        patch(
            "intent_engine.engine.output_renderer.render",
            return_value="## 操作建议\n保持纪律并复核风险。仅供参考，不构成投资建议。",
        ),
    ):
        result = run(
            user_input="理想汽车要不要卖？",
            conversation_id=conversation_id,
            portfolio_id=1,
        )

    assert result.aborted is False
    assert result.primary_intent == "PositionDecision"
    assert result.plan is not None
    assert {step.subtask for step in result.plan.primary_flow} == {
        "thesis_review",
        "position_fit_check",
        "action_evaluation",
    }
    assert {item.subtask for item in result.subtask_results} == {
        "thesis_review",
        "position_fit_check",
        "action_evaluation",
    }
    assert all(item.status == "success" for item in result.subtask_results)
    assert "操作建议" in result.final_output
    assert "不构成投资建议" in result.final_output


def test_multi_turn_context_inheritance() -> None:
    """A PositionDecision follow-up inherits the first turn's asset."""
    conversation_id = "test_e2e_multi_turn"
    clear_session(conversation_id)

    with (
        patch(
            "intent_engine.engine.recognize",
            side_effect=[
                (_position_payload("理想汽车"), None),
                (_position_payload(None), None),
            ],
        ),
        patch("intent_engine.engine.subtask_runner.run", return_value=_successful_subtasks()),
        patch(
            "intent_engine.engine.output_renderer.render",
            return_value="这是可复核的测试输出，仅供参考。",
        ),
    ):
        first = run(
            "理想汽车目前值得持有吗？",
            conversation_id=conversation_id,
            portfolio_id=1,
        )
        second = run(
            "那如果下个月发布会后呢？",
            conversation_id=conversation_id,
            portfolio_id=1,
        )

    assert first.context is not None
    assert first.context.inherited_fields.asset == "理想汽车"
    assert second.context is not None
    assert second.context.turn_index == 2
    assert second.context.inherited_fields.asset == "理想汽车"
    assert len(second.context.conversation_history) == 1
