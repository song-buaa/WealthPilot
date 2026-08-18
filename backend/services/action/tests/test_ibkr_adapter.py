"""
IBKRBrokerAdapter 单元测试 — M1 + M2 验证。

用 mock 替代 ib_async.IB 连接，不依赖真实 Gateway。
覆盖: 四闸门、place_order + orderRef + permId、Inactive 三分支、
状态映射、异常透传、not_found 重试、orderRef 反查。

运行: cd ~/Documents/GitHub/WealthPilot && python -m pytest backend/services/action/tests/test_ibkr_adapter.py -v
"""
import asyncio
import inspect
import threading
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

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
                     perm_id=100, order_ref="", log=None, advanced_error=""):
    trade = MagicMock()
    trade.order = MagicMock()
    trade.order.orderId = order_id
    trade.order.permId = perm_id
    trade.order.orderRef = order_ref
    trade.order.action = "BUY"
    trade.order.orderType = "LMT"
    trade.order.totalQuantity = 100
    trade.order.lmtPrice = 150.0
    trade.order.tif = "DAY"
    trade.order.outsideRth = False
    trade.order.transmit = True
    trade.order.whatIf = False
    trade.order.account = "DU1234567"
    trade.order.clientId = 1
    trade.orderStatus = _make_mock_order_status(status, filled, avg_price, perm_id)
    trade.log = log or []
    trade.advancedError = advanced_error
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
    adapter._open_order_evidence = kwargs.get("open_order_evidence", {})
    adapter._submitted_order_ids = kwargs.get("submitted_order_ids", set())
    def run_immediately(operation, **_kwargs):
        result = operation()
        return asyncio.run(result) if inspect.isawaitable(result) else result

    adapter._run_on_loop = run_immediately
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


class TestGate1AccountPrefix:
    """闸门 1: 账户前缀正向校验（paper/live 互斥）。

    构造时预检 + 连接后 _resolve_and_verify_account 双重校验。
    """

    # ── paper 模式 ──

    def test_paper_du_precheck_pass(self):
        """paper 模式 + DU 账户 → 预检通过。"""
        adapter = IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
        IBKRBrokerAdapter.__init__(adapter, account_id="DU1234567")
        assert adapter._account_id == "DU1234567"

    @patch.dict("os.environ", {"ENABLE_IBKR_LIVE_TRADING": "false"})
    def test_paper_u_account_precheck_rejected(self):
        """paper 模式 + U 账户（实盘）→ 预检拒绝。"""
        with patch("backend.services.action.brokers.ibkr.IBKR_READ_ONLY_MODE", False):
            with pytest.raises(AssertionError, match="Paper 模式"):
                IBKRBrokerAdapter(account_id="U1234567")

    def test_paper_resolve_du_pass(self):
        """连接后 managedAccounts 返回 DU → paper 模式通过。"""
        adapter = _make_adapter(account_id="")
        adapter._account_verified = False
        adapter._ib.managedAccounts.return_value = ["DU9999999"]
        adapter._resolve_and_verify_account()
        assert adapter._account_id == "DU9999999"
        assert adapter._account_verified is True

    def test_paper_resolve_u_rejected(self):
        """连接后 managedAccounts 返回 U → paper 模式拒绝。"""
        adapter = _make_adapter(account_id="")
        adapter._account_verified = False
        adapter._ib.managedAccounts.return_value = ["U7777777"]
        with patch("backend.services.action.brokers.ibkr.IBKR_READ_ONLY_MODE", False):
            with pytest.raises(AssertionError, match="Paper 模式"):
                adapter._resolve_and_verify_account()

    @patch("backend.services.action.brokers.ibkr.IBKR_READ_ONLY_MODE", True)
    @patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", False)
    def test_live_read_only_u_precheck_pass(self):
        """Live + local read-only + live trading disabled 可只读连接。"""
        adapter = IBKRBrokerAdapter(account_id="U1234567")
        assert adapter._account_id == "U1234567"

    @patch("backend.services.action.brokers.ibkr.IBKR_READ_ONLY_MODE", True)
    @patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", False)
    def test_live_read_only_resolve_u_pass(self):
        adapter = _make_adapter(account_id="")
        adapter._account_verified = False
        adapter._ib.managedAccounts.return_value = ["U7777777"]
        adapter._resolve_and_verify_account()
        assert adapter._account_id == "U7777777"

    # ── live 模式 ──

    @patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", True)
    def test_live_u_precheck_pass(self):
        """live 模式 + U 账户 → 预检通过。"""
        adapter = IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
        IBKRBrokerAdapter.__init__(adapter, account_id="U1234567")
        assert adapter._account_id == "U1234567"

    @patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", True)
    def test_live_du_precheck_rejected(self):
        """live 模式 + DU 账户（paper）→ 预检拒绝（互斥）。"""
        with pytest.raises(AssertionError, match="Paper 账户"):
            IBKRBrokerAdapter(account_id="DU1234567")

    @patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", True)
    def test_live_resolve_u_pass(self):
        """连接后 live 模式 + U 账户 → 通过。"""
        adapter = _make_adapter(account_id="")
        adapter._account_verified = False
        adapter._ib.managedAccounts.return_value = ["U7777777"]
        with patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", True):
            adapter._resolve_and_verify_account()
        assert adapter._account_id == "U7777777"
        assert adapter._account_verified is True

    @patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", True)
    def test_live_resolve_du_rejected(self):
        """连接后 live 模式 + DU 账户 → 拒绝（互斥）。"""
        adapter = _make_adapter(account_id="")
        adapter._account_verified = False
        adapter._ib.managedAccounts.return_value = ["DU1234567"]
        with patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", True):
            with pytest.raises(AssertionError, match="Paper 账户"):
                adapter._resolve_and_verify_account()

    # ── 空/异常 ──

    def test_empty_account_rejected(self):
        """任何模式 + 空账户 → 拒绝。"""
        from backend.services.action.brokers.ibkr import _validate_account_prefix
        with pytest.raises(AssertionError, match="账户为空"):
            _validate_account_prefix("", "test")

    def test_none_account_rejected(self):
        """任何模式 + None 账户 → 拒绝。"""
        from backend.services.action.brokers.ibkr import _validate_account_prefix
        with pytest.raises(AssertionError, match="账户为空"):
            _validate_account_prefix(None, "test")

    @patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", True)
    def test_live_unknown_prefix_rejected(self):
        """live 模式 + 异常前缀 → 拒绝。"""
        from backend.services.action.brokers.ibkr import _validate_account_prefix
        with patch("backend.services.action.brokers.ibkr.ENABLE_IBKR_LIVE_TRADING", True):
            with pytest.raises(AssertionError, match="前缀不在允许列表"):
                _validate_account_prefix("X9999999", "test")

    # ── 原有：managedAccounts 边界 ──

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
        assert result.status == "broker_pending"

    def test_hk_market_allowed(self):
        adapter = _make_adapter()
        trade = _make_mock_trade(order_id=43, perm_id=1002)
        adapter._ib.placeOrder.return_value = trade
        result = adapter.place_order(_make_request(symbol="0700:HK"))
        assert result.status == "broker_pending"

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
        assert result.status == "broker_pending"

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


