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
    stripped = asset.strip()
    if len(stripped) <= 3 and any(vague == stripped for vague in VAGUE_ASSET_WORDS):
        return False
    if stripped in VAGUE_ASSET_WORDS:
        return False
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
    session_id: str,
    portfolio_id: int,
) -> AsyncGenerator[str, None]:
    """
    投资决策 SSE 流式接口核心逻辑。
    委托给 v3 PEER Agents 路径实现。
    """
    from backend.services.decision_service_v3 import run_chat_stream_v3
    async for event in run_chat_stream_v3(message, session_id, portfolio_id):
        yield event


def get_decision_explain(session_id: str, decision_id: str) -> Optional[dict]:
    """获取某次决策的完整 DecisionResult（序列化为 dict）"""
    alloc_key = f"{session_id}:{decision_id}"
    alloc_explain = _ALLOC_EXPLAIN_STORE.get(alloc_key)
    if alloc_explain is not None:
        return alloc_explain

    session_store = _DECISION_STORE.get(session_id, {})
    result = session_store.get(decision_id)
    if result is None:
        return None
    d = _serialize_decision_result(result)
    primary_intent = _PRIMARY_INTENT_CACHE.get(session_id)
    if primary_intent and "intent" in d:
        d["intent"]["primary_intent"] = primary_intent
    return d


def clear_session(session_id: str) -> None:
    """清除服务端会话（对话重置时调用）"""
    _DECISION_STORE.pop(session_id, None)
    _PRIMARY_INTENT_CACHE.pop(session_id, None)
    _ALLOC_SESSION_CTX.pop(session_id, None)
    keys_to_remove = [k for k in _ALLOC_EXPLAIN_STORE if k.startswith(f"{session_id}:")]
    for k in keys_to_remove:
        _ALLOC_EXPLAIN_STORE.pop(k, None)
    context_manager.clear_session(session_id)


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


def _store_result(session_id: str, result: DecisionResult) -> None:
    if session_id not in _DECISION_STORE:
        _DECISION_STORE[session_id] = {}
    _DECISION_STORE[session_id][result.decision_id] = result


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
