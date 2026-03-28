"""
IntentEngine — 意图体系主入口

对应工程PRD §1.2（整体数据流）。

数据流：
    用户输入（自然语言）
        ↓
    [IntentRecognizer]  — LLM调用 #1，输出 IntentPayload
        ↓
    [ContextManager]    — 合并历史上下文，输出 ExecutionContext
        ↓
    [Orchestrator]      — 生成执行计划，输出 ExecutionPlan
        ↓
    [SubtaskRunner]     — 按计划执行子任务（每 Subtask 含 LLM 调用）
        ↓
    [OutputRenderer]    — 聚合输出，LLM 最终整合
        ↓
    最终响应

异常处理（PRD §5）：
    - IntentRecognizer 连续失败 → Education 兜底 + 澄清问题
    - confidence < 0.5 → 不执行，返回澄清问题
    - Subtask 失败 → 标记 skipped，继续执行，OutputRenderer 章节注明
    - TODO Phase 2: 合规拦截（KYC 未通过 + 交易类 Action → 拦截）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from . import context_manager, orchestrator, output_renderer, subtask_runner
from .intent_recognizer import recognize
from .types import (
    ExecutionContext,
    ExecutionPlan,
    IntentPayload,
    SubtaskResult,
    Turn,
)


# ── 输出数据类 ────────────────────────────────────────────────────────────────

@dataclass
class EngineResult:
    """意图引擎完整执行结果（供 UI 层消费）"""
    # 意图识别
    intent_payload: Optional[IntentPayload]
    confidence: float

    # 执行上下文
    context: Optional[ExecutionContext]

    # 执行计划
    plan: Optional[ExecutionPlan]

    # 各 Subtask 结果
    subtask_results: List[SubtaskResult]

    # 最终输出文本
    final_output: str

    # 澄清问题（confidence 低时非空）
    clarification_question: Optional[str]

    # 是否被中断（confidence < 0.5）
    aborted: bool = False
    abort_reason: Optional[str] = None

    @property
    def primary_intent(self) -> Optional[str]:
        return self.intent_payload.primary_intent if self.intent_payload else None


# ── 主入口 ────────────────────────────────────────────────────────────────────

def run(
    user_input: str,
    session_id: str,
    user_id: str = "default",
    portfolio_id: Optional[int] = None,
) -> EngineResult:
    """
    意图体系完整执行流程（PRD §1.2 整体数据流）。

    Args:
        user_input:   用户自然语言输入
        session_id:   会话唯一标识（多轮对话使用同一 session_id）
        user_id:      用户 ID（预留，目前未使用）
        portfolio_id: 用户组合 ID（传入 SubtaskRunner 的数据加载）

    Returns:
        EngineResult，包含意图、计划、各步骤结果和最终输出
    """

    # ── Step 1: IntentRecognizer（PRD §3.1）─────────────────────────────────
    try:
        payload, clarification = recognize(user_input)
    except EnvironmentError as e:
        return EngineResult(
            intent_payload=None,
            confidence=0.0,
            context=None,
            plan=None,
            subtask_results=[],
            final_output=f"⚙️ 系统未配置 API Key，无法运行意图识别。\n错误：{e}",
            clarification_question=None,
            aborted=True,
            abort_reason="no_api_key",
        )

    # confidence < 0.5 → 中断执行，返回澄清问题（PRD §5.1）
    if payload.confidence < 0.5 and clarification:
        return EngineResult(
            intent_payload=payload,
            confidence=payload.confidence,
            context=None,
            plan=None,
            subtask_results=[],
            final_output=clarification,
            clarification_question=clarification,
            aborted=True,
            abort_reason="low_confidence",
        )

    # TODO Phase 2: 合规拦截（PRD §5.4）
    # if not payload KYC check + trade action → abort with compliance message

    # ── Step 2: ContextManager（PRD §3.2）───────────────────────────────────
    ctx = context_manager.build_context(
        session_id=session_id,
        intent_payload=payload,
        portfolio_id=portfolio_id,
    )

    # ── Step 3: Orchestrator（PRD §3.3）─────────────────────────────────────
    plan = orchestrator.generate_plan(ctx)

    # ── Step 4: SubtaskRunner（PRD §3.4）────────────────────────────────────
    subtask_results = subtask_runner.run(plan, ctx)

    # ── Step 5: OutputRenderer（PRD §3.5）───────────────────────────────────
    final_output = output_renderer.render(subtask_results, ctx)

    # ── Step 6: 保存本轮对话摘要（PRD §3.2 会话历史维护）────────────────────
    turn = Turn(
        turn_index=ctx.turn_index,
        intent=payload.primary_intent,
        entities_snapshot={
            k: str(v)
            for k, v in {
                "asset": payload.entities.asset,
                "capital": payload.entities.capital,
                "time_horizon": payload.entities.time_horizon,
            }.items()
            if v is not None
        },
        summary=_summarize_output(final_output),
    )
    context_manager.save_turn(session_id, turn)

    return EngineResult(
        intent_payload=payload,
        confidence=payload.confidence,
        context=ctx,
        plan=plan,
        subtask_results=subtask_results,
        final_output=final_output,
        clarification_question=clarification,
        aborted=False,
    )


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def _summarize_output(text: str, max_len: int = 100) -> str:
    """从最终输出中提取摘要（用于注入下轮对话的历史背景）。"""
    # 取第一行非空文字作为摘要（通常是"当前情况概述"章节的开头）
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and len(line) > 10:
            return line[:max_len]
    return text[:max_len]
