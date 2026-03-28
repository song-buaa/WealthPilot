"""
decision_engine 公共数据类型

IntentResult 是 decision_engine 内部的意图表示，由 intent_engine 的适配器
（strategy.py: _payload_to_intent_result）从 IntentPayload 转换而来，
供 decision_flow / rule_engine / signal_engine / llm_engine 使用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class IntentResult:
    """
    投资意图解析结果（decision_engine 内部格式）。

    由 intent_engine.IntentPayload 经适配器转换后注入，不再由 intent_parser 生成。
    """
    asset: Optional[str]              # 标的名称，如 "理想汽车"；None 表示未识别
    action_type: str                  # 加仓判断 / 减仓判断 / 持有评估 / 买入判断 / 卖出判断
    time_horizon: str                 # 短期 / 中期 / 长期 / 未知
    trigger: Optional[str]            # 触发事件，如 "发布会"；可为 None
    confidence_score: float           # 0~1，意图识别置信度
    clarification: Optional[str] = None  # 澄清问题（低置信度时）
    intent_type: str = "investment_decision"  # 固定值，decision_engine 只处理 investment_decision
    is_context_inherited: bool = False        # 是否有字段继承自上轮对话

    @property
    def needs_clarification(self) -> bool:
        return self.confidence_score < 0.6
