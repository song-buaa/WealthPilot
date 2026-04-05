"""
LLM 推理模块 (LLM Engine)

职责：将结构化信号 + 规则 + 投研观点送入 LLM，获取最终投资建议。

使用模型：gpt-4.1（由 PRD 指定）

输出格式（强约束）：
    {
        "decision": "BUY / HOLD / TAKE_PROFIT / REDUCE / SELL / STOP_LOSS",
        "reasoning": ["..."],
        "risk": ["..."],
        "strategy": ["..."]
    }

UI 映射：
    BUY         → 加仓
    HOLD        → 观望
    TAKE_PROFIT → 部分止盈
    REDUCE      → 逐步减仓
    SELL        → 减仓/清仓
    STOP_LOSS   → 止损离场

异常处理：
    - API 调用失败 → 返回默认 HOLD 结果 + 提示
    - JSON 解析失败 → 重试提取，仍失败则降级
    - 超时 → 返回"系统繁忙，请稍后再试"
"""

from __future__ import annotations

import json
import os
import re
import traceback
from dataclasses import dataclass, field
from typing import Optional

import openai

from .data_loader import LoadedData
from .decision_context import build_decision_context, format_context_prompt
from .types import IntentResult
from .rule_engine import RuleResult
from .signal_engine import SignalResult


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class GenericLLMResult:
    """
    非 PositionDecision 意图的 LLM 结果。
    适用于：PortfolioReview / AssetAllocation / PerformanceAnalysis
    """
    intent_type: str                              # portfolio_review / asset_allocation / performance_analysis
    chat_answer: str                              # 左侧对话框展示文本
    raw_payload: dict = field(default_factory=dict)  # 完整 JSON 解析结果，供右侧面板使用
    raw_output: str = ""
    error: Optional[str] = None

    @property
    def is_fallback(self) -> bool:
        return self.error is not None


@dataclass
class LLMResult:
    """LLM 推理结果"""
    decision: str              # BUY / HOLD / SELL
    reasoning: list[str]       # 推理依据列表
    risk: list[str]            # 风险提示列表
    strategy: list[str]        # 操作策略建议列表
    chat_answer: str = ""      # 面向用户的自然语言对话回答（左侧面板直接展示）
    raw_output: str = ""       # LLM 原始输出（调试用）
    error: Optional[str] = None  # 异常时的错误描述
    # Phase 1: 结构化 DecisionResult（parse 成功时填充，失败时为 None）
    structured_result: Optional[dict] = None
    # BUG-04 修复：记录决策是否经过自动修正
    decision_corrected: bool = False     # True 表示原始输出非标准，已被自动修正
    original_decision: Optional[str] = None  # 修正前的原始决策值

    @property
    def decision_cn(self) -> str:
        """决策结论的中文映射。"""
        return {
            "BUY":         "加仓",
            "HOLD":        "观望",
            "TAKE_PROFIT": "部分止盈",
            "REDUCE":      "逐步减仓",
            "SELL":        "减仓/清仓",
            "STOP_LOSS":   "止损离场",
        }.get(self.decision, "观望")

    @property
    def decision_emoji(self) -> str:
        return {
            "BUY":         "📈",
            "HOLD":        "🔍",
            "TAKE_PROFIT": "💰",
            "REDUCE":      "📉",
            "SELL":        "🚨",
            "STOP_LOSS":   "🛑",
        }.get(self.decision, "🔍")

    @property
    def is_fallback(self) -> bool:
        """是否为降级结果（API 失败时）。"""
        return self.error is not None


# ── 基础 Prompt（所有意图共用）───────────────────────────────────────────────

_BASE_PROMPT = """你是 WealthPilot 的私人投资顾问，帮助用户基于真实持仓数据做出更理性的投资决策。

通用规范（所有意图均适用）：
1. 不得使用绝对性表达（如"必须买入"、"一定会涨"）
2. 建议必须有依据，不得凭空推断
3. 涉及具体操作时，必须说明风险
4. 输出语言为中文
5. 分析只能基于系统提供的数据，数据中未提供的内容不得推测或补全
6. 本系统输出仅供参考，不构成投资建议
7. markdown 加粗规范：只对关键数字和结论词加粗（如 **34.9%**、**观望**），禁止对完整句子加粗；加粗标记前后须紧邻空格或标点，禁止直接贴合中文字符（错误：浮亏**-31.4%**，正确：浮亏 **-31.4%** ，）"""


