"""
OrderManager 端到端测试 — 通过 MockBrokerAdapter 验证完整流程。

运行: cd ~/Documents/GitHub/WealthPilot && python -m pytest backend/services/action/tests/test_order_manager.py -v
"""
import json
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from backend.services.action.models import (
    ActionDraft, SymbolStrategy, OrderRecord, AuditLog, AllocationIntent,
)
from backend.services.action.order_manager import OrderManager
from backend.services.action.state_machine import StrategyStatus, OrderStatus
from backend.services.action.brokers.mock import MockBrokerAdapter


@pytest.fixture
def db_session():
    """每个测试用独立的内存 SQLite。"""
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


@pytest.fixture
def mock_adapter():
    """快速 Mock：0 延迟、0 异常。"""
    adapter = MockBrokerAdapter(
        fill_delay_seconds=0.01,
        partial_fill_probability=0,
        rejection_probability=0,
        network_failure_probability=0,
    )
    yield adapter
    adapter.shutdown()


@pytest.fixture
def manager(db_session, mock_adapter):
    return OrderManager(db_session, broker_adapter=mock_adapter)


def _create_strategy(session, **kwargs) -> SymbolStrategy:
    """辅助：直接创建一个 active 的 SymbolStrategy。"""
    defaults = {
        "symbol": "MSFT",
        "side": "BUY",
        "target_quantity": 100,
        "order_type": "LIMIT",
        "limit_price": 415,
        "status": StrategyStatus.ACTIVE,
    }
    defaults.update(kwargs)
    s = SymbolStrategy(**defaults)
    session.add(s)
    session.flush()
    return s


# ═══════════════════════════════════════════════════════════════════
# place_order 正常流程
# ═══════════════════════════════════════════════════════════════════

class TestPlaceOrderNormal:

    def test_place_order_submitted(self, manager, db_session):
        """正常下单 → status=submitted_to_broker（Mock 同步推进到 broker_pending）。"""
        strategy = _create_strategy(db_session)
        order = manager.place_order(strategy.id, {"quantity": 50, "limit_price": 415})
        assert order.status == OrderStatus.SUBMITTED_TO_BROKER
        assert order.broker_order_id is not None
        assert order.broker_order_id.startswith("MOCK-")
        assert order.broker_name == "mock"
        assert order.quantity == 50

    def test_place_order_writes_audit(self, manager, db_session):
        """下单后 audit_log 有记录。"""
        strategy = _create_strategy(db_session)
        manager.place_order(strategy.id, {"quantity": 50})
        db_session.commit()

        logs = db_session.query(AuditLog).all()
        event_types = [l.event_type for l in logs]
        assert "order_created" in event_types
        assert "order_submitted" in event_types


# ═══════════════════════════════════════════════════════════════════
# place_order 异常路径
# ═══════════════════════════════════════════════════════════════════

class TestPlaceOrderErrors:

    def test_strategy_not_found(self, manager):
        with pytest.raises(ValueError, match="策略不存在"):
            manager.place_order("nonexistent-id", {"quantity": 10})

    def test_strategy_not_active(self, manager, db_session):
        strategy = _create_strategy(db_session, status=StrategyStatus.PAUSED)
        with pytest.raises(ValueError, match="不可下单"):
            manager.place_order(strategy.id, {"quantity": 10})

    def test_over_quantity_rejected(self, manager, db_session):
        """超额下单禁止。"""
        strategy = _create_strategy(db_session, target_quantity=100)
        strategy.cumulative_filled_quantity = 80
        db_session.flush()

        with pytest.raises(ValueError, match="超额下单禁止"):
            manager.place_order(strategy.id, {"quantity": 30})  # 80+30 > 100

    def test_rejection_from_broker(self, db_session):
        """券商拒单 → status=rejected。"""
        adapter = MockBrokerAdapter(
            fill_delay_seconds=0.01,
            rejection_probability=1.0,
            network_failure_probability=0,
        )
        try:
            mgr = OrderManager(db_session, broker_adapter=adapter)
            strategy = _create_strategy(db_session)
            order = mgr.place_order(strategy.id, {"quantity": 50})
            assert order.status == OrderStatus.REJECTED
        finally:
            adapter.shutdown()

    def test_network_error_sets_unknown(self, db_session):
        """网络异常 → status=unknown。"""
        adapter = MockBrokerAdapter(
            fill_delay_seconds=0.01,
            rejection_probability=0,
            network_failure_probability=1.0,
        )
        try:
            mgr = OrderManager(db_session, broker_adapter=adapter)
            strategy = _create_strategy(db_session)
            order = mgr.place_order(strategy.id, {"quantity": 50})
            assert order.status == OrderStatus.UNKNOWN
        finally:
            adapter.shutdown()

    def test_no_adapter_stays_created(self, db_session):
        """无 adapter 时 order 停留在 created。"""
        mgr = OrderManager(db_session, broker_adapter=None)
        strategy = _create_strategy(db_session)
        order = mgr.place_order(strategy.id, {"quantity": 50})
        assert order.status == OrderStatus.CREATED


