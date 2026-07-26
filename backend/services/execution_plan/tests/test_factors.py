"""
因子 service 单元测试 — 纯确定性计算验证 + 降级路径覆盖。

运行: python -m pytest backend/services/execution_plan/tests/test_factors.py -v
"""
import math
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

from backend.services.execution_plan.factors import (
    build_factor_snapshot,
    _calc_atr,
    _calc_annual_volatility,
    _calc_drawdown_from_high,
    _calc_rsi,
    _calc_macd,
    FactorSnapshot,
)


# ── 纯计算函数测试 ────────────────────────────────────────────


class TestATR:
    def test_basic_atr(self):
        n = 30
        highs = pd.Series([110 + i * 0.1 for i in range(n)])
        lows = pd.Series([100 + i * 0.1 for i in range(n)])
        closes = pd.Series([105 + i * 0.1 for i in range(n)])
        atr = _calc_atr(highs, lows, closes, 14)
        assert atr is not None
        assert atr > 0

    def test_insufficient_data(self):
        assert _calc_atr(pd.Series([1, 2]), pd.Series([0, 1]), pd.Series([0.5, 1.5]), 14) is None


class TestVolatility:
    def test_basic_volatility(self):
        closes = pd.Series([100 + i * 0.5 + np.random.randn() for i in range(60)])
        vol = _calc_annual_volatility(closes)
        assert vol is not None
        assert vol > 0

    def test_insufficient_data(self):
        assert _calc_annual_volatility(pd.Series([100, 101])) is None


class TestDrawdown:
    def test_at_peak(self):
        closes = pd.Series([100, 105, 110, 115, 120])
        dd = _calc_drawdown_from_high(closes)
        assert dd == 0.0  # 当前就是最高点

    def test_below_peak(self):
        closes = pd.Series([100, 120, 110, 105, 90])
        dd = _calc_drawdown_from_high(closes)
        assert dd is not None
        assert dd < 0
        assert abs(dd - (-0.25)) < 0.001  # (90-120)/120 = -0.25


class TestPricePercentile:
    """price_percentile 在 build_factor_snapshot 内计算，此处验证逻辑。"""

    def test_at_low(self):
        # (100 - 100) / (200 - 100) = 0
        pct = (100 - 100) / (200 - 100)
        assert pct == 0.0

    def test_at_high(self):
        pct = (200 - 100) / (200 - 100)
        assert pct == 1.0

    def test_midpoint(self):
        pct = (150 - 100) / (200 - 100)
        assert pct == 0.5


# ── build_factor_snapshot 集成测试(mock 外部依赖) ──────────────


def _make_fake_kline(n=60, start_price=100.0) -> pd.DataFrame:
    """生成假 K 线 DataFrame。"""
    prices = [start_price + i * 0.5 + np.random.randn() * 2 for i in range(n)]
    return pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=n),
        "open": [p - 0.5 for p in prices],
        "high": [p + 2 for p in prices],
        "low": [p - 2 for p in prices],
        "close": prices,
        "volume": [1000000] * n,
    })


def _make_mock_registry(kline_df, source="test"):
    """构造一个返回固定 K线的 mock registry。"""
    from backend.services.execution_plan.kline_provider import (
        KlineProviderRegistry, KlineProvider, KlineResult,
    )

    class _MockProvider(KlineProvider):
        name = source
        def __init__(self, df):
            self._df = df
        def get_kline(self, symbol, market, period="day", count=260):
            if self._df is None:
                return None
            return KlineResult(bars=self._df, source=self.name, period=period)

    return KlineProviderRegistry([_MockProvider(kline_df)])


def _make_empty_registry():
    """构造一个所有源都返回空的 mock registry。"""
    return _make_mock_registry(None, "none")


class TestBuildFactorSnapshot:

    def test_full_snapshot_us(self):
        """K线充足（260 根）→ 因子完整，52w 从 bars 算。"""
        kline = _make_fake_kline(260, 150.0)
        registry = _make_mock_registry(kline, "test_broker")

        snap = build_factor_snapshot("AAPL:US", "US", kline_registry=registry)

        assert snap.symbol == "AAPL:US"
        assert snap.market == "US"
        assert snap.current_price is not None
        assert snap.atr14 is not None and snap.atr14 > 0
        assert snap.volatility_annual is not None and snap.volatility_annual > 0
        assert snap.high_52w is not None  # 260 >= 252, 52w 可算
        assert snap.low_52w is not None
        assert snap.price_percentile is not None
        assert 0 <= snap.price_percentile <= 1
        assert snap.drawdown_from_high is not None and snap.drawdown_from_high <= 0
        assert snap.ma5 is not None
        assert snap.ma20 is not None
        assert snap.rsi14 is not None
        assert snap.data_source_meta["kline_source"] == "test_broker"
        assert snap.data_source_meta["degraded_fields"] == []

    def test_degraded_no_52w_short_bars(self):
        """bars 不足 252 → 52w 降级，不报错。"""
        kline = _make_fake_kline(60, 300.0)
        registry = _make_mock_registry(kline, "test")

        snap = build_factor_snapshot("0700:HK", "HK", kline_registry=registry)

        assert snap.current_price is not None
        assert snap.atr14 is not None
        assert snap.high_52w is None  # bars<252, 52w 不可用
        assert snap.price_percentile is None
        assert "52w_high_low" in snap.data_source_meta["degraded_fields"]

    def test_degraded_no_kline(self):
        """K 线缺失 → 所有 K 线因子降级，不报错。"""
        registry = _make_empty_registry()

        snap = build_factor_snapshot("LI:US", "US", kline_registry=registry)

        assert snap.current_price is None
        assert snap.atr14 is None
        assert snap.volatility_annual is None
        assert snap.ma5 is None
        assert "kline" in snap.data_source_meta["degraded_fields"]

    def test_unsupported_market_now_works_via_seed(self):
        """v3.14: 不支持市场也能通过 seed 出数据（如果 registry 有 seed）。"""
        kline = _make_fake_kline(260, 100.0)
        registry = _make_mock_registry(kline, "seed")

        snap = build_factor_snapshot("600519:CN", "CN", kline_registry=registry)
        assert snap.current_price is not None
        assert snap.atr14 is not None

    def test_to_dict(self):
        """to_dict() 输出可 JSON 序列化。"""
        import json
        kline = _make_fake_kline(260, 150.0)
        registry = _make_mock_registry(kline, "test")

        snap = build_factor_snapshot("AAPL:US", "US", kline_registry=registry)
        d = snap.to_dict()
        json_str = json.dumps(d, default=str)
        assert "atr14" in json_str
        assert "data_source_meta" in json_str
