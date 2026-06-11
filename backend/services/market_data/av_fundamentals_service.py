"""
Alpha Vantage 财报 + 分析师数据 adapter。
输入: WP symbol（如 LI:US / MSFT:US，兼容旧格式 LI.US）
输出: FundamentalsData（失败时返回 None）
"""
import os
import json
import logging
import urllib.request
import urllib.parse
from typing import Optional
from datetime import datetime

from services.market_data.schema import FundamentalsData, AnalystData
from services.market_data.cache import get_cached, set_cached, FUNDAMENTALS_TTL

logger = logging.getLogger(__name__)

AV_BASE = "https://www.alphavantage.co/query"

# ── 多 Key 轮换(v3.4) ────────────────────────────────────────
import itertools as _itertools

def _load_av_keys() -> list[str]:
    """从环境变量加载所有 AV API Key。

    优先读 AV_API_KEY_1 ~ AV_API_KEY_4(多 key 轮换),
    fallback 到旧的 ALPHA_VANTAGE_API_KEY(单 key 兼容)。
    """
    keys = []
    for i in range(1, 5):
        k = os.environ.get(f"AV_API_KEY_{i}")
        if k:
            keys.append(k)
    if not keys:
        legacy = os.environ.get("ALPHA_VANTAGE_API_KEY")
        if legacy:
            keys.append(legacy)
    return keys or ["demo"]

_AV_KEYS = _load_av_keys()
_av_key_cycle = _itertools.cycle(_AV_KEYS)

def get_next_av_key() -> str:
    """Round-Robin 获取下一个 AV API Key。"""
    return next(_av_key_cycle)


def _wp_symbol_to_av_ticker(wp_symbol: str) -> Optional[str]:
    """LI:US → LI, 0700:HK → None（AV 无港股）。兼容旧格式 LI.US。"""
    from utils.symbol import symbol_to_av_ticker
    return symbol_to_av_ticker(wp_symbol)


def _av_get(function: str, symbol: str, api_key: str) -> Optional[dict]:
    """调 AV REST API，返回 dict 或 None。"""
    params = {"function": function, "symbol": symbol, "apikey": api_key}
    url = AV_BASE + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        if "Note" in data or "Information" in data:
            logger.warning(f"AV 频率限制: {function}/{symbol}")
            return None
        if "Error Message" in data:
            logger.warning(f"AV 错误: {data['Error Message']}")
            return None
        return data
    except Exception as e:
        logger.error(f"AV 请求失败 {function}/{symbol}: {e}")
        return None


def _safe_float(val, default=None) -> Optional[float]:
    if val is None or val == "None" or val == "-" or val == "":
        return default
    try:
        f = float(val)
        import math
        return None if math.isnan(f) else f
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=None) -> Optional[int]:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def _load_seed_fundamentals(ticker: str) -> Optional[FundamentalsData]:
    """从种子文件加载兜底基本面数据（AV 不覆盖或 API 失败时）。"""
    import json as _json
    from pathlib import Path
    seed_path = Path(__file__).resolve().parent.parent.parent.parent / "demo_seed" / "demo_seed_fundamentals.json"
    try:
        if not seed_path.exists():
            return None
        with open(seed_path, encoding="utf-8") as f:
            seeds = _json.load(f)
        data = seeds.get(ticker)
        if not data:
            return None
        return FundamentalsData(
            symbol=ticker,
            market_cap=data.get("market_cap") or data.get("market_cap_hkd") or data.get("market_cap_cny"),
            pe_ttm=data.get("pe_ratio"),
            pe_forward=data.get("forward_pe"),
            eps_ttm=data.get("eps") or data.get("eps_hkd") or data.get("eps_cny"),
            revenue_ttm=data.get("revenue_ttm") or data.get("revenue_ttm_hkd") or data.get("revenue_ttm_cny"),
            profit_margin=data.get("profit_margin"),
            high_52w=data.get("52_week_high") or data.get("52_week_high_hkd") or data.get("52_week_high_cny"),
            low_52w=data.get("52_week_low") or data.get("52_week_low_hkd") or data.get("52_week_low_cny"),
            beta=data.get("beta"),
            analyst=AnalystData(target_price_avg=data.get("analyst_target_price")),
            data_as_of=seeds.get("_updated", "seed"),
        )
    except Exception as e:
        logger.debug(f"种子基本面加载失败: {e}")
        return None


