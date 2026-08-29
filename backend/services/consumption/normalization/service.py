"""Persist the small, deterministic RawTransaction → EconomicEvent pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from typing import Iterable

from sqlalchemy.orm import Session

from backend.services.consumption.contracts import canonical_json
from backend.services.consumption.economic_events import (
    BASE_CURRENCY,
    EconomicDirection,
    EventType,
    FxSource,
    ResolutionStatus,
    RuleSource,
)
from backend.services.consumption.models import (
    EconomicEvent,
    EconomicEventProjectionRevision,
    EventRawLink,
    RawTransaction,
)
from backend.services.consumption.normalization.rules import (
    Evidence,
    classify_source,
    has_explicit_internal_transfer_marker,
    refund_reference,
)


NORMALIZER_VERSION = "economic-event-orm-v1"
CONFIRMED_OWNED = "CONFIRMED_OWNED"


@dataclass(frozen=True)
class NormalizationResult:
    created_events: int
    reused_events: int
    created_links: int
    created_revisions: int


def _magnitude(value: Decimal) -> Decimal:
    return abs(Decimal(value)).quantize(Decimal("0.00000001"))


def _effective_date(raw: RawTransaction) -> date | None:
    return raw.transaction_date or raw.posting_date


def _semantic_key(event_type: EventType, raws: Iterable[RawTransaction]) -> str:
    return sha256(canonical_json({
        "normalizer_version": NORMALIZER_VERSION,
        "event_type": event_type.value,
        "raw_ids": sorted(raw.id for raw in raws),
    }).encode("utf-8")).hexdigest()


def _fx(raw: RawTransaction) -> tuple[Decimal | None, Decimal | None, FxSource]:
    amount = _magnitude(raw.amount)
    if raw.settlement_currency == BASE_CURRENCY and raw.settlement_amount is not None:
        base = _magnitude(raw.settlement_amount)
        return base, (base / amount if amount else None), FxSource.BANK_SETTLEMENT
    if raw.currency == BASE_CURRENCY:
        return amount, Decimal("1"), FxSource.NATIVE_CNY
    return None, None, FxSource.FX_REQUIRED


def _direction(event_type: EventType, amount: Decimal) -> EconomicDirection:
    if event_type in {EventType.INTERNAL_TRANSFER, EventType.LIQUIDITY_SWEEP}:
        return EconomicDirection.NEUTRAL
    if event_type in {EventType.REFUND, EventType.INCOME, EventType.LOAN_DISBURSEMENT, EventType.REBATE}:
        return EconomicDirection.INFLOW
    return EconomicDirection.OUTFLOW if amount <= 0 else EconomicDirection.INFLOW


class EconomicEventNormalizer:
    """Offline normalizer with idempotent semantic identity and audit revisions."""

    def normalize(self, session: Session, raws: Iterable[RawTransaction] | None = None) -> NormalizationResult:
        rows = tuple(raws) if raws is not None else tuple(
            session.query(RawTransaction).order_by(RawTransaction.id).all()
        )
        active_raw_ids = {
            item[0] for item in session.query(EventRawLink.raw_transaction_id)
            .filter(EventRawLink.is_active.is_(True)).all()
        }
        pending = [row for row in rows if row.id not in active_raw_ids]
        created_events = reused_events = created_links = created_revisions = 0

        def persist(event_type: EventType, event_rows: list[RawTransaction], evidence: Evidence,
                    *, status: ResolutionStatus = ResolutionStatus.RESOLVED,
                    reason: str | None = None, original_event: EconomicEvent | None = None,
                    roles: dict[str, str] | None = None) -> EconomicEvent:
            nonlocal created_events, reused_events, created_links, created_revisions
            semantic_key = _semantic_key(event_type, event_rows)
            event = session.query(EconomicEvent).filter_by(
                normalizer_version=NORMALIZER_VERSION, semantic_key=semantic_key
            ).one_or_none()
            if event is not None:
                reused_events += 1
                return event
            first = event_rows[0]
            base_amount, fx_rate, fx_source = _fx(first)
            if fx_source == FxSource.FX_REQUIRED and status == ResolutionStatus.RESOLVED:
                status, reason = ResolutionStatus.NEEDS_REVIEW, "FX_REQUIRED"
            event_date = min((item for item in (_effective_date(row) for row in event_rows) if item), default=None)
            analytics_date = event_date
            if event_type == EventType.REFUND:
                analytics_date = original_event.event_date if original_event else None
                if original_event is None:
                    status, reason = ResolutionStatus.UNMATCHED, "ORIGINAL_CONSUMPTION_UNMATCHED"
            event = EconomicEvent(
                semantic_key=semantic_key, event_type=event_type.value, event_date=event_date,
                analytics_effective_date=analytics_date, amount=_magnitude(first.amount),
                currency=first.currency, economic_direction=_direction(event_type, Decimal(first.amount)).value,
                base_currency=BASE_CURRENCY, base_amount=base_amount, fx_rate=fx_rate,
                fx_source=fx_source.value, resolution_status=status.value, resolution_reason=reason,
                original_event_id=original_event.id if original_event else None,
                normalizer_version=NORMALIZER_VERSION,
                rule_sources=canonical_json([evidence.rule_source.value]),
                provenance=canonical_json({"confidence": evidence.confidence, "reason": evidence.reason}),
            )
            session.add(event)
            session.flush()
            for index, raw in enumerate(event_rows):
                role = (roles or {}).get(raw.id, "PRIMARY" if index == 0 else "SOURCE_DUPLICATE")
                session.add(EventRawLink(
                    event_id=event.id, raw_transaction_id=raw.id, link_role=role,
                    rule_source=evidence.rule_source.value,
                    evidence=canonical_json({"confidence": evidence.confidence, "reason": evidence.reason}),
                ))
                created_links += 1
            created_events += 1
            if event_type == EventType.CONSUMPTION:
                created_revisions += self._append_projection_if_changed(session, event, "INITIAL_NORMALIZATION", evidence.rule_source)
            return event

        # A candidate duplicate is collapsed only with matching persisted raw facts.
        duplicate_groups: dict[str, list[RawTransaction]] = {}
        for raw in pending:
            if raw.dedup_status == "CANDIDATE_DUPLICATE":
                duplicate_groups.setdefault(raw.match_fingerprint, []).append(raw)
        consumed: set[str] = set()
        for group in duplicate_groups.values():
            first_evidence = classify_source(raw_description=group[0].raw_description, account_type=group[0].account.account_type)
            if len(group) > 1 and first_evidence.event_type != EventType.OTHER:
                persist(first_evidence.event_type, group, Evidence(first_evidence.event_type, RuleSource.SOURCE_DEDUP))
                consumed.update(item.id for item in group)

        # Internal transfer is guarded by explicit source marker, confirmed ownership,
        # opposing equal amounts/currency, and no more than one matching counterpart.
        transfer_rows = [row for row in pending if row.id not in consumed and has_explicit_internal_transfer_marker(row.raw_description)]
        transfer_matches: dict[str, list[RawTransaction]] = {}
        for raw in transfer_rows:
            transfer_matches[raw.id] = [other for other in transfer_rows if other.id != raw.id
                and raw.account_id != other.account_id
                and raw.account.ownership_status == CONFIRMED_OWNED
                and other.account.ownership_status == CONFIRMED_OWNED
                and raw.currency == other.currency
                and _magnitude(raw.amount) == _magnitude(other.amount)
                and Decimal(raw.amount) * Decimal(other.amount) < 0
                and _effective_date(raw) and _effective_date(other)
                and abs((_effective_date(raw) - _effective_date(other)).days) <= 3]
        for raw in transfer_rows:
            if raw.id in consumed:
                continue
            matches = transfer_matches[raw.id]
            if (
                len(matches) == 1
                and matches[0].id not in consumed
                and len(transfer_matches[matches[0].id]) == 1
            ):
                other = matches[0]
                persist(EventType.INTERNAL_TRANSFER, [raw, other], Evidence(EventType.INTERNAL_TRANSFER, RuleSource.ACCOUNT_PAIR_MATCH), roles={raw.id: "TRANSFER_LEG", other.id: "TRANSFER_LEG"})
                consumed.update((raw.id, other.id))
            else:
                reason = (
                    "INTERNAL_TRANSFER_AMBIGUOUS"
                    if len(matches) > 1 or any(len(transfer_matches[item.id]) > 1 for item in matches)
                    else "INTERNAL_OWNERSHIP_OR_PAIR_UNPROVEN"
                )
                persist(EventType.OTHER, [raw], Evidence(EventType.OTHER, RuleSource.DESCRIPTION_RULE, reason), status=ResolutionStatus.NEEDS_REVIEW, reason=reason)
                consumed.add(raw.id)

        created_by_raw: dict[str, EconomicEvent] = {}
        refund_rows: list[RawTransaction] = []
        for raw in pending:
            if raw.id in consumed:
                continue
            evidence = classify_source(raw_description=raw.raw_description, account_type=raw.account.account_type)
            if evidence.event_type == EventType.REFUND:
                refund_rows.append(raw)
                continue
            status = ResolutionStatus.NEEDS_REVIEW if evidence.event_type == EventType.OTHER else ResolutionStatus.RESOLVED
            event = persist(evidence.event_type, [raw], evidence, status=status, reason=evidence.reason)
            consumed.add(raw.id)
            if event.event_type == EventType.CONSUMPTION.value:
                created_by_raw[raw.id] = event

        for raw in refund_rows:
            referenced_raw = refund_reference(raw.raw_description)
            original = created_by_raw.get(referenced_raw or "")
            if original is None and referenced_raw:
                original = session.query(EconomicEvent).join(EventRawLink).filter(
                    EventRawLink.raw_transaction_id == referenced_raw,
                    EventRawLink.is_active.is_(True),
                    EconomicEvent.event_type == EventType.CONSUMPTION.value,
                ).one_or_none()
            refund = persist(EventType.REFUND, [raw], classify_source(raw_description=raw.raw_description, account_type=raw.account.account_type), original_event=original, roles={raw.id: "REFUND_SOURCE"})
            consumed.add(raw.id)
            if refund.original_event_id:
                original_event = session.get(EconomicEvent, refund.original_event_id)
                created_revisions += self._append_projection_if_changed(session, original_event, "REFUND_MATCHED", RuleSource.AMOUNT_DATE_MATCH)

        session.flush()
        return NormalizationResult(created_events, reused_events, created_links, created_revisions)

    @staticmethod
    def _append_projection_if_changed(session: Session, event: EconomicEvent, reason: str, rule_source: RuleSource) -> int:
        gross = Decimal(event.amount)
        refunds = session.query(EconomicEvent).filter(
            EconomicEvent.original_event_id == event.id,
            EconomicEvent.event_type == EventType.REFUND.value,
            EconomicEvent.resolution_status == ResolutionStatus.RESOLVED.value,
        ).all()
        refund_amount = min(gross, sum((Decimal(item.amount) for item in refunds), Decimal("0")))
        net = gross - refund_amount
        current = session.query(EconomicEventProjectionRevision).filter_by(event_id=event.id, is_active=True).one_or_none()
        if current and (Decimal(current.gross_amount), Decimal(current.refund_amount), Decimal(current.net_amount)) == (gross, refund_amount, net):
            return 0
        if current:
            current.is_active = False
        base_net = None
        if event.base_amount is not None and gross:
            base_net = (Decimal(event.base_amount) * net / gross).quantize(Decimal("0.00000001"))
        revision = EconomicEventProjectionRevision(
            event_id=event.id, revision_number=(current.revision_number + 1 if current else 1),
            gross_amount=gross, refund_amount=refund_amount, net_amount=net,
            base_currency=event.base_currency, base_net_amount=base_net, reason=reason,
            rule_source=rule_source.value, supersedes_revision_id=current.id if current else None,
        )
        session.add(revision)
        session.flush()
        return 1
