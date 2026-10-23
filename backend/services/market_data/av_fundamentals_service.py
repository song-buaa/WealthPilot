"""
Alpha Vantage 财报、现金流 + 分析师数据 adapter。
输入: WP symbol（如 LI:US / MSFT:US，兼容旧格式 LI.US）
输出: FundamentalsData（缺失字段为 None，端点状态保留在 provenance）
"""
import os
import json
import logging
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional
from datetime import datetime, timezone

from backend.services.market_data.schema import FundamentalsData, AnalystData
from backend.services.market_data.cache import get_cached, set_cached, FUNDAMENTALS_TTL

logger = logging.getLogger(__name__)

AV_BASE = "https://www.alphavantage.co/query"

# ── 多 Key 轮换(v3.4) ────────────────────────────────────────
import itertools as _itertools

def _load_av_keys() -> list[str]:
    """从环境变量加载所有 AV API Key。

    优先读 AV_API_KEY_1 ~ AV_API_KEY_4(多 key 轮换),
    fallback 到旧的 ALPHA_VANTAGE_API_KEY(单 key 兼容)。
    """
    keys = []
    for i in range(1, 5):
        k = os.environ.get(f"AV_API_KEY_{i}")
        if k:
            keys.append(k)
    if not keys:
        legacy = os.environ.get("ALPHA_VANTAGE_API_KEY")
        if legacy:
            keys.append(legacy)
    return keys or ["demo"]

_AV_KEYS = _load_av_keys()
_av_key_cycle = _itertools.cycle(_AV_KEYS)

def get_next_av_key() -> str:
    """Round-Robin 获取下一个 AV API Key。"""
    return next(_av_key_cycle)


def _wp_symbol_to_av_ticker(wp_symbol: str) -> Optional[str]:
    """LI:US → LI, 0700:HK → None（AV 无港股）。兼容旧格式 LI.US。"""
    from backend.utils.symbol import symbol_to_av_ticker
    return symbol_to_av_ticker(wp_symbol)


def _av_get(function: str, symbol: str, api_key: str) -> Optional[dict]:
    """调 AV REST API，返回 dict 或 None。"""
    params = {"function": function, "symbol": symbol, "apikey": api_key}
    url = AV_BASE + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        return data
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"_av_error": "invalid_response"}
    except urllib.error.HTTPError as exc:
        return {"_av_error": "rate_limited" if exc.code == 429 else
                "access_denied" if exc.code in {401, 403} else "provider_error"}
    except Exception:
        # Exception strings can contain the credential-bearing request URL.
        logger.warning("AV transport failure: %s/%s", function, symbol)
        return {"_av_error": "network_error"}


def _response_status(function, payload):
    if not isinstance(payload, dict) or not payload:
        return "missing"
    if payload.get("_av_error") in {"network_error", "invalid_response", "rate_limited", "access_denied", "provider_error"}:
        return payload["_av_error"]
    notice = str(payload.get("Information") or payload.get("Note") or "").lower()
    if notice:
        if "premium" in notice:
            return "premium"
        if any(word in notice for word in ("rate", "frequency", "per day", "per minute", "limit")):
            return "rate_limited"
        return "provider_notice"
    if "Error Message" in payload:
        return "provider_error"
    if function == "OVERVIEW":
        return "available" if payload.get("Symbol") else "missing"
    return "available" if isinstance(payload.get("quarterlyReports"), list) and payload["quarterlyReports"] else "missing"


def _endpoint(function, ticker, api_key):
    # Separate successful endpoint caches. Old aggregate/Seed entries never mask
    # recovery, and failures are retried on the next analysis, not cached for a day.
    key = f"av_fundamentals:v2:{function}:{ticker}"
    cached = get_cached(key)
    if cached is not None:
        return cached["payload"], dict(cached["diagnostic"], cache_hit=True)
    payload = _av_get(function, ticker, api_key)
    status = _response_status(function, payload)
    returned_symbol = (payload.get("Symbol") or payload.get("symbol")) if isinstance(payload, dict) else None
    if status == "available" and returned_symbol and returned_symbol != ticker:
        status = "symbol_mismatch"
    diagnostic = dict(status=status, fetched_at=datetime.now(timezone.utc).isoformat(), cache_hit=False)
    if status == "available":
        set_cached(key, dict(payload=payload, diagnostic=diagnostic), FUNDAMENTALS_TTL)
    else:
        logger.info("AV endpoint unavailable: %s/%s status=%s", function, ticker, status)
        payload = None
    return payload, diagnostic


