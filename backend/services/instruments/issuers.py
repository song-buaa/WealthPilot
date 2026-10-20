"""Small evidenced direct-security issuer projection. Never combine quantities."""
from backend.utils.symbol import position_security_identity
VERSION = "issuer.2026-10-20.1"
EVIDENCE = ["https://ir.lixiang.com/investor-faqs",
            "https://ir.lixiang.com/news-releases/news-release-details/li-auto-inc-files-its-annual-report-form-20-f-3"]
IDENTITIES = {("LI:US", "USD"): "LI_AUTO", ("2015:HK", "HKD"): "LI_AUTO",
              ("02015:HK", "HKD"): "LI_AUTO",
              ("3690:HK", "HKD"): "MEITUAN", ("03690:HK", "HKD"): "MEITUAN",
              ("PDD:US", "USD"): "PDD_HOLDINGS"}
ISSUERS = {
    "LI_AUTO": ("Li Auto Inc.", "CN", EVIDENCE),
    "MEITUAN": ("Meituan", "CN", ["https://www.hkexnews.hk/listedco/listconews/sehk/2024/0429/2024042900115.pdf"]),
    "PDD_HOLDINGS": ("PDD Holdings Inc.", "CN", [
        "https://investor.pddholdings.com/information-investors",
        "https://www.sec.gov/Archives/edgar/data/1737806/000110465926050727/pdd-20251231x20f.htm"]),
}


def exposure(items):
    groups, unknown = {}, []
    for item in items:
        if item.get("economic_asset_class") != "EQUITY" or item.get("vehicle_type") not in {
                "COMMON_STOCK", "ADR", "DEPOSITARY_RECEIPT"}:
            continue  # no ETF look-through, country-by-currency or name matching
        identity = position_security_identity(item.get("symbol"), item.get("broker"), item.get("broker_security_type"),
            item.get("original_currency") or item.get("currency"))
        issuer = IDENTITIES.get(identity)
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