class TestLiveReadOnlyMutationGuard:
    @patch("backend.services.action.brokers.ibkr.IBKR_READ_ONLY_MODE", True)
    def test_place_order_is_rejected_without_gateway_mutation(self):
        adapter = _make_adapter(account_id="U1234567")
        result = adapter.place_order(_make_request())
        assert result.status == "rejected"
        assert "只读模式" in result.raw_response["reason"]
        adapter._ib.placeOrder.assert_not_called()

    @patch("backend.services.action.brokers.ibkr.IBKR_READ_ONLY_MODE", True)
    def test_cancel_order_is_blocked_without_gateway_mutation(self):
        adapter = _make_adapter(account_id="U1234567")
        assert adapter.cancel_order("555") is False
        adapter._ib.cancelOrder.assert_not_called()


class TestCase1ResolvedContractCapabilities:
    @staticmethod
    def _detail(exchange: str):
        detail = MagicMock()
        detail.contract.conId = 272686955
        detail.contract.symbol = "IBTA"
        detail.contract.localSymbol = "IBTA"
        detail.contract.secType = "STK"
        detail.contract.exchange = exchange
        detail.contract.primaryExchange = "LSEETF"
        detail.contract.currency = "USD"
        detail.contract.tradingClass = "EUET"
        detail.stockType = "ETF"
        detail.longName = "ISHARES USD TRSRY 1-3Y USD A"
        isin = MagicMock(tag="ISIN", value="IE00BYXPSP02")
        detail.secIdList = [isin]
        detail.minTick = 0.0001
        detail.validExchanges = "SMART,LSEETF"
        detail.marketRuleIds = "26,1874"
        detail.tradingHours = "20260815:0000-20260815:2359"
        detail.liquidHours = detail.tradingHours
        detail.timeZoneId = "UTC"
        return detail

    def test_resolver_converts_routable_row_to_direct_lse_contract(self):
        adapter = _make_adapter()
        description = MagicMock()
        description.contract.symbol = "IBTA"
        description.contract.localSymbol = "IBTA"
        adapter._ib.reqMatchingSymbolsAsync = AsyncMock(return_value=[description])
        broad = self._detail("SMART")
        direct = self._detail("LSEETF")
        adapter._ib.reqContractDetailsAsync = AsyncMock(side_effect=[[broad], [direct]])
        adapter._ib.qualifyContractsAsync = AsyncMock(return_value=[direct.contract])
        rule = MagicMock(lowEdge=0, increment=0.0001)
        adapter._ib.reqMarketRuleAsync = AsyncMock(return_value=[rule])
        result = adapter.resolve_lse_usd_etf("IBTA")
        assert result["candidate_count"] == 1
        assert result["con_id"] == 272686955
        assert result["exchange"] == "LSEETF"
        assert result["market_rule_id"] == 1874

    def test_resolver_uses_generic_local_symbol_path_for_cbu0(self):
        adapter = _make_adapter()
        description = MagicMock()
        description.contract.symbol = "CBU0"
        description.contract.localSymbol = ""
        wrong = self._detail("SMART")
        wrong.contract.conId = 999
        wrong.contract.symbol = "CBU0"
        wrong.contract.localSymbol = "CBU0"
        wrong.contract.currency = "EUR"
        target = self._detail("LSEETF")
        target.contract.conId = 79000139
        target.contract.symbol = "CSBGU0"
        target.contract.localSymbol = "CBU0"
        target.contract.primaryExchange = "EBS"
        target.longName = "ISHARES USD TRES BOND 7-10Y"
        target.secIdList = [MagicMock(tag="ISIN", value="IE00B3VWN518")]
        target.marketRuleIds = "26,983"
        adapter._ib.reqMatchingSymbolsAsync = AsyncMock(return_value=[description])
        adapter._ib.reqContractDetailsAsync = AsyncMock(
            side_effect=[[wrong], [target], [target]],
        )
        adapter._ib.qualifyContractsAsync = AsyncMock(return_value=[target.contract])
        adapter._ib.reqMarketRuleAsync = AsyncMock(
            return_value=[MagicMock(lowEdge=0, increment=0.0005)],
        )
        result = adapter.resolve_lse_usd_etf("CBU0")
        assert result["con_id"] == 79000139
        assert result["symbol"] == "CSBGU0"
        assert result["local_symbol"] == "CBU0"
        assert result["market_rule_id"] == 983

    def test_resolved_lse_contract_uses_persisted_conid_with_smart_routing(self):
        adapter = _make_adapter()
        trade = _make_mock_trade(order_id=48, perm_id=1008)
        trade.contract.conId = 79000139
        trade.contract.symbol = "CSBGU0"
        trade.contract.exchange = "SMART"
        trade.contract.currency = "USD"
        adapter._ib.placeOrder.return_value = trade
        resolved = {
            "con_id": 79000139, "symbol": "CSBGU0", "local_symbol": "CBU0",
            "sec_type": "STK", "exchange": "SMART",
            "listing_exchange": "LSEETF", "execution_exchange": "SMART",
            "primary_exchange": "EBS",
            "currency": "USD", "trading_class": "EUET",
            "smart_qualification": {
                "con_id": 79000139, "symbol": "CSBGU0",
                "local_symbol": "CSBGU0", "sec_type": "STK",
                "exchange": "SMART", "primary_exchange": "EBS",
                "currency": "USD", "trading_class": "CSBGU0",
            },
        }
        result = adapter.place_order(_make_request(
            symbol="CBU0:LSE", resolved_contract=resolved,
        ))
        contract = adapter._ib.placeOrder.call_args[0][0]
        assert contract.conId == 79000139
        assert contract.symbol == "CSBGU0"
        assert contract.localSymbol == "CSBGU0"
        assert contract.exchange == "SMART"
        assert result.raw_response["con_id"] == 79000139

    def test_smart_qualification_preserves_verified_listing_identity(self):
        adapter = _make_adapter()
        qualified = MagicMock()
        qualified.conId = 79000224
        qualified.symbol = "CSBGU3"
        qualified.localSymbol = "CSBGU3"
        qualified.secType = "STK"
        qualified.exchange = "SMART"
        qualified.primaryExchange = "EBS"
        qualified.currency = "USD"
        qualified.tradingClass = "EUET"
        adapter._ib.qualifyContractsAsync = AsyncMock(return_value=[qualified])
        listing = {
            "con_id": 79000224, "symbol": "CSBGU3", "local_symbol": "CBU3",
            "sec_type": "STK", "stock_type": "ETF", "exchange": "LSEETF",
            "primary_exchange": "EBS", "currency": "USD",
            "trading_class": "EUET", "isin": "IE00B3VWN179",
            "market_rule_id": 983, "market_rule": [],
        }

        result = adapter.qualify_execution_route(listing, exchange="SMART")

        requested = adapter._ib.qualifyContractsAsync.call_args[0][0]
        assert requested.conId == listing["con_id"]
        assert requested.exchange == "SMART"
        assert result["con_id"] == listing["con_id"]
        assert result["isin"] == listing["isin"]
        assert result["listing_exchange"] == "LSEETF"
        assert result["execution_exchange"] == "SMART"
        assert result["exchange"] == "SMART"
        assert result["local_symbol"] == "CBU3"
        assert result["smart_qualification"]["local_symbol"] == "CSBGU3"

    def test_smart_qualification_identity_change_fails_closed(self):
        adapter = _make_adapter()
        qualified = MagicMock()
        qualified.conId = 999
        qualified.symbol = "CSBGU3"
        qualified.localSymbol = "CBU3"
        qualified.secType = "STK"
        qualified.exchange = "SMART"
        qualified.primaryExchange = "EBS"
        qualified.currency = "USD"
        qualified.tradingClass = "EUET"
        adapter._ib.qualifyContractsAsync = AsyncMock(return_value=[qualified])
        listing = {
            "con_id": 79000224, "symbol": "CSBGU3", "local_symbol": "CBU3",
            "sec_type": "STK", "exchange": "LSEETF",
            "primary_exchange": "EBS", "currency": "USD",
            "trading_class": "EUET",
        }

        with pytest.raises(ValueError, match="identity mismatch"):
            adapter.qualify_execution_route(listing, exchange="SMART")

    def test_resolved_contract_mismatch_fails_before_gateway_mutation(self):
        adapter = _make_adapter()
        result = adapter.place_order(_make_request(
            symbol="IBTA:LSE",
            resolved_contract={
                "con_id": 272686955, "symbol": "IBTA", "local_symbol": "IBTA",
                "sec_type": "STK", "exchange": "SMART", "currency": "USD",
            },
        ))
        assert result.status == "rejected"
        assert result.raw_response["action"] == "place_order_blocked_contract_identity"
        adapter._ib.placeOrder.assert_not_called()

    def test_whatif_uses_protocol_preview_flags_without_real_submit(self):
        adapter = _make_adapter()
        state = MagicMock()
        state.commission = 1.23
        state.minCommission = 1
        state.maxCommission = float("1.7976931348623157e308")
        state.commissionCurrency = "USD"
        state.initMarginBefore = 100
        state.initMarginChange = 10
        state.initMarginAfter = 110
        state.maintMarginBefore = 80
        state.maintMarginChange = 8
        state.maintMarginAfter = 88
        state.equityWithLoanBefore = 1000
        state.equityWithLoanChange = -11.23
        state.equityWithLoanAfter = 988.77
        state.warningText = ""
        adapter._ib.whatIfOrderAsync = AsyncMock(return_value=state)
        result = adapter.what_if_limit_order({
            "con_id": 272686955, "symbol": "IBTA", "local_symbol": "IBTA",
            "sec_type": "STK", "exchange": "SMART",
            "listing_exchange": "LSEETF", "execution_exchange": "SMART",
            "primary_exchange": "LSEETF", "currency": "USD",
            "trading_class": "EUET",
            "smart_qualification": {
                "con_id": 272686955, "symbol": "IBTA",
                "local_symbol": "IBTA", "sec_type": "STK",
                "exchange": "SMART", "primary_exchange": "LSEETF",
                "currency": "USD", "trading_class": "EUET",
            },
        }, quantity=10, limit_price=Decimal("5.00"))
        order = adapter._ib.whatIfOrderAsync.call_args[0][1]
        contract = adapter._ib.whatIfOrderAsync.call_args[0][0]
        assert contract.exchange == "SMART"
        assert order.whatIf is True
        assert order.transmit is True
        assert result["what_if"] is True
        assert result["transmit"] is True
        assert result["con_id"] == 272686955
        assert result["listing_exchange"] == "LSEETF"
        assert result["execution_exchange"] == "SMART"
        assert result["commission_currency"] == "USD"
        assert result["commission_reserve"] == 1.23
        assert result["commission_basis"] == "EXACT"
        assert result["min_commission"] == 1
        assert result["max_commission"] is None
        assert result["initial_margin_change"] == 10
        assert result["maintenance_margin_change"] == 8
        assert result["equity_with_loan_change"] == -11.23
        adapter._ib.placeOrder.assert_not_called()
        adapter._ib.openTrades.assert_not_called()

    def test_whatif_uses_max_commission_as_conservative_reserve(self):
        adapter = _make_adapter()
        state = MagicMock()
        state.commission = float("1.7976931348623157e308")
        state.minCommission = 1.99
        state.maxCommission = 4.64030555
        state.commissionCurrency = "USD"
        state.initMarginBefore = state.initMarginChange = state.initMarginAfter = 0
        state.maintMarginBefore = state.maintMarginChange = state.maintMarginAfter = 0
        state.equityWithLoanBefore = state.equityWithLoanChange = 0
        state.equityWithLoanAfter = 0
        state.warningText = ""
        adapter._ib.whatIfOrderAsync = AsyncMock(return_value=state)
        result = adapter.what_if_limit_order({
            "con_id": 79000224, "symbol": "CSBGU3",
            "local_symbol": "CBU3", "sec_type": "STK",
            "exchange": "SMART", "listing_exchange": "LSEETF",
            "execution_exchange": "SMART", "primary_exchange": "EBS",
            "currency": "USD", "trading_class": "EUET",
            "smart_qualification": {
                "con_id": 79000224, "symbol": "CSBGU3",
                "local_symbol": "CSBGU3", "sec_type": "STK",
                "exchange": "SMART", "primary_exchange": "EBS",
                "currency": "USD", "trading_class": "CSBGU3",
            },
        }, quantity=123, limit_price=Decimal("126.29"))
        assert result["commission"] is None
        assert result["min_commission"] == 1.99
        assert result["max_commission"] == 4.64030555
        assert result["commission_reserve"] == 4.64030555
        assert result["commission_basis"] == "MAX_COMMISSION"

    def test_whatif_does_not_infer_missing_commission_currency(self):
        adapter = _make_adapter()
        state = MagicMock()
        state.commission = 1.23
        state.minCommission = float("1.7976931348623157e308")
        state.maxCommission = float("1.7976931348623157e308")
        state.commissionCurrency = ""
        state.initMarginBefore = state.initMarginChange = state.initMarginAfter = 0
        state.maintMarginBefore = state.maintMarginChange = state.maintMarginAfter = 0
        state.equityWithLoanBefore = state.equityWithLoanChange = 0
        state.equityWithLoanAfter = 0
        state.warningText = ""
        adapter._ib.whatIfOrderAsync = AsyncMock(return_value=state)
        result = adapter.what_if_limit_order({
            "con_id": 272686955, "symbol": "IBTA", "local_symbol": "IBTA",
            "sec_type": "STK", "exchange": "LSEETF",
            "primary_exchange": "LSEETF", "currency": "USD",
            "trading_class": "EUET",
        }, quantity=10, limit_price=Decimal("5.00"))
        assert result["commission_currency"] is None
        assert result["min_commission"] is None
        assert result["max_commission"] is None
        adapter._ib.placeOrder.assert_not_called()

    def test_real_order_builder_is_not_whatif_and_transmits(self):
        order = IBKRBrokerAdapter._build_live_limit_order(_make_request())
        assert order.whatIf is False
        assert order.transmit is True
        assert order.outsideRth is False
        assert order.orderRef == "test-order-001"

    def test_market_hours_closed_fails_closed(self):
        adapter = _make_adapter()
        now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
        assert adapter.is_market_open({
            "liquid_hours": "20260815:CLOSED", "time_zone_id": "UTC",
        }, now=now) is False

    def test_quote_without_prices_is_missing_even_if_market_data_type_is_live(self):
        adapter = _make_adapter()
        ticker = MagicMock()
        ticker.marketDataType = 1
        ticker.bid = float("nan")
        ticker.ask = float("nan")
        ticker.last = float("nan")
        ticker.time = datetime(2026, 8, 15, tzinfo=timezone.utc)
        adapter._ib.reqTickersAsync = AsyncMock(return_value=[ticker])
        result = adapter.get_executable_quote({
            "con_id": 272686955, "symbol": "IBTA", "local_symbol": "IBTA",
            "sec_type": "STK", "exchange": "LSEETF",
            "primary_exchange": "LSEETF", "currency": "USD",
            "trading_class": "EUET",
        })
        assert result["quote_quality"] == "MISSING"


