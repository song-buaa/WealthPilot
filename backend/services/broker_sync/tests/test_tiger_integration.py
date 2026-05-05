"""
TigerAdapter 集成测试 —— 真实调用 API,验证端到端链路。

默认不跑(打 integration 标签)。手动运行:
    pytest -m integration services/broker_sync/tests/test_tiger_integration.py -v

适合在以下场景跑一次:
- 升级 tigeropen SDK 后
- 修改 adapter 后
- API 长时间未使用,验证连通性还在
"""
from decimal import Decimal

import pytest
from dotenv import load_dotenv
from pathlib import Path

# 显式加载 .env(集成测试可能在 IDE 里跑,默认不会自动加载)
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent.parent.parent / ".env")

from services.broker_sync.tiger.sync_service import TigerSyncService


@pytest.mark.integration
def test_real_api_returns_positions():
    """真实 API 应该返回至少 1 条持仓,且字段全部合规。"""
    service = TigerSyncService()
    positions = service.fetch_positions()

    assert len(positions) > 0, "API 返回空持仓,请检查账户是否有持仓"

    for pos in positions:
        # 必填字段非空
        assert pos.broker == "tiger"
        assert pos.account_id
        assert pos.symbol
        assert pos.name
        assert pos.quantity > 0
        assert pos.avg_cost > 0
        assert pos.current_price > 0
        assert pos.market_value > 0

        # symbol 格式正确
        assert "." in pos.symbol, f"symbol 缺少市场后缀: {pos.symbol}"

        # 港股必须是 5 位数字代码
        if pos.market == "HK":
            code = pos.symbol.split(".")[0]
            assert len(code) == 5, f"港股代码未补零到 5 位: {pos.symbol}"
            assert code.isdigit(), f"港股代码非纯数字: {pos.symbol}"

        # 浮盈比例合理范围(单只股票浮盈 -100% ~ +1000% 之间)
        assert Decimal("-1.0") <= pos.unrealized_pnl_pct <= Decimal("10.0"), \
            f"浮盈比例异常: {pos.symbol} = {pos.unrealized_pnl_pct}"

        # raw_data 完整保留
        assert pos.raw_data, "raw_data 为空,违反保留原则"


@pytest.mark.integration
def test_pnl_pct_is_decimal_form():
    """验证 unrealized_pnl_pct 用小数格式(0.3 = 30%),不是百分数(30 = 30%)。"""
    service = TigerSyncService()
    positions = service.fetch_positions()

    # 浮盈最大值检查:如果 adapter 误把百分数当小数,数值会大于 10(对应 1000%)
    max_pct = max(abs(p.unrealized_pnl_pct) for p in positions)
    assert max_pct < Decimal("10.0"), \
        f"浮盈比例 {max_pct} 超过 10.0,可能是百分数没正确归一化为小数"
