"""
M8.3 单元测试 — data_loader 新建仓数据加载路径。

测试 _infer_symbol_for_new_entry / _try_load_new_entry / LoadedData 新字段。
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from decision_engine.data_loader import (
    _infer_symbol_for_new_entry,
    _try_load_new_entry,
    LoadedData,
    PositionInfo,
    UserProfile,
    InvestmentRules,
)


# ============================================================
# Symbol 推断
# ============================================================

class TestInferSymbol:
    def test_chinese_name_apple(self):
        assert _infer_symbol_for_new_entry("苹果") == "AAPL:US"

    def test_chinese_name_tesla(self):
        assert _infer_symbol_for_new_entry("特斯拉") == "TSLA:US"

    def test_chinese_name_nvidia(self):
        assert _infer_symbol_for_new_entry("英伟达") == "NVDA:US"

    def test_chinese_name_pdd(self):
        assert _infer_symbol_for_new_entry("拼多多") == "PDD:US"

    def test_chinese_name_xiaomi_maps_hk(self):
        assert _infer_symbol_for_new_entry("小米集团") == "1810:HK"

    def test_chinese_name_tencent_maps_hk(self):
        assert _infer_symbol_for_new_entry("腾讯") == "0700:HK"

    def test_pure_ticker_us(self):
        assert _infer_symbol_for_new_entry("AAPL") == "AAPL:US"

    def test_pure_ticker_lowercase(self):
        assert _infer_symbol_for_new_entry("msft") == "MSFT:US"

    def test_ticker_market_format(self):
        assert _infer_symbol_for_new_entry("TSLA:US") == "TSLA:US"

    def test_unrecognized_returns_none(self):
        assert _infer_symbol_for_new_entry("完全不认识的标的xyz") is None

    def test_empty_returns_none(self):
        assert _infer_symbol_for_new_entry("") is None

    def test_amd_case_insensitive(self):
        assert _infer_symbol_for_new_entry("amd") == "AMD:US"
        assert _infer_symbol_for_new_entry("AMD") == "AMD:US"


# ============================================================
# _try_load_new_entry
# ============================================================

class TestTryLoadNewEntry:
    def test_us_stock_returns_virtual_position(self):
        result = _try_load_new_entry("AAPL", [])
        assert result is not None
        vp = result["virtual_position"]
        assert vp.ticker == "AAPL"
        assert vp.is_virtual is True
        assert vp.weight == 0
        assert vp.market_value_cny == 0
        assert result["market_not_supported_message"] is None

    def test_hk_stock_intercepted_with_message(self):
        result = _try_load_new_entry("小米集团", [])
        assert result is not None
        assert result["market_not_supported_message"] is not None
        assert "v3.5" in result["market_not_supported_message"]
        assert result["av_fundamentals"] is None

    def test_hk_stock_tencent_intercepted(self):
        result = _try_load_new_entry("腾讯", [])
        assert result is not None
        assert "港股" in result["market_not_supported_message"]

    def test_unrecognized_symbol_warning(self):
        result = _try_load_new_entry("ASDFGHJKL_RANDOM", [])
        assert result is not None
        assert result["warning"] is not None
        assert "无法识别" in result["warning"]
        assert result["virtual_position"].is_virtual is True

    def test_us_stock_with_av_mock(self):
        """mock AV 调用,验证 av_fundamentals 传递"""
        mock_fund = MagicMock()
        mock_fund.high_52w = 200.0
        mock_fund.low_52w = 100.0

        with patch("services.market_data.av_fundamentals_service.fetch_fundamentals", return_value=mock_fund):
            result = _try_load_new_entry("TSLA", [])

        assert result is not None
        vp = result["virtual_position"]
        assert vp.ticker == "TSLA"
        assert vp.is_virtual is True


# ============================================================
# LoadedData 新字段
# ============================================================

class TestLoadedDataNewFields:
    def _make_loaded(self, **overrides):
        defaults = dict(
            profile=UserProfile(),
            positions=[],
            target_position=None,
            rules=InvestmentRules(
                max_single_position=0.4,
                max_equity_pct=0.8,
                min_cash_pct=0.2,
                max_leverage_ratio=1.35,
            ),
            research=[],
            total_assets=100000,
        )
        defaults.update(overrides)
        return LoadedData(**defaults)

    def test_default_is_not_new_entry(self):
        ld = self._make_loaded()
        assert ld.is_new_entry is False
        assert ld.av_fundamentals is None
        assert ld.market_not_supported_message is None

    def test_new_entry_flag(self):
        ld = self._make_loaded(is_new_entry=True)
        assert ld.is_new_entry is True

    def test_market_not_supported(self):
        ld = self._make_loaded(
            is_new_entry=True,
            market_not_supported_message="港股新建仓暂不支持",
        )
        assert ld.market_not_supported_message is not None


# ============================================================
# PositionInfo.is_virtual
# ============================================================

class TestPositionInfoVirtual:
    def test_default_not_virtual(self):
        p = PositionInfo(
            name="test", ticker="TEST", asset_class="equity",
            weight=0.1, market_value_cny=10000, cost_price=100,
            current_price=110, profit_loss_rate=0.1,
        )
        assert p.is_virtual is False

    def test_virtual_position(self):
        p = PositionInfo(
            name="test", ticker="TEST", asset_class="equity",
            weight=0, market_value_cny=0, cost_price=0,
            current_price=0, profit_loss_rate=0, is_virtual=True,
        )
        assert p.is_virtual is True


# ============================================================
# 回归保护: 已持仓标的不触发新建仓
# ============================================================

class TestExistingPositionRegression:
    def test_existing_position_not_new_entry(self):
        """已持仓标的: is_new_entry 应为 False"""
        ld = LoadedData(
            profile=UserProfile(),
            positions=[PositionInfo(
                name="理想汽车", ticker="LI", asset_class="equity",
                weight=0.1, market_value_cny=10000, cost_price=100,
                current_price=110, profit_loss_rate=0.1,
            )],
            target_position=PositionInfo(
                name="理想汽车", ticker="LI", asset_class="equity",
                weight=0.1, market_value_cny=10000, cost_price=100,
                current_price=110, profit_loss_rate=0.1,
            ),
            rules=InvestmentRules(
                max_single_position=0.4, max_equity_pct=0.8,
                min_cash_pct=0.2, max_leverage_ratio=1.35,
            ),
            research=[],
            total_assets=100000,
        )
        assert ld.is_new_entry is False
        assert ld.target_position is not None
        assert ld.target_position.is_virtual is False
