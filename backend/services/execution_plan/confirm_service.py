"""
执行计划确认服务 — 把 ExecutionPlan 草案确认落库。

链路: ExecutionPlan(draft) → 组装 ActionDraft → confirm_draft
      → N 条 SymbolStrategy(带 plan_id) → 反写 tranche.linked_symbol_strategy_id

复用 OrderManager.create_draft / confirm_draft 下游轨道，不重建。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from backend.services.action.models import ActionDraft, SymbolStrategy
from backend.services.action.order_manager import OrderManager
from backend.services.execution_plan.models import ExecutionPlan, ExecutionTranche
from backend.services.execution_plan.state_machine import (
    validate_plan_transition, PlanStatus,
)

logger = logging.getLogger(__name__)


def confirm_execution_plan(
    session: Session,
    plan_id: str,
    broker_adapter=None,
) -> dict:
    """确认执行计划 → 拆成 N 条 SymbolStrategy 进投资行动轨道。

    Returns:
        {"plan_id": str, "draft_id": str, "strategies_created": int,
         "strategy_ids": list[str]}
    """
    plan = session.query(ExecutionPlan).filter_by(id=plan_id).first()
    if not plan:
        raise ValueError(f"执行计划不存在: {plan_id}")

    if plan.plan_status != PlanStatus.DRAFT:
        raise ValueError(f"只能确认 draft 状态的计划，当前: {plan.plan_status}")

    tranches = (
        session.query(ExecutionTranche)
        .filter_by(plan_id=plan_id)
        .order_by(ExecutionTranche.sequence)
        .all()
    )
    if not tranches:
        raise ValueError("计划无批次，无法确认")

    # ── 1. 组装 ActionDraft payload ──
    symbol_strategies = []
    for t in tranches:
        symbol_strategies.append({
            "symbol": plan.symbol,
            "side": _map_side(plan.side),
            "quantity": int(t.quantity),
            "order_type": t.order_type,
            "trigger_price": float(t.trigger_price) if t.trigger_price else None,
            "limit_price": float(t.limit_price) if t.limit_price else None,
            # 附加: plan_id + 批次序号，confirm_draft 时写入 SymbolStrategy
            "_plan_id": plan_id,
            "_tranche_sequence": t.sequence,
            "_tranche_id": t.id,
        })

    payload = {
        "symbol_strategies": symbol_strategies,
        "allocation_intents": [],
        "risk_notes": [plan.risk_notes] if plan.risk_notes else [],
        "missing_fields": [],
    }

    # ── 2. 创建 + 确认 ActionDraft ──
    mgr = OrderManager(session, broker_adapter=broker_adapter)
    draft = mgr.create_draft(
        conversation_id="",
        payload=payload,
        decision_summary=plan.rationale or f"执行计划 {plan_id}",
    )
    session.flush()

    entities = mgr.confirm_draft(draft.id)

    # ── 3. 回写 plan_id 到 SymbolStrategy + 反写 tranche ──
    strategy_ids = []
    for entity in entities:
        if isinstance(entity, SymbolStrategy):
            # 找对应的 tranche 序号
            idx = len(strategy_ids)
            if idx < len(symbol_strategies):
                ss = symbol_strategies[idx]
                entity.plan_id = ss["_plan_id"]
                entity.tranche_sequence = ss["_tranche_sequence"]

                # 反写 tranche.linked_symbol_strategy_id
                tranche_id = ss["_tranche_id"]
                tranche = session.query(ExecutionTranche).filter_by(id=tranche_id).first()
                if tranche:
                    tranche.linked_symbol_strategy_id = entity.id

            strategy_ids.append(entity.id)

    # ── 4. 计划状态 draft → active ──
    from datetime import datetime, timezone
    validate_plan_transition(plan.plan_status, PlanStatus.ACTIVE)
    plan.plan_status = PlanStatus.ACTIVE
    plan.activated_at = datetime.now(timezone.utc)

    session.flush()
    session.commit()

    logger.info(
        "[confirm_execution_plan] plan=%s → %d strategies created, draft=%s",
        plan_id, len(strategy_ids), draft.id,
    )

    return {
        "plan_id": plan_id,
        "draft_id": draft.id,
        "strategies_created": len(strategy_ids),
        "strategy_ids": strategy_ids,
    }


def _map_side(plan_side: str) -> str:
    """ExecutionPlan.side → SymbolStrategy.side 映射。"""
    return {"BUY": "BUY", "ADD": "BUY", "REDUCE": "SELL", "SELL": "SELL"}.get(
        plan_side, plan_side
    )
