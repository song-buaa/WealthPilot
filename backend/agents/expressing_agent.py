"""
ExpressingAgent - PEER 4 Agent 之 Expressing 角色。

职责（对标 agentUniverse ExpressingAgent）：
1. 加载意图对应的 prompt 模板
2. 注入输出规范 Skills（wp-citation-rules）的内容
3. 调用 LLM（Sol）生成结构化输出
4. 流式 yield chat_answer 给用户
5. 返回完整 LLMResult / GenericLLMResult

设计哲学：
- 唯一的 AsyncGenerator Agent（流式输出）
- 调用现有 llm_engine 函数（reason / review_portfolio / analyze_allocation 等）
- 流式分块由 _emit_text_chunks 处理（与 v2.6 _stream_text 行为一致）
"""
from __future__ import annotations

from app.llm_observation import peer_stage

from app.llm_config import MODEL_MAIN, chat_completion

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
            # A single-security execution cannot be narrated as generic
            # education. The Planning boundary must resolve this conflict
            # first; otherwise fail closed before any model call.
            if planning_output.route == "position_single" and pi != "PositionDecision":
                return "RouteIntentMismatch"
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

_PURE_RESEARCH_SYSTEM_PROMPT = """你是 WealthPilot 单标专业投研助手。用户明确未持仓且没有买入意图。
请基于下列合格证据分析经营变化、财务质量、竞争反证、投资逻辑及复核条件。
事实与推论分开；无合格证据的估值和现金流结论保持未知。
仅输出根 JSON 中的 chat_answer 与 professional_explanation；不要给出建仓价、仓位、减仓分批或订单指令。
专业解释的引用和数字占位符必须遵循用户消息中的确定性证据合同。"""


