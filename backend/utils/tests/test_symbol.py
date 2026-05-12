"""
backend/utils/symbol.py 单元测试。
"""
import pytest
from utils.symbol import (
    normalize_symbol,
    parse_symbol,
    ticker_to_broker_sync,
    broker_sync_to_symbol,
    symbol_to_futu,
    symbol_to_av_ticker,
    symbol_to_tiger_ticker,
    infer_symbol_from_ticker,
)


# ── normalize_symbol ─────────────────────────────────────────

class TestNormalizeSymbol:
    def test_us_stock(self):
        assert normalize_symbol("AAPL", "US") == "AAPL:US"

    def test_us_stock_with_quote(self):
        assert normalize_symbol("'AAPL", "US") == "AAPL:US"

    def test_hk_3_digit(self):
        assert normalize_symbol("700", "HK") == "0700:HK"

    def test_hk_4_digit(self):
        assert normalize_symbol("9988", "HK") == "9988:HK"

    def test_hk_5_digit_normalize_to_4(self):
        """00700 (5位补零) → 去掉多余前导零 → 0700:HK (4位)"""
        assert normalize_symbol("00700", "HK") == "0700:HK"

    def test_hk_5_digit_68(self):
        """00068 → 0068:HK"""
        assert normalize_symbol("00068", "HK") == "0068:HK"

    def test_hk_1_digit(self):
        assert normalize_symbol("1", "HK") == "0001:HK"

    def test_hk_2_digit(self):
        assert normalize_symbol("68", "HK") == "0068:HK"

    def test_cn_sh(self):
        assert normalize_symbol("600519", "CN") == "600519:SH"

    def test_cn_sz_main(self):
        assert normalize_symbol("000001", "CN") == "000001:SZ"

    def test_cn_sz_chinext(self):
        assert normalize_symbol("300750", "CN") == "300750:SZ"

    def test_lowercase_market(self):
        assert normalize_symbol("AAPL", "us") == "AAPL:US"

    def test_spaces(self):
        assert normalize_symbol(" LI ", " US ") == "LI:US"


# ── parse_symbol ─────────────────────────────────────────────

class TestParseSymbol:
    def test_colon_format(self):
        assert parse_symbol("LI:US") == ("LI", "US")

    def test_colon_hk(self):
        assert parse_symbol("0700:HK") == ("0700", "HK")

    def test_colon_sh(self):
        assert parse_symbol("600519:SH") == ("600519", "SH")

    def test_dot_ticker_market(self):
        """旧格式 TICKER.MARKET"""
        assert parse_symbol("LI.US") == ("LI", "US")

    def test_dot_market_ticker(self):
        """旧格式 MARKET.TICKER"""
        assert parse_symbol("US.LI") == ("LI", "US")

    def test_dot_hk_market_prefix(self):
        assert parse_symbol("HK.0700") == ("0700", "HK")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            parse_symbol("")

    def test_no_separator_raises(self):
        with pytest.raises(ValueError):
            parse_symbol("AAPL")

    def test_invalid_market_raises(self):
        with pytest.raises(ValueError):
            parse_symbol("AAPL:XX")

    def test_spaces_trimmed(self):
        assert parse_symbol(" LI : US ") == ("LI", "US")


# ── 格式转换函数 ─────────────────────────────────────────────

class TestConversions:
    def test_ticker_to_broker_sync(self):
        assert ticker_to_broker_sync("LI:US") == "LI.US"
        assert ticker_to_broker_sync("0700:HK") == "0700.HK"

    def test_broker_sync_to_symbol(self):
        assert broker_sync_to_symbol("LI.US") == "LI:US"
        assert broker_sync_to_symbol("00700.HK") == "00700:HK"

    def test_symbol_to_futu(self):
        assert symbol_to_futu("QQQ:US") == "US.QQQ"
        assert symbol_to_futu("0700:HK") == "HK.0700"

    def test_symbol_to_av_ticker_us(self):
        assert symbol_to_av_ticker("LI:US") == "LI"
        assert symbol_to_av_ticker("MSFT:US") == "MSFT"

    def test_symbol_to_av_ticker_hk_none(self):
        assert symbol_to_av_ticker("0700:HK") is None

    def test_symbol_to_tiger_ticker(self):
        assert symbol_to_tiger_ticker("LI:US") == "LI"
        assert symbol_to_tiger_ticker("0700:HK") == "0700"


# ── infer_symbol_from_ticker ─────────────────────────────────

class TestInferSymbol:
    def test_us_stock(self):
        assert infer_symbol_from_ticker("AAPL", "USD") == "AAPL:US"

    def test_us_stock_brk(self):
        assert infer_symbol_from_ticker("BRK", "USD") == "BRK:US"

    def test_hk_stock(self):
        assert infer_symbol_from_ticker("00068", "HKD") == "0068:HK"

    def test_hk_stock_700(self):
        assert infer_symbol_from_ticker("700", "HKD") == "0700:HK"

    def test_isin_lu(self):
        assert infer_symbol_from_ticker("LU1725895616", "USD") is None

    def test_isin_ie(self):
        assert infer_symbol_from_ticker("IE00B4L5Y983", "USD") is None

    def test_fund_6digit_cny(self):
        """6 位 CNY 代码保守返回 None(无法区分 A 股 vs 基金)"""
        assert infer_symbol_from_ticker("006479", "CNY") is None
        assert infer_symbol_from_ticker("000001", "CNY") is None
        assert infer_symbol_from_ticker("600519", "CNY") is None

    def test_empty(self):
        assert infer_symbol_from_ticker("", "USD") is None
        assert infer_symbol_from_ticker(None, "USD") is None

    def test_option_format(self):
        assert infer_symbol_from_ticker("AAPL240621C190", "USD") is None

    def test_long_name(self):
        assert infer_symbol_from_ticker("AVERYLONGTICKERCODE", "USD") is None

    def test_dash_in_ticker(self):
        assert infer_symbol_from_ticker("BRK-B", "USD") is None

    def test_a_share_with_suffix(self):
        assert infer_symbol_from_ticker("600519.SH", "CNY") == "600519:SH"
        assert infer_symbol_from_ticker("300750.SZ", "CNY") == "300750:SZ"
