"""
WealthPilot — 投资决策执行层（Investment Decision Engine）

职责：接收已解析的意图，执行数据加载→规则校验→信号生成→LLM推理管道，返回 DecisionResult。
意图识别由 intent_engine 统一负责，本层不做路由。

使用入口：decision_flow.run_with_intent(intent, user_input, portfolio_id)
"""

from .decision_flow import run_with_intent, DecisionResult
from .types import IntentResult
from .llm_engine import GenericLLMResult

__all__ = ["run_with_intent", "DecisionResult", "IntentResult", "GenericLLMResult"]
