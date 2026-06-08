"""
投资行动模块 API 路由 — v3.2

端点清单：
- POST   /api/action/drafts                — 创建草稿
- GET    /api/action/drafts/{id}           — 查看草稿
- PATCH  /api/action/drafts/{id}           — 编辑草稿
- POST   /api/action/drafts/{id}/confirm   — 确认草稿
- DELETE /api/action/drafts/{id}           — 丢弃草稿
- GET    /api/action/strategies            — 标的策略列表
- GET    /api/action/strategies/{id}       — 策略详情
- POST   /api/action/strategies/{id}/pause   — 暂停策略
- POST   /api/action/strategies/{id}/resume  — 恢复策略
- POST   /api/action/strategies/{id}/discard — 作废策略
- POST   /api/action/strategies/{id}/check_risk  — 风控检查（M6）
- POST   /api/action/strategies/{id}/place_order — 确认下单（M6）
- GET    /api/action/orders                — 订单列表
- GET    /api/action/orders/{id}           — 订单详情
"""
import json
import logging
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from app.database import get_session
from backend.services.action.order_manager import OrderManager
from backend.services.action.brokers.factory import get_broker_adapter
from backend.services.action.risk_engine import (
    check_order_risk, validate_confirmation, CONFIRMATION_TEXT,
)
from backend.utils.datetime_utils import utc_iso

logger = logging.getLogger(__name__)

router = APIRouter()


import os as _action_os

_BROKER_MODE = _action_os.getenv("BROKER_MODE", "mock")


def _get_manager():
    """获取 OrderManager 实例（每次请求新建 session + 注入 broker_adapter）。

    v3.4 M3: 通过 BROKER_MODE 环境变量切换 mock / tiger.paper / tiger.live。
    默认 "mock"(向后兼容 v3.2 行为)。
    """
    session = get_session()
    def _resolve_broker_name(bm: str) -> str:
        if "tiger" in bm:
            return "tiger"
        if "ibkr" in bm:
            return "ibkr"
        return "mock"

    adapter = get_broker_adapter(
        broker_name=_resolve_broker_name(_BROKER_MODE),
        mode=_BROKER_MODE.split(".")[-1] if "." in _BROKER_MODE else _BROKER_MODE,
    )
    return OrderManager(session, broker_adapter=adapter), session


def _serialize_draft(draft) -> dict:
    return {
        "id": draft.id,
        "conversation_id": draft.conversation_id,
        "decision_summary": draft.decision_summary,
        "payload": json.loads(draft.payload) if draft.payload else None,
        "status": draft.status,
        "created_at": utc_iso(draft.created_at),
        "updated_at": utc_iso(draft.updated_at),
        "confirmed_at": utc_iso(draft.confirmed_at),
        "discarded_at": utc_iso(draft.discarded_at),
    }


def _serialize_strategy(s) -> dict:
    return {
        "id": s.id,
        "source_draft_id": s.source_draft_id,
        "parent_intent_id": getattr(s, "parent_intent_id", None),
        "symbol": s.symbol,
        "side": s.side,
        "target_quantity": s.target_quantity,
        "target_quantity_pct": float(s.target_quantity_pct) if s.target_quantity_pct else None,
        "cumulative_filled_quantity": s.cumulative_filled_quantity,
        "order_type": s.order_type,
        "trigger_price": float(s.trigger_price) if s.trigger_price else None,
        "limit_price": float(s.limit_price) if s.limit_price else None,
        "status": s.status,
        "decision_basis": s.decision_basis,
        "created_at": utc_iso(s.created_at),
        "updated_at": utc_iso(s.updated_at),
    }


def _serialize_order(o) -> dict:
    return {
        "id": o.id,
        "strategy_id": o.strategy_id,
        "broker_name": o.broker_name,
        "broker_order_id": o.broker_order_id,
        "symbol": o.symbol,
        "side": o.side,
        "quantity": o.quantity,
        "filled_quantity": o.filled_quantity,
        "order_type": o.order_type,
        "limit_price": float(o.limit_price) if o.limit_price else None,
        "avg_filled_price": float(o.avg_filled_price) if o.avg_filled_price else None,
        "status": o.status,
        "created_at": utc_iso(o.created_at),
    }


# ═══════════════════════════════════════════════════════════════════
# ActionDraft 端点
# ═══════════════════════════════════════════════════════════════════

