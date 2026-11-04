"""Offline AV field contract, fiscal clocks and FCF conservation; no API/key usage."""
from datetime import datetime, timezone

import pytest

from backend.services.market_data.av_fundamentals_service import statement_metrics
from backend.services.market_data.evidence import record, safe_market
from backend.services.market_data.schema import FundamentalsData, MarketDataBundle

AT = datetime(2026, 10, 22, 7, 30, tzinfo=timezone.utc)
DATES = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30"]


def statements(*, published=False):
    income = {"quarterlyReports": []}
    cash = {"quarterlyReports": []}
    for date in DATES:
        common = dict(fiscalDateEnding=date, reportedCurrency="CNY")
        if published:
            common["publicationDate"] = "2026-08-20" if date == DATES[0] else date
        income["quarterlyReports"].append(dict(common, totalRevenue="100", netIncome="10"))
        cash["quarterlyReports"].append(dict(common, operatingCashflow="20", capitalExpenditures="30"))
    return income, cash


def project(income, cash):
    values, provenance, missing = statement_metrics("LI", income, cash, fetched_at=AT.isoformat())
    raw = dict(symbol="LI:US", source="alphavantage", record_id="AV:LI", fetched_at=AT.isoformat(),
        field_provenance=provenance, **values)
    return raw, missing


def test_fcf_is_exactly_four_aligned_same_currency_quarters_and_traceable():
    income, cash = statements(published=True)
    raw, missing = project(income, cash)
    assert raw["net_income_ttm"] == 40
    assert raw["operating_cash_flow_ttm"] == 80
    assert raw["capital_expenditures_ttm"] == 120
    assert raw["free_cash_flow_ttm"] == -40
    assert raw["report_currency"] == "CNY" and not missing
    meta = raw["field_provenance"]["free_cash_flow_ttm"]
    assert meta["source_field"] == "operatingCashflow - capitalExpenditures"
    assert meta["fiscal_period_end"] == "2026-06-30"
    assert meta["published_at"] == "2026-08-20"
    assert meta["fetched_at"] == AT.isoformat()
    assert meta["valuation_observed_at"] is None
    state = record("fundamentals", raw, at=AT, symbol="LI:US")
    assert state["quality"] == "valid"  # fiscal period is 114d old, not a 7d quote
    assert state["values"]["free_cash_flow_ttm"] == -40


@pytest.mark.parametrize("mutation", ["missing_quarter", "duplicate", "gap", "currency", "missing_ocf", "negative_capex", "infinite"])
def test_incomplete_or_inconsistent_cashflow_cannot_be_ttm(mutation):
    income, cash = statements()
    rows = cash["quarterlyReports"]
    if mutation == "missing_quarter":
        rows.pop()
    elif mutation == "duplicate":
        rows[1]["fiscalDateEnding"] = rows[0]["fiscalDateEnding"]
    elif mutation == "gap":
        rows[0]["fiscalDateEnding"] = "2026-09-30"
    elif mutation == "currency":
        rows[0]["reportedCurrency"] = "USD"
    elif mutation == "missing_ocf":
        rows[0]["operatingCashflow"] = "None"
    elif mutation == "negative_capex":
        rows[0]["capitalExpenditures"] = "-30"
    else:
        rows[0]["operatingCashflow"] = "inf"
    raw, missing = project(income, cash)
    assert "free_cash_flow_ttm" not in raw
    assert "free_cash_flow_ttm" in missing


def test_income_cannot_sum_three_valid_quarters_as_full_ttm():
    income, cash = statements()
    income["quarterlyReports"][0]["netIncome"] = "None"
    raw, missing = project(income, cash)
    assert "net_income_ttm" not in raw and "net_income_ttm" in missing
    assert raw["revenue_ttm"] == 400


def test_fetched_clock_cannot_invent_statement_publication():
    income, cash = statements()
    raw, _ = project(income, cash)
    assert raw["published_at"] is None
    state = record("fundamentals", raw, at=AT, symbol="LI:US")
    assert state["quality"] == "unknown"
    assert state["values"]["free_cash_flow_ttm"] is None
    assert "PUBLICATION_TIME_UNKNOWN" in state["field_quality"]["free_cash_flow_ttm"]["issues"]


def test_quarter_report_and_valuation_do_not_share_ttl_or_clocks():
    income, cash = statements(published=True)
    raw, _ = project(income, cash)
    raw["pe_ttm"] = 18
    raw["field_provenance"]["pe_ttm"] = dict(source="alphavantage", record_id="AV:PE",
        currency="USD", valuation_observed_at="2026-10-20T07:00:00+00:00", fetched_at=AT.isoformat())
    state = record("fundamentals", raw, at=AT, symbol="LI:US")
    assert state["quality"] == "partial"
    assert state["field_quality"]["net_income_ttm"]["quality"] == "valid"
    assert state["field_quality"]["pe_ttm"]["issues"] == ["STALE"]
    assert state["values"]["pe_ttm"] is None
    raw["field_provenance"]["pe_ttm"]["valuation_observed_at"] = AT.isoformat()
    assert record("fundamentals", raw, at=AT, symbol="LI:US")["quality"] == "valid"


