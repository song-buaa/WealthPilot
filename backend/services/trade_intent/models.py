"""Phase 1 typed contracts for natural-language trade intent.

These models intentionally contain no broker contract, quote, quantity, limit
price, cash-authority, batch, or order fields.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FieldProvenance(str, Enum):
    USER_EXPLICIT = "USER_EXPLICIT"
    AI_INFERRED = "AI_INFERRED"
    NOT_PROVIDED = "NOT_PROVIDED"


class FieldResolutionStatus(str, Enum):
    RESOLVED = "RESOLVED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTING = "CONFLICTING"
    UNSUPPORTED_FOR_V3_15_V1 = "UNSUPPORTED_FOR_V3_15_V1"


class IntentReadiness(str, Enum):
    READY_FOR_CONFIRMATION = "READY_FOR_CONFIRMATION"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNSUPPORTED_FOR_V3_15_V1 = "UNSUPPORTED_FOR_V3_15_V1"
    PARSE_FAILED = "PARSE_FAILED"


class IntentConfirmationStatus(str, Enum):
    PENDING = "PENDING"
    BLOCKED = "BLOCKED"
    CONFIRMED = "CONFIRMED"


class TradeIntentField(_StrictModel):
    value: str | int | float | bool | dict[str, Any] | None = None
    provenance: FieldProvenance
    source_text: str | None = None
    status: FieldResolutionStatus


class TradeIntentIssue(_StrictModel):
    code: str
    field_path: str
    status: FieldResolutionStatus
    message: str
    blocking: bool = True


class TradeIntentLeg(_StrictModel):
    sequence: int = Field(ge=1)
    alias: TradeIntentField
    allocation_mode: TradeIntentField
    target_amount: TradeIntentField
    venue_override: TradeIntentField
    trading_currency_override: TradeIntentField
    share_class_override: TradeIntentField


class StructuredTradeIntent(_StrictModel):
    schema_version: str = "v3.15-phase1"
    intent_id: str = Field(default_factory=lambda: f"ti_{uuid4().hex}")
    candidate: bool = True

    broker: TradeIntentField
    account: TradeIntentField
    funding_source: TradeIntentField
    funding_currency: TradeIntentField
    budget_mode: TradeIntentField
    stated_cash: TradeIntentField

    venue: TradeIntentField
    trading_currency: TradeIntentField
    share_class: TradeIntentField
    side: TradeIntentField
    order_type: TradeIntentField

    legs: list[TradeIntentLeg] = Field(default_factory=list)
    issues: list[TradeIntentIssue] = Field(default_factory=list)
    readiness: IntentReadiness = IntentReadiness.NEEDS_REVIEW
    confirmation_status: IntentConfirmationStatus = IntentConfirmationStatus.BLOCKED
    confirmed_at: datetime | None = None
    phase_boundary: str = "TYPED_INTENT_ONLY"

    @property
    def is_confirmable(self) -> bool:
        return (
            self.readiness == IntentReadiness.READY_FOR_CONFIRMATION
            and self.confirmation_status
            in (IntentConfirmationStatus.PENDING, IntentConfirmationStatus.CONFIRMED)
        )


def unresolved_field(
    status: FieldResolutionStatus = FieldResolutionStatus.MISSING,
) -> TradeIntentField:
    return TradeIntentField(
        value=None,
        provenance=FieldProvenance.NOT_PROVIDED,
        source_text=None,
        status=status,
    )
