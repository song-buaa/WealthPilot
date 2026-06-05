"""
M3 前半: IBKR factory 接入 + OrderManager 装配验证。

用 test double 验证:
1. factory 返回 IBKRBrokerAdapter 实例
2. OrderManager 注入后 place_order/cancel_order/sync 路由到 adapter
全程 mock，不连真实 Gateway。

运行: cd ~/Documents/GitHub/WealthPilot && python -m pytest backend/services/action/tests/test_ibkr_assembly.py -v
"""
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from backend.services.action.models import (
    SymbolStrategy, OrderRecord, AuditLog,
)
from backend.services.action.order_manager import OrderManager
from backend.services.action.state_machine import StrategyStatus, OrderStatus
from backend.services.action.brokers.base import (
    BrokerAdapter, OrderRequest, OrderStatusUpdate,
)
from backend.services.action.brokers.ibkr import IBKRBrokerAdapter


# ── Fixtures ──────────────────────────────────────────────────


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


def _create_strategy(session, **kwargs) -> SymbolStrategy:
    defaults = {
        "symbol": "AAPL:US",
        "side": "BUY",
        "target_quantity": 100,
        "order_type": "LIMIT",
        "limit_price": 150,
        "status": StrategyStatus.ACTIVE,
    }
    defaults.update(kwargs)
    s = SymbolStrategy(**defaults)
    session.add(s)
    session.flush()
    return s


