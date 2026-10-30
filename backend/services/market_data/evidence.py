"""Deterministic provenance and analysis qualification for existing PEER inputs.

No provider, persistence or trading operations. Source clocks never default to now.
"""
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import math
from urllib.parse import urlparse


def mapping(value):
    if isinstance(value, dict):
        return deepcopy(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return {}


def stamp(value):
    try:
        if isinstance(value, datetime):
            return value if value.tzinfo else None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Dates carry day precision, not intraday freshness.
        return parsed.replace(tzinfo=timezone.utc) if len(str(value)) == 10 else parsed if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (TypeError, ValueError):
        return None



FINANCIAL_FIELDS = ("revenue_ttm", "revenue_yoy", "net_income_ttm", "net_income_yoy",
    "operating_cash_flow_ttm", "capital_expenditures_ttm", "free_cash_flow_ttm",
    "roe", "gross_margin", "profit_margin", "eps_ttm")
VALUATION_FIELDS = ("pe_ttm", "pe_forward", "peg_ratio", "market_cap", "high_52w", "low_52w", "beta", "analyst")


def fundamental_field_quality(raw, *, at, symbol=None):
    """Fields have independent clocks; fetching never rejuvenates fiscal or valuation facts."""
    result = {}
    expected_currency = {"US":"USD", "HK":"HKD"}.get((symbol or "").rsplit(":", 1)[-1])
    for field in FINANCIAL_FIELDS + VALUATION_FIELDS:
        if raw.get(field) is None:
            continue
        meta = mapping(mapping(raw.get("field_provenance")).get(field))
        issues = list(meta.get("verification_issues") or [])
        source = str(meta.get("source") or "unknown").lower()
        clock = meta.get("valuation_observed_at") if field in VALUATION_FIELDS else meta.get("fiscal_period_end")
        observed, fetched, published = stamp(clock), stamp(meta.get("fetched_at")), stamp(meta.get("published_at"))
        if (any(token in source for token in ("seed", "mock", "demo", "synthetic", "fixture"))
                or meta.get("is_seed") or meta.get("is_mock")
                or meta.get("quality") in {"seed", "mock", "stale", "superseded", "conflict"}):
            issues.append("SYNTHETIC")
        if source == "unknown" or not (meta.get("source_url") or meta.get("record_id")):
            issues.append("PROVENANCE_UNVERIFIED")
        if fetched is None:
            issues.append("FETCH_TIME_UNKNOWN")
        elif fetched > at:
            issues.append("FETCH_TIME_FUTURE")
        if observed is None:
            issues.append("VALUATION_TIME_UNKNOWN" if field in VALUATION_FIELDS else "FISCAL_PERIOD_UNKNOWN")
        elif observed > at or (fetched and observed > fetched):
            issues.append("SOURCE_CLOCK_CONFLICT")
        else:
            ttl = 86400 if field in VALUATION_FIELDS else (450 if meta.get("period_basis") == "annual" else 150)*86400
            if (at-observed).total_seconds() > ttl:
                issues.append("STALE")
        if field in FINANCIAL_FIELDS:
            start = stamp(meta.get("fiscal_period_start"))
            quarters_raw = meta.get("fiscal_quarter_ends")
            quarters = [stamp(value) for value in quarters_raw] if isinstance(quarters_raw, list) else []
            quarter_window = (len(quarters) == 4 and all(quarters) and len(set(quarters)) == 4
                and quarters[0] == observed and all(60 <= (a-b).days <= 120 for a, b in zip(quarters, quarters[1:])))
            if ((meta.get("period_basis") == "quarterly_ttm" and not quarter_window)
                    or (meta.get("period_basis") != "quarterly_ttm" and (not start or (observed and start > observed)))
                    or not meta.get("period_basis")):
                issues.append("FISCAL_PERIOD_UNKNOWN")
            if published is None:
                issues.append("PUBLICATION_TIME_UNKNOWN")
            elif published > at or (fetched and published > fetched) or (observed and published < observed):
                issues.append("PUBLICATION_CLOCK_CONFLICT")
            currency = meta.get("currency")
            if not currency or (raw.get("report_currency") and currency != raw["report_currency"]):
                issues.append("REPORT_CURRENCY_UNVERIFIED")
        elif (not meta.get("currency") or (expected_currency and meta["currency"] != expected_currency)
                or (expected_currency and raw.get("valuation_currency") and raw["valuation_currency"]!=expected_currency)):
            issues.append("LISTED_MARKET_CURRENCY_CONFLICT")
        if field == "eps_ttm" and raw.get("eps_share_basis") != "listed_unit":
            issues.append("EPS_LISTED_UNIT_BASIS_UNVERIFIED")
        if field != "analyst" and number(raw.get(field)) is None:
            issues.append("VALUE_UNAVAILABLE")
        result[field] = dict(quality="valid" if not issues else "unknown",
            issues=sorted(set(issues)), provenance=meta)
    income = result.get("net_income_ttm", {}).get("provenance", {})
    cash = result.get("free_cash_flow_ttm", {}).get("provenance", {})
    if income and cash and any(income.get(key) != cash.get(key) for key in
            ("currency", "period_basis", "fiscal_period_end", "fiscal_quarter_ends")):
        for field in ("net_income_ttm", "free_cash_flow_ttm"):
            result[field]["quality"] = "unknown"
            result[field]["issues"].append("CASH_FLOW_FINANCIAL_BASIS_CONFLICT")
    return result

def technical_period_quality(technical, symbol, at):
    """Recompute original daily OHLCV; periods fail independently, never interpolate."""
    result = {p:dict(quality="unknown", issues=["PERIOD_UNAVAILABLE"]) for p in ("day","week")}
    try:
        import pandas as pd
        from backend.services.execution_plan.kline_provider import KlineResult
        from backend.services.market_data.technical_evidence import from_bars
        series=technical.get("series",{})
        value=from_bars(symbol,KlineResult(pd.DataFrame(series["day"]["bars"]),technical.get("source","unknown")),at=at)
        if value is None:
            return result
        for period in ("day","week"):
            expected=value.series[period]
            actual=series.get(period,{})
            issues=list(expected.get("issues",[]))
            if expected.get("status")!="valid":
                issues.append("PERIOD_"+expected.get("status","unavailable").upper())
            if (actual.get("symbol")!=symbol or actual.get("currency")!=expected.get("currency")
                    or actual.get("market")!=expected.get("market") or actual.get("period")!=expected.get("period")
                    or actual.get("end_at")!=expected.get("end_at")
                    or actual.get("parameters")!=expected.get("parameters")):
                issues.append("PERIOD_METADATA_MISMATCH")
            if actual.get("bars")!=expected.get("bars"):
                issues.append("OHLCV_OR_CALENDAR_MISMATCH")
            for key,n in expected.get("indicators",{}).items():
                got=number(actual.get("indicators",{}).get(key))
                if n is None or got is None or not math.isclose(got,n,rel_tol=1e-8,abs_tol=1e-8):
                    issues.append("INDICATOR_MISMATCH:"+key)
            result[period]=dict(quality="valid" if not issues else "unknown",issues=sorted(set(issues)),
                missing_trading_sessions=expected.get("coverage",{}).get("missing_trading_sessions",[]),
                calendar=expected.get("coverage",{}).get("calendar"))
    except (KeyError,TypeError,ValueError,IndexError,ImportError,AttributeError):
        for item in result.values(): item["issues"]=["INVALID_OR_UNAVAILABLE_OHLCV"]
    return result


def technical_verified(technical, symbol, at):
    """Compatibility/full-evidence gate: L3 still requires both periods."""
    return all(state["quality"]=="valid" for state in technical_period_quality(technical,symbol,at).values())


def record(kind, value, *, at, symbol=None):
    raw = mapping(value)
    provenance = {**mapping(raw.get("metadata")), **mapping(raw.get("provenance"))}
    merged = {**raw, **provenance}
    source = str(merged.get("source") or merged.get("source_channel") or "unknown")
    data_as_of = merged.get("data_as_of") or merged.get("date")
    text = (source + " " + str(data_as_of)).lower()
    quality = "valid"
    issues = []
    if any(x in text for x in ("seed", "demo", "mock", "fixture", "synthetic")) or merged.get("is_seed") or merged.get("is_mock"):
        quality = "mock" if "mock" in text or merged.get("is_mock") else "seed"
        issues.append(kind.upper()+"_SYNTHETIC")
    date = stamp(data_as_of)
    if date is None:
        if quality == "valid":
            quality = "unknown"
        issues.append(kind.upper()+"_SOURCE_DATE_UNKNOWN")
    elif date > at or (at-date).total_seconds() > {"quote":float("inf"), "fundamentals":7*86400,
            "research":90*86400, "event":7*86400, "technical":7*86400}.get(kind, 86400):
        if quality == "valid":
            quality = "stale"
        issues.append(kind.upper()+"_STALE_OR_FUTURE")
    source_url = merged.get("source_url") or merged.get("url")
    record_id = merged.get("record_id") or merged.get("id")
    if source == "unknown" or not (source_url or record_id):
        issues.append(kind.upper()+"_PROVENANCE_UNVERIFIED")
        if quality == "valid":
            quality = "unknown"
    if kind == "quote" and quality == "valid":
        from backend.services.execution_plan.market_hours import valuation_quality
        market = (symbol or merged.get("symbol") or "").rsplit(":",1)[-1]
        state = valuation_quality(data_as_of.isoformat() if isinstance(data_as_of,datetime) else data_as_of,market,at=at,basis=merged.get("price_basis"))
        if state["status"] not in {"intraday","recent_close"}:
            quality = "stale"
            issues.append("QUOTE_NOT_CURRENT_SESSION")
    if kind in {"research", "event", "fundamentals"} and not merged.get("period"):
        issues.append(kind.upper()+"_PERIOD_UNKNOWN")
        if quality == "valid":
            quality = "unknown"
    fetched = stamp(merged.get("fetched_at"))
    if fetched is None:
        issues.append(kind.upper()+"_FETCH_TIME_UNKNOWN")
        if quality=="valid":
            quality="unknown"
    elif fetched > at or (date is not None and date > fetched):
        issues.append(kind.upper()+"_SOURCE_CLOCK_CONFLICT")
        quality="conflict"
    if kind in {"research", "event"}:
        published = stamp(merged.get("published_at"))
        if published is None:
            issues.append(kind.upper()+"_PUBLICATION_TIME_UNKNOWN")
            quality="unknown" if quality=="valid" else quality
        elif published > at or (fetched is not None and published > fetched):
            issues.append(kind.upper()+"_PUBLICATION_CLOCK_CONFLICT")
            quality="conflict"
        elif (at-published).total_seconds() > (90 if kind=="research" else 7)*86400:
            issues.append(kind.upper()+"_PUBLICATION_STALE")
            quality="stale" if quality=="valid" else quality
    if kind in {"research","event"} and not (raw.get("text") or raw.get("content")):
        issues.append(kind.upper()+"_CONTENT_UNAVAILABLE")
        quality="unknown" if quality=="valid" else quality
    if kind=="quote" and (number(raw.get("current_price")) or 0)<=0:
        issues.append("QUOTE_PRICE_INVALID")
        quality="unknown" if quality=="valid" else quality
    if kind=="fundamentals" and not any(number(raw.get(k)) is not None for k in
            ("pe_ttm","revenue_ttm","net_income_ttm","profit_margin")):
        issues.append("FUNDAMENTAL_VALUES_UNAVAILABLE")
        quality="unknown" if quality=="valid" else quality
    if (merged.get("fiscal_period") and merged.get("period")
            and merged["fiscal_period"]!=merged["period"]):
        issues.append("FINANCIAL_PERIOD_CONFLICT")
        quality="conflict"
    if kind=="fundamentals" and raw.get("pe_ttm") is not None and not raw.get("valuation_currency"):
        issues.append("VALUATION_CURRENCY_UNVERIFIED")
        quality="unknown" if quality=="valid" else quality
    if kind=="fundamentals" and raw.get("eps_ttm") is not None and raw.get("eps_share_basis")!="listed_unit":
        issues.append("EPS_LISTED_UNIT_BASIS_UNVERIFIED")
        quality="unknown" if quality=="valid" else quality
    expected_currency={"US":"USD","HK":"HKD"}.get((symbol or "").rsplit(":",1)[-1])
    observed_currency=raw.get("currency") if kind=="quote" else raw.get("valuation_currency") if kind=="fundamentals" else None
    if kind=="quote" and expected_currency and not observed_currency:
        issues.append("QUOTE_CURRENCY_UNVERIFIED")
        quality="unknown" if quality=="valid" else quality
    if expected_currency and observed_currency and observed_currency!=expected_currency:
        issues.append("LISTED_MARKET_CURRENCY_CONFLICT")
        if kind != "fundamentals" or not raw.get("field_provenance"):
            quality="conflict"
    field_quality = {}
    if kind == "fundamentals" and raw.get("field_provenance"):
        field_quality = fundamental_field_quality(raw, at=at, symbol=symbol)
        fields_valid = [name for name, state in field_quality.items() if state["quality"] == "valid"]
        # Keep the original invalid source observations in field_quality, not facts.
        for name, state in field_quality.items():
            if state["quality"] != "valid":
                raw[name] = None
            issues += ["FUNDAMENTALS_" + name.upper() + "_" + issue for issue in state["issues"]]
        if quality not in {"seed", "mock", "conflict"}:
            quality = "valid" if fields_valid and len(fields_valid) == len(field_quality) else "partial" if fields_valid else "unknown"
        # The generic seven-day envelope does not apply to separately clocked reports.
        issues = [issue for issue in issues if issue not in {"FUNDAMENTALS_SOURCE_DATE_UNKNOWN", "FUNDAMENTALS_STALE_OR_FUTURE", "FUNDAMENTALS_PERIOD_UNKNOWN"}]
    period_quality = {}
    if kind == "technical":
        period_quality = technical_period_quality(raw, symbol, at)
        count = sum(state["quality"]=="valid" for state in period_quality.values())
        if quality == "valid":
            quality = "valid" if count==2 else "partial" if count else "unknown"
        issues += ["TECHNICAL_"+period.upper()+"_"+issue for period,state in period_quality.items() for issue in state["issues"]]
    host = urlparse(str(source_url or "")).hostname or ""
    primary = any(host == domain or host.endswith("."+domain) for domain in
                  ("sec.gov", "hkexnews.hk", "lixiang.com", "pddholdings.com", "meituan.com"))
    return dict(kind=kind, symbol=merged.get("symbol") or symbol, source=source,
        source_url=source_url, record_id=record_id, published_at=merged.get("published_at"),
        data_as_of=data_as_of.isoformat() if isinstance(data_as_of, datetime) else data_as_of,
        fetched_at=merged.get("fetched_at"), scope=merged.get("scope") or kind,
        period=merged.get("period"), quality=quality,
        is_seed=quality=="seed", is_mock=quality=="mock", is_fallback=bool(merged.get("is_fallback")),
        primary=primary, issues=issues, values=raw, field_quality=field_quality, period_quality=period_quality)


# Explicit statement semantics, not narrative KPIs or a caller's confirmed flag.
_OFFICIAL_FIELD_BASIS = {"totalRevenue":"total_revenue", "operatingCashflow":"operating_cash_flow",
                         "capitalExpenditures":"positive_capital_cash_outflow", "netIncome":"consolidated_net_income",
                         "operatingIncome":"operating_income", "parentNetIncome":"net_income_attributable_parent",
                         "ordinaryNetIncome":"net_income_available_to_ordinary_shareholders",
                         "costOfRevenueReported":"cost_of_revenue_as_reported", "salesMarketingReported":"sales_marketing_as_reported",
                         "generalAdminReported":"general_admin_as_reported", "researchDevelopmentReported":"research_development_as_reported",
                         "incomeTaxReported":"income_tax_as_reported",
                         "sellingGeneralAdminReported":"combined_selling_general_admin_as_reported",
                         "vehicleDeliveries":"vehicle_deliveries", "vehicleSales":"vehicle_sales", "vehicleMargin":"vehicle_margin",
                         "dilutedEPSADS":"diluted_eps_ads", "dilutedEPSOrdinary":"diluted_eps_ordinary"}


def _official_observations(raw, symbol, at, *, disclosure_verifier=None):
    # Cards/AI extraction are candidates, including their URL/date/hash claims.
    # Only a server-injected independent source owner can supply authority.
    # Absence of an independently fetched original-source verifier fails closed.
    if not callable(disclosure_verifier) or not isinstance(raw.get("record_id"), str) or not raw["record_id"]:
        return []
    verified = disclosure_verifier(raw.get("record_id"))
    if not isinstance(verified, dict) or any(verified.get(key) != raw.get(key) for key in
            ("record_id", "symbol", "source_url", "published_at", "fetched_at",
             "official_disclosure", "financial_observations")):
        return []
    proof = mapping(verified.get("source_verification"))
    digest = proof.get("document_sha256")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or not isinstance(proof.get("publication_locator"), str)
            or not proof["publication_locator"].strip()):
        return []
    from backend.services.instruments.issuers import IDENTITIES
    meta=mapping(raw.get("official_disclosure"))
    issuer=IDENTITIES.get((symbol,{"US":"USD","HK":"HKD"}.get((symbol or "").rsplit(":",1)[-1])))
    domain={"LI_AUTO":"lixiang.com","PDD_HOLDINGS":"pddholdings.com","XPENG":"xiaopeng.com","MEITUAN":"meituan.com"}.get(issuer)
    parsed=urlparse(str(raw.get("source_url") or ""))
    # Independent source-owner proof carries exact directory + Submissions identity.
    # This is never populated from a request/card or inferred company name.
    identity=mapping(proof.get("issuer_identity"))
    cik=identity.get("cik")
    sec_source=False
    if (identity.get("symbol")==symbol and isinstance(cik,str) and len(cik)==10 and cik.isdigit()
            and int(cik)>0 and identity.get("issuer_type") in {"domestic","foreign_private"}
            and identity.get("identity_source")=="https://www.sec.gov/files/company_tickers.json"
            and identity.get("submissions_url")=="https://data.sec.gov/submissions/CIK"+cik+".json"
            and isinstance(proof.get("identity_document_sha256"),str)
            and len(proof["identity_document_sha256"])==64):
        expected=issuer or "SEC_CIK_"+cik
        from research_v2.sec_disclosures import allowed_url
        sec_source=(identity.get("issuer_id")==expected and meta.get("cik")==cik
            and allowed_url(raw.get("source_url"))
            and ((parsed.hostname=="data.sec.gov" and parsed.path=="/api/xbrl/companyfacts/CIK"+cik+".json")
                 or (parsed.hostname=="www.sec.gov" and parsed.path.startswith("/Archives/edgar/data/"+str(int(cik))+"/"))))
        if sec_source: issuer=expected
    # Preserve the reviewed legacy full-submission LI parser's independent proof.
    sec_original = (issuer == "LI_AUTO" and parsed.hostname == "www.sec.gov"
                    and parsed.path.startswith("/Archives/edgar/data/") and not identity)
    host=parsed.hostname or ""
    published,fetched=stamp(raw.get("published_at")),stamp(raw.get("fetched_at"))
    if (parsed.scheme!="https" or parsed.username or parsed.password or not (sec_source or (domain and (host==domain or host.endswith("."+domain))) or sec_original)
            or not issuer or meta.get("issuer_id")!=issuer or meta.get("source_url")!=raw.get("source_url")
            or not raw.get("record_id") or not published or not fetched or not published<=fetched<=at
            or any(token in str(raw.get("source","")).lower() for token in ("seed","mock","fixture","synthetic","demo"))
            or raw.get("is_seed") or raw.get("is_mock")
            or raw.get("quality") in {"seed","mock","stale","conflict","superseded"}):
        return []
    result=[]
    for obs in raw.get("financial_observations",[]) if isinstance(raw.get("financial_observations"),list) else []:
        if not isinstance(obs,dict): continue
        key=obs.get("source_field");end=stamp(obs.get("fiscal_period_end"))
        value,scale=number(obs.get("value")),number(obs.get("unit_scale"))
        if (key not in _OFFICIAL_FIELD_BASIS or obs.get("accounting_basis") not in {_OFFICIAL_FIELD_BASIS[key], *( ["net_income_attributable_parent"] if key=="netIncome" else [])}
                or obs.get("period_basis") not in ({"quarterly","annual"} if sec_source else {"quarterly"}) or not end or end>published
                or obs.get("unit")!={"dilutedEPSADS":"currency_per_listed_unit","dilutedEPSOrdinary":"currency_per_ordinary_share","vehicleDeliveries":"vehicles","vehicleMargin":"percent"}.get(key,"currency_units") or scale not in {1,1000,1000000}
                or not isinstance(obs.get("source_locator"),str) or not obs["source_locator"].strip()
                or isinstance(obs.get("value"),bool) or isinstance(obs.get("unit_scale"),bool)
                or obs.get("currency") not in ({None} if key in {"vehicleDeliveries","vehicleMargin"} else {"CNY","USD","HKD"}) or value is None
                or not math.isfinite(value*scale)):
            continue
        result.append(dict(obs,value=value*scale,unit_scale=1,published_at=raw["published_at"],
            issuer_type=identity.get('issuer_type'),
            source_document_sha256=digest, publication_locator=proof["publication_locator"],
            issuer_id=issuer,record_id=raw["record_id"],source_url=raw["source_url"],fetched_at=raw["fetched_at"]))
    return result


