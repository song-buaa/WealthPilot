"""
M8.1 单元测试 — _is_asset_unambiguous / _is_asset_in_portfolio 拆分验证。

核心场景: asset="小米集团" + 持仓无小米 → unambiguous=True, in_portfolio=False
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from backend.services.decision_service import (
    _is_asset_clear,
    _is_asset_unambiguous,
    _is_asset_in_portfolio,
    VAGUE_ASSET_WORDS,
)


@dataclass
class FakePosition:
    name: str
    ticker: Optional[str] = None


# ============================================================
# _is_asset_unambiguous
# ============================================================

class TestIsAssetUnambiguous:
    def test_normal_name(self):
        assert _is_asset_unambiguous("小米集团") is True

    def test_ticker(self):
        assert _is_asset_unambiguous("AAPL") is True

    def test_chinese_ticker(self):
        assert _is_asset_unambiguous("理想汽车") is True

    def test_none(self):
        assert _is_asset_unambiguous(None) is False

    def test_empty(self):
        assert _is_asset_unambiguous("") is False

    def test_whitespace(self):
        assert _is_asset_unambiguous("   ") is False

    def test_vague_word_stock(self):
        assert _is_asset_unambiguous("股票") is False

    def test_vague_word_fund(self):
        assert _is_asset_unambiguous("基金") is False

    def test_vague_word_that_one(self):
        assert _is_asset_unambiguous("那只") is False

    def test_vague_word_this_one(self):
        assert _is_asset_unambiguous("这个") is False

    def test_vague_word_position(self):
        assert _is_asset_unambiguous("持仓") is False

    def test_short_non_vague(self):
        assert _is_asset_unambiguous("LI") is True

    def test_all_vague_words_rejected(self):
        for word in VAGUE_ASSET_WORDS:
            assert _is_asset_unambiguous(word) is False, f"应拒绝模糊词: {word}"


# ============================================================
# _is_asset_in_portfolio
# ============================================================

class TestIsAssetInPortfolio:
    POSITIONS = [
        FakePosition(name="理想汽车", ticker="LI"),
        FakePosition(name="苹果公司", ticker="AAPL"),
        FakePosition(name="腾讯控股", ticker="00700"),
    ]

    def test_name_match(self):
        assert _is_asset_in_portfolio("理想汽车", self.POSITIONS) is True

    def test_partial_name_match(self):
        assert _is_asset_in_portfolio("理想", self.POSITIONS) is True

    def test_ticker_match(self):
        assert _is_asset_in_portfolio("LI", self.POSITIONS) is True

    def test_ticker_case_insensitive(self):
        assert _is_asset_in_portfolio("aapl", self.POSITIONS) is True

    def test_hk_ticker_match(self):
        assert _is_asset_in_portfolio("00700", self.POSITIONS) is True

    def test_not_in_portfolio(self):
        assert _is_asset_in_portfolio("小米集团", self.POSITIONS) is False

    def test_not_in_portfolio_ticker(self):
        assert _is_asset_in_portfolio("01810", self.POSITIONS) is False

    def test_none(self):
        assert _is_asset_in_portfolio(None, self.POSITIONS) is False

    def test_empty(self):
        assert _is_asset_in_portfolio("", self.POSITIONS) is False

    def test_empty_positions(self):
        assert _is_asset_in_portfolio("小米", []) is False


# ============================================================
# _is_asset_clear backward compat
# ============================================================

class TestIsAssetClearBackwardCompat:
    POSITIONS = [
        FakePosition(name="理想汽车", ticker="LI"),
    ]

    def test_in_portfolio_and_unambiguous(self):
        """在持仓 + 名称明确 → True (旧行为不变)"""
        assert _is_asset_clear("理想汽车", self.POSITIONS) is True

    def test_not_in_portfolio(self):
        """不在持仓 → False (旧行为不变)"""
        assert _is_asset_clear("小米集团", self.POSITIONS) is False

    def test_vague_word(self):
        """模糊词 → False (旧行为不变)"""
        assert _is_asset_clear("股票", self.POSITIONS) is False

    def test_none(self):
        assert _is_asset_clear(None, self.POSITIONS) is False

    def test_equals_unambiguous_and_in_portfolio(self):
        """_is_asset_clear 等于 unambiguous AND in_portfolio"""
        test_cases = [
            ("理想汽车", self.POSITIONS),
            ("小米集团", self.POSITIONS),
            ("股票", self.POSITIONS),
            (None, self.POSITIONS),
            ("LI", self.POSITIONS),
            ("MSFT", []),
        ]
        for asset, positions in test_cases:
            expected = _is_asset_unambiguous(asset) and _is_asset_in_portfolio(asset, positions)
            actual = _is_asset_clear(asset, positions)
            assert actual == expected, f"asset={asset}: clear={actual} != unambiguous&in_portfolio={expected}"


# ============================================================
# 核心新建仓场景
# ============================================================

class TestNewPositionScenario:
    POSITIONS = [
        FakePosition(name="理想汽车", ticker="LI"),
        FakePosition(name="苹果公司", ticker="AAPL"),
    ]

    def test_xiaomi_not_held_but_unambiguous(self):
        """M8 核心场景: 小米集团不在持仓,但名称明确 → 应走 position_single"""
        assert _is_asset_unambiguous("小米集团") is True
        assert _is_asset_in_portfolio("小米集团", self.POSITIONS) is False
        # 旧逻辑会返回 False (bug)
        assert _is_asset_clear("小米集团", self.POSITIONS) is False

    def test_msft_not_held_but_unambiguous(self):
        """MSFT 不在持仓,但 ticker 明确"""
        assert _is_asset_unambiguous("MSFT") is True
        assert _is_asset_in_portfolio("MSFT", self.POSITIONS) is False

    def test_held_asset_still_works(self):
        """已持仓标的行为不变"""
        assert _is_asset_unambiguous("理想汽车") is True
        assert _is_asset_in_portfolio("理想汽车", self.POSITIONS) is True

    def test_vague_still_rejected(self):
        """模糊词仍然被拒"""
        assert _is_asset_unambiguous("那只股票") is False
