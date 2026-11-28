"""Small evidenced direct-security issuer projection. Never combine quantities."""
import re

from backend.utils.symbol import position_security_identity
VERSION = "issuer.2026-11-26.1"
EVIDENCE = ["https://ir.lixiang.com/investor-faqs",
            "https://ir.lixiang.com/news-releases/news-release-details/li-auto-inc-files-its-annual-report-form-20-f-3"]
IDENTITIES = {("LI:US", "USD"): "LI_AUTO", ("2015:HK", "HKD"): "LI_AUTO",
              ("02015:HK", "HKD"): "LI_AUTO",
              ("3690:HK", "HKD"): "MEITUAN", ("03690:HK", "HKD"): "MEITUAN",
              ("PDD:US", "USD"): "PDD_HOLDINGS", ("XPEV:US", "USD"): "XPENG"}
ISSUERS = {
    "LI_AUTO": ("Li Auto Inc.", "CN", EVIDENCE),
    "XPENG": ("XPeng Inc.", "CN", ["https://ir.xiaopeng.com/news-events/news-releases", "https://www.sec.gov/Archives/edgar/data/1810997/"]),
    "MEITUAN": ("Meituan", "CN", ["https://www.hkexnews.hk/listedco/listconews/sehk/2024/0429/2024042900115.pdf"]),
    "PDD_HOLDINGS": ("PDD Holdings Inc.", "CN", [
        "https://investor.pddholdings.com/information-investors",
        "https://www.sec.gov/Archives/edgar/data/1737806/000110465926050727/pdd-20251231x20f.htm"]),
}


# The financial-source resolver already uses these SEC CIK identities.
_CORE_US = (
    ("0001652044", "Alphabet Inc.", ("GOOG", "GOOGL")),
    ("0000320193", "Apple Inc.", ("AAPL",)),
    ("0000789019", "Microsoft Corp.", ("MSFT",)),
    ("0001326801", "Meta Platforms Inc.", ("META",)),
    ("0001067983", "Berkshire Hathaway Inc.", ("BRK.B", "BRK-B")),
    ("0001318605", "Tesla Inc.", ("TSLA",)),
)
for cik, name, tickers in _CORE_US:
    key = "SEC_CIK_" + cik
    ISSUERS[key] = (name, "US", ["https://www.sec.gov/files/company_tickers.json",
                                "https://data.sec.gov/submissions/CIK" + cik + ".json"])
    IDENTITIES.update({(ticker + ":US", "USD"): key for ticker in tickers})


# Filing and exchange notice establish the exact listed security. Broker rows
# still need the existing canonical US/USD common-stock identity.
ANT_POLICY_LABEL = "ANT（Anthropic, PBC；Nasdaq Class A普通股）"
ANT_SECURITY = {
    "issuer_id": "SEC_CIK_0002130357",
    "cusip": "03703U107",
    "nasdaq_class": "Class A",
}
for ticker, key, name, evidence in (
    ("NVDA", "SEC_CIK_0001045810", "NVIDIA Corporation", [
        "https://data.sec.gov/submissions/CIK0001045810.json",
    ]),
    ("ANT", ANT_SECURITY["issuer_id"], "Anthropic, PBC", [
        "https://www.sec.gov/Archives/edgar/data/2130357/000162828026075973/ant-20261119.htm",
        "https://www.nasdaqtrader.com/TraderNews.aspx?id=DTN2026-28",
    ]),
):
    IDENTITIES[(ticker + ":US", "USD")] = key
    ISSUERS[key] = (name, "US", evidence)


def policy_target_identity(label):
    """Resolve a policy table label using the same listing keys as holdings."""
    ticker, _, qualifier = label.partition("（")
    combined = re.fullmatch(r"([A-Z][A-Z0-9.-]*(?:、[A-Z][A-Z0-9.-]*)+)合并）", qualifier)
    symbols = combined[1].split("、") if combined else ticker.split("/")
    identities = [IDENTITIES.get((symbol + ":US", "USD")) for symbol in symbols]
    issuer = identities[0] if identities and all(key == identities[0] for key in identities) else None
    if issuer == ANT_SECURITY["issuer_id"] and label != ANT_POLICY_LABEL:
        issuer = None
    return symbols, issuer


def direct_issuer(item):
    if item.get("economic_asset_class") != "EQUITY" or item.get("vehicle_type") not in {
            "COMMON_STOCK", "ADR", "DEPOSITARY_RECEIPT"}:
        return None
    identity = position_security_identity(item.get("symbol"), item.get("broker"), item.get("broker_security_type"),
        item.get("original_currency") or item.get("currency"))
    issuer = IDENTITIES.get(identity)
    if (item.get("security_symbol") and item["security_symbol"] != item.get("symbol")
            and item.get("security_currency")):
        canonical = IDENTITIES.get((item["security_symbol"], item["security_currency"]))
        if issuer and canonical != issuer:
            return None
        issuer = canonical
    if issuer == ANT_SECURITY["issuer_id"] and (
            identity != ("ANT:US", "USD")
            or item.get("security_symbol") not in {None, "ANT:US"}
            or item.get("security_currency") not in {None, "USD"}
            or item.get("cusip") not in {None, ANT_SECURITY["cusip"]}
            or item.get("share_class") not in {None, ANT_SECURITY["nasdaq_class"]}):
        return None
    if issuer and issuer.startswith("SEC_CIK_") and item["vehicle_type"] != "COMMON_STOCK":
        return None
    return issuer


