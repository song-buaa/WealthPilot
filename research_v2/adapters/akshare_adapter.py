"""
AKShare Adapter — 港股数据拉取（新闻/公司概况/历史行情）。

数据源：东方财富（通过 AKShare 库）。
支持市场：:HK

环境变量:
  AKSHARE_DEV_MOCK=1 → 从 tests/fixtures/ 读 JSON，不调真实 API
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

# AKShare 使用东方财富等境内数据源，需要绕过代理
os.environ.setdefault("NO_PROXY", "*")

from research_v2.adapters.base import InfoAdapter, RawFact
from research_v2.schemas import SourceRef, SourceType
from research_v2.symbol import Symbol

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_FIXTURES_DIR = os.path.join(_PROJECT_ROOT, "tests", "fixtures")


def _is_mock_mode() -> bool:
    return os.environ.get("AKSHARE_DEV_MOCK", "0") == "1"


def _load_fixture(filename: str) -> dict:
    path = os.path.join(_FIXTURES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class AKShareAdapter(InfoAdapter):
    """AKShare 港股数据 Adapter（news / fundamental / hist）。"""

    @property
    def adapter_id(self) -> str:
        return "akshare"

    @property
    def supported_source_types(self) -> list[SourceType]:
        return [
            SourceType.AKSHARE_NEWS,
            SourceType.AKSHARE_FUNDAMENTAL,
            SourceType.AKSHARE_HIST,
        ]

    def is_symbol_supported(self, symbol: Symbol) -> bool:
        return symbol.market in ("HK", "SH", "SZ")

    def fetch(self, symbols: list[Symbol], since: Optional[datetime] = None) -> list[RawFact]:
        results: list[RawFact] = []
        for symbol in symbols:
            if not self.is_symbol_supported(symbol):
                logger.info("AKShare 不支持 %s，跳过", symbol)
                continue

            if symbol.market == "HK":
                code = symbol.ticker.zfill(5)
                fetch_fns = [self.fetch_news, self.fetch_fundamental, self.fetch_hist]
            else:
                code = symbol.ticker
                fetch_fns = [self._fetch_a_news, self._fetch_a_fundamental]

            for fetch_fn in fetch_fns:
                try:
                    facts = fetch_fn(symbol, code)
                    results.extend(facts)
                except Exception as e:
                    logger.warning("AKShare %s 失败: %s %s", fetch_fn.__name__, symbol, e)
        return results

    def fetch_news(self, symbol: Symbol, hk_code: str) -> list[RawFact]:
        """拉取港股新闻（东方财富）。"""
        if _is_mock_mode():
            try:
                data = _load_fixture(f"akshare_{hk_code}_news.json")
            except FileNotFoundError:
                logger.warning("Mock fixture 不存在: akshare_%s_news.json", hk_code)
                return []
            articles = data.get("articles", [])
        else:
            import akshare as ak
            df = ak.stock_news_em(symbol=hk_code)
            articles = []
            for _, row in df.head(10).iterrows():
                articles.append({
                    "title": row.get("新闻标题", ""),
                    "content": str(row.get("新闻内容", ""))[:2000],
                    "datetime": str(row.get("发布时间", "")),
                    "source": row.get("文章来源", ""),
                    "url": row.get("新闻链接", ""),
                })

        if not articles:
            logger.info("AKShare NEWS 返回 0 条: %s", symbol)
            return []

        results: list[RawFact] = []
        for art in articles[:5]:
            try:
                as_of = datetime.strptime(art["datetime"][:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                as_of = datetime.now()

            url = art.get("url", "")
            refs = []
            if url:
                refs.append(SourceRef(ref_type="url", ref_value=url, title=art.get("title")))

            results.append(RawFact(
                source_type=SourceType.AKSHARE_NEWS,
                source_url=url,
                as_of=as_of,
                affected_symbols=[symbol],
                payload={
                    "title": art.get("title", ""),
                    "content": art.get("content", ""),
                    "source": art.get("source", ""),
                },
                source_refs=refs,
            ))

        logger.info("AKShare NEWS: %s → %d 条", symbol, len(results))
        return results

    def fetch_fundamental(self, symbol: Symbol, hk_code: str) -> list[RawFact]:
        """拉取港股公司概况。"""
        if _is_mock_mode():
            try:
                data = _load_fixture(f"akshare_{hk_code}_fundamental.json")
            except FileNotFoundError:
                logger.warning("Mock fixture 不存在: akshare_%s_fundamental.json", hk_code)
                return []
        else:
            import akshare as ak
            df = ak.stock_hk_company_profile_em(symbol=hk_code)
            if df is None or df.empty:
                logger.info("AKShare COMPANY_PROFILE 返回空: %s", symbol)
                return []
            data = df.iloc[0].to_dict()

        return [RawFact(
            source_type=SourceType.AKSHARE_FUNDAMENTAL,
            source_url=None,
            as_of=datetime.now(),
            affected_symbols=[symbol],
            payload=data,
            source_refs=[SourceRef(ref_type="api_call_id", ref_value=f"akshare_profile_{hk_code}")],
        )]

    def fetch_hist(self, symbol: Symbol, hk_code: str) -> list[RawFact]:
        """拉取港股近 7 天历史行情。"""
        if _is_mock_mode():
            try:
                data = _load_fixture(f"akshare_{hk_code}_hist.json")
            except FileNotFoundError:
                logger.warning("Mock fixture 不存在: akshare_%s_hist.json", hk_code)
                return []
            records = data.get("records", [])
        else:
            import akshare as ak
            end = datetime.now().strftime("%Y%m%d")
            start = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
            df = ak.stock_hk_hist(symbol=hk_code, period="daily", start_date=start, end_date=end, adjust="qfq")
            if df is None or df.empty:
                logger.info("AKShare HIST 返回空: %s", symbol)
                return []
            records = []
            for _, row in df.iterrows():
                records.append({
                    "date": str(row.get("日期", "")),
                    "open": float(row.get("开盘", 0)),
                    "close": float(row.get("收盘", 0)),
                    "high": float(row.get("最高", 0)),
                    "low": float(row.get("最低", 0)),
                    "volume": float(row.get("成交量", 0)),
                    "change_pct": float(row.get("涨跌幅", 0)),
                })

        if not records:
            return []

        latest = records[-1]
        try:
            as_of = datetime.strptime(latest["date"][:10], "%Y-%m-%d")
        except (ValueError, KeyError):
            as_of = datetime.now()

        return [RawFact(
            source_type=SourceType.AKSHARE_HIST,
            source_url=None,
            as_of=as_of,
            affected_symbols=[symbol],
            payload={"records": records, "latest": latest},
            source_refs=[SourceRef(ref_type="api_call_id", ref_value=f"akshare_hist_{hk_code}")],
        )]

    # ── A 股子能力 ──────────────────────────────────────────────────

    def _fetch_a_news(self, symbol: Symbol, code: str) -> list[RawFact]:
        """A 股新闻（东方财富）。"""
        if _is_mock_mode():
            try:
                data = _load_fixture(f"akshare_{code}_news.json")
            except FileNotFoundError:
                logger.warning("Mock fixture 不存在: akshare_%s_news.json", code)
                return []
            articles = data.get("articles", data) if isinstance(data, dict) else data
        else:
            import akshare as ak
            df = ak.stock_news_em(symbol=code)
            if df is None or df.empty:
                return []
            articles = []
            for _, row in df.head(10).iterrows():
                articles.append({
                    "title": str(row.get("新闻标题", "")),
                    "content": str(row.get("新闻内容", ""))[:2000],
                    "datetime": str(row.get("发布时间", "")),
                    "source": str(row.get("文章来源", "")),
                    "url": str(row.get("新闻链接", "")),
                })

        if not articles:
            return []

        results: list[RawFact] = []
        for art in (articles if isinstance(articles, list) else [articles])[:5]:
            try:
                as_of = datetime.strptime(str(art.get("datetime", ""))[:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                as_of = datetime.now()

            url = art.get("url", "")
            refs = []
            if url:
                refs.append(SourceRef(ref_type="url", ref_value=url, title=art.get("title")))

            results.append(RawFact(
                source_type=SourceType.AKSHARE_NEWS,
                source_url=url,
                as_of=as_of,
                affected_symbols=[symbol],
                payload={
                    "title": art.get("title", ""),
                    "content": art.get("content", ""),
                    "source": art.get("source", ""),
                },
                source_refs=refs,
            ))

        logger.info("AKShare A股 NEWS: %s → %d 条", symbol, len(results))
        return results

    def _fetch_a_fundamental(self, symbol: Symbol, code: str) -> list[RawFact]:
        """A 股基本面（东方财富个股信息）。"""
        if _is_mock_mode():
            try:
                data = _load_fixture(f"akshare_{code}_fundamental.json")
            except FileNotFoundError:
                logger.warning("Mock fixture 不存在: akshare_%s_fundamental.json", code)
                return []
        else:
            import akshare as ak
            df = ak.stock_individual_info_em(symbol=code)
            if df is None or df.empty:
                return []
            data = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))

        # 统一为 dict
        if isinstance(data, list):
            data = {item.get("item", f"key_{i}"): item.get("value") for i, item in enumerate(data)}

        return [RawFact(
            source_type=SourceType.AKSHARE_FUNDAMENTAL,
            source_url=None,
            as_of=datetime.now(),
            affected_symbols=[symbol],
            payload=data,
            source_refs=[SourceRef(ref_type="api_call_id", ref_value=f"akshare_a_info_{code}")],
        )]