def test_future_publication_cannot_be_repaired_by_current_fetch():
    income, cash = statements(published=True)
    raw, _ = project(income, cash)
    raw["field_provenance"]["free_cash_flow_ttm"]["published_at"] = "2026-11-01"
    state = record("fundamentals", raw, at=AT, symbol="LI:US")
    assert "PUBLICATION_CLOCK_CONFLICT" in state["field_quality"]["free_cash_flow_ttm"]["issues"]


def test_partial_fundamentals_copy_retains_only_individually_verified_fields():
    income, cash = statements(published=True)
    raw, _ = project(income, cash)
    raw.update(pe_ttm=999)
    fund = FundamentalsData(**{key: value for key, value in raw.items() if key in FundamentalsData.__dataclass_fields__})
    state = record("fundamentals", fund, at=AT, symbol="LI:US")
    bundle = MarketDataBundle(symbol="LI:US", fundamentals=fund)
    safe = safe_market(bundle, {"records": [state]})
    assert safe.fundamentals.pe_ttm is None
    assert safe.fundamentals.free_cash_flow_ttm == -40
    assert bundle.fundamentals.pe_ttm == 999


def test_original_av_adapter_consumes_cash_flow_without_inventing_provider_clocks(monkeypatch):
    from backend.services.market_data import av_fundamentals_service as adapter
    import backend.core.demo_mode as demo
    import time
    income, cash = statements()
    calls = []
    def isolated_get(function, symbol, api_key):
        calls.append(function)
        return {"OVERVIEW": {"Symbol": "LI", "LatestQuarter": DATES[0], "Currency": "USD", "PERatio": "18"},
                "INCOME_STATEMENT": income, "CASH_FLOW": cash}[function]
    monkeypatch.setattr(demo, "PUBLIC_DEMO_MODE", False)
    monkeypatch.setattr(adapter, "_av_get", isolated_get)
    monkeypatch.setattr(adapter, "get_next_av_key", lambda: "offline-test-key")
    monkeypatch.setattr(adapter, "get_cached", lambda key: None)
    monkeypatch.setattr(adapter, "set_cached", lambda *args: None)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    value = adapter.fetch_fundamentals("LI:US")
    assert calls == ["OVERVIEW", "INCOME_STATEMENT", "CASH_FLOW"]
    assert value.free_cash_flow_ttm == -40 and value.report_currency == "CNY"
    assert value.data_as_of is None and value.published_at is None and value.valuation_observed_at is None
    assert value.field_provenance["pe_ttm"]["valuation_observed_at"] is None
    assert value.field_provenance["free_cash_flow_ttm"]["published_at"] is None
    assert value.fiscal_period_start is None  # quarter ending dates do not establish a period start
    assert value.field_provenance["free_cash_flow_ttm"]["fiscal_quarter_ends"] == DATES


def test_cashflow_outage_preserves_qualified_income_and_marks_missing():
    income, _ = statements(published=True)
    raw, missing = project(income, None)
    assert raw["revenue_ttm"] == 400
    assert "free_cash_flow_ttm" not in raw and "free_cash_flow_ttm" in missing
    state = record("fundamentals", raw, at=AT, symbol="LI:US")
    assert state["quality"] == "valid"


def test_missing_field_provenance_cannot_smuggle_valuation_into_valid_report():
    income, cash = statements(published=True)
    raw, _ = project(income, cash)
    raw["pe_ttm"] = 2000
    state = record("fundamentals", raw, at=AT, symbol="LI:US")
    assert state["quality"] == "partial"
    assert state["values"]["pe_ttm"] is None
    assert "VALUATION_TIME_UNKNOWN" in state["field_quality"]["pe_ttm"]["issues"]


def test_verified_field_projection_does_not_upgrade_parent_or_precise_qualification():
    from backend.services.market_data.evidence import assess
    income, cash = statements(published=True)
    for row in cash["quarterlyReports"]:
        row["capitalExpenditures"] = "5"
    raw, _ = project(income, cash)
    raw["pe_ttm"] = 900
    assessment = assess({"symbol": "LI:US", "fundamentals": raw}, at=AT)
    assert assessment["records"][0]["quality"] == "partial"
    assert any(row["field"] == "free_cash_flow_ttm" and row["values"]["free_cash_flow_ttm"] == 60
        for row in assessment["verified_field_records"])
    assert not any(row["field"] == "pe_ttm" for row in assessment["verified_field_records"])
    assert not assessment["qualification"]["L3"]["allowed"]
    assert "FUNDAMENTALS_VERIFIED_EVIDENCE_REQUIRED" in assessment["qualification"]["L3"]["blockers"]


def test_financial_cashflow_cross_period_comparison_does_not_promote_unmatched_fields():
    income, cash = statements(published=True)
    raw, _ = project(income, cash)
    raw["field_provenance"]["free_cash_flow_ttm"]["currency"] = "USD"
    state = record("fundamentals", raw, at=AT, symbol="LI:US")
    assert state["values"]["free_cash_flow_ttm"] is None
    assert "CASH_FLOW_FINANCIAL_BASIS_CONFLICT" in state["field_quality"]["free_cash_flow_ttm"]["issues"]


