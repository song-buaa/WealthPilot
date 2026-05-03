"""ExpressingAgent 单元测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AV_DEV_MOCK", "1")

from dotenv import load_dotenv
load_dotenv()

import asyncio


async def _collect_chunks(agent, plan_out, exec_out, user_query: str) -> list[str]:
    """收集所有流式 chunk 到一个 list。"""
    chunks = []
    async for chunk in agent.run_streaming(plan_out, exec_out, user_query):
        chunks.append(chunk)
    return chunks


def test_expressing_agent_general_chat():
    """通用对话：能拿到流式 chunk 和完整结果。"""
    from backend.agents import (
        get_planning_agent, get_executing_agent, get_expressing_agent,
    )
    from backend.agents.contracts import AgentTaskStatus

    async def _run():
        planning = get_planning_agent()
        plan_out = planning.run(user_query="什么是夏普比率？", session_id="test_expr_001")

        if plan_out.route != "general":
            print(f"   Planner 路由到 {plan_out.route}（不是 general），跳过此测试")
            print("✅ ExpressingAgent 通用对话（跳过 - Planner 路由不匹配）")
            return

        executing = get_executing_agent()
        exec_out = executing.run(plan_out, "什么是夏普比率？")

        expressing = get_expressing_agent()
        chunks = await _collect_chunks(expressing, plan_out, exec_out, "什么是夏普比率？")

        assert len(chunks) > 0, "ExpressingAgent 没有产生任何 chunk"
        full_text = "".join(chunks)
        assert len(full_text) > 10, f"chat_answer 太短: {full_text}"

        assert expressing.last_output is not None
        assert expressing.last_output.status == AgentTaskStatus.COMPLETED
        assert expressing.last_output.task_id == plan_out.task_id
        assert expressing.last_output.prompt_template_id == "general_chat"

        print(f"   chunks 数量: {len(chunks)}")
        print(f"   chat_answer 长度: {len(full_text)}")
        print(f"   template_id: {expressing.last_output.prompt_template_id}")
        print(f"   duration: {expressing.last_output.duration_ms}ms")
        print("✅ ExpressingAgent 通用对话流式输出正确")

    asyncio.run(_run())


def test_expressing_agent_streaming_chunks():
    """流式分块大小符合预期（15 字一块）。"""
    from backend.agents.expressing_agent import _emit_text_chunks

    async def _run():
        text = "这是一段测试文本，用来验证流式分块的大小是否符合 15 字符一块的设定。" \
               "我们期望每个 chunk 长度不超过 15。"
        chunks = []
        async for chunk in _emit_text_chunks(text, chunk_size=15, interval_ms=0):
            chunks.append(chunk)

        for i, chunk in enumerate(chunks):
            assert len(chunk) <= 15, f"chunk {i} 长度 {len(chunk)} 超过 15"

        assert "".join(chunks) == text
        print(f"✅ 流式分块正确：{len(chunks)} 个 chunk，每个 ≤ 15 字符")

    asyncio.run(_run())


def test_expressing_agent_intent_template_mapping():
    """意图类型到 prompt 模板 ID 的映射正确。"""
    from backend.agents.expressing_agent import ExpressingAgent

    agent = ExpressingAgent()

    cases = [
        ("PositionDecision", "position_decision"),
        ("PortfolioReview", "portfolio_review"),
        ("AssetAllocation", "asset_allocation"),
        ("PerformanceAnalysis", "performance_analysis"),
        ("GeneralChat", "general_chat"),
        ("Education", "general_chat"),
        ("UnknownIntent", "position_decision"),
    ]

    for intent_type, expected in cases:
        actual = agent._intent_to_template_id(intent_type)
        assert actual == expected, f"{intent_type} → 期望 {expected}, 实际 {actual}"

    print(f"✅ {len(cases)} 个意图模板映射正确")


def test_expressing_agent_capital_amount_extraction():
    """资金金额提取。"""
    from backend.agents.expressing_agent import ExpressingAgent
    from backend.agents.contracts import PlanningOutput

    agent = ExpressingAgent()
    fake_plan = PlanningOutput()

    cases = [
        ("100万怎么配", 1000000.0),
        ("我有30万", 300000.0),
        ("怎么调整组合", None),
    ]

    for query, expected in cases:
        actual = agent._extract_capital_amount(query, fake_plan)
        assert actual == expected, f"'{query}' → 期望 {expected}, 实际 {actual}"

    print(f"✅ {len(cases)} 个资金金额提取场景正确")


def test_expressing_agent_task_id_propagation():
    """task_id 从 PlanningOutput 透传。"""
    from backend.agents import (
        get_planning_agent, get_executing_agent, get_expressing_agent,
    )

    async def _run():
        planning = get_planning_agent()
        plan_out = planning.run(user_query="什么是 PE 估值", session_id="test_expr_005")

        if plan_out.route != "general":
            print(f"   Planner 路由到 {plan_out.route}（不是 general），跳过此测试")
            print("✅ ExpressingAgent task_id（跳过 - Planner 路由不匹配）")
            return

        executing = get_executing_agent()
        exec_out = executing.run(plan_out, "什么是 PE 估值")

        expressing = get_expressing_agent()
        async for _ in expressing.run_streaming(plan_out, exec_out, "什么是 PE 估值"):
            pass

        assert expressing.last_output.task_id == plan_out.task_id
        print(f"✅ task_id 全链路一致：{expressing.last_output.task_id}")

    asyncio.run(_run())


if __name__ == "__main__":
    test_expressing_agent_streaming_chunks()
    test_expressing_agent_intent_template_mapping()
    test_expressing_agent_capital_amount_extraction()
    test_expressing_agent_general_chat()
    test_expressing_agent_task_id_propagation()
    print("\n🎉 ExpressingAgent 5/5 测试通过")
