"""
TigerBrokerAdapter 单元测试。

M1.1: 闸门 / 状态映射 / Symbol 解析 / Happy Path / 审计 Payload
M1.2: EXPIRED 二义性 / not_found 重试 / ApiException 精细分类 / 异常透传
M2:   CredentialProvider 集成(InMemoryCredentialProvider 替代 private_key_path)

全部使用 mock,不调用真实 Tiger API(M1.3 做沙箱验证)。
"""
from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tigeropen.common.exceptions import ApiException
from tigeropen.trade.domain.order import OrderStatus as TigerOrderStatus

from backend.services.action.brokers.base import OrderRequest, OrderStatusUpdate
from backend.services.action.brokers.credentials import (
    InMemoryCredentialProvider,
    CredentialNotFoundError,
)
from backend.services.action.brokers.tiger import (
    TIGER_PAPER_ACCOUNT,
    TIGER_TO_V32_STATUS,
    OrphanOrderError,
    TigerBrokerAdapter,
    UnsupportedMarketError,
)

FAKE_PEM = "-----BEGIN RSA PRIVATE KEY-----\nfake_key_content\n-----END RSA PRIVATE KEY-----\n"
FAKE_CREDS = {
    "tiger_id": "20159046",
    "account_id": TIGER_PAPER_ACCOUNT,
    "private_key_pem": FAKE_PEM,
}


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def mock_trade_client():
    return MagicMock()


@pytest.fixture
def credential_provider():
    provider = InMemoryCredentialProvider()
    provider.save("tiger.paper", dict(FAKE_CREDS))
    return provider


@pytest.fixture
def adapter(mock_trade_client, credential_provider):
    with patch("backend.services.action.brokers.tiger.TradeClient") as MockTC, \
         patch("backend.services.action.brokers.tiger.TigerOpenClientConfig") as MockConfig:
        mock_config_inst = MagicMock()
        mock_config_inst.is_paper = True
        MockConfig.return_value = mock_config_inst
        MockTC.return_value = mock_trade_client

        a = TigerBrokerAdapter(
            credential_provider=credential_provider,
            broker_key="tiger.paper",
        )
    return a


def _make_request(**overrides) -> OrderRequest:
    defaults = {
        "symbol": "US.SPY",
        "side": "BUY",
        "quantity": 1,
        "order_type": "LIMIT",
        "limit_price": Decimal("370.0"),
        "local_order_id": "test-order-001",
    }
    defaults.update(overrides)
    return OrderRequest(**defaults)


def _make_tiger_order(**overrides):
    o = MagicMock()
    o.id = overrides.get("id", 43207668465158144)
    o.order_id = overrides.get("order_id", 7)
    o.status = overrides.get("status", TigerOrderStatus.HELD)
    o.filled = overrides.get("filled", 0)
    o.avg_fill_price = overrides.get("avg_fill_price", 0.0)
    o.reason = overrides.get("reason", "")
    o.trade_time = overrides.get("trade_time", int(time.time() * 1000))
    o.outside_rth = overrides.get("outside_rth", False)
    o.contract = MagicMock()
    o.contract.symbol = overrides.get("symbol", "SPY")
    o.contract.market = overrides.get("market", "US")
    o.contract.currency = overrides.get("currency", "USD")
    return o


# ============================================================
# 1. 闸门测试 (M1.1)
# ============================================================

