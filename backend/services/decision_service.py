"""
Decision Service — 投资决策业务逻辑（含 SSE 流式输出）

从 app_pages/strategy.py 提取的纯业务逻辑，去除所有 Streamlit 依赖。
直接复用：intent_engine, decision_engine（全部不动）

SSE 设计：
  - 使用 asyncio.to_thread() 在线程池中运行阻塞的 LLM 调用
  - 管道各阶段之间 yield SSE 事件，给前端进度反馈
  - 最终文本分块 yield，模拟流式输出
  - DecisionResult 按 session_id 存入进程内字典，供 /explain 端点查询
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import replace as _replace
from typing import AsyncGenerator, Optional

from intent_engine import intent_recognizer, context_manager
from intent_engine.types import IntentEntities
from decision_engine import decision_flow, llm_engine, data_loader
from decision_engine.types import IntentResult
from decision_engine.decision_flow import DecisionResult, FlowStage

# 资产配置模块类型（延迟导入 allocation_ai 避免循环引用）
from app.allocation.types import (
    AllocationChatRequest, SessionContext as AllocationSessionContext,
)


# ── 进程内 decision 缓存（{session_id: {decision_id: DecisionResult}}）────────
# 服务重启后清空是预期行为，无需持久化
_DECISION_STORE: dict[str, dict[str, DecisionResult]] = {}

# ── primary_intent 缓存（intent_engine 输出，decision_engine 不存储）───────────
# key: session_id，value: 该 session 最近一次的 primary_intent 字符串
_PRIMARY_INTENT_CACHE: dict[str, str] = {}

# ── AssetAllocation 意图的 sessionContext 缓存 ─────────────────────────────────
_ALLOC_SESSION_CTX: dict[str, AllocationSessionContext] = {}

# ── AssetAllocation ExplainData 缓存（{session_id:decision_id: dict}）──────────
_ALLOC_EXPLAIN_STORE: dict[str, dict] = {}


# ── 公开接口 ──────────────────────────────────────────────────────────────────

async def run_chat_stream(
    message: str,
    session_id: str,
    portfolio_id: int,
) -> AsyncGenerator[str, None]:
    """
    投资决策 SSE 流式接口核心逻辑。
    每次 yield 一条格式化的 SSE 字符串（已含 "data: ...\n\n"）。
    """
    try:
        # ── Stage 1: 意图识别 ────────────────────────────────────────────────
        yield _sse("stage", {"stage": "intent", "label": "意图识别中..."})

        try:
            payload, clarification = await asyncio.to_thread(
                intent_recognizer.recognize, message
            )
        except EnvironmentError as e:
            yield _sse("error", {"code": "config_error", "message": str(e)})
            return

        # ── 构建多轮上下文 ───────────────────────────────────────────────────
        ctx = await asyncio.to_thread(
            context_manager.build_context, session_id, payload, portfolio_id
        )

        # 低置信度 → 澄清问题（不走决策管道）
        if clarification and payload.confidence < 0.5:
            yield _sse("intent", {
                "primary_intent": payload.primary_intent,
                "confidence": payload.confidence,
                "needs_clarification": True,
            })
            yield _sse("text", {"delta": clarification})
            yield _sse("done", {"decision_id": None, "conclusion_level": None, "conclusion_label": None})
            return

        _PRIMARY_INTENT_CACHE[session_id] = payload.primary_intent
        yield _sse("intent", {
            "primary_intent":    payload.primary_intent,
            "asset":             payload.entities.asset,
            "action":            payload.actions[0] if payload.actions else None,
            "confidence":        payload.confidence,
            "needs_clarification": False,
        })

        # ── 路由分发 ─────────────────────────────────────────────────────────
        if payload.primary_intent == "PositionDecision":
            multi = list(payload.entities.multi_assets or [])

            if len(multi) >= 2:
                # 多标的：依次处理，合并回答
                async for event in _stream_multi_asset(
                    payload, ctx, message, multi, session_id, portfolio_id
                ):
                    yield event
            else:
                # 单标的：完整 6 步管道
                intent_result = _payload_to_intent_result(payload, ctx)
                async for event in _stream_position_decision(
                    intent_result, message, session_id, portfolio_id
                ):
                    yield event

        elif payload.primary_intent in (
            "PortfolioReview", "AssetAllocation", "PerformanceAnalysis"
        ):
            async for event in _stream_portfolio_intent(
                payload, message, session_id, portfolio_id
            ):
                yield event

        else:
            # GeneralChat / Education
            async for event in _stream_general_chat(message):
                yield event

    except Exception as e:
        yield _sse("error", {"code": "internal_error", "message": str(e)})


def get_decision_explain(session_id: str, decision_id: str) -> Optional[dict]:
    """获取某次决策的完整 DecisionResult（序列化为 dict）"""
    # 先检查 allocation explain 缓存
    alloc_key = f"{session_id}:{decision_id}"
    alloc_explain = _ALLOC_EXPLAIN_STORE.get(alloc_key)
    if alloc_explain is not None:
        return alloc_explain

    # 再检查 decision 缓存
    session_store = _DECISION_STORE.get(session_id, {})
    result = session_store.get(decision_id)
    if result is None:
        return None
    d = _serialize_decision_result(result)
    # 补充 primary_intent（来自 intent_engine，decision_engine 不存储）
    primary_intent = _PRIMARY_INTENT_CACHE.get(session_id)
    if primary_intent and "intent" in d:
        d["intent"]["primary_intent"] = primary_intent
    return d


def clear_session(session_id: str) -> None:
    """清除服务端会话（对话重置时调用）"""
    _DECISION_STORE.pop(session_id, None)
    _PRIMARY_INTENT_CACHE.pop(session_id, None)
    _ALLOC_SESSION_CTX.pop(session_id, None)
    # 清理 allocation explain 缓存
    keys_to_remove = [k for k in _ALLOC_EXPLAIN_STORE if k.startswith(f"{session_id}:")]
    for k in keys_to_remove:
        _ALLOC_EXPLAIN_STORE.pop(k, None)
    context_manager.clear_session(session_id)


# ── 内部：各意图路由的流式处理 ────────────────────────────────────────────────

async def _stream_position_decision(
    intent_result: IntentResult,
    user_input: str,
    session_id: str,
    portfolio_id: int,
) -> AsyncGenerator[str, None]:
    """单标的完整 6 步决策管道，逐阶段 yield SSE 事件"""

    yield _sse("stage", {"stage": "loading", "label": "加载持仓数据..."})

    result: DecisionResult = await asyncio.to_thread(
        decision_flow.run_with_intent,
        intent_result,
        user_input,
        portfolio_id,
    )

    # 管道各阶段完成后补发中间阶段事件（给前端进度显示用）
    if result.stage.value in (
        FlowStage.PRE_CHECK.value, FlowStage.RULE_CHECK.value,
        FlowStage.SIGNAL.value, FlowStage.LLM.value, FlowStage.DONE.value,
    ):
        yield _sse("stage", {"stage": "rules",    "label": "规则校验完成"})
        yield _sse("stage", {"stage": "signals",  "label": "信号分析完成"})
        yield _sse("stage", {"stage": "reasoning","label": "AI 推理完成"})

    # 存入缓存
    if result.decision_id:
        _store_result(session_id, result)

    # 生成回答文本并流式 yield
    answer = _build_chat_answer(result, user_input)
    async for chunk_event in _stream_text(answer):
        yield chunk_event

    # done 事件（含 Phase 1 结构化结果）
    conclusion_level, conclusion_label = _extract_conclusion(result)
    done_payload = {
        "decision_id":     result.decision_id,
        "conclusion_level": conclusion_level,
        "conclusion_label": conclusion_label,
    }

    # Phase 1: 附加结构化 DecisionResult
    if result.llm and result.llm.structured_result is not None:
        done_payload["mode"] = "structured"
        done_payload["decisionResult"] = result.llm.structured_result
        done_payload["rawText"] = result.llm.raw_output
    else:
        done_payload["mode"] = "fallback"
        done_payload["decisionResult"] = None
        done_payload["rawText"] = result.llm.raw_output if result.llm else ""

    yield _sse("done", done_payload)


async def _stream_multi_asset(
    payload,
    ctx,
    user_input: str,
    multi_assets: list[str],
    session_id: str,
    portfolio_id: int,
) -> AsyncGenerator[str, None]:
    """多标的同操作分发"""
    yield _sse("stage", {"stage": "loading", "label": f"分析 {len(multi_assets)} 个标的..."})

    results: list[tuple[str, DecisionResult]] = []
    last_decision_id = None

    for asset_name in multi_assets:
        yield _sse("stage", {"stage": "reasoning", "label": f"分析 {asset_name} 中..."})

        new_entities = _replace(payload.entities, asset=asset_name, multi_assets=[])
        single_payload = _replace(payload, entities=new_entities)
        intent_result = _payload_to_intent_result(single_payload, ctx)

        r = await asyncio.to_thread(
            decision_flow.run_with_intent, intent_result, user_input, portfolio_id
        )
        results.append((asset_name, r))
        if r.decision_id:
            _store_result(session_id, r)
            last_decision_id = r.decision_id

    answer = _build_multi_asset_answer(results, user_input)
    async for chunk_event in _stream_text(answer):
        yield chunk_event

    yield _sse("done", {
        "decision_id":     last_decision_id,
        "conclusion_level": "multi_asset",
        "conclusion_label": f"已分析 {len(results)} 个标的",
    })


async def _stream_portfolio_intent(
    payload,
    user_input: str,
    session_id: str,
    portfolio_id: int,
) -> AsyncGenerator[str, None]:
    """组合级别分析（PortfolioReview / AssetAllocation / PerformanceAnalysis）"""

    # ── AssetAllocation：走资产配置模块的完整处理逻辑 ──────────────────
    if payload.primary_intent == "AssetAllocation":
        async for event in _stream_asset_allocation(payload, user_input, session_id, portfolio_id):
            yield event
        return

    # ── 其他组合意图：PortfolioReview / PerformanceAnalysis ─────────
    _INTENT_CONFIG = {
        "PortfolioReview":     ("portfolio_review",    "组合全面评估", llm_engine.review_portfolio),
        "PerformanceAnalysis": ("performance_analysis","收益表现分析", llm_engine.analyze_performance),
    }
    intent_type_key, action_label, llm_fn = _INTENT_CONFIG[payload.primary_intent]

    yield _sse("stage", {"stage": "loading",   "label": "加载组合数据..."})
    yield _sse("stage", {"stage": "reasoning", "label": f"{action_label}中..."})

    decision_id = f"decision_{uuid.uuid4().hex[:8]}"

    def _run():
        loaded = data_loader.load(asset_name=None, pid=portfolio_id)
        if loaded.has_data_errors:
            return None, None
        generic_llm = llm_fn(user_input, loaded)
        return loaded, generic_llm

    loaded, generic_llm = await asyncio.to_thread(_run)

    if loaded is None:
        yield _sse("text", {"delta": "⚠️ 数据加载失败，请先在「投资账户总览」导入持仓数据。"})
        yield _sse("done", {"decision_id": None, "conclusion_level": None, "conclusion_label": None})
        return

    if generic_llm.is_fallback:
        answer = f"⚠️ AI 分析遇到问题：{generic_llm.error}\n\n请稍后重试。"
    else:
        answer = (
            (generic_llm.chat_answer or f"{action_label}完成。")
            + "\n\n---\n*⚖️ 仅供参考，不构成投资建议。投资有风险，入市需谨慎。*"
        )

    async for chunk_event in _stream_text(answer):
        yield chunk_event

    # 存储简化的 explain 数据供右侧面板查询
    _ALLOC_EXPLAIN_STORE[f"{session_id}:{decision_id}"] = {
        "decision_id": decision_id,
        "stage": "done",
        "was_aborted": False,
        "aborted_reason": None,
        "intent": {
            "primary_intent": payload.primary_intent,
            "asset": None,
            "action": action_label,
            "time_context": None,
            "confidence": payload.confidence,
            "intent_type": intent_type_key,
            "is_inherited": False,
        },
        "data": {
            "total_assets": loaded.total_assets if loaded else None,
        },
        "rules": None,
        "signals": None,
        "pre_check": None,
        "llm": {
            "reasoning": [f"基于组合全部持仓数据进行{action_label}"],
        } if not generic_llm.is_fallback else None,
        "generic_llm": {
            "chat_answer": generic_llm.chat_answer,
            "is_fallback": generic_llm.is_fallback,
            "error": generic_llm.error,
        },
    }

    yield _sse("done", {
        "decision_id":     decision_id,
        "conclusion_level": intent_type_key,
        "conclusion_label": action_label,
    })


async def _stream_asset_allocation(
    payload,
    user_input: str,
    session_id: str,
    portfolio_id: int,
) -> AsyncGenerator[str, None]:
    """
    AssetAllocation 意图：调用资产配置模块的完整处理逻辑。
    包含偏离计算、增量分配、纪律校验、强制模板 System Prompt。
    结果存入 _DECISION_STORE 供 /explain 端点查询。
    """
    yield _sse("stage", {"stage": "loading", "label": "加载配置数据..."})

    decision_id = f"decision_{uuid.uuid4().hex[:8]}"

    # 获取或创建该 session 的 allocationSessionContext
    alloc_ctx = _ALLOC_SESSION_CTX.get(session_id, AllocationSessionContext())

    # 构建 AllocationChatRequest
    req = AllocationChatRequest(
        message=user_input,
        conversation_history=[],
        session_context=alloc_ctx,
    )

    yield _sse("stage", {"stage": "reasoning", "label": "资产配置分析中..."})

    try:
        from backend.services.allocation_ai import handle_chat as _allocation_handle_chat
        result = await _allocation_handle_chat(req)
    except Exception as e:
        yield _sse("text", {"delta": f"⚠️ 资产配置分析失败：{str(e)}"})
        yield _sse("done", {"decision_id": None, "conclusion_level": None, "conclusion_label": None})
        return

    # 更新 sessionContext
    if result.updated_session_context:
        _ALLOC_SESSION_CTX[session_id] = result.updated_session_context

    # 构建回答文本
    r = result.response
    parts = []
    if r.diagnosis:
        parts.append(r.diagnosis)
    if r.logic:
        parts.append(r.logic)
    if r.status_conclusion:
        parts.append(f"**{r.status_conclusion}**")
    if r.deviation_detail:
        parts.append(r.deviation_detail)
    if r.action_direction:
        desc = r.action_direction.get("description", "") if isinstance(r.action_direction, dict) else ""
        if desc:
            parts.append(desc)
    if r.risk_note:
        parts.append(f"\n> {r.risk_note}")

    answer = "\n\n".join(parts) if parts else "已收到你的消息。"

    # 流式输出
    async for chunk_event in _stream_text(answer):
        yield chunk_event

    # 构建 ExplainData 存入缓存（按约定结构）
    explain_data = _build_allocation_explain(decision_id, result)
    _ALLOC_EXPLAIN_STORE[f"{session_id}:{decision_id}"] = explain_data

    yield _sse("done", {
        "decision_id": decision_id,
        "conclusion_level": "asset_allocation",
        "conclusion_label": "资产配置分析",
        "mode": "allocation",
        # 方案表格数据（前端用于渲染表格）
        "allocationPlan": r.plan if r.plan else None,
    })


async def _stream_general_chat(user_input: str) -> AsyncGenerator[str, None]:
    """普通对话路由"""
    yield _sse("stage", {"stage": "reasoning", "label": "回复中..."})

    response = await asyncio.to_thread(llm_engine.chat, user_input, None)
    answer = response or "（暂时无法回复，请重试）"

    async for chunk_event in _stream_text(answer):
        yield chunk_event

    yield _sse("done", {"decision_id": None, "conclusion_level": "general_chat", "conclusion_label": "普通对话"})


# ── 内部：文本流式分块 ────────────────────────────────────────────────────────

async def _stream_text(text: str, chunk_size: int = 15) -> AsyncGenerator[str, None]:
    """将完整文本按字符分块 yield，模拟流式输出"""
    for i in range(0, len(text), chunk_size):
        yield _sse("text", {"delta": text[i:i + chunk_size]})
        await asyncio.sleep(0.008)


# ── 内部：SSE 格式化 ──────────────────────────────────────────────────────────

def _sse(event_type: str, data: dict) -> str:
    """格式化为 SSE 字符串"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ── 内部：意图适配器（从 strategy.py 原样提取）────────────────────────────────

