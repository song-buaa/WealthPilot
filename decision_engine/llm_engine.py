"""
LLM 推理模块 (LLM Engine)

职责：将结构化信号 + 规则 + 投研观点送入 Claude，获取最终投资建议。

使用模型：claude-sonnet-4-20250514（由 PRD 指定）
System Prompt：固定（由 PRD 指定，不允许修改）

输出格式（强约束）：
    {
        "decision": "BUY / HOLD / SELL",
        "reasoning": ["..."],
        "risk": ["..."],
        "strategy": ["..."]
    }

UI 映射：
    BUY  → 加仓
    HOLD → 观望
    SELL → 减仓

异常处理：
    - API 调用失败 → 返回默认 HOLD 结果 + 提示
    - JSON 解析失败 → 重试提取，仍失败则降级
    - 超时 → 返回"系统繁忙，请稍后再试"
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import traceback
from dataclasses import dataclass, field
from typing import Optional

import anthropic
import httpx

from .data_loader import LoadedData
from .intent_parser import IntentResult
from .rule_engine import RuleResult
from .signal_engine import SignalResult


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class LLMResult:
    """LLM 推理结果"""
    decision: str              # BUY / HOLD / SELL
    reasoning: list[str]       # 推理依据列表
    risk: list[str]            # 风险提示列表
    strategy: list[str]        # 操作策略建议列表
    raw_output: str = ""       # LLM 原始输出（调试用）
    error: Optional[str] = None  # 异常时的错误描述
    # BUG-04 修复：记录决策是否经过自动修正
    decision_corrected: bool = False     # True 表示原始输出非标准，已被自动修正
    original_decision: Optional[str] = None  # 修正前的原始决策值

    @property
    def decision_cn(self) -> str:
        """决策结论的中文映射。"""
        return {"BUY": "加仓", "HOLD": "观望", "SELL": "减仓"}.get(self.decision, "观望")

    @property
    def decision_emoji(self) -> str:
        return {"BUY": "📈", "HOLD": "🔍", "SELL": "📉"}.get(self.decision, "🔍")

    @property
    def is_fallback(self) -> bool:
        """是否为降级结果（API 失败时）。"""
        return self.error is not None


# ── System Prompt（PRD 固定，不允许修改）────────────────────────────────────

_SYSTEM_PROMPT = """你是一个专业的投资决策助手。

你需要基于：
- 用户持仓情况
- 投资纪律
- 投研观点
- 信号层分析结果

提供理性、克制、可解释的投资建议。

要求：
1. 不得使用绝对性表达（如"必须买入"）
2. 必须给出理由
3. 必须提示风险
4. 输出语言为中文
5. 风格类似投顾报告，简洁理性

输出格式（严格 JSON，不含任何其他文字）：
{
  "decision": "BUY 或 HOLD 或 SELL",
  "reasoning": ["推理依据1", "推理依据2", "推理依据3"],
  "risk": ["风险提示1", "风险提示2"],
  "strategy": ["操作建议1", "操作建议2"]
}

decision 只能是 BUY / HOLD / SELL 三选一。
每个列表项控制在 40 字以内，简洁有力。"""


# ── 客户端（懒加载）──────────────────────────────────────────────────────────

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("未找到 ANTHROPIC_API_KEY 环境变量。")

        # 与 intent_parser 相同的 proxy 修复：避免 Streamlit ScriptRunnerThread 无 event loop
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

        https_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        all_proxy = os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
        proxy_url = None
        if https_proxy and not https_proxy.startswith("socks"):
            proxy_url = https_proxy
        elif all_proxy:
            proxy_url = all_proxy

        try:
            http_client = httpx.Client(proxy=proxy_url) if proxy_url else httpx.Client()
            _client = anthropic.Anthropic(api_key=api_key, http_client=http_client)
        except Exception:
            _client = anthropic.Anthropic(api_key=api_key)

    return _client


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def reason(
    user_query: str,
    data: LoadedData,
    intent: IntentResult,
    rule_result: RuleResult,
    signals: SignalResult,
) -> LLMResult:
    """
    调用 Claude 进行投资推理。

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

    try:
        client = _get_client()
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, indent=2)
                }
            ]
        )
        raw = response.content[0].text.strip()
        parsed = _extract_json(raw)
        return _build_result(parsed, raw)

    except EnvironmentError as e:
        return _fallback_result(str(e), "HOLD")

    except anthropic.APITimeoutError:
        return _fallback_result("系统繁忙，请稍后再试。", "HOLD")

    except anthropic.APIError as e:
        return _fallback_result(f"API 调用失败：{e}", "HOLD")

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        # JSON 解析失败：降级为 HOLD
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
                "position_ratio": f"{rule_result.position_ratio:.0%}",
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