class TestSafetyGates:
    def test_paper_only_blocks_live_account(self):
        provider = InMemoryCredentialProvider()
        provider.save("tiger.live", {
            "tiger_id": "20159046",
            "account_id": "4472659",
            "private_key_pem": FAKE_PEM,
        })

        with patch("backend.services.action.brokers.tiger.TradeClient"), \
             patch("backend.services.action.brokers.tiger.TigerOpenClientConfig"), \
             pytest.raises(AssertionError, match="实盘交易未开启"):
            TigerBrokerAdapter(
                credential_provider=provider,
                broker_key="tiger.live",
            )

    def test_market_whitelist_blocks_cn(self, adapter):
        req = _make_request(symbol="600519")
        result = adapter.place_order(req)
        assert result.status == "rejected"
        assert "不支持市场 CN" in result.raw_response.get("reason", "")

    def test_market_whitelist_blocks_cn_prefixed(self, adapter):
        req = _make_request(symbol="CN.600519")
        result = adapter.place_order(req)
        assert result.status == "rejected"
        assert "不支持市场 CN" in result.raw_response.get("reason", "")

    def test_market_order_rejected(self, adapter):
        req = _make_request(order_type="MARKET")
        result = adapter.place_order(req)
        assert result.status == "rejected"
        assert "仅支持 LIMIT" in result.raw_response.get("reason", "")

    def test_outside_rth_force_false(self, adapter, mock_trade_client):
        mock_trade_client.place_order.return_value = 12345
        with patch("backend.services.action.brokers.tiger.limit_order") as mock_lo:
            mock_order = _make_tiger_order(id=12345)
            mock_order.outside_rth = True
            mock_lo.return_value = mock_order
            adapter.place_order(_make_request())
            assert mock_order.outside_rth is False


# ============================================================
# 2. 状态映射测试 (M1.1, _map_status now returns tuple)
# ============================================================

class TestStatusMapping:
    def test_map_held_to_broker_pending(self, adapter):
        o = _make_tiger_order(status=TigerOrderStatus.HELD)
        mapped, extras = adapter._map_status(o)
        assert mapped == "broker_pending"
        assert extras == {}

    def test_map_filled(self, adapter):
        o = _make_tiger_order(status=TigerOrderStatus.FILLED)
        mapped, _ = adapter._map_status(o)
        assert mapped == "filled"

    def test_map_partially_filled(self, adapter):
        o = _make_tiger_order(status=TigerOrderStatus.PARTIALLY_FILLED)
        mapped, _ = adapter._map_status(o)
        assert mapped == "partially_filled"

    def test_map_cancelled(self, adapter):
        o = _make_tiger_order(status=TigerOrderStatus.CANCELLED)
        mapped, _ = adapter._map_status(o)
        assert mapped == "cancelled"

    def test_map_pending_cancel_to_cancelled(self, adapter):
        o = _make_tiger_order(status=TigerOrderStatus.PENDING_CANCEL)
        mapped, _ = adapter._map_status(o)
        assert mapped == "cancelled"

    def test_map_rejected(self, adapter):
        o = _make_tiger_order(status=TigerOrderStatus.REJECTED)
        mapped, _ = adapter._map_status(o)
        assert mapped == "rejected"

    def test_map_pending_new_to_submitted(self, adapter):
        o = _make_tiger_order(status=TigerOrderStatus.PENDING_NEW)
        mapped, _ = adapter._map_status(o)
        assert mapped == "submitted_to_broker"

    def test_map_new_to_submitted(self, adapter):
        o = _make_tiger_order(status=TigerOrderStatus.NEW)
        mapped, _ = adapter._map_status(o)
        assert mapped == "submitted_to_broker"

    def test_map_unknown_enum_to_unknown(self, adapter):
        o = MagicMock()
        o.status = "SOME_FUTURE_STATUS"
        mapped, _ = adapter._map_status(o)
        assert mapped == "unknown"

    def test_all_non_expired_tiger_statuses_covered(self):
        for member in TigerOrderStatus:
            if member == TigerOrderStatus.EXPIRED:
                continue
            assert member in TIGER_TO_V32_STATUS, f"缺少映射: {member}"


# ============================================================
# 3. Symbol 解析测试 (M1.1)
# ============================================================

