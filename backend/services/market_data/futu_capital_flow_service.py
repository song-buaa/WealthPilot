"""
富途 get_capital_flow 资金流向 adapter。
输入: WP 内部 symbol（如 LI:US / 0700:HK，兼容旧格式 LI.US）
输出: CapitalFlowData（失败时返回 None）

注意:
- 返回 391 条分钟级数据，取最后一行作为当日汇总
- 美股 main_in_flow 字段为 N/A，置为 None
- TTL 缓存：60 分钟
"""
import math
import logging
from datetime import datetime, timezone
from typing import Optional

from services.market_data.schema import CapitalFlowData
from services.market_data.cache import get_cached, set_cached

logger = logging.getLogger(__name__)

CAPITAL_FLOW_TTL = 60 * 60  # 60 分钟


def _to_futu_code(wp_symbol: str) -> str:
    """LI:US → US.LI, 0700:HK → HK.0700。兼容旧格式 LI.US。"""
    from utils.symbol import symbol_to_futu
    return symbol_to_futu(wp_symbol)


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def fetch_capital_flow(wp_symbol: str) -> Optional[CapitalFlowData]:
    """拉取富途资金流向（当日汇总）。任何异常返回 None。"""
    cache_key = f"futu_capital_flow:{wp_symbol}"
    cached = get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        from futu import OpenQuoteContext, PeriodType

        futu_code = _to_futu_code(wp_symbol)
        ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
        ret, data = ctx.get_capital_flow(futu_code, period_type=PeriodType.INTRADAY)
        ctx.close()

        if ret != 0 or data is None or len(data) == 0:
            logger.warning(f"富途 capital_flow 失败: {wp_symbol}, ret={ret}")
            return None

        # 取最后一行作为当日汇总
        last_row = data.iloc[-1]

        result = CapitalFlowData(
            symbol=wp_symbol,
            net_inflow=_safe_float(last_row.get("in_flow")) or 0.0,
            super_net=_safe_float(last_row.get("super_in_flow")),
            big_net=_safe_float(last_row.get("big_in_flow")),
            mid_net=_safe_float(last_row.get("mid_in_flow")),
            small_net=_safe_float(last_row.get("sml_in_flow")),
            main_net=_safe_float(last_row.get("main_in_flow")),
            data_as_of=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        )

        set_cached(cache_key, result, CAPITAL_FLOW_TTL)
        return result

    except Exception as e:
        logger.error(f"富途 capital_flow 异常: {wp_symbol}: {e}")
        return None