def _extract_json(text: str) -> dict:
    """从 LLM 输出中稳健提取 JSON，兼容多种输出格式。"""
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 提取 ```json ... ``` 代码块
    block = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if block:
        return json.loads(block.group(1))

    # 提取第一个完整 { ... }
    brace = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}', text, re.DOTALL)
    if brace:
        return json.loads(brace.group())

    # 最后尝试：提取任意 { ... } 块
    all_braces = re.search(r'\{.*\}', text, re.DOTALL)
    if all_braces:
        return json.loads(all_braces.group())

    raise ValueError(f"无法提取 JSON，原始输出：{text[:300]}")


def _build_result(parsed: dict, raw: str) -> LLMResult:
    """从解析后的 dict 构建 LLMResult。"""
    raw_decision = str(parsed.get("decision", "HOLD")).strip()
    decision = raw_decision.upper()

    # BUG-04 修复：检测并记录非标准决策被自动修正的情况
    decision_corrected = False
    original_decision: Optional[str] = None
    if decision not in ("BUY", "HOLD", "SELL"):
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
        raw_output=raw,
        decision_corrected=decision_corrected,
        original_decision=original_decision,
    )


# ── general_chat 普通对话 ───────────────────────────────────────────────────────

_CHAT_SYSTEM_PROMPT = """你是 WealthPilot 的投资助手，负责回答用户的日常问题。

规则：
- 回答自然、友好、简洁
- 如果问题涉及具体的买入/卖出/加仓/减仓操作，引导用户直接在对话框中描述操作意图，系统会自动进入决策流程
- 禁止输出【结论】【原因】【建议】格式的结构化投资决策
- 不提供任何形式的具体买卖建议"""


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
        response = client.messages.create(
            model="claude-haiku-4-20250514",  # 普通对话用轻量模型
            max_tokens=512,
            system=_CHAT_SYSTEM_PROMPT,
            messages=messages,
        )
        return response.content[0].text.strip()
    except EnvironmentError:
        return "⚙️ 未配置 API Key，无法回复。"
    except Exception as e:
        tb_lines = traceback.format_exc()
        print(f"[llm_engine.chat] 失败:\n{tb_lines}", flush=True)
        return "抱歉，系统暂时繁忙，请稍后再试。"


# ── 左侧 Chat 自然语言回答生成 ────────────────────────────────────────────────

_CHAT_ANSWER_SYSTEM = """你是 WealthPilot 个人投资助手。系统已对用户的投资决策请求完成了完整分析，现在由你用自然对话的方式把结论和理由讲给用户听。

输出必须包含以下三部分，缺一不可：
1. 【建议判断】直接说明建议（加仓/观望/减仓），结合持仓比例或关键事件说清楚为什么
2. 【推理解释】引用实际数据（如仓位 X%、纪律上限 Y%、收益率 Z% 等），说明推理逻辑
3. 【行动方向 + 风险】给出可操作的建议，并点出至少一个需要警惕的具体风险

格式要求：
- 禁止以"综合分析来看"、"根据系统分析"、"基于上述"等套话开头
- 禁止使用【结论】【原因】【建议】【风险提示】等机械标题，三部分之间用自然过渡
- 语气专业克制，像一个了解你情况的私人投顾，不是机器人念报告
- 可以用 **粗体** 强调关键词（建议、数字、标的名），不要每句都加
- 字数不低于 180 字，不超过 280 字，内容充实，不得因字数限制而省略任何一部分"""


