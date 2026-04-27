"""
Alpha Vantage Adapter — 三个子能力合一个 class。

- fetch_news: NEWS_SENTIMENT API
- fetch_fundamental: COMPANY_OVERVIEW API
- fetch_earnings: EARNINGS API

环境变量:
  AV_DEV_MOCK=1  → 从 tests/fixtures/av_*.json 读取，不真调 API
  ALPHA_VANTAGE_API_KEY → 真实调用时使用
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Optional

from research_v2.adapters.base import AdapterQuotaError, InfoAdapter, RawFact
from research_v2.schemas import SourceRef, SourceType
from research_v2.symbol import Symbol

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FIXTURES_DIR = os.path.join(_PROJECT_ROOT, "tests", "fixtures")

RELEVANCE_THRESHOLD = 0.6


def _get_ticker_relevance(news_item: dict, target_ticker: str) -> float:
    """获取新闻对目标 ticker 的 relevance_score。"""
    for ts in news_item.get("ticker_sentiment", []):
        if ts.get("ticker") == target_ticker:
            try:
                return float(ts.get("relevance_score", "0"))
            except (ValueError, TypeError):
                return 0.0
    return 0.0


def _is_news_relevant(news_item: dict, target_ticker: str) -> bool:
    """判断新闻是否真的与目标 ticker 强相关。

    双重条件：
    1. relevance_score >= 阈值
    2. target ticker 是该新闻中 relevance 最高的 ticker（即新闻主角）
    """
    target_rel = _get_ticker_relevance(news_item, target_ticker)
    if target_rel < RELEVANCE_THRESHOLD:
        return False

    # 检查 target 是否是 primary ticker（relevance 最高）
    max_rel = 0.0
    for ts in news_item.get("ticker_sentiment", []):
        try:
            rel = float(ts.get("relevance_score", "0"))
        except (ValueError, TypeError):
            continue
        if rel > max_rel:
            max_rel = rel
    return target_rel >= max_rel


def _is_mock_mode() -> bool:
    return os.environ.get("AV_DEV_MOCK", "0") == "1"


def _load_fixture(filename: str) -> dict:
    path = os.path.join(_FIXTURES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_av_time(time_str: str) -> datetime:
    """解析 Alpha Vantage 时间格式 YYYYMMDDTHHMMSS → datetime"""
    try:
        return datetime.strptime(time_str, "%Y%m%dT%H%M%S")
    except ValueError:
        try:
            return datetime.strptime(time_str, "%Y%m%dT%H%M")
        except ValueError:
            return datetime.now()


def _call_av_api(function_name: str, **params) -> dict:
    """调用 Alpha Vantage REST API。"""
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY")
    if not api_key:
        raise AdapterQuotaError("未配置 ALPHA_VANTAGE_API_KEY 环境变量")

    import requests
    url = "https://www.alphavantage.co/query"
    params["function"] = function_name
    params["apikey"] = api_key

    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "Information" in data and "premium" in data.get("Information", "").lower():
        raise AdapterQuotaError(f"Alpha Vantage 配额受限: {data['Information']}")

    return data


_MIN_CALL_INTERVAL = 13.0  # 免费版 5次/分钟 = 12秒/次 + 1秒缓冲


class AlphaVantageAdapter(InfoAdapter):
    """Alpha Vantage 三合一 Adapter（news / fundamental / earnings）。"""

    def __init__(self) -> None:
        self._last_call_time: float = 0.0

    def _throttle(self) -> None:
        """真实 API 调用前的限频缓冲。AV_DEV_MOCK=1 时跳过。"""
        if _is_mock_mode():
            return
        elapsed = time.time() - self._last_call_time
        if elapsed < _MIN_CALL_INTERVAL:
            sleep_duration = _MIN_CALL_INTERVAL - elapsed
            logger.info("Alpha Vantage 限频缓冲，等待 %.1f 秒", sleep_duration)
            time.sleep(sleep_duration)
        self._last_call_time = time.time()

    @property
    def adapter_id(self) -> str:
        return "alpha_vantage"

    @property
    def supported_source_types(self) -> list[SourceType]:
        return [
            SourceType.ALPHA_VANTAGE_NEWS,
            SourceType.ALPHA_VANTAGE_FUNDAMENTAL,
            SourceType.ALPHA_VANTAGE_EARNINGS,
        ]

    def is_symbol_supported(self, symbol: Symbol) -> bool:
        return symbol.market == "US"

    def fetch(self, symbols: list[Symbol], since: Optional[datetime] = None) -> list[RawFact]:
        """拉取所有子能力的数据，合并返回。"""
        results: list[RawFact] = []
        for symbol in symbols:
            if not self.is_symbol_supported(symbol):
                logger.info("AlphaVantage 不支持 %s，跳过", symbol)
                continue
            results.extend(self.fetch_news(symbol, since=since))
            results.extend(self.fetch_fundamental(symbol))
            results.extend(self.fetch_earnings(symbol))
        return results

    def fetch_news(self, symbol: Symbol, since: Optional[datetime] = None) -> list[RawFact]:
        """拉取 NEWS_SENTIMENT。"""
        ticker = symbol.ticker

        if _is_mock_mode():
            fixture_name = f"av_{ticker.lower()}_news.json"
            try:
                data = _load_fixture(fixture_name)
            except FileNotFoundError:
                logger.warning("Mock fixture 不存在: %s", fixture_name)
                return []
        else:
            params = {"tickers": ticker, "limit": 10, "sort": "LATEST"}
            if since:
                params["time_from"] = since.strftime("%Y%m%dT%H%M")
            self._throttle()
            data = _call_av_api("NEWS_SENTIMENT", **params)

        feed = data.get("feed", [])
        if not feed:
            logger.info("AlphaVantage NEWS 返回 0 条: %s", symbol)
            return []

        # 相关性过滤：只保留 relevance_score >= 阈值的新闻
        filtered = []
        dropped = []
        for article in feed:
            if _is_news_relevant(article, ticker):
                filtered.append(article)
            else:
                dropped.append({
                    "title": article.get("title", "")[:80],
                    "relevance": _get_ticker_relevance(article, ticker),
                })

        if dropped:
            logger.info(
                "AlphaVantage news 相关性过滤: 保留 %d 条, drop %d 条 (relevance<%.2f). 被过滤示例: %s",
                len(filtered), len(dropped), RELEVANCE_THRESHOLD,
                dropped[0] if dropped else None,
            )

        if not filtered:
            logger.info("AlphaVantage NEWS 过滤后 0 条: %s", symbol)
            return []

        results: list[RawFact] = []
        for article in filtered[:5]:
            as_of = _parse_av_time(article.get("time_published", ""))
            source_url = article.get("url", "")

            refs = []
            if source_url:
                refs.append(SourceRef(ref_type="url", ref_value=source_url, title=article.get("title")))

            raw_fact = RawFact(
                source_type=SourceType.ALPHA_VANTAGE_NEWS,
                source_url=source_url,
                as_of=as_of,
                affected_symbols=[symbol],
                payload={
                    "title": article.get("title", ""),
                    "summary": article.get("summary", ""),
                    "source": article.get("source", ""),
                    "topics": [t.get("topic", "") for t in article.get("topics", [])],
                    "overall_sentiment_score": article.get("overall_sentiment_score"),
                    "overall_sentiment_label": article.get("overall_sentiment_label"),
                    "ticker_sentiment": article.get("ticker_sentiment", []),
                },
                source_refs=refs,
            )
            results.append(raw_fact)

        logger.info("AlphaVantage NEWS: %s → %d 条", symbol, len(results))
        return results

    def fetch_fundamental(self, symbol: Symbol) -> list[RawFact]:
        """拉取 COMPANY_OVERVIEW。"""
        ticker = symbol.ticker

        if _is_mock_mode():
            fixture_name = f"av_{ticker.lower()}_fundamental.json"
            try:
                data = _load_fixture(fixture_name)
            except FileNotFoundError:
                logger.warning("Mock fixture 不存在: %s", fixture_name)
                return []
        else:
            self._throttle()
            data = _call_av_api("COMPANY_OVERVIEW", symbol=ticker)

        if not data or "Symbol" not in data:
            logger.info("AlphaVantage COMPANY_OVERVIEW 返回空: %s", symbol)
            return []

        raw_fact = RawFact(
            source_type=SourceType.ALPHA_VANTAGE_FUNDAMENTAL,
            source_url=None,
            as_of=datetime.now(),
            affected_symbols=[symbol],
            payload=data,
            source_refs=[SourceRef(ref_type="api_call_id", ref_value=f"av_overview_{ticker}")],
        )
        logger.info("AlphaVantage COMPANY_OVERVIEW: %s → 1 条", symbol)
        return [raw_fact]

    def fetch_earnings(self, symbol: Symbol) -> list[RawFact]:
        """拉取 EARNINGS。"""
        ticker = symbol.ticker

        if _is_mock_mode():
            fixture_name = f"av_{ticker.lower()}_earnings.json"
            try:
                data = _load_fixture(fixture_name)
            except FileNotFoundError:
                logger.warning("Mock fixture 不存在: %s", fixture_name)
                return []
        else:
            self._throttle()
            data = _call_av_api("EARNINGS", symbol=ticker)

        if not data or "quarterlyEarnings" not in data:
            logger.info("AlphaVantage EARNINGS 返回空: %s", symbol)
            return []

        quarterly = data.get("quarterlyEarnings", [])
        latest = quarterly[0] if quarterly else {}
        fiscal_date = latest.get("fiscalDateEnding", "")

        try:
            as_of = datetime.strptime(fiscal_date, "%Y-%m-%d") if fiscal_date else datetime.now()
        except ValueError:
            as_of = datetime.now()

        raw_fact = RawFact(
            source_type=SourceType.ALPHA_VANTAGE_EARNINGS,
            source_url=None,
            as_of=as_of,
            affected_symbols=[symbol],
            payload={
                "symbol": ticker,
                "latest_quarterly": latest,
                "annual_earnings": data.get("annualEarnings", [])[:3],
                "quarterly_earnings": quarterly[:4],
            },
            source_refs=[SourceRef(ref_type="api_call_id", ref_value=f"av_earnings_{ticker}")],
        )
        logger.info("AlphaVantage EARNINGS: %s → 1 条", symbol)
        return [raw_fact]
