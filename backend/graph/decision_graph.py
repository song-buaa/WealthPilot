"""
WealthPilot Decision Graph (v2.6 M1.3 Step 1)

StateGraph 实现：把 decision_service.py 的 6 条分支映射为固定路由。
节点内部不直接调用 _stream_* 子函数（它们是 async generator），
而是填充 sse_handler + sse_kwargs，供外层 SSE 层消费。

M1.3 Step 2 将加入 LLM Planner 和动态路由。
"""

from typing import TypedDict, Optional, Annotated
from langgraph.graph import StateGraph, END, add_messages

from intent_engine import intent_recognizer
from backend.services.decision_service import (
    _detect_feature_type,
    _is_asset_clear,
)


class DecisionState(TypedDict):
    """LangGraph StateGraph 的全局状态定义。"""
    # ── 输入 ──
    user_query: str
    session_id: str
    conversation_history: Annotated[list, add_messages]

    # ── OrchestratorAgent 填充 ──
    intent: str
    routing_plan: Optional[dict]
    target_symbols: list[str]
    needs_clarification: bool
    candidate_holdings: list[dict]

    # ── ResearchAgent 填充（M1.3+）──
    research_cards: list[dict]

    # ── DisciplineAgent 填充（M1.3+）──
    discipline_violations: list[dict]

    # ── AllocationAgent 填充（M1.3+）──
    allocation_deviation: Optional[dict]

    # ── PositionDecisionAgent 填充（M1.3+）──
    decision_result: Optional[dict]

    # ── DecisionValidator 填充（M1.3+）──
    validation_result: Optional[dict]
    validation_log: list[dict]

    # ── 调试与评测 ──
    agents_invoked: list[str]
    tool_calls: list[dict]
    planner_rationale: str

    # ── 路由决策（OrchestratorNode 填充）──
    route: str
    intent_payload: Optional[dict]
    multi_assets: list[str]
    portfolio_id: int

    # ── 执行结果（供外层 SSE 层消费）──
    sse_handler: str
    sse_kwargs: dict


# ── 节点 ──────────────────────────────────────────────────────────────────


def _payload_to_dict(payload) -> dict:
    """安全地把 IntentPayload dataclass 转成 dict。"""
    if hasattr(payload, "__dict__"):
        d = {}
        d["primary_intent"] = payload.primary_intent
        d["confidence"] = payload.confidence
        d["actions"] = payload.actions
        if payload.entities:
            d["asset"] = payload.entities.asset
            d["multi_assets"] = payload.entities.multi_assets
            d["time_horizon"] = payload.entities.time_horizon
        return d
    return {}


def orchestrator_node(state: DecisionState) -> dict:
    """
    OrchestratorNode：意图识别 + 路由决策。
    对应 decision_service.py 的 Stage 0-1 + 路由分发逻辑。
    """
    user_query = state["user_query"]
    session_id = state["session_id"]
    conversation_history = state.get("conversation_history", [])
    portfolio_id = state.get("portfolio_id", 1)

    # 意图识别
    payload, clarify_question = intent_recognizer.recognize(
        user_query,
        conversation_history=conversation_history or None,
        position_names=None,
    )

    intent = payload.primary_intent
    confidence = payload.confidence
    asset = payload.entities.asset if payload.entities else None
    multi_assets = list(payload.entities.multi_assets) if payload.entities else []
    payload_dict = _payload_to_dict(payload)

    # 低置信度 → 直接澄清
    if confidence < 0.5:
        return {
            "route": "low_confidence",
            "intent": intent,
            "intent_payload": payload_dict,
            "sse_handler": "low_confidence",
            "sse_kwargs": {"clarify_question": clarify_question},
        }

    # PositionDecision 路由
    if intent == "PositionDecision":
        if multi_assets and len(multi_assets) >= 2:
            return {
                "route": "position_multi",
                "intent": intent,
                "intent_payload": payload_dict,
                "multi_assets": multi_assets,
                "sse_handler": "_stream_multi_asset",
                "sse_kwargs": {
                    "user_input": user_query,
                    "multi_assets": multi_assets,
                    "session_id": session_id,
                    "portfolio_id": portfolio_id,
                },
            }

        # asset 明确性检查（positions 列表暂简化为空，Step 2 完善）
        if asset and not any(
            v == asset.strip() for v in [
                "股票", "基金", "标的", "持仓", "资产", "仓位",
                "这只", "那只", "某只", "一只", "一个", "这个", "那个",
            ]
        ):
            return {
                "route": "position_single",
                "intent": intent,
                "intent_payload": payload_dict,
                "sse_handler": "_stream_position_decision",
                "sse_kwargs": {
                    "user_input": user_query,
                    "session_id": session_id,
                    "portfolio_id": portfolio_id,
                },
            }

        # asset 不明确 → 候选清单
        return {
            "route": "clarify",
            "intent": intent,
            "intent_payload": payload_dict,
            "sse_handler": "_build_clarification_reply",
            "sse_kwargs": {
                "user_input": user_query,
                "feature_type": _detect_feature_type(user_query),
            },
        }

    # 组合级意图
    if intent in ("PortfolioReview", "AssetAllocation", "PerformanceAnalysis"):
        return {
            "route": "portfolio",
            "intent": intent,
            "intent_payload": payload_dict,
            "sse_handler": "_stream_portfolio_intent",
            "sse_kwargs": {
                "user_input": user_query,
                "session_id": session_id,
                "portfolio_id": portfolio_id,
            },
        }

    # Education / GeneralChat
    return {
        "route": "general",
        "intent": intent,
        "intent_payload": payload_dict,
        "sse_handler": "_stream_general_chat",
        "sse_kwargs": {
            "user_input": user_query,
            "session_id": session_id,
        },
    }


# ── 条件边 ────────────────────────────────────────────────────────────────


def route_after_orchestrator(state: DecisionState) -> str:
    """条件边：根据 route 字段决定下一个节点。"""
    return state.get("route", "general")


# ── 构建 StateGraph ──────────────────────────────────────────────────────


def build_decision_graph():
    graph = StateGraph(DecisionState)

    graph.add_node("orchestrator", orchestrator_node)
    graph.set_entry_point("orchestrator")

    # 所有路径都直接结束 graph，SSE 由外层处理
    graph.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "position_single": END,
            "position_multi": END,
            "portfolio": END,
            "general": END,
            "clarify": END,
            "low_confidence": END,
        },
    )

    return graph.compile()


# 模块级单例
decision_graph = build_decision_graph()
