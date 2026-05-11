"""
ExpressingAgent - PEER 4 Agent 之 Expressing 角色。

职责（对标 agentUniverse ExpressingAgent）：
1. 加载意图对应的 prompt 模板
2. 注入输出规范 Skills（wp-citation-rules）的内容
3. 调用 LLM（GPT-4.1）生成结构化输出
4. 流式 yield chat_answer 给用户
5. 返回完整 LLMResult / GenericLLMResult

设计哲学：
- 唯一的 AsyncGenerator Agent（流式输出）
- 调用现有 llm_engine 函数（reason / review_portfolio / analyze_allocation 等）
- 流式分块由 _emit_text_chunks 处理（与 v2.6 _stream_text 行为一致）
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import AsyncGenerator, Optional

from backend.agents.contracts import (
    PlanningOutput,
    ExecutionOutput,
    ExpressionOutput,
    AgentTaskStatus,
)

logger = logging.getLogger(__name__)

# 宏观关键词（与 PlanningAgent._MACRO_KEYWORDS 保持一致）
_MACRO_KEYWORDS = [
    "美联储", "加息", "降息", "央行", "通胀", "通缩",
    "贸易战", "汇率", "GDP", "经济周期",
]

_MACRO_ANALYSIS_INSTRUCTION = """
重要补充指令——用户问题涉及宏观经济事件:
请在你的分析中,优先围绕用户提到的具体宏观事件展开:
1. **事件影响传导**: 该宏观事件如何具体影响用户当前持仓的不同资产类别(权益/固收/另类等),给出具体的影响方向和幅度估计
2. **历史参照**: 类似宏观事件历史上对类似配置组合产生过什么影响（如有数据支撑则引用）
3. **针对性调整**: 给出针对此宏观事件的具体调整建议,精确到资产类别比例或具体持仓的增减,不要给笼统的"再平衡"建议
4. **不可替代性**: 你的分析必须围绕用户问的宏观事件,不能用通用组合评估模板敷衍

