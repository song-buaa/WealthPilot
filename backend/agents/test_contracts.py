"""4 个 Agent 数据契约的单元测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AV_DEV_MOCK", "1")

import time


def test_planning_output_basic():
    from backend.agents.contracts import PlanningOutput, AgentTaskStatus

    out = PlanningOutput()
    assert out.task_id.startswith("task_")
    assert out.status == AgentTaskStatus.PENDING
    assert out.duration_ms is None  # 还没完成

    time.sleep(0.01)
    out.mark_completed()
    assert out.status == AgentTaskStatus.COMPLETED
    assert out.duration_ms is not None and out.duration_ms >= 10
    print("✅ PlanningOutput 基本字段和状态机正确")


def test_planning_output_skill_selection():
    from backend.agents.contracts import PlanningOutput

    out = PlanningOutput(
        route="position_single",
        rationale="用户问单标的决策",
        selected_skills=[
            "wp-fetch-holdings",
            "wp-fetch-research",
            "wp-check-discipline",
            "wp-generate-signals",
            "wp-reasoning",
        ],
    )
    assert len(out.selected_skills) == 5
    assert "wp-fetch-research" in out.selected_skills
    print(f"✅ PlanningOutput 选择 {len(out.selected_skills)} 个 Skills")


def test_execution_output_abort():
    from backend.agents.contracts import ExecutionOutput, AgentTaskStatus

    out = ExecutionOutput()
    out.mark_aborted("未持仓且非买入意图", "您当前未持有该标的，请明确意图")
    assert out.aborted is True
    assert out.status == AgentTaskStatus.SKIPPED
    assert out.abort_chat_answer != ""
    print("✅ ExecutionOutput abort 状态正确")


def test_expression_output_streaming():
    from backend.agents.contracts import ExpressionOutput

    out = ExpressionOutput(
        prompt_template_id="position_decision",
        citation_rules_applied=["wp-citation-rules"],
        chat_answer="### 结论\n建议持有...",
        mode="structured",
    )
    assert out.prompt_template_id == "position_decision"
    assert "wp-citation-rules" in out.citation_rules_applied
    print("✅ ExpressionOutput trace 字段正确")


def test_review_output_scoring():
    from backend.agents.contracts import ReviewOutput

    # 通过场景
    out_pass = ReviewOutput(score=0.85, action="pass")
    assert out_pass.passed is True

    # 重试场景（轻度问题，只重试 Expressing）
    out_retry = ReviewOutput(
        score=0.6,
        action="retry",
        jump_step="expressing",
        score_rationale="引用规则部分缺失",
    )
    assert out_retry.passed is False
    assert out_retry.jump_step == "expressing"

    # 重试场景（重度问题，从 Executing 重新开始）
    out_retry_deep = ReviewOutput(
        score=0.3,
        action="retry",
        jump_step="executing",
    )
    assert out_retry_deep.jump_step == "executing"

    # 降级场景
    out_fallback = ReviewOutput(score=0.2, action="fallback")
    assert out_fallback.passed is False

    print("✅ ReviewOutput 评分机制 4 种场景正确")


def test_a2a_alignment():
    """验证字段对齐 A2A 协议。"""
    from backend.agents.contracts import (
        PlanningOutput, ExecutionOutput, ExpressionOutput, ReviewOutput,
        AgentTaskStatus,
    )

    # 4 个 Output 都有 A2A 对齐字段
    for cls in [PlanningOutput, ExecutionOutput, ExpressionOutput, ReviewOutput]:
        instance = cls()
        assert hasattr(instance, "task_id"), f"{cls.__name__} 缺少 task_id"
        assert hasattr(instance, "status"), f"{cls.__name__} 缺少 status"
        assert hasattr(instance, "started_at"), f"{cls.__name__} 缺少 started_at"
        assert hasattr(instance, "completed_at"), f"{cls.__name__} 缺少 completed_at"
        assert hasattr(instance, "error"), f"{cls.__name__} 缺少 error"

    # AgentTaskStatus 5 个状态对齐 A2A 标准
    expected_statuses = {"pending", "in_progress", "completed", "failed", "skipped"}
    actual_statuses = {s.value for s in AgentTaskStatus}
    assert actual_statuses == expected_statuses

    print("✅ 4 个 Output 字段全部对齐 A2A 协议")


def test_task_id_propagation():
    """验证 task_id 可以从 PlanningOutput 传递到下游。"""
    from backend.agents.contracts import (
        PlanningOutput, ExecutionOutput, ExpressionOutput, ReviewOutput,
    )

    plan = PlanningOutput()
    tid = plan.task_id

    exec_out = ExecutionOutput(task_id=tid)
    expr_out = ExpressionOutput(task_id=tid)
    review_out = ReviewOutput(task_id=tid)

    assert exec_out.task_id == plan.task_id == tid
    assert expr_out.task_id == tid
    assert review_out.task_id == tid
    print(f"✅ task_id 全链路传递: {tid}")


if __name__ == "__main__":
    test_planning_output_basic()
    test_planning_output_skill_selection()
    test_execution_output_abort()
    test_expression_output_streaming()
    test_review_output_scoring()
    test_a2a_alignment()
    test_task_id_propagation()
    print("\n🎉 4 Agent 数据契约 7/7 测试通过")