def fetch_fundamentals(wp_symbol: str) -> Optional[FundamentalsData]:
    """调 AV OVERVIEW + INCOME_STATEMENT。失败/不支持时降级到种子数据。"""
    try:
        ticker = _wp_symbol_to_av_ticker(wp_symbol)
    except (ValueError, Exception):
        ticker = None
    if ticker is None:
        # AV 不支持港股/A股/基金 → 种子兜底
        raw_ticker = wp_symbol.split(":")[0] if ":" in wp_symbol else wp_symbol
        logger.info(f"AV 不支持: {wp_symbol}，尝试种子兜底")
        return _load_seed_fundamentals(raw_ticker)

    cache_key = f"av_fundamentals:{ticker}"
    cached = get_cached(cache_key)
    if cached is not None:
        return cached

    # DEMO_ALLOW_MARKET_DATA=false → 跳过 AV API，直接种子
    try:
        from backend.core.demo_mode import PUBLIC_DEMO_MODE, DEMO_ALLOW_MARKET_DATA
        if PUBLIC_DEMO_MODE and not DEMO_ALLOW_MARKET_DATA:
            seed = _load_seed_fundamentals(ticker)
            if seed:
                set_cached(cache_key, seed, FUNDAMENTALS_TTL)
            return seed
    except ImportError:
        pass

    api_key = get_next_av_key()

    # --- OVERVIEW ---
    overview = _av_get("OVERVIEW", ticker, api_key)
    if not overview:
        logger.info(f"AV OVERVIEW 失败，尝试种子兜底: {ticker}")
        seed = _load_seed_fundamentals(ticker)
        if seed:
            set_cached(cache_key, seed, FUNDAMENTALS_TTL)
        return seed

    missing = []

    def get_f(key):
        val = _safe_float(overview.get(key))
        if val is None:
            missing.append(key)
        return val

    def get_i(key):
        val = _safe_int(overview.get(key))
        if val is None:
            missing.append(key)
        return val

    # 分析师
    target_price = _safe_float(overview.get("AnalystTargetPrice"))
    current_price_approx = _safe_float(overview.get("50DayMovingAverage"))
    upside_pct = None
    if current_price_approx and target_price and current_price_approx > 0:
        upside_pct = round((target_price - current_price_approx) / current_price_approx * 100, 1)

    sb = get_i("AnalystRatingStrongBuy")
    b = get_i("AnalystRatingBuy")
    h = get_i("AnalystRatingHold")
    s = get_i("AnalystRatingSell")
    ss = get_i("AnalystRatingStrongSell")

    total = (sb or 0) + (b or 0) + (h or 0) + (s or 0) + (ss or 0)
    consensus = None
    if total > 0:
        buy_pct = ((sb or 0) + (b or 0)) / total
        sell_pct = ((s or 0) + (ss or 0)) / total
        if buy_pct >= 0.7:
            consensus = "Strong Buy" if (sb or 0) / total >= 0.3 else "Buy"
        elif sell_pct >= 0.3:
            consensus = "Sell"
        else:
            consensus = "Hold"

    analyst = AnalystData(
        analyst_count=total if total > 0 else None,
        consensus=consensus,
        target_price_avg=target_price,
        target_price_upside_pct=upside_pct,
        strong_buy=sb, buy=b, hold=h, sell=s, strong_sell=ss,
    )

    # --- INCOME_STATEMENT（营收增速）---
    import time
    time.sleep(1.5)  # AV 免费版限频

    revenue_yoy = None
    net_income_yoy = None
    revenue_ttm = None
    net_income_ttm = None

    income = _av_get("INCOME_STATEMENT", ticker, api_key)
    if income and "quarterlyReports" in income:
        quarterly = income["quarterlyReports"]
        if len(quarterly) >= 8:
            def sum_4(reports, fld):
                vals = [_safe_float(r.get(fld)) for r in reports[:4]]
                return sum(v for v in vals if v is not None) if any(v is not None for v in vals) else None

            r_now = sum_4(quarterly[:4], "totalRevenue")
            r_prev = sum_4(quarterly[4:8], "totalRevenue")
            n_now = sum_4(quarterly[:4], "netIncome")
            n_prev = sum_4(quarterly[4:8], "netIncome")

            revenue_ttm = r_now
            net_income_ttm = n_now

            if r_now and r_prev and r_prev != 0:
                revenue_yoy = round((r_now - r_prev) / abs(r_prev) * 100, 1)
            if n_now and n_prev and n_prev != 0:
                net_income_yoy = round((n_now - n_prev) / abs(n_prev) * 100, 1)

    # gross_margin: AV GrossProfitTTM 是绝对值,转百分比
    gross_margin_val = _safe_float(overview.get("GrossProfitTTM"))
    gross_margin = None
    if gross_margin_val and revenue_ttm and revenue_ttm > 0:
        gm = round(gross_margin_val / revenue_ttm * 100, 1)
        if 0 < gm <= 100:
            gross_margin = gm

    # profit_margin: AV 直接给比例(如 0.393 = 39.3%)
    profit_margin_raw = _safe_float(overview.get("ProfitMargin"))
    profit_margin = round(profit_margin_raw * 100, 1) if profit_margin_raw is not None else None

    # ROE: AV 给比例(如 0.34 = 34%)
    roe_raw = _safe_float(overview.get("ReturnOnEquityTTM"))
    roe = round(roe_raw * 100, 1) if roe_raw is not None else None

    fundamentals = FundamentalsData(
        symbol=wp_symbol,
        pe_ttm=get_f("PERatio"),
        pe_forward=get_f("ForwardPE"),
        peg_ratio=get_f("PEGRatio"),
        beta=get_f("Beta"),
        market_cap=get_f("MarketCapitalization"),
        high_52w=get_f("52WeekHigh"),
        low_52w=get_f("52WeekLow"),
        eps_ttm=get_f("EPS"),
        roe=roe,
        gross_margin=gross_margin,
        profit_margin=profit_margin,
        revenue_ttm=revenue_ttm,
        revenue_yoy=revenue_yoy,
        net_income_ttm=net_income_ttm,
        net_income_yoy=net_income_yoy,
        analyst=analyst,
        data_as_of=datetime.now().strftime("%Y-%m-%d"),
        missing_fields=missing,
    )

    set_cached(cache_key, fundamentals, FUNDAMENTALS_TTL)
    return fundamentals
