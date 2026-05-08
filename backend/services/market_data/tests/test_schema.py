"""Schema 和缓存的单元测试（不依赖外部 API）。"""
import time
from services.market_data.schema import QuoteData, FundamentalsData, AnalystData, MarketDataBundle
from services.market_data.cache import TTLCache


def test_quote_data_defaults():
    q = QuoteData(symbol="LI.US", current_price=17.63, change_pct=-2.3)
    assert q.update_frequency == "realtime"
    assert q.source == "futu"
    assert q.current_price == 17.63


def test_market_data_bundle_snapshot():
    q = QuoteData(symbol="MSFT.US", name="微软", current_price=420.0,
                  change_pct=1.5, volume=20000000,
                  high_52w=552.0, low_52w=364.0, market_cap=3e12,
                  pe_ttm=25.0, eps=4.27, dividend_yield=0.8)
    a = AnalystData(analyst_count=34, consensus="Strong Buy",
                    target_price_avg=558.0, target_price_upside_pct=32.8,
                    strong_buy=20, buy=12, hold=2, sell=0, strong_sell=0)
    f = FundamentalsData(symbol="MSFT.US", pe_ttm=25.0, pe_forward=24.7,
                         peg_ratio=1.8, beta=0.9, market_cap=3e12,
                         high_52w=552.0, low_52w=364.0, eps_ttm=4.27,
                         roe=34.0, gross_margin=70.1, profit_margin=38.3,
                         revenue_ttm=250e9, revenue_yoy=18.0,
                         net_income_ttm=95e9, net_income_yoy=23.0,
                         analyst=a)
    bundle = MarketDataBundle(symbol="MSFT.US", quote=q, fundamentals=f)
    snap = bundle.to_snapshot_dict()

    assert snap["currentPrice"] == 420.0
    assert snap["peRatio"] == 25.0
    assert snap["analystConsensus"] == "Strong Buy"
    assert snap["targetPriceUpsidePct"] == 32.8
    assert snap["revenueGrowthYoY"] == 18.0
    assert snap["quoteUpdateFrequency"] == "realtime"
    assert snap["fundamentalsUpdateFrequency"] == "daily"


def test_ttl_cache():
    cache = TTLCache()
    cache.set("k1", "v1", ttl_seconds=1)
    assert cache.get("k1") == "v1"
    time.sleep(1.1)
    assert cache.get("k1") is None


def test_missing_quote_returns_none_snapshot():
    bundle = MarketDataBundle(symbol="UNKNOWN.US", quote=None, fundamentals=None)
    snap = bundle.to_snapshot_dict()
    assert snap["currentPrice"] is None
    assert snap["analystConsensus"] is None
    assert snap["missingFields"] == []
