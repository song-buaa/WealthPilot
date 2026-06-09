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
            interval_ok = _check_interval(session, plan.id, min_interval_days, now)

            if tranche.linked_symbol_strategy_id:
                from backend.services.action.models import SymbolStrategy
                strategy = session.query(SymbolStrategy).filter_by(
                    id=tranche.linked_symbol_strategy_id
                ).first()
            else:
                strategy = None

            if not interval_ok:
                result["skipped_interval"] += 1
                # 结构化标记纪律暂缓
                if strategy:
                    strategy.interval_blocked = f"价格已到，距上批成交不足{min_interval_days}天，暂缓"
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

            # 结构化标记 armed（前端判断 armed_at != null）
            if strategy:
                strategy.armed_at = now
                strategy.interval_blocked = None  # 清除之前的暂缓标记

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


# ══════════════════════════════════════════════════════════════════
# 补扫（backfill）— 用历史 K 线补判遗漏
# ══════════════════════════════════════════════════════════════════


def backfill_missed_triggers(
    session: Session,
    since: datetime,
    now: datetime | None = None,
    fetch_history_klines=None,
) -> dict:
    """补扫：拉 since→now 的历史 15 分钟 K 线，判断是否有遗漏的触达。

    不依赖 APScheduler misfire，显式拉历史数据。
    **绝不自动下单。**

    Args:
        session: DB session
        since: 上次扫描时间
        now: 当前时间
        fetch_history_klines: 可注入。签名 (symbol, market, since, now) → list[(high, low)]
    """
    global _last_scan_time
    if now is None:
        now = datetime.now(timezone.utc)

    if fetch_history_klines is None:
        fetch_history_klines = _fetch_history_klines_default

    from app.discipline.config import get_rules
    rules = get_rules()
    min_interval_days = rules.get("position_sizing", {}).get(
        "min_interval_between_adds_days", 1
    )

    result = {"scanned": 0, "armed": 0, "skipped_interval": 0, "failed_fetch": 0}

    plans = session.query(ExecutionPlan).filter_by(plan_status=PlanStatus.ACTIVE).all()

    for plan in plans:
        pending_tranches = (
            session.query(ExecutionTranche)
            .filter_by(plan_id=plan.id, status=TrancheStatus.PENDING)
            .filter(ExecutionTranche.trigger_price.isnot(None))
            .order_by(ExecutionTranche.sequence)
            .all()
        )
        if not pending_tranches:
            continue

        # 拉历史 K 线
        bars = fetch_history_klines(plan.symbol, plan.market, since, now)
        if bars is None:
            result["failed_fetch"] += 1
            logger.warning("[backfill] %s 历史K线取不到，保守跳过", plan.symbol)
            continue

        is_buy = plan.side in ("BUY", "ADD")

        for tranche in pending_tranches:
            result["scanned"] += 1
            tp = float(tranche.trigger_price)

            # 遍历每根 K 线判断
            triggered = False
            for bar_high, bar_low in bars:
                if is_buy and bar_low <= tp:
                    triggered = True
                    break
                elif not is_buy and bar_high >= tp:
                    triggered = True
                    break

            if not triggered:
                continue

            # 纪律间隔
            interval_ok = _check_interval(session, plan.id, min_interval_days, now)

            from backend.services.action.models import SymbolStrategy
            strategy = None
            if tranche.linked_symbol_strategy_id:
                strategy = session.query(SymbolStrategy).filter_by(
                    id=tranche.linked_symbol_strategy_id
                ).first()

            if not interval_ok:
                result["skipped_interval"] += 1
                if strategy:
                    strategy.interval_blocked = f"补扫发现到价，但距上批成交不足{min_interval_days}天"
                continue

            try:
                validate_tranche_transition(tranche.status, TrancheStatus.ARMED)
            except ValueError:
                continue

            tranche.status = TrancheStatus.ARMED
            tranche.triggered_at = now

            if strategy:
                strategy.armed_at = now
                strategy.interval_blocked = None

            result["armed"] += 1
            logger.info("[backfill] %s 批%d 补扫 armed", plan.symbol, tranche.sequence)

    session.flush()
    _last_scan_time = now
    return result


def _fetch_history_klines_default(
    symbol: str, market: str, since: datetime, until: datetime,
) -> list[tuple[float, float]] | None:
    """用 Tiger 历史 15 分钟 K 线。返回 [(high, low), ...]。"""
    try:
        import os
        from pathlib import Path
        from tigeropen.common.consts import Language, BarPeriod
        from tigeropen.quote.quote_client import QuoteClient
        from tigeropen.tiger_open_config import TigerOpenClientConfig
        from tigeropen.common.util.signature_utils import read_private_key
        from utils.symbol import symbol_to_tiger_ticker

        tiger_symbol = symbol_to_tiger_ticker(symbol)

        project_root = Path(__file__).parent.parent.parent.parent
        pk_path = project_root / "backend" / "secrets" / "tiger_private_key.pem"
        if not pk_path.exists():
            pk_path = Path(__file__).parent.parent.parent / "secrets" / "tiger_private_key.pem"

        config = TigerOpenClientConfig(sandbox_debug=False)
        config.tiger_id = os.environ.get("TIGER_ID")
        config.account = os.environ.get("TIGER_ACCOUNT")
        config.private_key = read_private_key(str(pk_path))
        config.language = Language.zh_CN
        client = QuoteClient(config)

        # begin_time/end_time 支持毫秒时间戳或字符串
        begin_ms = int(since.timestamp() * 1000)
        end_ms = int(until.timestamp() * 1000)

        data = client.get_bars(
            [tiger_symbol],
            period=BarPeriod.FIFTEEN_MINUTES,
            begin_time=begin_ms,
            end_time=end_ms,
            limit=100,
        )

        if data is None or len(data) == 0:
            return None

        if "symbol" in data.columns:
            data = data[data["symbol"] == tiger_symbol]

        if len(data) == 0:
            return None

        return [(float(row["high"]), float(row["low"])) for _, row in data.iterrows()]

    except Exception as e:
        logger.warning("[backfill] 历史K线获取失败 %s: %s", symbol, e)
        return None