def link_disclosure_clocks(market, research, at, symbol, *, disclosure_verifier=None):
    """Join existing structured official facts only after exact component matching.

    No fetch/write and no fact replacement. Every quarter (and prior-year growth
    component) needs its own matching observation; a latest release is not a TTM clock.
    disclosure_verifier is a trusted in-process source-owner dependency, never a
    card/request field. It must resolve independently checked original publication
    and observations by record ID. Hash/locator claims from a card grant no trust.
    The research owner supplies it only for independently fetched original bytes.
    """
    raw=mapping(market.get("fundamentals"))
    observations=[];projected=[]
    for item in research:
        row=mapping(item)
        official=_official_observations(row,symbol,at,disclosure_verifier=disclosure_verifier) if row.get("symbol")==symbol else []
        if official:
            # The independent server source owner, not card structure, grants trust.
            row["quality"]="official_structured_observations"
            row["financial_observations"]=official
            observations.extend(official)
        projected.append(row if official else item)
    provenance=mapping(raw.get("field_provenance"))
    if provenance: raw["field_provenance"]=provenance
    for field,meta in provenance.items():
        if not isinstance(meta,dict): continue
        if meta.get("published_at") or meta.get("unit")!="currency_units": continue
        keys=({"free_cash_flow_ttm":["operatingCashflow","capitalExpenditures"]}.get(field)
              or ([meta.get("source_field")] if meta.get("source_field") in _OFFICIAL_FIELD_BASIS else []))
        original=meta.get("observations",[]);comparison=meta.get("comparison_observations",[])
        if not isinstance(original,list) or not isinstance(comparison,list): continue
        rows=original+comparison
        if any(not isinstance(row,dict) for row in rows): continue
        quarter_ends=meta.get("fiscal_quarter_ends",[])
        if (not keys or not rows or len(quarter_ends)!=4
                or len(meta.get("observations",[]))!=4
                or {row.get("fiscalDateEnding") for row in meta.get("observations",[])}!=set(quarter_ends)
                or (field.endswith("_yoy") and len(meta.get("comparison_observations",[]))!=4)):
            continue
        links=[]
        for row in rows:
            for key in keys:
                candidates=[obs for obs in observations if obs["source_field"]==key
                    and obs["period_basis"]=="quarterly" and obs["accounting_basis"]==_OFFICIAL_FIELD_BASIS[key]
                    and obs["fiscal_period_end"]==row.get("fiscalDateEnding")
                    and obs["currency"]==row.get("reportedCurrency")]
                matches=[obs for obs in candidates if obs["value"]==number(row.get(key))]
                if not matches or len({obs["value"] for obs in candidates})!=1: break
                links.append({name:matches[0][name] for name in ("issuer_id","record_id","source_url",
                    "published_at","fetched_at","fiscal_period_end","currency","unit","unit_scale","accounting_basis","source_field","source_locator",
                    "source_document_sha256","publication_locator")})
                links[-1].update({name:matches[0][name] for name in ("reported_value","reported_unit_scale","sign_normalization") if name in matches[0]})
            else: continue
            break
        else:
            if len(links)==len(rows)*len(keys):
                meta["published_at"]=max((link["published_at"] for link in links),key=stamp)
                meta["publication_links"]=links
    if raw: market["fundamentals"]=raw
    return market,projected


