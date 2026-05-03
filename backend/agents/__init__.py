"""
WealthPilot v3.0 Multi-Agent 模块。

PEER 4 Agent 协作模式：
- PlanningAgent：意图识别 + Skill 选择 + 路由
- ExecutingAgent：数据加载 + 信号生成 + 纪律校验
- ExpressingAgent：调用 LLM 生成自然语言（流式）
- ReviewingAgent：输出校验 + 评分 + 重试决策
"""

from .contracts import (
    AgentTaskStatus,
    PlanningOutput,
    ExecutionOutput,
    ExpressionOutput,
    ReviewOutput,
)

__all__ = [
    "AgentTaskStatus",
    "PlanningOutput",
    "ExecutionOutput",
    "ExpressionOutput",
    "ReviewOutput",
]
