"""
WealthPilot - FX 汇率服务
所有资产以 CNY 为 base currency。
Provider: Frankfurter API (https://api.frankfurter.app)
支持 latest 和 historical 两种模式，保留替换 provider 的能力（接口抽象）。
"""

import logging
import warnings
from typing import Tuple

try:
    import requests as _requests
except ImportError:
    _requests = None  # type: ignore

logger = logging.getLogger(__name__)

# ── 默认 fallback 汇率 ──────────────────────────────────────────
FALLBACK_RATES = {
    "USD": 6.90,
    "HKD": 0.92,
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


class FXService:
    """
    FX 汇率服务。
    - 同一 session 内相同 (from_currency, to_currency, date) 只请求一次（内存缓存）。
    - 网络失败时 fallback 到 hardcoded 默认值，并记录警告。
    """

    def __init__(self, provider: FXProvider = None):
        self._provider = provider or FrankfurterProvider()
        self._cache: dict = {}  # key: (from_currency, to_currency, date) -> (rate, actual_date)

    def get_rate(self, from_currency: str, to_currency: str = "CNY", date: str = "latest") -> float:
        """
        返回汇率。
        date 格式: 'YYYY-MM-DD' 或 'latest'
        """
        rate, _ = self._get_rate_with_date(from_currency, to_currency, date)
        return rate

    def convert(self, amount: float, from_currency: str, to_currency: str = "CNY", date: str = "latest") -> Tuple[float, float, str]:
        """
        返回 (converted_amount, rate_used, rate_date)
        汇率换算用确定性代码，不经过 LLM。
        """
        rate, actual_date = self._get_rate_with_date(from_currency, to_currency, date)
        converted = amount * rate
        return converted, rate, actual_date

    def _get_rate_with_date(self, from_currency: str, to_currency: str, date: str) -> Tuple[float, str]:
        """内部方法：带缓存和 fallback 的汇率获取"""
        key = (from_currency.upper(), to_currency.upper(), date)

        if key in self._cache:
            return self._cache[key]

        # 同货币
        if from_currency.upper() == to_currency.upper():
            result = (1.0, date if date != "latest" else "latest")
            self._cache[key] = result
            return result

        # 尝试直接获取
        try:
            rate, actual_date = self._provider.fetch_rate(from_currency, to_currency, date)
            result = (rate, actual_date)
            self._cache[key] = result
            return result
        except Exception as e:
            logger.warning(f"FX API 请求失败 ({from_currency}->{to_currency}, {date}): {e}，尝试交叉汇率")

        # 尝试交叉汇率：先查 from->USD，再查 USD->to
        try:
            if from_currency.upper() != "USD" and to_currency.upper() != "USD":
                rate_from_usd, date1 = self._provider.fetch_rate(from_currency, "USD", date)
                rate_usd_to, date2 = self._provider.fetch_rate("USD", to_currency, date)
                rate = rate_from_usd * rate_usd_to
                actual_date = date1
                result = (rate, actual_date)
                self._cache[key] = result
                return result
        except Exception as e2:
            logger.warning(f"交叉汇率也失败 ({from_currency}->{to_currency}): {e2}，使用 fallback")

        # Fallback：先查 from->CNY fallback，再折算
        fallback_from = FALLBACK_RATES.get(from_currency.upper(), 1.0)
        fallback_to = FALLBACK_RATES.get(to_currency.upper(), 1.0)

        if to_currency.upper() == "CNY":
            rate = fallback_from
        elif from_currency.upper() == "CNY":
            rate = 1.0 / fallback_to if fallback_to != 0 else 1.0
        else:
            rate = fallback_from / fallback_to if fallback_to != 0 else fallback_from

        warnings.warn(
            f"FX fallback: {from_currency}->{to_currency} 使用硬编码汇率 {rate:.4f}",
            RuntimeWarning,
            stacklevel=3,
        )
        result = (rate, "fallback")
        self._cache[key] = result
        return result


# 模块级单例
fx_service = FXService()