def _payload_to_intent_result(payload, ctx) -> IntentResult:
    _ACTION_MAP = {
        "BUY":         "买入判断",
        "ADD":         "加仓判断",
        "SELL":        "卖出判断",
        "STOP_LOSS":   "卖出判断",
        "TAKE_PROFIT": "减仓判断",
        "REDUCE":      "减仓判断",
        "HOLD":        "持有评估",
        "ANALYZE":     "持有评估",
    }
    asset       = payload.entities.asset or ctx.inherited_fields.asset
    first_action = payload.actions[0] if payload.actions else "ANALYZE"
    action_type = _ACTION_MAP.get(first_action, "持有评估")
    time_horizon = payload.entities.time_horizon or ctx.inherited_fields.time_horizon or "未知"

    return IntentResult(
        asset=asset,
        action_type=action_type,
        time_horizon=time_horizon,
        trigger=None,
        confidence_score=payload.confidence,
        clarification=None,
        intent_type="investment_decision",
        is_context_inherited=bool(not payload.entities.asset and ctx.inherited_fields.asset),
    )


# ── 内部：回答生成（从 strategy.py 原样提取）─────────────────────────────────

def _build_chat_answer(result: DecisionResult, user_input: str) -> str:
    if result.was_aborted:
        return result.aborted_reason or "分析中断，请重新描述您的投资需求。"

    if result.is_complete and result.llm:
        answer = result.llm.chat_answer
        if not answer:
            decision_cn = {
                "BUY":         "加仓",
                "HOLD":        "观望",
                "TAKE_PROFIT": "部分止盈",
                "REDUCE":      "逐步减仓",
                "SELL":        "减仓/清仓",
                "STOP_LOSS":   "止损离场",
            }.get(result.llm.decision, "观望")
            asset = result.intent.asset if result.intent else "该标的"
            reasons = "；".join(result.llm.reasoning[:2]) if result.llm.reasoning else ""
            answer = f"**{asset}** 当前建议**{decision_cn}**。" + (f"\n\n{reasons}。" if reasons else "")

        suffix_parts = []
        if result.llm.is_fallback:
            suffix_parts.append(f"⚠️ *AI 推理遇到问题（{result.llm.error}），结论为降级结果。*")
        if result.llm.decision_corrected:
            suffix_parts.append(
                f"ℹ️ *AI 原始输出「{result.llm.original_decision}」不在标准选项内，"
                f"已自动修正为「{result.llm.decision_cn}」。*"
            )
        suffix = ("\n\n> " + "\n> ".join(suffix_parts)) if suffix_parts else ""
        return answer + suffix + "\n\n---\n*⚖️ 仅供参考，不构成投资建议。投资有风险，入市需谨慎。*"

    return "分析未能完成，请重试。"