def _safe_float(val, default=None) -> Optional[float]:
    if val is None or val == "None" or val == "-" or val == "":
        return default
    try:
        f = float(val)
        import math
        return f if math.isfinite(f) else None
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=None) -> Optional[int]:
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default



def _quarter_window(payload, limit=4):
    """Require complete, unique, consecutive fiscal quarters in one report currency."""
    rows = (payload or {}).get("quarterlyReports") or []
    if not isinstance(rows, list):
        return None
    try:
        rows = sorted(rows, key=lambda row: row["fiscalDateEnding"], reverse=True)[:limit]
        dates = [datetime.strptime(row["fiscalDateEnding"], "%Y-%m-%d").date() for row in rows]
        currencies = {row.get("reportedCurrency") for row in rows}
        currency = next(iter(currencies), None)
        if (len(rows) != limit or len(set(dates)) != limit or len(currencies) != 1
                or not isinstance(currency, str) or len(currency) != 3 or not currency.isupper()
                or any(not 60 <= (a-b).days <= 120 for a, b in zip(dates, dates[1:]))):
            return None
        return rows
    except (KeyError, TypeError, ValueError):
        return None


def _complete_sum(rows, field, *, nonnegative=False):
    values = [_safe_float(row.get(field)) for row in (rows or [])]
    if not values or any(value is None or (nonnegative and value < 0) for value in values):
        return None
    import math
    total = sum(values)
    return total if math.isfinite(total) else None


def _report_provenance(ticker, function, rows, fetched_at):
    # Fiscal period is never substituted for disclosure time.
    published = [row.get("published_at") or row.get("publicationDate") for row in rows]
    return dict(source="alphavantage", source_url="https://www.alphavantage.co/documentation/#" +
                ("cash-flow" if function == "CASH_FLOW" else "income-statement"),
        record_id=f"AV:{function}:{ticker}:{rows[0]['fiscalDateEnding']}",
        fiscal_period_start=None, fiscal_period_end=rows[0]["fiscalDateEnding"],
        fiscal_quarter_ends=[row["fiscalDateEnding"] for row in rows],
        period=rows[0]["fiscalDateEnding"], period_basis="quarterly_ttm",
        currency=rows[0]["reportedCurrency"], data_as_of=rows[0]["fiscalDateEnding"],
        published_at=max(published) if all(isinstance(value, str) and value for value in published) else None,
        fetched_at=fetched_at, valuation_observed_at=None,
        observations=[{key:row[key] for key in ("fiscalDateEnding", "reportedCurrency", "totalRevenue", "netIncome",
            "netIncomeFromContinuingOperations", "operatingCashflow", "capitalExpenditures") if key in row} for row in rows])


