"""Owner-approved trusted identity mappings for the v3.15 Case 1 UAT.

IBKR proves the tradable contract.  The issuer/fund-manager pages prove that the
same ISIN is an accumulating share class.  Both sides must match before a leg
can become executable.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone


@dataclass(frozen=True)
class TrustedInstrument:
    user_alias: str
    isin: str
    con_id: int
    symbol: str
    local_symbol: str
    sec_type: str
    stock_type: str
    exchange: str
    primary_exchange: str
    currency: str
    trading_class: str
    long_name: str
    share_class: str
    verification_status: str
    verification_source: str
    verified_at: str
    resolver_version: str = "case1-v1"

    def to_dict(self) -> dict:
        return asdict(self)


_VERIFIED_AT = datetime(2026, 8, 15, tzinfo=timezone.utc).isoformat()
_CBU3_VERIFIED_AT = datetime(2026, 8, 17, tzinfo=timezone.utc).isoformat()

TRUSTED_INSTRUMENTS: dict[str, TrustedInstrument] = {
    "IBTA": TrustedInstrument(
        user_alias="IBTA", isin="IE00BYXPSP02", con_id=272686955,
        symbol="IBTA", local_symbol="IBTA", sec_type="STK", stock_type="ETF",
        exchange="LSEETF", primary_exchange="LSEETF", currency="USD",
        trading_class="EUET", long_name="ISHARES USD TRSRY 1-3Y USD A",
        share_class="ACC", verification_status="VERIFIED",
        verification_source=(
            "https://www.ishares.com/uk/professional/en/products/287340/"
            "ishares-treasury-bond-1-3yr-ucits-etf-usd-acc-fund"
        ),
        verified_at=_VERIFIED_AT,
    ),
    "VDCA": TrustedInstrument(
        user_alias="VDCA", isin="IE00BGYWSV06", con_id=354532794,
        symbol="VDCA", local_symbol="VDCA", sec_type="STK", stock_type="ETF",
        exchange="LSEETF", primary_exchange="LSEETF", currency="USD",
        trading_class="EUET", long_name="VAND USDCP1-3 USDA",
        share_class="ACC", verification_status="VERIFIED",
        verification_source=(
            "https://www.vanguard.co.uk/uk-fund-directory/product/etf/bond/9592/"
            "usd-corporate-1-3-year-bond-ucits"
        ),
        verified_at=_VERIFIED_AT,
    ),
    "CBU0": TrustedInstrument(
        user_alias="CBU0", isin="IE00B3VWN518", con_id=79000139,
        symbol="CSBGU0", local_symbol="CBU0", sec_type="STK", stock_type="ETF",
        exchange="LSEETF", primary_exchange="EBS", currency="USD",
        trading_class="EUET", long_name="ISHARES USD TRES BOND 7-10Y",
        share_class="ACC", verification_status="VERIFIED",
        verification_source=(
            "https://www.ishares.com/uk/professionals/en/products/253745/"
            "ishares-treasury-bond-7-10yr-ucits-etf-usd-acc"
        ),
        verified_at=_VERIFIED_AT,
    ),
    "CBU3": TrustedInstrument(
        user_alias="CBU3", isin="IE00B3VWN179", con_id=79000224,
        symbol="CSBGU3", local_symbol="CBU3", sec_type="STK", stock_type="ETF",
        exchange="LSEETF", primary_exchange="EBS", currency="USD",
        trading_class="EUET", long_name="ISHARES TRSY 1-3YR USD ACC B",
        share_class="ACC", verification_status="VERIFIED",
        verification_source=(
            "https://www.ishares.com/uk/individual/en/products/253499/"
            "CBU3?siteEntryPassthrough=true"
        ),
        verified_at=_CBU3_VERIFIED_AT,
    ),
    "IB01": TrustedInstrument(
        user_alias="IB01", isin="IE00BGSF1X88", con_id=354802220,
        symbol="IB01", local_symbol="IB01", sec_type="STK", stock_type="ETF",
        exchange="LSEETF", primary_exchange="LSEETF", currency="USD",
        trading_class="EUET", long_name="ISHARES US TREAS 0-1YR USD A",
        share_class="ACC", verification_status="VERIFIED",
        verification_source=(
            "https://www.ishares.com/uk/individual/en/products/307243/"
            "ishares-treasury-bond-0-1yr-ucits-etf"
        ),
        verified_at=_VERIFIED_AT,
    ),
}


EXPECTED_MARKET_RULE_IDS = {
    "IBTA": 1874,
    "VDCA": 98,
    "CBU0": 983,
    "CBU3": 983,
    "IB01": 1874,
}


def verify_resolved_instrument(alias: str, resolved: dict) -> TrustedInstrument:
    """Fail closed unless every identity field matches the approved mapping."""
    trusted = TRUSTED_INSTRUMENTS.get(alias.upper())
    if trusted is None:
        raise ValueError(f"{alias}: MANUAL_VERIFICATION_REQUIRED")
    required = {
        "con_id": trusted.con_id,
        "symbol": trusted.symbol,
        "local_symbol": trusted.local_symbol,
        "sec_type": trusted.sec_type,
        "stock_type": trusted.stock_type,
        "exchange": trusted.exchange,
        "primary_exchange": trusted.primary_exchange,
        "currency": trusted.currency,
        "trading_class": trusted.trading_class,
        "isin": trusted.isin,
    }
    mismatches = {
        key: {"expected": expected, "actual": resolved.get(key)}
        for key, expected in required.items()
        if resolved.get(key) != expected
    }
    if mismatches:
        raise ValueError(f"{alias}: trusted identity mismatch: {mismatches}")
    return trusted