async def _focused_numeric_explanation(assessment: dict, question: str, candidate: object):
    """Use the same Expressing evidence contract when the large decision JSON omitted numbers."""
    from backend.services.market_data.output_policy import explanation_projection

    metrics = (assessment.get("financial_research") or {}).get("records") or []
    if not any(row.get("quality") == "valid" and row.get("kind") == "research_metric"
               and row.get("field") not in {"observed_price", "fx_rate"} for row in metrics):
        return None
    accepted, _ = explanation_projection(candidate, assessment)
    key_refs = {row["record_id"] for row in metrics
                if row.get("quality") == "valid" and row.get("kind") == "research_metric"
                and any(part in row.get("field", "") for part in
                        ("yoy", "qoq", "margin", "cash_conversion", "per_delivery"))}
    def bindings(rows):
        return {number.get("evidence_ref") for row in rows for number in row.get("numbers", [])
                if number.get("evidence_ref") in key_refs}
    if not key_refs or len(bindings(accepted)) >= min(2, len(key_refs)):
        return None
    # Never erase a verified paragraph to make room for a numerical supplement.
    # Keep raw candidates so the final owner can reapply its placeholder contract.
    original = [row for row in (candidate or []) if explanation_projection([row], assessment)[0]]
    if len(original) >= 6:
        return None

    from intent_engine._llm_client import get_client
    import json

    try:
        response = await asyncio.to_thread(lambda: chat_completion(
            get_client(), model=MODEL_MAIN, reasoning_effort="high", max_tokens=28672,
            timeout=180, messages=[
                {"role": "system", "content": (
                    "你在原PEER的Expressing阶段补足专业解释，不重新计算事实或决定交易。"
                    "仅输出JSON对象，根字段professional_explanation为最多六段的数组。"
                    "依据用户消息中已有的有效引用及数字绑定合同，至少一段经营解释应绑定实际可用的"
                    "同比、利润率或汽车经营指标；仅在证据允许时论述现金质量、竞争反证、投资逻辑与复核条件。"
                    "把事实与推论分开；缺失的估值、分部和来源保持未知。"
                    "不得给出交易价格、数量、订单或未绑定的数字。")},
                {"role": "user", "content": question},
            ]))
        raw = response.choices[0].message.content or ""
        parsed = json.loads(raw)
        proposed = parsed.get("professional_explanation") if isinstance(parsed, dict) else None
        checked, _ = explanation_projection(proposed, assessment)
        logger.info("focused_explanation accepted=%s numeric_bound=%s",
                    len(checked), sum(bool(row.get("numbers")) for row in checked))
        merged = list(original)
        seen = {(row.get("topic"), row.get("text")) for row in original}
        covered = bindings(accepted)
        for row in proposed or []:
            valid, _ = explanation_projection([row], assessment)
            if not valid or not (bindings(valid) - covered):
                continue
            identity = (row.get("topic"), row.get("text"))
            if identity in seen or len(merged) >= 6:
                continue
            merged.append(row)
            seen.add(identity)
            covered |= bindings(valid)
        final, _ = explanation_projection(merged, assessment)
        if len(final) == len(merged) and bindings(final) > bindings(accepted):
            return merged
    except Exception as exc:
        # A supplementary explanation is never allowed to erase a completed
        # first pass. Keep diagnostics free of model text and private context.
        logger.warning("focused_explanation unavailable category=%s",
                       getattr(exc, "kind", type(exc).__name__))
    return None

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

    @peer_stage("expressing")
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
            from backend.services.investment_policy.runtime import prompt
            if getattr(execution_output,"analysis_mode","") != "l1_research":
                user_query += prompt(planning_output.portfolio_id)
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
                    user_query, intent_type, conversation_history,
                ):
                    yield chunk
            elif intent_type in ("GeneralChat", "Education"):
                async for chunk in self._express_general_chat(
                    out, user_query, conversation_history,
                    execution_output=execution_output,  # v3.6: 透传知识库召回
                ):
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
            if getattr(execution_output,"position_evidence",{}):
                out.actionable, out.actionable_hint = False, None

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
        if getattr(execution_output,"analysis_mode","") == "l1_research":
            async for chunk in self._express_l1_research(out, planning_output, execution_output):
                yield chunk
            return
        from decision_engine import llm_engine

        if execution_output.loaded_data is None:
            out.mark_failed("execution_output.loaded_data 缺失")
            yield "数据加载失败，无法生成决策建议。"
            return

        loaded = execution_output.loaded_data
        assessment = getattr(execution_output, "position_evidence", {})
        prompt_market = getattr(execution_output, "market_data", None)
        if assessment:
            from copy import copy
            from backend.services.market_data.evidence import safe_market
            from backend.services.market_data.output_policy import prompt_context
            loaded = copy(loaded)
            loaded.research = [str(r["values"].get("content") or r["values"].get("text") or "")
                for r in assessment["records"] if r["kind"]=="research" and r["quality"]=="valid"]
            loaded.retrieved_research_views = []
            prompt_market = safe_market(prompt_market,assessment)
            user_query += prompt_context(assessment)

        from backend.services.technical_patterns.ai_integration import (
            build_pattern_ai_context,
        )
        from backend.services.technical_patterns.decision_integration import (
            target_from_execution_output,
        )

        target = target_from_execution_output(execution_output)
        requested_symbols = (
            (target.requested_symbol,) if target is not None else None
        )
        pattern_ai_context = build_pattern_ai_context(
            execution_output.pattern_evidence,
            requested_symbols=requested_symbols,
        )

        # M8.4: 新建仓分支
        if getattr(loaded, "is_new_entry", False):
            new_entry_execution = execution_output
            if assessment:
                new_entry_execution = copy(execution_output)
                new_entry_execution.loaded_data = loaded
                new_entry_execution.market_data = prompt_market
                if not any(r["kind"]=="fundamentals" and r["quality"]=="valid" for r in assessment["records"]):
                    loaded.av_fundamentals = None
            async for chunk in self._express_new_entry(
                out, new_entry_execution, user_query, pattern_ai_context,
            ):
                if not assessment:
                    yield chunk
            if assessment:
                from backend.services.market_data.output_policy import enforce
                enforce(out,assessment)
                async for chunk in _emit_text_chunks(out.chat_answer):
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
                prompt_market,
                pattern_ai_context=pattern_ai_context,
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
            if assessment and not llm_result.is_fallback:
                enriched = await _focused_numeric_explanation(
                    assessment, user_query, structured.get("professional_explanation"))
                if enriched is not None:
                    structured["professional_explanation"] = enriched
            out.structured_payload = structured

        out.mode = "fallback" if llm_result.is_fallback else "structured"
        if assessment:
            from backend.services.market_data.output_policy import enforce
            enforce(out,assessment)

        async for chunk in _emit_text_chunks(out.chat_answer):
            yield chunk

    async def _express_l1_research(self, out, plan, executed):
        from backend.services.market_data.l1_context import messages, ContextBudgetExceeded
        from backend.services.market_data.output_policy import enforce
        from backend.services.investment_policy.runtime import context
        from app.llm_config import l1_research_options, error_kind, LLMCallError
        from intent_engine._llm_client import get_client
        from decision_engine.llm_engine import LLMResult
        import json

        assessment = executed.position_evidence
        loaded = executed.loaded_data
        if not assessment or not loaded:
            out.mark_failed('L1_EVIDENCE_UNAVAILABLE')
            return
        policy = context(plan.portfolio_id, consumer='peer', view='position', symbol=assessment.get('symbol'))
        policy = {k:v for k,v in policy.items() if k in
            {'mode','status','policy_reference','rules','philosophy','obligations','position_target','precision','execution_authorized'}}
        target = getattr(loaded,'target_position',None)
        holding = dict(state='not_held' if loaded.is_new_entry else 'stored_holding_requires_freshness_check' if target is not None else 'UNKNOWN',
            source='original_loaded_data',
            stored_reference={k:getattr(target,k,None) for k in ('ticker','name','quantity','currency')},
            execution_authorized=False)
        try:
            payload, diagnostic = messages(assessment, executed.research_question,
                                          policy=policy, holding=holding, history=executed.research_history)
            executed.research_context_diagnostics = diagnostic
            logger.info('l1_context_preflight %s', json.dumps(diagnostic,ensure_ascii=True))
            response = await asyncio.to_thread(lambda:chat_completion(get_client(),
                **l1_research_options(), messages=payload))
            raw = response.choices[0].message.content or ''
            parsed = json.loads(raw)
            if not isinstance(parsed,dict):raise LLMCallError('invalid_output')
        except ContextBudgetExceeded as error:
            executed.research_context_diagnostics = error.diagnostic
            out.mark_failed('L1_CONTEXT_BUDGET_EXCEEDED')
            return
        except Exception as error:
            kind = error_kind(error)
            executed.research_context_diagnostics['model_state'] = kind if kind in {'truncated','timeout','empty_output','invalid_output','refusal'} else 'failed'
            out.mark_failed('L1_EXPRESSION_'+kind.upper())
            return
        objective = (assessment.get('research_intent') or {}).get('objective')
        pure = (assessment.get('research_intent') or {}).get('no_holding_research')
        decision = ('watch' if pure else 'exit' if objective=='exit' else 'trim' if objective in {'reduce','trim'} else
                    'buy_init' if loaded.is_new_entry else 'hold')
        out.prompt_template_id='l1_professional_research.v1'
        out.mode='structured'
        out.structured_payload=dict(decisionType=decision, is_new_entry=bool(loaded.is_new_entry and not pure),
            professional_explanation=parsed.get('professional_explanation'))
        out.llm_result=LLMResult('HOLD',[],[],[],chat_answer='',raw_output='',structured_result={})
        enforce(out,assessment,compact_incomplete=True)  # Original, full authority; the compact pack cannot grant qualification.
        executed.research_context_diagnostics['model_state']='completed'
        executed.research_context_diagnostics['accepted_count']=len(out.structured_payload['professional_explanation'])
        # The original PEER buffers this qualified L1 text until Review/final
        # requalification. Do not pace a discarded pre-Review copy character by
        # character; the final owner still controls public delivery.
        if out.chat_answer:
            yield out.chat_answer

    # ────────────────────────────────────────────────────────
    # 新建仓路径（v3.4 M8.4）
    # ────────────────────────────────────────────────────────

    async def _express_new_entry(
        self,
        out: ExpressionOutput,
        execution_output: ExecutionOutput,
        user_query: str,
        pattern_ai_context: object | None = None,
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

        assessment = getattr(execution_output, "position_evidence", {})
        intent = assessment.get("research_intent") or {}
        pure_research = bool(assessment and intent.get("no_holding_research")
                             and intent.get("objective") == "evaluate")
        prompt = (_PURE_RESEARCH_SYSTEM_PROMPT + "\n标的：" + str(payload["asset_name"])
                  + " (" + str(payload["ticker"]) + ")" if pure_research else
                  _NEW_ENTRY_SYSTEM_PROMPT + "\n\n" + _CHAT_FORMAT_NEW_ENTRY.format(**payload))
        if assessment:
            prompt += ("\n本次有确定性证据资格控制。请输出JSON对象，包含chat_answer和professional_explanation；"
                       "chat_answer不会直接显示，专业解释必须遵循用户消息中已有的证据与数字绑定合同。")
        from backend.services.technical_patterns.ai_integration import (
            format_pattern_ai_prompt_section,
        )

        pattern_section = format_pattern_ai_prompt_section(
            pattern_ai_context
        )
        if pattern_section:
            prompt += "\n\n" + pattern_section

        try:
            from intent_engine._llm_client import get_client, MODEL_MAIN
            client = get_client()
            response = await asyncio.to_thread(
                lambda: chat_completion(client,
                    model=MODEL_MAIN,
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_query},
                    ],
                    temperature=0.3,
                    # Hidden reasoning counts against completion tokens. The
                    # prior 8192 effective cap was exhausted before any JSON.
                    max_tokens=28672 if assessment else 2000,
                    reasoning_effort="high" if pure_research else None,
                    timeout=180 if assessment else 30,
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
        out.prompt_template_id = "single_security_research" if pure_research else "new_entry_evaluation"

        # A virtual position is not evidence of an intention to buy.
        out.structured_payload = {
            "decisionType": "watch" if pure_research else "buy_init",
            "is_new_entry": not pure_research,
            "asset_name": getattr(virtual_pos, "name", ""),
            "ticker": getattr(virtual_pos, "ticker", ""),
        }
        if assessment:
            # New-entry has a smaller JSON contract than held-position
            # DecisionResult. Extract only its untrusted explanation candidate;
            # the caller buffers this response and enforce() validates it.
            import json
            candidate = chat_answer.strip()
            if candidate.startswith("```") and candidate.endswith("```"):
                candidate = candidate[3:-3].strip()
                if candidate.startswith("json"):
                    candidate = candidate[4:].lstrip()
            try:
                parsed = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                parsed = {}
            if isinstance(parsed, dict):
                out.structured_payload["professional_explanation"] = parsed.get("professional_explanation")

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
        conversation_history: Optional[list[dict]] = None,
    ) -> AsyncGenerator[str, None]:
        """组合级表达：根据意图类型调用对应 LLM engine 函数。"""
        from decision_engine import llm_engine

        if execution_output.loaded_data is None:
            out.mark_failed("loaded_data 缺失")
            yield "组合数据加载失败。"
            return

        loaded = execution_output.loaded_data
        if intent_type in ("PortfolioReview", "AssetAllocation"):
            from copy import copy
            from backend.services.market_data.output_policy import explanation_prompt, _evidence_index
            assessments = getattr(execution_output, "portfolio_evidence", [])
            evidence = {"records":[record for assessment in assessments
                                   for record in _evidence_index(assessment).values()]}
            # Reuse the same original model call while removing unqualified
            # legacy/RAG research from the model's current-fact context.
            loaded = copy(loaded)
            loaded.research = [str(record.get("values", {}).get("content") or
                                   record.get("values", {}).get("text") or "")
                               for record in evidence["records"] if record.get("kind") == "research"]
            loaded.retrieved_research_views = []
            user_query += explanation_prompt(evidence)
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
                    conversation_history=conversation_history,
                )
            elif intent_type == "AssetAllocation":
                capital_amount = self._extract_capital_amount(user_query, planning_output)
                generic = await asyncio.to_thread(
                    llm_engine.analyze_allocation,
                    user_query, loaded,
                    capital_amount,
                    planning_output.portfolio_id,
                    conversation_history=conversation_history,
                )
            elif intent_type == "PerformanceAnalysis":
                generic = await asyncio.to_thread(
                    llm_engine.analyze_performance, user_query, loaded,
                    conversation_history=conversation_history,
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
        conversation_history: Optional[list[dict]] = None,
        execution_output: Optional[ExecutionOutput] = None,
    ) -> AsyncGenerator[str, None]:
        """通用对话：调用 llm_engine.chat()。v3.6 新增知识库原则注入。"""
        from decision_engine import llm_engine

        # v3.6: 从 execution_output 提取知识库原则
        principles_text = ""
        if execution_output and execution_output.loaded_data:
            chunks = getattr(execution_output.loaded_data, "retrieved_principles", [])
            if chunks:
                principles_text = "\n\n".join(
                    getattr(c, "content", str(c)) for c in chunks
                )

        try:
            chat_text = await asyncio.to_thread(
                llm_engine.chat, user_query, conversation_history,
                principles_text or None,  # v3.6: fallback 到常量
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
