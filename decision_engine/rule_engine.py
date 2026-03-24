"""
规则校验模块 (Rule Engine)

职责：执行硬规则校验，检测仓位是否违规或临近上限。
输出结构化结果，供信号层和 LLM 使用。

规则（来自 PRD）：
    position_ratio = current_position / max_position
    >= 1.0  → violation = True（超限，拦截加仓）
    >= 0.8  → warning = "接近上限"
    < 0.8   → 正常
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .data_loader import LoadedData, InvestmentRules
from .intent_parser import IntentResult


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class RuleResult:
    """规则校验输出"""
    position_ratio: float          # 当前仓位 / 上限（0~1+）
    current_weight: float          # 当前仓位占比（0~1）
    max_position: float            # 单一持仓上限（0~1）
    violation: bool                # True = 超限，拦截加仓操作
    warning: Optional[str]         # 警告文本（violation=False 时可能有）
    rule_details: list[str] = field(default_factory=list)  # 所有规则检查摘要

    @property
    def status_label(self) -> str:
        """返回状态标签，供 UI 展示。"""
        if self.violation:
            return "超限 ⛔"
        if self.warning:
            return f"警告 ⚠️  {self.warning}"
        return "正常 ✅"


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def check(data: LoadedData, intent: IntentResult) -> RuleResult:
    """
    执行规则校验。

    Args:
        data: LoadedData（含持仓和规则配置）
        intent: IntentResult（用于判断操作类型）

    Returns:
        RuleResult
    """
    rules: InvestmentRules = data.rules
    details: list[str] = []

    # ── 仓位比率计算 ────────────────────────────────────────────────────────
    current_weight = 0.0
    if data.target_position is not None:
        current_weight = data.target_position.weight

    max_pos = rules.max_single_position  # 已转换为小数（0~1）
    # 避免除零
    position_ratio = current_weight / max_pos if max_pos > 0 else 0.0

    # ── 规则判断 ─────────────────────────────────────────────────────────────
    violation = False
    warning: Optional[str] = None

    if position_ratio >= 1.0:
        violation = True
        details.append(
            f"⛔ 单一持仓超限：当前 {current_weight:.1%} ≥ 上限 {max_pos:.1%}"
        )
    elif position_ratio >= 0.8:
        warning = "接近上限"
        details.append(
            f"⚠️  单一持仓接近上限：当前 {current_weight:.1%}，"
            f"上限 {max_pos:.1%}（已用 {position_ratio:.0%}）"
        )
    else:
        details.append(
            f"✅ 单一持仓正常：当前 {current_weight:.1%}，"
            f"上限 {max_pos:.1%}（已用 {position_ratio:.0%}）"
        )

    # ── 加仓特殊规则：目标不在持仓中，仓位为 0，提示 ───────────────────────
    if data.target_position is None and intent.action_type in ("加仓判断", "买入判断"):
        details.append("ℹ️  当前持仓中未持有该标的，属于新建仓操作。")

    # ── 操作类型为减仓/卖出时，持仓为 0 的情况 ──────────────────────────────
    if current_weight == 0.0 and intent.action_type in ("减仓判断", "卖出判断"):
        violation = True
        warning = "未持有该标的，无法减仓"
        details.append(f"⛔ 未持有该标的，无法执行减仓/卖出操作。")

    return RuleResult(
        position_ratio=position_ratio,
        current_weight=current_weight,
        max_position=max_pos,
        violation=violation,
        warning=warning,
        rule_details=details,
    )