def _build_multi_asset_answer(results: list[tuple[str, DecisionResult]], user_input: str) -> str:
    parts = []
    for asset_name, r in results:
        if r.was_aborted:
            parts.append(f"**{asset_name}**：{r.aborted_reason or '分析中断，请补充持仓数据后重试。'}")
        elif r.is_complete and r.llm:
            if r.llm.chat_answer:
                parts.append(f"**{asset_name}** — {r.llm.decision_emoji} {r.llm.decision_cn}\n\n{r.llm.chat_answer}")
            else:
                decision_cn = r.llm.decision_cn
                reasons = "；".join(r.llm.reasoning[:2]) if r.llm.reasoning else ""
                parts.append(
                    f"**{asset_name}** — {r.llm.decision_emoji} **{decision_cn}**。"
                    + (f"\n\n{reasons}。" if reasons else "")
                )
        else:
            parts.append(f"**{asset_name}**：数据加载失败，请稍后重试。")

    combined = "\n\n---\n\n".join(parts)
    return combined + "\n\n---\n*⚖️ 仅供参考，不构成投资建议。投资有风险，入市需谨慎。*"


def _extract_conclusion(result: DecisionResult) -> tuple[Optional[str], Optional[str]]:
    """从 DecisionResult 提取结论档位和标签"""
    if result.was_aborted:
        return "aborted", "分析中断"
    if result.is_complete and result.llm:
        decision = result.llm.decision
        label = result.llm.decision_cn or decision
        return decision, label
    return None, None


