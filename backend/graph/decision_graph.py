"""
WealthPilot Decision Graph (v2.6 M1.3 Step 2)

StateGraph 实现：LLM Planner 路由 + SSE handler 映射。
orchestrator_node 做两次调用：
1. IntentRecognizer：意图识别
2. LLM Planner：生成 RoutingPlan（含 rationale）

Planner 失败时降级到规则路由（与 Step 1 逻辑一致）。
"""

import logging
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END

from intent_engine import intent_recognizer

logger = logging.getLogger(__name__)
from backend.services.decision_service import (
    _detect_feature_type,
    _is_asset_clear,
    _is_asset_unambiguous,
    _is_asset_in_portfolio,
)

# 组合级意图集合：这些意图分析的是整个组合而非单个标的，
# 不需要标的消歧（clarify），不应被对话历史中的个股上下文污染路由。
# 根因参考：v3.2 诊断发现 Decision 入口的对话历史会让 LLM Planner
# 把 AssetAllocation 误路由到 clarify/position_single。
PORTFOLIO_LEVEL_INTENTS = {"AssetAllocation", "PortfolioReview", "PerformanceAnalysis"}

# 模糊标的词（与 decision_service.VAGUE_ASSET_WORDS 保持一致）
_VAGUE_WORDS = [
    "股票", "基金", "标的", "持仓", "资产", "仓位",
    "这只", "那只", "某只", "一只", "一个", "这个", "那个",
]


class DecisionState(TypedDict):
    """LangGraph StateGraph 的全局状态定义。"""
    # ── 输入 ──
    user_query: str
    conversation_id: str
    conversation_history: list

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
    all_positions: list   # AggregatedPosition 对象列表

    # ── 执行结果（供外层 SSE 层消费）──
    sse_handler: str
    sse_kwargs: dict


# ── M2.5 方案 B：追问守门函数 ──────────────────────────────────────────


def _check_followup_continuation(
    conversation_history: list[dict],
    current_query: str,
    position_names: list[str],
) -> str | None:
    """
    检测当前消息是否是对上一轮讨论标的的追问。

    返回 None = 不继承；返回 asset 名称 = 应继承为 PositionDecision。
    优先用真实持仓列表（position_names），兜底用硬编码关键词。
    """
    # 找上一轮 user 消息
    last_user_content = ""
    for turn in reversed(conversation_history):
        if turn.get("role") == "user":
            last_user_content = turn.get("content", "")
            break

    if not last_user_content:
        return None

    # 候选标的列表：优先用真实持仓名
    candidates = list(position_names) if position_names else []
    # 兜底：常见中英文标的名（仅在 position_names 为空时使用）
    if not candidates:
        candidates = [
            "微软", "MSFT", "理想", "LI", "Coinbase", "COIN",
            "腾讯", "00700", "美团", "03690", "谷歌", "GOOGL",
            "苹果", "AAPL", "英伟达", "NVDA", "特斯拉", "TSLA",
        ]

    # 上一轮和当前都提到的标的 → 追问
    for name in candidates:
        if name in last_user_content and name in current_query:
            return name

    return None


# ── 节点 ──────────────────────────────────────────────────────────────────