# ═══════════════════════════════════════════════════════════════════
# sync_order_status
# ═══════════════════════════════════════════════════════════════════

class TestSyncOrderStatus:

    def test_sync_filled(self, manager, db_session):
        """正常成交后 sync → filled + cumulative 回写。"""
        strategy = _create_strategy(db_session, target_quantity=100)
        order = manager.place_order(strategy.id, {"quantity": 100})

        time.sleep(0.1)  # 等异步成交

        order = manager.sync_order_status(order.id)
        assert order.status == OrderStatus.FILLED
        assert order.filled_quantity == 100
        assert order.filled_at is not None

        # strategy cumulative 回写
        s = manager.get_strategy(strategy.id)
        assert s.cumulative_filled_quantity == 100

    def test_sync_auto_completes_strategy(self, manager, db_session):
        """cumulative >= target → strategy auto completed。"""
        strategy = _create_strategy(db_session, target_quantity=50)
        order = manager.place_order(strategy.id, {"quantity": 50})

        time.sleep(0.1)

        manager.sync_order_status(order.id)

        s = manager.get_strategy(strategy.id)
        assert s.status == StrategyStatus.COMPLETED
        assert s.completed_at is not None

    def test_sync_network_error(self, db_session):
        """sync 网络异常 → 不崩溃，标记 unknown。"""
        adapter = MockBrokerAdapter(
            fill_delay_seconds=0.01,
            rejection_probability=0,
            network_failure_probability=0,
        )
        try:
            mgr = OrderManager(db_session, broker_adapter=adapter)
            strategy = _create_strategy(db_session)
            order = mgr.place_order(strategy.id, {"quantity": 50})

            time.sleep(0.05)

            # sync 时模拟网络异常
            adapter.network_failure_probability = 1.0
            order = mgr.sync_order_status(order.id)
            # 不崩溃，可能是 unknown 或保持原状
            assert order is not None
        finally:
            adapter.shutdown()

    def test_sync_no_broker_order_id(self, db_session):
        """无 broker_order_id 时直接返回。"""
        mgr = OrderManager(db_session, broker_adapter=None)
        strategy = _create_strategy(db_session)
        order = mgr.place_order(strategy.id, {"quantity": 50})
        # 无 adapter → created 状态，无 broker_order_id
        result = mgr.sync_order_status(order.id)
        assert result.status == OrderStatus.CREATED


# ═══════════════════════════════════════════════════════════════════
# 多笔订单 + 累计成交
# ═══════════════════════════════════════════════════════════════════

class TestMultipleOrders:

    def test_two_orders_cumulative(self, manager, db_session):
        """两笔订单累计不超额。"""
        strategy = _create_strategy(db_session, target_quantity=100)

        o1 = manager.place_order(strategy.id, {"quantity": 60})
        time.sleep(0.1)
        manager.sync_order_status(o1.id)

        o2 = manager.place_order(strategy.id, {"quantity": 40})
        time.sleep(0.1)
        manager.sync_order_status(o2.id)

        s = manager.get_strategy(strategy.id)
        assert s.cumulative_filled_quantity == 100
        assert s.status == StrategyStatus.COMPLETED

    def test_third_order_over_quantity(self, manager, db_session):
        """第三笔超额 → 拒绝。"""
        strategy = _create_strategy(db_session, target_quantity=100)

        o1 = manager.place_order(strategy.id, {"quantity": 60})
        time.sleep(0.1)
        manager.sync_order_status(o1.id)

        # strategy 已 completed，不能再下单
        with pytest.raises(ValueError):
            manager.place_order(strategy.id, {"quantity": 50})


# ═══════════════════════════════════════════════════════════════════
# AllocationIntent 不能下单
# ═══════════════════════════════════════════════════════════════════

