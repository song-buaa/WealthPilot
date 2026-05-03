"""
v3.0 Skills Output → 业务对象 Adapter 集合。

设计动机：
- M2 Tool 输出的是工程契约（轻量、独立可序列化的 dataclass/BaseModel）
- ExpressingAgent 消费的是业务契约（含完整上下文的 dataclass）
- Adapter 层负责类型映射，遵循 Adapter 模式

每个 Adapter 函数的职责单一：
- 接收 Skill 调用返回的 Output 对象
- 转换为下游业务对象
- 不做副作用，纯函数
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.graph.tools import (
        DisciplineCheckOutput,
        GenerateSignalsOutput,
    )
    from decision_engine.rule_engine import RuleResult
    from decision_engine.signal_engine import SignalResult


def discipline_output_to_rule_result(
    output: "DisciplineCheckOutput",
) -> "RuleResult":
    """
    DisciplineCheckOutput → RuleResult。

    字段映射（6 个字段完全同名同义）：
    - violation:        bool         → bool
    - warning:          Optional[str]→ Optional[str]
    - current_weight:   float        → float
    - max_position:     float        → float
    - position_ratio:   float        → float
    - rule_details:     list[str]    → list[str]

    RuleResult.status_label 是 property（自动从 violation/warning 计算），
    不需要 Adapter 处理。
    """
    from decision_engine.rule_engine import RuleResult

    return RuleResult(
        position_ratio=output.position_ratio,
        current_weight=output.current_weight,
        max_position=output.max_position,
        violation=output.violation,
        warning=output.warning,
        rule_details=list(output.rule_details) if output.rule_details else [],
    )


def signals_output_to_signal_result(
    output: "GenerateSignalsOutput",
) -> "SignalResult":
    """
    GenerateSignalsOutput → SignalResult。

    字段映射：
    - position_signal:    str → str（直接复制）
    - fundamental_signal: str → str（直接复制）
    - sentiment_signal:   str → str（直接复制）
    - event_uncertainty + event_direction: 两个扁平字段 → 嵌套构造 EventSignal
    - asset_name / error: 丢弃（SignalResult 不需要）

    Step 8b-3 才使用此 Adapter。
    """
    from decision_engine.signal_engine import SignalResult, EventSignal

    event_signal = EventSignal(
        uncertainty=output.event_uncertainty,
        direction=output.event_direction,
    )

    return SignalResult(
        position_signal=output.position_signal,
        event_signal=event_signal,
        fundamental_signal=output.fundamental_signal,
        sentiment_signal=output.sentiment_signal,
    )