def exposure(items):
    groups, unknown = {}, []
    for item in items:
        if item.get("economic_asset_class") != "EQUITY" or item.get("vehicle_type") not in {
                "COMMON_STOCK", "ADR", "DEPOSITARY_RECEIPT"}:
            continue  # no ETF look-through, country-by-currency or name matching
        issuer = direct_issuer(item)
        member = {key:item.get(key) for key in ("id", "symbol", "account_ref", "quantity", "cost_price",
                   "currency", "original_currency", "original_value", "current_price", "market_value_cny",
                   "security_symbol", "holding_channel", "cost_value_cny")}
        if issuer is None:
            unknown.append(member)
            continue
        name, region, evidence = ISSUERS[issuer]
        group = groups.setdefault(issuer, dict(issuer_id=issuer, name=name, economic_region=region,
            mapping_version=VERSION, evidence=evidence, market_value_cny=0, members=[]))
        group["market_value_cny"] += item.get("market_value_cny") or 0
        group["members"].append(member)
    denominator = sum(float(i.get("market_value_cny") or 0) for i in items)
    for group in groups.values():
        group["weight_of_investments"] = group["market_value_cny"] / denominator if denominator > 0 else None
    known_cn = sum(g["market_value_cny"] for g in groups.values() if g["economic_region"] == "CN")
    return dict(mapping_version=VERSION, companies=list(groups.values()), unmapped_direct_holdings=unknown,
                investment_denominator_cny=denominator,
                known_china_direct_equity_cny=known_cn,
                known_china_weight_of_investments=known_cn/denominator if denominator > 0 else None,
                unmapped_direct_value_cny=sum(i.get("market_value_cny") or 0 for i in unknown),
                unmapped_scope="Unknown direct issuers remain undistributed; known CN exposure is a lower bound. Funds are not looked through.",
                scope="CNY market-value exposure only; native securities/quantities/costs remain separate; no ADR conversion or fund look-through")


# Financial-source identity only. This does not expand portfolio aggregation or
# infer economic region, share classes, positions or trading identities.
def sec_ticker_matches(symbol, ticker_directory):
    """Only exact official ticker or unambiguous dot-to-hyphen class spelling."""
    import re
    if not isinstance(symbol,str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}:US",symbol) or not isinstance(ticker_directory,dict):
        return []
    ticker=symbol[:-3]
    return [r for r in ticker_directory.values() if isinstance(r,dict) and r.get('ticker') in {ticker,ticker.replace('.', '-')}]


def resolve_sec_issuer(symbol, ticker_directory, submissions, *, allow_registration_only=False):
    """Exact official ticker -> CIK -> Submissions identity, never name matching.

    Inputs must be read independently by the research source owner from SEC.
    Duplicate ticker mappings, mismatched CIK/ticker and mixed filing regimes fail
    closed. Existing portfolio issuer IDs are reused without mutating that owner.
    """
    import re
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}:US", symbol):
        return None
    ticker = symbol[:-3]
    if not isinstance(ticker_directory, dict) or not isinstance(submissions, dict):
        return None
    matches = sec_ticker_matches(symbol, ticker_directory)
    if len(matches) != 1:
        return None
    ticker = matches[0]["ticker"]
    try:
        cik = str(int(matches[0]["cik_str"])).zfill(10)
        if len(cik) != 10 or int(cik) <= 0 or int(submissions.get("cik")) != int(cik):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    if (not isinstance(submissions.get("tickers"), list) or ticker not in submissions["tickers"]
            or not isinstance(submissions.get("name"), str)):
        return None
    filings = submissions.get("filings")
    recent = filings.get("recent") if isinstance(filings,dict) else None
    if not isinstance(recent,dict) or not isinstance(recent.get("form"),list) or any(not isinstance(f,str) for f in recent["form"]):
        return None
    forms = set(recent["form"])
    domestic = bool(forms & {"10-K", "10-Q"})
    foreign = bool(forms & {"20-F", "6-K"})
    registration_only = (allow_registration_only and not domestic and not foreign and ticker == 'ANT'
                         and 'SEC_CIK_'+cik == ANT_SECURITY['issuer_id']
                         and {'8-A12B', '424B4', '8-K'} <= forms)
    if domestic == foreign and not registration_only:
        return None
    registered = sorted({row['ticker'] for row in ticker_directory.values()
                         if isinstance(row, dict) and isinstance(row.get('ticker'), str)
                         and str(row.get('cik_str', '')).zfill(10) == cik})
    return dict(symbol=symbol, issuer_id=IDENTITIES.get((symbol, "USD"), "SEC_CIK_"+cik),
                cik=cik, name=submissions["name"], issuer_type=("registration_only" if registration_only else ("foreign_private" if foreign else "domestic")),
                sec_ticker=ticker, registered_tickers=registered,
                registered_equity_tickers=registered,  # Legacy key: not a security-type proof.
                identity_source="https://www.sec.gov/files/company_tickers.json",
                submissions_url="https://data.sec.gov/submissions/CIK"+cik+".json")


def extended_common_issuer(item, listings):
    """Apply Owner-admitted listings pinned to the PolicySession to canonical rows."""
    existing = direct_issuer(item)
    symbol = item.get("symbol")
    listing = listings.get(symbol) if isinstance(symbol, str) else None
    if listing is None:
        return existing
    if (item.get("economic_asset_class") != "EQUITY" or item.get("vehicle_type") != "COMMON_STOCK"
            or (item.get("original_currency") or item.get("currency")) != "USD"
            or item.get("security_symbol") not in (None, symbol)
            or item.get("security_currency") not in (None, "USD")
            or item.get("cusip") not in (None, listing.cusip)
            or item.get("share_class") not in (None, listing.share_class)
            or (existing is not None and existing != listing.issuer_id)):
        return None
    return listing.issuer_id
