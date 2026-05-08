"""
富途 OpenD snapshot adapter。
输入: WP 内部 symbol（如 LI.US / 02015.HK）
输出: QuoteData（失败时返回 None）
"""
import math
import logging
from datetime import datetime, timezone
from typing import Optional

from services.market_data.schema import QuoteData
from services.market_data.cache import get_cached, set_cached, QUOTE_TTL

logger = logging.getLogger(__name__)


def _to_futu_code(wp_symbol: str) -> str:
    """LI.US → US.LI, 02015.HK → HK.02015"""
    if "." not in wp_symbol:
        return wp_symbol
    raw, market = wp_symbol.rsplit(".", 1)
    return f"{market.upper()}.{raw}"


def _safe(value, default=None):
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    return value


def fetch_quote(wp_symbol: str) -> Optional[QuoteData]:
    """拉取富途 snapshot。任何异常返回 None。"""
    cache_key = f"futu_quote:{wp_symbol}"
    cached = get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        from futu import OpenQuoteContext
        futu_code = _to_futu_code(wp_symbol)

        ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
        ret, data = ctx.get_market_snapshot([futu_code])
        ctx.close()

        if ret != 0 or data is None or len(data) == 0:
            logger.warning(f"富途 snapshot 失败: {wp_symbol}, ret={ret}")
            return None

        row = data.iloc[0]
        missing = []

        def get_field(col):
            val = _safe(row.get(col))
            if val is None:
                missing.append(col)
            return val

        # 涨跌幅:从 last_price / prev_close_price 计算
        last = _safe(row.get("last_price"))
        prev_close = _safe(row.get("prev_close_price"))
        if last is None:
            missing.append("last_price")
        change_pct = None
        if last is not None and prev_close and prev_close > 0:
            change_pct = round((last - prev_close) / prev_close * 100, 2)

        quote = QuoteData(
            symbol=wp_symbol,
            name=get_field("name"),
            current_price=last,
            change_pct=change_pct,
            volume=get_field("volume"),
            high_52w=get_field("highest52weeks_price"),
            low_52w=get_field("lowest52weeks_price"),
            market_cap=get_field("total_market_val"),
            pe_ttm=get_field("pe_ttm_ratio"),
            pb=get_field("pb_ratio"),
            eps=get_field("earning_per_share"),
            dividend_yield=get_field("dividend_ratio_ttm"),
            turnover_rate=get_field("turnover_rate"),
            currency="HKD" if ".HK" in wp_symbol else "USD",
            data_as_of=datetime.now(timezone.utc),
            missing_fields=missing,
        )

        set_cached(cache_key, quote, QUOTE_TTL)
        return quote

    except Exception as e:
        logger.error(f"富途 snapshot 异常: {wp_symbol}: {e}")
        return None
