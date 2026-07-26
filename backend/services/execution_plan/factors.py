"""
执行计划因子 service — 确定性计算，不调 LLM。

产出 FactorSnapshot(dict)，给规则引擎消费。

v3.14 改造：
  - K 线(日线 OHLCV): 通过 KlineProviderRegistry 获取（Broker → AV → Seed）
  - 52w high/low: 从 bars 计算（bars ≥252 根时），不再依赖富途
  - 降级原则: 缺失只标 null + 记 degraded_fields，绝不 block
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class DataSourceMeta:
    """因子数据来源元信息。"""
    price_source: str = ""         # futu / tiger / none
    kline_source: str = ""         # tiger / none
    kline_period: str = "day"
    kline_points: int = 0
    latest_price_time: str = ""
    is_realtime: bool = False
    delayed_minutes: Optional[int] = None
    degraded_fields: list[str] = field(default_factory=list)
    degraded_reason: str = ""


@dataclass
class FactorSnapshot:
    """执行因子快照 — 规则引擎的输入。"""
    symbol: str
    market: str                        # US / HK

    # 价格
    current_price: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None

    # 复用因子(从 K 线重算)
    ma5: Optional[float] = None
    ma20: Optional[float] = None
    rsi14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    ma_position: str = "N/A"
    trend_signal: str = "neutral"

    # 新增因子
    atr14: Optional[float] = None
    volatility_annual: Optional[float] = None
    price_percentile: Optional[float] = None   # 0~1, 在 52w 范围内的位置
    drawdown_from_high: Optional[float] = None  # 负数, 如 -0.15 = 从高点回撤 15%

    # 元信息
    data_source_meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def build_factor_snapshot(
    wp_symbol: str,
    market: str,
    bars: int = 260,
    kline_registry=None,
) -> FactorSnapshot:
    """构建因子快照。

    v3.14: 通过 KlineProviderRegistry 获取 K线，单一 FactorComputer 算因子。
    52w 从 bars 计算（≥252 根时），不再依赖富途。
    任何数据缺失只降级标记，绝不抛异常。
    """
    # 懒初始化 registry（允许外部注入以便测试）
    if kline_registry is None:
        from backend.services.execution_plan.kline_provider import build_kline_registry
        kline_registry = build_kline_registry()

    degraded: list[str] = []
    degraded_reasons: list[str] = []

    # ── 1. 通过 registry 获取 K线 ──
    kline_result, kline_degraded = kline_registry.resolve(wp_symbol, market, "day", bars)

    if kline_degraded:
        provider_reasons = getattr(kline_registry, "last_degraded_reasons", [])
        degraded.extend(f"kline_provider:{name}" for name in kline_degraded)
        degraded_reasons.extend(provider_reasons or [
            f"K线降级: {', '.join(kline_degraded)} 不可用"
        ])

    if kline_result is None or kline_result.bars is None or len(kline_result.bars) == 0:
        kline_source = "none"
        kline_points = 0
        degraded.append("kline")
        if not kline_degraded:
            degraded_reasons.append("所有 K线数据源均不可用")
        kline_df = pd.DataFrame()
        price_time = ""
        is_realtime = False
        delayed_minutes = None
    else:
        kline_source = kline_result.source
        kline_df = kline_result.bars
        kline_points = len(kline_df)
        price_time = kline_result.latest_price_time
        is_realtime = kline_result.is_realtime
        delayed_minutes = kline_result.delayed_minutes

    # ── 2. 单一 FactorComputer：从 bars 算全部因子 ──
    snapshot_data = compute_factors_from_bars(kline_df)

    # ── 3. 52w 从 bars 计算（≥252 根时）──
    high_52w = None
    low_52w = None
    if len(kline_df) >= 252 and "high" in kline_df.columns and "low" in kline_df.columns:
        last_252 = kline_df.tail(252)
        high_52w = _safe_round(float(last_252["high"].astype(float).max()), 2)
        low_52w = _safe_round(float(last_252["low"].astype(float).min()), 2)
    elif len(kline_df) > 0:
        degraded.append("52w_high_low")
        degraded_reasons.append(f"bars 不足 252 根({len(kline_df)})，无法计算 52w")

    # 价格分位
    current_price = snapshot_data.get("current_price")
    price_percentile = None
    if current_price and high_52w and low_52w and high_52w > low_52w:
        price_percentile = round((current_price - low_52w) / (high_52w - low_52w), 4)
        price_percentile = max(0.0, min(1.0, price_percentile))
    elif current_price:
        if "52w_high_low" not in degraded:
            degraded.append("price_percentile")
            degraded_reasons.append("缺少 52w 数据，无法计算价格分位")

    # ── 4. 组装 ──
    meta = DataSourceMeta(
        price_source=kline_source,
        kline_source=kline_source,
        kline_period="day",
        kline_points=kline_points,
        latest_price_time=price_time,
        is_realtime=is_realtime,
        delayed_minutes=delayed_minutes,
        degraded_fields=degraded,
        degraded_reason="; ".join(degraded_reasons) if degraded_reasons else "",
    )

    return FactorSnapshot(
        symbol=wp_symbol,
        market=market,
        current_price=current_price,
        high_52w=high_52w,
        low_52w=low_52w,
        ma5=snapshot_data.get("ma5"),
        ma20=snapshot_data.get("ma20"),
        rsi14=snapshot_data.get("rsi14"),
        macd=snapshot_data.get("macd"),
        macd_signal=snapshot_data.get("macd_signal"),
        macd_hist=snapshot_data.get("macd_hist"),
        ma_position=snapshot_data.get("ma_position", "N/A"),
        trend_signal=snapshot_data.get("trend_signal", "neutral"),
        atr14=snapshot_data.get("atr14"),
        volatility_annual=snapshot_data.get("volatility_annual"),
        price_percentile=price_percentile,
        drawdown_from_high=snapshot_data.get("drawdown_from_high"),
        data_source_meta=asdict(meta),
    )


def compute_factors_from_bars(kline_df: pd.DataFrame) -> dict:
    """单一 FactorComputer：从 OHLCV bars 算全部因子。

    所有 provider 共用这一份计算逻辑。
    返回 dict，字段名与 FactorSnapshot 对齐。
    """
    if kline_df is None or len(kline_df) == 0:
        return {}

    closes = kline_df["close"].astype(float) if "close" in kline_df.columns else pd.Series(dtype=float)
    highs = kline_df["high"].astype(float) if "high" in kline_df.columns else pd.Series(dtype=float)
    lows = kline_df["low"].astype(float) if "low" in kline_df.columns else pd.Series(dtype=float)

    current_price = float(closes.iloc[-1]) if len(closes) > 0 else None

    ma5 = _safe_round(closes.tail(5).mean(), 2) if len(closes) >= 5 else None
    ma20 = _safe_round(closes.tail(20).mean(), 2) if len(closes) >= 20 else None
    rsi14 = _calc_rsi(closes, 14)
    macd_val, macd_sig, macd_hist = _calc_macd(closes)
    ma_position = _determine_ma_position(current_price, ma5, ma20) if current_price else "N/A"
    trend_signal = _determine_trend(ma_position, rsi14, macd_hist)

    atr14 = _calc_atr(highs, lows, closes, 14)
    volatility_annual = _calc_annual_volatility(closes)
    drawdown = _calc_drawdown_from_high(closes)

    return {
        "current_price": current_price,
        "ma5": ma5,
        "ma20": ma20,
        "rsi14": _safe_round(rsi14, 1),
        "macd": _safe_round(macd_val, 4),
        "macd_signal": _safe_round(macd_sig, 4),
        "macd_hist": _safe_round(macd_hist, 4),
        "ma_position": ma_position,
        "trend_signal": trend_signal,
        "atr14": _safe_round(atr14, 4),
        "volatility_annual": _safe_round(volatility_annual, 4),
        "drawdown_from_high": _safe_round(drawdown, 4),
    }


# ── 内部: K 线获取 ─────────────────────────────────────────────


def _fetch_raw_kline(wp_symbol: str, bars: int = 60) -> Optional[pd.DataFrame]:
    """拉取 Tiger 日线 K 线，返回 raw DataFrame。失败返回 None。"""
    try:
        import os
        from pathlib import Path
        from tigeropen.common.consts import Language
        from tigeropen.quote.quote_client import QuoteClient
        from tigeropen.tiger_open_config import TigerOpenClientConfig
        from tigeropen.common.util.signature_utils import read_private_key
        from utils.symbol import symbol_to_tiger_ticker

        tiger_symbol = symbol_to_tiger_ticker(wp_symbol)

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
            return None

        if "symbol" in data.columns:
            data = data[data["symbol"] == tiger_symbol]

        if len(data) == 0:
            return None

        return data.sort_values("time").reset_index(drop=True)

    except Exception as e:
        logger.error("Tiger K线获取失败 %s: %s", wp_symbol, e)
        return None


def _is_futu_available(host: str = "127.0.0.1", port: int = 11111, timeout: float = 0.5) -> bool:
    """快速 socket 探测富途 OpenD 是否可达（不触发 SDK 重试）。"""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, OSError):
        return False


def _fetch_52w(wp_symbol: str) -> tuple[Optional[float], Optional[float], str, str]:
    """从富途拿 52w high/low。返回 (high, low, source, time_str)。"""
    if not _is_futu_available():
        logger.info("富途 OpenD 不可达，52w 数据降级")
        return None, None, "none", ""

    try:
        from services.market_data.futu_quote_service import fetch_quote
        quote = fetch_quote(wp_symbol)
        if quote and quote.high_52w is not None and quote.low_52w is not None:
            time_str = quote.data_as_of.isoformat() if quote.data_as_of else ""
            return quote.high_52w, quote.low_52w, "futu", time_str
    except Exception as e:
        logger.warning("富途 52w 获取失败 %s: %s", wp_symbol, e)

    return None, None, "none", ""


# ── 内部: 因子计算(纯 pandas) ──────────────────────────────────


def _calc_atr(highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14) -> Optional[float]:
    """ATR(14): True Range 的 EMA。"""
    if len(closes) < period + 1:
        return None
    prev_close = closes.shift(1)
    tr = pd.concat([
        highs - lows,
        (highs - prev_close).abs(),
        (lows - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    last = atr.iloc[-1]
    return float(last) if not (math.isnan(last) or math.isinf(last)) else None


def _calc_annual_volatility(closes: pd.Series) -> Optional[float]:
    """年化波动率: daily returns std * sqrt(252)。"""
    if len(closes) < 10:
        return None
    returns = closes.pct_change().dropna()
    if len(returns) < 5:
        return None
    std = returns.std()
    vol = float(std * np.sqrt(252))
    return vol if not (math.isnan(vol) or math.isinf(vol)) else None


def _calc_drawdown_from_high(closes: pd.Series) -> Optional[float]:
    """从 K 线窗口内最高价的回撤(负数)。"""
    if len(closes) < 2:
        return None
    peak = closes.max()
    current = closes.iloc[-1]
    if peak <= 0:
        return None
    dd = float((current - peak) / peak)
    return dd if not (math.isnan(dd) or math.isinf(dd)) else None


def _calc_rsi(closes: pd.Series, period: int = 14) -> Optional[float]:
    """RSI(14)。复用 tiger_kline_service 的算法。"""
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
    closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """MACD(12,26,9)。复用 tiger_kline_service 的算法。"""
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return float(macd_line.iloc[-1]), float(signal_line.iloc[-1]), float(hist.iloc[-1])


def _determine_ma_position(price: float, ma5: Optional[float], ma20: Optional[float]) -> str:
    if ma5 is None or ma20 is None:
        return "N/A"
    if price > ma5 and price > ma20:
        return "above_both"
    if price < ma5 and price < ma20:
        return "below_both"
    return "between"


def _determine_trend(ma_position: str, rsi14: Optional[float], macd_hist: Optional[float]) -> str:
    score = 0
    if ma_position == "above_both":
        score += 2
    elif ma_position == "below_both":
        score -= 2
    if rsi14 is not None:
        score += 1 if rsi14 > 60 else (-1 if rsi14 < 40 else 0)
    if macd_hist is not None:
        score += 1 if macd_hist > 0 else (-1 if macd_hist < 0 else 0)
    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "neutral"


def _safe_round(val: Optional[float], digits: int) -> Optional[float]:
    if val is None:
        return None
    if math.isnan(val) or math.isinf(val):
        return None
    return round(val, digits)
