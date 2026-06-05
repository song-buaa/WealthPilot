"""
IBKRBrokerAdapter 单元测试 — M1 骨架验证。

用 mock 替代 ib_async.IB 连接，不依赖真实 Gateway。
覆盖: 四闸门、place_order + orderRef、状态映射、异常透传、cancel 反查。

运行: cd ~/Documents/GitHub/WealthPilot && python -m pytest backend/services/action/tests/test_ibkr_adapter.py -v
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch, PropertyMock
import types

import pytest

from backend.services.action.brokers.base import OrderRequest, OrderStatusUpdate
from backend.services.action.brokers.ibkr import (
    IBKRBrokerAdapter,
    IB_TO_V32_STATUS,
    SUPPORTED_MARKETS,
)


# ── Mock IB 对象 ──────────────────────────────────────────────


def _make_mock_order_status(status="Submitted", filled=0, avg_price=0.0):
    os = MagicMock()
    os.status = status
    os.filled = filled
    os.avgFillPrice = avg_price
    os.remaining = 0
    return os


def _make_mock_trade(order_id=1, status="Submitted", filled=0, avg_price=0.0,
                     perm_id=0, order_ref=""):
    trade = MagicMock()
    trade.order = MagicMock()
    trade.order.orderId = order_id
    trade.order.permId = perm_id
    trade.order.orderRef = order_ref
    trade.orderStatus = _make_mock_order_status(status, filled, avg_price)
    return trade


def _make_adapter_with_mock_ib(**kwargs):
    """创建一个绕过真实连接的 IBKRBrokerAdapter。"""
    adapter = IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
    adapter._host = "127.0.0.1"
    adapter._port = 4002
    adapter._client_id = 1
    adapter._account_id = kwargs.get("account_id", "DU1234567")
    adapter._timeout = 5.0
    adapter._connected = True
    adapter._loop = MagicMock()
    adapter._thread = MagicMock()
    adapter._ib = MagicMock()
    return adapter


def _make_request(**overrides):
    defaults = dict(
        symbol="AAPL:US",
        side="BUY",
        quantity=100,
        order_type="LIMIT",
        limit_price=Decimal("150.00"),
        local_order_id="test-order-001",
    )
    defaults.update(overrides)
    return OrderRequest(**defaults)


# ═══════════════════════════════════════════════════════════════════
# 闸门测试
# ═══════════════════════════════════════════════════════════════════


class TestGate1PaperOnly:

    def test_paper_account_allowed(self):
        """DU 开头的模拟盘账号被接受。"""
        adapter = IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
        # 不应抛异常
        IBKRBrokerAdapter.__init__(adapter, account_id="DU1234567")

    @patch.dict("os.environ", {"ENABLE_IBKR_LIVE_TRADING": "false"})
    def test_live_account_rejected(self):
        """非 DU 开头 + 实盘未开启 → 拒绝。"""
        with pytest.raises(AssertionError, match="实盘交易未开启"):
            IBKRBrokerAdapter(account_id="U1234567")

    @patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", True)
    def test_live_account_allowed_when_enabled(self):
        """实盘开启时非 DU 账号被接受。"""
        adapter = IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
        IBKRBrokerAdapter.__init__(adapter, account_id="U1234567")
        # 不应抛异常


class TestGate2MarketWhitelist:

    def test_us_market_allowed(self):
        adapter = _make_adapter_with_mock_ib()
        adapter._ib.placeOrder.return_value = _make_mock_trade(order_id=42)
        req = _make_request(symbol="AAPL:US")
        result = adapter.place_order(req)
        assert result.status == "submitted_to_broker"

    def test_hk_market_allowed(self):
        adapter = _make_adapter_with_mock_ib()
        adapter._ib.placeOrder.return_value = _make_mock_trade(order_id=43)
        req = _make_request(symbol="0700:HK")
        result = adapter.place_order(req)
        assert result.status == "submitted_to_broker"

    def test_cn_market_rejected(self):
        adapter = _make_adapter_with_mock_ib()
        req = _make_request(symbol="600519:SH")
        result = adapter.place_order(req)
        assert result.status == "rejected"
        assert "不支持市场" in result.raw_response.get("reason", "")


class TestGate3OrderType:

    def test_limit_allowed(self):
        adapter = _make_adapter_with_mock_ib()
        adapter._ib.placeOrder.return_value = _make_mock_trade(order_id=44)
        req = _make_request(order_type="LIMIT")
        result = adapter.place_order(req)
        assert result.status == "submitted_to_broker"

    def test_conditional_limit_rejected(self):
        adapter = _make_adapter_with_mock_ib()
        req = _make_request(order_type="CONDITIONAL_LIMIT")
        result = adapter.place_order(req)
        assert result.status == "rejected"
        assert "条件限价单" in result.raw_response.get("reason", "")

    def test_market_order_rejected(self):
        adapter = _make_adapter_with_mock_ib()
        req = _make_request(order_type="MARKET")
        result = adapter.place_order(req)
        assert result.status == "rejected"
        assert "仅支持 LIMIT" in result.raw_response.get("reason", "")


class TestGate4OutsideRth:

    def test_outside_rth_false(self):
        """place_order 构造的 LimitOrder.outsideRth 必须为 False。"""
        adapter = _make_adapter_with_mock_ib()
        adapter._ib.placeOrder.return_value = _make_mock_trade(order_id=45)
        req = _make_request()
        adapter.place_order(req)
        # 检查传给 placeOrder 的 order 参数
        call_args = adapter._ib.placeOrder.call_args
        order_arg = call_args[1].get("order") or call_args[0][1]
        assert order_arg.outsideRth is False


# ═══════════════════════════════════════════════════════════════════
# place_order 核心逻辑
# ═══════════════════════════════════════════════════════════════════


class TestPlaceOrder:

    def test_order_ref_set(self):
        """orderRef 写入 local_order_id。"""
        adapter = _make_adapter_with_mock_ib()
        adapter._ib.placeOrder.return_value = _make_mock_trade(order_id=46)
        req = _make_request(local_order_id="my-wp-id-123")
        adapter.place_order(req)
        call_args = adapter._ib.placeOrder.call_args
        order_arg = call_args[1].get("order") or call_args[0][1]
        assert order_arg.orderRef == "my-wp-id-123"

    def test_broker_order_id_returned(self):
        adapter = _make_adapter_with_mock_ib()
        adapter._ib.placeOrder.return_value = _make_mock_trade(order_id=99)
        req = _make_request()
        result = adapter.place_order(req)
        assert result.broker_order_id == "99"

    def test_connection_error_transparent(self):
        """ConnectionError 透传给上层。"""
        adapter = _make_adapter_with_mock_ib()
        adapter._ib.placeOrder.side_effect = ConnectionError("disconnected")
        req = _make_request()
        with pytest.raises(ConnectionError):
            adapter.place_order(req)

    def test_timeout_error_transparent(self):
        """TimeoutError 透传给上层。"""
        adapter = _make_adapter_with_mock_ib()
        adapter._ib.placeOrder.side_effect = TimeoutError("timed out")
        req = _make_request()
        with pytest.raises(TimeoutError):
            adapter.place_order(req)

    def test_generic_exception_rejected(self):
        """其他异常 → rejected。"""
        adapter = _make_adapter_with_mock_ib()
        adapter._ib.placeOrder.side_effect = RuntimeError("IB internal error")
        req = _make_request()
        result = adapter.place_order(req)
        assert result.status == "rejected"


# ═══════════════════════════════════════════════════════════════════
# 状态映射
# ═══════════════════════════════════════════════════════════════════


class TestStatusMapping:

    @pytest.mark.parametrize("ib_status,expected", [
        ("ApiPending", "submitted_to_broker"),
        ("PendingSubmit", "submitted_to_broker"),
        ("PreSubmitted", "broker_pending"),
        ("Submitted", "broker_pending"),
        ("PendingCancel", "broker_pending"),  # 保守: 不提前判 cancelled
        ("Filled", "filled"),
        ("Cancelled", "cancelled"),
        ("ApiCancelled", "cancelled"),
        ("Inactive", "unknown"),  # 保守: 不强判终态
    ])
    def test_status_mapping(self, ib_status, expected):
        adapter = _make_adapter_with_mock_ib()
        trade = _make_mock_trade(order_id=1, status=ib_status, filled=0)
        adapter._ib.trades.return_value = [trade]
        result = adapter.get_order_status("1")
        assert result.status == expected

    def test_unknown_ib_status_maps_to_unknown(self):
        """未知的 IB 状态字符串 → unknown。"""
        adapter = _make_adapter_with_mock_ib()
        trade = _make_mock_trade(order_id=1, status="SomeFutureStatus")
        adapter._ib.trades.return_value = [trade]
        result = adapter.get_order_status("1")
        assert result.status == "unknown"

    def test_order_not_found_maps_to_unknown(self):
        """查不到订单 → unknown。"""
        adapter = _make_adapter_with_mock_ib()
        adapter._ib.trades.return_value = []
        result = adapter.get_order_status("999")
        assert result.status == "unknown"

    def test_filled_quantity_returned(self):
        adapter = _make_adapter_with_mock_ib()
        trade = _make_mock_trade(order_id=1, status="Filled", filled=100, avg_price=151.5)
        adapter._ib.trades.return_value = [trade]
        result = adapter.get_order_status("1")
        assert result.filled_quantity == 100
        assert result.avg_filled_price == Decimal("151.5")


# ═══════════════════════════════════════════════════════════════════
# cancel_order
# ═══════════════════════════════════════════════════════════════════


class TestCancelOrder:

    def test_cancel_found_and_active(self):
        adapter = _make_adapter_with_mock_ib()
        trade = _make_mock_trade(order_id=1, status="Submitted")
        adapter._ib.trades.return_value = [trade]
        result = adapter.cancel_order("1")
        assert result is True
        adapter._ib.cancelOrder.assert_called_once()

    def test_cancel_not_found(self):
        adapter = _make_adapter_with_mock_ib()
        adapter._ib.trades.return_value = []
        result = adapter.cancel_order("999")
        assert result is False

    def test_cancel_already_filled(self):
        adapter = _make_adapter_with_mock_ib()
        trade = _make_mock_trade(order_id=1, status="Filled")
        adapter._ib.trades.return_value = [trade]
        result = adapter.cancel_order("1")
        assert result is False


# ═══════════════════════════════════════════════════════════════════
# broker_name + symbol parsing
# ═══════════════════════════════════════════════════════════════════


class TestMisc:

    def test_broker_name(self):
        adapter = _make_adapter_with_mock_ib()
        assert adapter.broker_name == "ibkr"

    def test_parse_symbol_us(self):
        m, s = IBKRBrokerAdapter._parse_symbol("AAPL:US")
        assert m == "US" and s == "AAPL"

    def test_parse_symbol_hk(self):
        m, s = IBKRBrokerAdapter._parse_symbol("0700:HK")
        assert m == "HK" and s == "0700"

    def test_parse_symbol_cn(self):
        m, s = IBKRBrokerAdapter._parse_symbol("600519:SH")
        assert m == "SH" and s == "600519"
