"""
IBKRBrokerAdapter 单元测试 — M1 + M2 验证。

用 mock 替代 ib_async.IB 连接，不依赖真实 Gateway。
覆盖: 四闸门、place_order + orderRef + permId、Inactive 三分支、
状态映射、异常透传、not_found 重试、orderRef 反查。

运行: cd ~/Documents/GitHub/WealthPilot && python -m pytest backend/services/action/tests/test_ibkr_adapter.py -v
"""
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from backend.services.action.brokers.base import OrderRequest, OrderStatusUpdate
from backend.services.action.brokers.ibkr import (
    IBKRBrokerAdapter,
    IB_TO_V32_STATUS,
    SUPPORTED_MARKETS,
    OrphanOrderError,
)


# ── Mock IB 对象 ──────────────────────────────────────────────


def _make_mock_order_status(status="Submitted", filled=0, avg_price=0.0, perm_id=0):
    os = MagicMock()
    os.status = status
    os.filled = filled
    os.avgFillPrice = avg_price
    os.permId = perm_id
    os.remaining = 0
    os.whyHeld = ""
    return os


def _make_mock_log_entry(error_code=0, message="", status=""):
    entry = MagicMock()
    entry.errorCode = error_code
    entry.message = message
    entry.status = status
    entry.time = datetime.now()
    return entry


def _make_mock_trade(order_id=1, status="Submitted", filled=0, avg_price=0.0,
                     perm_id=100, order_ref="", log=None):
    trade = MagicMock()
    trade.order = MagicMock()
    trade.order.orderId = order_id
    trade.order.permId = perm_id
    trade.order.orderRef = order_ref
    trade.orderStatus = _make_mock_order_status(status, filled, avg_price, perm_id)
    trade.log = log or []
    trade.contract = MagicMock()
    trade.contract.conId = 12345
    trade.contract.symbol = "AAPL"
    trade.contract.exchange = "SMART"
    trade.contract.currency = "USD"
    return trade


def _make_adapter(**kwargs):
    """创建一个绕过真实连接的 IBKRBrokerAdapter。"""
    adapter = IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
    adapter._host = "127.0.0.1"
    adapter._port = 4002
    adapter._client_id = 1
    adapter._account_id = kwargs.get("account_id", "DU1234567")
    adapter._timeout = 5.0
    adapter._connected = True
    adapter._account_verified = True
    adapter._loop = MagicMock()
    adapter._thread = MagicMock()
    adapter._ib = MagicMock()
    adapter._error_codes = kwargs.get("error_codes", {})  # M2.5
    return adapter


def _make_request(**overrides):
    defaults = dict(
        symbol="AAPL:US", side="BUY", quantity=100,
        order_type="LIMIT", limit_price=Decimal("150.00"),
        local_order_id="test-order-001",
    )
    defaults.update(overrides)
    return OrderRequest(**defaults)


# ═══════════════════════════════════════════════════════════════════
# 闸门测试 (M1, 保留)
# ═══════════════════════════════════════════════════════════════════