# ── 内部：缓存管理 ────────────────────────────────────────────────────────────

def _store_result(session_id: str, result: DecisionResult) -> None:
    if session_id not in _DECISION_STORE:
        _DECISION_STORE[session_id] = {}
    _DECISION_STORE[session_id][result.decision_id] = result


def _serialize_decision_result(result: DecisionResult) -> dict:
    """序列化 DecisionResult 为 JSON-safe dict（供 /explain 端点返回）"""
    d: dict = {
        "decision_id": result.decision_id,
        "stage":       result.stage.value if result.stage else None,
        "was_aborted": result.was_aborted,
        "aborted_reason": result.aborted_reason,
    }

    if result.intent:
        d["intent"] = {
            "asset":         result.intent.asset,
            "action":        result.intent.action_type,    # 前端统一用 action
            "time_context":  result.intent.time_horizon,   # 前端统一用 time_context
            "confidence":    result.intent.confidence_score,
            "intent_type":   result.intent.intent_type,
            "is_inherited":  result.intent.is_context_inherited,
        }

    if result.data:
        ld = result.data
        d["data"] = {
            "asset_name":      ld.target_position.name if ld.target_position else None,
            "has_data_errors": ld.has_data_errors,
            "research":        ld.research,
            "total_assets":    ld.total_assets,
            "target_position": {
                "name":             ld.target_position.name,
                "weight":           ld.target_position.weight,
                "market_value_cny": ld.target_position.market_value_cny,
                "profit_loss_rate": ld.target_position.profit_loss_rate,
                "platforms":        ld.target_position.platforms,
            } if ld.target_position else None,
        }

    if result.pre_check:
        d["pre_check"] = {
            "passed":  result.pre_check.passed,
            "message": result.pre_check.message,
        }

    if result.rules:
        d["rules"] = {
            "passed":         not result.rules.violation,   # RuleResult 无 passed，取反 violation
            "current_weight": result.rules.current_weight,
            "max_position":   result.rules.max_position,
            "violation":      result.rules.violation,
            "warning":        result.rules.warning,
            "rule_details":   result.rules.rule_details,
        }

    if result.signals:
        d["signals"] = {
            "position":    result.signals.position_signal,
            "event":       {
                "uncertainty": result.signals.event_signal.uncertainty,
                "direction":   result.signals.event_signal.direction,
            },
            "fundamental": result.signals.fundamental_signal,
            "sentiment":   result.signals.sentiment_signal,
        }

    if result.llm:
        d["llm"] = {
            "decision":           result.llm.decision,
            "decision_cn":        result.llm.decision_cn,
            "decision_emoji":     result.llm.decision_emoji,
            "reasoning":          result.llm.reasoning,
            "risk":               result.llm.risk,
            "strategy":           result.llm.strategy,
            "chat_answer":        result.llm.chat_answer,
            "is_fallback":        result.llm.is_fallback,
            "decision_corrected": result.llm.decision_corrected,
            "original_decision":  result.llm.original_decision,
            # Phase 1: 结构化 DecisionResult
            "structured_result":  result.llm.structured_result,
        }

    if result.generic_llm:
        d["generic_llm"] = {
            "chat_answer": result.generic_llm.chat_answer,
            "is_fallback": result.generic_llm.is_fallback,
            "error":       result.generic_llm.error,
        }

    return d


