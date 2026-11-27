"""BTC-only policy and isolated diagnostics. No asset, provider or order I/O."""
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, localcontext
from typing import ClassVar, Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, PolicyAmendmentApproval, SnapshotV2, SourceClause

VERSION = "wp.policy.btc.v1"
SECTIONS = ("4.3", "5.1", "5.2", "5.3", "6.2", "9.3")
ADMISSION_EVIDENCE = (
    "strategy_logic_and_return_source", "multiple_market_cycles", "independent_out_of_sample",
    "walk_forward", "paper_or_shadow", "cost_slippage_liquidity", "exposure_drawdown_capital",
    "overfitting_lookahead_leakage_fill_realism",
)
PROHIBITED_TOOLS = {
    "MARGIN_SPOT", "BORROW", "PERPETUAL", "FUTURE", "OPTION", "SHORT", "LEVERAGED_PRODUCT",
}


class BtcGovernance(Contract):
    contract: Literal["wp.policy.btc.v1"] = VERSION
    strategic_assets: tuple[Literal["BTC"]] = ("BTC",)
    eligible_instrument: Literal["UNLEVERAGED_SPOT"] = "UNLEVERAGED_SPOT"
    prohibited_tools: tuple[str, ...] = tuple(sorted(PROHIBITED_TOOLS))
    consumer_credit_allowed: Literal[False] = False
    strategy_scope: Literal["RESEARCH_AND_INDEPENDENTLY_APPROVED_SPOT"] = "RESEARCH_AND_INDEPENDENTLY_APPROVED_SPOT"
    strategy_internal_btc_range: tuple[Literal[0], Literal[1]] = (0, 1)
    strategy_live_budget: Literal["NOT_AUTHORIZED"] = "NOT_AUTHORIZED"
    funding_priority: Literal["VERIFIED_EXISTING_USDT_AND_INVESTABLE_SURPLUS_AFTER_DEBT"] = "VERIFIED_EXISTING_USDT_AND_INVESTABLE_SURPLUS_AFTER_DEBT"
    strategy_budget_scope: Literal["SHARED_WITH_LONG_TERM"] = "SHARED_WITH_LONG_TERM"
    stablecoins: Literal["SETTLEMENT_CASH_REQUIRES_EXISTING_QUALIFICATION"] = "SETTLEMENT_CASH_REQUIRES_EXISTING_QUALIFICATION"
    stablecoin_yield: Literal["NOT_AUTHORIZED"] = "NOT_AUTHORIZED"
    actual_exposure: Literal["DEDUPLICATED_BTC_POSITIONS_ONLY"] = "DEDUPLICATED_BTC_POSITIONS_ONLY"
    existing_btc_rule_basis: Literal["QUALIFIED_ACTUAL_EXPOSURE"] = "QUALIFIED_ACTUAL_EXPOSURE"
    potential_exposure: Literal["DIAGNOSTIC_DEDUPLICATE_BUY_RESERVATIONS"] = "DIAGNOSTIC_DEDUPLICATE_BUY_RESERVATIONS"
    admission_evidence: tuple[str, ...] = ADMISSION_EVIDENCE
    human_strategy_approval_required: Literal[True] = True
    execution_authorized: Literal[False] = False
    content_approval: PolicyAmendmentApproval
    source_clauses: dict[str, SourceClause]

    @model_validator(mode="after")
    def contract_binding(self):
        if (self.prohibited_tools != tuple(sorted(PROHIBITED_TOOLS))
                or self.admission_evidence != ADMISSION_EVIDENCE
                or set(self.source_clauses) != set(SECTIONS)
                or any(ref.section != key or ref.document != "policy" for key, ref in self.source_clauses.items())):
            raise ValueError("BTC_GOVERNANCE_CONTRACT_MISMATCH")
        return self


def verify_btc_scope(snapshot):
    governance = snapshot.btc_governance
    if any(ref != snapshot.contextual_clauses.get(key) for key, ref in governance.source_clauses.items()):
        raise ValueError("BTC_GOVERNANCE_SOURCE_MISMATCH")


class SnapshotV2WithBtcGovernance(SnapshotV2):
    direct_target_count: ClassVar[int] = 8
    amendment_approval: PolicyAmendmentApproval
    btc_governance: BtcGovernance

    @property
    def content_status(self):
        return self.btc_governance.content_approval.status

    @model_validator(mode="after")
    def btc_approval_binding(self):
        previous = self.amendment_approval
        current = self.btc_governance.content_approval
        if (self.strategic_review_scope.content_status != "APPROVED_CONTENT"
                or previous.status != "APPROVED_CONTENT"
                or (current.approved_on and current.approved_on < previous.approved_on)
                or self.content_approved_on != (current.approved_on or previous.approved_on).isoformat()
                or (current.reference and current.reference in {
                    previous.reference, self.strategic_review_scope.content_approval_ref})):
            raise ValueError("BTC_INDEPENDENT_CONTENT_APPROVAL_REQUIRED")
        verify_btc_scope(self)
        return self


