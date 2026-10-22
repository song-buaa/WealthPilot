"""
老虎 QuoteClient K线数据 + 技术指标计算 adapter。
输入: WP 内部 symbol（如 MSFT:US / 0700:HK，兼容旧格式 MSFT.US）
输出: TechnicalData（失败时返回 None）

技术指标用 pandas 手写，不依赖 TA-Lib：
- MA5 / MA20
- RSI(14)
- MACD(12, 26, 9)

TTL 缓存：日线数据每天只需拉一次，缓存 4 小时
"""
import logging
from typing import Optional

import pandas as pd
import numpy as np

from services.market_data.schema import TechnicalData

logger = logging.getLogger(__name__)

KLINE_TTL = 4 * 60 * 60  # 4 小时


def _to_tiger_symbol(wp_symbol: str) -> str:
    """MSFT:US → MSFT, 0700:HK → 0700。兼容旧格式 MSFT.US。"""
    from utils.symbol import symbol_to_tiger_ticker
    return symbol_to_tiger_ticker(wp_symbol)


def _calc_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    last = rsi.iloc[-1]
    return float(last) if not np.isnan(last) else None


def _calc_macd(
    closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(hist.iloc[-1])


def _determine_ma_position(
    price: float, ma5: Optional[float], ma20: Optional[float]
) -> str:
    if ma5 is None or ma20 is None:
        return "N/A"
    if price > ma5 and price > ma20:
        return "above_both"
    if price < ma5 and price < ma20:
        return "below_both"
    return "between"


def _determine_trend(
    ma_position: str, rsi14: Optional[float], macd_hist: Optional[float]
) -> str:
    """
    综合判断技术面趋势（简单多数原则）：
    均线位置(权重2) + RSI(权重1) + MACD hist(权重1)
    """
    score = 0
    if ma_position == "above_both":
        score += 2
    elif ma_position == "below_both":
        score -= 2

    if rsi14 is not None:
        if rsi14 > 60:
            score += 1
        elif rsi14 < 40:
            score -= 1

    if macd_hist is not None:
        if macd_hist > 0:
            score += 1
        elif macd_hist < 0:
            score -= 1

    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "neutral"


def fetch_kline(wp_symbol: str, bars: int = 300) -> Optional[TechnicalData]:
    """Compatibility entrypoint; reuse the original multi-provider OHLCV registry."""
    from backend.services.market_data.technical_evidence import fetch
    return fetch(wp_symbol, bars)
