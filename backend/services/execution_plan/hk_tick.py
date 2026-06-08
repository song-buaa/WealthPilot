"""
港股报价档位表 (HKEX Price Ladder)。

v1 覆盖主流价格段（腾讯/美团/小米/阿里等常见标的所在区间）。
超出覆盖区间 → fallback 保守 $0.01 + 标 degraded。
美股固定 $0.01。
"""
from __future__ import annotations

# HKEX 主流价格段报价单位（价格上界, tick size）
# 来源: https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism
_HK_TICK_TABLE = [
    (0.25,    0.001),
    (0.50,    0.005),
    (10.0,    0.010),
    (20.0,    0.020),
    (100.0,   0.050),
    (200.0,   0.100),
    (500.0,   0.200),
    (1000.0,  0.500),
    (2000.0,  1.000),
    (5000.0,  2.000),
    (9999.0,  5.000),  # >5000 的档位
]

# fallback: 价格超出覆盖区间
_HK_FALLBACK_TICK = 0.01


def hk_tick_size(price: float) -> tuple[float, bool]:
    """返回港股给定价格的最小报价单位。

    Returns:
        (tick_size, is_degraded) — is_degraded=True 表示使用了 fallback
    """
    if price <= 0:
        return _HK_FALLBACK_TICK, True
    for upper, tick in _HK_TICK_TABLE:
        if price <= upper:
            return tick, False
    return _HK_FALLBACK_TICK, True


def get_tick_size(price: float, market: str) -> tuple[float, bool]:
    """按市场返回最小报价单位。

    Returns:
        (tick_size, is_degraded)
    """
    if market == "HK":
        return hk_tick_size(price)
    # 美股固定 $0.01
    return 0.01, False


def round_to_tick(price: float, market: str) -> tuple[float, bool]:
    """价格按报价档位取整(向下取整,买入友好)。

    Returns:
        (rounded_price, is_degraded)
    """
    tick, degraded = get_tick_size(price, market)
    if tick <= 0:
        return round(price, 2), True
    rounded = round(price / tick) * tick
    # 精度修正(浮点)
    decimals = max(len(str(tick).rstrip('0').split('.')[-1]) if '.' in str(tick) else 0, 2)
    return round(rounded, decimals), degraded