class TestParseSymbol:
    def test_with_us_prefix(self):
        assert TigerBrokerAdapter._parse_symbol("US.SPY") == ("US", "SPY")

    def test_with_hk_prefix(self):
        assert TigerBrokerAdapter._parse_symbol("HK.00700") == ("HK", "00700")

    def test_with_cn_prefix(self):
        assert TigerBrokerAdapter._parse_symbol("CN.600519") == ("CN", "600519")

    def test_pure_us_letters(self):
        assert TigerBrokerAdapter._parse_symbol("SPY") == ("US", "SPY")

    def test_pure_us_mixed(self):
        assert TigerBrokerAdapter._parse_symbol("MSFT") == ("US", "MSFT")

    def test_pure_hk_5digit(self):
        assert TigerBrokerAdapter._parse_symbol("00700") == ("HK", "00700")

    def test_pure_hk_4digit(self):
        assert TigerBrokerAdapter._parse_symbol("9988") == ("HK", "9988")

    def test_pure_cn_6digit(self):
        assert TigerBrokerAdapter._parse_symbol("600519") == ("CN", "600519")

    def test_lowercase_letters(self):
        assert TigerBrokerAdapter._parse_symbol("spy") == ("US", "spy")

    def test_prefix_case_insensitive(self):
        assert TigerBrokerAdapter._parse_symbol("us.SPY") == ("US", "SPY")

    # 新增: TICKER:MARKET 主路径测试
    def test_colon_us(self):
        assert TigerBrokerAdapter._parse_symbol("SPY:US") == ("US", "SPY")

    def test_colon_hk(self):
        assert TigerBrokerAdapter._parse_symbol("0700:HK") == ("HK", "0700")

    def test_colon_sh(self):
        assert TigerBrokerAdapter._parse_symbol("600519:SH") == ("SH", "600519")


# ============================================================
# 4. Happy Path 测试 (M1.1, mock Tiger SDK)
# ============================================================

class TestHappyPath:
    def test_place_order_us_limit_buy(self, adapter, mock_trade_client):
        mock_order = _make_tiger_order(id=99999)
        mock_trade_client.place_order.return_value = 99999

        with patch("backend.services.action.brokers.tiger.limit_order", return_value=mock_order):
            result = adapter.place_order(_make_request())

        assert result.status == "submitted_to_broker"
        assert result.broker_order_id == "99999"
        assert result.local_order_id == "test-order-001"
        assert result.filled_quantity == 0
        assert result.timestamp > 0
        mock_trade_client.place_order.assert_called_once_with(mock_order)

    def test_place_order_hk_limit_buy(self, adapter, mock_trade_client):
        mock_order = _make_tiger_order(id=88888)
        mock_trade_client.place_order.return_value = 88888

        with patch("backend.services.action.brokers.tiger.limit_order", return_value=mock_order):
            result = adapter.place_order(_make_request(symbol="HK.00700"))

        assert result.status == "submitted_to_broker"
        assert result.raw_response["market"] == "HK"
        assert result.raw_response["currency"] == "HKD"

    def test_get_order_status_held(self, adapter, mock_trade_client):
        tiger_order = _make_tiger_order(status=TigerOrderStatus.HELD, filled=0)
        mock_trade_client.get_order.return_value = tiger_order

        result = adapter.get_order_status("43207668465158144")

        assert result.status == "broker_pending"
        assert result.broker_order_id == "43207668465158144"
        assert result.filled_quantity == 0

    def test_get_order_status_filled(self, adapter, mock_trade_client):
        tiger_order = _make_tiger_order(
            status=TigerOrderStatus.FILLED, filled=10, avg_fill_price=739.28,
        )
        mock_trade_client.get_order.return_value = tiger_order

        result = adapter.get_order_status("123")

        assert result.status == "filled"
        assert result.filled_quantity == 10
        assert result.avg_filled_price == Decimal("739.28")

    def test_cancel_order_success(self, adapter, mock_trade_client):
        mock_trade_client.cancel_order.return_value = 12345
        assert adapter.cancel_order("12345") is True

    def test_list_open_orders(self, adapter, mock_trade_client):
        mock_trade_client.get_open_orders.return_value = [
            _make_tiger_order(id=111, status=TigerOrderStatus.HELD),
            _make_tiger_order(id=222, status=TigerOrderStatus.PARTIALLY_FILLED, filled=5),
        ]
        result = adapter.list_open_orders()
        assert len(result) == 2
        assert result[0].status == "broker_pending"
        assert result[1].status == "partially_filled"
        assert result[1].filled_quantity == 5

    def test_list_open_orders_empty(self, adapter, mock_trade_client):
        mock_trade_client.get_open_orders.return_value = []
        assert adapter.list_open_orders() == []

    def test_get_positions(self, adapter, mock_trade_client):
        pos = MagicMock()
        pos.contract = MagicMock()
        pos.contract.symbol = "SPY"
        pos.contract.market = "US"
        pos.contract.currency = "USD"
        pos.quantity = 100
        pos.average_cost = 500.0
        pos.market_value = 73928.0
        mock_trade_client.get_positions.return_value = [pos]

        result = adapter.get_positions()
        assert len(result) == 1
        assert result[0]["symbol"] == "SPY"
        assert result[0]["quantity"] == 100

    def test_get_account_info(self, adapter, mock_trade_client):
        asset = MagicMock()
        asset.summary = MagicMock()
        asset.summary.currency = "USD"
        asset.summary.cash = 1000000.0
        asset.summary.buying_power = 4000000.0
        asset.summary.net_liquidation = 1000000.0
        mock_trade_client.get_assets.return_value = [asset]

        result = adapter.get_account_info()
        assert result["broker"] == "tiger"
        assert result["cash"] == 1000000.0
        assert result["is_paper"] is True

    def test_authenticate_success(self, adapter, mock_trade_client):
        acc = MagicMock()
        acc.account = TIGER_PAPER_ACCOUNT
        mock_trade_client.get_managed_accounts.return_value = [acc]
        assert adapter.authenticate({}) is True

    def test_authenticate_failure(self, adapter, mock_trade_client):
        mock_trade_client.get_managed_accounts.side_effect = Exception("network")
        assert adapter.authenticate({}) is False

    def test_broker_name(self, adapter):
        assert adapter.broker_name == "tiger"


