"""
市场数据统一 Schema。
所有字段可为 None（数据源失败或不支持时）。
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Literal


@dataclass
class QuoteData:
    """富途 snapshot 实时行情 + 估值。来源:富途 OpenD。"""
    symbol: str
    name: Optional[str] = None
    current_price: Optional[float] = None
    change_pct: Optional[float] = None
    volume: Optional[int] = None
    avg_volume_30d: Optional[int] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    market_cap: Optional[float] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    eps: Optional[float] = None
    dividend_yield: Optional[float] = None
    turnover_rate: Optional[float] = None
    currency: str = "USD"
    update_frequency: Literal["realtime"] = "realtime"
    data_as_of: Optional[datetime] = None
    source: str = "futu"
    missing_fields: list[str] = field(default_factory=list)


@dataclass
class AnalystData:
    """分析师评级与目标价。来源:Alpha Vantage OVERVIEW。"""
    analyst_count: Optional[int] = None
    consensus: Optional[str] = None
    target_price_avg: Optional[float] = None
    target_price_upside_pct: Optional[float] = None
    strong_buy: Optional[int] = None
    buy: Optional[int] = None
    hold: Optional[int] = None
    sell: Optional[int] = None
    strong_sell: Optional[int] = None


@dataclass
class FundamentalsData:
    """财报关键指标 + 分析师。来源:Alpha Vantage OVERVIEW + INCOME_STATEMENT。"""
    symbol: str
    pe_ttm: Optional[float] = None
    pe_forward: Optional[float] = None
    peg_ratio: Optional[float] = None
    beta: Optional[float] = None
    market_cap: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None
    eps_ttm: Optional[float] = None
    roe: Optional[float] = None
    gross_margin: Optional[float] = None
    profit_margin: Optional[float] = None
    revenue_ttm: Optional[float] = None
    revenue_yoy: Optional[float] = None
    net_income_ttm: Optional[float] = None
    net_income_yoy: Optional[float] = None
    analyst: Optional[AnalystData] = None
    update_frequency: Literal["daily"] = "daily"
    data_as_of: Optional[str] = None
    source: str = "alphavantage"
    missing_fields: list[str] = field(default_factory=list)


@dataclass
class MarketDataBundle:
    """wp-fetch-realtime-quote + wp-fetch-fundamentals 的合并输出。"""
    symbol: str
    quote: Optional[QuoteData] = None
    fundamentals: Optional[FundamentalsData] = None
    fetched_at: Optional[datetime] = None

    @property
    def has_quote(self) -> bool:
        return self.quote is not None and self.quote.current_price is not None

    @property
    def has_fundamentals(self) -> bool:
        return self.fundamentals is not None

    def to_snapshot_dict(self) -> dict:
        """转成 structured_result.marketDataSnapshot 格式。"""
        q = self.quote
        f = self.fundamentals
        a = f.analyst if f else None

        return {
            "currentPrice": q.current_price if q else None,
            "changePct": q.change_pct if q else None,
            "currency": q.currency if q else "USD",
            "marketCap": q.market_cap if q else (f.market_cap if f else None),
            "high52w": q.high_52w if q else (f.high_52w if f else None),
            "low52w": q.low_52w if q else (f.low_52w if f else None),
            "peRatio": q.pe_ttm if q else (f.pe_ttm if f else None),
            "eps": q.eps if q else (f.eps_ttm if f else None),
            "roe": f.roe if f else None,
            "grossMargin": f.gross_margin if f else None,
            "revenueGrowthYoY": f.revenue_yoy if f else None,
            "netIncomeGrowthYoY": f.net_income_yoy if f else None,
            "analystConsensus": a.consensus if a else None,
            "analystCount": a.analyst_count if a else None,
            "analystTargetPrice": a.target_price_avg if a else None,
            "targetPriceUpsidePct": a.target_price_upside_pct if a else None,
            "analystRatingDistribution": {
                "strongBuy": a.strong_buy,
                "buy": a.buy,
                "hold": a.hold,
                "sell": a.sell,
                "strongSell": a.strong_sell,
            } if a else None,
            "dataSource": "futu+alphavantage",
            "quoteDataAsOf": q.data_as_of.isoformat() if q and q.data_as_of else None,
            "fundamentalsDataAsOf": f.data_as_of if f else None,
            "quoteUpdateFrequency": "realtime",
            "fundamentalsUpdateFrequency": "daily",
            "missingFields": list(set(
                (q.missing_fields if q else []) +
                (f.missing_fields if f else [])
            )),
        }