def statement_metrics(ticker, income, cashflow, *, fetched_at):
    """Pure AV projection; never infer missing period, amount or publication."""
    values, provenance, missing = {}, {}, []
    clocks = fetched_at if isinstance(fetched_at, dict) else {"INCOME_STATEMENT":fetched_at, "CASH_FLOW":fetched_at}
    reports = _quarter_window(income)
    history = _quarter_window(income, 8)
    previous = history[4:] if history else None
    if reports:
        meta = _report_provenance(ticker, "INCOME_STATEMENT", reports, clocks.get("INCOME_STATEMENT"))
        for source, field in (("totalRevenue", "revenue_ttm"), ("netIncome", "net_income_ttm")):
            value = _complete_sum(reports, source)
            if value is not None:
                values[field], provenance[field] = value, dict(meta, source_field=source)
            else:
                missing.append(field)
            prior = _complete_sum(previous, source)
            if value is not None and prior not in (None, 0) and previous and previous[0]["reportedCurrency"] == reports[0]["reportedCurrency"]:
                growth = "revenue_yoy" if source == "totalRevenue" else "net_income_yoy"
                values[growth] = round((value-prior)/abs(prior)*100, 1)
                provenance[growth] = dict(meta, scope="TTM year over year", source_field=source)
        values.update(report_currency=meta["currency"], fiscal_period_start=meta["fiscal_period_start"],
                      fiscal_period_end=meta["fiscal_period_end"], published_at=meta["published_at"])
    else:
        missing += ["revenue_ttm", "net_income_ttm", "INCOME_QUARTERS_INCOMPLETE_OR_INCONSISTENT"]
    cash = _quarter_window(cashflow)
    aligned = cash and (not reports or ([r["fiscalDateEnding"] for r in reports] == [r["fiscalDateEnding"] for r in cash]
               and reports[0]["reportedCurrency"] == cash[0]["reportedCurrency"]))
    if aligned:
        operating = _complete_sum(cash, "operatingCashflow")
        capex = _complete_sum(cash, "capitalExpenditures", nonnegative=True)
        meta = _report_provenance(ticker, "CASH_FLOW", cash, clocks.get("CASH_FLOW"))
        if not reports:
            values.update(report_currency=meta["currency"], fiscal_period_end=meta["fiscal_period_end"], published_at=meta["published_at"])
        if operating is not None:
            values["operating_cash_flow_ttm"] = operating
            provenance["operating_cash_flow_ttm"] = dict(meta, source_field="operatingCashflow")
        if operating is not None and capex is not None:
            for field, value, source in (
                    ("capital_expenditures_ttm", capex, "capitalExpenditures"),
                    ("free_cash_flow_ttm", operating-capex, "operatingCashflow - capitalExpenditures")):
                values[field], provenance[field] = value, dict(meta, source_field=source,
                    capex_convention="positive_cash_outflow", computation="sum_four_consecutive_quarters")
            if capex == 0:
                # A run of zero vendor values is an observation, not verified
                # absence of investment. No caller flag can certify it.
                values.pop("free_cash_flow_ttm", None)
                provenance.pop("free_cash_flow_ttm", None)
                provenance["capital_expenditures_ttm"]["verification_issues"] = ["ZERO_CAPEX_UNVERIFIED"]
                missing.append("ZERO_CAPEX_UNVERIFIED")
        else:
            missing.append("CASH_FLOW_FIELDS_OR_CAPEX_SIGN_UNVERIFIED")
    else:
        missing.append("CASH_FLOW_PERIOD_OR_CURRENCY_UNVERIFIED")
    missing += [field for field in ("operating_cash_flow_ttm", "capital_expenditures_ttm", "free_cash_flow_ttm") if field not in values]
    return values, provenance, missing

def _load_seed_fundamentals(ticker: str) -> Optional[FundamentalsData]:
    """从种子文件加载兜底基本面数据（AV 不覆盖或 API 失败时）。"""
    import json as _json
    from pathlib import Path
    seed_path = Path(__file__).resolve().parent.parent.parent.parent / "demo_seed" / "demo_seed_fundamentals.json"
    try:
        if not seed_path.exists():
            return None
        with open(seed_path, encoding="utf-8") as f:
            seeds = _json.load(f)
        data = seeds.get(ticker)
        if not data:
            return None
        return FundamentalsData(
            symbol=ticker,
            market_cap=data.get("market_cap") or data.get("market_cap_hkd") or data.get("market_cap_cny"),
            pe_ttm=data.get("pe_ratio"),
            pe_forward=data.get("forward_pe"),
            eps_ttm=data.get("eps") or data.get("eps_hkd") or data.get("eps_cny"),
            revenue_ttm=data.get("revenue_ttm") or data.get("revenue_ttm_hkd") or data.get("revenue_ttm_cny"),
            profit_margin=data.get("profit_margin"),
            high_52w=data.get("52_week_high") or data.get("52_week_high_hkd") or data.get("52_week_high_cny"),
            low_52w=data.get("52_week_low") or data.get("52_week_low_hkd") or data.get("52_week_low_cny"),
            beta=data.get("beta"),
            analyst=AnalystData(target_price_avg=data.get("analyst_target_price")),
            data_as_of="seed:" + seeds.get("_updated", "unknown"),
        )
    except Exception as e:
        logger.debug(f"种子基本面加载失败: {e}")
        return None


