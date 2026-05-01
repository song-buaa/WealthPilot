"""
WealthPilot Decision Graph (v2.6 M1.2 骨架)

当前状态：骨架文件，仅定义 DecisionState。
M1.3 阶段将在此文件实现完整的 LangGraph StateGraph，
包含 7 个 Agent 节点 + LLM Planner + 条件路由。

M1.2 不在此文件做任何实际调用，decision_service.py 的主逻辑不变。
"""

from typing import TypedDict, Optional, Annotated
from langgraph.graph import add_messages


class DecisionState(TypedDict):
    """
    LangGraph StateGraph 的全局状态定义。
    M1.3 开始各 Agent 节点会读写此 State。
    """
    # ── 输入 ──
    user_query: str
    session_id: str
    conversation_history: Annotated[list, add_messages]

    # ── OrchestratorAgent 填充（M1.3）──
    intent: str           # PositionDecision / PortfolioReview / ...
    routing_plan: Optional[dict]   # LLM Planner 生成的 DAG（M1.3）
    target_symbols: list[str]
    needs_clarification: bool
    candidate_holdings: list[dict]  # 候选标的列表（M1.2 候选清单用）

    # ── ResearchAgent 填充（M1.3）──
    research_cards: list[dict]

    # ── DisciplineAgent 填充（M1.3）──
    discipline_violations: list[dict]

    # ── AllocationAgent 填充（M1.3）──
    allocation_deviation: Optional[dict]

    # ── PositionDecisionAgent 填充（M1.3）──
    decision_result: Optional[dict]

    # ── DecisionValidator 填充（M1.3）──
    validation_result: Optional[dict]
    validation_log: list[dict]

    # ── 调试与评测 ──
    agents_invoked: list[str]
    tool_calls: list[dict]
    planner_rationale: str