@router.post("/drafts/generate")
def generate_draft(body: dict):
    """AI 生成行动清单草稿（调 ActionPlanner Skill）。"""
    from backend.services.action.action_planner import (
        plan_actions, ActionPlannerInput,
    )

    conversation_id = body.get("conversation_id", "")
    conversation_context = body.get("conversation_context", [])
    expressing_output = body.get("expressing_output", {})

    # 调 ActionPlanner Skill
    planner_input = ActionPlannerInput(
        conversation_id=conversation_id,
        conversation_context=conversation_context,
        expressing_output=expressing_output,
    )
    draft_result = plan_actions(planner_input)

    # 入库
    mgr, session = _get_manager()
    try:
        draft = mgr.create_draft(
            conversation_id=conversation_id,
            payload=draft_result.to_payload_dict(),
            decision_summary=draft_result.decision_summary,
        )
        session.commit()

        result = _serialize_draft(draft)
        result["risk_notes"] = draft_result.risk_notes
        result["missing_fields"] = draft_result.missing_fields
        return result
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


@router.post("/drafts")
def create_draft(body: dict):
    """手动创建行动清单草稿（未来扩展）。"""
    mgr, session = _get_manager()
    try:
        draft = mgr.create_draft(
            conversation_id=body.get("conversation_id", ""),
            payload=body.get("payload", {}),
            decision_summary=body.get("decision_summary", ""),
        )
        session.commit()
        return _serialize_draft(draft)
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: str):
    """查看草稿。"""
    mgr, session = _get_manager()
    try:
        draft = mgr.get_draft(draft_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="草稿不存在")
        return _serialize_draft(draft)
    finally:
        session.close()


@router.patch("/drafts/{draft_id}")
def update_draft(draft_id: str, body: dict):
    """编辑草稿（仅 draft 状态可编辑）。"""
    mgr, session = _get_manager()
    try:
        draft = mgr.update_draft(draft_id, body)
        session.commit()
        return _serialize_draft(draft)
    except ValueError as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


@router.post("/drafts/{draft_id}/confirm")
def confirm_draft(draft_id: str):
    """确认草稿 → 拆解为策略/配置意图。missing_fields 非空时返回 422。"""
    mgr, session = _get_manager()
    try:
        entities = mgr.confirm_draft(draft_id)
        session.commit()
        return {
            "draft_id": draft_id,
            "status": "confirmed",
            "created_entities": len(entities),
        }
    except ValueError as e:
        session.rollback()
        code = 422 if "缺失信息" in str(e) else 400
        raise HTTPException(status_code=code, detail=str(e))
    finally:
        session.close()


@router.delete("/drafts/{draft_id}")
def discard_draft(draft_id: str):
    """丢弃草稿。"""
    mgr, session = _get_manager()
    try:
        draft = mgr.discard_draft(draft_id)
        session.commit()
        return _serialize_draft(draft)
    except ValueError as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════
# SymbolStrategy 端点
# ═══════════════════════════════════════════════════════════════════

@router.get("/strategies")
def list_strategies(
    status: Optional[str] = Query(None),
    parent_intent_id: Optional[str] = Query(None),
):
    """标的策略列表（支持 ?status= 和 ?parent_intent_id= 筛选）。"""
    mgr, session = _get_manager()
    try:
        strategies = mgr.list_strategies(status=status, parent_intent_id=parent_intent_id)
        # is_held: 查 positions 表判断用户是否持有该标的
        from app.models import Position
        held_symbols = set()
        positions = session.query(Position).filter(Position.market_value_cny > 0).all()
        for p in positions:
            held_symbols.add(p.name)
            if p.ticker:
                held_symbols.add(p.ticker)
        result = []
        for s in strategies:
            d = _serialize_strategy(s)
            d["is_held"] = s.symbol in held_symbols
            result.append(d)
        return {"items": result}
    finally:
        session.close()


@router.get("/strategies/{strategy_id}")
def get_strategy(strategy_id: str):
    """策略详情。"""
    mgr, session = _get_manager()
    try:
        s = mgr.get_strategy(strategy_id)
        if s is None:
            raise HTTPException(status_code=404, detail="策略不存在")
        return _serialize_strategy(s)
    finally:
        session.close()


@router.post("/strategies/{strategy_id}/pause")
def pause_strategy(strategy_id: str):
    """暂停策略。"""
    mgr, session = _get_manager()
    try:
        s = mgr.pause_strategy(strategy_id)
        session.commit()
        return _serialize_strategy(s)
    except ValueError as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


@router.post("/strategies/{strategy_id}/resume")
def resume_strategy(strategy_id: str):
    """恢复策略。"""
    mgr, session = _get_manager()
    try:
        s = mgr.resume_strategy(strategy_id)
        session.commit()
        return _serialize_strategy(s)
    except ValueError as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


