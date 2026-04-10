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
import re
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


# ── 多轮对话历史（持久化） ────────────────────────────────────────────────────

def get_conversation_history(session_id: str, limit: int = 6) -> list[dict]:
    """读取该 session 最近 limit 条记录，按 created_at 升序返回。"""
    from app.database import get_session as get_db_session
    from app.models import ConversationMessage

    db = get_db_session()
    try:
        rows = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.session_id == session_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
            .all()
        )
        rows.reverse()  # 升序
        return [
            {
                "role": r.role,
                "content": r.content,
                "intent": r.intent,
                "asset": r.asset,
            }
            for r in rows
        ]
    finally:
        db.close()


def save_conversation_turn(
    session_id: str,
    user_input: str,
    chat_answer: str,
    intent: str | None = None,
    asset: str | None = None,
) -> None:
    """写入本轮的 user 消息和 assistant 消息，共两条记录。"""
    from app.database import get_session as get_db_session
    from app.models import ConversationMessage

    db = get_db_session()
    try:
        db.add(ConversationMessage(
            session_id=session_id, role="user", content=user_input,
        ))
        db.add(ConversationMessage(
            session_id=session_id, role="assistant", content=chat_answer,
            intent=intent, asset=asset,
        ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── 标的明确性校验与智能澄清 ────────────────────────────────────────────────

VAGUE_ASSET_WORDS = [
    "股票", "基金", "标的", "持仓", "资产", "仓位",
    "这只", "那只", "某只", "一只", "一个", "这个", "那个",
]

# 进程内澄清上下文缓存 {session_id: {...}}
_CLARIFICATION_CTX: dict[str, dict] = {}


def _is_asset_clear(asset: str | None, positions: list) -> bool:
    """判断意图识别出的标的是否明确可匹配到持仓。"""
    if not asset:
        return False
    # 模糊标的词：仅当 asset 本身就是这些泛指词时才判定为不明确
    # （如"股票""基金""这只"），而不是 asset 名称中包含这些字（如"景顺...股票A"）
    stripped = asset.strip()
    if len(stripped) <= 3 and any(vague == stripped for vague in VAGUE_ASSET_WORDS):
        return False
    if stripped in VAGUE_ASSET_WORDS:
        return False
    # 模糊匹配持仓名称
    asset_lower = stripped.lower()
    for p in positions:
        if asset_lower in p.name.lower() or p.name.lower() in asset_lower:
            return True
        if p.ticker and (asset_lower == p.ticker.lower() or asset_lower in p.ticker.lower()):
            return True
    return False


def _detect_feature_type(user_input: str) -> str:
    """根据用户输入的关键词判断描述特征类型。"""
    gain_kw = ["涨", "盈利", "赚", "落袋", "止盈", "涨了", "正收益"]
    loss_kw = ["亏", "跌", "亏损", "止损", "割肉", "持续亏", "浮亏"]
    heavy_kw = ["重", "加仓", "不轻", "占比高", "仓位大", "看好", "偏重"]
    if any(k in user_input for k in gain_kw):
        return "gain"
    if any(k in user_input for k in loss_kw):
        return "loss"
    if any(k in user_input for k in heavy_kw):
        return "heavy"
    return "default"


def _get_candidate_positions(user_input: str, positions: list) -> tuple[list, str]:
    """根据用户描述特征从持仓里筛选候选标的，最多3条。返回 (candidates, feature_type)。"""
    ft = _detect_feature_type(user_input)
    if ft == "gain":
        cands = sorted([p for p in positions if p.pl_rate > 0], key=lambda x: x.pl_rate, reverse=True)[:3]
    elif ft == "loss":
        cands = sorted([p for p in positions if p.pl_rate < 0], key=lambda x: x.pl_rate)[:3]
    elif ft == "heavy":
        cands = sorted(positions, key=lambda x: x.weight, reverse=True)[:3]
    else:
        cands = sorted(positions, key=lambda x: x.weight, reverse=True)[:3]
    return cands, ft


def _build_clarification_reply(user_input: str, candidates: list, feature_type: str) -> str:
    """生成澄清回复文本。"""
    if feature_type == "gain":
        intro = "您持仓中目前涨幅较大的有："
        suffix = "请问您说的是哪一只？或者直接告诉我标的名称也可以。"
        items = [f"• {p.name}（+{p.pl_rate:.1f}%）" for p in candidates]
    elif feature_type == "loss":
        intro = "您持仓中目前处于浮亏的有："
        suffix = "请问您说的是哪一只？"
        items = [f"• {p.name}（{p.pl_rate:.1f}%）" for p in candidates]
    elif feature_type == "heavy":
        intro = "您持仓中仓位较重的标的有："
        suffix = "请问您想分析的是哪一只？或者直接告诉我标的名称也可以。"
        items = [f"• {p.name}（占比 {p.weight * 100:.1f}%）" for p in candidates]
    else:
        intro = "请问您指的是哪个标的？您当前持仓中包括："
        suffix = "直接告诉我标的名称，我来帮您分析。"
        items = [f"• {p.name}（占比 {p.weight * 100:.1f}%）" for p in candidates]
    return f"{intro}\n" + "\n".join(items) + f"\n\n{suffix}"


def _try_resolve_clarification(session_id: str, user_input: str, positions: list) -> str | None:
    """
    尝试从澄清上下文中解析用户的回复。
    如果用户输入能匹配到候选标的之一，返回合并后的问题；否则返回 None。
    """
    ctx = _CLARIFICATION_CTX.get(session_id)
    if not ctx or not ctx.get("pending_clarification"):
        return None

    input_lower = user_input.lower().strip()
    candidates = ctx.get("candidates", [])

    # 尝试匹配候选标的
    matched_asset = None
    for name in candidates:
        if name.lower() in input_lower or input_lower in name.lower():
            matched_asset = name
            break
    # 也尝试匹配持仓列表（用户可能给了一个不在候选中但在持仓中的名称）
    if not matched_asset:
        for p in positions:
            if p.name.lower() in input_lower or input_lower in p.name.lower():
                matched_asset = p.name
                break
            if p.ticker and (input_lower == p.ticker.lower()):
                matched_asset = p.name
                break

    if matched_asset:
        original = ctx.get("original_question", user_input)
        # 清除澄清状态
        _CLARIFICATION_CTX.pop(session_id, None)
        return f"{original}（标的：{matched_asset}）"

    return None


def _extract_capital_amount(text: str) -> float | None:
    """从自然语言中提取资金金额，返回标准化的元值。"""
    if not text:
        return None
    m = re.search(r'(\d+(?:\.\d+)?)\s*[万wW]', text)
    if m:
        return float(m.group(1)) * 10000
    m = re.search(r'(\d+(?:\.\d+)?)\s*千', text)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r'\b(\d{4,})\b', text)
    if m:
        return float(m.group(1))
    return None


def _calc_asset_breakdown(positions: list) -> dict:
    """计算五大类资产占比和盈亏汇总。兼容 PositionInfo 和 AggregatedPosition。"""
    cats: dict[str, dict] = {}
    total_mv = sum(p.market_value_cny for p in positions) or 1.0
    for p in positions:
        ac = getattr(p, 'asset_class', '其他') or "其他"
        if ac not in cats:
            cats[ac] = {"market_value": 0.0, "pnl": 0.0, "count": 0}
        cats[ac]["market_value"] += p.market_value_cny
        # 兼容：PositionInfo 用 profit_loss_rate * market_value 估算盈亏金额
        pnl = getattr(p, 'profit_loss_value', None)
        if pnl is None:
            rate = getattr(p, 'profit_loss_rate', 0) or 0
            pnl = p.market_value_cny * rate / (1 + rate) if rate != -1 else 0
        cats[ac]["pnl"] += pnl
        cats[ac]["count"] += 1
    for c in cats.values():
        c["pct"] = round(c["market_value"] / total_mv * 100, 1)
        c["pnl"] = round(c["pnl"], 0)

    top3 = sorted(positions, key=lambda x: x.weight, reverse=True)[:3]
    return {
        "categories": cats,
        "total": round(total_mv, 0),
        "top3_by_weight": [
            {
                "name": p.name,
                "weight": round(p.weight * 100, 1),
                "pnl_pct": round((getattr(p, 'pl_rate', None) or getattr(p, 'profit_loss_rate', 0) or 0) * (100 if abs(getattr(p, 'profit_loss_rate', 0) or 0) < 1 else 1), 1),
            }
            for p in top3
        ],
    }


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
        # ── Stage 0: 读取对话历史 + 持仓数据 ────────────────────────────────
        history = await asyncio.to_thread(get_conversation_history, session_id, 6)
        position_names = await asyncio.to_thread(data_loader.get_position_names, portfolio_id)

        # 加载持仓列表（用于澄清流程）
        from app.utils.position_aggregator import aggregate_investment_positions
        all_positions, _ = await asyncio.to_thread(aggregate_investment_positions, portfolio_id)

        # ── Stage 0.5: 检查是否在回复澄清问题 ──────────────────────────────
        combined = await asyncio.to_thread(
            _try_resolve_clarification, session_id, message, all_positions
        )
        clarification_resolved = False
        if combined:
            # 用户回复了标的名称，用合并后的问题替换原始消息
            print(f"[decision_service] 澄清继承: '{message}' → '{combined}'", flush=True)
            message = combined
            clarification_resolved = True  # 跳过后续标的明确性校验

        # ── Stage 1: 意图识别 ────────────────────────────────────────────────
        yield _sse("stage", {"stage": "intent", "label": "意图识别中..."})

        try:
            payload, clarification = await asyncio.to_thread(
                intent_recognizer.recognize, message, history or None, position_names or None
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

            # ── 标的明确性校验（单标的场景，澄清已解决时跳过）─────────────
            if not clarification_resolved:
                _asset_clear = _is_asset_clear(payload.entities.asset, all_positions)
            else:
                _asset_clear = True  # 澄清流程已确认标的，直接通过
            if len(multi) < 2 and not _asset_clear:
                # 标的不明确，进入澄清流程
                candidates, feature_type = _get_candidate_positions(message, all_positions)
                if candidates:
                    reply = _build_clarification_reply(message, candidates, feature_type)
                    # 保存澄清上下文
                    _CLARIFICATION_CTX[session_id] = {
                        "original_question": message,
                        "candidates": [p.name for p in candidates],
                        "pending_clarification": True,
                    }
                    # 保存到对话历史
                    try:
                        await asyncio.to_thread(
                            save_conversation_turn, session_id, message, reply,
                            "PositionDecision", None,
                        )
                    except Exception:
                        pass
                    yield _sse("text", {"delta": reply})
                    yield _sse("done", {"decision_id": None, "conclusion_level": None, "conclusion_label": None})
                    return

            if len(multi) >= 2:
                # 多标的：依次处理，合并回答
                async for event in _stream_multi_asset(
                    payload, ctx, message, multi, session_id, portfolio_id
                ):
                    yield event
            else:
                # 单标的：完整 6 步管道
                intent_result = _payload_to_intent_result(payload, ctx)
                # 澄清确认后视为新的第一轮，传空历史以获得完整标题格式
                effective_history = [] if clarification_resolved else history
                async for event in _stream_position_decision(
                    intent_result, message, session_id, portfolio_id, effective_history
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
            # GeneralChat / Education — 但如果含操作动词+模糊标的，走澄清
            # 注意：Education 意图高置信度时跳过此检查（行为偏差/方法论类问题不走澄清）
            _OP_KEYWORDS = ["加仓", "减仓", "买入", "卖出", "止损", "止盈", "落袋", "清仓", "建仓"]
            is_education = payload.primary_intent == "Education" and payload.confidence >= 0.8
            has_op = any(k in message for k in _OP_KEYWORDS)
            has_vague = any(v in message for v in VAGUE_ASSET_WORDS)
            if not is_education and has_op and has_vague and all_positions:
                candidates, feature_type = _get_candidate_positions(message, all_positions)
                if candidates:
                    reply = _build_clarification_reply(message, candidates, feature_type)
                    _CLARIFICATION_CTX[session_id] = {
                        "original_question": message,
                        "candidates": [p.name for p in candidates],
                        "pending_clarification": True,
                    }
                    try:
                        await asyncio.to_thread(
                            save_conversation_turn, session_id, message, reply, "PositionDecision", None,
                        )
                    except Exception:
                        pass
                    yield _sse("text", {"delta": reply})
                    yield _sse("done", {"decision_id": None, "conclusion_level": None, "conclusion_label": None})
                    return
            async for event in _stream_general_chat(message, session_id):
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
    conversation_history: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """单标的完整 6 步决策管道，逐阶段 yield SSE 事件"""

    yield _sse("stage", {"stage": "loading", "label": "加载持仓数据..."})

    result: DecisionResult = await asyncio.to_thread(
        decision_flow.run_with_intent,
        intent_result,
        user_input,
        portfolio_id,
        conversation_history,
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

    # 保存对话历史（持久化）
    chat_answer_text = result.llm.chat_answer if result.llm else (result.aborted_reason or "")
    intent_str = intent_result.intent_type if hasattr(intent_result, 'intent_type') else "PositionDecision"
    asset_str = intent_result.asset
    try:
        await asyncio.to_thread(
            save_conversation_turn, session_id, user_input, chat_answer_text or answer,
            intent_str, asset_str,
        )
    except Exception as e:
        print(f"[decision_service] 保存对话历史失败: {e}", flush=True)

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

    # ── 所有组合意图统一走 LLM prompt 路径 ──────────────────────────
    _INTENT_CONFIG = {
        "PortfolioReview":     ("portfolio_review",    "组合全面评估", llm_engine.review_portfolio),
        "AssetAllocation":     ("asset_allocation",    "资产配置分析", llm_engine.analyze_allocation),
        "PerformanceAnalysis": ("performance_analysis","收益表现分析", llm_engine.analyze_performance),
    }
    intent_type_key, action_label, llm_fn = _INTENT_CONFIG[payload.primary_intent]

    yield _sse("stage", {"stage": "loading",   "label": "加载组合数据..."})
    yield _sse("stage", {"stage": "reasoning", "label": f"{action_label}中..."})

    decision_id = f"decision_{uuid.uuid4().hex[:8]}"

    # AssetAllocation: 提取资金金额（优先从 intent entities，兜底从原始消息）
    capital_amount = None
    if payload.primary_intent == "AssetAllocation":
        capital_amount = _extract_capital_amount(payload.entities.capital or "")
        if not capital_amount:
            capital_amount = _extract_capital_amount(user_input)
        if capital_amount:
            print(f"[decision_service] 提取到资金金额: ¥{capital_amount:,.0f}", flush=True)
        else:
            print(f"[decision_service] 未检测到资金金额（用户未明确）", flush=True)

    def _run():
        loaded = data_loader.load(asset_name=None, pid=portfolio_id)
        if loaded.has_data_errors:
            return None, None
        # PortfolioReview：替换为宏观联网搜索结果
        if payload.primary_intent == "PortfolioReview":
            macro_research = data_loader.search_portfolio_research(loaded.positions)
            if macro_research:
                loaded.research = macro_research
        # PerformanceAnalysis：不需要投研观点
        elif payload.primary_intent == "PerformanceAnalysis":
            loaded.research = []
        # AssetAllocation: 传入资金金额和 portfolio_id
        if payload.primary_intent == "AssetAllocation":
            generic_llm = llm_engine.analyze_allocation(
                user_input, loaded,
                capital_amount=capital_amount,
                portfolio_id=portfolio_id,
            )
        else:
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

    # 计算资产分布（用于右侧面板）
    asset_breakdown = _calc_asset_breakdown(loaded.positions) if loaded else None

    # 提取结构化结果（除 chat_answer 外）
    portfolio_result = None
    if generic_llm.raw_payload:
        portfolio_result = {
            k: v for k, v in generic_llm.raw_payload.items() if k != "chat_answer"
        }

    # 结论标签（从 LLM 结构化结果或降级推断）
    conclusion_type = (
        (portfolio_result or {}).get("conclusion_type")
        or (portfolio_result or {}).get("allocation_type")
        or intent_type_key
    )

    # 构建意图特定的推理步骤和结构化结果
    perf_data = loaded.research  # 默认
    if payload.primary_intent == "PerformanceAnalysis" and loaded:
        from decision_engine.llm_engine import _build_performance_data
        perf = _build_performance_data(loaded)
        profit_names = [p["name"] for p in perf.get("profit_top3", [])]
        loss_names = [p["name"] for p in perf.get("loss_top3", [])]
        diag_label = {
            "concentration": "集中度过高", "asset_mix": "资产配比问题",
            "stock_selection": "个股分化明显", "healthy": "收益结构健康",
            "low_defense": "防御资产不足",
        }.get((portfolio_result or {}).get("diagnosis_type", ""), "综合分析")
        llm_reasoning = [
            f"计算各标的盈亏绝对金额，共{len(loaded.positions)}个持仓",
            f"盈利Top3：{', '.join(profit_names)}" if profit_names else "无盈利标的",
            f"亏损Top3：{', '.join(loss_names)}" if loss_names else "无亏损标的",
            f"识别结构性问题：{diag_label}",
        ]
    else:
        llm_reasoning = (portfolio_result or {}).get("key_findings",
                         [f"基于组合全部持仓数据进行{action_label}"])

    # 存储 explain 数据供右侧面板查询
    explain_data = {
        "decision_id": decision_id,
        "stage": "done",
        "was_aborted": False,
        "aborted_reason": None,
        "intent": {
            "primary_intent": payload.primary_intent,
            "asset": None,
            "action": conclusion_type,
            "time_context": None,
            "confidence": payload.confidence,
            "intent_type": intent_type_key,
            "is_inherited": False,
        },
        "data": {
            "total_assets": loaded.total_assets if loaded else None,
            "asset_breakdown": asset_breakdown,
            "position_count": len(loaded.positions) if loaded else 0,
            "research": [r for r in (loaded.research if loaded else [])],
        },
        "rules": None,
        "signals": None,
        "pre_check": None,
        "llm": {"reasoning": llm_reasoning} if not generic_llm.is_fallback else None,
        "generic_llm": {
            "chat_answer": generic_llm.chat_answer,
            "is_fallback": generic_llm.is_fallback,
            "error": generic_llm.error,
        },
        "portfolioResult": portfolio_result,
    }
    # 意图特定字段
    if payload.primary_intent == "PerformanceAnalysis" and portfolio_result:
        explain_data["performanceResult"] = {
            "diagnosis_type": portfolio_result.get("diagnosis_type"),
            "overall_pnl": portfolio_result.get("overall_pnl"),
            "structural_issue": portfolio_result.get("structural_issue"),
            "profit_drivers": portfolio_result.get("profit_drivers", []),
            "loss_drivers": portfolio_result.get("loss_drivers", []),
        }
    if payload.primary_intent == "AssetAllocation" and portfolio_result:
        explain_data["allocationResult"] = {
            "allocation_type": portfolio_result.get("allocation_type"),
            "capital_amount": capital_amount,
            "allocation_plan": portfolio_result.get("allocation_plan", []),
            "priority_order": portfolio_result.get("priority_order", []),
        }
    _ALLOC_EXPLAIN_STORE[f"{session_id}:{decision_id}"] = explain_data

    # 保存对话历史（持久化）
    try:
        await asyncio.to_thread(
            save_conversation_turn, session_id, user_input,
            generic_llm.chat_answer or answer,
            payload.primary_intent, payload.entities.asset,
        )
    except Exception as e:
        print(f"[decision_service] 保存对话历史失败: {e}", flush=True)

    done_payload = {
        "decision_id":     decision_id,
        "conclusion_level": conclusion_type,
        "conclusion_label": action_label,
        "portfolioResult":  portfolio_result,
    }
    if payload.primary_intent == "PerformanceAnalysis":
        done_payload["performanceResult"] = explain_data.get("performanceResult")
    if payload.primary_intent == "AssetAllocation":
        done_payload["allocationResult"] = explain_data.get("allocationResult")
    yield _sse("done", done_payload)


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


async def _stream_general_chat(user_input: str, session_id: str = "") -> AsyncGenerator[str, None]:
    """普通对话路由"""
    yield _sse("stage", {"stage": "reasoning", "label": "回复中..."})

    response = await asyncio.to_thread(llm_engine.chat, user_input, None)
    answer = response or "（暂时无法回复，请重试）"

    async for chunk_event in _stream_text(answer):
        yield chunk_event

    # 保存对话历史（持久化）
    if session_id:
        try:
            await asyncio.to_thread(
                save_conversation_turn, session_id, user_input, answer, "Education", None,
            )
        except Exception as e:
            print(f"[decision_service] 保存对话历史失败: {e}", flush=True)

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
        # 如果有 LLM 结构化结果，用 decisionType 覆盖 action 显示
        action_display = result.intent.action_type
        if result.llm and result.llm.structured_result:
            dt = result.llm.structured_result.get("decisionType")
            if dt:
                action_display = dt  # 前端 ACTION_LABELS 会映射 trim→减仓 等
        d["intent"] = {
            "asset":         result.intent.asset,
            "action":        action_display,
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
