"""
WealthPilot - FX 汇率服务
所有资产以 CNY 为 base currency。
Provider: Frankfurter API (https://api.frankfurter.app)
支持 latest 和 historical 两种模式，保留替换 provider 的能力（接口抽象）。
"""

import logging
import warnings
import math
import time
from dataclasses import dataclass, asdict, replace
from datetime import date as calendar_date, datetime, timezone
from threading import RLock
from typing import Tuple

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore

logger = logging.getLogger(__name__)

# ── 默认 fallback 汇率（2026-06 更新）─────────────────────────────
FALLBACK_RATES = {
    "USD": 7.25,
    "HKD": 0.93,
}

FRANKFURTER_BASE_URL = "https://api.frankfurter.app"


class FXProvider:
    """汇率 Provider 抽象基类，便于替换数据源"""

    def fetch_rate(self, from_currency: str, to_currency: str, date: str) -> Tuple[float, str]:
        """
        返回 (rate, actual_date)。
        date: 'latest' 或 'YYYY-MM-DD'
        """
        raise NotImplementedError


class FrankfurterProvider(FXProvider):
    """Frankfurter API Provider"""

    def fetch_rate(self, from_currency: str, to_currency: str, date: str) -> Tuple[float, str]:
        if _requests is None:
            raise RuntimeError("requests 库未安装")

        # 同货币直接返回
        if from_currency.upper() == to_currency.upper():
            return 1.0, date if date != "latest" else "latest"

        url_date = "latest" if date == "latest" else date
        url = f"{FRANKFURTER_BASE_URL}/{url_date}"
        params = {"from": from_currency.upper(), "to": to_currency.upper()}

        resp = _requests.get(url, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()

        rate = data["rates"].get(to_currency.upper())
        if rate is None:
            raise ValueError(f"Frankfurter 未返回 {to_currency} 汇率，响应: {data}")

        actual_date = data.get("date", url_date)
        return float(rate), actual_date


def fx_date_is_current(value, today=None):
    """Freshness policy shared by sync and stored-valuation projections."""
    try:
        age = ((today or calendar_date.today()) - calendar_date.fromisoformat(value)).days
        return 0 <= age <= 4
    except (TypeError, ValueError):
        return False


class FXRateUnavailable(ValueError):
    """No defensible conversion exists; callers must retain/label last-good."""


@dataclass(frozen=True)
class FXQuote:
    rate: float | None
    rate_date: str | None
    fetched_at: str
    source: str
    quality: str
    issues: tuple[str, ...] = ()

    def to_dict(self):
        return asdict(self)


class FXService:
    """One FX authority, bounded latest/failure cache and explicit evidence."""

    def __init__(self, provider=None, *, ttl=300, failure_ttl=15, clock=None):
        self._provider = provider or FrankfurterProvider()
        self._cache = {}
        self._expires = {}
        self._last_good = {}
        self._clock = clock or time.monotonic
        self.ttl, self.failure_ttl = ttl, failure_ttl
        self._lock = RLock()

    def get_rate(self, from_currency, to_currency="CNY", date="latest"):
        return self._get_rate_with_date(from_currency, to_currency, date)[0]

    def convert(self, amount, from_currency, to_currency="CNY", date="latest"):
        rate, actual_date = self._get_rate_with_date(from_currency, to_currency, date)
        return amount * rate, rate, actual_date

    def _get_rate_with_date(self, from_currency, to_currency, date):
        quote = self.get_quote(from_currency, to_currency, date)
        if quote.rate is None:
            raise FXRateUnavailable("FX_RATE_UNAVAILABLE")
        # The legacy tuple API must not disguise stale/fallback values as fresh.
        actual = quote.rate_date if quote.quality in {"current", "historical", "legacy"} else quote.quality
        return quote.rate, actual

    @staticmethod
    def _valid(rate):
        rate = float(rate)
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("INVALID_FX_RATE")
        return rate

    def get_quote(self, from_currency, to_currency="CNY", date="latest"):
        key = (from_currency.upper(), to_currency.upper(), date)
        with self._lock:
            now = self._clock()
            cached = self._cache.get(key)
            # Preserve the old private test/cache injection contract, never call
            # an unversioned tuple a newly verified provider observation.
            if isinstance(cached, tuple):
                value, actual = cached
                quote = FXQuote(self._valid(value), actual, "", "legacy_cache",
                                "fallback" if actual == "fallback" else "legacy", ("FX_LEGACY_CACHE",))
                self._cache[key], self._expires[key] = quote, now+self.failure_ttl
                return quote
            if cached and now < self._expires.get(key, 0):
                return cached
            stamp = datetime.now(timezone.utc).isoformat()
            if key[0] == key[1]:
                quote = FXQuote(1.0, calendar_date.today().isoformat() if date == "latest" else date,
                                stamp, "same_currency", "current" if date == "latest" else "historical")
            else:
                try:
                    rate, actual = self._provider.fetch_rate(*key)
                    quote = self._provider_quote(rate, actual, date, stamp, "provider")
                except Exception:
                    quote = None
                    if key[0] != "USD" and key[1] != "USD":
                        try:
                            first, d1 = self._provider.fetch_rate(key[0], "USD", date)
                            second, d2 = self._provider.fetch_rate("USD", key[1], date)
                            quote = self._provider_quote(self._valid(first)*self._valid(second),
                                min(d1, d2), date, stamp, "provider_cross")
                        except Exception:
                            pass
                    if quote is None:
                        old = self._last_good.get(key)
                        if old:
                            quote = replace(old, quality="stale", issues=("FX_REFRESH_FAILED", "FX_STALE"))
                        else:
                            rates = {"CNY": 1.0, **FALLBACK_RATES}
                            rate = rates[key[0]]/rates[key[1]] if key[0] in rates and key[1] in rates else None
                            quote = FXQuote(rate, None, stamp, "hardcoded" if rate else "unavailable",
                                            "fallback" if rate else "unavailable", ("FX_FALLBACK" if rate else "FX_UNKNOWN",))
                            if rate:
                                warnings.warn(f"FX fallback: {key[0]}->{key[1]}", RuntimeWarning, stacklevel=2)
                        logger.warning("[FX degraded] %s->%s: %s", key[0], key[1], quote.quality)
            if quote.quality in {"current", "historical", "stale"} and quote.source.startswith("provider"):
                self._last_good[key] = quote
            self._cache[key] = quote
            self._expires[key] = now + (self.ttl if quote.quality in {"current", "historical"} else self.failure_ttl)
            return quote

    def _provider_quote(self, rate, actual, requested, stamp, source):
        actual_date = calendar_date.fromisoformat(actual)
        today = calendar_date.today()
        if actual_date > today:
            raise ValueError("FX_FUTURE_DATE")
        quality = "historical" if requested != "latest" else "current" if fx_date_is_current(actual, today) else "stale"
        return FXQuote(self._valid(rate), actual, stamp, source, quality,
                       ("FX_STALE",) if quality == "stale" else ())


fx_service = FXService()