class TestGate1PaperOnly:
    """闸门 1: paper-only 断言。

    构造时预检 + 连接后 _resolve_and_verify_account 双重校验。
    """

    def test_paper_account_precheck_pass(self):
        """构造时传 DU 开头 → 预检通过（不触发连接）。"""
        adapter = IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
        IBKRBrokerAdapter.__init__(adapter, account_id="DU1234567")
        assert adapter._account_id == "DU1234567"

    @patch.dict("os.environ", {"ENABLE_IBKR_LIVE_TRADING": "false"})
    def test_live_account_precheck_rejected(self):
        """构造时传非 DU 开头 + 实盘未开启 → 预检拒绝。"""
        with pytest.raises(AssertionError, match="实盘交易未开启"):
            IBKRBrokerAdapter(account_id="U1234567")

    @patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", True)
    def test_live_account_allowed_when_enabled(self):
        """实盘开启时非 DU 账号预检通过。"""
        adapter = IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
        IBKRBrokerAdapter.__init__(adapter, account_id="U1234567")

    def test_resolve_managed_du_account(self):
        """连接后 managedAccounts 返回 DU 账户 → 校验通过。"""
        adapter = _make_adapter(account_id="")
        adapter._account_verified = False
        adapter._ib.managedAccounts.return_value = ["DU9999999"]
        adapter._resolve_and_verify_account()
        assert adapter._account_id == "DU9999999"
        assert adapter._account_verified is True

    def test_resolve_managed_live_account_rejected(self):
        """连接后 managedAccounts 返回非 DU → 拒绝并断连。"""
        adapter = _make_adapter(account_id="")
        adapter._account_verified = False
        adapter._ib.managedAccounts.return_value = ["U7777777"]
        with pytest.raises(AssertionError, match="非 Paper 账号"):
            adapter._resolve_and_verify_account()

    def test_resolve_empty_managed_accounts_rejected(self):
        """连接后 managedAccounts 返回空列表 → 报错。"""
        adapter = _make_adapter(account_id="")
        adapter._account_verified = False
        adapter._ib.managedAccounts.return_value = []
        with pytest.raises(RuntimeError, match="未返回任何账户"):
            adapter._resolve_and_verify_account()

    def test_resolve_config_account_not_in_managed(self):
        """config 指定的账户不在 managedAccounts 中 → 报错。"""
        adapter = _make_adapter(account_id="DU1111111")
        adapter._account_verified = False
        adapter._ib.managedAccounts.return_value = ["DU2222222"]
        with pytest.raises(RuntimeError, match="不在.*managedAccounts"):
            adapter._resolve_and_verify_account()

    def test_resolve_config_account_matches(self):
        """config 指定的账户在 managedAccounts 中 → 校验通过。"""
        adapter = _make_adapter(account_id="DU1234567")
        adapter._account_verified = False
        adapter._ib.managedAccounts.return_value = ["DU1234567", "DU9999999"]
        adapter._resolve_and_verify_account()
        assert adapter._account_id == "DU1234567"
        assert adapter._account_verified is True


class TestGate2MarketWhitelist:

    def test_us_market_allowed(self):
        adapter = _make_adapter()
        trade = _make_mock_trade(order_id=42, perm_id=1001)
        adapter._ib.placeOrder.return_value = trade
        result = adapter.place_order(_make_request(symbol="AAPL:US"))
        assert result.status == "submitted_to_broker"

    def test_hk_market_allowed(self):
        adapter = _make_adapter()
        trade = _make_mock_trade(order_id=43, perm_id=1002)
        adapter._ib.placeOrder.return_value = trade
        result = adapter.place_order(_make_request(symbol="0700:HK"))
        assert result.status == "submitted_to_broker"

    def test_cn_market_rejected(self):
        adapter = _make_adapter()
        result = adapter.place_order(_make_request(symbol="600519:SH"))
        assert result.status == "rejected"
        assert "不支持市场" in result.raw_response.get("reason", "")


class TestGate3OrderType:

    def test_limit_allowed(self):
        adapter = _make_adapter()
        adapter._ib.placeOrder.return_value = _make_mock_trade(perm_id=1003)
        result = adapter.place_order(_make_request(order_type="LIMIT"))
        assert result.status == "submitted_to_broker"

    def test_conditional_limit_rejected(self):
        adapter = _make_adapter()
        result = adapter.place_order(_make_request(order_type="CONDITIONAL_LIMIT"))
        assert result.status == "rejected"
        assert "条件限价单" in result.raw_response.get("reason", "")

    def test_market_order_rejected(self):
        adapter = _make_adapter()
        result = adapter.place_order(_make_request(order_type="MARKET"))
        assert result.status == "rejected"


class TestGate4OutsideRth:

    def test_outside_rth_false(self):
        adapter = _make_adapter()
        adapter._ib.placeOrder.return_value = _make_mock_trade(perm_id=1004)
        adapter.place_order(_make_request())
        call_args = adapter._ib.placeOrder.call_args
        order_arg = call_args[1].get("order") or call_args[0][1]
        assert order_arg.outsideRth is False