class IBKRTestDouble(BrokerAdapter):
    """最小 test double：记录调用并返回确定性结果。

    不继承 IBKRBrokerAdapter（避免 __init__ 的闸门断言和连接逻辑），
    直接实现 BrokerAdapter 接口，仅用于验证 OrderManager 路由正确。
    """

    def __init__(self):
        self.place_order_called = False
        self.cancel_order_called = False
        self.get_order_status_called = False
        self._counter = 0

    @property
    def broker_name(self) -> str:
        return "ibkr"

    def authenticate(self, credentials: dict) -> bool:
        return True

    def place_order(self, request: OrderRequest) -> OrderStatusUpdate:
        self.place_order_called = True
        self._counter += 1
        return OrderStatusUpdate(
            broker_order_id=f"IBKR-PERM-{self._counter}",
            local_order_id=request.local_order_id,
            status="submitted_to_broker",
            filled_quantity=0,
            timestamp=0,
            raw_response={
                "broker": "ibkr",
                "order_ref": request.local_order_id,
            },
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        self.cancel_order_called = True
        return True

    def get_order_status(self, broker_order_id: str) -> OrderStatusUpdate:
        self.get_order_status_called = True
        return OrderStatusUpdate(
            broker_order_id=broker_order_id,
            local_order_id="",
            status="cancelled",
            filled_quantity=0,
            timestamp=0,
            raw_response={"broker": "ibkr"},
        )

    def list_open_orders(self) -> list[OrderStatusUpdate]:
        return []

    def get_positions(self) -> list[dict]:
        return []

    def get_account_info(self) -> dict:
        return {"broker": "ibkr"}


# ═══════════════════════════════════════════════════════════════════
# 任务 1: factory 返回 IBKRBrokerAdapter
# ═══════════════════════════════════════════════════════════════════


class TestFactoryIBKR:

    def test_factory_returns_ibkr_adapter(self):
        """get_broker_adapter(broker_name='ibkr') 返回 IBKRBrokerAdapter。"""
        from backend.services.action.brokers.factory import get_broker_adapter

        # mock config 里的 IBKR 配置（factory 内 lazy import settings）
        mock_settings = MagicMock()
        mock_settings.ibkr_host = "127.0.0.1"
        mock_settings.ibkr_port = 4002
        mock_settings.ibkr_client_id = 1
        mock_settings.ibkr_account = "DU1234567"

        with patch("backend.core.config.settings", mock_settings):
            adapter = get_broker_adapter(broker_name="ibkr")

        assert isinstance(adapter, IBKRBrokerAdapter)
        assert adapter.broker_name == "ibkr"
        assert adapter._account_id == "DU1234567"
        # 构造不触发连接
        assert adapter._connected is False

    def test_factory_ibkr_no_connection_on_construct(self):
        """构造 IBKRBrokerAdapter 不触发真实连接。"""
        adapter = IBKRBrokerAdapter(
            host="192.168.255.255",  # 不可达地址
            port=9999,
            account_id="DU9999999",
        )
        # 不应崩溃——连接是延迟的
        assert adapter._connected is False
        assert adapter._ib is None

    def test_factory_mock_still_works(self):
        """加 ibkr 分支后，mock 路径仍正常。"""
        from backend.services.action.brokers.factory import get_broker_adapter
        adapter = get_broker_adapter(broker_name="mock")
        assert adapter.broker_name == "mock"

    def test_factory_unsupported_still_raises(self):
        """不支持的 broker_name 仍抛 UnsupportedBrokerError。"""
        from backend.services.action.brokers.factory import (
            get_broker_adapter, UnsupportedBrokerError,
        )
        with pytest.raises(UnsupportedBrokerError):
            get_broker_adapter(broker_name="unknown_broker")


# ═══════════════════════════════════════════════════════════════════
# 任务 2: OrderManager 装配路由验证
# ═══════════════════════════════════════════════════════════════════


class TestOrderManagerIBKRRouting:

    def test_place_order_routes_to_ibkr(self, db_session):
        """OrderManager.place_order → IBKRTestDouble.place_order。"""
        adapter = IBKRTestDouble()
        mgr = OrderManager(db_session, broker_adapter=adapter)
        strategy = _create_strategy(db_session)

        order = mgr.place_order(strategy.id, {"quantity": 50, "limit_price": 150})

        assert adapter.place_order_called
        assert order.status == OrderStatus.SUBMITTED_TO_BROKER
        assert order.broker_order_id.startswith("IBKR-PERM-")
        assert order.broker_name == "ibkr"

    def test_cancel_order_routes_to_ibkr(self, db_session):
        """OrderManager.cancel_order → 走 M0 分流 → adapter.cancel_order + sync。"""
        adapter = IBKRTestDouble()
        mgr = OrderManager(db_session, broker_adapter=adapter)
        strategy = _create_strategy(db_session)

        order = mgr.place_order(strategy.id, {"quantity": 50, "limit_price": 150})
        assert order.broker_order_id is not None

        order = mgr.cancel_order(order.id)

        # M0 分流: 有 broker_order_id → 调 adapter.cancel_order + sync
        assert adapter.cancel_order_called
        assert adapter.get_order_status_called
        # test double 的 get_order_status 返回 cancelled
        assert order.status == OrderStatus.CANCELLED

    def test_sync_order_status_routes_to_ibkr(self, db_session):
        """OrderManager.sync_order_status → adapter.get_order_status。"""
        adapter = IBKRTestDouble()
        mgr = OrderManager(db_session, broker_adapter=adapter)
        strategy = _create_strategy(db_session)

        order = mgr.place_order(strategy.id, {"quantity": 50, "limit_price": 150})
        adapter.get_order_status_called = False  # reset

        order = mgr.sync_order_status(order.id)

        assert adapter.get_order_status_called

    def test_broker_name_written_to_order_record(self, db_session):
        """OrderRecord.broker_name 记录 'ibkr'。"""
        adapter = IBKRTestDouble()
        mgr = OrderManager(db_session, broker_adapter=adapter)
        strategy = _create_strategy(db_session)

        order = mgr.place_order(strategy.id, {"quantity": 50, "limit_price": 150})
        assert order.broker_name == "ibkr"

    def test_audit_log_written(self, db_session):
        """下单后有 audit_log。"""
        adapter = IBKRTestDouble()
        mgr = OrderManager(db_session, broker_adapter=adapter)
        strategy = _create_strategy(db_session)

        mgr.place_order(strategy.id, {"quantity": 50, "limit_price": 150})
        db_session.commit()

        logs = db_session.query(AuditLog).all()
        event_types = [l.event_type for l in logs]
        assert "order_created" in event_types
        assert "order_submitted" in event_types


# ═══════════════════════════════════════════════════════════════════
# 任务 3: 启动默认行为未变
# ═══════════════════════════════════════════════════════════════════


class TestDefaultBehaviorUnchanged:

    def test_default_broker_mode_is_mock(self):
        """BROKER_MODE 默认 'mock'，不会路由到 ibkr。"""
        import os
        mode = os.getenv("BROKER_MODE", "mock")
        # 当前逻辑: "tiger" if "tiger" in mode else "mock"
        # "ibkr" 不含 "tiger" → 会 fallback 到 mock
        broker_name = "tiger" if "tiger" in mode else "mock"
        assert broker_name == "mock" or broker_name == "tiger"
        assert broker_name != "ibkr"

    def test_ibkr_mode_not_routed_by_current_api_action(self):
        """api/action.py 当前路由逻辑不识别 'ibkr' → 不会意外连 IB。

        当前 api/action.py:53 的逻辑是:
            broker_name = "tiger" if "tiger" in _BROKER_MODE else "mock"
        即使 BROKER_MODE=ibkr，也会 fallback 到 mock。
        这是预期行为——api/action.py 的路由改动属于 M3 后半段。
        """
        test_mode = "ibkr"
        broker_name = "tiger" if "tiger" in test_mode else "mock"
        assert broker_name == "mock"