# ── 意图专属 Prompt ───────────────────────────────────────────────────────────
# 调用时拼接：_BASE_PROMPT + "\n\n" + _XXXX_PROMPT
# ─────────────────────────────────────────────────────────────────────────────

_POSITION_DECISION_PROMPT = """当前任务：单标的决策（PositionDecision）
判断用户对某一具体标的的操作是否合适（加仓/减仓/买入/卖出/持有），输出结构化建议。

输出格式（严格 JSON，不含任何其他文字）：
{
  "decision": "BUY 或 HOLD 或 SELL",
  "reasoning": ["核心推理依据，有几条写几条（2-5条），每条说清一个判断逻辑，不超过60字"],
  "risk": ["核心风险点，有几条写几条（上限3条），每条说清一个要点，不超过60字"],
  "strategy": ["操作建议，有几条写几条（上限3条），每条说清一个要点，不超过60字"],
  "chat_answer": "见下方写作要求"
}

decision 从以下6个选项中选一个，选最精准的那个：
- BUY：基本面向好，仓位有空间，适合加仓或新建仓
- HOLD：信号中性，当前仓位合理，建议维持观望
- TAKE_PROFIT：已有较大浮盈，建议锁定部分收益（减仓幅度有限，仍看好后市）
- REDUCE：风险上升或仓位过重，建议分批降低仓位（比 SELL 更保守）
- SELL：基本面恶化或超出纪律上限，建议大幅减仓乃至清仓
- STOP_LOSS：已出现明显亏损且下行风险未解除，建议止损出场

chat_answer 写作要求：

语气：用"您"直接对用户说，像私人投顾在当面解释，不是AI在出具报告。开头直接进入正题，禁止用"综合来看""根据系统""综合分析"等套话开场。

结构（三段，不要输出段落标题）：

第一段（2-3句）：先给出结论，然后用一句话说清楚最关键的理由。好的开头示例："您的理想汽车仓位已经到了34.9%…" / "发布会前加仓这个想法可以理解，但…" / "目前不建议加仓，主要是仓位的问题。"

第二段（3-4句）：用实际数据说明判断依据，语言自然连贯，不要列表。涵盖有价值的维度：持仓仓位和收益情况、距纪律上限还有多少空间、信号层里有判断价值的项（如基本面、事件不确定性）、投研观点（有则引用：[用户录入] 的内容优先引用，[联网参考] 的内容作补充参考；无则跳过）。

第三段（2-3句）：说清楚主要风险在哪，操作建议只基于系统已有数据，不编造具体数字。结尾给一个具体的观察点或行动触发条件，例如"等发布会后看交付数据再决定"。

整体字数控制在 300-500 字。禁止重复 reasoning/risk/strategy 字段的原文。

---
## 输出格式要求（严格遵守）

你的回答必须且只能是如下 JSON 格式。第一个字符必须是 {，不要在 JSON 之外添加任何文字、解释或 Markdown。

{
  "decisionType": "buy_init | buy_more | hold | trim | exit | wait | need_info",
  "coreSuggestion": "一句话核心判断（≤40字）",
  "rationale": ["依据1", "依据2", "依据3（1-3条）"],
  "riskPoints": ["风险点1", "风险点2（1-2条）"],
  "recommendedAction": {
    "action": "与 decisionType 相同的值",
    "detail": "具体操作说明，包含价位或仓位比例"
  },
  "confidence": 0.0到1.0之间的小数,
  "confidenceReason": "置信度原因 + 建议适用前提 + 主要不确定因素",
  "infoNeeded": ["若 confidence < 0.5 必须填写，否则可为空数组"],
  "evidenceSources": ["从以下枚举中选择：profile | position | discipline | research | recent_records | news | user_message"],
  "chat_answer": "面向用户的自然语言回答，2-4句话，语气自然，不要重复 coreSuggestion 的原文，而是用对话语气解释判断和建议"
}

注意事项：
- rationale 1-3 条，riskPoints 1-2 条
- coreSuggestion 与 decisionType 结论必须语义一致
- confidence < 0.5 时，infoNeeded 必须填写且不能为空数组
- chat_answer 仍须按上方 chat_answer 写作要求生成（结构三段、300-500字），供前端直接展示"""


