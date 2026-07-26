"""
KlineProvider 接口 + Registry + 三个实现。

v3.14: 把"数据从哪来"和"因子怎么算"拆开。
所有 provider 返回统一的 KlineResult，FactorComputer 只有一份。
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 接口
# ═══════════════════════════════════════════════════════════════

@dataclass
class KlineResult:
    """K 线获取结果。"""
    bars: pd.DataFrame          # columns: date, open, high, low, close, volume
    source: str                 # provider.name: "broker" | "av" | "seed"
    period: str = "day"
    latest_price_time: str = ""
    is_realtime: bool = False
    delayed_minutes: Optional[int] = None


class KlineProvider(ABC):
    """K 线数据源抽象接口。"""
    name: str = ""
    failure_reason: str = ""

    def _unavailable(self, reason: str) -> None:
        self.failure_reason = reason

    @abstractmethod
    def get_kline(self, symbol: str, market: str,
                  period: str = "day", count: int = 260) -> Optional[KlineResult]:
        """成功返回 KlineResult；无数据/失败返回 None（不抛异常）。"""


# ═══════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════

class KlineProviderRegistry:
    """按有序列表逐个 resolve，命中即返回，失败累加到 degraded。"""

    def __init__(self, providers: list[KlineProvider]):
        self._providers = providers
        self.last_degraded_reasons: list[str] = []

    def resolve(self, symbol: str, market: str,
                period: str = "day", count: int = 260,
                ) -> tuple[Optional[KlineResult], list[str]]:
        degraded: list[str] = []
        self.last_degraded_reasons = []
        for p in self._providers:
            p.failure_reason = ""
            try:
                res = p.get_kline(symbol, market, period, count)
                if res is not None and res.bars is not None and len(res.bars) > 0:
                    return res, degraded
            except Exception as e:
                logger.warning("[KlineRegistry] %s 异常: %s", p.name, e)
                p.failure_reason = f"异常：{e}"
            degraded.append(p.name)
            self.last_degraded_reasons.append(
                f"{p.name} 不可用：{p.failure_reason or '未返回有效 K 线'}"
            )
        return None, degraded


# ═══════════════════════════════════════════════════════════════
# 实现 1: BrokerKlineProvider（包现有 Tiger 逻辑）
# ═══════════════════════════════════════════════════════════════

class BrokerKlineProvider(KlineProvider):
    """Tiger SDK 日线。包现有 _fetch_raw_kline 逻辑，原样保留。"""
    name = "broker"

    def get_kline(self, symbol: str, market: str,
                  period: str = "day", count: int = 260) -> Optional[KlineResult]:
        if market not in ("US", "HK"):
            self._unavailable("Tiger 不支持该市场")
            return None
        try:
            from pathlib import Path
            from tigeropen.common.consts import Language
            from tigeropen.quote.quote_client import QuoteClient
            from tigeropen.tiger_open_config import TigerOpenClientConfig
            from tigeropen.common.util.signature_utils import read_private_key
            from backend.utils.symbol import symbol_to_tiger_ticker

            tiger_symbol = symbol_to_tiger_ticker(symbol)

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

            data = client.get_bars([tiger_symbol], period="day", limit=count)
            if data is None or len(data) == 0:
                self._unavailable("Tiger 未返回日线")
                return None

            if "symbol" in data.columns:
                data = data[data["symbol"] == tiger_symbol]
            if len(data) == 0:
                self._unavailable("Tiger 返回日线未匹配标的")
                return None

            df = data.sort_values("time").reset_index(drop=True)
            # 标准化列名
            df = _standardize_columns(df)

            time_str = ""
            if "date" in df.columns and len(df) > 0:
                time_str = str(df["date"].iloc[-1])

            return KlineResult(
                bars=df,
                source="broker",
                period=period,
                latest_price_time=time_str,
                is_realtime=False,
            )
        except Exception as e:
            logger.warning("[BrokerKline] Tiger K线获取失败 %s: %s", symbol, e)
            self._unavailable(f"Tiger 获取失败：{e}")
            return None


# ═══════════════════════════════════════════════════════════════
# 实现 2: AVKlineProvider（Alpha Vantage TIME_SERIES_DAILY）
# ═══════════════════════════════════════════════════════════════

class AVKlineProvider(KlineProvider):
    """Alpha Vantage TIME_SERIES_DAILY, outputsize=full。"""
    name = "av"

    def get_kline(self, symbol: str, market: str,
                  period: str = "day", count: int = 260) -> Optional[KlineResult]:
        public_demo_mode, demo_allow_market_data = _get_demo_config()
        if public_demo_mode and not demo_allow_market_data:
            self._unavailable("Demo 已禁用外部行情")
            return None
        # AV 仅支持美股（港股返回 None 由 registry 降级到 seed）
        if market not in ("US",):
            self._unavailable("AV 不支持该市场")
            return None

        try:
            from backend.utils.symbol import symbol_to_av_ticker
            av_ticker = symbol_to_av_ticker(symbol)
            if av_ticker is None:
                self._unavailable("无法转换为 AV ticker")
                return None

            from backend.services.market_data.av_fundamentals_service import (
                get_next_av_key, AV_BASE,
            )
            import json
            import urllib.request
            import urllib.parse

            api_key = get_next_av_key()

            # 先试 full（252+ 根算 52w），Premium 不可用时 fallback compact（100 根）
            data = None
            for outputsize in ("full", "compact"):
                params = {
                    "function": "TIME_SERIES_DAILY",
                    "symbol": av_ticker,
                    "outputsize": outputsize,
                    "apikey": api_key,
                }
                url = AV_BASE + "?" + urllib.parse.urlencode(params)

                with urllib.request.urlopen(url, timeout=20) as resp:
                    data = json.loads(resp.read())

                if "Note" in data or "Information" in data:
                    msg = data.get("Note", data.get("Information", ""))
                    if "prem" in msg.lower() and outputsize == "full":
                        logger.info("[AVKline] AV full 需要 Premium, fallback compact: %s", av_ticker)
                        data = None
                        import time; time.sleep(1)  # 避免 AV 限频
                        api_key = get_next_av_key()  # 换 key
                        continue
                    logger.warning("[AVKline] AV 频率限制: %s", av_ticker)
                    self._unavailable("AV 返回限频信息")
                    return None
                break

            if data is None:
                self._unavailable("AV 未返回数据")
                return None
            if "Error Message" in data:
                logger.warning("[AVKline] AV 错误: %s", data["Error Message"])
                self._unavailable("AV 返回错误")
                return None

            ts_key = "Time Series (Daily)"
            if ts_key not in data:
                logger.warning("[AVKline] AV 无 %s key, keys=%s", ts_key, list(data.keys()))
                self._unavailable("AV 响应缺少日线字段")
                return None

            ts = data[ts_key]
            rows = []
            for date_str, vals in sorted(ts.items()):
                rows.append({
                    "date": date_str,
                    "open": float(vals["1. open"]),
                    "high": float(vals["2. high"]),
                    "low": float(vals["3. low"]),
                    "close": float(vals["4. close"]),
                    "volume": float(vals["5. volume"]),
                })

            if not rows:
                self._unavailable("AV 日线为空")
                return None

            df = pd.DataFrame(rows).tail(count).reset_index(drop=True)
            time_str = df["date"].iloc[-1] if len(df) > 0 else ""

            return KlineResult(
                bars=df,
                source="av",
                period=period,
                latest_price_time=time_str,
                is_realtime=False,
                delayed_minutes=15,  # AV 数据有约 15 分钟延迟
            )
        except Exception as e:
            logger.warning("[AVKline] AV K线获取失败 %s: %s", symbol, e)
            self._unavailable(f"AV 获取失败：{e}")
            return None


# ═══════════════════════════════════════════════════════════════
# 实现 3: SeedKlineProvider（demo 兜底）
# ═══════════════════════════════════════════════════════════════

class SeedKlineProvider(KlineProvider):
    """读取版本受控的固定 OHLCV fixture，专供 Demo / 显式测试注册。"""
    name = "seed"

    def get_kline(self, symbol: str, market: str,
                  period: str = "day", count: int = 260) -> Optional[KlineResult]:
        try:
            from backend.utils.symbol import parse_symbol

            ticker, normalized_market = parse_symbol(symbol)
            fixture = _load_seed_fixture()
            df = fixture[
                (fixture["symbol"] == ticker)
                & (fixture["market"].isin({market, normalized_market}))
            ].copy()
            if df.empty:
                self._unavailable("静态 K 线 fixture 未覆盖该标的")
                return None
            df = df.tail(count).reset_index(drop=True)

            return KlineResult(
                bars=df,
                source="seed",
                period=period,
                latest_price_time=df["date"].iloc[-1] if len(df) > 0 else "",
                is_realtime=False,
            )
        except Exception as e:
            logger.warning("[SeedKline] 静态 K线读取失败 %s: %s", symbol, e)
            self._unavailable(f"静态 fixture 读取失败：{e}")
            return None


# ═══════════════════════════════════════════════════════════════
# 工厂（唯一配置点）
# ═══════════════════════════════════════════════════════════════

def build_kline_registry(*, include_seed: Optional[bool] = None) -> KlineProviderRegistry:
    """按环境构建 KlineProviderRegistry。

    dev:   [Broker, AV]
    demo:  [AV, Seed]  （broker 根本不实例化）

    ``include_seed`` 仅供单元测试显式注入 fixture；生产环境按 Demo
    配置决定，不会在非 Demo 环境静默使用种子行情。
    """
    public_demo_mode, _ = _get_demo_config()
    if include_seed is None:
        include_seed = public_demo_mode

    providers: list[KlineProvider] = []
    if not public_demo_mode:
        providers.append(BrokerKlineProvider())
    providers.append(AVKlineProvider())
    if include_seed:
        providers.append(SeedKlineProvider())

    logger.info("[KlineRegistry] 注册: %s", [p.name for p in providers])
    return KlineProviderRegistry(providers)


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """标准化列名为 date/open/high/low/close/volume。"""
    col_map = {}
    for col in df.columns:
        cl = col.lower()
        if cl in ("time", "date", "日期"):
            col_map[col] = "date"
        elif cl in ("open", "开盘"):
            col_map[col] = "open"
        elif cl in ("high", "最高"):
            col_map[col] = "high"
        elif cl in ("low", "最低"):
            col_map[col] = "low"
        elif cl in ("close", "收盘"):
            col_map[col] = "close"
        elif cl in ("volume", "成交量"):
            col_map[col] = "volume"
    if col_map:
        df = df.rename(columns=col_map)
    return df


def _get_demo_config() -> tuple[bool, bool]:
    """延迟读取唯一 Demo 配置，避免纯计算模块导入时触发启动断言。"""
    from backend.core.demo_mode import PUBLIC_DEMO_MODE, DEMO_ALLOW_MARKET_DATA

    return PUBLIC_DEMO_MODE, DEMO_ALLOW_MARKET_DATA


@lru_cache(maxsize=1)
def _load_seed_fixture() -> pd.DataFrame:
    """读取版本受控、日期和 OHLCV 均固定的 Demo fixture。"""
    fixture_path = Path(__file__).resolve().parents[3] / "demo_seed" / "demo_seed_kline_ohlcv.csv"
    df = pd.read_csv(fixture_path, dtype={"symbol": str, "market": str, "date": str})
    required = {"symbol", "market", "date", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"K线 fixture 缺少字段: {sorted(missing)}")
    return df.sort_values(["symbol", "market", "date"]).reset_index(drop=True)
