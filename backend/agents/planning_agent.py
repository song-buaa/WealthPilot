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

import json
import logging
import os
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
    """根据路由决策选择 Skill 组合（静态映射，99% 场景）。"""
    return _SKILL_BUNDLES_BY_ROUTE.get(route, [])


# ════════════════════════════════════════════════
# v3.0 Step 9：LLM Skill Selector 配置
# ════════════════════════════════════════════════

_CROSS_INTENT_KEYWORD_PAIRS = [
    ("加仓", "配置"),
    ("止损", "调整"),
    ("健康", "重点"),
    ("买入", "卖出"),
]

_MULTI_ASSET_CONNECTORS = ["顺便", "另外", "还有", "和", "或者比较"]

_MACRO_KEYWORDS = [
    "美联储", "加息", "降息", "央行", "通胀", "通缩",
    "贸易战", "汇率", "GDP", "经济周期",
]

_LLM_SELECTABLE_EXTRA_SKILLS = {
    "wp-fetch-research": "宏观研究模式 - 涉及政策/经济周期等系统性问题时增补",
    "wp-propose-allocation": "配置方案生成 - 涉及资金分配决策时增补",
    "wp-calc-allocation-deviation": "偏离度计算 - 涉及组合再平衡判断时增补",
}


def _is_edge_case(user_query: str, intent_result) -> tuple[bool, str]:
    """
    检测用户问题是否为"边界场景"（需要 LLM Selector 增补 Skill）。

    5 个信号：低置信度 / 需要澄清 / 跨意图关键词 / 多标的连接词 / 宏观关键词。
    """
    if intent_result is None:
        return False, ""

    # 信号 1：低置信度（intent 可能是 dict）
    if isinstance(intent_result, dict):
        conf = intent_result.get("confidence", 1.0)
    elif hasattr(intent_result, "confidence_score"):
        conf = intent_result.confidence_score
    else:
        conf = 1.0
    if conf < 0.7:
        return True, f"low_confidence={conf:.2f}"

    # 信号 2：需要澄清
    if isinstance(intent_result, dict):
        needs_c = intent_result.get("needs_clarification", False)
    elif hasattr(intent_result, "needs_clarification"):
        needs_c = intent_result.needs_clarification
    else:
        needs_c = False
    if needs_c:
        return True, "needs_clarification"

    query = user_query or ""

    # 信号 3：跨意图关键词组合
    for kw_a, kw_b in _CROSS_INTENT_KEYWORD_PAIRS:
        if kw_a in query and kw_b in query:
            return True, f"cross_intent_keywords={kw_a}+{kw_b}"

    # 信号 4：多标的连接词
    for connector in _MULTI_ASSET_CONNECTORS:
        if connector in query:
            return True, f"multi_asset_connector={connector}"

    # 信号 5：宏观关键词
    for kw in _MACRO_KEYWORDS:
        if kw in query:
            return True, f"macro_keyword={kw}"

    return False, ""


def _llm_augment_skills(
    user_query: str,
    intent_type: str,
    base_bundle: list[str],
) -> list[str]:
    """
    边界场景：让 LLM 在 base_bundle 基础上"做加法"。
    失败兜底：返回空列表。
    """
    try:
        import openai
    except ImportError:
        return []

    candidates = []
    for skill_name, desc in _LLM_SELECTABLE_EXTRA_SKILLS.items():
        if skill_name not in base_bundle:
            candidates.append(f"- {skill_name}: {desc}")

    if not candidates:
        return []

    candidates_text = "\n".join(candidates)

    prompt = f"""用户问题包含超出标准工作流的诉求，需要在标准 Skill 组合基础上追加额外能力。

【用户问题】
{user_query}

【当前意图】
{intent_type}

【已包含的标准 Skill】
{', '.join(base_bundle) if base_bundle else '(无)'}

【可追加的额外 Skill】
{candidates_text}

请判断需要追加哪些 Skill。规则：
- 只追加确实需要的 Skill，不要为了使用而使用
- 如果标准 Skill 已经够用，返回空列表
- 不能选不在【可追加的额外 Skill】列表里的 Skill 名

输出严格 JSON 格式：
{{"extra_skills": ["wp-...", ...], "rationale": "简短理由（不超过 30 字）"}}
"""

    try:
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            timeout=10,
        )

        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        extra = data.get("extra_skills", [])

        valid_extra = [
            s for s in extra
            if s in _LLM_SELECTABLE_EXTRA_SKILLS and s not in base_bundle
        ]

        rationale = data.get("rationale", "")
        if valid_extra:
            print(f"[PlanningAgent] LLM 增补 Skills: {valid_extra}, 理由: {rationale}", flush=True)

        return valid_extra

    except Exception as e:
        print(f"[PlanningAgent] LLM Selector 异常（兜底走静态 bundle）: {e}", flush=True)
        return []


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

            # ── v3.0 Step 9：Skill Selector（静态映射 + LLM 增补兜底）──
            base_bundle = _select_skills_for_route(out.route)

            is_edge, edge_reason = _is_edge_case(user_query, out.intent)

            if not is_edge:
                out.selected_skills = base_bundle
            else:
                logger.info(
                    f"[PlanningAgent] 边界场景: {edge_reason}, 调 LLM Selector"
                )
                intent_type = ""
                if isinstance(out.intent, dict):
                    intent_type = out.intent.get("primary_intent", "")
                extra_skills = _llm_augment_skills(
                    user_query=user_query,
                    intent_type=intent_type,
                    base_bundle=base_bundle,
                )
                merged = list(base_bundle)
                for s in extra_skills:
                    if s not in merged:
                        merged.append(s)
                out.selected_skills = merged
                if extra_skills:
                    out.rationale += f" | LLM Selector 增补: {extra_skills}"

            logger.info(
                f"[PlanningAgent] task={out.task_id} route={out.route} "
                f"skills={len(out.selected_skills)} edge={is_edge}"
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