_PORTFOLIO_REVIEW_PROMPT = """当前任务：组合评估（PortfolioReview）
评估用户整体投资组合的结构健康度，包括集中度、风险敞口、资产配比、是否需要再平衡。

输出格式（严格 JSON，不含任何其他文字）：
{
  "risk_level": "高 或 中 或 低",
  "key_findings": ["整体组合的核心发现，2-4条，每条不超过60字"],
  "concentration_issues": ["集中度问题，有则列出，无则空数组，每条不超过60字"],
  "rebalance_needed": true 或 false,
  "rebalance_suggestions": ["调仓方向建议，有则列出（上限3条），无则空数组，每条不超过60字"],
  "chat_answer": "见下方写作要求"
}

chat_answer 写作要求：

语气：用"您"直接对用户说，像私人投顾在当面解释。开头直接进入正题，禁止套话。

结构（三段，不要输出段落标题）：

第一段（2-3句）：先给出对整体组合状态的判断，点明最核心的问题或亮点。

第二段（3-4句）：用数据说明具体情况，涵盖有价值的维度：集中度是否过高、资产类别分布是否合理、整体风险水平、主要持仓的表现。

第三段（2-3句）：说明是否需要调仓，以及调整的方向和优先级，操作建议只基于系统已有数据。

整体字数控制在 300-500 字。"""


_ASSET_ALLOCATION_PROMPT = """当前任务：资产配置（AssetAllocation）
根据用户的资金规模、风险偏好、投资目标，给出资产配置方向建议。

输出格式（严格 JSON，不含任何其他文字）：
{
  "allocation_principles": ["配置原则，2-4条，每条不超过60字"],
  "allocation_suggestions": [
    {"asset_class": "资产类别", "direction": "增加 或 维持 或 减少", "rationale": "理由，不超过60字"}
  ],
  "risks": ["主要风险点，上限3条，每条不超过60字"],
  "chat_answer": "见下方写作要求"
}

注意：allocation_suggestions 只给方向性建议，不编造系统数据之外的具体比例数字。

chat_answer 写作要求：

语气：用"您"直接对用户说，像私人投顾在当面解释。开头直接进入正题，禁止套话。

结构（三段，不要输出段落标题）：

第一段（2-3句）：基于用户的风险偏好和投资目标，说明配置的总体方向和首要原则。

第二段（3-4句）：结合用户当前持仓，说明哪些方向值得增加、哪些需要控制，说清楚理由。

第三段（2-3句）：说明主要风险和注意事项，提示分散化和纪律执行的重要性。

整体字数控制在 300-500 字。"""


_PERFORMANCE_ANALYSIS_PROMPT = """当前任务：收益分析（PerformanceAnalysis）
分析用户投资组合的盈亏情况，找出收益驱动因素和亏损来源，给出改进方向。

输出格式（严格 JSON，不含任何其他文字）：
{
  "summary": "整体收益情况一句话概括，不超过40字",
  "key_drivers": ["收益主要驱动因素，2-4条，每条不超过60字"],
  "loss_reasons": ["主要亏损或拖累来源，有则列出（上限3条），无则空数组，每条不超过60字"],
  "improvement_suggestions": ["改进建议，1-3条，每条不超过60字"],
  "chat_answer": "见下方写作要求"
}

chat_answer 写作要求：

语气：用"您"直接对用户说，像私人投顾在当面解释。开头直接进入正题，禁止套话。

结构（三段，不要输出段落标题）：

第一段（2-3句）：先给出整体收益状态的判断，点出最关键的亮点或问题。

第二段（3-4句）：分别说明主要盈利来源和主要拖累来源，引用具体数据（收益率、占比等）。

第三段（2-3句）：给出可以改进的方向，只基于数据中实际存在的问题，不编造建议。

整体字数控制在 300-500 字。"""