# ═══════════════════════════════════════════════════════════════════
# place_order + permId 收口 (M2)
# ═══════════════════════════════════════════════════════════════════


class TestPlaceOrderPermId:

    def test_broker_order_id_is_perm_id(self):
        """place_order 返回的 broker_order_id 应该是 permId。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(order_id=42, perm_id=98765)
        adapter._ib.placeOrder.return_value = trade
        result = adapter.place_order(_make_request())
        assert result.broker_order_id == "98765"

    def test_fallback_to_order_id_when_perm_id_zero(self):
        """permId 超时未回填 → fallback 用 orderId。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(order_id=42, perm_id=0)
        trade.orderStatus.permId = 0
        adapter._ib.placeOrder.return_value = trade
        # 用极短超时避免等待
        with patch("backend.services.action.brokers.ibkr.PERM_ID_WAIT_SECONDS", 0.01):
            result = adapter.place_order(_make_request())
        assert result.broker_order_id == "42"

    def test_order_ref_set(self):
        adapter = _make_adapter()
        adapter._ib.placeOrder.return_value = _make_mock_trade(perm_id=1005)
        adapter.place_order(_make_request(local_order_id="my-wp-id-123"))
        call_args = adapter._ib.placeOrder.call_args
        order_arg = call_args[1].get("order") or call_args[0][1]
        assert order_arg.orderRef == "my-wp-id-123"

    def test_raw_response_has_multi_ids(self):
        """raw_response 包含 permId / orderId / clientId / orderRef / conId。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(order_id=42, perm_id=98765, order_ref="ref-1")
        adapter._ib.placeOrder.return_value = trade
        result = adapter.place_order(_make_request(local_order_id="ref-1"))
        raw = result.raw_response
        assert raw["perm_id"] == 98765
        assert raw["order_id"] == 42
        assert raw["client_id"] == 1
        assert raw["order_ref"] == "ref-1"

    def test_connection_error_transparent(self):
        adapter = _make_adapter()
        adapter._ib.placeOrder.side_effect = ConnectionError("disconnected")
        with pytest.raises(ConnectionError):
            adapter.place_order(_make_request())

    def test_timeout_error_transparent(self):
        adapter = _make_adapter()
        adapter._ib.placeOrder.side_effect = TimeoutError("timed out")
        with pytest.raises(TimeoutError):
            adapter.place_order(_make_request())

    def test_generic_exception_rejected(self):
        adapter = _make_adapter()
        adapter._ib.placeOrder.side_effect = RuntimeError("IB error")
        result = adapter.place_order(_make_request())
        assert result.status == "rejected"


# ═══════════════════════════════════════════════════════════════════
# 状态映射 + Inactive 三分支 (M2 核心)
# ═══════════════════════════════════════════════════════════════════


class TestStatusMapping:

    @pytest.mark.parametrize("ib_status,expected", [
        ("ApiPending", "submitted_to_broker"),
        ("PendingSubmit", "submitted_to_broker"),
        ("PreSubmitted", "broker_pending"),
        ("Submitted", "broker_pending"),
        ("PendingCancel", "broker_pending"),
        ("Filled", "filled"),
        ("Cancelled", "cancelled"),
        ("ApiCancelled", "cancelled"),
    ])
    def test_standard_status_mapping(self, ib_status, expected):
        adapter = _make_adapter()
        trade = _make_mock_trade(perm_id=1, status=ib_status)
        adapter._ib.trades.return_value = [trade]
        result = adapter.get_order_status("1")
        assert result.status == expected

    def test_unknown_ib_status_maps_to_unknown(self):
        adapter = _make_adapter()
        trade = _make_mock_trade(perm_id=1, status="SomeFutureStatus")
        adapter._ib.trades.return_value = [trade]
        result = adapter.get_order_status("1")
        assert result.status == "unknown"

    def test_filled_quantity_returned(self):
        adapter = _make_adapter()
        trade = _make_mock_trade(perm_id=1, status="Filled", filled=100, avg_price=151.5)
        adapter._ib.trades.return_value = [trade]
        result = adapter.get_order_status("1")
        assert result.filled_quantity == 100
        assert result.avg_filled_price == Decimal("151.5")


class TestInactiveMapping:
    """Inactive 二义性三分支。"""

    def test_inactive_with_rejected_error_code(self):
        """Inactive + errorCode=201 → rejected。"""
        adapter = _make_adapter()
        log_entry = _make_mock_log_entry(error_code=201, message="Order rejected")
        trade = _make_mock_trade(perm_id=1, status="Inactive", log=[log_entry])
        adapter._ib.trades.return_value = [trade]

        result = adapter.get_order_status("1")
        assert result.status == "rejected"
        assert result.raw_response["inactive_resolved_as"] == "rejected"
        assert result.raw_response["inactive_error_code"] == 201

    def test_inactive_with_rejected_keyword(self):
        """Inactive + errorCode=0 但 message 含拒单关键词 → rejected。"""
        adapter = _make_adapter()
        log_entry = _make_mock_log_entry(
            error_code=0, message="Insufficient buying power"
        )
        trade = _make_mock_trade(perm_id=1, status="Inactive", log=[log_entry])
        adapter._ib.trades.return_value = [trade]

        result = adapter.get_order_status("1")
        assert result.status == "rejected"
        assert result.raw_response["inactive_resolved_as"] == "rejected"

    def test_inactive_no_error_with_why_held(self):
        """Inactive + 无 error + whyHeld 非空 → broker_pending。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(perm_id=1, status="Inactive", log=[])
        trade.orderStatus.whyHeld = "locate"
        adapter._ib.trades.return_value = [trade]

        result = adapter.get_order_status("1")
        assert result.status == "broker_pending"
        assert result.raw_response["inactive_resolved_as"] == "broker_pending"

    def test_inactive_ambiguous_unknown(self):
        """Inactive + 无 error + 无 whyHeld → unknown。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(perm_id=1, status="Inactive", log=[])
        trade.orderStatus.whyHeld = ""
        adapter._ib.trades.return_value = [trade]

        result = adapter.get_order_status("1")
        assert result.status == "unknown"
        assert result.raw_response["inactive_resolved_as"] == "unknown"

    def test_inactive_error_code_203(self):
        """errorCode=203 (Security not available) → rejected。"""
        adapter = _make_adapter()
        log_entry = _make_mock_log_entry(
            error_code=203, message="Security is not available"
        )
        trade = _make_mock_trade(perm_id=1, status="Inactive", log=[log_entry])
        adapter._ib.trades.return_value = [trade]

        result = adapter.get_order_status("1")
        assert result.status == "rejected"


# ═══════════════════════════════════════════════════════════════════
# not_found 重试 + OrphanOrderError (M2)
# ═══════════════════════════════════════════════════════════════════


class TestNotFoundRetry:

    def test_not_found_raises_orphan_error(self):
        """trades() 始终空 → 重试后抛 OrphanOrderError（继承 ConnectionError）。"""
        adapter = _make_adapter()
        adapter._ib.trades.return_value = []

        with patch("backend.services.action.brokers.ibkr.NOT_FOUND_RETRY_DELAYS", [0, 0]):
            with pytest.raises(OrphanOrderError):
                adapter.get_order_status("999")

        # OrphanOrderError 继承 ConnectionError
        with patch("backend.services.action.brokers.ibkr.NOT_FOUND_RETRY_DELAYS", [0, 0]):
            with pytest.raises(ConnectionError):
                adapter.get_order_status("999")

    def test_found_on_retry(self):
        """第一次 not_found，重试后找到。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(perm_id=555, status="Submitted")
        # _find_trade calls trades() twice per attempt (permId scan + orderId fallback)
        adapter._ib.trades.side_effect = [
            [],       # attempt 0: permId scan → miss
            [],       # attempt 0: orderId fallback → miss
            [trade],  # attempt 1 (retry): permId scan → hit
        ]

        with patch("backend.services.action.brokers.ibkr.NOT_FOUND_RETRY_DELAYS", [0, 0]):
            result = adapter.get_order_status("555")

        assert result.status == "broker_pending"


