"""
MockBrokerAdapter 单元测试 — 覆盖各异常场景。

运行: cd ~/Documents/GitHub/WealthPilot && python -m pytest backend/services/action/tests/test_broker_mock.py -v
"""
import time
from decimal import Decimal

import pytest

from backend.services.action.brokers.mock import MockBrokerAdapter
from backend.services.action.brokers.base import OrderRequest


def _make_request(**kwargs) -> OrderRequest:
    defaults = {
        "symbol": "US.LI",
        "side": "BUY",
        "quantity": 100,
        "order_type": "LIMIT",
        "limit_price": Decimal("18.50"),
        "local_order_id": "test-order-1",
    }
    defaults.update(kwargs)
    return OrderRequest(**defaults)


class TestMockPlaceOrder:
    """place_order 正常 + 异常场景。"""

    def test_normal_submit(self):
        adapter = MockBrokerAdapter(
            fill_delay_seconds=0.01,
            rejection_probability=0,
            network_failure_probability=0,
        )
        try:
            req = _make_request()
            update = adapter.place_order(req)
            # Mock 同步推进到 broker_pending（真实券商也是提交后立即 ack）
            assert update.status == "broker_pending"
            assert update.broker_order_id.startswith("MOCK-")
            assert update.local_order_id == "test-order-1"
        finally:
            adapter.shutdown()

    def test_rejection(self):
        adapter = MockBrokerAdapter(
            fill_delay_seconds=0.01,
            rejection_probability=1.0,  # 100% 拒单
            network_failure_probability=0,
        )
        try:
            req = _make_request()
            update = adapter.place_order(req)
            assert update.status == "rejected"
        finally:
            adapter.shutdown()

    def test_network_failure(self):
        adapter = MockBrokerAdapter(
            fill_delay_seconds=0.01,
            rejection_probability=0,
            network_failure_probability=1.0,  # 100% 网络异常
        )
        try:
            req = _make_request()
            with pytest.raises(ConnectionError):
                adapter.place_order(req)
        finally:
            adapter.shutdown()


class TestMockAsyncFill:
    """异步成交流程。"""

    def test_full_fill(self):
        adapter = MockBrokerAdapter(
            fill_delay_seconds=0.01,
            partial_fill_probability=0,  # 0% 部分成交 → 100% 全成交
            rejection_probability=0,
            network_failure_probability=0,
        )
        try:
            req = _make_request(quantity=50)
            update = adapter.place_order(req)
            broker_id = update.broker_order_id

            # 等待异步成交
            time.sleep(0.1)

            status = adapter.get_order_status(broker_id)
            assert status.status == "filled"
            assert status.filled_quantity == 50
        finally:
            adapter.shutdown()

    def test_partial_fill_then_complete(self):
        adapter = MockBrokerAdapter(
            fill_delay_seconds=0.01,
            partial_fill_probability=1.0,  # 100% 部分成交
            rejection_probability=0,
            network_failure_probability=0,
        )
        try:
            req = _make_request(quantity=100)
            update = adapter.place_order(req)
            broker_id = update.broker_order_id

            # 等待部分成交 + 后续处理
            time.sleep(0.15)

            status = adapter.get_order_status(broker_id)
            # 应该是 filled 或 cancelled（部分成交后 80%/20%）
            assert status.status in ("filled", "cancelled")
            if status.status == "filled":
                assert status.filled_quantity == 100
            else:
                # cancelled 时 filled_quantity 是部分成交数量
                assert 0 < status.filled_quantity < 100
        finally:
            adapter.shutdown()


class TestMockCancelOrder:
    """cancel_order 场景。"""

    def test_cancel_pending(self):
        adapter = MockBrokerAdapter(
            fill_delay_seconds=10,  # 慢成交，有时间取消
            rejection_probability=0,
            network_failure_probability=0,
        )
        try:
            req = _make_request()
            update = adapter.place_order(req)
            broker_id = update.broker_order_id

            time.sleep(0.05)  # 等 pending

            result = adapter.cancel_order(broker_id)
            assert result is True

            status = adapter.get_order_status(broker_id)
            assert status.status == "cancelled"
        finally:
            adapter.shutdown()

    def test_cancel_already_filled(self):
        adapter = MockBrokerAdapter(
            fill_delay_seconds=0.01,
            partial_fill_probability=0,
            rejection_probability=0,
            network_failure_probability=0,
        )
        try:
            req = _make_request()
            update = adapter.place_order(req)
            broker_id = update.broker_order_id

            time.sleep(0.1)  # 等已成交

            result = adapter.cancel_order(broker_id)
            assert result is False  # 已成交不能取消
        finally:
            adapter.shutdown()

    def test_cancel_nonexistent(self):
        adapter = MockBrokerAdapter()
        try:
            result = adapter.cancel_order("MOCK-nonexistent")
            assert result is False
        finally:
            adapter.shutdown()


class TestMockGetOrderStatus:
    """get_order_status 场景。"""

    def test_status_network_failure(self):
        adapter = MockBrokerAdapter(
            fill_delay_seconds=0.01,
            rejection_probability=0,
            network_failure_probability=1.0,  # 查询时 100% 网络异常
        )
        try:
            # 先正常下单（下单时不异常）
            adapter.network_failure_probability = 0
            req = _make_request()
            update = adapter.place_order(req)
            broker_id = update.broker_order_id

            # 查询时恢复网络异常
            adapter.network_failure_probability = 1.0
            with pytest.raises(ConnectionError):
                adapter.get_order_status(broker_id)
        finally:
            adapter.shutdown()

    def test_status_nonexistent(self):
        adapter = MockBrokerAdapter(network_failure_probability=0)
        try:
            with pytest.raises(ValueError):
                adapter.get_order_status("MOCK-bogus")
        finally:
            adapter.shutdown()


class TestMockShutdown:
    """shutdown 取消所有 Timer。"""

    def test_shutdown_cancels_timers(self):
        adapter = MockBrokerAdapter(
            fill_delay_seconds=100,  # 很长的延迟
            rejection_probability=0,
            network_failure_probability=0,
        )
        req = _make_request()
        adapter.place_order(req)

        assert len(adapter._timers) > 0
        adapter.shutdown()
        assert len(adapter._timers) == 0


class TestMockListAndAccount:
    """list_open_orders / get_positions / get_account_info。"""

    def test_list_open_orders(self):
        adapter = MockBrokerAdapter(
            fill_delay_seconds=100,
            rejection_probability=0,
            network_failure_probability=0,
        )
        try:
            req = _make_request()
            adapter.place_order(req)
            time.sleep(0.05)

            open_orders = adapter.list_open_orders()
            assert len(open_orders) >= 1
        finally:
            adapter.shutdown()

    def test_get_account_info(self):
        adapter = MockBrokerAdapter()
        info = adapter.get_account_info()
        assert info["broker"] == "mock"
        assert info["cash_available"] > 0

    def test_get_positions(self):
        adapter = MockBrokerAdapter()
        positions = adapter.get_positions()
        assert positions == []

    def test_authenticate(self):
        adapter = MockBrokerAdapter()
        assert adapter.authenticate({}) is True

    def test_broker_name(self):
        adapter = MockBrokerAdapter()
        assert adapter.broker_name == "mock"
