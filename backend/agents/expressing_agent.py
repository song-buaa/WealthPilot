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
# v3.4 M8.4 新建仓 prompt 模板
# ════════════════════════════════════════════════════════════

_NEW_ENTRY_SYSTEM_PROMPT = """你是 WealthPilot 投资分析助手。用户正在评估一个**尚未持有**的标的，考虑是否建仓。
请基于提供的基本面数据给出专业、客观的新建仓评估。

重要规则:
- 不要展示"当前仓位"或"持仓盈亏"——用户尚未建仓
- 不要承诺收益,只提供分析
- 如果纪律约束已阻止(如组合熔断),明确拒绝并说明原因
- 输出使用 Markdown 格式"""

_CHAT_FORMAT_NEW_ENTRY = """【标的信息】
- 标的: {asset_name} ({ticker})

【基本面数据】
- PE (TTM): {pe_ttm}
- PB: {pb}
- EPS (TTM): {eps_ttm}
- 52 周区间: {low_52w} - {high_52w}
- 分析师目标价: {analyst_target}
- 分析师评级: {analyst_ratings}
- 营收增速(YoY): {revenue_yoy}
- 净利增速(YoY): {net_income_yoy}
- ROE: {roe}
- 毛利率: {gross_margin}

【用户组合上下文】
- 总资产: {total_assets}

【纪律约束】
{rule_summary}

【任务】
请按以下结构输出新建仓评估(Markdown 格式):

## 建仓建议
核心结论一句话: 是否建议建仓 / 建议谨慎观察 / 不建议建仓

## 基本面解读
2-3 句概括估值水平(PE/PB/EPS) + 52 周价格位置 + 成长性(营收/净利增速)

## 建仓价区间
基于当前估值和 52 周区间,给出"理想建仓价"和"可接受建仓价"区间

## 仓位建议
建议首次建仓占总资产的百分比(一般建议 5-10%,首次建仓不超过 10%)

## 风险提示
1-2 句该标的的主要风险"""


def _fmt(val, prefix="", suffix="", default="数据暂缺"):
    """格式化可能为 None 的数值。"""
    if val is None:
        return default
    if isinstance(val, float):
        return f"{prefix}{val:.2f}{suffix}"
    return f"{prefix}{val}{suffix}"


def _fmt_pct(val, default="数据暂缺"):
    if val is None:
        return default
    return f"{val:.1f}%"