def orchestrator_node(state: DecisionState) -> dict:
    """
    OrchestratorNode（LLM Planner 版本）。

    先用 IntentRecognizer 做意图识别（已有能力），
    再做一次轻量 LLM call 生成 RoutingPlan（含 rationale），
    最后根据 plan 决定路由。

    为什么不只用 IntentRecognizer？
    IntentRecognizer 输出 intent 类别，但不给出"为什么选这条路"的理由。
    LLM Planner 的 rationale 字段是 Eval L2 评测和可解释性的关键。
    """
    import json
    from intent_engine._llm_client import get_client, MODEL_MAIN

    user_query = state["user_query"]
    conversation_id = state["conversation_id"]
    conversation_history = state.get("conversation_history", [])
    portfolio_id = state.get("portfolio_id", 1)
    all_positions = state.get("all_positions", [])

    # Step 1: 意图识别
    position_names = [p.name for p in all_positions] if all_positions else []
    payload, clarification = intent_recognizer.recognize(
        user_query,
        conversation_history=conversation_history or None,
        position_names=position_names or None,
    )

    intent = payload.primary_intent
    confidence = payload.confidence
    entities = payload.entities
    asset = entities.asset if entities else None
    multi_assets = list(entities.multi_assets or []) if entities else []

    # M7: asset 清洗（去掉粘连的类型词后缀，如 "000001基金" → "000001"）
    if asset:
        from backend.services.fund_classifier import normalize_asset_name
        original_asset = asset
        asset = normalize_asset_name(original_asset)
        if asset != original_asset:
            if entities:
                entities.asset = asset
            print(f"[M7] asset 清洗: '{original_asset}' → '{asset}'", flush=True)

    # === M2.5 方案 B：追问守门 ===
    # 防止 IntentRecognizer 把追问误判为低置信度 Education
    # 如果有对话历史 + 当前问句提到了上一轮讨论的标的 → 强制继承路由
    if confidence < 0.5 and conversation_history and len(conversation_history) >= 2:
        inherited = _check_followup_continuation(
            conversation_history, user_query, position_names,
        )
        if inherited:
            inherited_asset = inherited
            intent = "PositionDecision"
            confidence = 0.85
            asset = inherited_asset
            if entities:
                entities.asset = inherited_asset
            logger.info(
                f"[intent.followup_guard] 追问守门触发: "
                f"继承 intent=PositionDecision, asset={inherited_asset}, confidence=0.85"
            )

    # 低置信度直接走澄清，不需要 LLM Planner
    if confidence < 0.5:
        return {
            "route": "low_confidence",
            "intent_payload": {
                "primary_intent": intent,
                "confidence": confidence,
                "asset": asset,
                "action_type": payload.actions[0] if payload.actions else None,
                "actions": list(payload.actions) if payload.actions else [],
            },
            "sse_handler": "low_confidence",
            "sse_kwargs": {"clarify_question": clarification},
            "planner_rationale": f"置信度 {confidence} < 0.5，直接走澄清路径",
            "agents_invoked": ["orchestrator"],
        }

    # Step 2: LLM Planner call（生成 RoutingPlan + rationale）
    asset_unambiguous = _is_asset_unambiguous(asset)
    asset_in_portfolio = _is_asset_in_portfolio(asset, all_positions)

    planner_prompt = f"""你是 WealthPilot 决策系统的编排者（Planner）。
根据用户输入和意图识别结果，决定本次请求应该走哪条处理路径。

用户输入：{user_query}
意图识别结果：{intent}（置信度 {confidence:.2f}）
识别到的标的：{asset or "未识别"}
标的名称是否明确：{asset_unambiguous}
标的是否在持仓中：{asset_in_portfolio}
多标的：{multi_assets if multi_assets else "无"}

可选路径：
- position_single：单标的决策（标的明确的 PositionDecision，包括已持仓的加减仓和非持仓的新建仓评估）
- position_multi：多标的对比决策
- clarify：标的不明确（用户说的是模糊指代如"那只股票"），需要展示候选清单让用户选择
- portfolio：组合级分析（PortfolioReview / AssetAllocation / PerformanceAnalysis）
- general：通用教育/对话（Education / GeneralChat）

判断要点：
- "标的名称是否明确"用于判断用户的描述是否清晰（避免"那只股票"这种模糊指代）
- "标的是否在持仓中"用于判断是已有持仓决策还是新建仓评估
- 名称明确 + 在持仓中 → position_single（已持仓的加减仓决策）
- 名称明确 + 不在持仓中 → position_single（新建仓评估）
- 名称不明确 → clarify（让用户澄清）

请输出 JSON，格式如下（只输出 JSON，不要有其他文字）：
{{
  "route": "<路径名>",
  "rationale": "<用一句话说明为什么选这条路径，要基于用户输入的具体内容>"
}}"""

    try:
        client = get_client()
        response = client.chat.completions.create(
            model=MODEL_MAIN,
            messages=[{"role": "user", "content": planner_prompt}],
            temperature=0,
            max_tokens=200,
            timeout=10,
        )
        raw = response.choices[0].message.content.strip()
        plan = json.loads(raw)
        route = plan.get("route", "general")
        rationale = plan.get("rationale", "")
    except Exception as e:
        # Planner 失败时降级到规则路由
        route = _intent_to_route_fallback(intent, asset, asset_unambiguous, multi_assets)
        rationale = f"Planner 调用失败（{e}），降级到规则路由"

    # 后置校验：操作关键词 + 模糊标的 → 重路由到 clarify
    # 豁免 1：Education 且无明确标的（纯语义描述行为习惯，不触发）
    # 豁免 2：组合级意图（PortfolioReview/PerformanceAnalysis/AssetAllocation）完全跳过
    if route == "general" and intent not in PORTFOLIO_LEVEL_INTENTS:
        _OP_KW = ["加仓", "减仓", "买入", "卖出", "止损", "止盈", "落袋", "清仓", "建仓"]
        has_op = any(k in user_query for k in _OP_KW)
        has_vague = any(v in user_query for v in _VAGUE_WORDS)
        # Education 无明确标的时跳过（用户在描述行为习惯，不需要澄清）
        if has_op and has_vague and not (intent == "Education" and not asset):
            route = "clarify"
            rationale += "（含操作关键词+模糊标的，重路由到澄清）"

    # 组合级意图守门：AssetAllocation / PortfolioReview / PerformanceAnalysis
    # 强制走 portfolio 路由，不允许被 LLM Planner 或对话历史误路由到个股路径。
    # 根因：Decision 入口复用 session 时，对话历史中的个股上下文会让 LLM Planner
    # 把 AssetAllocation 误判为 clarify 或 position_single。
    if intent in PORTFOLIO_LEVEL_INTENTS and route not in ("portfolio", "general"):
        original_route = route
        route = "portfolio"
        rationale += f"（{intent} 是组合级意图，强制走 portfolio 路由，原 LLM 推荐 {original_route}）"

    # M7 守门：用户明确说基金 + asset 是 6 位代码 → 强制 position_single
    if (route == "clarify"
        and intent == "PositionDecision"
        and asset and asset.isdigit() and len(asset) == 6):
        from backend.services.fund_classifier import is_likely_fund
        _is_fund, _fund_reason = is_likely_fund(asset, all_positions, user_query)
        if _is_fund:
            route = "position_single"
            rationale += f" | M7 守门：{_fund_reason}，强制走单标决策"
            print(f"[M7] Planner 守门生效: {asset} → position_single", flush=True)

    # 根据 route 构建 sse_kwargs
    sse_handler, sse_kwargs = _build_sse_kwargs(
        route, user_query, conversation_id, portfolio_id, multi_assets
    )

    return {
        "route": route,
        "intent_payload": {
            "primary_intent": intent,
            "confidence": confidence,
            "asset": asset,
            "action_type": payload.actions[0] if payload.actions else None,
            "actions": list(payload.actions) if payload.actions else [],
        },
        "multi_assets": multi_assets,
        "sse_handler": sse_handler,
        "sse_kwargs": sse_kwargs,
        "planner_rationale": rationale,
        "agents_invoked": ["orchestrator"],
    }