# ============================================================
# 5. 审计 Payload 测试 (M1.1)
# ============================================================

class TestAuditPayload:
    def test_raw_response_contains_required_fields(self, adapter, mock_trade_client):
        mock_order = _make_tiger_order(id=12345)
        mock_trade_client.place_order.return_value = 12345

        with patch("backend.services.action.brokers.tiger.limit_order", return_value=mock_order):
            result = adapter.place_order(_make_request())

        raw = result.raw_response
        required_keys = {
            "broker", "account_type", "account_id_masked",
            "action", "outside_rth", "broker_order_id",
            "tiger_status", "mapped_status", "reason",
            "symbol", "market", "currency", "limit_price",
            "quantity", "side", "order_type",
        }
        for key in required_keys:
            assert key in raw, f"raw_response 缺少字段: {key}"

        assert raw["broker"] == "tiger"
        assert raw["account_type"] == "paper"
        assert raw["outside_rth"] is False

    def test_raw_response_no_fx_rate(self, adapter, mock_trade_client):
        mock_order = _make_tiger_order(id=12345)
        mock_trade_client.place_order.return_value = 12345

        with patch("backend.services.action.brokers.tiger.limit_order", return_value=mock_order):
            result = adapter.place_order(_make_request())

        raw = result.raw_response
        assert "fx_rate_to_cny" not in raw
        assert "amount_cny_equivalent" not in raw


# ============================================================
# 6. EXPIRED 二义性测试 (M1.2)
# ============================================================

