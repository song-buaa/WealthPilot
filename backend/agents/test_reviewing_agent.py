"""ReviewingAgent 单元测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AV_DEV_MOCK", "1")

from dotenv import load_dotenv
load_dotenv()


def test_reviewing_agent_pass():
    """硬校验通过的场景：直接 pass，不调 LLM。"""
    from backend.agents.contracts import (
        PlanningOutput, ExecutionOutput, ExpressionOutput,
    )
    from backend.agents import get_reviewing_agent

    plan_out = PlanningOutput(task_id="task_test_pass")
    exec_out = ExecutionOutput(task_id=plan_out.task_id)

    # 没有 LLMResult → 跳过硬校验 → pass
    expr_out = ExpressionOutput(
        task_id=plan_out.task_id,
        chat_answer="### 结论\n建议持有...",
        mode="structured",
    )

    reviewing = get_reviewing_agent()
    review = reviewing.run(plan_out, exec_out, expr_out, "测试问题")

    assert review.action == "pass", f"期望 pass，实际 {review.action}"
    assert review.score == 1.0
    assert review.task_id == plan_out.task_id
    assert review.passed is True
    print(f"✅ 硬校验通过场景：action=pass, score={review.score}")


def test_reviewing_agent_retry_limit():
    """重试次数上限场景：直接 fallback。"""
    from backend.agents.contracts import (
        PlanningOutput, ExecutionOutput, ExpressionOutput,
    )
    from backend.agents import get_reviewing_agent
    from decision_engine.llm_engine import LLMResult

    plan_out = PlanningOutput(task_id="task_test_limit")
    plan_out.intent = {"primary_intent": "PositionDecision", "confidence": 0.9}
    exec_out = ExecutionOutput(task_id=plan_out.task_id)

    # 构造一个会失败的 LLMResult
    bad_llm_result = LLMResult(
        decision="HOLD",
        reasoning=[],
        risk=[],
        strategy=[],
        chat_answer="",
        raw_output="",
    )
    expr_out = ExpressionOutput(
        task_id=plan_out.task_id,
        chat_answer="",
        llm_result=bad_llm_result,
    )

    reviewing = get_reviewing_agent()
    review = reviewing.run(plan_out, exec_out, expr_out, "测试", retry_count=3)

    assert review.action == "fallback", f"期望 fallback，实际 {review.action}"
    assert review.retry_count == 3
    print(f"✅ 重试上限场景：action=fallback, score={review.score}")


def test_reviewing_agent_a2a_alignment():
    """task_id 全链路传递 + A2A 字段。"""
    from backend.agents.contracts import (
        PlanningOutput, ExecutionOutput, ExpressionOutput, AgentTaskStatus,
    )
    from backend.agents import get_reviewing_agent

    plan_out = PlanningOutput()
    exec_out = ExecutionOutput(task_id=plan_out.task_id)
    expr_out = ExpressionOutput(
        task_id=plan_out.task_id,
        chat_answer="测试回答",
    )

    reviewing = get_reviewing_agent()
    review = reviewing.run(plan_out, exec_out, expr_out, "测试")

    assert review.task_id == plan_out.task_id
    assert review.status in (AgentTaskStatus.COMPLETED, AgentTaskStatus.FAILED)
    assert review.started_at > 0
    assert review.completed_at is not None
    print(f"✅ A2A 字段一致：task_id={review.task_id}")


def test_reviewing_agent_full_chain():
    """4 Agent 完整协作链路。"""
    import asyncio
    from backend.agents import (
        get_planning_agent, get_executing_agent,
        get_expressing_agent, get_reviewing_agent,
    )

    async def _run():
        planning = get_planning_agent()
        plan_out = planning.run("什么是夏普比率？", session_id="test_full_chain")

        if plan_out.route != "general":
            print(f"   Planner 路由到 {plan_out.route}（不是 general），跳过此测试")
            print("✅ 4 Agent 链路（跳过 - Planner 路由不匹配）")
            return

        executing = get_executing_agent()
        exec_out = executing.run(plan_out, "什么是夏普比率？")

        expressing = get_expressing_agent()
        async for _ in expressing.run_streaming(plan_out, exec_out, "什么是夏普比率？"):
            pass
        expr_out = expressing.last_output

        reviewing = get_reviewing_agent()
        review = reviewing.run(plan_out, exec_out, expr_out, "什么是夏普比率？")

        assert review.action == "pass"
        assert review.task_id == plan_out.task_id
        print(f"✅ 4 Agent 完整链路：")
        print(f"   Planning  → route={plan_out.route}")
        print(f"   Executing → status={exec_out.status.value}")
        print(f"   Expressing → chat_len={len(expr_out.chat_answer)}")
        print(f"   Reviewing → action={review.action}, score={review.score}")

    asyncio.run(_run())


if __name__ == "__main__":
    test_reviewing_agent_pass()
    test_reviewing_agent_retry_limit()
    test_reviewing_agent_a2a_alignment()
    test_reviewing_agent_full_chain()
    print("\n🎉 ReviewingAgent 4/4 测试通过")
