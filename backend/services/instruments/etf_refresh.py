"""ETF Owner's public-source refresh. The application imports no capture code."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from hashlib import sha256
from io import BytesIO
import json
import re
from urllib.request import build_opener, HTTPRedirectHandler, Request
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from exchange_calendars import get_calendar
from openpyxl import load_workbook

from backend.services.investment_policy.contracts import canonical, digest
from backend.services.investment_policy.etf_observation import ET, MAX_LAG, ETFObservation, freshness, latest_completed_session
from backend.services.investment_policy.etf_release import AUTOMATED_SPY_PROOF, MAX_BYTES, read_owned, write_version

from .etf_holdings import ETFHoldings, ETF_IDENTITIES
from .issuers import ANT_SECURITY, extended_common_issuer, VERSION as ISSUER_VERSION

SOURCES = {
    "SPY": "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx",
    "QQQ": "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/holdings/fund?idType=ticker&productType=ETF",
    "SPMO": "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46138E339/holdings/fund?idType=cusip&productType=ETF",
}
NAV_SOURCE = "https://www.ssga.com/library-content/products/fund-data/etfs/us/navhist-us-en-spy.xlsx"
PARSER = "etf-disclosure-observation.v2"
FORMAL_CUSIPS = {"AAPL": "037833100", "GOOG": "02079K107", "GOOGL": "02079K305", "NVDA": "67066G104"}
D = Decimal


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download(url):
    # Only the fixed issuer URLs or internally constructed Nasdaq close URLs
    # reach this transport. Reject redirects before issuing another request.
    with build_opener(NoRedirect()).open(Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
        "Accept": "*/*",
    }), timeout=20) as response:
        raw = response.read(MAX_BYTES + 1)
        if response.status != 200 or response.geturl() != url or not 0 < len(raw) <= MAX_BYTES:
            raise ValueError("ETF_SOURCE_RESPONSE_INVALID")
    return raw


def capture(root, url, fetch):
    raw = fetch(url)
    if not 0 < len(raw) <= MAX_BYTES:
        raise ValueError("ETF_SOURCE_SIZE_INVALID")
    sha = sha256(raw).hexdigest()
    name = f"raw-{sha}"
    write_version(root, name, raw)
    return raw, {"url": url, "sha256": sha, "file": name, "fetched_at": datetime.now(timezone.utc).isoformat()}


def worksheets(raw, sheet):
    # Reuse the existing SPY receipt's openpyxl read-only decoding.
    with ZipFile(BytesIO(raw)) as archive:
        if sum(entry.file_size for entry in archive.infolist()) > 16 * 1024 * 1024:
            raise ValueError("ETF_WORKBOOK_EXPANSION_LIMIT")
    book = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    try:
        yield from book[sheet].values
    finally:
        book.close()


def finite(value, *, minimum=None):
    if value is None or isinstance(value, bool):
        raise ValueError("ETF_NUMERIC_VALUE_INVALID")
    number = D(str(value))
    if not number.is_finite() or abs(number) >= D("1e16") or (minimum is not None and number < minimum):
        raise ValueError("ETF_NUMERIC_VALUE_INVALID")
    return number


def observation(ticker, raw):
    """Keep all disclosed rows, signed percentages and missing weights."""
    if ticker == "SPY":
        rows = worksheets(raw, "holdings")
        fund, identity, stamp, _, header = (next(rows) for _ in range(5))
        if (not str(fund[1]).endswith("S&P 500® ETF Trust") or identity[:2] != ("Ticker Symbol:", "SPY")
                or stamp[0] != "Holdings:" or header[:8] != (
                    "Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector", "Shares Held", "Local Currency")):
            raise ValueError("ETF_SOURCE_SHAPE_INVALID")
        day = datetime.strptime(stamp[1], "As of %d-%b-%Y").date()
        details = []
        for name, symbol, cusip, _, weight, _, units, currency, *_ in rows:
            if weight is None:
                if symbol or cusip:  # footer has neither a ticker nor an identifier
                    raise ValueError("ETF_SOURCE_WEIGHT_MISSING")
                continue
            details.append((symbol, cusip, currency, "CASH" if symbol == "-" else "EQUITY_UNTYPED",
                            weight, units, None, name))
    else:
        value = json.loads(raw, parse_float=D)
        day = date.fromisoformat(value["effectiveDate"])
        if (value["cusip"] != {"QQQ": "QQQ", "SPMO": "46138E339"}[ticker]
                or value["effectiveBusinessDate"] != day.isoformat()
                or value["totalNumberOfHoldings"] != len(value["holdings"])):
            raise ValueError("ETF_SOURCE_IDENTITY_INVALID")
        details = [(r["ticker"], r["cusip"], r["currency"], r["securityTypeCode"],
                    r["percentageOfTotalNetAssets"], r["units"], r.get("marketValueBase"), r["issuerName"])
                   for r in value["holdings"]]
    if not 0 < len(details) <= 1000:
        raise ValueError("ETF_SOURCE_ROWS_INVALID")
    clean = []
    seen = set()
    for symbol, cusip, currency, kind, weight, units, value, name in details:
        if (not isinstance(name, str) or not isinstance(kind, str)
                or (symbol is not None and not isinstance(symbol, str))
                or (cusip is not None and not isinstance(cusip, str))
                or not isinstance(currency, str) or not re.fullmatch("[A-Z]{3}", currency)):
            raise ValueError("ETF_SOURCE_ROW_INVALID")
        identity = (symbol, cusip, currency, kind)
        if identity in seen:
            raise ValueError("ETF_SOURCE_DUPLICATE_ROW")
        seen.add(identity)
        clean.append({"ticker": symbol, "cusip": cusip, "currency": currency, "type": kind, "name": name,
                      "units": str(finite(units)), "value": None if value is None else str(finite(value)),
                      "observed_weight_pct": None if weight is None else str(finite(weight))})
    if not any(row["observed_weight_pct"] is not None for row in clean):
        raise ValueError("ETF_SOURCE_WEIGHT_MISSING")
    return day, clean


def observed_issuer(row, day, listings):
    ticker = {"BRK/B": "BRK.B", "BRK-B": "BRK.B"}.get(row["ticker"], row["ticker"])
    if not ticker or row["currency"] != "USD" or row["type"] not in {"COM", "EQUITY_UNTYPED"}:
        return None
    if not isinstance(row["cusip"], str) or not re.fullmatch("[A-Z0-9]{9}", row["cusip"]):
        return None
    symbol = f"{ticker}:US"
    listing = listings.get(symbol)
    expected = FORMAL_CUSIPS.get(ticker)
    if ticker == "ANT":
        expected = ANT_SECURITY["cusip"]
    if ((expected and expected != row["cusip"])
            or (listing and (not listing.cusip or listing.cusip != row["cusip"]
                             or not listing.valid_from <= day <= listing.valid_until))):
        return None
    return extended_common_issuer({"symbol": symbol, "currency": "USD", "cusip": row["cusip"],
                                   "economic_asset_class": "EQUITY", "vehicle_type": "COMMON_STOCK"}, listings)



def map_weights(rows, day, listings):
    mapped, unknown = {}, D(0)
    for row in rows:
        if row["observed_weight_pct"] is None:
            continue
        weight = D(row["observed_weight_pct"])
        issuer = observed_issuer(row, day, listings or {})
        if issuer:
            mapped[issuer] = mapped.get(issuer, D(0)) + weight
        elif row["type"] in {"COM", "EQUITY_UNTYPED", "ADR", "DRNY", "REIT"}:
            unknown += weight
    return mapped, unknown


def price_url(ticker, day):
    previous = day - timedelta(days=1)
    kind = "etf" if ticker == "SPY" else "stocks"
    return (f"https://api.nasdaq.com/api/quote/{ticker}/historical?assetclass={kind}"
            f"&fromdate={previous.isoformat()}&todate={day.isoformat()}")


def close(raw, ticker, day):
    value = json.loads(raw)
    if value["status"]["rCode"] != 200 or value["data"]["symbol"] != ticker:
        raise ValueError("ETF_CLOSE_IDENTITY_UNVERIFIED")
    rows = [r for r in value["data"]["tradesTable"]["rows"] if r["date"] == day.strftime("%m/%d/%Y")]
    if len(rows) != 1:
        raise ValueError("ETF_CLOSE_DATE_UNVERIFIED")
    price = rows[0]["close"]
    if not isinstance(price, str):
        raise ValueError("ETF_CLOSE_FORMAT_INVALID")
    return finite(price.strip("$").replace(",", ""), minimum=D("0.01"))


def qualify_spy(day, rows, holdings, root, fetch):
    """Preserve the accepted three-stock proof and add NVDA with exact CUSIP."""
    nav_raw, nav_receipt = capture(root, NAV_SOURCE, fetch)
    nav_rows = worksheets(nav_raw, "navhist")
    _, identity, _, header = (next(nav_rows) for _ in range(4))
    nav = [r[1:4] for r in nav_rows if r[0] == day.strftime("%d-%b-%Y")]
    if identity[1] != "SPY" or header[:4] != ("Date", "NAV", "Shares Outstanding", "Total Net Assets") or len(nav) != 1:
        raise ValueError("ETF_NAV_IDENTITY_OR_DATE_UNVERIFIED")
    nav_price, shares, total_nav = [finite(v, minimum=D("0.01")) for v in nav[0]]
    if shares != shares.to_integral_value() or abs(total_nav / shares - nav_price) > D("0.0000005"):
        raise ValueError("ETF_NAV_RECONCILIATION_FAILED")
    receipts = [holdings, nav_receipt]
    quote, receipt = capture(root, price_url("SPY", day), fetch)
    receipts.append(receipt)
    etf_close = close(quote, "SPY", day)
    bounds = []
    for ticker, cusip in FORMAL_CUSIPS.items():
        matched = [row for row in rows if row["ticker"] == ticker]
        if len(matched) != 1 or matched[0]["cusip"] != cusip or matched[0]["currency"] != "USD":
            raise ValueError(f"ETF_CONSTITUENT_IDENTITY_UNVERIFIED: {ticker}")
        units = finite(matched[0]["units"], minimum=D(1))
        if units != units.to_integral_value():
            raise ValueError("ETF_CONSTITUENT_QUANTITY_UNVERIFIED")
        quote, receipt = capture(root, price_url(ticker, day), fetch)
        receipts.append(receipt)
        equity_value = units * (close(quote, ticker, day) - D("0.005"))
        # C multiplies by ETF market value. When the ETF trades at a premium,
        # cap the weight so the market-value result still bounds the NAV claim.
        denominator = max(total_nav + D("0.005"), shares * (etf_close + D("0.005")))
        bound = (equity_value / denominator).quantize(D("0.000000000000001"), rounding=ROUND_DOWN)
        bounds.append({"symbol": f"{ticker}:US", "currency": "USD", "weight_lower_bound": bound})
    known = max(datetime.fromisoformat(r["fetched_at"]) for r in receipts)
    calendar = get_calendar("XNYS")
    expiry = calendar.session_open(calendar.next_session(day)).to_pydatetime() - timedelta(microseconds=1)
    if (known > expiry or any(datetime.fromisoformat(r["fetched_at"]) < calendar.session_close(day)
                              for r in receipts)):
        raise ValueError("ETF_PROOF_WINDOW_UNAVAILABLE")
    record = ETFHoldings(symbol="SPY:US", isin=ETF_IDENTITIES["SPY:US"], holdings_as_of=day,
                         known_at=known, valid_until=expiry, source=holdings["url"],
                         source_sha256=holdings["sha256"], parser_version=PARSER + AUTOMATED_SPY_PROOF,
                         weight_basis="NONNEGATIVE_EQUITY_NAV", rows=bounds)
    return record, {"inputs": receipts, "total_nav": str(total_nav), "etf_close": str(etf_close),
                    "shares_outstanding": str(shares), "weight_denominator": str(denominator)}



def refresh(root, key, portfolio_id, *, fetch=download, listings=None):
    """Fetch each official source once; serialize refreshes, retain independent last-good records."""
    import fcntl
    import os
    from urllib.error import URLError

    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from backend.services.investment_policy.etf_release import publish, read_observations, read_release, release_root

    root = release_root(root)
    if root.stat().st_uid != os.getuid():
        raise ValueError("ETF_REFRESH_OWNER_REQUIRED")
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    failures = (ValueError, OSError, TypeError, KeyError, ArithmeticError, BadZipFile, StopIteration, URLError, ParseError, IndexError, AttributeError)
    with os.fdopen(os.open(root / ".refresh.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        started = datetime.now(timezone.utc)
        latest = latest_completed_session(started)
        previous, previous_observations = {}, {}
        listing_sha = digest({s: r.model_dump(mode="json") for s, r in (listings or {}).items()})
        if os.path.lexists(root / "current.json"):
            prior_receipt, manifest = read_release(root, pub, portfolio_id, started)
            previous_observations = read_observations(root, prior_receipt, pub)
            previous = {record.symbol: record for record in manifest.records}
        diagnostics, updates, caps, observations = {}, {}, {}, {}
        for ticker, url in SOURCES.items():
            phase = "capture"
            try:
                raw, receipt = capture(root, url, fetch)
                name = f"observation-{PARSER}-{ticker}-{receipt['sha256']}.json"
                phase = "observation"
                try:
                    saved = json.loads(read_owned(root, name))
                    cached = True
                except FileNotFoundError:
                    day, rows = observation(ticker, raw)
                    if day > latest or not get_calendar("XNYS").is_session(day):
                        raise ValueError("ETF_HOLDINGS_CLOCK_UNVERIFIED")
                    saved = {"schema": PARSER, "ticker": ticker, "holdings_as_of": day.isoformat(),
                             "source": receipt, "rows": rows}
                    write_version(root, name, canonical(saved).encode())
                    cached = False
                if (saved["schema"] != PARSER or saved["ticker"] != ticker
                        or saved["source"]["sha256"] != receipt["sha256"] or saved["source"]["url"] != url):
                    raise ValueError("ETF_OBSERVATION_CACHE_BINDING_INVALID")
                day = date.fromisoformat(saved["holdings_as_of"])
                rows = saved["rows"]
                if day > latest or not get_calendar("XNYS").is_session(day):
                    raise ValueError("ETF_HOLDINGS_CLOCK_UNVERIFIED")
                phase = "identity"
                mapped, unknown = map_weights(rows, day, listings)
                lag = len(get_calendar("XNYS").sessions_in_range(day, latest)) - 1
                diagnostic = {
                    "observed_as_of": day.isoformat(), "lag_sessions": lag, "receipt": receipt,
                    "first_observed": saved["source"], "parser_version": PARSER, "issuer_version": ISSUER_VERSION,
                    "rows": len(rows), "parsed": not cached,
                    "observed_weight_sum_pct": str(sum(D(r["observed_weight_pct"] or "0") for r in rows)),
                    "issuer_observed_weight_pct": {k: str(v) for k, v in mapped.items()},
                    "unmapped_equity_observed_weight_pct": str(unknown),
                    "status": "OFFICIAL_OBSERVATION_ONLY", "upper_bound": None,
                    "complex_rows": [row for row in rows if row["type"] not in {"COM", "EQUITY_UNTYPED"}],
                }
                diagnostics[ticker] = diagnostic
                symbol = ticker + ":US"
                old_observation = previous_observations.get(symbol)
                same_observation = old_observation and (old_observation.source_sha256,
                    old_observation.parser_version, old_observation.issuer_version, old_observation.listing_sha256
                    ) == (receipt["sha256"], PARSER, ISSUER_VERSION, listing_sha)
                lag_status = "STALE" if lag > MAX_LAG else "OBSERVED"
                diagnostic["observation"] = lag_status
                if not same_observation and lag_status == "OBSERVED":
                    phase = "observation_qualification"
                    # The cache is useful for diagnostics, but signing binds interpretation
                    # to the downloaded bytes, never to mutable cached rows.
                    verified_day, verified_rows = observation(ticker, raw) if cached else (day, rows)
                    if verified_day != day:
                        raise ValueError("ETF_OBSERVATION_CACHE_BINDING_INVALID")
                    verified_weights, unmapped = map_weights(verified_rows, day, listings)
                    observations[symbol] = ETFObservation(
                        symbol=symbol, holdings_as_of=day, known_at=receipt["fetched_at"],
                        source=url, source_sha256=receipt["sha256"], parser_version=PARSER,
                        issuer_version=ISSUER_VERSION, listing_sha256=listing_sha,
                        issuer_weights_pct=verified_weights, unmapped_equity_weight_pct=unmapped,
                        disclosure_rows=len(verified_rows),
                        missing_weight_rows=sum(r["observed_weight_pct"] is None for r in verified_rows),
                        disclosed_weight_pct=sum(D(r["observed_weight_pct"] or "0") for r in verified_rows),
                    )
                old = previous.get(ticker + ":US")
                if ticker != "SPY":
                    diagnostic["formal"] = "COMPLEX_EXPOSURE_UNVERIFIED"
                elif day != latest:
                    diagnostic["formal"] = "VALUATION_SESSION_MISMATCH"
                elif old and old.source_sha256 == receipt["sha256"] and old.holdings_as_of == day:
                    diagnostic["formal"] = "UNCHANGED"
                else:
                    phase = "formal"
                    # A disk-cached observation must never be a signing input.
                    verified_day, verified_rows = observation(ticker, raw) if cached else (day, rows)
                    if verified_day != day:
                        raise ValueError("ETF_OBSERVATION_CACHE_BINDING_INVALID")
                    record, proof = qualify_spy(day, verified_rows, receipt, root, fetch)
                    write_version(root, f"proof-{record.source_sha256}-{record.known_at.timestamp():.6f}.json",
                                  canonical(proof).encode())
                    updates[record.symbol] = record
                    caps[record.symbol] = D(proof["etf_close"]) + D("0.005")
                    diagnostic["formal"] = "QUALIFIED_PARTIAL"
                    diagnostic["proof"] = proof
            except failures as exc:
                diagnostics.setdefault(ticker, {})["failure"] = {"stage": phase, "reason": str(exc)[:240]}
        now = datetime.now(timezone.utc)
        updates = {symbol: record for symbol, record in updates.items() if record.valid_until >= now}
        release, published_event = publish(
            root, key, portfolio_id, updates, now, close_caps={symbol: caps[symbol] for symbol in updates},
            observations={symbol: record for symbol, record in observations.items()
                          if freshness(record, now, latest_completed_session(now))[0] == "OBSERVED"},
            with_event=True,
        )
        report = {"started_at": started.isoformat(), "completed_at": now.isoformat(),
                  "latest_completed_session": latest.isoformat(), "sources": diagnostics,
                  "published": release.sequence if published_event else None,
                  "current_release_sequence": release.sequence if release else None,
                  "published_event": published_event}
        raw = canonical(report).encode()
        write_version(root, f"refresh-{sha256(raw).hexdigest()}.json", raw)
        return report


def register_refresh(scheduler, job):
    """Run once at 06:00 New York time the calendar day after a completed session."""
    from apscheduler.triggers.cron import CronTrigger

    def after_session():
        now = datetime.now(ET)
        previous = now.date() - timedelta(days=1)
        if get_calendar("XNYS").is_session(previous):
            job()

    scheduler.add_job(after_session, CronTrigger(hour=6, timezone=ET), id="etf_evidence_refresh",
                      max_instances=1, coalesce=True, misfire_grace_time=900)
