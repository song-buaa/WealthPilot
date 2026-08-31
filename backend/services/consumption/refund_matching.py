"""Deterministic source-evidence refund matching for active EconomicEvents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json

from sqlalchemy.orm import Session

from backend.services.consumption.adapters.common import normalized_text
from backend.services.consumption.economic_events import EventType, ResolutionStatus, RuleSource
from backend.services.consumption.models import (
    EconomicEvent,
    EconomicEventProjectionRevision,
    EventRawLink,
    RawTransaction,
)
from backend.services.consumption.normalization.service import EconomicEventNormalizer


MATCH_WINDOW_DAYS = 180
PREFERRED_EXACT_MATCH_DAYS = 14


@dataclass(frozen=True)
class RefundReplayResult:
    matched_refund_count: int
    matched_refund_amount: Decimal
    unmatched_refund_count: int
    unmatched_refund_amount: Decimal
    affected_consumption_event_ids: tuple[str, ...]
    matched_refund_event_ids: tuple[str, ...]


@dataclass(frozen=True)
class _EventRow:
    event: EconomicEvent
    raw: RawTransaction
    projection: EconomicEventProjectionRevision | None

    @property
    def amount(self) -> Decimal:
        return Decimal(self.event.amount)

    @property
    def remaining(self) -> Decimal:
        return Decimal(self.projection.net_amount) if self.projection else self.amount

    @property
    def occurred_on(self) -> date | None:
        return self.event.event_date or self.raw.transaction_date or self.raw.posting_date

    @property
    def descriptor(self) -> str:
        return normalized_text(self.raw.raw_description).casefold()


class RefundMatcher:
    """Match only source-supported refunds; ambiguous candidates remain unmatched."""

    def replay(self, session: Session) -> RefundReplayResult:
        consumptions = self._rows(session, EventType.CONSUMPTION)
        refunds = [item for item in self._rows(session, EventType.REFUND) if item.event.original_event_id is None]
        matched: list[_EventRow] = []
        matched_ids: set[str] = set()
        affected: set[str] = set()

        # A same-card, same-description, same-currency, exact-amount group is
        # sufficient only when its candidate cardinalities agree.  That permits
        # the statement-proven two-purchase/two-refund case without guessing a
        # single refund among several indistinguishable purchases.
        # ``payment_instrument_id`` can be either NULL or a UUID.  Sort a
        # stable representation rather than the raw tuple so mixed optional
        # values cannot make a replay fail before any matching happens.
        for key in sorted({self._exact_key(item) for item in refunds}, key=repr):
            refund_group = [item for item in refunds if self._exact_key(item) == key]
            candidate_group = [
                item for item in consumptions
                if self._exact_key(item) == key and self._within_window(item, refund_group)
                and item.remaining == refund_group[0].amount
            ]
            # A lone exact refund can be disambiguated by a uniquely nearest
            # same-card/source transaction only for a short, documented
            # settlement interval.  Equal candidates on that nearest date (or
            # any longer-gap alternative) remain unmatched instead of being
            # guessed.  This admits the cross-statement Pinduoduo case while
            # retaining the conservative ambiguity boundary.
            if len(refund_group) == 1 and candidate_group:
                refund = refund_group[0]
                nearest_days = min((refund.occurred_on - item.occurred_on).days for item in candidate_group)
                if nearest_days <= PREFERRED_EXACT_MATCH_DAYS:
                    candidate_group = [
                        item for item in candidate_group
                        if (refund.occurred_on - item.occurred_on).days == nearest_days
                    ]
            if not candidate_group or len(candidate_group) != len(refund_group):
                continue
            ordered_refunds = sorted(refund_group, key=self._sort_key)
            ordered_consumptions = sorted(candidate_group, key=self._sort_key)
            if all(self._eligible_pair(consumption, refund) for consumption, refund in zip(ordered_consumptions, ordered_refunds)):
                for consumption, refund in zip(ordered_consumptions, ordered_refunds):
                    self._match(session, consumption, refund, "EXACT_SOURCE_GROUP")
                    matched.append(refund)
                    matched_ids.add(refund.event.id)
                    affected.add(consumption.event.id)

        # A partial refund has no exact gross counterpart.  It is matched only
        # when one remaining source-equivalent purchase can absorb it.  Resolve
        # to a fixed point in one replay: an earlier source-proven match can
        # remove an alternative candidate for a later partial refund.  Without
        # this loop, a second replay could create a new (though non-duplicate)
        # revision, which is not operationally idempotent.
        while True:
            pass_matched = False
            for refund in sorted((item for item in refunds if item.event.id not in matched_ids), key=self._sort_key):
                candidates = [
                    item for item in consumptions
                    if item.remaining > refund.amount and self._eligible_pair(item, refund)
                ]
                if len(candidates) != 1:
                    continue
                consumption = candidates[0]
                self._match(session, consumption, refund, "UNIQUE_PARTIAL_SOURCE_MATCH")
                matched.append(refund)
                matched_ids.add(refund.event.id)
                affected.add(consumption.event.id)
                pass_matched = True
            if not pass_matched:
                break

        unmatched = [item for item in refunds if item.event.id not in matched_ids]
        session.flush()
        return RefundReplayResult(
            matched_refund_count=len(matched),
            matched_refund_amount=sum((item.amount for item in matched), Decimal("0")),
            unmatched_refund_count=len(unmatched),
            unmatched_refund_amount=sum((item.amount for item in unmatched), Decimal("0")),
            affected_consumption_event_ids=tuple(sorted(affected)),
            matched_refund_event_ids=tuple(sorted(item.event.id for item in matched)),
        )

    @staticmethod
    def _rows(session: Session, event_type: EventType) -> list[_EventRow]:
        primary_link_id = (
            session.query(EventRawLink.id)
            .filter(EventRawLink.event_id == EconomicEvent.id, EventRawLink.is_active.is_(True))
            .order_by(EventRawLink.id)
            .limit(1)
            .correlate(EconomicEvent)
            .scalar_subquery()
        )
        return [
            _EventRow(event, raw, projection)
            for event, raw, projection in session.query(EconomicEvent, RawTransaction, EconomicEventProjectionRevision)
            .join(EventRawLink, EventRawLink.id == primary_link_id)
            .join(RawTransaction, RawTransaction.id == EventRawLink.raw_transaction_id)
            .outerjoin(
                EconomicEventProjectionRevision,
                (EconomicEventProjectionRevision.event_id == EconomicEvent.id)
                & EconomicEventProjectionRevision.is_active.is_(True),
            )
            .filter(
                EconomicEvent.is_active.is_(True),
                RawTransaction.is_active.is_(True),
                EconomicEvent.event_type == event_type.value,
            )
            .all()
        ]

    @staticmethod
    def _exact_key(item: _EventRow) -> tuple[str, str | None, str, str, Decimal]:
        return (
            item.raw.account_id,
            item.raw.payment_instrument_id,
            item.event.currency,
            item.descriptor,
            item.amount,
        )

    @staticmethod
    def _sort_key(item: _EventRow) -> tuple[date, int, str]:
        return (item.occurred_on or date.min, item.raw.source_row_index, item.event.id)

    @staticmethod
    def _within_window(consumption: _EventRow, refunds: list[_EventRow]) -> bool:
        return all(RefundMatcher._eligible_pair(consumption, refund) for refund in refunds)

    @staticmethod
    def _eligible_pair(consumption: _EventRow, refund: _EventRow) -> bool:
        if (
            consumption.raw.account_id != refund.raw.account_id
            or consumption.event.currency != refund.event.currency
            or consumption.descriptor != refund.descriptor
        ):
            return False
        if (
            consumption.raw.payment_instrument_id is not None
            and refund.raw.payment_instrument_id is not None
            and consumption.raw.payment_instrument_id != refund.raw.payment_instrument_id
        ):
            return False
        purchase_date, refund_date = consumption.occurred_on, refund.occurred_on
        return bool(
            purchase_date and refund_date and purchase_date <= refund_date
            and (refund_date - purchase_date).days <= MATCH_WINDOW_DAYS
        )

    @staticmethod
    def _match(session: Session, consumption: _EventRow, refund: _EventRow, reason: str) -> None:
        refund.event.original_event_id = consumption.event.id
        refund.event.analytics_effective_date = consumption.event.event_date
        refund.event.resolution_status = ResolutionStatus.RESOLVED.value
        refund.event.resolution_reason = reason
        provenance = json.loads(refund.event.provenance)
        provenance["refund_match"] = {
            "reason": reason,
            "source_account_id": refund.raw.account_id,
            "window_days": (refund.occurred_on - consumption.occurred_on).days,
        }
        refund.event.provenance = json.dumps(provenance, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        EconomicEventNormalizer._append_projection_if_changed(
            session, consumption.event, "REFUND_MATCHED", RuleSource.AMOUNT_DATE_MATCH,
        )
