"""
v3.0 决策服务 - 4 Agent 协作链路。

通过环境变量 USE_V3_AGENTS=1 启用。
默认情况下 decision_service.py 仍走 v2.6 _stream_* 函数链。

核心入口：run_chat_stream_v3()
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import AsyncGenerator, Optional

from backend.agents import (
    get_planning_agent,
    get_executing_agent,
    get_expressing_agent,
    get_reviewing_agent,
)
from backend.agents.contracts import AgentTaskStatus

logger = logging.getLogger(__name__)


def _sse(event_type: str, data: dict) -> str:
    """SSE 事件格式化（同 v2.6）。"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def run_chat_stream_v3(
    user_input: str,
    session_id: str,
    portfolio_id: int = 1,
    conversation_history: Optional[list[dict]] = None,
) -> AsyncGenerator[str, None]:
    """
    v3.0 决策流：4 Agent 协作。

    Planning → Executing → Expressing(流式) → Reviewing → done
    """
    decision_id = f"decision_{uuid.uuid4().hex[:12]}"

    try:
        # ── Stage 1: PlanningAgent ──
        yield _sse("stage", {"stage": "intent", "label": "意图识别中..."})

        planning = get_planning_agent()
        plan_out = await asyncio.to_thread(
            planning.run,
            user_input, session_id, portfolio_id, conversation_history,
        )

        if plan_out.status == AgentTaskStatus.FAILED:
            yield _sse("error", {
                "code": "planning_failed",
                "message": plan_out.error or "意图识别失败",
            })
            return

        # yield intent 事件（兼容 v2.6 协议）
        intent = plan_out.intent or {}
        if isinstance(intent, dict):
            yield _sse("intent", {
                "primary_intent": intent.get("primary_intent", ""),
                "asset": intent.get("asset"),
                "action": None,
                "confidence": intent.get("confidence", 0),
                "needs_clarification": plan_out.needs_clarification,
                "planner_route": plan_out.route,
                "planner_rationale": plan_out.rationale,
            })
        else:
            yield _sse("intent", {
                "primary_intent": "",
                "asset": None,
                "action": None,
                "confidence": 0,
                "needs_clarification": plan_out.needs_clarification,
                "planner_route": plan_out.route,
                "planner_rationale": plan_out.rationale,
            })

        # ── 特殊路由直通 ──
        if plan_out.route == "low_confidence":
            yield _sse("text", {
                "delta": "您的问题我没太理解清楚，能再具体描述一下吗？"
            })
            yield _sse("done", {
                "decision_id": None,
                "conclusion_level": None,
                "conclusion_label": None,
            })
            return

        if plan_out.route == "clarify":
            # v3 clarify: 走 v2.6 的澄清逻辑（候选清单）
            # 简化处理：直接返回文本提示
            yield _sse("text", {
                "delta": "请问您具体想分析哪个标的？可以告诉我标的名称或代码。"
            })
            yield _sse("done", {
                "decision_id": None,
                "conclusion_level": None,
                "conclusion_label": None,
            })
            return

        # ── Stage 2: ExecutingAgent ──
        yield _sse("stage", {"stage": "loading", "label": "加载数据..."})

        executing = get_executing_agent()
        exec_out = await asyncio.to_thread(
            executing.run, plan_out, user_input,
        )

        if exec_out.status == AgentTaskStatus.FAILED:
            yield _sse("error", {
                "code": "execution_failed",
                "message": exec_out.error or "数据加载失败",
            })
            return

        if exec_out.aborted:
            if exec_out.abort_chat_answer:
                from backend.agents.expressing_agent import _emit_text_chunks
                async for chunk in _emit_text_chunks(exec_out.abort_chat_answer):
                    yield _sse("text", {"delta": chunk})
            yield _sse("done", {
                "decision_id": decision_id,
                "conclusion_level": None,
                "conclusion_label": exec_out.abort_reason,
            })
            return

        # Execution SKIPPED (general/clarify) → 直接到 Expressing
        # ── Stage 3: ExpressingAgent（流式）──
        yield _sse("stage", {"stage": "reasoning", "label": "AI 推理中..."})

        expressing = get_expressing_agent()
        async for chunk in expressing.run_streaming(
            plan_out, exec_out, user_input, conversation_history,
        ):
            yield _sse("text", {"delta": chunk})

        expr_out = expressing.last_output

        if expr_out is None or expr_out.status == AgentTaskStatus.FAILED:
            yield _sse("error", {
                "code": "expression_failed",
                "message": (expr_out.error if expr_out else None) or "LLM 推理失败",
            })
            return

        # ── Stage 4: ReviewingAgent ──
        reviewing = get_reviewing_agent()
        review_out = await asyncio.to_thread(
            reviewing.run, plan_out, exec_out, expr_out, user_input,
        )

        if review_out.action == "retry":
            yield _sse("validator_warning", {
                "message": f"输出质量待优化（评分 {review_out.score:.2f}）",
                "failed_rules": review_out.failed_rules,
            })

        # ── Stage 5: done 事件 ──
        done_payload = _build_done_payload(plan_out, expr_out, review_out, decision_id)
        yield _sse("done", done_payload)

    except Exception as e:
        logger.exception(f"[v3] run_chat_stream_v3 异常: {e}")
        yield _sse("error", {"code": "internal_error", "message": str(e)})


def _build_done_payload(plan_out, expr_out, review_out, decision_id: str) -> dict:
    """构造 done 事件 payload（兼容 v2.6 结构）。"""
    validator_payload = {
        "passed": review_out.passed,
        "action": review_out.action,
        "failures": [
            {"rule": r, "message": m, "severity": "hard"}
            for r, m in zip(review_out.failed_rules, review_out.failure_messages)
        ],
    }

    intent = plan_out.intent or {}
    primary_intent = intent.get("primary_intent", "") if isinstance(intent, dict) else ""

    # PositionDecision
    if plan_out.route in ("position_single", "position_multi"):
        from decision_engine.llm_engine import LLMResult
        llm_result = expr_out.llm_result

        if isinstance(llm_result, LLMResult):
            return {
                "decision_id": decision_id,
                "conclusion_level": llm_result.decision,
                "conclusion_label": llm_result.decision_cn,
                "mode": expr_out.mode,
                "decisionResult": expr_out.structured_payload,
                "rawText": expr_out.raw_text,
                "validator": validator_payload,
            }
        return {
            "decision_id": decision_id,
            "conclusion_level": "HOLD",
            "conclusion_label": "观望",
            "mode": "fallback",
            "decisionResult": None,
            "rawText": expr_out.raw_text,
            "validator": validator_payload,
        }

    # Portfolio 类
    if plan_out.route == "portfolio":
        label_map = {
            "PortfolioReview": ("portfolio_review", "组合全面评估", "portfolioResult"),
            "AssetAllocation": ("asset_allocation", "资产配置分析", "allocationResult"),
            "PerformanceAnalysis": ("performance_analysis", "收益表现分析", "performanceResult"),
        }
        level, label, result_key = label_map.get(
            primary_intent, ("portfolio_unknown", primary_intent, "portfolioResult")
        )
        payload = {
            "decision_id": decision_id,
            "conclusion_level": level,
            "conclusion_label": label,
            result_key: expr_out.structured_payload,
            "validator": validator_payload,
        }
        return payload

    # General chat
    return {
        "decision_id": None,
        "conclusion_level": "general_chat",
        "conclusion_label": "普通对话",
    }