@router.post("/strategies/{strategy_id}/discard")
def discard_strategy(strategy_id: str):
    """作废策略。"""
    mgr, session = _get_manager()
    try:
        s = mgr.discard_strategy(strategy_id)
        session.commit()
        return _serialize_strategy(s)
    except ValueError as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════
# OrderRecord 端点
# ═══════════════════════════════════════════════════════════════════

@router.get("/orders")
def list_orders(
    strategy_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """订单列表（支持 ?strategy_id= 和 ?status= 筛选）。"""
    mgr, session = _get_manager()
    try:
        orders = mgr.list_orders(strategy_id=strategy_id, status=status)
        return {"items": [_serialize_order(o) for o in orders]}
    finally:
        session.close()


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str):
    """本地取消订单。"""
    mgr, session = _get_manager()
    try:
        o = mgr.cancel_order(order_id)
        session.commit()
        return _serialize_order(o)
    except ValueError as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


@router.get("/orders/{order_id}")
def get_order(order_id: str):
    """订单详情。"""
    mgr, session = _get_manager()
    try:
        o = mgr.get_order(order_id)
        if o is None:
            raise HTTPException(status_code=404, detail="订单不存在")
        return _serialize_order(o)
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════
# Timeline 聚合端点
# ═══════════════════════════════════════════════════════════════════

@router.get("/timeline")
def get_timeline(limit: int = Query(50, le=200)):
    """行动记录时间轴（audit_logs 聚合 + 追溯链路）。"""
    from backend.services.action.models import AuditLog, SymbolStrategy, ActionDraft
    mgr, session = _get_manager()
    try:
        # 1. 查 audit_logs（按时间倒序）
        logs = (
            session.query(AuditLog)
            .order_by(AuditLog.timestamp.desc())
            .limit(limit)
            .all()
        )

        # 2. 提取关联 ID 去重
        strategy_ids = set()
        draft_ids = set()
        order_ids = set()
        for log in logs:
            p = json.loads(log.payload) if log.payload else {}
            if p.get("strategy_id"):
                strategy_ids.add(p["strategy_id"])
            if p.get("draft_id"):
                draft_ids.add(p["draft_id"])
            if p.get("order_id"):
                order_ids.add(p["order_id"])

        # 3. 批量 IN 查询
        strategies_map = {}
        if strategy_ids:
            rows = session.query(SymbolStrategy).filter(
                SymbolStrategy.id.in_(strategy_ids)
            ).all()
            strategies_map = {s.id: s for s in rows}

        drafts_map = {}
        if draft_ids:
            rows = session.query(ActionDraft).filter(
                ActionDraft.id.in_(draft_ids)
            ).all()
            drafts_map = {d.id: d for d in rows}

        orders_map = {}
        if order_ids:
            from backend.services.action.models import OrderRecord
            rows = session.query(OrderRecord).filter(
                OrderRecord.id.in_(order_ids)
            ).all()
            orders_map = {o.id: o for o in rows}

        # 4. 组装时间轴
        items = []
        for log in logs:
            p = json.loads(log.payload) if log.payload else {}
            item = {
                "id": log.id,
                "event_type": log.event_type,
                "timestamp": utc_iso(log.timestamp),
                "payload": p,
            }

            # 追溯链路
            trace = {}
            sid = p.get("strategy_id")
            did = p.get("draft_id")
            oid = p.get("order_id")

            # 订单事件 → 从订单找策略
            if oid and oid in orders_map:
                o = orders_map[oid]
                trace["order"] = {
                    "id": o.id, "symbol": o.symbol, "side": o.side,
                    "quantity": o.quantity, "status": o.status,
                    "limit_price": float(o.limit_price) if o.limit_price else None,
                }
                if o.strategy_id and o.strategy_id not in strategies_map:
                    s = session.query(SymbolStrategy).filter_by(id=o.strategy_id).first()
                    if s:
                        strategies_map[s.id] = s
                sid = sid or o.strategy_id

            if sid and sid in strategies_map:
                s = strategies_map[sid]
                trace["strategy"] = {
                    "id": s.id, "symbol": s.symbol, "side": s.side,
                    "target_quantity": s.target_quantity,
                    "limit_price": float(s.limit_price) if s.limit_price else None,
                    "related_conversation_id": s.related_conversation_id,
                }
                did = did or s.source_draft_id

            if did and did in drafts_map:
                d = drafts_map[did]
                trace["draft"] = {
                    "id": d.id,
                    "decision_summary": (d.decision_summary or "")[:80],
                    "conversation_id": d.conversation_id,
                }

            item["trace"] = trace
            items.append(item)

        return {"items": items, "total": len(items)}
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════
# ActionDraft 列表端点
# ═══════════════════════════════════════════════════════════════════

