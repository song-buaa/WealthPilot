"""Minimal, explicit user-provided consumption facts without bank-source fabrication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256

from sqlalchemy.orm import Session

from backend.services.consumption.classification_design import (
    ClassificationSource, ClassificationStatus, EligibilityStatus, PrimaryCategory,
)
from backend.services.consumption.contracts import canonical_json
from backend.services.consumption.economic_events import EconomicDirection, EventType, FxSource, ResolutionStatus
from backend.services.consumption.models import (
    ConsumptionInterpretation, EconomicEvent, EconomicEventProjectionRevision, ManualConsumptionEntry,
)


MANUAL_EVENT_VERSION = "manual-consumption-v1"
USER_PROVIDED_SOURCE = "USER_PROVIDED"


@dataclass(frozen=True)
class ManualConsumptionSpec:
    id: str
    occurred_on: date
    amount: Decimal
    description: str
    primary_category: PrimaryCategory
    secondary_category: str


def _semantic_key(entry_id: str) -> str:
    return sha256(f"{MANUAL_EVENT_VERSION}:{entry_id}".encode("utf-8")).hexdigest()


def _matches(entry: ManualConsumptionEntry, spec: ManualConsumptionSpec) -> bool:
    return all((
        entry.event_id == spec.id,
        entry.occurred_on == spec.occurred_on,
        Decimal(entry.amount) == Decimal(spec.amount),
        entry.description == spec.description,
        entry.currency == "CNY",
        entry.source == USER_PROVIDED_SOURCE,
        entry.is_active,
    ))


def upsert_manual_consumption(session: Session, spec: ManualConsumptionSpec) -> bool:
    """Create one explicit user-provided event once; reject conflicting stable IDs."""
    entry = session.get(ManualConsumptionEntry, spec.id)
    if entry is not None:
        if not _matches(entry, spec):
            raise ValueError(f"manual entry {spec.id} conflicts with existing facts")
        return False
    if session.get(EconomicEvent, spec.id) is not None:
        raise ValueError(f"manual event id {spec.id} is already used by a non-manual event")

    amount = Decimal(spec.amount).quantize(Decimal("0.00000001"))
    if amount <= 0:
        raise ValueError("manual consumption amount must be positive")
    provenance = canonical_json({"entry_id": spec.id, "source": USER_PROVIDED_SOURCE, "kind": "MANUAL_CONSUMPTION"})
    event = EconomicEvent(
        id=spec.id,
        semantic_key=_semantic_key(spec.id),
        event_type=EventType.CONSUMPTION.value,
        event_date=spec.occurred_on,
        analytics_effective_date=spec.occurred_on,
        amount=amount,
        currency="CNY",
        economic_direction=EconomicDirection.OUTFLOW.value,
        base_currency="CNY",
        base_amount=amount,
        fx_rate=Decimal("1"),
        fx_source=FxSource.NATIVE_CNY.value,
        resolution_status=ResolutionStatus.RESOLVED.value,
        resolution_reason="USER_PROVIDED_MANUAL_FACT",
        normalizer_version=MANUAL_EVENT_VERSION,
        rule_sources=canonical_json([USER_PROVIDED_SOURCE]),
        provenance=provenance,
    )
    session.add(event)
    session.add(ManualConsumptionEntry(
        id=spec.id,
        event_id=spec.id,
        occurred_on=spec.occurred_on,
        description=spec.description,
        amount=amount,
        currency="CNY",
        source=USER_PROVIDED_SOURCE,
        provenance=provenance,
    ))
    session.add(EconomicEventProjectionRevision(
        event_id=spec.id,
        revision_number=1,
        gross_amount=amount,
        refund_amount=Decimal("0"),
        net_amount=amount,
        base_currency="CNY",
        base_net_amount=amount,
        reason="USER_PROVIDED_MANUAL_FACT",
        rule_source=USER_PROVIDED_SOURCE,
    ))
    session.add(ConsumptionInterpretation(
        event_id=spec.id,
        eligibility_status=EligibilityStatus.ELIGIBLE.value,
        eligibility_source=ClassificationSource.USER_CONFIRMATION.value,
        eligibility_reason="USER_PROVIDED_MANUAL_FACT",
        classification_status=ClassificationStatus.CLASSIFIED.value,
        primary_category=spec.primary_category.value,
        secondary_category=spec.secondary_category,
        classification_source=ClassificationSource.USER_CONFIRMATION.value,
        classification_reason="USER_PROVIDED_MANUAL_FACT",
        user_confirmed=True,
        revision_number=1,
        resolver_version=MANUAL_EVENT_VERSION,
        actor_type="LOCAL_USER",
        actor_id=USER_PROVIDED_SOURCE,
    ))
    session.flush()
    return True
