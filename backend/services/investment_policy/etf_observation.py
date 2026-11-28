"""Research-only projection. These weights never enter the concentration rules."""
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from exchange_calendars import get_calendar
from pydantic import Field, model_validator

from backend.services.instruments.etf_holdings import ETF_IDENTITIES
from backend.services.instruments.issuers import ISSUERS

from .contracts import Contract

ET = ZoneInfo("America/New_York")
MAX_LAG = 5


def latest_completed_session(at):
    calendar = get_calendar("XNYS")
    day = calendar.date_to_session(at.astimezone(ET).date(), direction="previous")
    if calendar.session_close(day) > at:
        day = calendar.previous_session(day)
    return day.date()


class ETFObservation(Contract):
    symbol: Literal["SPY:US", "QQQ:US", "SPMO:US"]
    holdings_as_of: date
    known_at: datetime
    source: str = Field(pattern=r"^https://[^\s]+$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    parser_version: str = Field(min_length=1)
    issuer_version: str = Field(min_length=1)
    listing_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    issuer_weights_pct: dict[str, Decimal] = Field(max_length=1000)
    unmapped_equity_weight_pct: Decimal
    disclosure_rows: int = Field(gt=0, le=1000)
    missing_weight_rows: int = Field(default=0, ge=0, le=1000)
    disclosed_weight_pct: Decimal

    @model_validator(mode="after")
    def valid(self):
        if (self.known_at.utcoffset() is None or self.holdings_as_of > self.known_at.astimezone(ET).date()
                or any(key not in ISSUERS and not (key.startswith("SEC_CIK_")
                       and len(key) == 18 and key[8:].isascii() and key[8:].isdigit())
                       for key in self.issuer_weights_pct)
                or any(not 0 <= value <= 100 for value in self.issuer_weights_pct.values())
                or not 0 <= self.unmapped_equity_weight_pct <= 200
                or not 0 <= self.disclosed_weight_pct <= 200):
            raise ValueError("ETF_OBSERVATION_INVALID")
        return self


def freshness(record, at, latest):
    calendar = get_calendar("XNYS")
    day = record.holdings_as_of
    # Old or unsupported calendar dates must not invalidate formal C.
    if day < calendar.first_session.date():
        return "STALE", None, None
    if (day > latest or not calendar.is_session(day) or record.known_at > at
            or record.known_at < calendar.session_close(day)):
        return "UNVERIFIED", None, None
    lag = len(calendar.sessions_in_range(day, latest)) - 1
    expiry_index = calendar.sessions.get_loc(calendar.date_to_session(day)) + MAX_LAG + 1
    if expiry_index >= len(calendar.sessions):
        return "UNVERIFIED", lag, None
    expiry = calendar.session_close(calendar.sessions[expiry_index]).to_pydatetime()
    return ("STALE" if lag > MAX_LAG else "OBSERVED"), lag, expiry


def project_observations(records, *, at, snapshot_at, denominator, formal_issuers, funds, listing_names):
    try:
        latest = latest_completed_session(at)
    except ValueError:
        latest = None
    sources, estimates = {}, {}
    for symbol in ETF_IDENTITIES:
        received = records.get(symbol)
        state, lag, expiry = "UNAVAILABLE", None, None
        if received:
            state, lag, expiry = freshness(received, at, latest) if latest else ("UNVERIFIED", None, None)
        exposure = funds.get(symbol)
        if exposure is None:
            state = "NOT_HELD_IN_QUALIFIED_SCOPE" if denominator is not None else "SCOPE_UNKNOWN"
        elif not exposure[1]:
            state = "SCOPE_UNKNOWN"
        sources[symbol] = {
            "status": state, "holdings_as_of": received.holdings_as_of if received else None,
            "known_at": received.known_at if received else None, "lag_sessions": lag, "expires_at": expiry,
            "source": received.source if received else None, "source_sha256": received.source_sha256 if received else None,
            "issuer_mapping_version": received.issuer_version if received else None,
            "listing_sha256": received.listing_sha256 if received else None,
            "parser_version": received.parser_version if received else None,
            "disclosure_rows": received.disclosure_rows if received else None,
            "missing_weight_rows": received.missing_weight_rows if received else None,
            "mapped_weight_pct": sum(received.issuer_weights_pct.values(), Decimal(0)) if received else None,
            "unmapped_equity_weight_pct": received.unmapped_equity_weight_pct if received else None,
            "disclosed_weight_pct": received.disclosed_weight_pct if received else None,
        }
        estimates[symbol] = ({issuer: exposure[0] * weight / 100
                              for issuer, weight in received.issuer_weights_pct.items()} if state == "OBSERVED" else {})
    issuer_ids = set(formal_issuers).union(*(values.keys() for values in estimates.values()))
    issuers = {}
    for issuer in sorted(issuer_ids):
        formal = formal_issuers.get(issuer, {})
        direct = formal.get("direct_cny") if formal.get("direct_members") else None
        # Missing constituent identity/coverage never stands for zero.
        indirect = {symbol: estimates[symbol].get(issuer) for symbol in ETF_IDENTITIES}
        available = [amount for amount in (direct, *indirect.values()) if amount is not None]
        issuers[issuer] = {
            "issuer_name": listing_names.get(issuer) or ISSUERS.get(issuer, (issuer,))[0],
            "direct_cny": direct,
            "indirect_observed_cny": indirect,
            "available_observed_cny": sum(available, Decimal(0)) if available else None,
            "coverage": "PARTIAL",
        }
    return {
        "basis": "OFFICIAL_DISCLOSURE_ESTIMATE", "status": "CONDITIONAL" if denominator is None else "OBSERVATION_ONLY",
        "snapshot_at": snapshot_at, "latest_completed_session": latest,
        "max_lag_sessions": MAX_LAG, "sources": sources, "issuers": issuers,
    }
