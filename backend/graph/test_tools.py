"""M2 Tool Layer 单元测试"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("AV_DEV_MOCK", "1")


def test_import():
    from backend.graph.tools import (
        TOOL_SCHEMAS, TOOL_EXECUTORS, call_tool,
        execute_fetch_holdings,
    )
    assert len(TOOL_SCHEMAS) == 7, f"期望 7 个 Schema，实际 {len(TOOL_SCHEMAS)}"
    assert len(TOOL_EXECUTORS) == 7, f"期望 7 个 Executor，实际 {len(TOOL_EXECUTORS)}"
    print("✅ import 正常，7 个 Tool 已注册")


def test_fetch_holdings():
    from backend.graph.tools import execute_fetch_holdings
    result = execute_fetch_holdings(portfolio_id=1)
    assert result.count >= 0
    assert result.total_assets_cny >= 0
    print(f"✅ fetch_holdings: {result.count} 条持仓，总市值 {result.total_assets_cny:,.0f} 元")


def test_calc_deviation():
    from backend.graph.tools import execute_calc_deviation
    result = execute_calc_deviation(portfolio_id=1)
    print(f"✅ calc_allocation_deviation: {len(result.by_class)} 个大类，{result.summary}")


def test_tool_schemas_valid():
    """每个 Schema 都有 name / description / parameters 三个字段"""
    from backend.graph.tools import TOOL_SCHEMAS
    for schema in TOOL_SCHEMAS:
        assert "name" in schema, f"缺少 name: {schema}"
        assert "description" in schema, f"缺少 description: {schema}"
        assert "parameters" in schema, f"缺少 parameters: {schema}"
    print(f"✅ 7 个 Tool Schema 格式正确")
    for s in TOOL_SCHEMAS:
        print(f"   - {s['name']}: {s['description'][:40]}...")


if __name__ == "__main__":
    test_import()
    test_fetch_holdings()
    test_calc_deviation()
    test_tool_schemas_valid()
    print("\n🎉 M2 Tool Layer 基础验证通过")
