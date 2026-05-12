"""TigerAdapter 单元测试。"""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from services.broker_sync.tiger.adapter import TigerAdapter


def make_mock_position(symbol, currency, sec_type, quantity, avg_cost, market_price,
                        market_value, unrealized_pnl, unrealized_pnl_percent,
                        name="测试", realized_pnl=Decimal("0"), today_pnl=None):
    """构造模拟的老虎 SDK Position 对象。"""
    contract = SimpleNamespace(
        symbol=symbol,
        currency=currency,
        sec_type=sec_type,
        name=name,
    )
    pos = SimpleNamespace(
        contract=contract,
        quantity=quantity,
        average_cost=avg_cost,
        market_price=market_price,
        market_value=market_value,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_percent=unrealized_pnl_percent,
        realized_pnl=realized_pnl,
        today_pnl=today_pnl,
    )
    return pos


def test_us_equity_normalization():
    """美股代码归一化: AAPL + USD → AAPL:US"""
    adapter = TigerAdapter(account_id="4472659")
    sdk_pos = make_mock_position(
        symbol="AAPL", currency="USD", sec_type="STK",
        quantity=60, avg_cost=Decimal("211.83"),
        market_price=Decimal("276.45"),
        market_value=Decimal("16587.00"),
        unrealized_pnl=Decimal("3877.20"),
        unrealized_pnl_percent=Decimal("30.5"),
        name="苹果",
    )
    result = adapter.to_position(sdk_pos)
    assert result.symbol == "AAPL:US"
    assert result.market == "US"
    assert result.asset_class == "equity"
    assert result.broker == "tiger"
    assert result.cost_basis == Decimal("60") * Decimal("211.83")


def test_hk_equity_zero_padding():
    """港股代码补零到 4 位: 00068 + HKD → 0068:HK"""
    adapter = TigerAdapter(account_id="4472659")
    sdk_pos = make_mock_position(
        symbol="00068", currency="HKD", sec_type="STK",
        quantity=500, avg_cost=Decimal("7.62"),
        market_price=Decimal("24.76"),
        market_value=Decimal("12380.00"),
        unrealized_pnl=Decimal("8570.00"),
        unrealized_pnl_percent=Decimal("224.9"),
        name="MANYCORE TECH",
    )
    result = adapter.to_position(sdk_pos)
    assert result.symbol == "0068:HK"
    assert result.market == "HK"
    assert result.currency == "HKD"


def test_leading_quote_stripped():
    """前导单引号必须去掉: 'AAPL → AAPL:US"""
    adapter = TigerAdapter(account_id="4472659")
    sdk_pos = make_mock_position(
        symbol="'AAPL", currency="USD", sec_type="STK",
        quantity=10, avg_cost=Decimal("200"),
        market_price=Decimal("276"),
        market_value=Decimal("2760"),
        unrealized_pnl=Decimal("760"),
        unrealized_pnl_percent=Decimal("38"),
    )
    result = adapter.to_position(sdk_pos)
    assert result.raw_symbol == "AAPL"
    assert result.symbol == "AAPL:US"