@router.get("/drafts")
def list_drafts(status: Optional[str] = Query(None)):
    """草稿列表（?status=draft 筛选待处理草稿）。"""
    mgr, session = _get_manager()
    try:
        drafts = mgr.list_drafts(status=status)
        return {"items": [_serialize_draft(d) for d in drafts]}
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════
# AllocationIntent 端点
# ═══════════════════════════════════════════════════════════════════

def _serialize_intent(i, strategies_count: int = 0) -> dict:
    return {
        "id": i.id,
        "source_draft_id": i.source_draft_id,
        "title": i.title,
        "target_allocation": json.loads(i.target_allocation) if i.target_allocation else None,
        "current_allocation_snapshot": (
            json.loads(i.current_allocation_snapshot) if i.current_allocation_snapshot else None
        ),
        "status": i.status,
        "related_conversation_id": i.related_conversation_id,
        "decision_basis": i.decision_basis,
        "related_strategies_count": strategies_count,
        "created_at": utc_iso(i.created_at),
        "updated_at": utc_iso(i.updated_at),
        "completed_at": utc_iso(i.completed_at),
    }


@router.get("/intents")
def list_intents(status: Optional[str] = Query(None)):
    """配置意图列表（?status=active / completed / discarded）。"""
    mgr, session = _get_manager()
    try:
        intents = mgr.list_intents(status=status)
        return {"items": [
            _serialize_intent(i, mgr.count_strategies_for_intent(i.id))
            for i in intents
        ]}
    finally:
        session.close()


@router.get("/intents/{intent_id}")
def get_intent(intent_id: str):
    """配置意图详情。"""
    mgr, session = _get_manager()
    try:
        i = mgr.get_intent(intent_id)
        if i is None:
            raise HTTPException(status_code=404, detail="配置意图不存在")
        return _serialize_intent(i, mgr.count_strategies_for_intent(i.id))
    finally:
        session.close()


@router.patch("/intents/{intent_id}")
def update_intent(intent_id: str, body: dict):
    """编辑配置意图（title / target_allocation / decision_basis）。"""
    mgr, session = _get_manager()
    try:
        i = mgr.update_intent(intent_id, body)
        session.commit()
        return _serialize_intent(i, mgr.count_strategies_for_intent(i.id))
    except ValueError as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


@router.post("/intents/{intent_id}/discard")
def discard_intent(intent_id: str):
    """作废配置意图。"""
    mgr, session = _get_manager()
    try:
        i = mgr.discard_intent(intent_id)
        session.commit()
        return _serialize_intent(i)
    except ValueError as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════
# M6: 风控检查 + 人工确认下单
# ═══════════════════════════════════════════════════════════════════


def _get_portfolio_total_value(session) -> Decimal:
    """获取投资组合总资产（CNY）。
    TODO: v3.x 升级多用户后，改为从 request.user.portfolio_id 获取
    """
    from app.utils.position_aggregator import aggregate_investment_positions
    _, total_assets = aggregate_investment_positions(pid=1)
    return Decimal(str(total_assets))


def _get_position_value(session, symbol: str) -> Optional[Decimal]:
    """获取指定标的的当前持仓市值（CNY）。"""
    from app.models import Position
    total = Decimal("0")
    positions = session.query(Position).filter(
        Position.market_value_cny > 0,
    ).all()
    for p in positions:
        if p.ticker == symbol or p.name == symbol:
            total += Decimal(str(p.market_value_cny))
    return total


def _get_fx_rate_to_cny(symbol: str, session) -> Decimal:
    """推断标的交易币种并返回对 CNY 的汇率。
    通过查 positions 表的 currency 字段判断交易币种。
    """
    from app.models import Position
    from app.fx_service import FALLBACK_RATES
    positions = session.query(Position).filter(
        Position.ticker == symbol,
    ).all()
    if not positions:
        # 无法确定币种，按 USD 处理（美股为主）
        return Decimal(str(FALLBACK_RATES.get("USD", 7.0)))
    currency = getattr(positions[0], 'currency', 'CNY')
    if currency == 'CNY':
        return Decimal("1")
    return Decimal(str(FALLBACK_RATES.get(currency, 7.0)))


def _get_discipline_result(symbol: str, side: str) -> Optional[dict]:
    """调用纪律检查（简化版）。失败时返回 None（不阻断下单）。"""
    try:
        from backend.graph.tools import execute_check_discipline
        action_type = "BUY" if side.upper() == "BUY" else "SELL"
        result = execute_check_discipline(
            asset_name=symbol,
            portfolio_id=1,
            action_type=action_type,
        )
        return result.model_dump()
    except Exception as e:
        logger.warning(f"[M6] 纪律检查失败（不阻断下单）: {e}")
        return None


