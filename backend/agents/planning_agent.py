"""
PlanningAgent - PEER 4 Agent 之 Planning 角色。

职责（对标 agentUniverse PlanningAgent）：
1. 读取 Memory（DecisionHistory，多轮对话上下文）
2. 意图识别（IntentRecognizer）
3. 路由决策（LLM Planner）
4. Skill 组合选择（v3.0 新增）
5. 后置守门（边界 case 修复）

设计哲学：
- 同步函数（非流式），输入 query → 输出 PlanningOutput
- 完整复用 v2.6 已稳定的 orchestrator_node 逻辑
- v3.0 新增能力：selected_skills 字段（今天用静态映射）
"""
from __future__ import annotations

import logging
from typing import Optional

from backend.agents.contracts import PlanningOutput, AgentTaskStatus

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════
# Skill 组合静态映射（v3.0 占位）
# ════════════════════════════════════════════════════════════
# 今天用静态映射建立 intent → Skills 组合的契约。
# 明天 Step 7 升级为 LLM Skill Selector：
#   读取 9 个 SKILL.md 的 description，让 LLM 动态选择组合。

_SKILL_BUNDLES_BY_ROUTE: dict[str, list[str]] = {
    # PositionDecision 单标决策：完整流程
    "position_single": [
        "wp-fetch-holdings",
        "wp-fetch-research",
        "wp-check-discipline",
        "wp-generate-signals",
        "wp-reasoning",
        "wp-citation-rules",
        "wp-output-validator",
    ],
    # 多标的决策：循环调用单标流程
    "position_multi": [
        "wp-fetch-holdings",
        "wp-fetch-research",
        "wp-check-discipline",
        "wp-generate-signals",
        "wp-reasoning",
        "wp-citation-rules",
        "wp-output-validator",
    ],
    # 组合级（PortfolioReview / AssetAllocation / PerformanceAnalysis 共用）
    "portfolio": [
        "wp-fetch-holdings",
        "wp-fetch-research",
        "wp-calc-allocation-deviation",
        "wp-propose-allocation",
        "wp-reasoning",
        "wp-citation-rules",
        "wp-output-validator",
    ],
    # GeneralChat：极简流程
    "general": [
        "wp-reasoning",
    ],
    # Clarify：不进 Executing/Expressing，由 SSE 层直接返回
    "clarify": [],
    # 低置信度：同 clarify
    "low_confidence": [],
}


def _select_skills_for_route(route: str) -> list[str]:
    """
    根据路由决策选择 Skill 组合。
    v3.0 占位实现，明天 Step 7 替换为 LLM Skill Selector。
    """
    return _SKILL_BUNDLES_BY_ROUTE.get(route, [])


# ════════════════════════════════════════════════════════════
# PlanningAgent 主类
# ════════════════════════════════════════════════════════════

class PlanningAgent:
    """
    PEER 4 Agent 之 Planning 角色。

    使用方式：
        agent = PlanningAgent()
        plan_out = agent.run(
            user_query="茅台还能拿吗",
            session_id="xxx",
            portfolio_id=1,
            conversation_history=[],
        )
        # plan_out.intent / plan_out.route / plan_out.selected_skills 可用
    """

    def __init__(self):
        pass

    def run(
        self,
        user_query: str,
        session_id: str = "",
        portfolio_id: int = 1,
        conversation_history: Optional[list[dict]] = None,
    ) -> PlanningOutput:
        """
        执行 Planning 阶段。

        步骤：
        1. 创建 PlanningOutput（task_id 自动生成）
        2. 调用现有 orchestrator_node 逻辑得到 route + intent + sse_handler
        3. 根据 route 选择 Skill 组合
        4. 填充 PlanningOutput 并返回
        """
        out = PlanningOutput()
        out.status = AgentTaskStatus.IN_PROGRESS
        out.portfolio_id = portfolio_id
        out.memory_context = conversation_history or []

        try:
            raw_result = self._invoke_orchestrator(
                user_query=user_query,
                session_id=session_id,
                portfolio_id=portfolio_id,
                conversation_history=conversation_history or [],
            )

            out.intent = raw_result.get("intent_payload")
            out.route = raw_result.get("route", "")
            out.sse_handler = raw_result.get("sse_handler", "")
            out.rationale = raw_result.get("planner_rationale", "")
            out.multi_assets = raw_result.get("multi_assets", [])
            out.needs_clarification = raw_result.get("needs_clarification", False)
            out.candidate_holdings = raw_result.get("candidate_holdings", [])

            # v3.0 新增：选择 Skill 组合
            out.selected_skills = _select_skills_for_route(out.route)

            logger.info(
                f"[PlanningAgent] task={out.task_id} route={out.route} "
                f"skills={len(out.selected_skills)} duration_planning={out.duration_ms}ms"
            )

            out.mark_completed()
            return out

        except Exception as e:
            logger.exception(f"[PlanningAgent] task={out.task_id} 异常: {e}")
            out.mark_failed(str(e))
            return out

    def _invoke_orchestrator(
        self,
        user_query: str,
        session_id: str,
        portfolio_id: int,
        conversation_history: list[dict],
    ) -> dict:
        """
        调用现有 LangGraph 编排逻辑得到路由决策。

        v3.0 阶段：复用 orchestrator_node 的全部逻辑。
        v3.1 演进：把这段逻辑直接迁移到 PlanningAgent 内部，废弃 LangGraph orchestrator_node。
        """
        from backend.graph.decision_graph import decision_graph

        initial_state = {
            "user_query": user_query,
            "session_id": session_id,
            "conversation_history": conversation_history or [],
            "portfolio_id": portfolio_id,
            "intent": "",
            "routing_plan": None,
            "target_symbols": [],
            "needs_clarification": False,
            "candidate_holdings": [],
            "research_cards": [],
            "discipline_violations": [],
            "allocation_deviation": None,
            "decision_result": None,
            "validation_result": None,
            "validation_log": [],
            "agents_invoked": [],
            "tool_calls": [],
            "planner_rationale": "",
            "route": "",
            "intent_payload": None,
            "multi_assets": [],
            "all_positions": [],
            "sse_handler": "",
            "sse_kwargs": {},
        }

        config = {"configurable": {"thread_id": session_id}}
        result_state = decision_graph.invoke(initial_state, config)

        return {
            "intent_payload": result_state.get("intent_payload"),
            "route": result_state.get("route", ""),
            "sse_handler": result_state.get("sse_handler", ""),
            "planner_rationale": result_state.get("planner_rationale", ""),
            "multi_assets": result_state.get("multi_assets", []),
            "needs_clarification": result_state.get("needs_clarification", False),
            "candidate_holdings": result_state.get("candidate_holdings", []),
        }


# ════════════════════════════════════════════════════════════
# 模块级别快捷函数（兼容性 API）
# ════════════════════════════════════════════════════════════

_default_agent: Optional[PlanningAgent] = None


def get_planning_agent() -> PlanningAgent:
    """获取全局 PlanningAgent 单例。"""
    global _default_agent
    if _default_agent is None:
        _default_agent = PlanningAgent()
    return _default_agent