def _intent_to_route_fallback(
    intent: str, asset: str | None, asset_unambiguous: bool, multi_assets: list
) -> str:
    """Planner 失败时的规则降级路由。

    v3.4 M8.1: 参数从 asset_clear 改为 asset_unambiguous。
    只要标的名称明确(无论是否在持仓),就走 position_single。
    """
    if intent == "PositionDecision":
        if multi_assets and len(multi_assets) >= 2:
            return "position_multi"
        if asset_unambiguous:
            return "position_single"
        return "clarify"
    if intent in ("PortfolioReview", "AssetAllocation", "PerformanceAnalysis"):
        return "portfolio"
    return "general"


def _build_sse_kwargs(
    route: str,
    user_input: str,
    conversation_id: str,
    portfolio_id: int,
    multi_assets: list,
) -> tuple[str, dict]:
    """根据 route 返回 (sse_handler, sse_kwargs)。"""
    if route == "position_single":
        return "_stream_position_decision", {
            "user_input": user_input,
            "conversation_id": conversation_id,
            "portfolio_id": portfolio_id,
        }
    if route == "position_multi":
        return "_stream_multi_asset", {
            "user_input": user_input,
            "multi_assets": multi_assets,
            "conversation_id": conversation_id,
            "portfolio_id": portfolio_id,
        }
    if route == "portfolio":
        return "_stream_portfolio_intent", {
            "user_input": user_input,
            "conversation_id": conversation_id,
            "portfolio_id": portfolio_id,
        }
    if route == "clarify":
        return "_build_clarification_reply", {
            "user_input": user_input,
            "feature_type": _detect_feature_type(user_input),
        }
    # general / 兜底
    return "_stream_general_chat", {
        "user_input": user_input,
        "conversation_id": conversation_id,
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

    import os
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver

    _ckpt_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "checkpoints.db",
    )
    os.makedirs(os.path.dirname(_ckpt_path), exist_ok=True)
    _conn = sqlite3.connect(_ckpt_path, check_same_thread=False)
    checkpointer = SqliteSaver(_conn)

    return graph.compile(checkpointer=checkpointer)


# 模块级单例
decision_graph = build_decision_graph()
