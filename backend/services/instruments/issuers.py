"""Small evidenced direct-security issuer projection. Never combine quantities."""
VERSION = "issuer.2026-10-19.1"
EVIDENCE = ["https://ir.lixiang.com/investor-faqs",
            "https://ir.lixiang.com/news-releases/news-release-details/li-auto-inc-files-its-annual-report-form-20-f-3"]
IDENTITIES = {("LI:US", "USD"): "LI_AUTO", ("2015:HK", "HKD"): "LI_AUTO",
              ("02015:HK", "HKD"): "LI_AUTO"}


def exposure(items):
    groups, unknown = {}, []
    for item in items:
        if item.get("economic_asset_class") != "EQUITY" or item.get("vehicle_type") not in {
                "COMMON_STOCK", "ADR", "DEPOSITARY_RECEIPT"}:
            continue  # no ETF look-through, country-by-currency or name matching
        identity = (item.get("symbol"), item.get("original_currency") or item.get("currency"))
        issuer = IDENTITIES.get(identity)
        member = {key:item.get(key) for key in ("id", "symbol", "account_ref", "quantity", "cost_price",
                   "currency", "original_currency", "original_value", "current_price", "market_value_cny")}
        if issuer is None:
            unknown.append(member)
            continue
        group = groups.setdefault(issuer, dict(issuer_id=issuer, name="Li Auto Inc.", economic_region="CN",
            mapping_version=VERSION, evidence=EVIDENCE, market_value_cny=0, members=[]))
        group["market_value_cny"] += item.get("market_value_cny") or 0
        group["members"].append(member)
    denominator = sum(float(i.get("market_value_cny") or 0) for i in items)
    for group in groups.values():
        group["weight_of_investments"] = group["market_value_cny"] / denominator if denominator > 0 else None
    return dict(mapping_version=VERSION, companies=list(groups.values()), unmapped_direct_holdings=unknown,
                scope="CNY market-value exposure only; native securities/quantities/costs remain separate; no ADR conversion or fund look-through")