def financial_source_conflicts(records):
    """Compare explicit same-quarter/currency statement observations only.

    Consume original stored facts, never narrative KPIs, user claims or fuzzy
    text extraction. Keep both source observations; do not silently overwrite AV.
    Net-income labels are intentionally not equated across consolidation bases.
    """
    conflicts = []
    fields = {"totalRevenue":"revenue_ttm", "operatingCashflow":"operating_cash_flow_ttm",
              "capitalExpenditures":"capital_expenditures_ttm", "netIncome":"net_income_ttm"}
    dependents = {"revenue_ttm":["revenue_yoy"], "operating_cash_flow_ttm":["free_cash_flow_ttm"],
                  "capital_expenditures_ttm":["free_cash_flow_ttm"], "net_income_ttm":["net_income_yoy"]}
    for disclosure in records:
        if not disclosure["primary"] or disclosure["quality"] != "valid":
            continue
        observations = disclosure["values"].get("financial_observations", [])
        # A card marked unverified is not a numerical authority merely because
        # its clocks and a whitelisted IR URL pass generic research checks.
        if disclosure["values"].get("quality") in {"unverified_until_source_checks", "unverified", "unknown", "stale", "conflict", "superseded"}:
            if observations:
                disclosure["issues"].append("FINANCIAL_OBSERVATIONS_UNVERIFIED")
            continue
        if not isinstance(observations, list):
            continue
        for parent in records:
            if parent["kind"] != "fundamentals" or parent["symbol"] != disclosure["symbol"]:
                continue
            raw_provenance = mapping(parent["values"].get("field_provenance"))
            for observation in observations:
                if not isinstance(observation, dict):
                    continue
                key = observation.get("source_field")
                if (key not in fields or observation.get("period_basis") != "quarterly"
                        or observation.get("accounting_basis") not in {None, _OFFICIAL_FIELD_BASIS[key]}):
                    continue
                for field in (fields[key],):
                    meta = mapping(raw_provenance.get(field))
                    for row in meta.get("observations", []):
                        expected, actual = number(observation.get("value")), number(row.get(key))
                        if (expected is None or actual is None or not observation.get("currency")
                                or row.get("reportedCurrency") != observation["currency"]
                                or row.get("fiscalDateEnding") != observation.get("fiscal_period_end")):
                            continue
                        # Allow only source precision rounding, not percentage
                        # tolerance that hides the real LI revenue discrepancy.
                        tolerance = number(observation.get("rounding_unit"))
                        tolerance = tolerance/2 if tolerance is not None and 0 < tolerance <= 1000000 else 0
                        if abs(expected-actual) <= tolerance:
                            continue
                        detail = dict(field=field, source_field=key, fiscal_period_end=row["fiscalDateEnding"],
                            currency=observation["currency"], provider_value=actual, official_value=expected,
                            provider_record_id=meta.get("record_id"), official_record_id=disclosure["record_id"],
                            official_source_url=disclosure["source_url"], issue="FINANCIAL_SOURCE_CONFLICT")
                        if detail not in conflicts:
                            conflicts.append(detail)
                        state = parent["field_quality"].get(field)
                        if state:
                            state["quality"] = "conflict"
                            state["issues"].append("FINANCIAL_SOURCE_CONFLICT")
                            parent["values"][field] = None
                        parent["issues"].append("FUNDAMENTALS_"+field.upper()+"_FINANCIAL_SOURCE_CONFLICT")
                        # A derived metric inherits an upstream quality problem;
                        # it is not a second direct numerical disagreement.
                        for dependent in dependents[field]:
                            derived = parent["field_quality"].get(dependent)
                            if derived and row["fiscalDateEnding"] in derived["provenance"].get("fiscal_quarter_ends", []):
                                derived["quality"] = "unknown"
                                derived["issues"].append("UPSTREAM_FINANCIAL_SOURCE_CONFLICT")
                                parent["values"][dependent] = None
                                parent["issues"].append("FUNDAMENTALS_"+dependent.upper()+"_UPSTREAM_FINANCIAL_SOURCE_CONFLICT")
            if any(item["quality"] == "conflict" for item in parent["field_quality"].values()):
                parent["quality"] = "partial" if any(item["quality"] == "valid" for item in parent["field_quality"].values()) else "conflict"
    return conflicts


