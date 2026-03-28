"""
WealthPilot 意图体系模块 (intent_engine)

Phase 1 实现（工程PRD §7 Phase 1）：
    - IntentRecognizer: LLM 意图识别 + JSON 校验 + 重试
    - ContextManager:   字段继承与重置逻辑（多轮对话）
    - Orchestrator:     单意图执行计划生成
    - SubtaskRunner:    PositionDecision 完整 Subtask 链路
    - OutputRenderer:   PositionDecision 输出模板

主入口：
    from intent_engine.engine import run
    result = run("理想汽车要不要卖？", session_id="s1")
"""
from .engine import EngineResult, run

__all__ = ["run", "EngineResult"]
