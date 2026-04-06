"""
资产配置模块 — 目标区间

从投资纪律模块的 asset_allocation_ranges 读取目标区间，确保所有模块数据口径统一。
不允许硬编码覆盖用户在投资纪律中设置的值。
"""

from app.allocation.types import AllocAssetClass, AssetTarget
from app.discipline.config import get_rules


def get_default_targets() -> list[AssetTarget]:
    """
    从投资纪律模块读取目标区间，与 Dashboard ALLOC_CATS 对齐。

    数据源：app/discipline/config.py → get_rules() → asset_allocation_ranges
    """
    rules = get_rules()
    ranges = rules.get("asset_allocation_ranges", {})

    cash_min = ranges.get("monetary_min_amount", 10_000)
    cash_max = ranges.get("monetary_max_amount", 100_000)
    fixed_min = ranges.get("fixed_income_min", 0.20)
    fixed_max = ranges.get("fixed_income_max", 0.60)
    equity_min = ranges.get("equity_min", 0.40)
    equity_max = ranges.get("equity_max", 0.80)
    alt_max = ranges.get("alternatives_max", 0.10)
    deriv_max = ranges.get("derivatives_max", 0.10)

    return [
        AssetTarget(
            asset_class=AllocAssetClass.CASH,
            cash_min_amount=cash_min,
            cash_max_amount=cash_max,
            floor_ratio=0.008,         # 对齐 ALLOC_CATS minPct
            ceiling_ratio=0.082,       # 对齐 ALLOC_CATS maxPct
            mid_ratio=0.045,
        ),
        AssetTarget(
            asset_class=AllocAssetClass.FIXED,
            floor_ratio=fixed_min,
            ceiling_ratio=fixed_max,
            mid_ratio=(fixed_min + fixed_max) / 2,
        ),
        AssetTarget(
            asset_class=AllocAssetClass.EQUITY,
            floor_ratio=equity_min,
            ceiling_ratio=equity_max,
            mid_ratio=(equity_min + equity_max) / 2,
        ),
        AssetTarget(
            asset_class=AllocAssetClass.ALT,
            floor_ratio=None,
            ceiling_ratio=alt_max,
            mid_ratio=alt_max / 2,
        ),
        AssetTarget(
            asset_class=AllocAssetClass.DERIV,
            floor_ratio=None,
            ceiling_ratio=deriv_max,
            mid_ratio=None,
        ),
    ]
