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
import os
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np

from services.market_data.schema import TechnicalData
from services.market_data.cache import get_cached, set_cached

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


def fetch_kline(wp_symbol: str, bars: int = 60) -> Optional[TechnicalData]:
    """拉取老虎日线 K 线并计算技术指标。任何异常返回 None。"""
    cache_key = f"tiger_kline:{wp_symbol}"
    cached = get_cached(cache_key)
    if cached is not None:
        return cached

    try:
        from tigeropen.common.consts import Language
        from tigeropen.quote.quote_client import QuoteClient
        from tigeropen.tiger_open_config import TigerOpenClientConfig
        from tigeropen.common.util.signature_utils import read_private_key

        tiger_symbol = _to_tiger_symbol(wp_symbol)

        project_root = Path(__file__).parent.parent.parent.parent
        pk_path = project_root / "backend" / "secrets" / "tiger_private_key.pem"
        if not pk_path.exists():
            pk_path = Path(__file__).parent.parent.parent / "secrets" / "tiger_private_key.pem"

        config = TigerOpenClientConfig(sandbox_debug=False)
        config.tiger_id = os.environ.get("TIGER_ID")
        config.account = os.environ.get("TIGER_ACCOUNT")
        config.private_key = read_private_key(str(pk_path))
        config.language = Language.zh_CN
        client = QuoteClient(config)

        data = client.get_bars([tiger_symbol], period="day", limit=bars)

        if data is None or len(data) == 0:
            logger.warning(f"老虎 K线返回空: {wp_symbol}")
            return None

        if "symbol" in data.columns:
            data = data[data["symbol"] == tiger_symbol]

        if len(data) == 0:
            return None

        data = data.sort_values("time").reset_index(drop=True)
        closes = data["close"].astype(float)
        current_price = float(closes.iloc[-1])

        ma5 = float(closes.tail(5).mean()) if len(closes) >= 5 else None
        ma20 = float(closes.tail(20).mean()) if len(closes) >= 20 else None
        rsi14 = _calc_rsi(closes, period=14)
        macd_val, macd_sig, macd_hist = _calc_macd(closes)

        ma_position = _determine_ma_position(current_price, ma5, ma20)
        trend_signal = _determine_trend(ma_position, rsi14, macd_hist)

        result = TechnicalData(
            symbol=wp_symbol,
            current_price=current_price,
            ma5=round(ma5, 2) if ma5 is not None else None,
            ma20=round(ma20, 2) if ma20 is not None else None,
            rsi14=round(rsi14, 1) if rsi14 is not None else None,
            macd=round(macd_val, 4) if macd_val is not None else None,
            macd_signal=round(macd_sig, 4) if macd_sig is not None else None,
            macd_hist=round(macd_hist, 4) if macd_hist is not None else None,
            ma_position=ma_position,
            trend_signal=trend_signal,
            data_as_of=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            bars_count=len(closes),
        )

        set_cached(cache_key, result, KLINE_TTL)
        return result

    except Exception as e:
        logger.error(f"老虎 K线异常: {wp_symbol}: {e}")
        return None
