"""
v3.0 决策服务 - PEER 4 Agent 协作链路。

核心入口：run_chat_stream_v3()
由 decision_service.run_chat_stream() 委托调用。
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
    conversation_id: str,
    portfolio_id: int = 1,
    conversation_history: Optional[list[dict]] = None,
) -> AsyncGenerator[str, None]:
    """
    v3.0 决策流：4 Agent 协作。

    Planning → Executing → Expressing(流式) → Reviewing → done
    """
    decision_id = f"decision_{uuid.uuid4().hex[:12]}"

    try:
        # ── Stage 0: 加载对话历史 ──
        if conversation_history is None:
            try:
                from backend.services.decision_service import get_conversation_history
                conversation_history = await asyncio.to_thread(
                    get_conversation_history, conversation_id,
                )
            except Exception as e:
                logger.warning(f"[v3] Stage 0 加载 history 失败: {e}")
                conversation_history = []

        # ── Stage 0.5a: 恢复 intent_engine 上下文（重启/切换会话后）──
        try:
            from backend.services.decision_service import restore_conversation_context
            await asyncio.to_thread(restore_conversation_context, conversation_id)
        except Exception as e:
            logger.warning(f"[v3] Stage 0.5a restore_conversation_context 失败: {e}")

        # ── Stage 0.5b: 加载 all_positions + 多轮澄清解析（v2.6 兼容）──
        all_positions = []
        try:
            from app.utils.position_aggregator import aggregate_investment_positions
            all_positions, _ = await asyncio.to_thread(
                aggregate_investment_positions, portfolio_id,
            )
        except Exception as e:
            logger.warning(f"[v3] Stage 0.5 加载 all_positions 失败: {e}")

        # 多轮澄清解析
        # 注意：澄清解析只对个股消歧有意义（上一轮 clarify 让用户选标的，本轮匹配结果）。
        # 组合级意图（AssetAllocation 等）不需要标的消歧，但此处无法提前知道意图，
        # 所以仍然执行解析。后续 PlanningAgent 的路由守门会确保组合级意图不被误路由。
        clarification_resolved = False
        try:
            from backend.services.decision_service import _try_resolve_clarification

            resolved_input = await asyncio.to_thread(
                _try_resolve_clarification,
                conversation_id, user_input, all_positions,
            )

            if resolved_input:
                logger.info(
                    f"[v3] Stage 0.5: 澄清解析命中 - "
                    f"'{user_input}' → '{resolved_input[:60]}...'"
                )
                user_input = resolved_input
                clarification_resolved = True
        except Exception as e:
            logger.warning(f"[v3] Stage 0.5 澄清解析异常: {e}")

        # ── Stage 1: PlanningAgent ──
        yield _sse("stage", {"stage": "intent", "label": "意图识别中..."})

        planning = get_planning_agent()
        plan_out = await asyncio.to_thread(
            planning.run,
            user_input, conversation_id, portfolio_id, conversation_history,
            all_positions,
        )

        if plan_out.status == AgentTaskStatus.FAILED:
            error_msg = plan_out.error or "意图识别失败"
            yield _sse("error", {
                "code": "planning_failed",
                "message": error_msg,
            })
            # 保存对话历史（即使失败也记录）
            try:
                from backend.services.decision_service import save_conversation_turn
                await asyncio.to_thread(
                    save_conversation_turn, conversation_id, user_input, f"[系统错误] {error_msg}",
                    "PlanningFailed", None,
                )
            except Exception as e:
                logger.warning(f"[v3] planning_failed save_conversation_turn 失败: {e}")
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
            # 用 IntentRecognizer 的 clarify_question（对齐 v2 行为）
            clarify_text = (
                plan_out.clarify_question
                or "您能描述一下您想分析哪个标的，以及想做什么操作（买入、卖出、还是持有判断）吗？"
            )
            yield _sse("text", {"delta": clarify_text})
            # 保存对话历史
            try:
                from backend.services.decision_service import save_conversation_turn
                await asyncio.to_thread(
                    save_conversation_turn, conversation_id, user_input, clarify_text,
                    "LowConfidence", None,
                )
            except Exception as e:
                logger.warning(f"[v3] low_confidence save_conversation_turn 失败: {e}")
            yield _sse("done", {
                "decision_id": None,
                "conclusion_level": None,
                "conclusion_label": None,
            })
            return

        if plan_out.route == "clarify":
            async for event in _handle_clarify(
                plan_out, decision_id, user_input, conversation_id, portfolio_id,
            ):
                yield event
            return

        # ── position_multi 路由：循环分发（对齐 v2 _stream_multi_asset）──
        if plan_out.route == "position_multi" and plan_out.multi_assets:
            async for event in _handle_position_multi(
                plan_out, plan_out.multi_assets, user_input,
                conversation_id, portfolio_id, decision_id,
            ):
                yield event
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
            # R2 修复：ABORT 也保存对话历史
            try:
                from backend.services.decision_service import save_conversation_turn
                abort_text = exec_out.abort_chat_answer or exec_out.abort_reason or ""
                intent_dict = plan_out.intent if isinstance(plan_out.intent, dict) else {}
                await asyncio.to_thread(
                    save_conversation_turn, conversation_id, user_input, abort_text,
                    intent_dict.get("primary_intent", "Unknown"),
                    intent_dict.get("asset"),
                )
            except Exception as e:
                logger.warning(f"[v3] ABORT save_conversation_turn 失败: {e}")
            yield _sse("done", {
                "decision_id": decision_id,
                "conclusion_level": "aborted",
                "conclusion_label": "分析中断",
            })
            return

        # D3 修复：补发 rules/signals stage 事件（对齐 v2 视觉粒度）
        if getattr(exec_out, "rule_result", None):
            yield _sse("stage", {"stage": "rules", "label": "规则校验完成"})
        if getattr(exec_out, "signal_result", None):
            yield _sse("stage", {"stage": "signals", "label": "信号分析完成"})

        # R-M7 修复：用 target_position.name 回写 intent.asset（对齐 v2 _payload_to_intent_result）
        # 场景: 基金代码 "004928" → 产品全名 "易方达稳鑫30天滚动持有短债A"
        try:
            if (exec_out.loaded_data
                    and exec_out.loaded_data.target_position
                    and isinstance(plan_out.intent, dict)):
                target_name = getattr(exec_out.loaded_data.target_position, "name", None)
                if target_name:
                    plan_out.intent["asset"] = target_name
        except Exception as e:
            logger.warning(f"[v3] R-M7 asset 回写失败: {e}")

        # Execution SKIPPED (general/clarify) → 直接到 Expressing
        # ── Stage 3: ExpressingAgent（流式）──
        yield _sse("stage", {"stage": "reasoning", "label": "AI 推理中..."})

        expressing = get_expressing_agent()
        # 澄清解析成功后清空 history，让 LLM 用首轮结构化格式输出（对齐 v2.6 L457）
        effective_history = [] if clarification_resolved else conversation_history
        async for chunk in expressing.run_streaming(
            plan_out, exec_out, user_input, effective_history,
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

        # ── Stage 5: 写入 store + 保存对话历史 + done 事件 ──
        await _write_stores_v3(
            conversation_id, decision_id, plan_out, exec_out, expr_out,
            user_input=user_input,
        )
        done_payload = _build_done_payload(plan_out, expr_out, review_out, decision_id)
        yield _sse("done", done_payload)

    except Exception as e:
        logger.exception(f"[v3] run_chat_stream_v3 异常: {e}")
        yield _sse("error", {"code": "internal_error", "message": str(e)})


async def _handle_clarify(
    plan_out,
    decision_id: str,
    user_input: str,
    conversation_id: str,
    portfolio_id: int,
) -> AsyncGenerator[str, None]:
    """
    Clarify 路由：完整移植 v2.6 的候选清单交互（M1.1）。

    流程：
    1. 加载持仓数据（aggregate_investment_positions）
    2. 调 _get_candidate_positions 筛 top 3 候选
    3. yield candidates SSE 事件
    4. 调 _build_clarification_reply 生成澄清文本
    5. 写入 _CLARIFICATION_CTX（让用户下一轮点选/输入能关联回原始问题）
    6. save_conversation_turn 持久化
    7. yield text + done 事件
    """
    from app.utils.position_aggregator import aggregate_investment_positions
    from backend.services.decision_service import (
        _get_candidate_positions,
        _build_candidates_payload,
        _build_clarification_reply,
        _CLARIFICATION_CTX,
        save_conversation_turn,
    )

    # 1. 加载所有持仓
    try:
        all_positions, _ = await asyncio.to_thread(
            aggregate_investment_positions, portfolio_id,
        )
    except Exception as e:
        logger.warning(f"[v3] clarify 加载持仓失败: {e}")
        all_positions = []

    # 2. 筛候选
    candidates, feature_type = _get_candidate_positions(user_input, all_positions)

    # 3. yield candidates SSE 事件
    if candidates:
        cand_payload = _build_candidates_payload(candidates, feature_type)
        yield _sse("candidates", {"items": cand_payload})

    # 4. 生成澄清回复文本
    reply = _build_clarification_reply(user_input, candidates, feature_type)

    # 5. 写入澄清上下文（关键！多轮澄清依赖这个）
    _CLARIFICATION_CTX[conversation_id] = {
        "original_question": user_input,
        "candidates": [p.name for p in candidates],
        "pending_clarification": True,
    }

    # 6. 持久化对话（best effort）
    try:
        await asyncio.to_thread(
            save_conversation_turn,
            conversation_id, user_input, reply, "PositionDecision", None,
        )
    except Exception as e:
        logger.debug(f"[v3] save_conversation_turn 失败（可忽略）: {e}")

    # 7. yield text + done
    yield _sse("text", {"delta": reply})
    yield _sse("done", {
        "decision_id": None,
        "conclusion_level": None,
        "conclusion_label": None,
    })


async def _handle_position_multi(
    plan_out,
    multi_assets: list[str],
    user_input: str,
    conversation_id: str,
    portfolio_id: int,
    decision_id: str,
) -> AsyncGenerator[str, None]:
    """
    Position multi 路由：循环分析多个标的，拼接结果。
    对齐 v2.6 _stream_multi_asset 行为。
    """
    from copy import copy
    from decision_engine.decision_flow import DecisionResult, FlowStage
    from decision_engine.types import IntentResult
    from backend.services.decision_service import (
        _build_multi_asset_answer, _DECISION_STORE, save_conversation_turn,
    )
    from backend.agents.expressing_agent import _emit_text_chunks

    yield _sse("stage", {"stage": "loading", "label": f"分析 {len(multi_assets)} 个标的..."})

    executing = get_executing_agent()
    expressing = get_expressing_agent()

    results: list[tuple[str, DecisionResult | None]] = []
    intent_base = plan_out.intent if isinstance(plan_out.intent, dict) else {}

    for asset_name in multi_assets:
        yield _sse("stage", {"stage": "reasoning", "label": f"分析 {asset_name} 中..."})

        # 构造单标 PlanningOutput（强制 PositionDecision 让 ExpressingAgent 走 reason 路径）
        single_plan = copy(plan_out)
        single_plan.route = "position_single"
        single_plan.intent = dict(
            intent_base, asset=asset_name, multi_assets=[],
            primary_intent="PositionDecision",
        )

        try:
            exec_out = await asyncio.to_thread(executing.run, single_plan, user_input)

            if exec_out.aborted:
                # 单标 ABORT：构造 aborted DecisionResult
                dr = DecisionResult(
                    decision_id=f"{decision_id}_{asset_name}",
                    stage=FlowStage.ABORTED,
                    intent=IntentResult(
                        asset=asset_name,
                        action_type=intent_base.get("action_type", "持有评估"),
                        time_horizon="未知", trigger=None, confidence_score=0.9,
                    ),
                )
                dr.aborted_reason = exec_out.abort_chat_answer or exec_out.abort_reason or "分析中断"
                results.append((asset_name, dr))
                continue

            # 调 ExpressingAgent（消费全部 chunk，只取 last_output）
            async for _ in expressing.run_streaming(
                single_plan, exec_out, user_input, [],
            ):
                pass
            expr_out = expressing.last_output

            llm_result = getattr(expr_out, "llm_result", None) if expr_out else None

            dr = DecisionResult(
                decision_id=f"{decision_id}_{asset_name}",
                stage=FlowStage.DONE,
                intent=IntentResult(
                    asset=asset_name,
                    action_type=intent_base.get("action_type", "持有评估"),
                    time_horizon="未知", trigger=None, confidence_score=0.9,
                ),
                data=getattr(exec_out, "loaded_data", None),
                pre_check=getattr(exec_out, "pre_check_result", None),
                rules=getattr(exec_out, "rule_result", None),
                signals=getattr(exec_out, "signal_result", None),
                llm=llm_result,
            )
            results.append((asset_name, dr))
            _DECISION_STORE.setdefault(conversation_id, {})[dr.decision_id] = dr

        except Exception as e:
            logger.warning(f"[v3] position_multi 分析 {asset_name} 失败: {e}")
            results.append((asset_name, None))

    # P2 修复：调综合 LLM 生成横向对比报告（替代机械拼接）
    valid_results = [(a, r) for a, r in results if r is not None]
    if valid_results:
        try:
            from decision_engine import llm_engine as _llm

            # 提取全局纪律（从第一个结果的 data.rules，单一数据源，不硬编码 default）
            global_rules: dict = {}
            first_data = valid_results[0][1].data if valid_results[0][1] else None
            if first_data:
                data_rules = getattr(first_data, "rules", None)
                if data_rules:
                    max_pos = getattr(data_rules, "max_single_position", None)
                    max_eq = getattr(data_rules, "max_equity_pct", None)
                    min_cash = getattr(data_rules, "min_cash_pct", None)
                    if max_pos is not None:
                        global_rules["max_single_position_pct"] = round(max_pos * 100, 1)
                    if max_eq is not None:
                        global_rules["max_equity_pct"] = round(max_eq * 100, 1)
                    if min_cash is not None:
                        global_rules["min_cash_pct"] = round(min_cash * 100, 1)
                else:
                    logger.warning("[v3] data.rules 不存在, global_rules 将为空")

            summaries = []
            for asset_name, r in valid_results:
                s: dict = {"name": asset_name}
                if r.data and r.data.target_position:
                    tp = r.data.target_position
                    s["weight_pct"] = round(getattr(tp, "weight", 0) * 100, 2)
                    s["pnl_rate_pct"] = round(getattr(tp, "profit_loss_rate", 0) * 100, 2)
                    s["market_value_cny"] = getattr(tp, "market_value_cny", 0)
                    s["asset_class"] = getattr(tp, "asset_class", "")
                # 信号
                if r.signals:
                    s["signals"] = {
                        "fundamental": getattr(r.signals, "fundamental_signal", None),
                        "position": getattr(r.signals, "position_signal", None),
                        "sentiment": getattr(r.signals, "sentiment_signal", None),
                    }
                # 投研数据（含 AV 数据）
                if r.data and getattr(r.data, "research", None):
                    s["research"] = [str(item)[:200] for item in list(r.data.research)[:3]]
                # LLM 分析摘要
                if r.llm:
                    s["decision"] = getattr(r.llm, "decision_cn", "") or getattr(r.llm, "decision", "")
                    s["reasoning"] = list(getattr(r.llm, "reasoning", []) or [])[:5]
                    s["risk"] = list(getattr(r.llm, "risk", []) or [])[:3]
                    if r.llm.structured_result:
                        s["confidence"] = r.llm.structured_result.get("confidence")
                summaries.append(s)

            yield _sse("stage", {"stage": "reasoning", "label": "生成横向对比报告..."})
            answer = await asyncio.to_thread(
                _llm.compare_multi_assets, user_input, summaries,
                global_rules=global_rules,
            )
            logger.info(f"[v3] P2 综合对比报告生成成功, 长度={len(answer)}")
        except Exception as e:
            logger.warning(f"[v3] compare_multi_assets 失败, 降级到拼接: {e}")
            try:
                answer = _build_multi_asset_answer(valid_results, user_input)
            except Exception:
                answer = "\n\n---\n\n".join(
                    f"**{a}**\n\n{getattr(r.llm, 'chat_answer', '') if r and r.llm else '分析完成'}"
                    for a, r in valid_results
                )
    else:
        answer = "未能成功分析任何标的，建议您逐个标的单独询问。"

    # 流式发送
    async for chunk in _emit_text_chunks(answer):
        yield _sse("text", {"delta": chunk})

    # 保存对话历史
    try:
        await asyncio.to_thread(
            save_conversation_turn,
            conversation_id, user_input, answer,
            "PositionDecision", ", ".join(multi_assets),
        )
    except Exception as e:
        logger.warning(f"[v3] position_multi save_conversation_turn 失败: {e}")

    # done 事件
    last_did = valid_results[-1][1].decision_id if valid_results else None
    yield _sse("done", {
        "decision_id": last_did,
        "conclusion_level": "multi_asset",
        "conclusion_label": f"已分析 {len(valid_results)} 个标的",
    })


async def _write_stores_v3(
    conversation_id: str,
    decision_id: str,
    plan_out,
    exec_out,
    expr_out,
    user_input: str = "",
) -> None:
    """
    v3 决策完成后写入 _DECISION_STORE / _ALLOC_EXPLAIN_STORE / _PRIMARY_INTENT_CACHE
    + save_conversation_turn（R2 修复）。
    """
    try:
        from decision_engine.decision_flow import DecisionResult, FlowStage
        from decision_engine.types import IntentResult
        from backend.services.decision_service import (
            _DECISION_STORE, _PRIMARY_INTENT_CACHE, _ALLOC_EXPLAIN_STORE,
            _calc_asset_breakdown, save_conversation_turn,
        )

        intent_dict = plan_out.intent if isinstance(plan_out.intent, dict) else {}
        primary_intent = intent_dict.get("primary_intent", "")

        # _PRIMARY_INTENT_CACHE（D2 顺手修）
        _PRIMARY_INTENT_CACHE[conversation_id] = primary_intent

        route = plan_out.route

        # ── PositionDecision → _DECISION_STORE ──
        if route in ("position_single", "position_multi"):
            intent_obj = IntentResult(
                asset=intent_dict.get("asset", ""),
                action_type=intent_dict.get("action_type") or "持有评估",
                time_horizon="未知",
                trigger=None,
                confidence_score=intent_dict.get("confidence", 0.9),
            )
            dr = DecisionResult(
                decision_id=decision_id,
                stage=FlowStage.DONE,
                intent=intent_obj,
                data=getattr(exec_out, "loaded_data", None),
                pre_check=getattr(exec_out, "pre_check_result", None),
                rules=getattr(exec_out, "rule_result", None),
                signals=getattr(exec_out, "signal_result", None),
                llm=getattr(expr_out, "llm_result", None) if expr_out else None,
            )
            # 附加 market_data 供 explain 序列化时反算持仓股数
            dr._market_data = getattr(exec_out, "market_data", None)
            _DECISION_STORE.setdefault(conversation_id, {})[decision_id] = dr

        # ── Portfolio 类 → _ALLOC_EXPLAIN_STORE ──
        elif route == "portfolio" and expr_out:
            loaded = getattr(exec_out, "loaded_data", None)
            generic_llm = getattr(expr_out, "llm_result", None)
            portfolio_result = expr_out.structured_payload or {}

            asset_breakdown = None
            if loaded and loaded.positions:
                try:
                    asset_breakdown = _calc_asset_breakdown(loaded.positions)
                except Exception:
                    pass

            _intent_config = {
                "PortfolioReview": "portfolio_review",
                "AssetAllocation": "asset_allocation",
                "PerformanceAnalysis": "performance_analysis",
            }
            intent_type_key = _intent_config.get(primary_intent, "portfolio_review")

            explain_data = {
                "decision_id": decision_id,
                "stage": "done",
                "was_aborted": False,
                "aborted_reason": None,
                "intent": {
                    "primary_intent": primary_intent,
                    "asset": None,
                    "action": portfolio_result.get("conclusion_type") or intent_type_key,
                    "time_context": None,
                    "confidence": intent_dict.get("confidence", 0.9),
                    "intent_type": intent_type_key,
                    "is_inherited": False,
                },
                "data": {
                    "total_assets": loaded.total_assets if loaded else None,
                    "asset_breakdown": asset_breakdown,
                    "position_count": len(loaded.positions) if loaded else 0,
                    "research": list(loaded.research) if loaded and loaded.research else [],
                },
                "rules": None,
                "signals": None,
                "pre_check": None,
                "llm": {"reasoning": portfolio_result.get("key_findings", [])}
                       if generic_llm and not getattr(generic_llm, "is_fallback", False)
                       else None,
                "generic_llm": {
                    "chat_answer": getattr(generic_llm, "chat_answer", "") if generic_llm else "",
                    "is_fallback": getattr(generic_llm, "is_fallback", False) if generic_llm else False,
                    "error": getattr(generic_llm, "error", None) if generic_llm else None,
                },
                "portfolioResult": portfolio_result,
            }
            _ALLOC_EXPLAIN_STORE[f"{conversation_id}:{decision_id}"] = explain_data

        # ── R2 修复：保存对话历史 ──
        chat_answer = getattr(expr_out, "chat_answer", "") if expr_out else ""
        if not chat_answer:
            chat_answer = "(无回复内容)"
        asset = intent_dict.get("asset") if intent_dict else None
        try:
            await asyncio.to_thread(
                save_conversation_turn,
                conversation_id, user_input, chat_answer,
                primary_intent, asset,
            )
        except Exception as e:
            logger.warning(f"[v3] save_conversation_turn 失败: {e}")

    except Exception as e:
        logger.warning(f"[v3] store 写入失败（不阻断主流程）: {e}")


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

    # v3.2 actionable 字段（所有路由共用）
    actionable_fields = {
        "actionable": getattr(expr_out, "actionable", False),
        "actionable_hint": getattr(expr_out, "actionable_hint", None),
    }

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
                **actionable_fields,
            }
        return {
            "decision_id": decision_id,
            "conclusion_level": "HOLD",
            "conclusion_label": "观望",
            "mode": "fallback",
            "decisionResult": None,
            "rawText": expr_out.raw_text,
            "validator": validator_payload,
            **actionable_fields,
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
            **actionable_fields,
        }
        return payload

    # General chat
    return {
        "decision_id": None,
        "conclusion_level": "general_chat",
        "conclusion_label": "普通对话",
        **actionable_fields,
    }
