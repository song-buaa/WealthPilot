"""SnowballAdapter 单元测试。"""
from decimal import Decimal
from types import SimpleNamespace

from services.broker_sync.snowball.adapter import SnowballAdapter


def make_item(
    symbol="LI",
    exchange="USEX",
    security_type="STK",
    position=300.0,
    average_price=31.27784,
    market_price=17.63,
    realized_pnl=0.0,
    account_id="U3831209",
    contract_id=436980133,
):
    return SimpleNamespace(
        symbol=symbol,
        exchange=exchange,
        security_type=security_type,
        position=position,
        average_price=average_price,
        market_price=market_price,
        realized_pnl=realized_pnl,
        account_id=account_id,
        contract_id=contract_id,
    )


def test_us_symbol_normalization():
    """LI + USEX → LI:US"""
    adapter = SnowballAdapter(account_id="U3831209")
    pos = adapter.item_to_position(make_item(symbol="LI", exchange="USEX"))
    assert pos.symbol == "LI:US"
    assert pos.market == "US"
    assert pos.currency == "USD"


def test_hk_symbol_zero_padding():
    """700 + HKEX → 0700:HK"""
    adapter = SnowballAdapter(account_id="U3831209")
    pos = adapter.item_to_position(make_item(symbol="700", exchange="HKEX"))
    assert pos.symbol == "0700:HK"
    assert pos.currency == "HKD"


def test_market_value_calculated():
    """market_value = position * market_price"""
    adapter = SnowballAdapter(account_id="U3831209")
    pos = adapter.item_to_position(make_item(position=300.0, market_price=17.63))
    assert pos.market_value == Decimal("300") * Decimal("17.63")


def test_unrealized_pnl_calculated():
    """unrealized_pnl = (market_price - average_price) * position"""
    adapter = SnowballAdapter(account_id="U3831209")
    pos = adapter.item_to_position(make_item(position=300.0, average_price=31.27784, market_price=17.63))
    expected_pnl = (Decimal("17.63") - Decimal("31.27784")) * Decimal("300")
    assert abs(pos.unrealized_pnl - expected_pnl) < Decimal("0.01")


def test_unrealized_pnl_pct_calculated():
    """unrealized_pnl_pct = (market_price - avg_cost) / avg_cost"""
    adapter = SnowballAdapter(account_id="U3831209")
    pos = adapter.item_to_position(make_item(average_price=31.27784, market_price=17.63))
    expected_pct = (Decimal("17.63") - Decimal("31.27784")) / Decimal("31.27784")
    assert abs(pos.unrealized_pnl_pct - expected_pct) < Decimal("0.0001")


def test_cost_method_is_weighted_average():
    adapter = SnowballAdapter(account_id="U3831209")
    pos = adapter.item_to_position(make_item())
    assert pos.cost_method == "weighted_average"


def test_broker_is_snowball():
    adapter = SnowballAdapter(account_id="U3831209")
    pos = adapter.item_to_position(make_item())
    assert pos.broker == "snowball"
