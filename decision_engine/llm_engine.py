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

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import anthropic

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

    # 持仓摘要（TOP5）
    top_positions = sorted(data.positions, key=lambda p: p.weight, reverse=True)[:5]
    position_summary = [
        {"name": p.name, "weight": f"{p.weight:.1%}", "asset_class": p.asset_class}
        for p in top_positions
    ]

    # 目标持仓信息
    target_info = None
    if data.target_position:
        tp = data.target_position
        target_info = {
            "name": tp.name,
            "current_weight": f"{tp.weight:.1%}",
            "profit_loss_rate": f"{tp.profit_loss_rate:.1%}",
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
