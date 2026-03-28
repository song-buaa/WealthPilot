"""
OutputRenderer — 输出渲染模块

对应工程PRD §3.5。

职责：
    聚合所有 SubtaskResult，按输出模板生成最终响应。

LLM 调用规格（PRD §3.5）：
    - 调用次数：1次（整合 Subtask 结果为连贯的最终输出）
    - 输出格式：自然语言（面向用户）
    - 流式输出：当前返回完整字符串；TODO Phase 2: SSE 流式推送

PositionDecision 输出模板（PRD §3.5）：
    1. 当前情况概述
    2. 核心逻辑判断（投资逻辑是否仍成立）
    3. 风险评估
    4. 操作建议（BUY / SELL / ADD / REDUCE / HOLD）
    5. 风险提示（合规）

Action 对输出的影响（PRD §3.5）：
    SELL / STOP_LOSS  → 操作建议章节强调风险，合规提示前置
    BUY / ADD         → 增加"买入理由"小节
    TAKE_PROFIT       → 输出"止盈逻辑"与"持续持有"两种路径对比
    ANALYZE           → 不输出操作建议章节，仅输出分析

TODO Phase 2: SSE 流式推送（output_chunk 按字符级）
TODO Phase 2: output_section_start 事件推送
"""
from __future__ import annotations

import json
import traceback
from typing import Dict, List, Optional

import openai

from .types import (
    ExecutionContext,
    SubtaskResult,
    TRADE_ACTIONS,
)
from ._llm_client import MODEL_MAIN, get_client

# 最终输出的最大 token 数
_MAX_TOKENS_FINAL = 1500


# ── 主入口 ────────────────────────────────────────────────────────────────────