@router.post("/strategies/{strategy_id}/check_risk")
def check_risk(strategy_id: str, body: dict, request: Request):
    """风控检查：前端打开确认弹窗时调用。"""
    mgr, session = _get_manager()
    try:
        strategy = mgr.get_strategy(strategy_id)
        if strategy is None:
            raise HTTPException(status_code=404, detail="策略不存在")

        quantity = body.get("quantity", strategy.target_quantity or 0)
        limit_price = Decimal(str(body.get("limit_price", strategy.limit_price or 0)))

        total_value = _get_portfolio_total_value(session)
        position_value = _get_position_value(session, strategy.symbol)
        discipline = _get_discipline_result(strategy.symbol, strategy.side)
        fx_rate = _get_fx_rate_to_cny(strategy.symbol, session)

        risk_result = check_order_risk(
            symbol=strategy.symbol,
            side=strategy.side,
            quantity=quantity,
            limit_price=limit_price,
            portfolio_total_value=total_value,
            current_position_value=position_value,
            discipline_result=discipline,
            fx_rate_to_cny=fx_rate,
        )

        return {
            "passed": risk_result.passed,
            "requires_confirmation": risk_result.requires_confirmation,
            "warnings": [
                {
                    "rule": w.rule,
                    "severity": w.severity,
                    "message": w.message,
                    "detail": w.detail,
                }
                for w in risk_result.warnings
            ],
            "portfolio_total_value": float(total_value),
            "confirmation_text_required": CONFIRMATION_TEXT if risk_result.requires_confirmation else None,
        }
    finally:
        session.close()


@router.post("/strategies/{strategy_id}/place_order")
def place_order(strategy_id: str, body: dict, request: Request):
    """
    人工确认下单（M6）。

    后端为风控最终裁判：
    1. 二次跑完整风控（不信任前端）
    2. 触发风控但 confirmation_text 不匹配 → 422
    3. 调 OrderManager.place_order
    4. 写增强审计日志
    """
    mgr, session = _get_manager()
    try:
        strategy = mgr.get_strategy(strategy_id)
        if strategy is None:
            raise HTTPException(status_code=404, detail="策略不存在")

        quantity = body.get("quantity", strategy.target_quantity or 0)
        limit_price_raw = body.get("limit_price", strategy.limit_price or 0)
        limit_price = Decimal(str(limit_price_raw))
        confirmation_text = body.get("confirmation_text", "")

        # 提取 IP + User-Agent
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent", "")

        # 1. 二次跑完整风控（后端为最终裁判）
        total_value = _get_portfolio_total_value(session)
        position_value = _get_position_value(session, strategy.symbol)
        discipline = _get_discipline_result(strategy.symbol, strategy.side)
        fx_rate = _get_fx_rate_to_cny(strategy.symbol, session)

        risk_result = check_order_risk(
            symbol=strategy.symbol,
            side=strategy.side,
            quantity=quantity,
            limit_price=limit_price,
            portfolio_total_value=total_value,
            current_position_value=position_value,
            discipline_result=discipline,
            fx_rate_to_cny=fx_rate,
        )

        # 2. 触发风控但确认文字不匹配 → 422
        if risk_result.requires_confirmation:
            if not validate_confirmation(risk_result, confirmation_text):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "risk_confirmation_required",
                        "message": "触发风控警告，需输入正确的风险确认文字",
                        "warnings": [w.message for w in risk_result.warnings],
                        "expected_text": CONFIRMATION_TEXT,
                    },
                )

        # 3. 调 OrderManager.place_order
        order = mgr.place_order(strategy_id, {
            "quantity": quantity,
            "limit_price": limit_price_raw,
        })

        # 4. 写增强审计日志（order_placed_manual_confirm）
        risk_warnings_data = [
            {"rule": w.rule, "message": w.message, "detail": w.detail}
            for w in risk_result.warnings
        ]
        mgr._audit(
            "order_placed_manual_confirm",
            payload={
                "order_id": order.id,
                "strategy_id": strategy_id,
                "symbol": strategy.symbol,
                "side": strategy.side,
                "quantity": quantity,
                "limit_price": float(limit_price),
                "risk_warnings": risk_warnings_data,
                "risk_acknowledged": bool(confirmation_text),
            },
            ip_address=ip_address,
            user_agent=user_agent,
        )

        session.commit()
        return _serialize_order(order)

    except HTTPException:
        session.rollback()
        raise
    except ValueError as e:
        session.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        session.rollback()
        logger.error(f"[M6] place_order 异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()
