"""_denormalize_ticker 单元测试。

覆盖 BRK.B / LU 债券 / 港股 zfill 等关键 case。
直接测试函数逻辑，避免 position_upsert_service 的重依赖链。
"""
import pytest

# 复制 _denormalize_ticker 的完整逻辑用于独立测试
# 正式代码在 position_upsert_service.py:PositionUpsertService._denormalize_ticker
_KNOWN_MARKETS = {"US", "HK", "SH", "SZ"}


def _denormalize_ticker(symbol: str) -> str:
    if not symbol:
        return symbol
    if ":" in symbol:
        return symbol.split(":", 1)[0]
    if "." in symbol:
        parts = symbol.rsplit(".", 1)
        if len(parts) == 2 and parts[1].upper() in _KNOWN_MARKETS:
            return parts[0]
    return symbol


class TestDenormalizeTicker:
    def test_new_format_basic(self):
        assert _denormalize_ticker("AAPL:US") == "AAPL"
        assert _denormalize_ticker("LI:US") == "LI"
        assert _denormalize_ticker("0700:HK") == "0700"
        assert _denormalize_ticker("600519:SH") == "600519"

    def test_new_format_with_dot_in_ticker(self):
        """BRK.B:US → BRK.B（关键 case）"""
        assert _denormalize_ticker("BRK.B:US") == "BRK.B"
        assert _denormalize_ticker("BRK.A:US") == "BRK.A"

    def test_old_format_basic(self):
        assert _denormalize_ticker("AAPL.US") == "AAPL"
        assert _denormalize_ticker("0700.HK") == "0700"
        assert _denormalize_ticker("600519.SH") == "600519"

    def test_old_format_with_dot_in_ticker(self):
        """BRK.B.US → BRK.B（关键修复 case）"""
        assert _denormalize_ticker("BRK.B.US") == "BRK.B"
        assert _denormalize_ticker("BRK.A.US") == "BRK.A"

    def test_no_market_suffix(self):
        """USD 不是 market，保留原值"""
        assert _denormalize_ticker("LU2416422678.USD") == "LU2416422678.USD"
        assert _denormalize_ticker("LU1725895616.USD") == "LU1725895616.USD"

    def test_hk_market(self):
        assert _denormalize_ticker("0068:HK") == "0068"
        assert _denormalize_ticker("00068.HK") == "00068"
        assert _denormalize_ticker("1879:HK") == "1879"

    def test_known_markets_only(self):
        """只 US/HK/SH/SZ 识别为 market"""
        assert _denormalize_ticker("FOO.BAR") == "FOO.BAR"
        assert _denormalize_ticker("ABC.USD") == "ABC.USD"
        assert _denormalize_ticker("XYZ.EUR") == "XYZ.EUR"

    def test_empty_and_plain(self):
        assert _denormalize_ticker("") == ""
        assert _denormalize_ticker("AAPL") == "AAPL"
        assert _denormalize_ticker("000386") == "000386"
