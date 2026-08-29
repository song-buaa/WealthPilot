"""Pure reference contract and deterministic normalizer for economic events.

This module intentionally has no ORM, database, network, LLM, taxonomy, or
analytics dependency.  It is a Golden-Case reference implementation for the
future persistence design, not a production import orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Iterable

from backend.services.consumption.contracts import canonical_json


NORMALIZER_VERSION = "economic-event-reference-v1"
BASE_CURRENCY = "CNY"


class EventType(StrEnum):
    CONSUMPTION = "CONSUMPTION"
    REFUND = "REFUND"
    CREDIT_CARD_REPAYMENT = "CREDIT_CARD_REPAYMENT"
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
    INVESTMENT_TRANSFER = "INVESTMENT_TRANSFER"
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
    INCOME = "INCOME"
    LOAN_DISBURSEMENT = "LOAN_DISBURSEMENT"
    DEBT_REPAYMENT = "DEBT_REPAYMENT"
    FEE_INTEREST = "FEE_INTEREST"
    REBATE = "REBATE"
    OTHER = "OTHER"


class EconomicDirection(StrEnum):
    OUTFLOW = "OUTFLOW"
    INFLOW = "INFLOW"
    NEUTRAL = "NEUTRAL"


class ResolutionStatus(StrEnum):
    RESOLVED = "RESOLVED"
    UNMATCHED = "UNMATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class RuleSource(StrEnum):
    DESCRIPTION_RULE = "DESCRIPTION_RULE"
    ACCOUNT_PAIR_MATCH = "ACCOUNT_PAIR_MATCH"
    AMOUNT_DATE_MATCH = "AMOUNT_DATE_MATCH"
    SOURCE_DEDUP = "SOURCE_DEDUP"


class FxSource(StrEnum):
    BANK_SETTLEMENT = "BANK_SETTLEMENT"
    NATIVE_CNY = "NATIVE_CNY"
    FX_REQUIRED = "FX_REQUIRED"


@dataclass(frozen=True)
class RawTransactionInput:
    """In-memory projection of already-persisted RawTransaction facts."""

    raw_id: str
    account_id: str
    account_type: str
    transaction_date: date | None
    posting_date: date | None
    source_amount: Decimal
    currency: str
    raw_description: str
    dedup_status: str = "UNIQUE"
    settlement_amount: Decimal | None = None
    settlement_currency: str | None = None

    @property
    def effective_date(self) -> date | None:
        return self.transaction_date or self.posting_date


@dataclass(frozen=True)
class NormalizationContext:
    """Non-Raw facts supplied by future account-ownership resolution."""

    owned_account_ids: frozenset[str]
    internal_transfer_date_window_days: int = 3


@dataclass(frozen=True)
class EconomicEvent:
    event_id: str
    event_type: EventType
    event_date: date | None
    analytics_effective_date: date | None
    raw_transaction_ids: tuple[str, ...]
    amount: Decimal
    currency: str
    base_amount: Decimal | None
    base_currency: str
    fx_rate: Decimal | None
    fx_source: FxSource
    economic_direction: EconomicDirection
    resolution_status: ResolutionStatus
    resolution_reason: str | None
    rule_sources: tuple[RuleSource, ...]
    original_event_id: str | None = None
    gross_amount: Decimal | None = None
    refund_amount: Decimal | None = None
    net_amount: Decimal | None = None
    normalizer_version: str = NORMALIZER_VERSION

    def to_dict(self) -> dict[str, object]:
        def money(value: Decimal | None) -> str | None:
            return format(value.quantize(Decimal("0.01")), "f") if value is not None else None

        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "analytics_effective_date": self.analytics_effective_date.isoformat() if self.analytics_effective_date else None,
            "raw_transaction_ids": list(self.raw_transaction_ids),
            "amount": money(self.amount),
            "currency": self.currency,
            "base_amount": money(self.base_amount),
            "base_currency": self.base_currency,
            "fx_rate": money(self.fx_rate),
            "fx_source": self.fx_source.value,
            "economic_direction": self.economic_direction.value,
            "resolution_status": self.resolution_status.value,
            "resolution_reason": self.resolution_reason,
            "rule_sources": [item.value for item in self.rule_sources],
            "original_event_id": self.original_event_id,
            "gross_amount": money(self.gross_amount),
            "refund_amount": money(self.refund_amount),
            "net_amount": money(self.net_amount),
            "normalizer_version": self.normalizer_version,
        }


def canonical_event_json(events: Iterable[EconomicEvent]) -> str:
    return canonical_json([event.to_dict() for event in events])


def _event_id(event_type: EventType, raw_ids: Iterable[str]) -> str:
    return f"event:{event_type.value.lower()}:{'|'.join(sorted(raw_ids))}"


def _magnitude(value: Decimal) -> Decimal:
    return abs(value).quantize(Decimal("0.01"))


def _base_amount(raw: RawTransactionInput) -> tuple[Decimal | None, Decimal | None, FxSource]:
    amount = _magnitude(raw.source_amount)
    if raw.settlement_currency == BASE_CURRENCY and raw.settlement_amount is not None:
        base = _magnitude(raw.settlement_amount)
        return base, (base / amount if amount else None), FxSource.BANK_SETTLEMENT
    if raw.currency == BASE_CURRENCY:
        return amount, Decimal("1.00"), FxSource.NATIVE_CNY
    return None, None, FxSource.FX_REQUIRED


def _direction(event_type: EventType, source_amount: Decimal) -> EconomicDirection:
    if event_type in {EventType.INTERNAL_TRANSFER, EventType.LIQUIDITY_SWEEP}:
        return EconomicDirection.NEUTRAL
    if event_type in {EventType.REFUND, EventType.INCOME, EventType.LOAN_DISBURSEMENT, EventType.REBATE}:
        return EconomicDirection.INFLOW
    return EconomicDirection.OUTFLOW if source_amount <= 0 else EconomicDirection.INFLOW


def _tag(value: str) -> str | None:
    if value.startswith("[") and "]" in value:
        return value[1:value.index("]")]
    return None


def _linked_raw_id(value: str) -> str | None:
    marker = "REF:"
    return value.split(marker, 1)[1].split()[0] if marker in value else None


_TAGS: dict[str, EventType] = {
    "CONSUMPTION": EventType.CONSUMPTION,
    "CC_REPAYMENT": EventType.CREDIT_CARD_REPAYMENT,
    "LIQUIDITY_SWEEP": EventType.LIQUIDITY_SWEEP,
    "INVESTMENT": EventType.INVESTMENT_TRANSFER,
    "INCOME": EventType.INCOME,
    "LOAN_DISBURSEMENT": EventType.LOAN_DISBURSEMENT,
    "DEBT_REPAYMENT": EventType.DEBT_REPAYMENT,
    "FEE_INTEREST": EventType.FEE_INTEREST,
    "REBATE": EventType.REBATE,
}


def _single_event(raw: RawTransactionInput, event_type: EventType, *, status: ResolutionStatus = ResolutionStatus.RESOLVED,
                  reason: str | None = None, original_event_id: str | None = None) -> EconomicEvent:
    base_amount, fx_rate, fx_source = _base_amount(raw)
    if fx_source == FxSource.FX_REQUIRED and status == ResolutionStatus.RESOLVED:
        status, reason = ResolutionStatus.NEEDS_REVIEW, "FX_REQUIRED"
    event_date = raw.effective_date
    return EconomicEvent(
        event_id=_event_id(event_type, (raw.raw_id,)), event_type=event_type,
        event_date=event_date, analytics_effective_date=event_date,
        raw_transaction_ids=(raw.raw_id,), amount=_magnitude(raw.source_amount), currency=raw.currency,
        base_amount=base_amount, base_currency=BASE_CURRENCY, fx_rate=fx_rate, fx_source=fx_source,
        economic_direction=_direction(event_type, raw.source_amount), resolution_status=status,
        resolution_reason=reason, rule_sources=(RuleSource.DESCRIPTION_RULE,),
        original_event_id=original_event_id,
        gross_amount=_magnitude(raw.source_amount) if event_type == EventType.CONSUMPTION else None,
        refund_amount=Decimal("0.00") if event_type == EventType.CONSUMPTION else None,
        net_amount=_magnitude(raw.source_amount) if event_type == EventType.CONSUMPTION else None,
    )


def normalize(raw_transactions: Iterable[RawTransactionInput], context: NormalizationContext) -> tuple[EconomicEvent, ...]:
    """Normalize synthetic/source-provenance DTOs deterministically.

    Only explicit source-description markers are recognised in this reference.
    Unknown inflows and installment principal remain unresolved rather than being
    guessed as income or a reconstructed purchase.
    """
    rows = tuple(sorted(raw_transactions, key=lambda item: item.raw_id))
    consumed: set[str] = set()
    events: list[EconomicEvent] = []

    # Candidate duplicates become a single event only across batches when a
    # deterministic source-equivalent description/date/amount key agrees.
    duplicate_groups: dict[tuple[object, ...], list[RawTransactionInput]] = {}
    for raw in rows:
        if raw.dedup_status == "CANDIDATE_DUPLICATE":
            duplicate_groups.setdefault((raw.account_id, raw.effective_date, raw.source_amount, raw.currency, raw.raw_description), []).append(raw)
    for group in duplicate_groups.values():
        if len(group) > 1 and _tag(group[0].raw_description) == "CONSUMPTION":
            first = group[0]
            event = _single_event(first, EventType.CONSUMPTION)
            events.append(EconomicEvent(**{**event.__dict__, "event_id": _event_id(EventType.CONSUMPTION, (item.raw_id for item in group)), "raw_transaction_ids": tuple(item.raw_id for item in group), "rule_sources": (RuleSource.DESCRIPTION_RULE, RuleSource.SOURCE_DEDUP)}))
            consumed.update(item.raw_id for item in group)

    # Internal transfer requires both known-owned accounts, equal magnitude,
    # opposing source signs and a bounded effective-date window.
    candidates = [item for item in rows if _tag(item.raw_description) == "INTERNAL" and item.raw_id not in consumed]
    while candidates:
        first = candidates.pop(0)
        match = next((other for other in candidates if other.account_id != first.account_id and other.account_id in context.owned_account_ids and first.account_id in context.owned_account_ids and other.currency == first.currency and _magnitude(other.source_amount) == _magnitude(first.source_amount) and other.source_amount * first.source_amount < 0 and first.effective_date and other.effective_date and abs((other.effective_date - first.effective_date).days) <= context.internal_transfer_date_window_days), None)
        if match is None:
            events.append(_single_event(first, EventType.OTHER, status=ResolutionStatus.NEEDS_REVIEW, reason="INTERNAL_OWNERSHIP_OR_PAIR_UNPROVEN"))
            consumed.add(first.raw_id)
            continue
        candidates.remove(match)
        consumed.update((first.raw_id, match.raw_id))
        base, rate, fx = _base_amount(first)
        event_date = min(first.effective_date, match.effective_date) if first.effective_date and match.effective_date else first.effective_date or match.effective_date
        events.append(EconomicEvent(_event_id(EventType.INTERNAL_TRANSFER, (first.raw_id, match.raw_id)), EventType.INTERNAL_TRANSFER, event_date, event_date, tuple(sorted((first.raw_id, match.raw_id))), _magnitude(first.source_amount), first.currency, base, BASE_CURRENCY, rate, fx, EconomicDirection.NEUTRAL, ResolutionStatus.RESOLVED, None, (RuleSource.DESCRIPTION_RULE, RuleSource.ACCOUNT_PAIR_MATCH)))

    consumption_by_raw: dict[str, EconomicEvent] = {}
    refunds: list[RawTransactionInput] = []
    for raw in rows:
        if raw.raw_id in consumed:
            continue
        tag = _tag(raw.raw_description)
        if tag == "REFUND":
            refunds.append(raw)
            continue
        if tag == "INSTALLMENT_PRINCIPAL":
            events.append(_single_event(raw, EventType.OTHER, status=ResolutionStatus.NEEDS_REVIEW, reason="INSTALLMENT_ORIGINAL_PURCHASE_UNAVAILABLE"))
        elif tag == "UNKNOWN_INCOMING":
            events.append(_single_event(raw, EventType.OTHER, status=ResolutionStatus.NEEDS_REVIEW, reason="INCOMING_SOURCE_UNPROVEN"))
        elif tag == "INSTALLMENT_PAYMENT":
            events.append(_single_event(raw, EventType.CREDIT_CARD_REPAYMENT))
        elif event_type := _TAGS.get(tag or ""):
            event = _single_event(raw, event_type)
            events.append(event)
            if event_type == EventType.CONSUMPTION:
                consumption_by_raw[raw.raw_id] = event
        else:
            events.append(_single_event(raw, EventType.OTHER, status=ResolutionStatus.NEEDS_REVIEW, reason="UNRECOGNISED_SOURCE_FACT"))

    for raw in refunds:
        original_raw_id = _linked_raw_id(raw.raw_description)
        original = consumption_by_raw.get(original_raw_id or "")
        if original is None:
            unmatched = _single_event(raw, EventType.REFUND, status=ResolutionStatus.UNMATCHED, reason="ORIGINAL_CONSUMPTION_UNMATCHED")
            events.append(EconomicEvent(**{**unmatched.__dict__, "analytics_effective_date": None}))
            continue
        refund = _single_event(raw, EventType.REFUND, original_event_id=original.event_id)
        refund = EconomicEvent(**{**refund.__dict__, "analytics_effective_date": original.event_date, "rule_sources": (RuleSource.DESCRIPTION_RULE, RuleSource.AMOUNT_DATE_MATCH)})
        events.append(refund)
        refunded = min(original.gross_amount or Decimal("0.00"), _magnitude(raw.source_amount))
        events[events.index(original)] = EconomicEvent(**{**original.__dict__, "refund_amount": refunded, "net_amount": (original.gross_amount or Decimal("0.00")) - refunded})

    return tuple(sorted(events, key=lambda item: item.event_id))
