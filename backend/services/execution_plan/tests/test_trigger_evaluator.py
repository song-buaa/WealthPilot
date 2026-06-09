"""
触发评估循环测试 — M6 核心逻辑验证。

覆盖:
  a. 价格触达 → armed
  b. 未触达 → 保持 pending
  c. 触达但纪律间隔未满 → 不 armed
  d. 非交易时段 → skip

运行: python -m pytest backend/services/execution_plan/tests/test_trigger_evaluator.py -v
"""
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
# Import action models so their tables are created by Base.metadata.create_all
import backend.services.action.models  # noqa: F401
from backend.services.execution_plan.models import ExecutionPlan, ExecutionTranche
from backend.services.execution_plan.state_machine import TrancheStatus, PlanStatus
from backend.services.execution_plan.trigger_evaluator import evaluate_triggers, backfill_missed_triggers
from backend.services.execution_plan.market_hours import is_market_open


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_plan(session, symbol="LI:US", market="US", side="BUY",
               trigger_prices=(13.0, 12.0, 11.0)):
    """创建一个 active 3 批计划。"""
    plan = ExecutionPlan(
        symbol=symbol, market=market, side=side,
        plan_status=PlanStatus.ACTIVE,
    )
    session.add(plan)
    session.flush()

    for i, tp in enumerate(trigger_prices, 1):
        session.add(ExecutionTranche(
            plan_id=plan.id, sequence=i, quantity=100,
            trigger_type="PRICE_BELOW" if side in ("BUY", "ADD") else "PRICE_ABOVE",
            trigger_price=tp, limit_price=tp + 0.02,
            order_type="LIMIT", status=TrancheStatus.PENDING,
        ))
    session.flush()
    return plan


# ═══════════════════════════════════════════════════════════════════
# 交易时段判断
# ═══════════════════════════════════════════════════════════════════

class TestMarketHours:

    def test_us_summer_open(self):
        # 2026-06-09 14:00 UTC = 夏令时 10:00 EDT = 开盘中
        dt = datetime(2026, 6, 9, 14, 0, tzinfo=timezone.utc)
        assert is_market_open("US", dt) is True

    def test_us_summer_closed(self):
        # 2026-06-09 22:00 UTC = 夏令时 18:00 EDT = 已收盘
        dt = datetime(2026, 6, 9, 22, 0, tzinfo=timezone.utc)
        assert is_market_open("US", dt) is False

    def test_us_weekend(self):
        # 2026-06-07 = 周日
        dt = datetime(2026, 6, 7, 15, 0, tzinfo=timezone.utc)
        assert is_market_open("US", dt) is False

    def test_hk_morning(self):
        # 2026-06-09 02:00 UTC = HKT 10:00 = 港股上午开盘中
        dt = datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc)
        assert is_market_open("HK", dt) is True

    def test_hk_lunch_break(self):
        # 2026-06-09 04:30 UTC = HKT 12:30 = 港股午休
        dt = datetime(2026, 6, 9, 4, 30, tzinfo=timezone.utc)
        assert is_market_open("HK", dt) is False


# ═══════════════════════════════════════════════════════════════════
# 触发评估核心
# ═══════════════════════════════════════════════════════════════════