_NOT_IN_PORTFOLIO_PROMPT = """当前情况：用户询问了某只股票的投资操作（卖出/止损/持有判断），但系统在他的持仓记录中未找到该标的。

你的任务：生成一段自然、有帮助的引导回复。

回复结构（三段，不输出段落标题）：

第一段（1-2句）：确认你理解了用户的问题，说清楚他问的是哪只股票以及他描述的情况（亏损/涨跌等）。

第二段（2-3句）：说明在他的持仓记录里没有找到这只股票的数据，给出两条路径——
路径一：如果已经持有但尚未录入，引导去「投资账户总览」页面添加持仓，录入后系统就能基于实际成本和仓位给出准确分析。
路径二：如果想做通用参考分析，可以直接告诉我持仓数量和成本价，我可以帮你推演。

第三段（1句）：简短收尾，引导用户选择一条路径继续。

要求：
- 语气直接友好，用"您"
- 不使用套话（"很遗憾"、"根据系统数据"、"综合来看"）
- 不在没有持仓数据的情况下给出具体买卖结论
- 字数控制在 150-250 字"""


_GENERAL_CHAT_PROMPT = """当前任务：通用问答（GeneralChat / Education）
回答用户的投资知识问题或日常对话，不进入结构化决策流程，不输出 JSON。

规则：
- 回答自然、友好、有帮助，适当引用知识背景或市场常识举例说明
- 如果问题涉及用户的具体持仓操作（加仓/减仓/买入/卖出），引导用户直接描述操作意图，系统会自动进入决策流程
- 禁止输出结构化 JSON 或模板化结论
- 不提供针对具体持仓的买卖建议（当前上下文中没有持仓数据）
- 如果是投教类问题，结合实际例子解释，帮助用户建立认知"""


# ── 客户端（懒加载）──────────────────────────────────────────────────────────

_client: Optional[openai.OpenAI] = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("未找到 OPENAI_API_KEY 环境变量。")
        _client = openai.OpenAI(api_key=api_key)
    return _client


# ── DecisionResult 结构化解析（Phase 1）───────────────────────────────────────

# 新 decisionType → 旧 decision 枚举映射
_NEW_TO_OLD_DECISION = {
    'buy_init':  'BUY',
    'buy_more':  'BUY',
    'hold':      'HOLD',
    'trim':      'REDUCE',
    'exit':      'SELL',
    'wait':      'HOLD',
    'need_info': 'HOLD',
}


_VALID_DECISION_TYPES = ['buy_init', 'buy_more', 'hold', 'trim', 'exit', 'wait', 'need_info']
_VALID_EVIDENCE_SOURCES = ['profile', 'position', 'discipline', 'research', 'recent_records', 'news', 'user_message']


def validate_decision_result(result: dict) -> bool:
    """
    校验 LLM 返回的 DecisionResult JSON 结构是否合法。
    返回 True 表示校验通过，False 表示不合法。
    注意：evidenceSources 中的非法值会被过滤而不是直接拒绝整个结果。
    """
    try:
        if result.get('decisionType') not in _VALID_DECISION_TYPES:
            return False
        if not isinstance(result.get('rationale'), list) or not (1 <= len(result['rationale']) <= 3):
            return False
        if not isinstance(result.get('riskPoints'), list) or not (1 <= len(result['riskPoints']) <= 2):
            return False
        if not isinstance(result.get('confidence'), (int, float)) or not (0 <= result['confidence'] <= 1):
            return False
        if result['confidence'] < 0.5:
            if not (isinstance(result.get('infoNeeded'), list) and len(result['infoNeeded']) > 0):
                return False
        if not isinstance(result.get('evidenceSources'), list):
            return False

        # evidenceSources: 过滤非法值（宽容处理），过滤后至少保留 1 个
        valid_sources = [s for s in result['evidenceSources'] if s in _VALID_EVIDENCE_SOURCES]
        if len(valid_sources) == 0 and len(result['evidenceSources']) > 0:
            # 全部非法但有值 → 仍通过，后续清洗
            pass
        # 就地修正为合法值
        result['evidenceSources'] = valid_sources if valid_sources else result['evidenceSources']

        return True
    except Exception:
        return False