回答时仍按"组合现状/结构分析/市场背景/主要风险/调整建议"5 段结构,
但每一段都要紧扣用户问的宏观事件,而不是泛泛而谈。
"""


# ════════════════════════════════════════════════════════════
# v3.2 actionable 硬规则判断
# ════════════════════════════════════════════════════════════

# 这些 decisionType 代表明确的交易操作建议，适合生成行动清单
_ACTIONABLE_DECISION_TYPES = {"buy_init", "buy_more", "trim", "exit"}

_ACTIONABLE_HINTS = {
    "buy_init": "识别到建仓建议",
    "buy_more": "识别到加仓建议",
    "trim": "识别到减仓建议",
    "exit": "识别到清仓建议",
}


def _is_actionable(expr_output) -> tuple[bool, str | None]:
    """
    基于 ExpressionOutput 的 structured_payload 判断是否可生成行动清单。

    硬规则实现（不调 LLM）：
    - 从 structured_payload 中提取 decisionType
    - 若 decisionType ∈ _ACTIONABLE_DECISION_TYPES → (True, hint)
    - 否则 → (False, None)
    """
    payload = getattr(expr_output, "structured_payload", None)
    if not payload or not isinstance(payload, dict):
        return False, None

    decision_type = payload.get("decisionType", "")
    if decision_type in _ACTIONABLE_DECISION_TYPES:
        hint = _ACTIONABLE_HINTS.get(decision_type, "识别到可执行建议")
        return True, hint

    return False, None


# ════════════════════════════════════════════════════════════
# 流式分块辅助
# ════════════════════════════════════════════════════════════

async def _emit_text_chunks(
    text: str,
    chunk_size: int = 15,
    interval_ms: int = 8,
) -> AsyncGenerator[str, None]:
    """
    把完整文本按 chunk_size 字符分块流式输出。
    与 v2.6 _stream_text 行为一致。
    """
    if not text:
        return
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        yield chunk
        if interval_ms > 0:
            await asyncio.sleep(interval_ms / 1000)


# ════════════════════════════════════════════════════════════
# Intent → LLM Function 映射
# ════════════════════════════════════════════════════════════

def _resolve_intent_type(planning_output: PlanningOutput) -> str:
    """
    从 PlanningOutput 解析出意图类型字符串。
    intent 是 dict（从 orchestrator_node 返回的 intent_payload）。
    """
    intent = planning_output.intent
    if isinstance(intent, dict):
        pi = intent.get("primary_intent", "")
        if pi:
            return pi

    sse_handler = planning_output.sse_handler or ""
    if "portfolio_review" in sse_handler or "review" in sse_handler:
        return "PortfolioReview"
    if "allocation" in sse_handler:
        return "AssetAllocation"
    if "performance" in sse_handler:
        return "PerformanceAnalysis"
    if "general" in sse_handler or "chat" in sse_handler:
        return "GeneralChat"

    if planning_output.route in ("position_single", "position_multi"):
        return "PositionDecision"
    if planning_output.route == "portfolio":
        return "PortfolioReview"

    return "GeneralChat"


# ════════════════════════════════════════════════════════════
# ExpressingAgent 主类
# ════════════════════════════════════════════════════════════

class ExpressingAgent:
    """
    PEER 4 Agent 之 Expressing 角色。

    使用方式（流式）：
        agent = ExpressingAgent()
        async for chunk in agent.run_streaming(plan_out, exec_out, user_query):
            yield chunk  # 转 SSE text 事件

        # 流式结束后获取完整输出
        full_output = agent.last_output
    """

    def __init__(self):
        self.last_output: Optional[ExpressionOutput] = None

    async def run_streaming(
        self,
        planning_output: PlanningOutput,
        execution_output: ExecutionOutput,
        user_query: str,
        conversation_history: Optional[list[dict]] = None,
    ) -> AsyncGenerator[str, None]:
        """
        执行 Expressing 阶段（流式）。

        步骤：
        1. 创建 ExpressionOutput（继承 task_id）
        2. 根据意图类型选择 LLM engine 函数
        3. 调用 LLM 拿到完整结果
        4. 流式 yield chat_answer
        5. 把完整结果存到 self.last_output
        """
        out = ExpressionOutput(task_id=planning_output.task_id)
        out.status = AgentTaskStatus.IN_PROGRESS

        try:
            intent_type = _resolve_intent_type(planning_output)
            out.prompt_template_id = self._intent_to_template_id(intent_type)
            out.citation_rules_applied = ["wp-citation-rules"]

            if intent_type == "PositionDecision":
                async for chunk in self._express_position_decision(
                    out, planning_output, execution_output,
                    user_query, conversation_history,
                ):
                    yield chunk
            elif intent_type in ("PortfolioReview", "AssetAllocation", "PerformanceAnalysis"):
                async for chunk in self._express_portfolio(
                    out, planning_output, execution_output,
                    user_query, intent_type,
                ):
                    yield chunk
            elif intent_type in ("GeneralChat", "Education"):
                async for chunk in self._express_general_chat(out, user_query):
                    yield chunk
            else:
                logger.warning(f"[ExpressingAgent] 未知意图: {intent_type}")
                out.mark_failed(f"未知意图 {intent_type}")
                yield "抱歉，无法识别您的请求。"
                return

            if out.status == AgentTaskStatus.IN_PROGRESS:
                out.mark_completed()

            # v3.2: actionable 硬规则判断
            out.actionable, out.actionable_hint = _is_actionable(out)

            logger.info(
                f"[ExpressingAgent] task={out.task_id} intent={intent_type} "
                f"template={out.prompt_template_id} mode={out.mode} "
                f"chat_len={len(out.chat_answer)} actionable={out.actionable} "
                f"duration={out.duration_ms}ms"
            )

        except Exception as e:
            logger.exception(f"[ExpressingAgent] task={out.task_id} 异常: {e}")
            out.mark_failed(str(e))
            yield "抱歉，分析过程出现异常，请稍后重试。"

        finally:
            self.last_output = out

    # ────────────────────────────────────────────────────────
    # PositionDecision 路径
    # ────────────────────────────────────────────────────────

    async def _express_position_decision(
        self,
        out: ExpressionOutput,
        planning_output: PlanningOutput,
        execution_output: ExecutionOutput,
        user_query: str,
        conversation_history: Optional[list[dict]],
    ) -> AsyncGenerator[str, None]:
        """单标决策的表达：调用 llm_engine.reason()。"""
        from decision_engine import llm_engine

        if execution_output.loaded_data is None:
            out.mark_failed("execution_output.loaded_data 缺失")
            yield "数据加载失败，无法生成决策建议。"
            return

        loaded = execution_output.loaded_data
        rule_result = execution_output.rule_result
        signal_result = execution_output.signal_result

        # 构造 IntentResult 供 llm_engine.reason 使用
        from decision_engine.types import IntentResult
        intent_dict = planning_output.intent or {}
        intent_obj = IntentResult(
            asset=intent_dict.get("asset", "") if isinstance(intent_dict, dict) else "",
            action_type=intent_dict.get("action_type", "持有评估") if isinstance(intent_dict, dict) else "持有评估",
            time_horizon="未知",
            trigger=None,
            confidence_score=intent_dict.get("confidence", 0.9) if isinstance(intent_dict, dict) else 0.9,
        )

        try:
            llm_result = await asyncio.to_thread(
                llm_engine.reason,
                user_query,
                loaded,
                intent_obj,
                rule_result,
                signal_result,
                conversation_history or [],
                getattr(execution_output, "market_data", None),
            )
        except Exception as e:
            out.mark_failed(f"LLM 调用失败: {e}")
            yield "LLM 推理过程出现异常。"
            return

        out.llm_result = llm_result
        out.chat_answer = llm_result.chat_answer or ""
        out.raw_text = llm_result.raw_output or ""

        if llm_result.structured_result:
            structured = dict(llm_result.structured_result)
            structured.pop("chat_answer", None)
            out.structured_payload = structured

        out.mode = "fallback" if llm_result.is_fallback else "structured"

        async for chunk in _emit_text_chunks(out.chat_answer):
            yield chunk

    # ────────────────────────────────────────────────────────
    # 组合级路径
    # ────────────────────────────────────────────────────────

    async def _express_portfolio(
        self,
        out: ExpressionOutput,
        planning_output: PlanningOutput,
        execution_output: ExecutionOutput,
        user_query: str,
        intent_type: str,
    ) -> AsyncGenerator[str, None]:
        """组合级表达：根据意图类型调用对应 LLM engine 函数。"""
        from decision_engine import llm_engine

        if execution_output.loaded_data is None:
            out.mark_failed("loaded_data 缺失")
            yield "组合数据加载失败。"
            return

        loaded = execution_output.loaded_data

        try:
            if intent_type == "PortfolioReview":
                # P1 修复: 检测宏观问句，注入针对性分析指令
                extra_instruction = ""
                if any(kw in user_query for kw in _MACRO_KEYWORDS):
                    extra_instruction = _MACRO_ANALYSIS_INSTRUCTION
                    logger.info(f"[ExpressingAgent] 检测到宏观问句，注入分析指令")
                generic = await asyncio.to_thread(
                    llm_engine.review_portfolio, user_query, loaded,
                    extra_instruction=extra_instruction,
                )
            elif intent_type == "AssetAllocation":
                capital_amount = self._extract_capital_amount(user_query, planning_output)
                generic = await asyncio.to_thread(
                    llm_engine.analyze_allocation,
                    user_query, loaded,
                    capital_amount,
                    planning_output.portfolio_id,
                )
            elif intent_type == "PerformanceAnalysis":
                generic = await asyncio.to_thread(
                    llm_engine.analyze_performance, user_query, loaded,
                )
            else:
                out.mark_failed(f"未知组合意图: {intent_type}")
                yield "无法识别的组合分析类型。"
                return
        except Exception as e:
            out.mark_failed(f"LLM 调用失败: {e}")
            yield "LLM 推理过程出现异常。"
            return

        out.llm_result = generic
        out.chat_answer = generic.chat_answer or ""
        out.raw_text = generic.raw_output or ""
        out.structured_payload = dict(generic.raw_payload or {})
        out.structured_payload.pop("chat_answer", None)
        out.mode = "fallback" if generic.is_fallback else "structured"

        async for chunk in _emit_text_chunks(out.chat_answer):
            yield chunk

    # ────────────────────────────────────────────────────────
    # GeneralChat 路径
    # ────────────────────────────────────────────────────────

    async def _express_general_chat(
        self,
        out: ExpressionOutput,
        user_query: str,
    ) -> AsyncGenerator[str, None]:
        """通用对话：调用 llm_engine.chat()。"""
        from decision_engine import llm_engine

        try:
            chat_text = await asyncio.to_thread(
                llm_engine.chat, user_query, None,
            )
        except Exception as e:
            out.mark_failed(f"LLM chat 失败: {e}")
            yield "对话生成异常。"
            return

        out.chat_answer = chat_text or ""
        out.raw_text = chat_text or ""
        out.structured_payload = {}
        out.mode = "structured"

        async for chunk in _emit_text_chunks(out.chat_answer):
            yield chunk

    # ────────────────────────────────────────────────────────
    # 辅助函数
    # ────────────────────────────────────────────────────────

    def _intent_to_template_id(self, intent_type: str) -> str:
        """意图类型 → prompt 模板 ID。"""
        mapping = {
            "PositionDecision": "position_decision",
            "PortfolioReview": "portfolio_review",
            "AssetAllocation": "asset_allocation",
            "PerformanceAnalysis": "performance_analysis",
            "GeneralChat": "general_chat",
            "Education": "general_chat",
        }
        return mapping.get(intent_type, "position_decision")

    def _extract_capital_amount(
        self,
        user_query: str,
        planning_output: PlanningOutput,
    ) -> Optional[float]:
        """从用户问句或 intent 里提取资金金额。"""
        intent = planning_output.intent
        if isinstance(intent, dict):
            capital = intent.get("capital_amount")
            if capital:
                return float(capital)

        match = re.search(r"(\d+(?:\.\d+)?)\s*万", user_query)
        if match:
            return float(match.group(1)) * 10000
        match = re.search(r"(\d+(?:\.\d+)?)\s*元", user_query)
        if match:
            return float(match.group(1))

        return None


# ════════════════════════════════════════════════════════════
# 模块级别快捷函数
# ════════════════════════════════════════════════════════════

def get_expressing_agent() -> ExpressingAgent:
    """
    获取 ExpressingAgent。
    注意：ExpressingAgent 有 last_output 状态，每次调用创建新实例。
    """
    return ExpressingAgent()
