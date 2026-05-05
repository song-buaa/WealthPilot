"""FutuAdapter 单元测试（不依赖 OpenD）。"""
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd

from services.broker_sync.futu.adapter import FutuAdapter


def make_mock_row(
    code="US.QQQ",
    stock_name="纳指100ETF-Invesco",
    qty=10,
    cost_price=500.0,
    nominal_price=600.0,
    market_val=6000.0,
    unrealized_pl=1000.0,
    pl_ratio=20.0,
    realized_pl=0.0,
    today_pl_val=50.0,
    can_sell_qty=10,
    currency="USD",
) -> pd.Series:
    return pd.Series({
        "code": code,
        "stock_name": stock_name,
        "qty": qty,
        "cost_price": cost_price,
        "nominal_price": nominal_price,
        "market_val": market_val,
        "unrealized_pl": unrealized_pl,
        "pl_ratio": pl_ratio,
        "realized_pl": realized_pl,
        "today_pl_val": today_pl_val,
        "can_sell_qty": can_sell_qty,
        "currency": currency,
    })


def test_us_symbol_normalization():
    """US.QQQ → QQQ.US"""
    adapter = FutuAdapter(account_id="6169")
    row = make_mock_row(code="US.QQQ")
    pos = adapter.row_to_position(row)
    assert pos.symbol == "QQQ.US"
    assert pos.market == "US"
    assert pos.raw_symbol == "QQQ"


def test_hk_symbol_zero_padding():
    """HK.68 → 00068.HK（补零到 5 位）"""
    adapter = FutuAdapter(account_id="6169")
    row = make_mock_row(code="HK.68", currency="HKD")
    pos = adapter.row_to_position(row)
    assert pos.symbol == "00068.HK"
    assert pos.market == "HK"


def test_pl_ratio_converted_from_percent_to_decimal():
    """pl_ratio=20.0 → unrealized_pnl_pct=0.20（百分数→小数）"""
    adapter = FutuAdapter(account_id="6169")
    row = make_mock_row(pl_ratio=20.0)
    pos = adapter.row_to_position(row)
    assert pos.unrealized_pnl_pct == Decimal("0.20")


def test_cost_method_is_weighted_average():
    """富途成本方式是 weighted_average"""
    adapter = FutuAdapter(account_id="6169")
    row = make_mock_row()
    pos = adapter.row_to_position(row)
    assert pos.cost_method == "weighted_average"


def test_broker_is_futu():
    adapter = FutuAdapter(account_id="6169")
    row = make_mock_row()
    pos = adapter.row_to_position(row)
    assert pos.broker == "futu"
    assert pos.account_id == "6169"


def test_default_asset_class_is_equity():
    """富途 DataFrame 无 stock_type 列,默认 equity"""
    adapter = FutuAdapter(account_id="6169")
    row = make_mock_row()
    pos = adapter.row_to_position(row)
    assert pos.asset_class == "equity"