class TestExpiredMapping:
    def test_expired_rejected_keyword_buying_power(self, adapter):
        o = _make_tiger_order(
            status=TigerOrderStatus.EXPIRED,
            reason="您的可用资金或者可用购买力不足",
        )
        mapped, extras = adapter._map_status(o)
        assert mapped == "rejected"
        assert extras.get("expired_resolved_as") == "rejected"

    def test_expired_rejected_keyword_contract(self, adapter):
        o = _make_tiger_order(
            status=TigerOrderStatus.EXPIRED,
            reason="合约不正确",
        )
        mapped, extras = adapter._map_status(o)
        assert mapped == "rejected"

    def test_expired_rejected_keyword_permission(self, adapter):
        o = _make_tiger_order(
            status=TigerOrderStatus.EXPIRED,
            reason="权限不足,无法交易",
        )
        mapped, extras = adapter._map_status(o)
        assert mapped == "rejected"

    def test_expired_expired_keyword_day_order(self, adapter):
        o = _make_tiger_order(
            status=TigerOrderStatus.EXPIRED,
            reason="day order expired at close",
        )
        mapped, extras = adapter._map_status(o)
        assert mapped == "expired"
        assert extras.get("expired_resolved_as") == "expired"

    def test_expired_expired_keyword_timeout(self, adapter):
        o = _make_tiger_order(
            status=TigerOrderStatus.EXPIRED,
            reason="订单超时已过期",
        )
        mapped, extras = adapter._map_status(o)
        assert mapped == "expired"

    def test_expired_unknown_reason_empty(self, adapter):
        o = _make_tiger_order(
            status=TigerOrderStatus.EXPIRED,
            reason="",
        )
        mapped, extras = adapter._map_status(o)
        assert mapped == "unknown"
        assert extras.get("tiger_expired_unknown_reason") is True
        assert extras.get("expired_resolved_as") == "unknown"

    def test_expired_unknown_reason_none(self, adapter):
        o = _make_tiger_order(status=TigerOrderStatus.EXPIRED)
        o.reason = None
        mapped, extras = adapter._map_status(o)
        assert mapped == "unknown"
        assert extras.get("tiger_expired_unknown_reason") is True

    def test_expired_unknown_reason_other(self, adapter):
        o = _make_tiger_order(
            status=TigerOrderStatus.EXPIRED,
            reason="some unexpected reason from Tiger",
        )
        mapped, extras = adapter._map_status(o)
        assert mapped == "unknown"
        assert extras.get("tiger_expired_unknown_reason") is True

    def test_expired_raw_response_in_get_order_status(self, adapter, mock_trade_client):
        tiger_order = _make_tiger_order(
            status=TigerOrderStatus.EXPIRED,
            reason="购买力不足",
        )
        mock_trade_client.get_order.return_value = tiger_order

        result = adapter.get_order_status("456")
        assert result.status == "rejected"
        assert result.raw_response.get("expired_resolved_as") == "rejected"

    def test_expired_unknown_raw_response_in_get_order_status(self, adapter, mock_trade_client):
        tiger_order = _make_tiger_order(
            status=TigerOrderStatus.EXPIRED,
            reason="",
        )
        mock_trade_client.get_order.return_value = tiger_order

        result = adapter.get_order_status("789")
        assert result.status == "unknown"
        assert result.raw_response.get("tiger_expired_unknown_reason") is True


# ============================================================
# 7. not_found 重试测试 (M1.2)
# ============================================================

class TestNotFoundRetry:
    def test_get_order_not_found_retry_once_success(self, adapter, mock_trade_client):
        """第一次 not_found,第二次成功。"""
        tiger_order = _make_tiger_order(status=TigerOrderStatus.HELD)
        mock_trade_client.get_order.side_effect = [
            ApiException(1200, "not_found:订单不存在"),
            tiger_order,
        ]

        with patch("backend.services.action.brokers.tiger.time.sleep"):
            result = adapter.get_order_status("123")

        assert result.status == "broker_pending"
        assert mock_trade_client.get_order.call_count == 2

    def test_get_order_not_found_retry_exhausted(self, adapter, mock_trade_client):
        """连续 3 次 not_found -> OrphanOrderError -> 透传为 ConnectionError。"""
        mock_trade_client.get_order.side_effect = [
            ApiException(1200, "not_found:订单不存在"),
            ApiException(1200, "not_found:订单不存在"),
            ApiException(1200, "not_found:订单不存在"),
        ]

        with patch("backend.services.action.brokers.tiger.time.sleep"), \
             pytest.raises(OrphanOrderError):
            adapter.get_order_status("999")

        assert mock_trade_client.get_order.call_count == 3

    def test_get_order_other_api_error_no_retry(self, adapter, mock_trade_client):
        """非 not_found 错误,不重试,返回 unknown。"""
        mock_trade_client.get_order.side_effect = ApiException(1200, "internal_error")

        result = adapter.get_order_status("456")
        assert result.status == "unknown"
        assert mock_trade_client.get_order.call_count == 1

    def test_orphan_order_error_is_connection_error(self):
        """OrphanOrderError 是 ConnectionError 子类。"""
        err = OrphanOrderError("test")
        assert isinstance(err, ConnectionError)

    def test_retry_uses_exponential_backoff(self, adapter, mock_trade_client):
        """验证指数退避的 sleep 时间。"""
        mock_trade_client.get_order.side_effect = [
            ApiException(1200, "not_found"),
            ApiException(1200, "not_found"),
            ApiException(1200, "not_found"),
        ]
        sleep_calls = []
        with patch("backend.services.action.brokers.tiger.time.sleep", side_effect=lambda s: sleep_calls.append(s)), \
             pytest.raises(OrphanOrderError):
            adapter.get_order_status("123")

        assert sleep_calls == [1, 2]  # 2^0=1, 2^1=2


