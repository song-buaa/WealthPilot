"""测试 _resolve_asset_class 各种场景。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from services.broker_sync.position_upsert_service import _resolve_asset_class


def test_us_stock_to_equity():
    """老虎 STK / 名称无特殊关键词 → 权益"""
    assert _resolve_asset_class("equity", "Apple Inc.", "AAPL") == "权益"


def test_unresolved_etf_does_not_default_to_equity():
    """ETF 缺少可靠 exposure evidence 时 fail closed。"""
    assert _resolve_asset_class("etf", "未知主题ETF", "UNKNOWN") == "未分类"


def test_us_treasury_etf_to_fixed_income():
    """美债 ETF 名称含"债券"关键词 → 固收(覆盖默认权益)"""
    result = _resolve_asset_class(
        "etf",
        "债券指数ETF-iShares Barclays 1-3年国债",
        "SHY",
    )
    assert result == "固收"


def test_offshore_bond_fund_chinese_name():
    """境外债券基金,中文名含"债券" → 固收"""
    result = _resolve_asset_class(
        "fund",
        "安本标准-前缘市场债券基金A Acc USD",
        "LU1725895616",
    )
    assert result == "固收"


def test_offshore_credit_fund_english_name():
    """境外信用债基金,英文名含"CREDIT" → 固收"""
    result = _resolve_asset_class(
        "fund",
        'VONTOBEL CREDIT OPPORTUNITIES "I" (USD) ACC',
        "LU2416422678",
    )
    assert result == "固收"


def test_option_to_derivative():
    """期权 → 衍生"""
    assert _resolve_asset_class("option", "AAPL 250620 Call 200", "AAPL250620C200") == "衍生"


def test_unknown_sec_type_falls_back_to_name():
    """未知 sec_type,纯靠名称兜底"""
    assert _resolve_asset_class("unknown", "实物黄金", "AU9999") == "另类"