def fetch_fundamentals(wp_symbol: str) -> Optional[FundamentalsData]:
    """调 AV OVERVIEW + INCOME_STATEMENT + CASH_FLOW；现金流缺源保持 None。"""
    try:
        ticker = _wp_symbol_to_av_ticker(wp_symbol)
    except (ValueError, Exception):
        ticker = None
    if ticker is None:
        # AV 不支持港股/A股/基金 → 种子兜底
        raw_ticker = wp_symbol.split(":")[0] if ":" in wp_symbol else wp_symbol
        logger.info(f"AV 不支持: {wp_symbol}，尝试种子兜底")
        return _load_seed_fundamentals(raw_ticker)

    # Apply mode isolation BEFORE all caches. Demo Seed is never stored in a
    # cache consumed by the real provider path.
    from backend.core.demo_mode import PUBLIC_DEMO_MODE, DEMO_ALLOW_MARKET_DATA
    if PUBLIC_DEMO_MODE and not DEMO_ALLOW_MARKET_DATA:
        return _load_seed_fundamentals(ticker)

    import time
    api_key = get_next_av_key()
    endpoints, diagnostics = {}, {}
    for function in ("OVERVIEW", "INCOME_STATEMENT", "CASH_FLOW"):
        if endpoints:
            time.sleep(1.5)  # retain the existing rate pacing
        endpoints[function], diagnostics[function] = _endpoint(function, ticker, api_key)
    overview = endpoints["OVERVIEW"] or {}
    income, cashflow = endpoints["INCOME_STATEMENT"], endpoints["CASH_FLOW"]

    missing = []

    def get_f(key):
        val = _safe_float(overview.get(key))
        if val is None:
            missing.append(key)
        return val

    def get_i(key):
        val = _safe_int(overview.get(key))
        if val is None:
            missing.append(key)
        return val

    # 分析师
    target_price = _safe_float(overview.get("AnalystTargetPrice"))
    current_price_approx = _safe_float(overview.get("50DayMovingAverage"))
    upside_pct = None
    if current_price_approx and target_price and current_price_approx > 0:
        upside_pct = round((target_price - current_price_approx) / current_price_approx * 100, 1)

    sb = get_i("AnalystRatingStrongBuy")
    b = get_i("AnalystRatingBuy")
    h = get_i("AnalystRatingHold")
    s = get_i("AnalystRatingSell")
    ss = get_i("AnalystRatingStrongSell")

    total = (sb or 0) + (b or 0) + (h or 0) + (s or 0) + (ss or 0)
    consensus = None
    if total > 0:
        buy_pct = ((sb or 0) + (b or 0)) / total
        sell_pct = ((s or 0) + (ss or 0)) / total
        if buy_pct >= 0.7:
            consensus = "Strong Buy" if (sb or 0) / total >= 0.3 else "Buy"
        elif sell_pct >= 0.3:
            consensus = "Sell"
        else:
            consensus = "Hold"

    analyst = AnalystData(
        analyst_count=total if total > 0 else None,
        consensus=consensus,
        target_price_avg=target_price,
        target_price_upside_pct=upside_pct,
        strong_buy=sb, buy=b, hold=h, sell=s, strong_sell=ss,
    )

    statement, field_provenance, statement_missing = statement_metrics(
        ticker, income, cashflow,
        fetched_at={name:item["fetched_at"] for name,item in diagnostics.items()})
    missing += [f"{name}:{item['status']}" for name,item in diagnostics.items() if item["status"] != "available"]
    missing += statement_missing
    revenue_ttm = statement.get("revenue_ttm")
    net_income_ttm = statement.get("net_income_ttm")
    revenue_yoy = statement.get("revenue_yoy")
    net_income_yoy = statement.get("net_income_yoy")
    # OVERVIEW provides no trustworthy valuation observation timestamp.
    for name in ("pe_ttm", "pe_forward", "peg_ratio", "beta", "market_cap", "high_52w", "low_52w", "eps_ttm", "roe", "gross_margin", "profit_margin", "analyst"):
        field_provenance[name] = dict(source="alphavantage",
            source_url="https://www.alphavantage.co/documentation/#company-overview",
            record_id=f"AV:OVERVIEW:{ticker}", period=overview.get("LatestQuarter"),
            period_basis="valuation" if name in {"pe_ttm", "pe_forward", "peg_ratio", "market_cap", "high_52w", "low_52w", "beta", "analyst"} else "quarterly_ttm",
            currency=overview.get("Currency"), data_as_of=None, published_at=None,
            valuation_observed_at=None, fetched_at=diagnostics["OVERVIEW"]["fetched_at"])

    # gross_margin: AV GrossProfitTTM 是绝对值,转百分比
    gross_margin_val = _safe_float(overview.get("GrossProfitTTM"))
    gross_margin = None
    if gross_margin_val and revenue_ttm and revenue_ttm > 0:
        gm = round(gross_margin_val / revenue_ttm * 100, 1)
        if 0 < gm <= 100:
            gross_margin = gm

    # profit_margin: AV 直接给比例(如 0.393 = 39.3%)
    profit_margin_raw = _safe_float(overview.get("ProfitMargin"))
    profit_margin = round(profit_margin_raw * 100, 1) if profit_margin_raw is not None else None

    # ROE: AV 给比例(如 0.34 = 34%)
    roe_raw = _safe_float(overview.get("ReturnOnEquityTTM"))
    roe = round(roe_raw * 100, 1) if roe_raw is not None else None

    fundamentals = FundamentalsData(
        symbol=wp_symbol,
        pe_ttm=get_f("PERatio"),
        pe_forward=get_f("ForwardPE"),
        peg_ratio=get_f("PEGRatio"),
        beta=get_f("Beta"),
        market_cap=get_f("MarketCapitalization"),
        high_52w=get_f("52WeekHigh"),
        low_52w=get_f("52WeekLow"),
        eps_ttm=get_f("EPS"),
        roe=roe,
        gross_margin=gross_margin,
        profit_margin=profit_margin,
        revenue_ttm=revenue_ttm,
        revenue_yoy=revenue_yoy,
        net_income_ttm=net_income_ttm,
        net_income_yoy=net_income_yoy,
        analyst=analyst,
        operating_cash_flow_ttm=statement.get("operating_cash_flow_ttm"),
        capital_expenditures_ttm=statement.get("capital_expenditures_ttm"),
        free_cash_flow_ttm=statement.get("free_cash_flow_ttm"),
        fiscal_period_start=statement.get("fiscal_period_start"),
        fiscal_period_end=statement.get("fiscal_period_end"),
        published_at=statement.get("published_at"),
        field_provenance=field_provenance,
        # OVERVIEW has no verified valuation time. LatestQuarter is a fiscal period.
        data_as_of=None,
        valuation_currency=overview.get("Currency"),
        report_currency=statement.get("report_currency"),
        provenance=dict(source="alphavantage",source_url="https://www.alphavantage.co/documentation/",
            record_id=f"AV:FUNDAMENTALS:{ticker}",published_at=None,data_as_of=None,
            endpoints=diagnostics,
            fetched_at=max(item["fetched_at"] for item in diagnostics.values()),period=statement.get("fiscal_period_end"),
            scope="independent endpoints; valuation time unverified",is_fallback=False),
        missing_fields=missing,
    )

    return fundamentals