def parse_decision_result(raw_response: str) -> dict | None:
    """
    尝试解析 LLM 返回的 DecisionResult JSON。
    返回 dict 表示解析成功，返回 None 表示解析失败（fallback 到纯文本）。
    """
    try:
        # 清理可能的 markdown 代码块标记
        text = raw_response.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        result = json.loads(text)

        if validate_decision_result(result):
            return result
        else:
            return None
    except (json.JSONDecodeError, KeyError):
        return None


def _structured_to_llm_result(structured: dict, raw: str) -> LLMResult:
    """
    将新 DecisionResult 结构映射为旧 LLMResult，保证后端管道兼容。
    structured_result 字段存储完整的新格式 dict。
    """
    decision_type = structured.get('decisionType', 'hold')
    old_decision = _NEW_TO_OLD_DECISION.get(decision_type, 'HOLD')

    chat_answer = str(structured.get('chat_answer', '') or '')

    # 去掉 chat_answer 后的纯结构化数据，供前端卡片化使用
    decision_result_clean = {k: v for k, v in structured.items() if k != 'chat_answer'}

    return LLMResult(
        decision=old_decision,
        reasoning=structured.get('rationale', []),
        risk=structured.get('riskPoints', []),
        strategy=[structured.get('recommendedAction', {}).get('detail', '')],
        chat_answer=chat_answer,
        raw_output=raw,
        structured_result=decision_result_clean,
    )


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def reason(
    user_query: str,
    data: LoadedData,
    intent: IntentResult,
    rule_result: RuleResult,
    signals: SignalResult,
) -> LLMResult:
    """
    调用 LLM 进行投资推理。

    Args:
        user_query: 用户原始输入
        data: 加载的数据（含持仓、规则、投研）
        intent: 意图解析结果
        rule_result: 规则校验结果
        signals: 信号层结果

    Returns:
        LLMResult
    """
    # 构建输入 payload
    payload = _build_payload(user_query, data, intent, rule_result, signals)

    # Phase 2: 构建 DecisionContext 并注入 system prompt
    try:
        pid = data.raw_portfolio.id if data.raw_portfolio and hasattr(data.raw_portfolio, 'id') else 1
        decision_ctx = build_decision_context(user_query, data, portfolio_id=pid)
        context_prompt = format_context_prompt(decision_ctx)
        system_prompt = _BASE_PROMPT + "\n\n" + context_prompt + "\n\n" + _POSITION_DECISION_PROMPT
        print(f"[llm_engine] DecisionContext 注入成功，prompt 长度={len(system_prompt)} 字符", flush=True)
    except Exception as e:
        print(f"[llm_engine] DecisionContext 构建失败，降级到无上下文: {e}", flush=True)
        system_prompt = _BASE_PROMPT + "\n\n" + _POSITION_DECISION_PROMPT

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4.1",
            max_tokens=1024,
            timeout=30,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
            ],
        )
        raw = response.choices[0].message.content.strip()

        # Phase 1: 优先尝试新 DecisionResult 格式解析
        structured = parse_decision_result(raw)
        if structured is not None:
            print(f"[llm_engine] ✅ DecisionResult 结构化解析成功: decisionType={structured.get('decisionType')}, confidence={structured.get('confidence')}")
            return _structured_to_llm_result(structured, raw)

        # Fallback: 旧格式解析
        print(f"[llm_engine] ⚠️ DecisionResult 结构化解析失败，fallback 到旧格式")
        parsed = _extract_json(raw)
        return _build_result(parsed, raw)

    except EnvironmentError as e:
        return _fallback_result(str(e), "HOLD")

    except openai.APITimeoutError:
        return _fallback_result("系统繁忙，请稍后再试。", "HOLD")

    except openai.APIError as e:
        return _fallback_result(f"API 调用失败：{e}", "HOLD")

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        return _fallback_result(f"推理结果解析失败，默认给出观望建议。（{type(e).__name__}）", "HOLD")

    except Exception as e:
        return _fallback_result(f"未知错误：{type(e).__name__}：{e}", "HOLD")