def assess(market_data=None, *, research=(), events=(), at=None, symbol=None,
           asset_view=None, planner=None, stored_price=None, principles_available=False,
           disclosure_verifier=None):
    at = at or datetime.now(timezone.utc)
    market = mapping(market_data)
    symbol = symbol or market.get("symbol")
    research = list(research)
    quarter_observations = [obs for item in research if mapping(item).get("symbol") == symbol
        for obs in _official_observations(mapping(item),symbol,at,disclosure_verifier=disclosure_verifier)]
    market,research=link_disclosure_clocks(market,research,at,symbol,disclosure_verifier=disclosure_verifier)
    records = [record(kind, market[kind], at=at, symbol=symbol)
               for kind in ("quote", "fundamentals", "technical", "capital_flow") if market.get(kind) is not None]
    records += [record("research", r, at=at, symbol=symbol) for r in research]
    records += [record("event", r, at=at, symbol=symbol) for r in events]
    source_conflicts = financial_source_conflicts(records)
    issues = []
    for kind in ("quote", "fundamentals", "technical", "research", "event"):
        if not any(r["kind"] == kind for r in records):
            issues.append(kind.upper()+"_UNAVAILABLE")
    quote, fundamental = mapping(market.get("quote")), mapping(market.get("fundamentals"))
    if fundamental.get("field_provenance"):
        fundamental = next((row["values"] for row in records if row["kind"] == "fundamentals"), {})
    price = number(quote.get("current_price")) or number(stored_price)
    conflicts = ["FINANCIAL_SOURCE_CONFLICT"] if source_conflicts else []
    low, high = number(fundamental.get("low_52w")), number(fundamental.get("high_52w"))
    # Stored broker CNY per-unit valuations must NEVER enter this comparison.
    if price and ((low is not None and price < low) or (high is not None and price > high)):
        conflicts.append("PRICE_OUTSIDE_52W_RANGE")
    pe, eps = number(fundamental.get("pe_ttm")), number(fundamental.get("eps_ttm"))
    same_basis = (fundamental.get("valuation_currency") == quote.get("currency")
                  and fundamental.get("eps_share_basis") == "listed_unit")
    if price and pe and eps and same_basis and abs(price-pe*eps)/price > .15:
        conflicts.append("PE_EPS_PRICE_CONFLICT")
    if quote and fundamental.get("valuation_currency") and quote.get("currency") != fundamental["valuation_currency"]:
        conflicts.append("VALUATION_CURRENCY_CONFLICT")
    income, cashflow=number(fundamental.get("net_income_ttm")),number(fundamental.get("free_cash_flow_ttm"))
    if income is not None and cashflow is not None and income>0>cashflow:
        conflicts.append("EARNINGS_CASH_FLOW_DIVERGENCE_REQUIRES_REVIEW")
    for r in records:
        if r["symbol"] and symbol and r["symbol"] != symbol:
            r["quality"] = "conflict"
            r["issues"].append("EVIDENCE_SYMBOL_CONFLICT")
    # A later official disclosure supersedes older internal conclusions for that topic.
    for older in records:
        for newer in records:
            if older is newer or not newer["primary"] or newer["quality"] != "valid":
                continue
            old_date = stamp(older["published_at"] or older["data_as_of"])
            new_date = stamp(newer["published_at"] or newer["data_as_of"])
            if (old_date and new_date and old_date < new_date
                    and older["values"].get("topic")
                    and older["values"].get("topic") == newer["values"].get("topic")
                    and older["kind"] in {"research", "fundamentals"}):
                older["quality"] = "superseded"
                older["issues"].append("OLDER_RESEARCH_SUPERSEDED_BY_DISCLOSURE")
    if any(issue not in {"FINANCIAL_SOURCE_CONFLICT", "EARNINGS_CASH_FLOW_DIVERGENCE_REQUIRES_REVIEW"} for issue in conflicts):
        for r in records:
            if r["kind"] == "quote":
                r["quality"] = "conflict"
            elif r["kind"] == "fundamentals":
                if r["field_quality"]:
                    for field in VALUATION_FIELDS:
                        state=r["field_quality"].get(field)
                        if state:
                            state["quality"]="conflict"
                            state["issues"].append("VALUATION_SOURCE_CONFLICT")
                            r["values"][field]=None
                    r["quality"]="partial" if any(state["quality"]=="valid" for state in r["field_quality"].values()) else "conflict"
                else:
                    r["quality"]="conflict"
    technical = mapping(market.get("technical"))
    series = technical.get("series", {})
    if not technical_verified(technical,symbol,at):
        issues.append("TECHNICAL_UNAVAILABLE")
    if not principles_available:
        issues.append("PRINCIPLES_RETRIEVAL_UNAVAILABLE")
    if fundamental and number(fundamental.get("free_cash_flow_ttm")) is None:
        issues.append("FREE_CASH_FLOW_UNAVAILABLE")
    issues += conflicts + [i for r in records for i in r["issues"]]
    valid = {r["kind"] for r in records if r["quality"] == "valid"}
    view = asset_view or {}
    quality = view.get("data_quality", {})
    levels = {}
    l1 = [] if symbol else ["SYMBOL_UNRESOLVED"]
    l2 = list(l1)
    if not view.get("snapshot_id"):
        l2.append("FIXED_ASSET_VIEW_REQUIRED")
    if not view.get("context_current", False):
        l2.append("ANALYSIS_CONTEXT_EXPIRED")
    if not view.get("issuer_exposure", {}).get("investment_denominator_cny"):
        l2.append("ISSUER_DENOMINATOR_UNAVAILABLE")
    b = quality.get("analysis_eligibility", {}).get("B", {})
    if not b.get("allowed", False):
        l2.append("ASSET_AMOUNT_QUALIFICATION_REQUIRED")
    l3 = l2 + [kind.upper()+"_VERIFIED_EVIDENCE_REQUIRED" for kind in
                ("quote", "fundamentals", "technical", "research", "event") if kind not in valid]
    fundamental_records = [row for row in records if row["kind"] == "fundamentals"]
    for row in fundamental_records:
        fields = row.get("field_quality")
        if not fields:
            values = row["values"] if row["quality"] == "valid" else {}
            if not any(number(values.get(name)) is not None for name in ("pe_ttm", "pe_forward", "peg_ratio", "market_cap")):
                l3.append("FUNDAMENTALS_VALUATION_EVIDENCE_REQUIRED")
            if not any(number(values.get(name)) is not None for name in ("revenue_ttm", "net_income_ttm", "profit_margin")):
                l3.append("FUNDAMENTALS_OPERATING_EVIDENCE_REQUIRED")
            continue
        if not any(fields.get(name, {}).get("quality") == "valid" for name in ("pe_ttm", "pe_forward", "peg_ratio", "market_cap")):
            l3.append("FUNDAMENTALS_VALUATION_EVIDENCE_REQUIRED")
        if not any(fields.get(name, {}).get("quality") == "valid" for name in ("revenue_ttm", "net_income_ttm", "profit_margin")):
            l3.append("FUNDAMENTALS_OPERATING_EVIDENCE_REQUIRED")
    if not quality.get("can_propose_rebalance", False):
        l3.append("CAN_PROPOSE_REBALANCE_FALSE")
    l3 += conflicts
    if not principles_available:
        l3.append("PRINCIPLES_RETRIEVAL_UNAVAILABLE")
    for level, blockers in (("L1", l1), ("L2", l2), ("L3", l3),
                            ("L4", l3+["MANUAL_TRADE_PREPARATION_NOT_AUTHORIZED"])):
        levels[level] = dict(allowed=not blockers, blockers=sorted(set(blockers)))
    verified_field_records = []
    for parent in records:
        if parent["kind"] != "fundamentals" or parent["quality"] not in {"valid", "partial"}:
            continue
        for field, state in parent.get("field_quality", {}).items():
            if state["quality"] != "valid":
                continue
            meta = state["provenance"]
            verified_field_records.append(dict(kind="fundamental_field", field=field,
                symbol=parent["symbol"], source=meta.get("source"), source_url=meta.get("source_url"),
                record_id=str(meta.get("record_id") or meta.get("source_url"))+":"+field, quality="valid", issues=[],
                data_as_of=meta.get("valuation_observed_at") if field in VALUATION_FIELDS else meta.get("fiscal_period_end"),
                published_at=meta.get("published_at"), fetched_at=meta.get("fetched_at"),
                period=meta.get("period"), scope=meta.get("period_basis"), currency=meta.get("currency"),
                fiscal_quarter_ends=meta.get("fiscal_quarter_ends"), publication_links=meta.get("publication_links",[]),
                values={field:parent["values"][field]}, is_seed=False, is_mock=False, is_fallback=parent["is_fallback"]))
    # Independently proven quarters support L1, never the full L3 gate above.
    quarter_fields = {"totalRevenue":"revenue_quarter", "netIncome":"net_income_quarter",
                      "operatingCashflow":"operating_cash_flow_quarter", "capitalExpenditures":"capital_expenditures_quarter",
                      "operatingIncome":"operating_income_quarter", "parentNetIncome":"net_income_attributable_parent_quarter",
                      "ordinaryNetIncome":"net_income_available_to_ordinary_shareholders_quarter",
                      "costOfRevenueReported":"cost_of_revenue_reported_quarter", "salesMarketingReported":"sales_marketing_reported_quarter",
                      "generalAdminReported":"general_admin_reported_quarter", "researchDevelopmentReported":"research_development_reported_quarter",
                      "incomeTaxReported":"income_tax_reported_quarter",
                      "sellingGeneralAdminReported":"selling_general_admin_reported_quarter",
                      "vehicleDeliveries":"vehicle_deliveries_quarter", "vehicleSales":"vehicle_sales_quarter", "vehicleMargin":"vehicle_margin_pct",
                      "dilutedEPSADS":"diluted_eps_ads_quarter", "dilutedEPSOrdinary":"diluted_eps_ordinary_quarter"}
    historical_field_records=[]
    for obs in quarter_observations:
        end = stamp(obs["fiscal_period_end"])
        if not end:
            continue
        historical=(at-end).total_seconds() > (450 if obs["period_basis"]=="annual" else 150)*86400
        field = quarter_fields[obs["source_field"]]
        if obs["period_basis"]=="annual": field=field.replace("_quarter","_annual")
        if obs['source_field']=='netIncome' and obs["accounting_basis"]=="net_income_attributable_parent": field=field.replace("net_income_","net_income_attributable_parent_")
        (historical_field_records if historical else verified_field_records).append(dict(kind="fundamental_field", field=field, symbol=symbol,
            source="official_disclosure", source_url=obs["source_url"],
            record_id=obs["record_id"]+":"+field+":"+obs["fiscal_period_end"]+(":"+str(obs["currency"] or "NON_MONETARY")+":"+obs["accounting_basis"] if obs.get("fiscal_period_start") else ""),
            quality="valid", issues=[], data_as_of=obs["fiscal_period_end"],
            published_at=obs["published_at"], fetched_at=obs["fetched_at"],
            period=obs["fiscal_period_end"], scope=obs["period_basis"], currency=obs["currency"],
            historical_only=historical, accounting_basis=obs['accounting_basis'],unit=obs['unit'],
            fiscal_period_start=obs.get('fiscal_period_start'),ordinary_shares_per_ads=obs.get('ordinary_shares_per_ads'),
            issuer_type=obs.get('issuer_type'),
            publication_links=[obs], values={field:obs["value"], "currency":obs["currency"]},
            is_seed=False, is_mock=False, is_fallback=False))
    verified_period_records=[]
    for parent in records:
        if parent["kind"]!="technical" or parent["quality"]!="partial":
            continue
        for period,state in parent["period_quality"].items():
            if state["quality"]=="valid":
                row=parent["values"]["series"][period]
                verified_period_records.append(dict(kind="technical",symbol=symbol,source=parent["source"],
                    record_id=row["record_id"],source_url=None,quality="valid",issues=[],period=period,
                    data_as_of=row["end_at"],fetched_at=parent["fetched_at"],
                    values={"series":{period:row}},is_seed=False,is_mock=False))
    valid_series={p:row for p,row in series.items() if any(r["kind"]=="technical" and
        r["quality"] in {"valid","partial"} and r.get("period_quality",{}).get(p,{}).get("quality")=="valid" for r in records)}
    planner = planner or {"status":"unknown", "fallback_type":None}
    if planner.get("status") != "success":
        issues.append("PLANNER_"+str(planner.get("status", "unknown")).upper())
    ceiling = .85 if levels["L3"]["allowed"] else .6 if levels["L2"]["allowed"] else .35
    return dict(schema_version="wealthpilot.position-evidence.v1", as_of=at.isoformat(), symbol=symbol,
        snapshot_id=view.get("snapshot_id"), records=records, verified_field_records=verified_field_records, historical_field_records=historical_field_records, verified_period_records=verified_period_records, conflicts=conflicts,
        source_conflicts=source_conflicts,
        issues=sorted(set(issues)), planner=planner, qualification=levels,
        confidence_ceiling=ceiling, technical_levels=valid_series or None,
        infoNeeded=sorted(set(l3)), execution_authorized=False)


