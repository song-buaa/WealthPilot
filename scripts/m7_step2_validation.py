"""
M7 Step 2 验证：测试三层基金判别 + 盈米 MCP 数据注入
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AV_DEV_MOCK", "1")
os.environ.setdefault("YINGMI_API_KEY", "8TiRdtPwvewqeP_ckn5KsQ")

from decision_engine.data_loader import load


def test_fund_in_portfolio():
    """Layer 1: 持仓里的基金（广发纳指100ETF）"""
    print("=" * 60)
    print("Test 1: Layer 1 - 持仓内基金 (广发纳指100ETF联接(QDII)C)")
    print("=" * 60)
    data = load(asset_name="广发纳指100ETF联接(QDII)C", pid=1)
    print(f"target_position: {data.target_position.name if data.target_position else None}")
    yingmi_count = sum(1 for r in (data.research or []) if "盈米" in r)
    print(f"盈米数据: {yingmi_count} 条")
    if yingmi_count > 0:
        print("✅ Layer 1 通过：持仓内基金走盈米 MCP")
    else:
        print("❌ Layer 1 失败")


def test_fund_not_in_portfolio_with_keyword():
    """Layer 2: 持仓外 + 用户说"基金" """
    print("\n" + "=" * 60)
    print("Test 2: Layer 2 - 持仓外基金 + 用户说'基金'")
    print("=" * 60)
    data = load(asset_name="000001", pid=1, user_query="分析一下000001这只基金")
    print(f"target_position: {data.target_position.name if data.target_position else None}")
    yingmi_count = sum(1 for r in (data.research or []) if "盈米" in r)
    print(f"盈米数据: {yingmi_count} 条")
    has_warning = any("不在您的持仓" in w.message for w in data.data_warnings)
    print(f"持仓外提醒: {has_warning}")
    if yingmi_count > 0 and data.target_position is not None:
        print("✅ Layer 2 通过：持仓外基金走盈米 MCP + 虚拟 target")
    else:
        print("❌ Layer 2 失败")


def test_ambiguous_code():
    """Layer 3: 歧义场景 - 6 位数字但用户没说"基金" """
    print("\n" + "=" * 60)
    print("Test 3: Layer 3 - 歧义代码 000001（无基金关键词）")
    print("=" * 60)
    data = load(asset_name="000001", pid=1, user_query="分析一下000001")
    yingmi_count = sum(1 for r in (data.research or []) if "盈米" in r)
    print(f"盈米数据: {yingmi_count} 条")
    if yingmi_count == 0:
        print("✅ Layer 3 通过：歧义场景不走盈米 MCP")
    else:
        print("❌ Layer 3 失败：歧义场景不应触发盈米")


def test_stock_not_affected():
    """股票标的不受影响"""
    print("\n" + "=" * 60)
    print("Test 4: 股票 - 贵州茅台（不应触发盈米 MCP）")
    print("=" * 60)
    data = load(asset_name="贵州茅台", pid=1)
    yingmi_count = sum(1 for r in (data.research or []) if "盈米" in r)
    print(f"盈米数据: {yingmi_count} 条")
    if yingmi_count == 0:
        print("✅ 股票标的未触发盈米 MCP")
    else:
        print("❌ 股票标的意外触发了盈米 MCP")


if __name__ == "__main__":
    test_fund_in_portfolio()
    test_fund_not_in_portfolio_with_keyword()
    test_ambiguous_code()
    test_stock_not_affected()