# ── 辅助函数 ───────────────────────────────────────────────────────────────────

def _build_payload(
    user_query: str,
    data: LoadedData,
    intent: IntentResult,
    rule_result: RuleResult,
    signals: SignalResult,
) -> dict:
    """拼接送给 LLM 的结构化 payload（PRD 指定格式）。"""

    # 持仓摘要（TOP5，已聚合，每标的唯一一条）
    top_positions = sorted(data.positions, key=lambda p: p.weight, reverse=True)[:5]
    position_summary = [
        {
            "name": p.name,
            "weight": f"{p.weight:.1%}",
            "asset_class": p.asset_class,
            "platforms": p.platforms if p.platforms else [],
        }
        for p in top_positions
    ]

    # 目标持仓信息（聚合后，包含跨平台合并市值）
    target_info = None
    if data.target_position:
        tp = data.target_position
        target_info = {
            "name": tp.name,
            "current_weight": f"{tp.weight:.1%}",   # 聚合后占比，与规则校验完全一致
            "market_value_cny": f"¥{tp.market_value_cny:,.0f}",
            "profit_loss_rate": f"{tp.profit_loss_rate:.1%}",
            "platforms": tp.platforms if tp.platforms else [],
        }

    return {
        "user_query": user_query,
        "intent": {
            "asset": intent.asset,
            "action_type": intent.action_type,
            "time_horizon": intent.time_horizon,
            "trigger": intent.trigger,
        },
        "position_context": {
            "target_asset": target_info,
            "top_holdings": position_summary,
            "total_assets_cny": f"¥{data.total_assets:,.0f}",
        },
        "rules": {
            "max_single_position": f"{data.rules.max_single_position:.0%}",
            "min_cash_pct": f"{data.rules.min_cash_pct:.0%}",
            "rule_check": {
                "violation": rule_result.violation,
                "warning": rule_result.warning,
            },
        },
        "signals": signals.to_dict(),
        "research": data.research,
        "user_profile": {
            "risk_level": data.profile.risk_level,
            "goal": data.profile.goal,
        },
    }


def _sanitize_json_strings(text: str) -> str:
    """
    将 JSON 字符串值内的原生控制字符转义。

    LLM 有时在 chat_answer 等字段里写入真实换行符/制表符，
    这在 JSON 规范中是非法的，会导致 json.loads 失败。
    此函数只处理字符串值内部，不影响 JSON 结构字符。
    """
    result = []
    in_string = False
    escape_next = False
    _ESCAPE = {'\n': '\\n', '\r': '\\r', '\t': '\\t'}
    for ch in text:
        if escape_next:
            escape_next = False
            result.append(ch)
            continue
        if ch == '\\' and in_string:
            escape_next = True
            result.append(ch)
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string and ch in _ESCAPE:
            result.append(_ESCAPE[ch])
            continue
        result.append(ch)
    return ''.join(result)


def _bracket_extract(text: str) -> Optional[str]:
    """
    用括号计数法从文本中定位第一个完整 JSON 对象的字符串范围。
    返回该子串，或 None（找不到平衡的 {}）。
    支持任意嵌套深度。
    """
    start = text.find('{')
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _extract_json(text: str) -> dict:
    """
    从 LLM 输出中稳健提取 JSON，兼容多种输出格式。

    解析优先级：
    1. 直接解析
    2. 去掉 ```json``` 包装后解析
    3. 括号计数法定位 JSON 边界后解析
    4. 上述任一步骤失败时，对字符串内控制字符转义后重试
    """
    def _try_loads(s: str) -> Optional[dict]:
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
        # 控制字符转义后重试（处理 chat_answer 里的原生换行符等）
        try:
            return json.loads(_sanitize_json_strings(s))
        except json.JSONDecodeError:
            return None

    # Step 1: 直接解析
    result = _try_loads(text)
    if result is not None:
        return result

    # Step 2: 去掉 ```json ... ``` 包装
    block = re.search(r'```(?:json)?\s*(\{.*?})\s*```', text, re.DOTALL)
    if block:
        result = _try_loads(block.group(1))
        if result is not None:
            return result

    # Step 3: 括号计数法定位 JSON 边界
    candidate = _bracket_extract(text)
    if candidate:
        result = _try_loads(candidate)
        if result is not None:
            return result

    raise ValueError(f"无法提取 JSON，原始输出：{text[:300]}")