class TestDedicatedLoopReadPath:
    """读取必须通过 dedicated event loop，且失败不能伪装为空集合。"""

    @staticmethod
    def _loop_adapter(timeout=0.1):
        adapter = IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
        adapter._timeout = timeout
        adapter._loop = asyncio.new_event_loop()
        adapter._thread = threading.Thread(
            target=adapter._loop.run_forever, daemon=True,
        )
        adapter._thread.start()
        adapter._ib = None
        adapter._connected = False
        adapter._account_id = "DU1234567"
        adapter._client_id = 1
        adapter._error_codes = {}
        return adapter

    @staticmethod
    def _stop_loop_adapter(adapter):
        adapter._loop.call_soon_threadsafe(adapter._loop.stop)
        adapter._thread.join(timeout=1)
        adapter._loop.close()

    def test_run_on_loop_uses_dedicated_thread(self):
        adapter = self._loop_adapter()
        try:
            assert adapter._run_on_loop(threading.get_ident) != threading.get_ident()
        finally:
            self._stop_loop_adapter(adapter)

    def test_run_on_loop_times_out_and_cancels(self):
        adapter = self._loop_adapter(timeout=0.02)

        async def slow_read():
            await asyncio.sleep(1)

        try:
            with pytest.raises(TimeoutError, match="调用超时"):
                adapter._run_on_loop(slow_read)
        finally:
            self._stop_loop_adapter(adapter)

    def test_authenticate_and_read_methods_dispatch_to_loop(self):
        adapter = _make_adapter(account_id="DU1234567")
        adapter._ensure_connected = MagicMock()
        adapter._ib.managedAccounts.return_value = ["DU1234567"]
        adapter._ib.positions.return_value = []
        adapter._ib.openTrades.return_value = []
        adapter._ib.reqOpenOrdersAsync = AsyncMock(return_value=[])
        account_value = MagicMock(tag="NetLiquidation", value="1")
        adapter._ib.accountSummaryAsync = AsyncMock(return_value=[account_value])
        runner = MagicMock(side_effect=adapter._run_on_loop)
        adapter._run_on_loop = runner

        assert adapter.authenticate({}) is True
        assert adapter.get_account_info()["NetLiquidation"] == 1.0
        assert adapter.get_positions() == []
        assert adapter.list_open_orders() == []
        assert runner.call_count == 4

    def test_empty_positions_is_distinct_from_query_failure(self):
        adapter = _make_adapter()
        adapter._ensure_connected = MagicMock()
        adapter._ib.positions.return_value = []
        assert adapter.get_positions() == []

        adapter._run_on_loop = MagicMock(side_effect=TimeoutError("timeout"))
        with pytest.raises(TimeoutError):
            adapter.get_positions()

    def test_empty_orders_is_distinct_from_query_failure(self):
        adapter = _make_adapter()
        adapter._ensure_connected = MagicMock()
        adapter._ib.reqOpenOrdersAsync = AsyncMock(return_value=[])
        adapter._ib.openTrades.return_value = []
        assert adapter.list_open_orders() == []

        adapter._run_on_loop = MagicMock(side_effect=ConnectionError("disconnected"))
        with pytest.raises(ConnectionError):
            adapter.list_open_orders()

    def test_shutdown_clears_loop_state_for_reconnect(self):
        adapter = _make_adapter()
        adapter.shutdown()
        assert adapter._connected is False
        assert adapter._ib is None
        assert adapter._loop is None
        assert adapter._thread is None


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
        assert result.status == "broker_pending"

    def test_cancelled_callback_is_returned_as_cancelled(self):
        adapter = _make_adapter()
        log_entry = _make_mock_log_entry(
            error_code=0, message="Broker cancelled order", status="Cancelled",
        )
        trade = _make_mock_trade(
            order_id=42, perm_id=98765, status="Cancelled", log=[log_entry],
        )
        adapter._ib.placeOrder.return_value = trade

        result = adapter.place_order(_make_request())

        assert result.status == "cancelled"
        assert result.raw_response["ib_status"] == "Cancelled"
        assert result.raw_response["broker_log"][0]["message"] == "Broker cancelled order"
        assert result.raw_response["terminal_reason_source"] == "trade_log"

    def test_cancelled_without_reason_persists_unknown_terminal_reason(self):
        adapter = _make_adapter()
        adapter._ib.placeOrder.return_value = _make_mock_trade(
            order_id=42, perm_id=98765, status="Cancelled",
        )

        result = adapter.place_order(_make_request())

        assert result.status == "cancelled"
        assert result.raw_response["terminal_reason"] == "UNKNOWN_BROKER_CANCEL"
        assert result.raw_response["terminal_reason_source"] == "fallback"

    def test_cancelled_error_and_advanced_reject_are_persisted(self):
        adapter = _make_adapter(error_codes={
            42: {
                "errorCode": 201,
                "errorString": "Order rejected by broker",
                "advancedOrderRejectJson": '{"errorCode":"XYZ"}',
                "timestamp": "2026-08-18T00:00:00+00:00",
            },
        })
        adapter._ib.placeOrder.return_value = _make_mock_trade(
            order_id=42, perm_id=98765, status="Cancelled",
            advanced_error='{"errorCode":"XYZ"}',
        )

        result = adapter.place_order(_make_request())

        raw = result.raw_response
        assert raw["broker_error_code"] == 201
        assert raw["advanced_order_reject_json"] == '{"errorCode":"XYZ"}'
        assert raw["terminal_reason_source"] == "advanced_order_reject_json"

    def test_pending_submit_without_ack_fails_closed_unknown(self):
        adapter = _make_adapter()
        trade = _make_mock_trade(
            order_id=42, perm_id=98765, status="PendingSubmit",
        )
        adapter._ib.placeOrder.return_value = trade

        with (
            patch("backend.services.action.brokers.ibkr.BROKER_ACK_WAIT_SECONDS", 0.01),
            patch("backend.services.action.brokers.ibkr.BROKER_ACK_POLL_INTERVAL", 0.001),
        ):
            result = adapter.place_order(_make_request())

        assert result.status == "unknown"
        assert result.raw_response["broker_ack_timeout"] is True

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
        assert order_arg.account == adapter._account_id

    def test_what_if_and_live_builders_keep_key_fields_in_parity(self):
        request = _make_request(quantity=12, limit_price=Decimal("121.52"))
        live = IBKRBrokerAdapter._build_live_limit_order(request)
        preview = IBKRBrokerAdapter._build_what_if_limit_order(
            quantity=12, limit_price=Decimal("121.52"),
        )
        for field in (
            "action", "orderType", "totalQuantity", "lmtPrice", "tif",
            "outsideRth", "transmit",
        ):
            assert getattr(live, field) == getattr(preview, field)
        assert live.whatIf is False
        assert preview.whatIf is True

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

    def test_inactive_with_rejected_keyword_now_unknown(self):
        """Inactive + errorCode=0 但 message 含拒单关键词 → unknown（降级）。
        keyword 匹配不再判 rejected，降级为 unknown 待人工确认。"""
        adapter = _make_adapter()
        log_entry = _make_mock_log_entry(
            error_code=0, message="Insufficient buying power"
        )
        trade = _make_mock_trade(perm_id=1, status="Inactive", log=[log_entry])
        adapter._ib.trades.return_value = [trade]

        result = adapter.get_order_status("1")
        assert result.status == "unknown"
        assert result.raw_response["inactive_resolved_as"] == "unknown"
        assert "buying power" in result.raw_response["keyword_matched"]
        assert "待人工确认" in result.raw_response["keyword_note"]

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

    def test_inactive_error_code_203_not_in_rejected_codes(self):
        """errorCode=203 未经探针实测，不在 REJECTED_ERROR_CODES 中。
        message 含 "not available" → keyword 降级为 unknown（非 rejected）。"""
        adapter = _make_adapter()
        log_entry = _make_mock_log_entry(
            error_code=203, message="Security is not available"
        )
        trade = _make_mock_trade(perm_id=1, status="Inactive", log=[log_entry])
        adapter._ib.trades.return_value = [trade]

        result = adapter.get_order_status("1")
        # 203 不在码表 + keyword 降级 → unknown
        assert result.status == "unknown"
        assert "not available" in result.raw_response["keyword_matched"]


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
        # 每次 retry 在 dedicated loop 内读取一次 trade snapshot。
        adapter._ib.trades.side_effect = [
            [],       # attempt 0: miss
            [trade],  # attempt 1 (retry): hit
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

    def test_execution_reconciliation_returns_detached_evidence(self):
        adapter = _make_adapter()
        fill = MagicMock()
        fill.execution.orderId = 85
        fill.execution.permId = 1347224986
        fill.execution.orderRef = "local-order-ref"
        fill.execution.execId = "exec-1"
        fill.execution.side = "BOT"
        fill.execution.shares = 3
        fill.execution.price = 126.25
        fill.execution.time = datetime(2026, 8, 18, tzinfo=timezone.utc)
        fill.contract.conId = 79000224
        fill.contract.localSymbol = "CBU3"
        fill.contract.symbol = "CSBGU3"
        adapter._ib.reqExecutionsAsync = AsyncMock(return_value=[fill])

        result = adapter.list_execution_details()

        assert result == [{
            "order_id": 85,
            "perm_id": 1347224986,
            "order_ref": "local-order-ref",
            "exec_id": "exec-1",
            "con_id": 79000224,
            "symbol": "CBU3",
            "side": "BOT",
            "shares": 3.0,
            "price": 126.25,
            "timestamp": "2026-08-18 00:00:00+00:00",
        }]

    def test_parse_symbol_us(self):
        assert IBKRBrokerAdapter._parse_symbol("AAPL:US") == ("US", "AAPL")

    def test_parse_symbol_hk(self):
        assert IBKRBrokerAdapter._parse_symbol("0700:HK") == ("HK", "0700")

    def test_parse_symbol_cn(self):
        assert IBKRBrokerAdapter._parse_symbol("600519:SH") == ("SH", "600519")

    def test_order_attributes_explicit(self):
        """防回归: adapter 构造的 Order 必须显式设 tif/outsideRth/orderType，
        不留空给 TWS preset 覆盖（error 10349 修复）。"""
        adapter = _make_adapter()
        trade = _make_mock_trade(order_id=99, perm_id=9999)
        adapter._ib.placeOrder.return_value = trade

        adapter.place_order(_make_request(symbol="AAPL:US"))

        # 拿到 placeOrder 被调时的 order 参数
        call_args = adapter._ib.placeOrder.call_args
        order = call_args[1].get("order") or call_args[0][1]
        assert order.tif == "DAY", f"tif 不能为空，实际: {order.tif!r}"
        assert order.outsideRth is False
        assert order.orderType == "LMT"


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

    def test_inactive_keyword_fallback_unknown(self):
        """Inactive + 无实测码 + message 含拒单关键词 → unknown（降级，非 rejected）。
        keyword 匹配作为信号保留在 extras，交人工确认。"""
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
        assert mapped == "unknown"
        assert extras["inactive_resolved_as"] == "unknown"
        assert "insufficient" in extras["keyword_matched"]
        assert "待人工确认" in extras["keyword_note"]

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

    def test_on_ib_error_captures_all_submitted_order_errors(self):
        adapter = _make_adapter(submitted_order_ids={8, 9})
        trade = _make_mock_trade(order_id=8, advanced_error='{"reason":"x"}')
        adapter._ib.trades.return_value = [trade]
        adapter._on_ib_error(reqId=8, errorCode=201,
                             errorString="Order rejected", contract=None)
        assert 8 in adapter._error_codes
        assert adapter._error_codes[8]["errorCode"] == 201
        assert adapter._error_codes[8]["advancedOrderRejectJson"] == '{"reason":"x"}'
        adapter._on_ib_error(reqId=8, errorCode=399,
                             errorString="Order precaution", contract=None)
        assert [item["errorCode"] for item in adapter._error_codes[8]["history"]] == [201, 399]

        # 202 cancellation reason is evidence and must not be discarded.
        adapter._on_ib_error(reqId=9, errorCode=202,
                             errorString="Order Canceled - Reason", contract=None)
        assert adapter._error_codes[9]["errorCode"] == 202

        # Non-order and unrelated request errors remain excluded.
        adapter._on_ib_error(reqId=-1, errorCode=201,
                             errorString="something", contract=None)
        assert -1 not in adapter._error_codes
        adapter._on_ib_error(reqId=10, errorCode=399,
                             errorString="unrelated request", contract=None)
        assert 10 not in adapter._error_codes

    def test_duplicate_order_id_error_fails_closed_with_reason(self):
        adapter = _make_adapter(error_codes={
            42: {
                "errorCode": 103,
                "errorString": "Duplicate order id",
                "timestamp": "2026-08-18T00:00:00+00:00",
            },
        })
        adapter._ib.placeOrder.return_value = _make_mock_trade(
            order_id=42, perm_id=0, status="Cancelled",
        )

        result = adapter.place_order(_make_request())

        assert result.status == "cancelled"
        assert result.raw_response["broker_error_code"] == 103
        assert result.raw_response["terminal_reason"] == "Duplicate order id"

    def test_open_order_hook_captures_warning_and_completed_state(self):
        adapter = _make_adapter()
        class Wrapper:
            def openOrder(self, order_id, contract, order, order_state):
                return None

        wrapper = Wrapper()
        adapter._ib.wrapper = wrapper
        adapter._install_open_order_evidence_hook()
        state = MagicMock(
            status="Cancelled", warningText="precaution",
            completedTime="20260817 21:19:50", completedStatus="Cancelled",
        )
        order = MagicMock(account="DU1234567")

        wrapper.openOrder(42, MagicMock(), order, state)

        evidence = adapter._open_order_evidence[42][-1]
        assert evidence["warning_text"] == "precaution"
        assert evidence["completed_status"] == "Cancelled"
        assert evidence["account_masked"] == "***4567"

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
