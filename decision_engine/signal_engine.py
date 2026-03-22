"""
信号生成模块 (Signal Engine) ⭐ 核心

职责：基于数据和规则，生成 4 个维度的结构化信号，送入 LLM。

信号维度（PRD 固定）：
    1. 仓位信号（position_signal）：偏高 / 合理 / 偏低
    2. 事件信号（event_signal）：{uncertainty: 高/中/低, direction: 利好/中性/利空}
    3. 基本面信号（fundamental_signal）：正面 / 中性 / 负面 / N/A
    4. 情绪信号（sentiment_signal）：中性（MVP 固定）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .data_loader import LoadedData
from .intent_parser import IntentResult
from .rule_engine import RuleResult


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class EventSignal:
    """事件不确定性信号"""
    uncertainty: str   # 高 / 中 / 低
    direction: str     # 利好 / 中性 / 利空


@dataclass
class SignalResult:
    """信号层完整输出"""
    position_signal: str      # 偏高 / 合理 / 偏低
    event_signal: EventSignal
    fundamental_signal: str   # 正面 / 中性 / 负面 / N/A
    sentiment_signal: str     # 中性（MVP 固定）

    def to_dict(self) -> dict:
        """序列化为字典，供 LLM Engine 拼接 prompt 使用。"""
        return {
            "position_signal": self.position_signal,
            "event_signal": {
                "uncertainty": self.event_signal.uncertainty,
                "direction": self.event_signal.direction,
            },
            "fundamental_signal": self.fundamental_signal,
            "sentiment_signal": self.sentiment_signal,
        }

    def summary_lines(self) -> list[str]:
        """生成人类可读的信号摘要，供 UI 展示。"""
        lines = []
        lines.append(f"仓位信号：{self.position_signal}")
        lines.append(
            f"事件信号：不确定性 {self.event_signal.uncertainty}，"
            f"方向 {self.event_signal.direction}"
        )
        lines.append(f"基本面信号：{self.fundamental_signal}")
        lines.append(f"情绪信号：{self.sentiment_signal}")
        return lines


# ── 关键词库 ─────────────────────────────────────────────────────────────────

_POSITIVE_KEYWORDS = {"看好", "增长", "超预期", "利好", "回暖", "机会", "上涨", "突破", "扩张", "健康"}
_NEGATIVE_KEYWORDS = {"风险", "下滑", "承压", "利空", "下跌", "减速", "亏损", "出清", "压力", "担忧"}


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def generate(
    data: LoadedData,
    intent: IntentResult,
    rule_result: RuleResult,
) -> SignalResult:
    """
    生成 4 个维度的信号。

    Args:
        data: LoadedData（含投研观点等）
        intent: IntentResult（含 trigger）
        rule_result: RuleResult（含 position_ratio）

    Returns:
        SignalResult
    """
    return SignalResult(
        position_signal=_compute_position_signal(rule_result),
        event_signal=_compute_event_signal(intent),
        fundamental_signal=_compute_fundamental_signal(data),
        sentiment_signal="中性",   # MVP 阶段固定为中性
    )


def _compute_position_signal(rule_result: RuleResult) -> str:
    """
    仓位信号：基于 position_ratio（当前仓位 / 上限）。
        >= 0.8 → 偏高
        0.4 ~ 0.8 → 合理
        <= 0.4 → 偏低
    """
    ratio = rule_result.position_ratio
    if ratio >= 0.8:
        return "偏高"
    elif ratio >= 0.4:
        return "合理"
    else:
        return "偏低"


def _compute_event_signal(intent: IntentResult) -> EventSignal:
    """
    事件信号：
        MVP 简化规则：
        - 若存在 trigger → uncertainty=高, direction=中性（无法判断方向）
        - 否则 → uncertainty=低, direction=中性
    """
    if intent.trigger:
        return EventSignal(uncertainty="高", direction="中性")
    else:
        return EventSignal(uncertainty="低", direction="中性")


def _compute_fundamental_signal(data: LoadedData) -> str:
    """
    基本面信号：基于投研观点关键词匹配。
        包含看好/增长等正面词 → 正面
        包含风险/下滑等负面词 → 负面
        无投研观点 → N/A
        无明显倾向 → 中性
    """
    if not data.research:
        return "N/A"

    # 检查是否是默认提示（无真实观点）
    if len(data.research) == 1 and "暂无" in data.research[0]:
        return "N/A"

    combined = " ".join(data.research)
    pos_hits = sum(1 for kw in _POSITIVE_KEYWORDS if kw in combined)
    neg_hits = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in combined)

    if pos_hits > neg_hits:
        return "正面"
    elif neg_hits > pos_hits:
        return "负面"
    else:
        return "中性"