def _build_result(parsed: dict, raw: str) -> LLMResult:
    """从解析后的 dict 构建 LLMResult。"""
    raw_decision = str(parsed.get("decision", "HOLD")).strip()
    decision = raw_decision.upper()

    # BUG-04 修复：检测并记录非标准决策被自动修正的情况
    _VALID_DECISIONS = {"BUY", "HOLD", "TAKE_PROFIT", "REDUCE", "SELL", "STOP_LOSS"}
    decision_corrected = False
    original_decision: Optional[str] = None
    if decision not in _VALID_DECISIONS:
        decision_corrected = True
        original_decision = raw_decision
        decision = "HOLD"

    def _to_list(v) -> list[str]:
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str):
            return [v] if v else []
        return []

    return LLMResult(
        decision=decision,
        reasoning=_to_list(parsed.get("reasoning", [])),
        risk=_to_list(parsed.get("risk", [])),
        strategy=_to_list(parsed.get("strategy", [])),
        chat_answer=str(parsed.get("chat_answer", "") or ""),
        raw_output=raw,
        decision_corrected=decision_corrected,
        original_decision=original_decision,
    )


# ── general_chat 普通对话 ───────────────────────────────────────────────────────

# _GENERAL_CHAT_PROMPT 已在上方意图专属 Prompt 区统一定义


def chat(user_query: str, context: Optional[list] = None) -> str:
    """
    普通对话模式（intent_type=general_chat），不进入决策流程，不输出结构化结论。

    Args:
        user_query: 用户当前输入
        context:    最近 1 轮对话记录（[{"role": "user", "content": ...}, {"role": "assistant", ...}]）

    Returns:
        纯文本回复
    """
    messages: list = []
    if context:
        for msg in context[-2:]:  # 最多保留最近 1 轮（2 条）
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_query})

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=512,
            timeout=20,
            messages=[{"role": "system", "content": _BASE_PROMPT + "\n\n" + _GENERAL_CHAT_PROMPT}] + messages,
        )
        return response.choices[0].message.content.strip()
    except EnvironmentError:
        return "⚙️ 未配置 API Key，无法回复。"
    except Exception as e:
        tb_lines = traceback.format_exc()
        print(f"[llm_engine.chat] 失败:\n{tb_lines}", flush=True)
        return "抱歉，系统暂时繁忙，请稍后再试。"



def _build_portfolio_payload(user_query: str, data: LoadedData) -> dict:
    """构建组合级别 LLM payload（PortfolioReview / AssetAllocation / PerformanceAnalysis 共用）"""
    top_positions = sorted(data.positions, key=lambda p: p.weight, reverse=True)[:10]
    holdings = [
        {
            "name": p.name,
            "weight": f"{p.weight:.1%}",
            "asset_class": p.asset_class,
            "profit_loss_rate": f"{p.profit_loss_rate:.1%}",
            "market_value_cny": f"¥{p.market_value_cny:,.0f}",
        }
        for p in top_positions
    ]
    return {
        "user_query": user_query,
        "portfolio": {
            "total_assets_cny": f"¥{data.total_assets:,.0f}",
            "holding_count": len(data.positions),
            "holdings": holdings,
        },
        "rules": {
            "max_single_position": f"{data.rules.max_single_position:.0%}",
            "max_equity_pct": f"{data.rules.max_equity_pct:.0%}",
            "min_cash_pct": f"{data.rules.min_cash_pct:.0%}",
        },
        "user_profile": {
            "risk_level": data.profile.risk_level,
            "goal": data.profile.goal,
        },
    }


