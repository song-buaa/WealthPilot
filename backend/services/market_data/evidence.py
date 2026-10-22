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


def record(kind, value, *, at, symbol=None):
    raw = mapping(value)
    provenance = {**mapping(raw.get("metadata")), **mapping(raw.get("provenance"))}
    merged = {**raw, **provenance}
    source = str(merged.get("source") or merged.get("source_channel") or "unknown")
    data_as_of = merged.get("data_as_of") or merged.get("date")
    text = (source + " " + str(data_as_of)).lower()
    quality = "valid"
    issues = []
    if any(x in text for x in ("seed", "demo", "mock")) or merged.get("is_seed") or merged.get("is_mock"):
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
    host = urlparse(str(source_url or "")).hostname or ""
    primary = any(host == domain or host.endswith("."+domain) for domain in
                  ("sec.gov", "hkexnews.hk", "lixiang.com", "pddholdings.com", "meituan.com"))
    return dict(kind=kind, symbol=merged.get("symbol") or symbol, source=source,
        source_url=source_url, record_id=record_id, published_at=merged.get("published_at"),
        data_as_of=data_as_of.isoformat() if isinstance(data_as_of, datetime) else data_as_of,
        fetched_at=merged.get("fetched_at"), scope=merged.get("scope") or kind,
        period=merged.get("period"), quality=quality,
        is_seed=quality=="seed", is_mock=quality=="mock", is_fallback=bool(merged.get("is_fallback")),
        primary=primary, issues=issues, values=raw)


def assess(market_data=None, *, research=(), events=(), at=None, symbol=None,
           asset_view=None, planner=None, stored_price=None, principles_available=False):
    at = at or datetime.now(timezone.utc)
    market = mapping(market_data)
    symbol = symbol or market.get("symbol")
    records = [record(kind, market[kind], at=at, symbol=symbol)
               for kind in ("quote", "fundamentals", "technical") if market.get(kind)]
    records += [record("research", r, at=at, symbol=symbol) for r in research]
    records += [record("event", r, at=at, symbol=symbol) for r in events]
    issues = []
    for kind in ("quote", "fundamentals", "technical", "research", "event"):
        if not any(r["kind"] == kind for r in records):
            issues.append(kind.upper()+"_UNAVAILABLE")
    quote, fundamental = mapping(market.get("quote")), mapping(market.get("fundamentals"))
    price = number(quote.get("current_price")) or number(stored_price)
    conflicts = []
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
                    and older["values"].get("topic") == newer["values"].get("topic")
                    and older["kind"] in {"research", "fundamentals"}):
                older["quality"] = "superseded"
                older["issues"].append("OLDER_RESEARCH_SUPERSEDED_BY_DISCLOSURE")
    if conflicts:
        for r in records:
            if r["kind"] in {"quote", "fundamentals"}:
                r["quality"] = "conflict"
    technical = mapping(market.get("technical"))
    series = technical.get("series", {})
    if not all(series.get(p, {}).get("status") == "valid" for p in ("day", "week")):
        issues.append("TECHNICAL_UNAVAILABLE")
        for r in records:
            if r["kind"] == "technical":
                r["quality"] = "unknown"
    if not principles_available:
        issues.append("PRINCIPLES_RETRIEVAL_UNAVAILABLE")
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
    if not quality.get("can_propose_rebalance", False):
        l3.append("CAN_PROPOSE_REBALANCE_FALSE")
    l3 += conflicts
    if not principles_available:
        l3.append("PRINCIPLES_RETRIEVAL_UNAVAILABLE")
    for level, blockers in (("L1", l1), ("L2", l2), ("L3", l3),
                            ("L4", l3+["MANUAL_TRADE_PREPARATION_NOT_AUTHORIZED"])):
        levels[level] = dict(allowed=not blockers, blockers=sorted(set(blockers)))
    planner = planner or {"status":"unknown", "fallback_type":None}
    if planner.get("status") != "success":
        issues.append("PLANNER_"+str(planner.get("status", "unknown")).upper())
    ceiling = .85 if levels["L3"]["allowed"] else .6 if levels["L2"]["allowed"] else .35
    return dict(schema_version="wealthpilot.position-evidence.v1", as_of=at.isoformat(), symbol=symbol,
        snapshot_id=view.get("snapshot_id"), records=records, conflicts=conflicts,
        issues=sorted(set(issues)), planner=planner, qualification=levels,
        confidence_ceiling=ceiling, technical_levels=series if "technical" in valid else None,
        infoNeeded=sorted(set(l3)), execution_authorized=False)


def safe_market(market_data, assessment):
    """Copy, keeping only verified parts for the original professional prompt."""
    from copy import copy
    if market_data is None:
        return None
    result = copy(market_data)
    for kind in ("quote", "fundamentals", "technical"):
        if not any(r["kind"] == kind and r["quality"] == "valid" for r in assessment["records"]):
            setattr(result, kind, None)
    return result