# ═══════════════════════════════════════════════════════════════════
# orderRef 反查 (M2)
# ═══════════════════════════════════════════════════════════════════


class TestOrderRefLookup:

    def test_find_by_ref_hit(self):
        """按 orderRef 反查 → 命中。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(perm_id=888, order_ref="wp-order-abc", status="Submitted")
        adapter._ib.trades.return_value = [trade]

        result = adapter.find_order_by_ref("wp-order-abc")
        assert result is not None
        assert result.broker_order_id == "888"
        assert result.local_order_id == "wp-order-abc"
        assert result.status == "broker_pending"

    def test_find_by_ref_miss(self):
        """按 orderRef 反查 → 未命中 → None。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(perm_id=888, order_ref="other-ref")
        adapter._ib.trades.return_value = [trade]

        result = adapter.find_order_by_ref("wp-order-xyz")
        assert result is None

    def test_find_by_ref_empty_trades(self):
        adapter = _make_adapter()
        adapter._ib.trades.return_value = []
        assert adapter.find_order_by_ref("anything") is None

    def test_find_by_ref_returns_correct_status(self):
        """反查命中的 Trade 走完整状态映射。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(
            perm_id=999, order_ref="wp-filled-order",
            status="Filled", filled=100, avg_price=155.0,
        )
        adapter._ib.trades.return_value = [trade]

        result = adapter.find_order_by_ref("wp-filled-order")
        assert result.status == "filled"
        assert result.filled_quantity == 100
        assert result.avg_filled_price == Decimal("155.0")


# ═══════════════════════════════════════════════════════════════════
# cancel_order 按 permId 反查 (M2)
# ═══════════════════════════════════════════════════════════════════


class TestCancelOrder:

    def test_cancel_found_by_perm_id(self):
        adapter = _make_adapter()
        trade = _make_mock_trade(perm_id=555, status="Submitted")
        adapter._ib.trades.return_value = [trade]
        assert adapter.cancel_order("555") is True
        adapter._ib.cancelOrder.assert_called_once()

    def test_cancel_not_found(self):
        adapter = _make_adapter()
        adapter._ib.trades.return_value = []
        assert adapter.cancel_order("999") is False

    def test_cancel_already_filled(self):
        adapter = _make_adapter()
        trade = _make_mock_trade(perm_id=555, status="Filled")
        adapter._ib.trades.return_value = [trade]
        assert adapter.cancel_order("555") is False


# ═══════════════════════════════════════════════════════════════════
# misc
# ═══════════════════════════════════════════════════════════════════


class TestMisc:

    def test_broker_name(self):
        assert _make_adapter().broker_name == "ibkr"

    def test_parse_symbol_us(self):
        assert IBKRBrokerAdapter._parse_symbol("AAPL:US") == ("US", "AAPL")

    def test_parse_symbol_hk(self):
        assert IBKRBrokerAdapter._parse_symbol("0700:HK") == ("HK", "0700")

    def test_parse_symbol_cn(self):
        assert IBKRBrokerAdapter._parse_symbol("600519:SH") == ("SH", "600519")


# ═══════════════════════════════════════════════════════════════════
# M2.5: errorCode 双来源合并 + Inactive 分流校准
# ═══════════════════════════════════════════════════════════════════


class TestM25ErrorCodeDualSource:
    """M2.5 开市探针校准: error callback + trade.log 双来源。"""

    def test_inactive_with_201_via_callback_rejected(self):
        """★ 核心回归: 201 经 error callback 到达 → Inactive 判 rejected。

        探针实测: 保证金不足时 trade.log errorCode=0，
        但 error callback 带 201。修复前会误判成 unknown。
        """
        adapter = _make_adapter(error_codes={
            8: {"errorCode": 201, "errorString": "Order rejected - insufficient margin"},
        })
        trade = _make_mock_trade(
            order_id=8,
            status="Inactive",
            log=[_make_mock_log_entry(error_code=0, message="", status="Inactive")],
        )
        mapped, extras = adapter._map_inactive(trade)
        assert mapped == "rejected", f"expected rejected, got {mapped}"
        assert extras["inactive_cb_error_code"] == 201
        assert extras["inactive_resolved_as"] == "rejected"

    def test_inactive_with_200_in_trade_log_rejected(self):
        """200 在 trade.log 里 → rejected（无效合约，探针 2a 的 Cancelled 分支不走 Inactive，
        但如果未来 IB 行为变化导致 200 + Inactive，也应判 rejected）。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(
            order_id=6,
            status="Inactive",
            log=[_make_mock_log_entry(error_code=200, message="No security definition",
                                     status="Inactive")],
        )
        mapped, extras = adapter._map_inactive(trade)
        assert mapped == "rejected"
        assert extras["inactive_log_error_code"] == 200

    def test_inactive_no_error_code_unknown(self):
        """Inactive 但无任何拒单码 → unknown（保守处理）。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(
            order_id=9,
            status="Inactive",
            log=[_make_mock_log_entry(error_code=0, message="", status="Inactive")],
        )
        mapped, extras = adapter._map_inactive(trade)
        assert mapped == "unknown"
        assert extras["inactive_resolved_as"] == "unknown"

    def test_error_202_not_rejected(self):
        """202 是撤单确认，不能判 rejected。

        202 在 Cancelled 状态出现（不走 Inactive），但确认 REJECTED_ERROR_CODES 不含 202。
        """
        from backend.services.action.brokers.ibkr import REJECTED_ERROR_CODES
        assert 202 not in REJECTED_ERROR_CODES

    def test_inactive_keyword_fallback_rejected(self):
        """trade.log errorCode=0 但 message 含拒单关键词 → rejected。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(
            order_id=10,
            status="Inactive",
            log=[_make_mock_log_entry(
                error_code=0,
                message="Your buying power is insufficient",
                status="Inactive",
            )],
        )
        mapped, extras = adapter._map_inactive(trade)
        assert mapped == "rejected"

    def test_inactive_why_held_broker_pending(self):
        """Inactive + whyHeld 非空 + 无 error → broker_pending。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(
            order_id=11,
            status="Inactive",
            log=[_make_mock_log_entry(error_code=0, message="", status="Inactive")],
        )
        trade.orderStatus.whyHeld = "locate"
        mapped, extras = adapter._map_inactive(trade)
        assert mapped == "broker_pending"
        assert extras["why_held"] == "locate"

    def test_on_ib_error_captures_rejected_codes(self):
        """_on_ib_error 只捕获 REJECTED_ERROR_CODES 里的 code。"""
        adapter = _make_adapter()
        # 201 应被捕获
        adapter._on_ib_error(reqId=8, errorCode=201,
                             errorString="Order rejected", contract=None)
        assert 8 in adapter._error_codes
        assert adapter._error_codes[8]["errorCode"] == 201

        # 202 不应被捕获（撤单确认）
        adapter._on_ib_error(reqId=9, errorCode=202,
                             errorString="Order Canceled", contract=None)
        assert 9 not in adapter._error_codes

        # -1 不应被捕获（非订单相关）
        adapter._on_ib_error(reqId=-1, errorCode=201,
                             errorString="something", contract=None)
        assert -1 not in adapter._error_codes

    def test_cancelled_with_200_not_misclassified(self):
        """带 200 的 Cancelled（无效合约）→ _map_status 走 Cancelled 分支，
        不走 Inactive 分流，映射为 cancelled。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(
            order_id=6, status="Cancelled",
            log=[_make_mock_log_entry(error_code=200, message="No security definition",
                                     status="Cancelled")],
        )
        mapped, extras = adapter._map_status(trade)
        assert mapped == "cancelled"  # Cancelled 直接走 IB_TO_V32_STATUS，不进 _map_inactive