class TestTriggerEvaluation:

    def _make_fetcher(self, high, low):
        """造一个返回固定高低的 mock fetcher。"""
        def fetch(symbol, market):
            return (high, low)
        return fetch

    def test_a_price_triggers_armed(self, db_session):
        """(a) 价格触达 → 该批被置 armed。"""
        plan = _make_plan(db_session, trigger_prices=(13.0, 12.0, 11.0))
        # 周期低点 12.5 → 触达 batch1(trigger=13.0) 但不触达 batch2(12.0)
        # 周期低点 12.5 <= 13.0 ✓
        # 但 12.5 > 12.0 ✗
        fetcher = self._make_fetcher(high=14.0, low=12.5)
        # 用美股夏令时盘中时间
        now = datetime(2026, 6, 9, 15, 0, tzinfo=timezone.utc)

        result = evaluate_triggers(db_session, fetch_kline_high_low=fetcher, now=now)

        assert result["armed"] == 1  # batch1 armed
        tranches = db_session.query(ExecutionTranche).filter_by(
            plan_id=plan.id
        ).order_by(ExecutionTranche.sequence).all()
        assert tranches[0].status == TrancheStatus.ARMED
        assert tranches[1].status == TrancheStatus.PENDING  # 未触达
        assert tranches[2].status == TrancheStatus.PENDING

    def test_b_no_trigger_stays_pending(self, db_session):
        """(b) 未触达 → 保持 pending。"""
        plan = _make_plan(db_session, trigger_prices=(10.0, 9.0, 8.0))
        # 周期低点 12.0 > 所有 trigger_price → 不触达
        fetcher = self._make_fetcher(high=14.0, low=12.0)
        now = datetime(2026, 6, 9, 15, 0, tzinfo=timezone.utc)

        result = evaluate_triggers(db_session, fetch_kline_high_low=fetcher, now=now)

        assert result["armed"] == 0
        tranches = db_session.query(ExecutionTranche).filter_by(plan_id=plan.id).all()
        assert all(t.status == TrancheStatus.PENDING for t in tranches)

    def test_c_interval_not_met(self, db_session):
        """(c) 触达但纪律间隔未满 → 不 armed。"""
        plan = _make_plan(db_session, trigger_prices=(13.0, 12.0, 11.0))
        now = datetime(2026, 6, 9, 15, 0, tzinfo=timezone.utc)

        # 模拟: batch1 已有关联 strategy + 成交记录(刚成交 2 小时前)
        from backend.services.action.models import SymbolStrategy, OrderRecord
        strategy = SymbolStrategy(
            symbol="LI:US", side="BUY", target_quantity=100,
            order_type="LIMIT", status="active", plan_id=plan.id,
        )
        db_session.add(strategy)
        db_session.flush()

        order = OrderRecord(
            strategy_id=strategy.id, broker_name="ibkr",
            symbol="LI:US", side="BUY", quantity=100,
            status="filled",
            filled_at=now - timedelta(hours=2),  # 2 小时前成交
        )
        db_session.add(order)
        db_session.flush()

        # 低点 12.5 触达 batch1(13.0)
        fetcher = self._make_fetcher(high=14.0, low=12.5)
        result = evaluate_triggers(db_session, fetch_kline_high_low=fetcher, now=now)

        assert result["skipped_interval"] >= 1
        assert result["armed"] == 0

    def test_d_non_trading_hours_skip(self, db_session):
        """(d) 非交易时段 → 整个计划 skip。"""
        plan = _make_plan(db_session, trigger_prices=(13.0,))
        fetcher = self._make_fetcher(high=14.0, low=10.0)  # 一定触达
        # 周日
        now = datetime(2026, 6, 7, 15, 0, tzinfo=timezone.utc)

        result = evaluate_triggers(db_session, fetch_kline_high_low=fetcher, now=now)

        assert result["skipped_market"] >= 1
        assert result["armed"] == 0

    def test_sell_trigger_high(self, db_session):
        """卖出批: high >= trigger_price 触达。"""
        plan = _make_plan(db_session, symbol="LI:US", side="REDUCE",
                          trigger_prices=(15.0, 16.0, 17.0))
        # 周期高点 15.5 >= 15.0 ✓, < 16.0 ✗
        fetcher = self._make_fetcher(high=15.5, low=14.0)
        now = datetime(2026, 6, 9, 15, 0, tzinfo=timezone.utc)

        result = evaluate_triggers(db_session, fetch_kline_high_low=fetcher, now=now)

        assert result["armed"] == 1
        t = db_session.query(ExecutionTranche).filter_by(
            plan_id=plan.id, sequence=1
        ).first()
        assert t.status == TrancheStatus.ARMED

    def test_multiple_batches_armed(self, db_session):
        """多批同时触达 → 全部 armed(纪律间隔允许时)。"""
        plan = _make_plan(db_session, trigger_prices=(13.0, 12.0, 11.0))
        # 低点 10.0 触达所有 3 批
        fetcher = self._make_fetcher(high=14.0, low=10.0)
        now = datetime(2026, 6, 9, 15, 0, tzinfo=timezone.utc)

        result = evaluate_triggers(db_session, fetch_kline_high_low=fetcher, now=now)

        assert result["armed"] == 3

    def test_armed_at_set_on_strategy(self, db_session):
        """armed 时 strategy.armed_at 被设置(结构化标记)。"""
        plan = _make_plan(db_session, trigger_prices=(13.0,))
        # 关联 strategy
        from backend.services.action.models import SymbolStrategy
        strategy = SymbolStrategy(
            symbol="LI:US", side="BUY", target_quantity=100,
            order_type="LIMIT", status="active", plan_id=plan.id,
        )
        db_session.add(strategy)
        db_session.flush()
        tranche = db_session.query(ExecutionTranche).filter_by(plan_id=plan.id).first()
        tranche.linked_symbol_strategy_id = strategy.id
        db_session.flush()

        fetcher = self._make_fetcher(high=14.0, low=12.0)
        now = datetime(2026, 6, 9, 15, 0, tzinfo=timezone.utc)
        evaluate_triggers(db_session, fetch_kline_high_low=fetcher, now=now)

        db_session.refresh(strategy)
        assert strategy.armed_at is not None
        assert strategy.interval_blocked is None