def eligibility(*, asset, instrument, purpose, identity_verified, new_consumer_credit: bool | None = None):
    """Describe policy eligibility for verified facts. Never approve execution."""
    if purpose == "RESEARCH":
        status = "RESEARCH_ONLY"
    elif not identity_verified:
        status = "UNKNOWN"
    elif asset == "BTC" and (instrument in PROHIBITED_TOOLS or new_consumer_credit):
        status = "HARD_BLOCK"
    elif purpose == "STRATEGY_LIVE":
        status = "NOT_AUTHORIZED"
    elif asset in {"USDT", "OTHER_STABLECOIN"} and instrument == "SETTLEMENT":
        status = "SETTLEMENT_ONLY_QUALIFICATION_REQUIRED"
    elif asset in {"USDT", "OTHER_STABLECOIN"} and instrument == "YIELD":
        status = "NOT_AUTHORIZED"
    elif purpose == "INVESTMENT" and asset == "BTC" and instrument == "UNLEVERAGED_SPOT":
        status = "POLICY_ELIGIBLE" if new_consumer_credit is False else "UNKNOWN"
    else:
        status = "NOT_ELIGIBLE"
    section = "5.3" if status == "HARD_BLOCK" else "9.3" if purpose == "STRATEGY_LIVE" else "4.3"
    if asset in {"USDT", "OTHER_STABLECOIN"} and purpose != "RESEARCH":
        section = "5.1"
    return {"status":status, "source_section":section,
            "execution_authorized":False}


def strategy_admission(*, qualified_evidence, btc_fraction=None):
    """A complete research dossier can go to human decision, never straight to live."""
    missing = [category for category in ADMISSION_EVIDENCE if category not in qualified_evidence]
    if btc_fraction is not None and (not btc_fraction.is_finite() or not 0 <= btc_fraction <= 1):
        status = "OUTSIDE_POLICY_POSITION_RANGE"
    elif missing or btc_fraction is None:
        status = "UNKNOWN"
    else:
        status = "READY_FOR_HUMAN_REVIEW"
    return {"status":status, "missing_evidence":missing, "source_section":"9.3",
            "live_authorization":"NOT_AUTHORIZED", "execution_authorized":False}


class ValuedFact(Contract):
    identity: str = Field(min_length=1)
    value_cny: Decimal
    qualified: bool

    @field_validator("value_cny")
    @classmethod
    def finite_nonnegative(cls, value):
        if not value.is_finite() or value < 0:
            raise ValueError("BTC_EXPOSURE_VALUE_INVALID")
        return value


class Position(ValuedFact):
    asset: Literal["BTC", "USDT", "OTHER"]
    sleeve: Literal["LONG_TERM", "STRATEGY", "OTHER"]


class OpenOrder(ValuedFact):
    side: Literal["BUY", "SELL"]
    funding_id: str | None = None


class ExposureEvidence(Contract):
    as_of: datetime
    denominator_cny: Decimal | None
    denominator_qualified: bool
    positions_complete: bool
    funding_complete: bool
    orders_complete: bool
    positions: tuple[Position, ...] = ()
    uninvested_funding: tuple[ValuedFact, ...] = ()
    open_orders: tuple[OpenOrder, ...] = ()
    strategy_budget_cny: Decimal | None = None

    @model_validator(mode="after")
    def evidence_clock(self):
        if self.as_of.utcoffset() is None:
            raise ValueError("BTC_EXPOSURE_AWARE_CLOCK_REQUIRED")
        for amount in (self.denominator_cny, self.strategy_budget_cny):
            if amount is not None and (not amount.is_finite() or amount < 0):
                raise ValueError("BTC_EXPOSURE_VALUE_INVALID")
        return self


def _qualified_unique(rows):
    by_identity = defaultdict(set)
    for row in rows:
        by_identity[row.identity].add(row)
    complete = all(len(values) == 1 and next(iter(values)).qualified for values in by_identity.values())
    return {key: next(iter(values)) for key, values in by_identity.items()
            if len(values) == 1 and next(iter(values)).qualified}, complete


def exposure_diagnostic(evidence: ExposureEvidence):
    """Combine independently qualified, common-clock CNY facts without portfolio reads."""
    positions, pos_ok = _qualified_unique(evidence.positions)
    funds, funds_ok = _qualified_unique(evidence.uninvested_funding)
    orders, orders_ok = _qualified_unique(evidence.open_orders)
    actual_ok = pos_ok and evidence.positions_complete
    extra_ok = funds_ok and orders_ok and evidence.funding_complete and evidence.orders_complete
    with localcontext() as ctx:
        ctx.prec = 60
        zero = Decimal(0)
        btc = [row for row in positions.values() if row.asset == "BTC"]
        actual = sum((row.value_cny for row in btc), zero)
        additional = sum((row.value_cny for row in funds.values()), zero)
        reservations = defaultdict(Decimal)
        for row in orders.values():
            if row.side == "SELL":
                continue
            if row.funding_id is not None:
                reservations[row.funding_id] += row.value_cny
            else:
                extra_ok = False
        if any(key not in funds or amount > funds[key].value_cny for key, amount in reservations.items()):
            extra_ok = False
        possible = actual + additional if actual_ok and extra_ok else None
        denom = evidence.denominator_cny if evidence.denominator_qualified and evidence.denominator_cny else None
        return {
            "actual_status":"KNOWN" if actual_ok else "UNKNOWN",
            "actual_known_lower_bound_cny":actual,
            "actual_cny":actual if actual_ok else None,
            "long_term_btc_known_cny":sum((r.value_cny for r in btc if r.sleeve == "LONG_TERM"), zero),
            "strategy_btc_known_cny":sum((r.value_cny for r in btc if r.sleeve == "STRATEGY"), zero),
            "usdt_known_cny":sum((r.value_cny for r in positions.values() if r.asset == "USDT"), zero),
            "strategy_budget_cny":evidence.strategy_budget_cny,
            "open_buy_known_cny":sum((r.value_cny for r in orders.values() if r.side == "BUY"), zero),
            "potential_status":"KNOWN" if possible is not None else "UNKNOWN",
            "potential_cny":possible,
            "actual_ratio":actual/denom if actual_ok and denom else None,
            "potential_ratio":possible/denom if possible is not None and denom else None,
            "as_of":evidence.as_of.isoformat(), "execution_authorized":False,
        }