def generate_chat_answer(
    user_query: str,
    intent,       # IntentResult
    data,         # LoadedData | None
    rules,        # RuleResult | None
    llm_result,   # LLMResult
) -> str:
    """
    基于完整决策链路结果，生成面向用户的自然语言对话回答。
    使用 Sonnet 模型（质量优先）。失败时返回简洁 fallback 文本。
    """
    asset = (intent.asset or "该标的") if intent else "该标的"
    decision_cn = {"BUY": "加仓", "HOLD": "观望", "SELL": "减仓"}.get(
        llm_result.decision, "观望"
    )

    # 持仓上下文
    if data and data.target_position:
        tp = data.target_position
        pos_desc = (
            f"{asset} 当前仓位 {tp.weight:.1%}，"
            f"市值约 ¥{tp.market_value_cny:,.0f}，"
            f"持仓收益率 {tp.profit_loss_rate:+.1%}"
        )
    else:
        pos_desc = f"当前未持有 {asset}（新建仓场景）"

    # 纪律约束
    if rules:
        if rules.violation:
            rule_desc = (
                f"仓位已超限：当前 {rules.current_weight:.1%}，"
                f"上限 {rules.max_position:.1%}，已超出 {rules.current_weight - rules.max_position:.1%}"
            )
        elif rules.warning:
            rule_desc = f"仓位接近上限：当前 {rules.current_weight:.1%}，上限 {rules.max_position:.1%}"
        else:
            rule_desc = f"仓位合规：当前 {rules.current_weight:.1%}，上限 {rules.max_position:.1%}"
    else:
        rule_desc = "无规则数据"

    reasoning_text = "\n".join(f"- {r}" for r in llm_result.reasoning) if llm_result.reasoning else "（无）"
    strategy_text  = "\n".join(f"- {s}" for s in llm_result.strategy)  if llm_result.strategy  else "（无）"
    risk_text      = "\n".join(f"- {r}" for r in llm_result.risk)      if llm_result.risk      else "（无）"

    user_content = f"""用户问题：{user_query}

系统决策建议：{decision_cn}（{llm_result.decision}）

持仓情况：{pos_desc}
纪律约束：{rule_desc}

AI 推理依据：
{reasoning_text}

操作建议：
{strategy_text}

风险提示：
{risk_text}

请基于以上完整信息，用自然对话方式向用户解释这个决策建议。"""

    try:
        client = _get_client()
        resp = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=800,
            system=_CHAT_ANSWER_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        return resp.content[0].text.strip()
    except Exception:
        # Fallback：简洁直接，不用机械模板
        tb_lines = traceback.format_exc()
        print(f"[llm_engine.generate_chat_answer] 失败:\n{tb_lines}", flush=True)
        reasons = "；".join(llm_result.reasoning[:2]) if llm_result.reasoning else ""
        return (
            f"**{asset}** 当前建议**{decision_cn}**。"
            + (f"\n\n{reasons}。" if reasons else "")
        )


def _fallback_result(error_msg: str, decision: str = "HOLD") -> LLMResult:
    """API 异常或解析失败时的降级结果（PRD §3.8：数据缺失→默认HOLD）。"""
    return LLMResult(
        decision=decision,
        reasoning=["当前无法完成 AI 推理，建议保持观望。"],
        risk=["请稍后重试，或手动评估当前持仓风险。"],
        strategy=["维持当前仓位，等待更多信息后再做决策。"],
        raw_output="",
        error=error_msg,
    )
