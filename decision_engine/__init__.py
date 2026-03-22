"""
WealthPilot — 投资决策引擎（Investment Decision Engine）

流程：用户输入 → 意图解析 → 数据加载 → 前置校验 → 规则校验 → 信号生成 → LLM推理 → 结果展示

使用入口：decision_flow.run(user_input, portfolio_id)
"""

from .decision_flow import run, DecisionResult

__all__ = ["run", "DecisionResult"]