def _call_generic_llm(
    intent_type: str,
    prompt: str,
    payload: dict,
) -> GenericLLMResult:
    """通用 LLM 调用，供组合级别意图共用。"""
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4.1",
            max_tokens=1024,
            timeout=30,
            messages=[
                {"role": "system", "content": _BASE_PROMPT + "\n\n" + prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
            ],
        )
        raw = response.choices[0].message.content.strip()
        parsed = _extract_json(raw)
        return GenericLLMResult(
            intent_type=intent_type,
            chat_answer=str(parsed.get("chat_answer", "") or ""),
            raw_payload=parsed,
            raw_output=raw,
        )
    except EnvironmentError as e:
        return _fallback_generic(intent_type, str(e))
    except openai.APITimeoutError:
        return _fallback_generic(intent_type, "系统繁忙，请稍后再试。")
    except openai.APIError as e:
        return _fallback_generic(intent_type, f"API 调用失败：{e}")
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        return _fallback_generic(intent_type, f"推理结果解析失败。（{type(e).__name__}）")
    except Exception as e:
        return _fallback_generic(intent_type, f"未知错误：{type(e).__name__}：{e}")


def review_portfolio(user_query: str, data: LoadedData) -> GenericLLMResult:
    """组合评估 LLM 推理（PortfolioReview）"""
    payload = _build_portfolio_payload(user_query, data)
    return _call_generic_llm("portfolio_review", _PORTFOLIO_REVIEW_PROMPT, payload)


def analyze_allocation(user_query: str, data: LoadedData) -> GenericLLMResult:
    """资产配置 LLM 推理（AssetAllocation）"""
    payload = _build_portfolio_payload(user_query, data)
    return _call_generic_llm("asset_allocation", _ASSET_ALLOCATION_PROMPT, payload)


def analyze_performance(user_query: str, data: LoadedData) -> GenericLLMResult:
    """收益分析 LLM 推理（PerformanceAnalysis）"""
    payload = _build_portfolio_payload(user_query, data)
    return _call_generic_llm("performance_analysis", _PERFORMANCE_ANALYSIS_PROMPT, payload)


def _fallback_generic(intent_type: str, error_msg: str) -> GenericLLMResult:
    """组合级别意图的降级结果。"""
    return GenericLLMResult(
        intent_type=intent_type,
        chat_answer="",
        raw_payload={},
        error=error_msg,
    )


def respond_not_in_portfolio(user_query: str, asset_name: str) -> str:
    """
    生成"标的不在持仓中"的智能引导回复。

    用于用户询问一个未录入持仓的标的时（通常是卖出/止损/持有类操作），
    代替硬编码的错误信息，给出有帮助的引导。
    """
    context = f"用户原始问题：{user_query}\n识别到的标的：{asset_name}"
    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            max_tokens=512,
            timeout=20,
            messages=[
                {"role": "system", "content": _BASE_PROMPT + "\n\n" + _NOT_IN_PORTFOLIO_PROMPT},
                {"role": "user", "content": context},
            ],
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return (
            f"我在您的持仓记录中没有找到 **{asset_name}** 的数据。\n\n"
            f"如果您已在其他平台持有但尚未录入，可以先在「投资账户总览」中添加持仓信息，"
            f"之后系统就能基于您的实际成本和仓位给出更准确的分析。\n\n"
            f"或者，您可以直接告诉我持仓数量和成本价，我可以帮您做参考推演。"
        )


def _fallback_result(error_msg: str, decision: str = "HOLD") -> LLMResult:
    """API 异常或解析失败时的降级结果（PRD §3.8：数据缺失→默认HOLD）。"""
    return LLMResult(
        decision=decision,
        reasoning=["当前无法完成 AI 推理，建议保持观望。"],
        risk=["请稍后重试，或手动评估当前持仓风险。"],
        strategy=["维持当前仓位，等待更多信息后再做决策。"],
        chat_answer="",
        raw_output="",
        error=error_msg,
    )
