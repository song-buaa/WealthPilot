"""
Decision Service — 投资决策业务逻辑

v3.0 PEER Agents 架构。SSE 流式逻辑在 decision_service_v3.py 中实现。
本模块保留：
- 共享辅助函数（对话历史、澄清流程、候选筛选等）
- 进程内缓存（_DECISION_STORE 等）
- 公开接口（run_chat_stream → 委托 v3、get_decision_explain、clear_session）
- 序列化函数（供 /explain 端点使用）
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


# ── 进程内 decision 缓存（{conversation_id: {decision_id: DecisionResult}}）────────
# 服务重启后清空是预期行为，无需持久化
_DECISION_STORE: dict[str, dict[str, DecisionResult]] = {}

# ── primary_intent 缓存（intent_engine 输出，decision_engine 不存储）───────────
# key: conversation_id，value: 该 session 最近一次的 primary_intent 字符串
_PRIMARY_INTENT_CACHE: dict[str, str] = {}

# ── AssetAllocation 意图的 sessionContext 缓存 ─────────────────────────────────
_ALLOC_SESSION_CTX: dict[str, AllocationSessionContext] = {}

# ── AssetAllocation ExplainData 缓存（{conversation_id:decision_id: dict}）──────────
_ALLOC_EXPLAIN_STORE: dict[str, dict] = {}


# ── 多轮对话历史（持久化） ────────────────────────────────────────────────────

def get_conversation_history(conversation_id: str, limit: int = 20) -> list[dict]:
    """返回用于 LLM 的对话历史（摘要 + 最近 N 条原文）。

    如果该会话有 context_summary，在消息列表前插入 system 摘要消息。
    """
    from app.database import get_session as get_db_session
    from app.models import Conversation, ConversationMessage

    db = get_db_session()
    try:
        # 取摘要
        conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        summary = conv.context_summary if conv else None

        # 取最近 limit 条消息
        rows = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
            .all()
        )
        rows.reverse()

        result: list[dict] = []
        if summary:
            result.append({
                "role": "system",
                "content": f"以下是本次对话的历史摘要：\n{summary}",
            })
        for r in rows:
            result.append({
                "role": r.role,
                "content": r.content,
                "intent": r.intent,
                "asset": r.asset,
            })
        return result
    finally:
        db.close()


def generate_conversation_title(first_message: str) -> str:
    """用 LLM 根据首条消息生成简洁的中文标题（不超过 12 字）。
    失败时 fallback 到前 20 字截断。
    """
    fallback = first_message[:20] if first_message else "新对话"
    try:
        from intent_engine._llm_client import get_client
        client = get_client()
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": (
                    "你是一个对话标题生成助手，根据用户的第一条消息，"
                    "用不超过12个字生成一个简洁的中文标题，只返回标题文字，不加引号标点"
                )},
                {"role": "user", "content": first_message},
            ],
            max_tokens=30,
            temperature=0,
            timeout=5,
        )
        title = (resp.choices[0].message.content or "").strip()
        return title if title else fallback
    except Exception:
        return fallback


def _update_conversation_title_async(conversation_id: str, first_message: str) -> None:
    """在后台线程中用 LLM 生成标题并写入 DB，不阻塞主流程。"""
    import threading

    def _worker():
        title = generate_conversation_title(first_message)
        from app.database import get_session as get_db_session
        from app.models import Conversation
        db = get_db_session()
        try:
            conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
            if conv:
                conv.title = title
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    threading.Thread(target=_worker, daemon=True).start()


# ── 长对话记忆压缩 ────────────────────────────────────────────────────────

_SUMMARY_PROMPT = """你是一个投资对话摘要助手。
将以下对话历史压缩成简洁摘要，要求：
- 保留所有提及的标的名称（如理想汽车、英伟达）
- 保留关键数字（价格、仓位比例、盈亏比例）
- 保留每个标的的操作结论（买入/减仓/持有/观望）
- 保留纪律检查结果（通过/违规/提示）
- 删除铺垫性的分析过程，只保留结论
- 摘要控制在 500 字以内
- 如果有旧摘要，将旧摘要和新对话合并压缩
只输出摘要文字，不加前缀或标记。"""


def generate_context_summary(
    messages_to_compress: list[dict],
    existing_summary: str | None = None,
) -> str:
    """将旧摘要 + 待压缩消息压缩成新摘要。失败时返回旧摘要。"""
    fallback = existing_summary or ""
    try:
        from intent_engine._llm_client import get_client
        client = get_client()

        user_content_parts: list[str] = []
        if existing_summary:
            user_content_parts.append(f"【旧摘要】\n{existing_summary}\n")
        user_content_parts.append("【新增对话】")
        for m in messages_to_compress:
            role_label = "用户" if m["role"] == "user" else "助手"
            content = m["content"][:300]  # 截断超长单条消息
            user_content_parts.append(f"{role_label}：{content}")

        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": _SUMMARY_PROMPT},
                {"role": "user", "content": "\n".join(user_content_parts)},
            ],
            max_tokens=600,
            temperature=0,
            timeout=15,
        )
        summary = (resp.choices[0].message.content or "").strip()
        return summary if summary else fallback
    except Exception:
        return fallback


def _compress_conversation_async(conversation_id: str) -> None:
    """后台线程：压缩旧消息为摘要，不阻塞主流程。"""
    import threading

    def _worker():
        from app.database import get_session as get_db_session
        from app.models import Conversation, ConversationMessage

        db = get_db_session()
        try:
            # 取所有未摘要的消息（升序）
            unsummarized = (
                db.query(ConversationMessage)
                .filter(
                    ConversationMessage.conversation_id == conversation_id,
                    ConversationMessage.is_summarized == False,
                )
                .order_by(ConversationMessage.created_at.asc())
                .all()
            )

            if len(unsummarized) <= 20:
                return  # 不需要压缩

            # 保留最新 10 条不压缩（短期窗口）
            to_compress = unsummarized[:-10]
            msgs_for_llm = [
                {"role": m.role, "content": m.content}
                for m in to_compress
            ]

            # 取旧摘要
            conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
            old_summary = conv.context_summary if conv else None

            # 生成新摘要
            new_summary = generate_context_summary(msgs_for_llm, old_summary)

            if new_summary and conv:
                conv.context_summary = new_summary
                for m in to_compress:
                    m.is_summarized = True
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    threading.Thread(target=_worker, daemon=True).start()


def save_conversation_turn(
    conversation_id: str,
    user_input: str,
    chat_answer: str,
    intent: str | None = None,
    asset: str | None = None,
) -> None:
    """写入本轮的 user 消息和 assistant 消息，共两条记录。
    同时确保 conversations 主表有对应记录（首条消息自动创建）。
    """
    from app.database import get_session as get_db_session
    from app.models import Conversation, ConversationMessage

    db = get_db_session()
    try:
        # 确保 conversations 主表有记录
        conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if conv is None:
            title = user_input[:20] if user_input else None
            conv = Conversation(id=conversation_id, title=title)
            db.add(conv)

        # 首条消息判断：基于消息数量（而非 conv 是否存在）
        # M3 流程下前端先创建空会话再发消息，conv 在发消息前就已存在
        existing_msg_count = db.query(ConversationMessage).filter(
            ConversationMessage.conversation_id == conversation_id
        ).count()
        is_first_message = (existing_msg_count == 0)

        from datetime import datetime
        conv.updated_at = datetime.utcnow()
        # 首条消息且 title 为空时，先写截断标题兜底
        if is_first_message and not conv.title and user_input:
            conv.title = user_input[:20]

        db.add(ConversationMessage(
            conversation_id=conversation_id, role="user", content=user_input,
        ))
        db.add(ConversationMessage(
            conversation_id=conversation_id, role="assistant", content=chat_answer,
            intent=intent, asset=asset,
        ))
        db.commit()

        # 首条消息写入成功后，在后台异步用 LLM 生成更好的标题
        if is_first_message and user_input:
            _update_conversation_title_async(conversation_id, user_input)

        # 检查是否需要触发摘要压缩（未摘要消息 > 20 条）
        unsummarized_count = db.query(ConversationMessage).filter(
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.is_summarized == False,
        ).count()
        if unsummarized_count > 20:
            _compress_conversation_async(conversation_id)

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ── 会话上下文恢复（重启 / 切换会话后从 DB 重建 intent_engine 内存状态）────

def restore_conversation_context(conversation_id: str) -> None:
    """
    从 DB 读取历史消息，重建 intent_engine context_manager 的内存状态。

    调用时机：chat 请求到达时，若 _SESSIONS 中无该 conversation_id。
    恢复内容：
    - turn_index（基于 user 消息数量）
    - conversation_history（最近 5 轮 Turn 摘要）
    - inherited_fields.asset（最后一条含 asset 的 assistant 消息）
    """
    from intent_engine.context_manager import _SESSIONS, _SessionState, MAX_HISTORY_TURNS
    from intent_engine.types import Turn, InheritedFields

    # 已有状态，不重复恢复
    if conversation_id in _SESSIONS:
        return

    from app.database import get_session as get_db_session
    from app.models import ConversationMessage

    db = get_db_session()
    try:
        rows = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.asc())
            .limit(20)  # 最多读 20 条（10 轮对话）
            .all()
        )
        if not rows:
            return  # 空会话，不需要恢复

        # 按轮次配对：(user, assistant)
        turns: list[Turn] = []
        last_asset: str | None = None
        last_intent: str | None = None
        user_count = 0

        for r in rows:
            if r.role == "user":
                user_count += 1
                # 用 assistant 消息补全上一轮的 Turn
                # 这里先记录 user 内容，等下一条 assistant 时配对
            elif r.role == "assistant":
                # 构建 Turn 摘要
                summary = (r.content or "")[:100]
                entities_snapshot: dict[str, str] = {}
                if r.asset:
                    entities_snapshot["asset"] = r.asset
                    last_asset = r.asset
                if r.intent:
                    last_intent = r.intent

                turns.append(Turn(
                    turn_index=user_count,
                    intent=r.intent or "Unknown",
                    entities_snapshot=entities_snapshot,
                    summary=summary,
                ))

        # 只保留最近 MAX_HISTORY_TURNS 轮
        recent_turns = turns[-MAX_HISTORY_TURNS:]

        # 构建 _SessionState
        state = _SessionState(
            turn_index=user_count,
            conversation_history=recent_turns,
        )

        # 恢复 inherited_fields（最后的 asset）
        if last_asset:
            state.inherited_fields = InheritedFields(asset=last_asset)

        _SESSIONS[conversation_id] = state

    finally:
        db.close()


# ── 标的明确性校验与智能澄清 ────────────────────────────────────────────────

VAGUE_ASSET_WORDS = [
    "股票", "基金", "标的", "持仓", "资产", "仓位",
    "这只", "那只", "某只", "一只", "一个", "这个", "那个",
]

# 进程内澄清上下文缓存 {conversation_id: {...}}
_CLARIFICATION_CTX: dict[str, dict] = {}


def _is_asset_unambiguous(asset: str | None) -> bool:
    """判断标的名称是否明确(无歧义),不涉及持仓。

    仅检查用户说的标的名称是否清晰可辨——"小米集团"明确,"那只股票"不明确。
    """
    if not asset:
        return False
    stripped = asset.strip()
    if not stripped:
        return False
    if stripped in VAGUE_ASSET_WORDS:
        return False
    # 检查是否包含模糊词作为子串(如"那只股票"含"那只")
    if any(vague in stripped for vague in VAGUE_ASSET_WORDS):
        return False
    return True


def _is_asset_in_portfolio(asset: str | None, positions: list) -> bool:
    """判断标的是否在用户当前持仓中。

    匹配规则(沿用原逻辑):
    - asset 与 position.name 双向 substring 匹配
    - asset 与 position.ticker 精确或 substring 匹配
    """
    if not asset:
        return False
    asset_lower = asset.strip().lower()
    for p in positions:
        if asset_lower in p.name.lower() or p.name.lower() in asset_lower:
            return True
        if p.ticker and (asset_lower == p.ticker.lower() or asset_lower in p.ticker.lower()):
            return True
    return False


def _is_asset_clear(asset: str | None, positions: list) -> bool:
    """已废弃。保留向后兼容,内部调用 _is_asset_unambiguous + _is_asset_in_portfolio。

    新代码应直接使用 _is_asset_unambiguous() 和 _is_asset_in_portfolio()。
    语义偷换 bug 见 M8.0 诊断报告。
    """
    return _is_asset_unambiguous(asset) and _is_asset_in_portfolio(asset, positions)


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


def _select_or_candidates(
    candidates: list,
    feature_type: str,
) -> tuple[object | None, list]:
    """取消直选逻辑，所有模糊输入一律返回候选清单。"""
    return None, candidates


def _build_clarification_reply(user_input: str, candidates: list, feature_type: str) -> str:
    """生成澄清回复文本，告诉用户筛选依据。"""
    if feature_type == "gain":
        intro = "根据您持仓的盈利情况，帮您筛出涨幅较大的标的："
        suffix = "请问您说的是哪一只？或者直接告诉我标的名称也可以。"
        items = [f"• {p.name}（+{p.pl_rate:.1f}%）" for p in candidates]
    elif feature_type == "loss":
        intro = "根据您持仓的亏损情况，帮您筛出浮亏较大的标的："
        suffix = "请问您说的是哪一只？"
        items = [f"• {p.name}（{p.pl_rate:.1f}%）" for p in candidates]
    elif feature_type == "heavy":
        intro = "根据您的持仓占比，帮您筛出仓位较重的标的："
        suffix = "请问您想分析的是哪一只？或者直接告诉我标的名称也可以。"
        items = [f"• {p.name}（占比 {p.weight * 100:.1f}%）" for p in candidates]
    else:
        intro = "根据您的持仓情况，帮您筛出以下标的："
        suffix = "请问您说的是哪一只？直接告诉我标的名称也可以。"
        items = [f"• {p.name}（占比 {p.weight * 100:.1f}%）" for p in candidates]
    return f"{intro}\n" + "\n".join(items) + f"\n\n{suffix}"


def _build_candidates_payload(candidates: list, feature_type: str) -> list[dict]:
    """构建结构化候选数据，供前端渲染点选按钮。"""
    result = []
    for p in candidates:
        if feature_type == "gain":
            metric_label = f"+{p.pl_rate:.1f}%"
        elif feature_type == "loss":
            metric_label = f"{p.pl_rate:.1f}%"
        else:
            metric_label = f"{p.weight * 100:.1f}%"
        result.append({
            "name": p.name,
            "symbol": getattr(p, "ticker", "") or "",
            "metric_label": metric_label,
            "metric_type": feature_type,
        })
    return result


def _try_resolve_clarification(conversation_id: str, user_input: str, positions: list) -> str | None:
    """
    尝试从澄清上下文中解析用户的回复。
    如果用户输入能匹配到候选标的之一，返回合并后的问题；否则返回 None。
    """
    ctx = _CLARIFICATION_CTX.get(conversation_id)
    if not ctx or not ctx.get("pending_clarification"):
        return None

    input_lower = user_input.lower().strip()
    candidates = ctx.get("candidates", [])

    matched_asset = None
    for name in candidates:
        if name.lower() in input_lower or input_lower in name.lower():
            matched_asset = name
            break
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
        _CLARIFICATION_CTX.pop(conversation_id, None)
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


# ══════════════════════════════════════════════════════════════════
# 公开接口
# ══════════════════════════════════════════════════════════════════

async def run_chat_stream(
    message: str,
    conversation_id: str,
    portfolio_id: int,
) -> AsyncGenerator[str, None]:
    """
    投资决策 SSE 流式接口核心逻辑。
    委托给 v3 PEER Agents 路径实现。
    """
    from backend.services.decision_service_v3 import run_chat_stream_v3
    async for event in run_chat_stream_v3(message, conversation_id, portfolio_id):
        yield event


def get_decision_explain(conversation_id: str, decision_id: str) -> Optional[dict]:
    """获取某次决策的完整 DecisionResult（序列化为 dict）"""
    alloc_key = f"{conversation_id}:{decision_id}"
    alloc_explain = _ALLOC_EXPLAIN_STORE.get(alloc_key)
    if alloc_explain is not None:
        return alloc_explain

    session_store = _DECISION_STORE.get(conversation_id, {})
    result = session_store.get(decision_id)
    if result is None:
        return None
    d = _serialize_decision_result(result)
    primary_intent = _PRIMARY_INTENT_CACHE.get(conversation_id)
    if primary_intent and "intent" in d:
        d["intent"]["primary_intent"] = primary_intent
    return d


def clear_session(conversation_id: str) -> None:
    """清除服务端会话（对话重置时调用）"""
    _DECISION_STORE.pop(conversation_id, None)
    _PRIMARY_INTENT_CACHE.pop(conversation_id, None)
    _ALLOC_SESSION_CTX.pop(conversation_id, None)
    keys_to_remove = [k for k in _ALLOC_EXPLAIN_STORE if k.startswith(f"{conversation_id}:")]
    for k in keys_to_remove:
        _ALLOC_EXPLAIN_STORE.pop(k, None)
    context_manager.clear_session(conversation_id)


# ══════════════════════════════════════════════════════════════════
# 内部：结果构建与序列化（v3 路径仍依赖）
# ══════════════════════════════════════════════════════════════════

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


def _store_result(conversation_id: str, result: DecisionResult) -> None:
    if conversation_id not in _DECISION_STORE:
        _DECISION_STORE[conversation_id] = {}
    _DECISION_STORE[conversation_id][result.decision_id] = result


def _serialize_target_position(ld, result) -> dict:
    """序列化 target_position，注入 estimated_shares（从 market_data 反算）。"""
    tp = ld.target_position
    info = {
        "name":             tp.name,
        "weight":           tp.weight,
        "market_value_cny": tp.market_value_cny,
        "profit_loss_rate": tp.profit_loss_rate,
        "platforms":        tp.platforms,
    }
    market_data = getattr(result, '_market_data', None)
    if market_data and hasattr(market_data, 'quote') and market_data.quote:
        q = market_data.quote
        if q.current_price and q.current_price > 0:
            info["current_price"] = q.current_price
            info["currency"] = getattr(q, 'currency', 'USD')
            fx = 7.2 if info["currency"] == "USD" else (0.92 if info["currency"] == "HKD" else 1.0)
            est_shares = round(tp.market_value_cny / (q.current_price * fx))
            info["estimated_shares"] = est_shares
    return info


def _serialize_decision_result(result: DecisionResult) -> dict:
    """序列化 DecisionResult 为 JSON-safe dict（供 /explain 端点返回）"""
    d: dict = {
        "decision_id": result.decision_id,
        "stage":       result.stage.value if result.stage else None,
        "was_aborted": result.was_aborted,
        "aborted_reason": result.aborted_reason,
    }

    if result.intent:
        action_display = result.intent.action_type
        if result.llm and result.llm.structured_result:
            dt = result.llm.structured_result.get("decisionType")
            if dt:
                action_display = dt
        d["intent"] = {
            "asset":         result.intent.asset,
            "action":        action_display,
            "time_context":  result.intent.time_horizon,
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
            "target_position": _serialize_target_position(ld, result) if ld.target_position else None,
            # v3.6.1: 知识库引用字段
            "retrieved_principles": [
                {
                    "content": c.content,
                    "source_type": c.source_type,
                    "source_channel": getattr(c, "source_channel", "local_principles"),
                    "parent_doc_path": c.parent_doc_path,
                    "date": getattr(c, "date", None),
                    "semantic_score": round(c.semantic_score, 3),
                }
                for c in (getattr(ld, "retrieved_principles", None) or [])
            ],
            "retrieved_research_views": [
                {
                    "content": c.content,
                    "source_type": c.source_type,
                    "source_channel": getattr(c, "source_channel", "local_rag"),
                    "parent_doc_path": c.parent_doc_path,
                    "date": getattr(c, "date", None),
                    "semantic_score": round(c.semantic_score, 3),
                }
                for c in (getattr(ld, "retrieved_research_views", None) or [])
            ],
        }

    if result.pre_check:
        d["pre_check"] = {
            "passed":  result.pre_check.passed,
            "message": result.pre_check.message,
        }

    if result.rules:
        d["rules"] = {
            "passed":         not result.rules.violation,
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

    d["intent"] = {
        "primary_intent": "AssetAllocation",
        "asset": None,
        "action": intent_type,
        "time_context": None,
        "confidence": 1.0,
        "intent_type": "asset_allocation",
        "is_inherited": False,
    }

    data_section: dict = {}
    if ep and ep.key_data:
        kd = ep.key_data
        data_section["totalAssets"] = kd.get("totalAssets") or kd.get("totalAmount") or kd.get("incrementAmount")
        data_section["overallStatus"] = kd.get("overallStatus")
        data_section["actionHint"] = kd.get("actionHint")
    if r.plan and r.plan.get("table"):
        data_section["allocationPlan"] = r.plan["table"]
    d["data"] = data_section

    if r.plan and r.plan.get("discipline"):
        disc = r.plan["discipline"]
        d["rules"] = {
            "passed": disc.get("passed", True),
            "violations": disc.get("violations", []),
        }
    else:
        d["rules"] = None

    d["signals"] = None
    d["pre_check"] = None

    reasoning_text = ep.reasoning if ep else ""
    d["llm"] = {
        "reasoning": [reasoning_text] if reasoning_text else [],
    } if ep else None

    return d
