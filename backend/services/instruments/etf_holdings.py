"""Versioned, offline NAV weight evidence. Data ingestion is an owner task."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from backend.services.investment_policy.contracts import Contract, digest

ETF_IDENTITIES = {"SPY:US": "US78462F1030", "QQQ:US": "US46090E1038", "SPMO:US": "US46138E3392"}
METHOD_VERSION = "etf-nav-lower-bounds.v1"


class Constituent(Contract):
    symbol: str = Field(pattern=r"^[A-Z0-9][A-Z0-9.-]*:[A-Z]+$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    weight_lower_bound: Decimal = Field(gt=0, le=1, max_digits=16, decimal_places=15)


class ETFHoldings(Contract):
    symbol: str
    isin: str
    holdings_as_of: date
    known_at: datetime
    valid_until: datetime
    source: str = Field(pattern=r"^https://[^\s]+$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(min_length=1)
    method_version: Literal["etf-nav-lower-bounds.v1"] = METHOD_VERSION
    weight_basis: Literal["NONNEGATIVE_EQUITY_NAV"]
    scope: Literal["PARTIAL", "COMPLETE_UNLEVERED"] = "PARTIAL"
    rows: tuple[Constituent, ...] = Field(min_length=1, max_length=1000)

    @property
    def coverage(self):
        return sum((r.weight_lower_bound for r in self.rows), Decimal(0))

    @model_validator(mode="after")
    def validate_evidence(self):
        if ETF_IDENTITIES.get(self.symbol) != self.isin:
            raise ValueError("ETF_IDENTITY_MISMATCH")
        if (self.known_at.utcoffset() is None or self.valid_until.utcoffset() is None
                or self.known_at > self.valid_until or self.holdings_as_of > self.known_at.date()):
            raise ValueError("ETF_INVALID_EVIDENCE_CLOCK")
        keys = set()
        for row in self.rows:
            ticker, market = row.symbol.split(":")
            if market == "HK" and ticker.isascii() and ticker.isdecimal():
                ticker = str(int(ticker))
            if market == "US" and ticker == "BRK-B":
                ticker = "BRK.B"
            keys.add((ticker, market, row.currency))
        if len(keys) != len(self.rows) or self.coverage > 1:
            raise ValueError("ETF_DUPLICATE_OR_EXCESS_WEIGHT")
        if self.scope == "COMPLETE_UNLEVERED" and self.coverage != 1:
            raise ValueError("ETF_FULL_DISCLOSURE_NOT_CLOSED")
        return self

    def status(self, *, snapshot_at, at, expected_sha256, valuation_as_of, valuation_basis):
        if not expected_sha256 or digest(self) != expected_sha256:
            return "VERSION_UNVERIFIED"
        if self.known_at > at or self.holdings_as_of > snapshot_at.date():
            return "NOT_YET_KNOWN"
        if at > self.valid_until:
            return "STALE"
        if valuation_basis != "close":
            return "VALUATION_DATE_UNVERIFIED"
        # Daily NAV weights cannot establish an intraday lower bound.
        from backend.services.execution_plan.market_hours import valuation_quality
        if valuation_quality(valuation_as_of, "US", at=snapshot_at, basis=valuation_basis)["status"] != "recent_close":
            return "VALUATION_DATE_UNVERIFIED"
        try:
            if len(valuation_as_of) == 10:
                valuation_date = date.fromisoformat(valuation_as_of)
            else:
                stamp = datetime.fromisoformat(valuation_as_of)
                if stamp.utcoffset() is None or stamp > snapshot_at:
                    return "VALUATION_DATE_UNVERIFIED"
                valuation_date = stamp.astimezone(ZoneInfo("America/New_York")).date()
        except (TypeError, ValueError):
            return "VALUATION_DATE_UNVERIFIED"
        return "FRESH" if valuation_date == self.holdings_as_of else "VALUATION_DATE_MISMATCH"