def test_newer_fetch_and_publication_cannot_rejuvenate_expired_fiscal_report():
    income, cash = statements(published=True)
    raw, _ = project(income, cash)
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)
    for meta in raw["field_provenance"].values():
        meta["fetched_at"] = later.isoformat()
        meta["published_at"] = "2026-12-31"
    raw["fetched_at"] = later.isoformat()
    state = record("fundamentals", raw, at=later, symbol="LI:US")
    assert state["quality"] == "unknown"
    assert "STALE" in state["field_quality"]["free_cash_flow_ttm"]["issues"]


def test_annual_financial_report_has_its_own_clock_instead_of_valuation_ttl():
    raw = dict(symbol="LI:US",source="provider",record_id="annual-report",fetched_at=AT.isoformat(),
        revenue_ttm=400,report_currency="CNY",field_provenance={"revenue_ttm": dict(
            source="provider",record_id="report-filing",currency="CNY",period_basis="annual",
            fiscal_period_start="2025-01-01",fiscal_period_end="2025-12-31",
            published_at="2026-03-01",fetched_at=AT.isoformat())})
    assert record("fundamentals",raw,at=AT,symbol="LI:US")["quality"] == "valid"


def test_valid_report_alone_cannot_grant_precise_qualification_without_current_valuation():
    from backend.services.market_data.evidence import assess
    income, _ = statements(published=True)
    raw, _ = project(income, None)
    assessment = assess({"symbol": "LI:US", "fundamentals": raw}, at=AT)
    assert assessment["records"][0]["quality"] == "valid"
    assert "FUNDAMENTALS_VALUATION_EVIDENCE_REQUIRED" in assessment["qualification"]["L3"]["blockers"]


@pytest.mark.parametrize("omitted,blocker", [("pe_ttm", "FUNDAMENTALS_VALUATION_EVIDENCE_REQUIRED"),
        ("revenue_ttm", "FUNDAMENTALS_OPERATING_EVIDENCE_REQUIRED")])
def test_legacy_contract_cannot_self_certify_complete_financial_evidence(omitted, blocker):
    from backend.services.market_data.evidence import assess
    raw = dict(symbol="LI:US",source="provider",record_id="legacy-fund",data_as_of="2026-10-21",
        fetched_at=AT.isoformat(),period="2026Q3",pe_ttm=20,revenue_ttm=1000,valuation_currency="USD")
    raw.pop(omitted)
    state = assess({"symbol":"LI:US","fundamentals":raw},at=AT)
    assert blocker in state["qualification"]["L3"]["blockers"]
    assert "FREE_CASH_FLOW_UNAVAILABLE" in state["issues"]


def test_field_mock_flag_cannot_hide_under_a_real_source_name():
    income, cash = statements(published=True)
    raw, _ = project(income, cash)
    raw["field_provenance"]["free_cash_flow_ttm"]["is_mock"] = True
    state = record("fundamentals",raw,at=AT,symbol="LI:US")
    assert state["values"]["free_cash_flow_ttm"] is None
    assert "SYNTHETIC" in state["field_quality"]["free_cash_flow_ttm"]["issues"]


def test_complete_separately_clocked_fundamentals_can_reach_l3_without_fcf_assumptions():
    from backend.services.market_data.evidence import assess
    from dataclasses import asdict
    from tests.test_v42_evidence import trusted_technical, verified
    income, cash = statements(published=True)
    for row in cash["quarterlyReports"]:
        row["capitalExpenditures"] = "5"
    raw, _ = project(income, cash)
    raw["pe_ttm"] = 20
    raw["field_provenance"]["pe_ttm"] = dict(source="alphavantage",record_id="AV:PE:dated",
        currency="USD",valuation_observed_at=AT.isoformat(),fetched_at=AT.isoformat())
    quote=verified("quote",current_price=20,currency="USD",price_basis="close")
    quote["data_as_of"]="2026-10-21"
    asset=dict(snapshot_id="asset-view:v1:58",context_current=True,
        issuer_exposure=dict(investment_denominator_cny=1000),
        data_quality=dict(can_propose_rebalance=True,analysis_eligibility={"B":dict(allowed=True)}))
    result=assess(dict(symbol="LI:US",quote=quote,fundamentals=raw,
        technical=asdict(trusted_technical("LI:US"))),research=[verified("research")],
        events=[verified("event")],asset_view=asset,at=AT,principles_available=True)
    assert result["qualification"]["L3"]["allowed"]
    assert not result["qualification"]["L4"]["allowed"]
    assert "FREE_CASH_FLOW_UNAVAILABLE" not in result["issues"]
    raw["field_provenance"]["pe_ttm"]["valuation_observed_at"]=None
    blocked=assess(dict(symbol="LI:US",fundamentals=raw),at=AT)
    assert "FUNDAMENTALS_VALUATION_EVIDENCE_REQUIRED" in blocked["qualification"]["L3"]["blockers"]
