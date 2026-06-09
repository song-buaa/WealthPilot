"""
触发评估循环 — M6 核心。

每 15 分钟扫描 active 计划的 pending 批次，判断是否到价。
到价 → 走纪律间隔检查 → 通过则置 armed。
**绝不自动下单。** armed 只是标记"已到价待人确认"。

安全红线：代码里不存在任何 armed → 自动 place_order 的路径。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from backend.services.execution_plan.models import ExecutionPlan, ExecutionTranche
from backend.services.execution_plan.state_machine import (
    validate_tranche_transition, TrancheStatus, PlanStatus,
)
from backend.services.execution_plan.market_hours import is_market_open

logger = logging.getLogger(__name__)

# 持久化上次扫描时间（模块级，进程内；重启后丢失，第二步补扫会从 DB 读）
_last_scan_time: datetime | None = None


def evaluate_triggers(
    session: Session,
    fetch_kline_high_low=None,
    now: datetime | None = None,
) -> dict:
    """触发评估主循环。

    Args:
        session: DB session
        fetch_kline_high_low: 可注入的数据获取函数，签名 (symbol, market) → (high, low) | None
            默认用富途/Tiger 15 分钟 K 线
        now: 当前时间（可注入，测试用）

    Returns:
        {"scanned": int, "armed": int, "skipped_interval": int, "skipped_market": int}
    """
    global _last_scan_time
    if now is None:
        now = datetime.now(timezone.utc)

    if fetch_kline_high_low is None:
        fetch_kline_high_low = _fetch_kline_high_low_default

    result = {"scanned": 0, "armed": 0, "skipped_interval": 0, "skipped_market": 0}

    # 读纪律间隔
    from app.discipline.config import get_rules
    rules = get_rules()
    min_interval_days = rules.get("position_sizing", {}).get(
        "min_interval_between_adds_days", 1
    )

    # 查所有 active 计划
    plans = session.query(ExecutionPlan).filter_by(plan_status=PlanStatus.ACTIVE).all()

    for plan in plans:
        # 交易时段检查
        if not is_market_open(plan.market, now):
            result["skipped_market"] += 1
            continue

        # 查该计划的 pending 批次
        pending_tranches = (
            session.query(ExecutionTranche)
            .filter_by(plan_id=plan.id, status=TrancheStatus.PENDING)
            .filter(ExecutionTranche.trigger_price.isnot(None))
            .order_by(ExecutionTranche.sequence)
            .all()
        )

        if not pending_tranches:
            continue

        # 取最近 K 线高低
        hl = fetch_kline_high_low(plan.symbol, plan.market)
        if hl is None:
            logger.warning("[trigger] %s K线数据不可用，跳过", plan.symbol)
            continue

        period_high, period_low = hl
        is_buy = plan.side in ("BUY", "ADD")

        for tranche in pending_tranches:
            result["scanned"] += 1
            tp = float(tranche.trigger_price)

            # 触达判断
            triggered = False
            if is_buy and period_low <= tp:
                triggered = True  # 买入：价格跌到或低于触发价
            elif not is_buy and period_high >= tp:
                triggered = True  # 卖出：价格涨到或高于触发价

            if not triggered:
                continue

            # 纪律间隔检查
            if not _check_interval(session, plan.id, min_interval_days, now):
                result["skipped_interval"] += 1
                logger.info(
                    "[trigger] %s 批%d 到价但纪律间隔未满(%d天)，暂缓",
                    plan.symbol, tranche.sequence, min_interval_days,
                )
                continue

            # 置 armed
            try:
                validate_tranche_transition(tranche.status, TrancheStatus.ARMED)
            except ValueError as e:
                logger.warning("[trigger] 状态流转拒绝: %s", e)
                continue

            tranche.status = TrancheStatus.ARMED
            tranche.triggered_at = now

            # 在对应 strategy 上打标记（后端直接更新，前端不需要 join tranche）
            if tranche.linked_symbol_strategy_id:
                from backend.services.action.models import SymbolStrategy
                strategy = session.query(SymbolStrategy).filter_by(
                    id=tranche.linked_symbol_strategy_id
                ).first()
                if strategy:
                    strategy.decision_basis = (
                        (strategy.decision_basis or "") +
                        f"\n[已到价] 批{tranche.sequence} 于 {now.strftime('%Y-%m-%d %H:%M')} UTC 触达触发价 {tp}"
                    ).strip()

            result["armed"] += 1
            logger.info(
                "[trigger] %s 批%d armed: trigger=%.2f, period_low=%.2f, period_high=%.2f",
                plan.symbol, tranche.sequence, tp, period_low, period_high,
            )

    session.flush()
    _last_scan_time = now
    return result


def _check_interval(
    session: Session, plan_id: str, min_interval_days: int, now: datetime,
) -> bool:
    """检查同 plan 的上一批成交距今是否满足纪律间隔。"""
    from backend.services.action.models import SymbolStrategy, OrderRecord

    # 查同 plan 的所有 strategy id
    strategy_ids = [
        s.id for s in
        session.query(SymbolStrategy.id).filter_by(plan_id=plan_id).all()
    ]
    if not strategy_ids:
        return True  # 无策略，无约束

    # 查最近成交时间
    from sqlalchemy import func
    last_filled = session.query(func.max(OrderRecord.filled_at)).filter(
        OrderRecord.strategy_id.in_(strategy_ids),
        OrderRecord.status == "filled",
    ).scalar()

    if last_filled is None:
        return True  # 从未成交，无间隔约束

    # 判断间隔（兼容 naive datetime — SQLite 不存 tz）
    if last_filled.tzinfo is None:
        last_filled = last_filled.replace(tzinfo=timezone.utc)
    now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    elapsed = now_aware - last_filled
    return elapsed >= timedelta(days=min_interval_days)


def _fetch_kline_high_low_default(symbol: str, market: str) -> tuple[float, float] | None:
    """从富途取最近 15 分钟 K 线的 high/low。降级用 snapshot 日内高低。"""
    try:
        from services.market_data.futu_quote_service import _to_futu_code
        from futu import OpenQuoteContext, KLType

        futu_code = _to_futu_code(symbol)
        ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
        try:
            ret, data = ctx.get_cur_kline(futu_code, 1, KLType.K_15M)
            if ret == 0 and data is not None and len(data) > 0:
                row = data.iloc[-1]
                return float(row["high"]), float(row["low"])

            # 降级：用 snapshot 日内高低
            ret, snap = ctx.get_market_snapshot([futu_code])
            if ret == 0 and snap is not None and len(snap) > 0:
                row = snap.iloc[0]
                h = row.get("high_price")
                l = row.get("low_price")
                if h is not None and l is not None:
                    return float(h), float(l)
        finally:
            ctx.close()
    except Exception as e:
        logger.warning("[trigger] K线获取失败 %s: %s", symbol, e)

    return None


def get_last_scan_time() -> datetime | None:
    return _last_scan_time
