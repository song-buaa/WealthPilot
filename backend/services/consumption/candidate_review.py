"""Explicit local review queue for ambiguous outgoing economic events.

The queue deliberately sits before consumption classification: an item here is
an ``OTHER`` outflow whose *eligibility* is not yet known. A local user can
either promote it into an eligible, classified consumption projection or
explicitly exclude it. Neither action changes immutable RawTransaction or
EconomicEvent facts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.services.consumption.classification import ClassificationResolver
from backend.services.consumption.classification_design import (
    ClassificationSource, EligibilityStatus, PrimaryCategory,
)
from backend.services.consumption.economic_events import EconomicDirection, EventType
from backend.services.consumption.models import (
    Account, ConsumptionInterpretation, EconomicEvent,
    EconomicEventProjectionRevision, EventRawLink, ImportBatch, RawTransaction,
)


class CandidateReviewError(ValueError):
    """Raised when an event is not currently eligible for candidate review."""


@dataclass(frozen=True)
class ConsumptionCandidateItem:
    event_id: str
    analytics_effective_date: date
    raw_description: str
    account_display_name: str
    source_label: str
    amount_cny: Decimal | None
    currency: str


@dataclass(frozen=True)
class ConsumptionCandidatePage:
    month: date | None
    items: tuple[ConsumptionCandidateItem, ...]
    total: int
    limit: int
    offset: int


def _safe_account_display_name(value: str | None, institution: str) -> str:
    label = " ".join((value or f"{institution}账户").split())[:40]
    return re.sub(r"(?:\*{2,})?\d{2,}", "****", label) or f"{institution}账户"


def _source_label(batch: ImportBatch, account: Account) -> str:
    kind = {"DEBIT_CARD": "Debit", "CREDIT_CARD": "Credit"}.get(account.account_type, account.account_type)
    return f"{batch.institution} {kind}"


class ConsumptionCandidateReviewService:
    """Read and resolve only ambiguous OTHER outflows using existing audit models."""

    def __init__(self, session: Session):
        self.session = session

    def list_candidates(
        self, *, month: date | None = None, limit: int = 100, offset: int = 0,
    ) -> ConsumptionCandidatePage:
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        if offset < 0:
            raise ValueError("offset must not be negative")
        primary_link_id = (
            self.session.query(func.min(EventRawLink.id))
            .filter(EventRawLink.event_id == EconomicEvent.id, EventRawLink.is_active.is_(True))
            .correlate(EconomicEvent)
            .scalar_subquery()
        )
        query = (
            self.session.query(EconomicEvent, ConsumptionInterpretation, RawTransaction, Account, ImportBatch)
            .join(ConsumptionInterpretation, (ConsumptionInterpretation.event_id == EconomicEvent.id) & ConsumptionInterpretation.is_active.is_(True))
            .join(EventRawLink, EventRawLink.id == primary_link_id)
            .join(RawTransaction, RawTransaction.id == EventRawLink.raw_transaction_id)
            .join(Account, Account.id == RawTransaction.account_id)
            .join(ImportBatch, ImportBatch.id == RawTransaction.import_batch_id)
            .filter(
                EconomicEvent.is_active.is_(True),
                EconomicEvent.event_type == EventType.OTHER.value,
                EconomicEvent.economic_direction == EconomicDirection.OUTFLOW.value,
                EconomicEvent.analytics_effective_date.is_not(None),
                ConsumptionInterpretation.eligibility_status == EligibilityStatus.NEEDS_REVIEW.value,
            )
        )
        if month is not None:
            next_month = date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
            query = query.filter(
                EconomicEvent.analytics_effective_date >= month,
                EconomicEvent.analytics_effective_date < next_month,
            )
        rows = query.order_by(
            EconomicEvent.base_amount.desc().nullslast(),
            EconomicEvent.amount.desc(),
            EconomicEvent.analytics_effective_date.desc(),
            EconomicEvent.id,
        ).all()
        items = tuple(
            ConsumptionCandidateItem(
                event_id=event.id,
                analytics_effective_date=event.analytics_effective_date,
                raw_description=raw.raw_description,
                account_display_name=_safe_account_display_name(account.display_name, account.institution),
                source_label=_source_label(batch, account),
                amount_cny=Decimal(event.base_amount) if event.base_amount is not None else None,
                currency=event.currency,
            )
            for event, _interpretation, raw, account, batch in rows
        )
        return ConsumptionCandidatePage(month=month, items=items[offset:offset + limit], total=len(items), limit=limit, offset=offset)

    def confirm_as_consumption(
        self, event_id: str, *, primary_category: PrimaryCategory, secondary_category: str,
    ) -> ConsumptionInterpretation:
        event, _current = self._current_candidate(event_id)
        interpretation = ClassificationResolver().confirm_event(
            self.session,
            event_id,
            eligibility_status=EligibilityStatus.ELIGIBLE,
            primary_category=primary_category,
            secondary_category=secondary_category,
            reason="CANDIDATE_CONFIRMED_CONSUMPTION",
        )
        self._ensure_consumption_projection(event)
        return interpretation

    def reject_as_non_consumption(self, event_id: str) -> ConsumptionInterpretation:
        self._current_candidate(event_id)
        return ClassificationResolver().confirm_event(
            self.session,
            event_id,
            eligibility_status=EligibilityStatus.INELIGIBLE,
            reason="CANDIDATE_REJECTED_NON_CONSUMPTION",
        )

    def _current_candidate(self, event_id: str) -> tuple[EconomicEvent, ConsumptionInterpretation]:
        event = self.session.get(EconomicEvent, event_id)
        current = self.session.query(ConsumptionInterpretation).filter_by(event_id=event_id, is_active=True).one_or_none()
        if (
            event is None
            or not event.is_active
            or current is None
            or event.event_type != EventType.OTHER.value
            or event.economic_direction != EconomicDirection.OUTFLOW.value
            or current.eligibility_status != EligibilityStatus.NEEDS_REVIEW.value
        ):
            raise CandidateReviewError("event is not an active consumption candidate")
        return event, current

    def _ensure_consumption_projection(self, event: EconomicEvent) -> None:
        """Create the projection required by Analytics without mutating event facts."""
        gross = Decimal(event.amount)
        base_net = Decimal(event.base_amount) if event.base_amount is not None else None
        current = self.session.query(EconomicEventProjectionRevision).filter_by(event_id=event.id, is_active=True).one_or_none()
        expected = (gross, Decimal("0"), gross, base_net)
        if current and (
            Decimal(current.gross_amount), Decimal(current.refund_amount), Decimal(current.net_amount),
            Decimal(current.base_net_amount) if current.base_net_amount is not None else None,
        ) == expected:
            return
        if current is not None:
            current.is_active = False
        self.session.add(EconomicEventProjectionRevision(
            event_id=event.id,
            revision_number=current.revision_number + 1 if current else 1,
            gross_amount=gross,
            refund_amount=Decimal("0"),
            net_amount=gross,
            base_currency=event.base_currency,
            base_net_amount=base_net,
            reason="CANDIDATE_CONFIRMED_CONSUMPTION",
            rule_source=ClassificationSource.USER_CONFIRMATION.value,
            supersedes_revision_id=current.id if current else None,
        ))
        self.session.flush()