def render(
    subtask_results: List[SubtaskResult],
    ctx: ExecutionContext,
) -> str:
    """
    聚合 Subtask 结果，调用 LLM 生成最终面向用户的输出（PRD §3.5）。

    Args:
        subtask_results: SubtaskRunner 返回的全部结果
        ctx:             当前执行上下文

    Returns:
        最终响应文本（面向用户，自然语言）
    """
    intent = ctx.intent_payload.primary_intent
    actions = ctx.intent_payload.actions

    # 构建整合 prompt
    prompt = _build_render_prompt(intent, actions, subtask_results, ctx)

    try:
        client = get_client()
        response = client.chat.completions.create(
            model=MODEL_MAIN,
            max_tokens=_MAX_TOKENS_FINAL,
            timeout=60,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()

    except EnvironmentError:
        raise

    except openai.APITimeoutError:
        return _fallback_render(subtask_results, ctx, "输出生成超时，以下为原始分析结果：")

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[OutputRenderer] LLM 调用失败:\n{tb}", flush=True)
        return _fallback_render(subtask_results, ctx, "输出整合遇到问题，以下为原始分析结果：")


# ── Prompt 构建 ───────────────────────────────────────────────────────────────

def _build_render_prompt(
    intent: str,
    actions: List[str],
    results: List[SubtaskResult],
    ctx: ExecutionContext,
) -> str:
    """根据 Intent 类型和 Action 生成整合 prompt。"""
    asset = ctx.inherited_fields.asset or ctx.intent_payload.entities.asset or "该标的"
    results_text = _format_subtask_results(results)
    action_guidance = _get_action_guidance(actions)
    template = _get_output_template(intent, actions)

    return f"""\
你是 WealthPilot 的个人投资助手，负责将多项分析结果整合为一份面向用户的投资分析报告。

# 各项分析结果（由系统自动完成）
{results_text}

# 用户信息
- 标的：{asset}
- 风险偏好：{ctx.user_profile.risk_level}
- 投资目标：{ctx.user_profile.goal}
- 操作意图：{', '.join(actions) if actions else '分析评估'}

# 输出结构要求
请严格按照以下结构输出，每个章节用 ## 标题标出：
{template}

# 输出规范
{action_guidance}
- 语言：中文，语气克制专业，像私人投顾在解释而非 AI 在汇报
- 字数：整体控制在 400~600 字
- 对于 skipped 或 failed 的分析项，在对应章节注明"该部分分析暂时不可用"
- 不得使用绝对性表达（如"必须买入"、"一定会涨"）
- 最后必须包含免责声明：本分析仅供参考，不构成投资建议\
"""


def _get_output_template(intent: str, actions: List[str]) -> str:
    """按 Intent 和 Action 返回输出章节模板（PRD §3.5）。"""
    if intent == "PositionDecision":
        # ANALYZE 信息类 → 不输出操作建议章节（PRD §3.5 Action 对输出结构的影响）
        if actions == ["ANALYZE"] or not (set(actions) & TRADE_ACTIONS):
            return """\
## 1. 当前情况概述
## 2. 核心逻辑判断
## 3. 风险评估
## 4. 免责声明"""

        # SELL / STOP_LOSS → 合规提示前置（PRD §3.5）
        if set(actions) & {"SELL", "STOP_LOSS"}:
            return """\
## 1. 当前情况概述
## 2. 核心逻辑判断
## 3. 风险评估
## 4. 操作建议（需说明卖出/止损理由，强调风险）
## 5. 风险提示"""

        # BUY / ADD → 增加"买入理由"（PRD §3.5）
        if set(actions) & {"BUY", "ADD"}:
            return """\
## 1. 当前情况概述
## 2. 核心逻辑判断
## 3. 买入理由
## 4. 风险评估
## 5. 操作建议
## 6. 风险提示"""

        # TAKE_PROFIT → 对比两条路径（PRD §3.5）
        if "TAKE_PROFIT" in actions:
            return """\
## 1. 当前情况概述
## 2. 核心逻辑判断
## 3. 止盈路径分析
## 4. 持续持有路径分析
## 5. 操作建议
## 6. 风险提示"""

        # 默认 PositionDecision 模板
        return """\
## 1. 当前情况概述
## 2. 核心逻辑判断
## 3. 风险评估
## 4. 操作建议
## 5. 风险提示"""

    elif intent == "PortfolioReview":
        return """\
## 1. 组合结构分析
## 2. 风险与集中度情况
## 3. 偏离目标情况
## 4. 是否需要调整
## 5. 调整方向建议"""

    elif intent == "AssetAllocation":
        return """\
## 1. 目标与约束说明
## 2. 配置原则
## 3. 资产分配方案
## 4. 风险说明"""

    elif intent == "PerformanceAnalysis":
        return """\
## 1. 收益总览
## 2. 关键驱动因素
## 3. 亏损/波动来源
## 4. 改进建议"""

    else:  # Education / fallback
        return """\
## 1. 概念/规则解释
## 2. 结合用户场景的示例（如有）"""


def _get_action_guidance(actions: List[str]) -> str:
    """返回 Action 相关的额外输出指引（PRD §3.5 Action 对输出结构的影响）。"""
    if set(actions) & {"SELL", "STOP_LOSS"}:
        return "- 操作建议章节必须强调风险，合规提示前置\n"
    elif set(actions) & {"BUY", "ADD"}:
        return "- 操作建议章节包含'买入理由'小节，说明核心逻辑\n"
    elif "TAKE_PROFIT" in actions:
        return "- 输出止盈逻辑与持续持有两种路径的对比分析\n"
    elif not (set(actions) & TRADE_ACTIONS):
        return "- 不输出具体买卖操作建议，仅输出分析结论\n"
    return ""


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _format_subtask_results(results: List[SubtaskResult]) -> str:
    """将 SubtaskResult 列表格式化为 prompt 可读文本。"""
    lines = []
    for r in results:
        status_label = {"success": "✓", "failed": "✗", "skipped": "—"}.get(r.status, "?")
        lines.append(f"### [{status_label}] {r.subtask}")
        if r.status == "success":
            lines.append(r.content)
        else:
            lines.append(f"该部分分析暂时不可用（{r.content}）")
        lines.append("")
    return "\n".join(lines)


def _fallback_render(
    results: List[SubtaskResult],
    ctx: ExecutionContext,
    prefix: str,
) -> str:
    """
    LLM 调用失败时的降级输出（PRD §5.3）。
    直接拼接各 Subtask 的原始分析结果。
    """
    lines = [prefix, ""]
    for r in results:
        if r.status == "success":
            lines.append(f"**{r.subtask}**")
            lines.append(r.content)
            lines.append("")
        elif r.status == "skipped":
            lines.append(f"**{r.subtask}**：该部分分析暂时不可用")
            lines.append("")
    lines.append("---")
    lines.append("*本分析仅供参考，不构成投资建议。*")
    return "\n".join(lines)
