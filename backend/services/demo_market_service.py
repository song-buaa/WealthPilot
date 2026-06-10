"""
Demo 行情服务 — AKShare 免费源 + 种子数据降级。

DEMO_ALLOW_MARKET_DATA=True 时：
- 美股/港股：AKShare 日线（15 分钟缓存）
- A 股/基金：降级到种子 CSV 静态价格
- 任何失败静默降级到种子数据

DEMO_ALLOW_MARKET_DATA=False 时：
- 全部返回种子静态数据
"""
import csv
import logging
import os
from pathlib import Path
from typing import Optional

import pandas as pd

from backend.core.demo_mode import DEMO_ALLOW_MARKET_DATA
from backend.core import demo_market_cache as cache

logger = logging.getLogger(__name__)

# 种子 CSV 路径
_SEED_CSV = Path(__file__).parent.parent.parent / "demo_seed" / "demo_seed_positions.csv"

# 缓存种子数据
_seed_data: Optional[dict] = None


def _load_seed() -> dict:
    """加载种子 CSV，返回 {ticker: {current_price, name, ...}}。"""
    global _seed_data
    if _seed_data is not None:
        return _seed_data

    _seed_data = {}
    if not _SEED_CSV.exists():
        logger.warning(f"[demo_market] 种子 CSV 不存在: {_SEED_CSV}")
        return _seed_data

    with open(_SEED_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row.get("ticker", "").strip()
            if ticker:
                _seed_data[ticker] = {
                    "name": row.get("name", ""),
                    "current_price": float(row.get("current_price", 0)),
                    "currency": row.get("currency", "CNY"),
                    "asset_class": row.get("asset_class", ""),
                }
    return _seed_data


def _normalize_symbol(symbol: str) -> tuple[str, str]:
    """解析 symbol → (pure_ticker, market)。

    支持格式：AAPL / TSLA:US / 0700:HK / 600519 / 007360
    """
    if ":" in symbol:
        parts = symbol.split(":")
        return parts[0], parts[1].upper()
    if symbol.isdigit():
        if len(symbol) in (4, 5):
            return symbol, "HK"
        if len(symbol) == 6:
            return symbol, "CN"
    return symbol, "US"


def fetch_demo_quote(symbol: str) -> Optional[dict]:
    """获取报价。返回 {"current_price": float, "source": str} 或 None。"""
    ticker, market = _normalize_symbol(symbol)

    # 缓存检查
    cache_key = f"quote:{symbol}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = None

    if DEMO_ALLOW_MARKET_DATA and market in ("US", "HK"):
        try:
            result = _fetch_akshare_quote(ticker, market)
            if result:
                result["source"] = "akshare"
                cache.put(cache_key, result)
                return result
        except Exception as e:
            logger.info(f"[demo_market] AKShare quote 失败({symbol}), 降级到种子: {e}")

    # 降级：种子数据
    seed = _load_seed()
    seed_entry = seed.get(ticker) or seed.get(symbol)
    if seed_entry and seed_entry["current_price"] > 0:
        result = {
            "current_price": seed_entry["current_price"],
            "source": "seed",
        }
        cache.put(cache_key, result)
        return result

    return None


def fetch_demo_kline(symbol: str, bars: int = 60) -> Optional[pd.DataFrame]:
    """获取 K 线。返回 DataFrame(date, open, high, low, close, volume) 或 None。"""
    ticker, market = _normalize_symbol(symbol)

    cache_key = f"kline:{symbol}:{bars}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    result = None

    if DEMO_ALLOW_MARKET_DATA and market in ("US", "HK"):
        try:
            result = _fetch_akshare_kline(ticker, market, bars)
            if result is not None and len(result) > 0:
                cache.put(cache_key, result)
                return result
        except Exception as e:
            logger.info(f"[demo_market] AKShare kline 失败({symbol}), 降级到种子: {e}")

    # 降级：从种子价格构造静态 K 线
    seed = _load_seed()
    seed_entry = seed.get(ticker) or seed.get(symbol)
    if seed_entry and seed_entry["current_price"] > 0:
        result = _generate_seed_kline(seed_entry["current_price"], bars)
        cache.put(cache_key, result)
        return result

    return None


def _fetch_akshare_quote(ticker: str, market: str) -> Optional[dict]:
    """从 AKShare 获取最新报价。"""
    import akshare as ak

    if market == "US":
        df = ak.stock_us_daily(symbol=ticker, adjust="")
        if df is None or len(df) == 0:
            return None
        last = df.iloc[-1]
        return {"current_price": float(last["close"])}

    if market == "HK":
        # AKShare 港股用 5 位代码
        hk_code = ticker.zfill(5)
        df = ak.stock_hk_daily(symbol=hk_code, adjust="")
        if df is None or len(df) == 0:
            return None
        last = df.iloc[-1]
        return {"current_price": float(last["close"])}

    return None


def _fetch_akshare_kline(ticker: str, market: str, bars: int) -> Optional[pd.DataFrame]:
    """从 AKShare 获取日线。返回标准化 DataFrame。"""
    import akshare as ak

    df = None
    if market == "US":
        raw = ak.stock_us_daily(symbol=ticker, adjust="")
        if raw is not None and len(raw) > 0:
            df = raw.tail(bars).reset_index(drop=True)
            df = df.rename(columns={"date": "date", "open": "open", "high": "high",
                                    "low": "low", "close": "close", "volume": "volume"})

    elif market == "HK":
        hk_code = ticker.zfill(5)
        raw = ak.stock_hk_daily(symbol=hk_code, adjust="")
        if raw is not None and len(raw) > 0:
            df = raw.tail(bars).reset_index(drop=True)
            # 港股列名可能不同，标准化
            col_map = {}
            for col in raw.columns:
                cl = col.lower()
                if "date" in cl or "日期" in cl:
                    col_map[col] = "date"
                elif "open" in cl or "开盘" in cl:
                    col_map[col] = "open"
                elif "high" in cl or "最高" in cl:
                    col_map[col] = "high"
                elif "low" in cl or "最低" in cl:
                    col_map[col] = "low"
                elif "close" in cl or "收盘" in cl:
                    col_map[col] = "close"
                elif "volume" in cl or "成交量" in cl:
                    col_map[col] = "volume"
            if col_map:
                df = df.rename(columns=col_map)

    if df is not None and len(df) > 0:
        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[["date", "open", "high", "low", "close", "volume"]].copy() if all(
            c in df.columns for c in ["date", "open", "high", "low", "close", "volume"]
        ) else df

    return None


def _generate_seed_kline(base_price: float, bars: int = 60) -> pd.DataFrame:
    """从种子价格生成确定性静态 K 线（不随机，用固定偏移）。"""
    import numpy as np
    from datetime import datetime, timedelta

    # 确定性波动序列（基于 sin + 小幅 drift）
    dates = []
    opens, highs, lows, closes, volumes = [], [], [], [], []
    today = datetime.now()

    for i in range(bars):
        day = today - timedelta(days=bars - i)
        dates.append(day.strftime("%Y-%m-%d"))

        # 确定性偏移：sin 波模拟
        pct = 0.02 * np.sin(i * 0.3) + 0.001 * (i - bars / 2) / bars
        price = base_price * (1 + pct)

        o = round(price * 0.998, 2)
        c = round(price, 2)
        h = round(max(o, c) * 1.005, 2)
        l = round(min(o, c) * 0.995, 2)
        v = int(1_000_000 + 500_000 * abs(np.sin(i * 0.5)))

        opens.append(o)
        closes.append(c)
        highs.append(h)
        lows.append(l)
        volumes.append(v)

    return pd.DataFrame({
        "date": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