def safe_market(market_data, assessment):
    """Copy, keeping only verified parts for the original professional prompt."""
    from copy import copy
    if market_data is None:
        return None
    result = copy(market_data)
    for kind in ("quote", "fundamentals", "technical", "capital_flow"):
        records = [r for r in assessment["records"] if r["kind"] == kind]
        if kind == "fundamentals" and records and records[0].get("field_quality"):
            if records[0]["quality"] not in {"valid", "partial"}:
                setattr(result, kind, None)
                continue
            original = getattr(result, kind, None)
            if original is not None:
                original = copy(original)
                for name in FINANCIAL_FIELDS + VALUATION_FIELDS:
                    if records[0]["field_quality"].get(name, {}).get("quality") != "valid":
                        setattr(original, name, None)
                setattr(result, kind, original if any(state["quality"] == "valid" for state in records[0]["field_quality"].values()) else None)
        elif kind=="technical" and records and records[0]["quality"]=="partial":
            original=copy(getattr(result,kind))
            original.series={p:row for p,row in original.series.items() if records[0]["period_quality"].get(p,{}).get("quality")=="valid"}
            if "day" not in original.series:
                for name in ("current_price","ma5","ma20","rsi14","macd","macd_signal","macd_hist"):
                    setattr(original,name,None)
                original.ma_position="N/A"; original.trend_signal="neutral"
            setattr(result,kind,original)
        elif not any(r["quality"] == "valid" for r in records):
            setattr(result, kind, None)
    return result