def _build_new_entry_payload(
    asset_name: str,
    ticker: str,
    av_data,
    total_assets: float,
    rule_result,
    full_discipline_rules: dict | None = None,
) -> dict:
    """构造新建仓 prompt payload,鲁棒处理 None 字段。

    不包含任何持仓字段(weight/market_value/profit_loss),
    避免 LLM 被误导生成"当前仓位"话术。

    v3.4 M8.5: rule_summary 从简版升级为完整纪律摘要。
    """
    # AV fundamentals 字段提取
    pe = getattr(av_data, "pe_ttm", None) if av_data else None
    eps = getattr(av_data, "eps_ttm", None) if av_data else None
    high_52 = getattr(av_data, "high_52w", None) if av_data else None
    low_52 = getattr(av_data, "low_52w", None) if av_data else None
    roe = getattr(av_data, "roe", None) if av_data else None
    gm = getattr(av_data, "gross_margin", None) if av_data else None
    rev_yoy = getattr(av_data, "revenue_yoy", None) if av_data else None
    ni_yoy = getattr(av_data, "net_income_yoy", None) if av_data else None

    # 分析师数据
    analyst = getattr(av_data, "analyst", None) if av_data else None
    target_price = getattr(analyst, "target_price_avg", None) if analyst else None
    consensus = getattr(analyst, "consensus", None) if analyst else None

    ratings_str = "暂无数据"
    if analyst:
        parts = []
        for label, field_name in [
            ("强买", "strong_buy"), ("买入", "buy"), ("持有", "hold"),
            ("卖出", "sell"), ("强卖", "strong_sell"),
        ]:
            v = getattr(analyst, field_name, None)
            if v is not None:
                parts.append(f"{label}:{v}")
        if parts:
            ratings_str = " / ".join(parts)
            if consensus:
                ratings_str += f" (共识: {consensus})"

    # PB: AV OVERVIEW 没有直接的 PB 字段,用 market_cap / book_value 计算(如有)
    # M8.4 简化: 如果 av_data 没有 pb, 用 "数据暂缺"
    pb = getattr(av_data, "pb", None) if av_data else None
    # AV 的 PriceToBookRatio 在 OVERVIEW 里
    if pb is None and av_data:
        pb = getattr(av_data, "peg_ratio", None)  # fallback: 用 PEG 代替 PB 显示

    # M8.5: 完整纪律摘要(替代 M8.4 简版)
    if full_discipline_rules:
        from app.discipline.new_entry_view import build_new_entry_discipline_summary
        rule_summary = build_new_entry_discipline_summary(full_discipline_rules)
    else:
        rule_summary = "【适用纪律(新建仓场景)】\n- 纪律配置暂缺"

    # 追加 rule_engine 实际校验结果(如有 blocker)
    if rule_result:
        violation = getattr(rule_result, "violation", False) if not isinstance(rule_result, dict) else rule_result.get("violation", False)
        warning = getattr(rule_result, "warning", False) if not isinstance(rule_result, dict) else rule_result.get("warning", False)
        if violation:
            rule_summary += "\n\n⛔ 硬约束触发: 纪律校验存在违规,建议暂缓建仓"
        elif warning:
            rule_summary += "\n\n⚠️ 纪律校验存在警告,建议谨慎评估"

    return {
        "asset_name": asset_name,
        "ticker": ticker,
        "pe_ttm": _fmt(pe),
        "pb": _fmt(pb),
        "eps_ttm": _fmt(eps, prefix="$"),
        "high_52w": _fmt(high_52, prefix="$"),
        "low_52w": _fmt(low_52, prefix="$"),
        "analyst_target": _fmt(target_price, prefix="$"),
        "analyst_ratings": ratings_str,
        "revenue_yoy": _fmt_pct(rev_yoy),
        "net_income_yoy": _fmt_pct(ni_yoy),
        "roe": _fmt_pct(roe),
        "gross_margin": _fmt_pct(gm),
        "total_assets": f"¥{total_assets:,.0f}" if total_assets else "数据暂缺",
        "rule_summary": rule_summary,
    }


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
        """单标决策的表达：调用 llm_engine.reason()。

        v3.4 M8.4: 新建仓场景(is_new_entry=True)走独立 prompt 模板,
        不复用持仓分析的 _CHAT_FORMAT_FIRST_TURN。
        """
        from decision_engine import llm_engine

        if execution_output.loaded_data is None:
            out.mark_failed("execution_output.loaded_data 缺失")
            yield "数据加载失败，无法生成决策建议。"
            return

        loaded = execution_output.loaded_data

        # M8.4: 新建仓分支
        if getattr(loaded, "is_new_entry", False):
            async for chunk in self._express_new_entry(
                out, execution_output, user_query,
            ):
                yield chunk
            return

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
    # 新建仓路径（v3.4 M8.4）
    # ────────────────────────────────────────────────────────

    async def _express_new_entry(
        self,
        out: ExpressionOutput,
        execution_output: ExecutionOutput,
        user_query: str,
    ) -> AsyncGenerator[str, None]:
        """新建仓评估表达(M8.4)。

        使用独立 prompt 模板,不复用持仓分析模板,避免输出
        "当前仓位 0%"等误导话术。
        """
        loaded = execution_output.loaded_data
        virtual_pos = loaded.target_position
        av_data = loaded.av_fundamentals

        payload = _build_new_entry_payload(
            asset_name=getattr(virtual_pos, "name", "未知标的"),
            ticker=getattr(virtual_pos, "ticker", ""),
            av_data=av_data,
            total_assets=loaded.total_assets,
            rule_result=execution_output.rule_result,
            full_discipline_rules=getattr(loaded, "full_discipline_rules", None),
        )

        prompt = _NEW_ENTRY_SYSTEM_PROMPT + "\n\n" + _CHAT_FORMAT_NEW_ENTRY.format(**payload)

        try:
            from intent_engine._llm_client import get_client, MODEL_MAIN
            client = get_client()
            response = await asyncio.to_thread(
                lambda: client.chat.completions.create(
                    model=MODEL_MAIN,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_query},
                    ],
                    temperature=0.3,
                    max_tokens=2000,
                    timeout=30,
                ),
            )
            chat_answer = response.choices[0].message.content or ""
        except Exception as e:
            logger.error("[ExpressingAgent] 新建仓 LLM 调用失败: %s", e)
            out.mark_failed(f"LLM 调用失败: {e}")
            yield "抱歉,新建仓分析过程出现异常。"
            return

        out.chat_answer = chat_answer
        out.raw_text = chat_answer
        out.mode = "structured"
        out.prompt_template_id = "new_entry_evaluation"

        # 强制 decisionType = buy_init
        out.structured_payload = {
            "decisionType": "buy_init",
            "is_new_entry": True,
            "asset_name": getattr(virtual_pos, "name", ""),
            "ticker": getattr(virtual_pos, "ticker", ""),
        }

        async for chunk in _emit_text_chunks(chat_answer):
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