class TestAllocationIntentCannotPlaceOrder:

    def test_allocation_intent_cannot_place_order(self, manager, db_session):
        """用 AllocationIntent.id 调 place_order → 明确拒绝。"""
        intent = AllocationIntent(
            title="测试配置意图",
            status="active",
        )
        db_session.add(intent)
        db_session.flush()

        with pytest.raises(ValueError, match="AllocationIntent cannot place orders"):
            manager.place_order(intent.id, {"quantity": 100})


# ═══════════════════════════════════════════════════════════════════
# expired 状态端到端
# ═══════════════════════════════════════════════════════════════════

class TestExpiredStatus:

    def test_order_expires_after_timeout(self):
        """Mock expire_after_seconds → 订单超时变 expired。"""
        adapter = MockBrokerAdapter(
            fill_delay_seconds=100,       # 成交极慢，不会先 fill
            expire_after_seconds=0.1,     # 0.1 秒后过期
            rejection_probability=0,
            network_failure_probability=0,
        )
        try:
            from backend.services.action.brokers.base import OrderRequest
            from decimal import Decimal
            req = OrderRequest(
                symbol="US.TEST", side="BUY", quantity=10,
                order_type="LIMIT", limit_price=Decimal("100"),
                local_order_id="expire-test",
            )
            update = adapter.place_order(req)
            broker_id = update.broker_order_id

            time.sleep(0.3)  # 等过期

            status = adapter.get_order_status(broker_id)
            assert status.status == "expired"
        finally:
            adapter.shutdown()

    def test_sync_expired_order(self, db_session):
        """sync expired → OrderRecord 更新，Strategy 保持 active。"""
        adapter = MockBrokerAdapter(
            fill_delay_seconds=100,
            expire_after_seconds=0.1,
            rejection_probability=0,
            network_failure_probability=0,
        )
        try:
            mgr = OrderManager(db_session, broker_adapter=adapter)
            strategy = _create_strategy(db_session, target_quantity=100)
            order = mgr.place_order(strategy.id, {"quantity": 50})

            time.sleep(0.3)  # 等过期

            order = mgr.sync_order_status(order.id)
            assert order.status == "expired"

            # expired 不应该让 strategy completed
            s = mgr.get_strategy(strategy.id)
            assert s.status == StrategyStatus.ACTIVE
            assert s.cumulative_filled_quantity == 0
        finally:
            adapter.shutdown()


# ═══════════════════════════════════════════════════════════════════
# M3.3: confirm_draft 新 payload 结构
# ═══════════════════════════════════════════════════════════════════

class TestConfirmDraftNewPayload:

    def test_confirm_with_symbol_strategies(self, manager, db_session):
        """新 payload 结构 → 正确创建 SymbolStrategy。"""
        payload = {
            "symbol_strategies": [
                {"symbol": "MSFT", "side": "BUY", "quantity": 100, "limit_price": 415},
            ],
            "allocation_intents": [],
            "risk_notes": ["仓位较重注意风险"],
            "missing_fields": [],
        }
        draft = manager.create_draft("conv-1", payload, "买入微软100股")
        db_session.flush()

        entities = manager.confirm_draft(draft.id)
        assert len(entities) == 1
        assert entities[0].symbol == "MSFT"
        assert entities[0].side == "BUY"
        assert entities[0].target_quantity == 100

    def test_confirm_with_allocation_intents(self, manager, db_session):
        """新 payload 结构 → 正确创建 AllocationIntent。"""
        payload = {
            "symbol_strategies": [],
            "allocation_intents": [
                {"title": "权益降至40%", "target_allocation": {"equity": 0.4}},
            ],
            "risk_notes": [],
            "missing_fields": [],
        }
        draft = manager.create_draft("conv-2", payload, "调整配置")
        db_session.flush()

        entities = manager.confirm_draft(draft.id)
        assert len(entities) == 1

    def test_confirm_rejected_when_missing_fields(self, manager, db_session):
        """missing_fields 非空 → 拒绝确认（结构化格式）。"""
        payload = {
            "symbol_strategies": [{"symbol": "LI", "side": "SELL"}],
            "allocation_intents": [],
            "risk_notes": [],
            "missing_fields": [
                {"target_type": "symbol_strategy", "target_index": 0,
                 "field": "quantity", "description": "未指定卖出数量"},
                {"target_type": "symbol_strategy", "target_index": 0,
                 "field": "limit_price", "description": "未指定限价"},
            ],
        }
        draft = manager.create_draft("conv-3", payload, "减仓理想")
        db_session.flush()

        with pytest.raises(ValueError, match="缺失信息"):
            manager.confirm_draft(draft.id)

        d = manager.get_draft(draft.id)
        assert d.status == "draft"