# ── AssetAllocation ExplainData 构建 ─────────────────────────────────────────

def _build_allocation_explain(decision_id: str, alloc_result) -> dict:
    """
    将资产配置模块的 AllocationChatResponse 转换为 ExplainData 格式。
    按约定结构填充 intent/data/rules 字段。
    """
    r = alloc_result.response
    intent_type = alloc_result.intent_type
    ep = r.explain_panel

    d: dict = {
        "decision_id": decision_id,
        "stage": "done",
        "was_aborted": False,
        "aborted_reason": None,
    }

    # intent 字段
    d["intent"] = {
        "primary_intent": "AssetAllocation",
        "asset": None,
        "action": intent_type,           # 子意图类型
        "time_context": None,
        "confidence": 1.0,
        "intent_type": "asset_allocation",
        "is_inherited": False,
    }

    # data 字段
    data_section: dict = {}
    if ep and ep.key_data:
        kd = ep.key_data
        data_section["totalAssets"] = kd.get("totalAssets") or kd.get("totalAmount") or kd.get("incrementAmount")
        data_section["overallStatus"] = kd.get("overallStatus")
        data_section["actionHint"] = kd.get("actionHint")
    if r.plan and r.plan.get("table"):
        data_section["allocationPlan"] = r.plan["table"]
    d["data"] = data_section

    # rules 字段（纪律校验）
    if r.plan and r.plan.get("discipline"):
        disc = r.plan["discipline"]
        d["rules"] = {
            "passed": disc.get("passed", True),
            "violations": disc.get("violations", []),
        }
    else:
        d["rules"] = None

    # signals / pre_check 留空
    d["signals"] = None
    d["pre_check"] = None

    # reasoning（必须为数组，与 PositionDecision 的 ExplainData 格式一致）
    reasoning_text = ep.reasoning if ep else ""
    d["llm"] = {
        "reasoning": [reasoning_text] if reasoning_text else [],
    } if ep else None

    return d