# ═══════════════════════════════════════════════════════════════════
# 补扫
# ═══════════════════════════════════════════════════════════════════

class TestBackfill:

    def test_a_backfill_armed(self, db_session):
        """(a) 历史K线里有触达 → 补扫后 armed。"""
        plan = _make_plan(db_session, trigger_prices=(13.0, 12.0))
        since = datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 6, 9, 15, 0, tzinfo=timezone.utc)

        # 历史 K 线: 一根 low=12.5 触达 batch1(13.0), 不触达 batch2(12.0)
        def fetcher(symbol, market, s, e):
            return [(14.0, 12.5), (13.8, 13.0)]  # 两根 K 线

        result = backfill_missed_triggers(db_session, since, now, fetch_history_klines=fetcher)

        assert result["armed"] == 1
        t1 = db_session.query(ExecutionTranche).filter_by(plan_id=plan.id, sequence=1).first()
        t2 = db_session.query(ExecutionTranche).filter_by(plan_id=plan.id, sequence=2).first()
        assert t1.status == TrancheStatus.ARMED
        assert t2.status == TrancheStatus.PENDING

    def test_b_fetch_failure_skip(self, db_session):
        """(b) 历史K线取不到 → 保守跳过、不误触发。"""
        plan = _make_plan(db_session, trigger_prices=(13.0,))
        since = datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 6, 9, 15, 0, tzinfo=timezone.utc)

        def fetcher(symbol, market, s, e):
            return None  # 接口失败

        result = backfill_missed_triggers(db_session, since, now, fetch_history_klines=fetcher)

        assert result["failed_fetch"] >= 1
        assert result["armed"] == 0
        t = db_session.query(ExecutionTranche).filter_by(plan_id=plan.id).first()
        assert t.status == TrancheStatus.PENDING

    def test_backfill_no_trigger(self, db_session):
        """历史K线里没有触达 → 保持 pending。"""
        plan = _make_plan(db_session, trigger_prices=(10.0,))
        since = datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 6, 9, 15, 0, tzinfo=timezone.utc)

        def fetcher(symbol, market, s, e):
            return [(14.0, 12.0), (13.5, 11.0)]  # 都没到 10.0

        result = backfill_missed_triggers(db_session, since, now, fetch_history_klines=fetcher)

        assert result["armed"] == 0
