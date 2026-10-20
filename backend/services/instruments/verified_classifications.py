"""Data-driven economic classifications verified by stable instrument IDs.

The main lookup uses durable conId/ISIN evidence. The narrowly source-qualified
QQQ listing correction below handles Tiger STK ambiguity; it is versioned,
officially evidenced and never takes precedence over conflicting stable IDs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VerifiedAssetClassification:
    con_id: int | None
    isin: str | None
    vehicle_type: str
    economic_asset_class: str
    economic_asset_subclass: str | None
    verification_source: str
    verified_at: str


_VERIFIED_AT = "2026-08-18T00:00:00+00:00"

VERIFIED_ASSET_CLASSIFICATIONS = (
    VerifiedAssetClassification(
        con_id=272686955,
        isin="IE00BYXPSP02",
        vehicle_type="ETF",
        economic_asset_class="FIXED_INCOME",
        economic_asset_subclass="SHORT_TERM_TREASURY",
        verification_source="issuer_verified_case1_instrument",
        verified_at=_VERIFIED_AT,
    ),
    VerifiedAssetClassification(
        con_id=354532794,
        isin="IE00BGYWSV06",
        vehicle_type="ETF",
        economic_asset_class="FIXED_INCOME",
        economic_asset_subclass="SHORT_TERM_CORPORATE_BOND",
        verification_source="issuer_verified_case1_instrument",
        verified_at=_VERIFIED_AT,
    ),
    VerifiedAssetClassification(
        con_id=79000139,
        isin="IE00B3VWN518",
        vehicle_type="ETF",
        economic_asset_class="FIXED_INCOME",
        economic_asset_subclass="GOVERNMENT_BOND",
        verification_source="issuer_verified_case1_instrument",
        verified_at=_VERIFIED_AT,
    ),
    VerifiedAssetClassification(
        con_id=79000224,
        isin="IE00B3VWN179",
        vehicle_type="ETF",
        economic_asset_class="FIXED_INCOME",
        economic_asset_subclass="SHORT_TERM_TREASURY",
        verification_source="issuer_verified_case1_instrument",
        verified_at=_VERIFIED_AT,
    ),
    VerifiedAssetClassification(
        con_id=354802220,
        isin="IE00BGSF1X88",
        vehicle_type="ETF",
        economic_asset_class="FIXED_INCOME",
        economic_asset_subclass="SHORT_TERM_TREASURY",
        verification_source="issuer_verified_case1_instrument",
        verified_at=_VERIFIED_AT,
    ),
    VerifiedAssetClassification(
        con_id=756733,
        isin="US78462F1030",
        vehicle_type="ETF",
        economic_asset_class="EQUITY",
        economic_asset_subclass="EQUITY_LARGE_CAP",
        verification_source="verified_equity_etf_fixture",
        verified_at=_VERIFIED_AT,
    ),
)

BY_CON_ID = {
    item.con_id: item
    for item in VERIFIED_ASSET_CLASSIFICATIONS
    if item.con_id is not None
}
BY_ISIN = {
    item.isin.upper(): item
    for item in VERIFIED_ASSET_CLASSIFICATIONS
    if item.isin
}


def find_verified_listing(evidence):
    """Narrow source-qualified correction for Tiger's STK wrapper ambiguity.

    No generic ticker/name heuristics. Conflicting stable IDs fail closed.
    Official listing/ISIN: https://www.invesco.com/us/en/financial-products/etfs/invesco-qqq-trust-series-1.html
    """
    if (evidence.broker, evidence.listing_symbol, evidence.currency) != ("tiger", "QQQ:US", "USD"):
        return None, False
    if evidence.con_id or evidence.isin and evidence.isin.upper() != "US46090E1038":
        return None, True
    # Tiger's verified STK ambiguity and its native ETF wrapper only. The same
    # underlying symbol does not turn an option/future/other contract into QQQ.
    security_type = str(evidence.broker_security_type or "").strip().upper()
    vehicle_hint = str(evidence.vehicle_type_hint or "").strip().upper()
    if security_type not in {"STK", "ETF"} or vehicle_hint not in {"", "COMMON", "COMMON_STOCK", "ETF"}:
        return None, False
    return VerifiedAssetClassification(None, "US46090E1038", "ETF", "EQUITY", "EQUITY_LARGE_CAP",
        "https://www.invesco.com/us/en/financial-products/etfs/invesco-qqq-trust-series-1.html",
        "2026-10-20"), False


def find_verified_classification(
    *,
    con_id: int | str | None = None,
    isin: str | None = None,
) -> tuple[VerifiedAssetClassification | None, bool]:
    """Return ``(record, identity_conflict)`` for stable identifiers.

    When both identifiers are supplied they must resolve to the same record.
    Conflicting evidence fails closed instead of silently choosing one source.
    """
    try:
        normalized_con_id = int(con_id) if con_id not in (None, "") else None
    except (TypeError, ValueError):
        normalized_con_id = None
    normalized_isin = str(isin or "").strip().upper() or None

    by_con_id = BY_CON_ID.get(normalized_con_id) if normalized_con_id else None
    by_isin = BY_ISIN.get(normalized_isin) if normalized_isin else None
    if by_con_id and by_isin and by_con_id != by_isin:
        return None, True
    matched = by_con_id or by_isin
    if matched is None:
        return None, False
    if normalized_con_id and matched.con_id and normalized_con_id != matched.con_id:
        return None, True
    if normalized_isin and matched.isin and normalized_isin != matched.isin.upper():
        return None, True
    return matched, False