# ============================================================
# 8. ApiException 精细分类测试 (M1.2)
# ============================================================

class TestApiExceptionClassification:
    def test_place_order_rejected_keyword_contract(self, adapter, mock_trade_client):
        mock_trade_client.place_order.side_effect = ApiException(1200, "bad_request:合约不正确")

        with patch("backend.services.action.brokers.tiger.limit_order", return_value=_make_tiger_order()):
            result = adapter.place_order(_make_request())

        assert result.status == "rejected"
        assert result.raw_response.get("raw_error_code") == 1200
        assert "unknown_api_error" not in result.raw_response

    def test_place_order_rejected_keyword_permission(self, adapter, mock_trade_client):
        mock_trade_client.place_order.side_effect = ApiException(1200, "permission denied")

        with patch("backend.services.action.brokers.tiger.limit_order", return_value=_make_tiger_order()):
            result = adapter.place_order(_make_request())

        assert result.status == "rejected"
        assert "unknown_api_error" not in result.raw_response

    def test_place_order_rejected_keyword_buying_power(self, adapter, mock_trade_client):
        mock_trade_client.place_order.side_effect = ApiException(1200, "购买力不足")

        with patch("backend.services.action.brokers.tiger.limit_order", return_value=_make_tiger_order()):
            result = adapter.place_order(_make_request())

        assert result.status == "rejected"
        assert "unknown_api_error" not in result.raw_response

    def test_place_order_unknown_api_error(self, adapter, mock_trade_client):
        mock_trade_client.place_order.side_effect = ApiException(500, "internal server error")

        with patch("backend.services.action.brokers.tiger.limit_order", return_value=_make_tiger_order()):
            result = adapter.place_order(_make_request())

        assert result.status == "rejected"
        assert result.raw_response.get("unknown_api_error") is True

    def test_cancel_order_already_terminal_returns_false(self, adapter, mock_trade_client):
        mock_trade_client.cancel_order.side_effect = ApiException(1200, "已成交无法撤单")
        assert adapter.cancel_order("12345") is False

    def test_cancel_order_unknown_error_returns_false(self, adapter, mock_trade_client):
        mock_trade_client.cancel_order.side_effect = ApiException(500, "server error")
        assert adapter.cancel_order("12345") is False


# ============================================================
# 9. ConnectionError / TimeoutError 透传测试 (M1.2)
# ============================================================

class TestExceptionPropagation:
    def test_place_order_connection_error_propagates(self, adapter, mock_trade_client):
        mock_trade_client.place_order.side_effect = ConnectionError("network down")

        with patch("backend.services.action.brokers.tiger.limit_order", return_value=_make_tiger_order()), \
             pytest.raises(ConnectionError, match="network down"):
            adapter.place_order(_make_request())

    def test_place_order_timeout_error_propagates(self, adapter, mock_trade_client):
        mock_trade_client.place_order.side_effect = TimeoutError("timed out")

        with patch("backend.services.action.brokers.tiger.limit_order", return_value=_make_tiger_order()), \
             pytest.raises(TimeoutError, match="timed out"):
            adapter.place_order(_make_request())

    def test_get_order_status_connection_error_propagates(self, adapter, mock_trade_client):
        mock_trade_client.get_order.side_effect = ConnectionError("conn refused")

        with pytest.raises(ConnectionError, match="conn refused"):
            adapter.get_order_status("123")

    def test_orphan_order_error_propagates_as_connection_error(self, adapter, mock_trade_client):
        """OrphanOrderError 透传,上层 except ConnectionError 能 catch。"""
        mock_trade_client.get_order.side_effect = [
            ApiException(1200, "not_found"),
            ApiException(1200, "not_found"),
            ApiException(1200, "not_found"),
        ]

        with patch("backend.services.action.brokers.tiger.time.sleep"):
            with pytest.raises(ConnectionError):
                adapter.get_order_status("999")
