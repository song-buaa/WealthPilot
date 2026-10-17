"""SDK contract tests on detached synthetic trades, no sockets or Gateway."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from ib_async import Contract, Order, OrderStatus, Trade, ExecutionFilter
from backend.services.action.brokers.ibkr import IBKRBrokerAdapter
from backend.services.action.brokers.base import OrderRequest


def adapter():
    result = IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
    result._account_id = "DU-SYNTHETIC"
    result._client_id = 1
    result._timeout = .05
    result._connected = True
    result._ib = MagicMock()
    result._ib.reqAllOpenOrdersAsync = AsyncMock(return_value=[])
    result._ib.reqExecutionsAsync = AsyncMock(return_value=[])
    result._ib.reqCompletedOrdersAsync = AsyncMock(return_value=[])
    result._open_order_evidence = {}
    result._error_codes = {}
    result._submitted_order_ids = set()
    result._run_on_loop = lambda fn, **kw: asyncio.run(fn())
    return result


def completed_trade(account="DU-SYNTHETIC", ref="stable-order-ref", filled=10, perm_id=99):
    return Trade(Contract(conId=354802220, symbol="IB01", exchange="SMART", currency="USD"),
        Order(orderId=1, permId=perm_id, orderRef=ref, account=account, action="BUY", orderType="LMT",
              totalQuantity=10, filledQuantity=filled, lmtPrice=100, tif="DAY"),
        OrderStatus(status="Filled", filled=0, avgFillPrice=0))


def fill(account="DU-SYNTHETIC", ref="stable-order-ref", fee_received=False):
    return SimpleNamespace(execution=SimpleNamespace(orderId=1, permId=99, orderRef=ref, execId="exec-1",
        acctNumber=account, clientId=2, shares=10, price=100, cumQty=10, avgPrice=100,
        side="BOT", time=datetime.now(timezone.utc)),
        contract=SimpleNamespace(conId=354802220, localSymbol="IB01", symbol="IB01", currency="USD"),
        commissionReport=SimpleNamespace(execId="exec-1" if fee_received else "", commission=0,
                                         currency="USD" if fee_received else ""))


def test_completed_after_restart_uses_filled_quantity_not_sdk_default_zero():
    ib = adapter()
    ib._ib.trades.side_effect = AssertionError("session cache is not recovery evidence")
    ib._ib.reqCompletedOrdersAsync.return_value = [completed_trade()]
    observed = ib.find_order_by_ref("stable-order-ref")
    assert observed.status == "filled" and observed.filled_quantity == 10
    assert observed.avg_filled_price is None  # no fabricated broker average
    ib._ib.reqCompletedOrdersAsync.assert_awaited_once_with(apiOnly=False)
    assert observed.raw_response["observed_account_id"] == ib._account_id


def test_executions_account_filter_before_sanitization_and_unknown_zero_fee():
    ib = adapter()
    ib._ib.reqExecutionsAsync.return_value = [fill(), fill(account="OTHER-ACCOUNT")]
    rows = ib.list_execution_details()
    assert len(rows) == 1 and rows[0]["cum_quantity"] == 10 and rows[0]["avg_price"] == 100
    assert rows[0]["commission"] is None and rows[0]["commission_currency"] is None
    sent = ib._ib.reqExecutionsAsync.call_args.args[0]
    assert isinstance(sent, ExecutionFilter) and sent.acctCode == ib._account_id
    ib._ib.reqExecutionsAsync.return_value = [fill(fee_received=True)]
    assert ib.list_execution_details()[0]["commission"] == 0  # only a received exact exec report proves zero


def test_other_account_same_ref_is_not_a_match_and_completed_failure_keeps_execution():
    ib = adapter()
    ib._ib.reqAllOpenOrdersAsync.return_value = [completed_trade(account="OTHER-ACCOUNT")]
    ib._ib.reqExecutionsAsync.return_value = [fill()]
    ib._ib.reqCompletedOrdersAsync.side_effect = TimeoutError("synthetic")
    observed = ib.find_order_by_ref("stable-order-ref")
    assert observed.filled_quantity == 10
    assert observed.raw_response["recovery_sources"]["completed"] == "UNAVAILABLE"
    ib._ib.disconnect.assert_called_once()


def test_known_perm_id_can_recover_missing_ref_but_never_fuzzy_identity():
    ib = adapter()
    ib._ib.reqExecutionsAsync.return_value = [fill(ref="")]
    assert ib.find_order_by_ref("stable-order-ref") is None
    assert ib.recover_order("stable-order-ref", perm_id=99).filled_quantity == 10
    assert ib.recover_order("stable-order-ref", perm_id=100) is None


def test_ambiguous_same_ref_is_quarantined_and_zero_client_never_connects():
    ib = adapter()
    ib._ib.reqCompletedOrdersAsync.return_value = [completed_trade(), completed_trade(perm_id=100)]
    assert ib.find_order_by_ref("stable-order-ref").raw_response["execution_correction"] is True
    ib._client_id = 0
    with pytest.raises(ConnectionError, match="NONZERO_CLIENT"):
        ib._ensure_connected()
    ib._ib.connectAsync.assert_not_called()


@pytest.mark.parametrize("tif", ["DAY", "GTC"])
def test_generic_ibkr_preserves_explicit_tif(tif):
    order = IBKRBrokerAdapter._build_live_limit_order(OrderRequest(
        "AAPL:US", "BUY", 10, "LIMIT", limit_price=Decimal("100"), time_in_force=tif,
        local_order_id="stable-order-ref"))
    assert order.tif == tif and order.orderRef == "stable-order-ref"
    assert order.whatIf is False and order.transmit is True


@pytest.mark.parametrize("tif", ["DAY", "GTC"])
def test_tiger_sdk_receives_confirmed_tif_and_expired_claim_never_sends(tif):
    from backend.services.action.brokers.tiger import TigerBrokerAdapter
    import time
    tiger = TigerBrokerAdapter.__new__(TigerBrokerAdapter)
    tiger._account_id = "synthetic-account"
    tiger._is_paper = True
    tiger._trade_client = MagicMock()
    request = OrderRequest("AAPL:US", "BUY", 10, "LIMIT", limit_price=Decimal("100"),
        time_in_force=tif, local_order_id="stable-order-ref", broker_account_id=tiger._account_id)
    tiger.place_order(request)
    submitted = tiger._trade_client.place_order.call_args.args[0]
    assert submitted.time_in_force == tif
    tiger._trade_client.place_order.reset_mock()
    request.dispatch_deadline = time.time() - 1
    with pytest.raises(TimeoutError, match="DEADLINE_EXPIRED"):
        tiger.place_order(request)
    tiger._trade_client.place_order.assert_not_called()


def test_fractional_broker_cumulative_quantity_is_not_silently_floored():
    ib = adapter()
    trade = completed_trade()
    trade.orderStatus.status = "Submitted"
    trade.orderStatus.filled = 1.5
    snapshot = ib._snapshot_trade(trade)
    assert snapshot["filled_quantity"] == 1.5
