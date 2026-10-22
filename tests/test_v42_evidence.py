"""Synthetic provenance and gate regressions: no providers or user database."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from backend.services.market_data.evidence import assess
from backend.services.market_data.output_policy import enforce
from backend.services.market_data.technical_evidence import from_bars
from backend.services.execution_plan.kline_provider import KlineResult

AT = datetime(2026,10,22,7,30,tzinfo=timezone.utc)


def verified(kind, **kwargs):
    kwargs.setdefault("text","synthetic dated evidence")
    if kind=="fundamentals":
        kwargs.setdefault("pe_ttm",20)
        kwargs.setdefault("valuation_currency","USD")
    return dict(symbol="LI:US",source="vendor",record_id=f"fixture:{kind}",
        data_as_of="2026-10-22T07:00:00+00:00",published_at="2026-10-21",fetched_at=AT.isoformat(),
        period="2026Q3",**kwargs)


def test_seed_pe_conflict_is_visible_even_when_source_unusable():
    result = assess(dict(symbol="LI:US",fundamentals=dict(pe_ttm=18.2,low_52w=13.2,
        source="alphavantage",data_as_of="seed:2026-06-11")),at=AT,stored_price=11.35)
    assert "PRICE_OUTSIDE_52W_RANGE" in result["conflicts"]
    assert "FUNDAMENTALS_SYNTHETIC" in result["issues"]
    assert result["qualification"]["L1"]["allowed"]
    assert not result["qualification"]["L3"]["allowed"]
    assert result["technical_levels"] is None


def test_new_official_report_supersedes_old_internal_judgment():
    old = verified("research",topic="profitability",text="old loss opinion")
    old["data_as_of"] = old["published_at"] = "2026-04-28"
    new = verified("research",topic="profitability",source_url="https://www.hkexnews.hk/fixture.pdf",text="new official fact")
    old.update(symbol="3690:HK",period="2026Q1")
    new.update(symbol="3690:HK",period="2026Q2",text="new official profit disclosure")
    result = assess(research=[old,new],symbol="3690:HK",at=AT)
    assert result["records"][0]["quality"]=="superseded"
    assert result["records"][1]["quality"]=="valid"


def test_fetched_at_cannot_repair_unknown_source_time():
    raw = verified("fundamentals",pe_ttm=8.5)
    raw["data_as_of"]=None
    result=assess(dict(symbol="PDD:US",fundamentals=raw),at=AT)
    assert result["records"][0]["quality"] in {"unknown","conflict"}
    assert "FUNDAMENTALS_SOURCE_DATE_UNKNOWN" in result["issues"]


@pytest.mark.parametrize("rebalance,amount,expected",[(True,True,"L3"),(False,True,"L2"),(True,False,"L1")])
def test_four_independent_qualification_levels(rebalance,amount,expected):
    from dataclasses import asdict
    technical=asdict(trusted_technical("LI:US"))
    market=dict(symbol="LI:US",quote=verified("quote",current_price=20,currency="USD",price_basis="close"),
        fundamentals=verified("fundamentals"),technical=technical)
    market["quote"]["data_as_of"]="2026-10-21"
    view=dict(snapshot_id="asset-view:v1:58",context_current=True,
        issuer_exposure=dict(investment_denominator_cny=1000),
        data_quality=dict(can_propose_rebalance=rebalance,analysis_eligibility={"B":dict(allowed=amount)}))
    result=assess(market,research=[verified("research")],events=[verified("event")],
        symbol="LI:US",asset_view=view,at=AT,principles_available=True)
    allowed=[k for k,v in result["qualification"].items() if v["allowed"]]
    assert allowed[-1]==expected
    assert not result["qualification"]["L4"]["allowed"]


def test_planner_fallback_is_distinct_from_evidence_failure_and_model_cannot_bypass():
    result=assess(symbol="PDD:US",at=AT,planner=dict(status="fallback",fallback_type="JSONDecodeError"))
    out=SimpleNamespace(structured_payload=dict(decisionType="exit",confidence=.99,estimated_shares=999,
        nested=dict(limit_price=999),reason="sell at 999"),chat_answer="Sell 999",raw_text="999",llm_result=None)
    enforce(out,result)
    assert "999" not in str(out.structured_payload)+out.chat_answer+out.raw_text
    assert out.structured_payload["confidence"]<=.35
    assert out.structured_payload["planner"]["fallback_type"]=="JSONDecodeError"
    assert out.structured_payload["infoNeeded"]
    assert out.actionable is False


def trusted_technical(symbol):
    import exchange_calendars as calendars
    calendar=calendars.get_calendar("XHKG" if symbol.endswith(":HK") else "XNYS")
    dates=calendar.sessions_in_range("2025-06-01","2026-10-21")
    closes=[20+(i%17)/10 for i in range(len(dates))]
    frame=pd.DataFrame(dict(date=[str(d.date()) for d in dates],close=closes,open=closes,
        high=[p+1 for p in closes],low=[p-1 for p in closes],volume=[1000]*len(dates)))
    value=from_bars(symbol,KlineResult(frame,"broker"),at=AT)
    return value


@pytest.mark.parametrize("symbol",["LI:US","PDD:US","3690:HK"])
def test_daily_weekly_reproducible_ohlcv_and_currency(symbol):
    value=trusted_technical(symbol)
    for period in ("day","week"):
        part=value.series[period]
        assert part["status"]=="valid"
        assert part["indicators"]["macd"] is not None
        assert part["indicators"]["ma20"]==pytest.approx(sum(r["close"] for r in part["bars"][-20:])/20)
        assert part["currency"]==("HKD" if symbol.endswith(":HK") else "USD")
    assert value.data_as_of=="2026-10-21"
    assert value.series["week"]["end_at"]=="2026-10-16"
    frame=pd.DataFrame(value.series["day"]["bars"])
    frame.loc[0,"low"]=1000
    with pytest.raises(ValueError,match="INVALID_OHLCV"):
        from_bars(symbol,KlineResult(frame,"broker"),at=AT)


@pytest.mark.parametrize("raw",["", "[]", '{"route":"invented"}', '{"route":"position_single","rationale":3}'])
def test_planner_rejects_invalid_payload(raw):
    from backend.graph.decision_graph import _parse_planner_response
    with pytest.raises(ValueError):
        _parse_planner_response(raw)


def test_planner_accepts_fenced_or_strict_json():
    from backend.graph.decision_graph import _parse_planner_response
    assert _parse_planner_response('```json\n{"route":"position_single"}\n```')["route"]=="position_single"


def test_financial_period_and_cash_conversion_need_review():
    fund=verified("fundamentals",fiscal_period="2026Q1",net_income_ttm=10,free_cash_flow_ttm=-20)
    result=assess(dict(symbol="LI:US",fundamentals=fund),at=AT)
    assert "FINANCIAL_PERIOD_CONFLICT" in result["issues"]
    assert "EARNINGS_CASH_FLOW_DIVERGENCE_REQUIRES_REVIEW" in result["conflicts"]
    assert not result["qualification"]["L3"]["allowed"]


def test_existing_research_projection_does_not_promote_ingestion_or_llm_kpis():
    from research_v2.repository import evidence_records
    facts=SimpleNamespace(primary_symbol="LI:US",affected_symbols=["LI:US"],source_type="alpha_vantage_news",
        source_refs=[SimpleNamespace(ref_type="url",ref_value="https://ir.lixiang.com/fixture")],
        as_of=AT,ingested_at=AT,raw_facts={"title":"fixture official publication", "time_published":"20261021T150000", "period":"2026Q3"})
    card=SimpleNamespace(status="active",card_id="fixture-research",facts=facts,
        narrative=SimpleNamespace(event_type="earnings",extracted_kpi={"pe_ttm":99999}))
    rows=evidence_records([card])
    assert rows[0]["data_as_of"]=="2026-10-21T15:00:00+00:00"
    assert "99999" not in str(rows)
    facts.raw_facts={"title":"unknown publication"}
    rows=evidence_records([card])
    assert rows[0]["data_as_of"] is None and rows[0]["published_at"] is None
    assert rows[0]["fetched_at"]==AT.isoformat()


def test_legacy_dataframe_technical_degrades_without_blocking_peer():
    from backend.services.market_data.schema import MarketDataBundle
    result=assess(MarketDataBundle(symbol="LI:US",technical=pd.DataFrame({"close":[1,2]})),at=AT)
    assert result["technical_levels"] is None
    assert "TECHNICAL_UNAVAILABLE" in result["issues"]
    assert result["qualification"]["L1"]["allowed"]


def test_hk_cny_or_usd_valuation_cannot_masquerade_as_hkd_market_quote():
    quote=verified("quote",current_price=60,currency="CNY",price_basis="close")
    quote.update(symbol="3690:HK",data_as_of="2026-10-21")
    result=assess(dict(symbol="3690:HK",quote=quote),at=AT)
    assert "LISTED_MARKET_CURRENCY_CONFLICT" in result["issues"]
    assert result["records"][0]["quality"]=="conflict"
    assert not result["qualification"]["L3"]["allowed"]


def test_pe_eps_consistency_cannot_assume_adr_or_hk_share_basis():
    fundamentals=verified("fundamentals",pe_ttm=20,eps_ttm=1)
    result=assess(dict(symbol="LI:US",fundamentals=fundamentals),at=AT)
    assert "EPS_LISTED_UNIT_BASIS_UNVERIFIED" in result["issues"]
    assert result["records"][0]["quality"]=="unknown"
    assert not result["qualification"]["L3"]["allowed"]


def test_original_kline_provider_reuses_configured_private_key_without_live_sdk_calls(monkeypatch,tmp_path):
    from unittest.mock import Mock, patch
    from backend.services.execution_plan.kline_provider import BrokerKlineProvider
    private_path=tmp_path/"synthetic-private-key.pem"
    monkeypatch.setenv("TIGER_PRIVATE_KEY_PATH",str(private_path))
    monkeypatch.setenv("TIGER_ID","synthetic-id")
    monkeypatch.setenv("TIGER_ACCOUNT","synthetic-account")
    sdk=Mock()
    sdk.get_bars.return_value=pd.DataFrame(dict(symbol=["3690"],time=["2026-10-21"],
        open=[10],high=[11],low=[9],close=[10],volume=[1000]))
    with patch("tigeropen.common.util.signature_utils.read_private_key",return_value="synthetic-key") as reader, \
         patch("tigeropen.tiger_open_config.TigerOpenClientConfig",return_value=SimpleNamespace()), \
         patch("tigeropen.quote.quote_client.QuoteClient",return_value=sdk):
        result=BrokerKlineProvider().get_kline("3690:HK","HK")
    assert result is not None and result.source=="broker"
    reader.assert_called_once_with(str(private_path))
    sdk.get_bars.assert_called_once()
    assert not private_path.exists()  # No credential file was created or read.
