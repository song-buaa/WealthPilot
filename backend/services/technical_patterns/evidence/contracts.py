"""Immutable, product-governed Pattern Evidence value contracts.

The contract contains technical facts and source lineage only.  It deliberately
has no recommendation, position-sizing, execution, or order authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Literal

from ..core.identity import canonicalize, stable_hash
from ..detectors.contracts import EvidenceValue


PATTERN_EVIDENCE_SCHEMA_VERSION = "wp-pattern-evidence-bundle-v1"
PatternTypeValue = Literal[
    "breakout",
    "breakdown",
    "rectangle",
    "ascending_triangle",
    "double_top",
    "double_bottom",
]
PatternDirectionValue = Literal["bullish", "bearish", "neutral"]
TimeframeValue = Literal["1d"]
SnapshotMediaType = Literal["image/svg+xml", "image/png"]
_PATTERN_TYPES = {
    "breakout",
    "breakdown",
    "rectangle",
    "ascending_triangle",
    "double_top",
    "double_bottom",
}
_PATTERN_DIRECTIONS = {"bullish", "bearish", "neutral"}
_CONFIRMATION_STATES = {"pending", "confirmed", "rejected", "not_required"}


class PatternEvidenceResultState(str, Enum):
    PATTERN_FOUND = "PATTERN_FOUND"
    NO_PATTERN = "NO_PATTERN"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
    DATA_QUALITY_BLOCKED = "DATA_QUALITY_BLOCKED"
    ENGINE_ERROR = "ENGINE_ERROR"


class ProductLifecycleStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class PatternInstrumentIdentity:
    instrument_id: str
    symbol: str
    market: str
    economic_asset_class: str
    con_id: int | None = None
    isin: str | None = None
    currency: str | None = None

    def __post_init__(self) -> None:
        if not all(
            (
                self.instrument_id,
                self.symbol,
                self.market,
                self.economic_asset_class,
            )
        ):
            raise ValueError("Pattern evidence requires stable instrument identity")
        if self.con_id is not None and self.con_id <= 0:
            raise ValueError("con_id must be positive when present")


@dataclass(frozen=True)
class EvidenceFactSnapshot:
    code: str
    value: EvidenceValue
    available_from: date
    available_from_session_ordinal: int
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.code
            or self.available_from_session_ordinal < 0
            or not self.source_ids
        ):
            raise ValueError("Evidence facts require code, availability, and lineage")


@dataclass(frozen=True)
class SourceFactSnapshot:
    source_type: Literal["pivot", "boundary"]
    source_id: str
    available_from: date
    available_from_session_ordinal: int

    def __post_init__(self) -> None:
        if (
            self.source_type not in {"pivot", "boundary"}
            or not self.source_id
            or self.available_from_session_ordinal < 0
        ):
            raise ValueError("Source facts require supported type and stable identity")


@dataclass(frozen=True)
class PatternEvidenceDescriptor:
    candidate_id: str
    pattern_type: PatternTypeValue
    pattern_family: str
    direction: PatternDirectionValue
    lifecycle_status: ProductLifecycleStatus
    formed_on: date
    available_from: date
    evaluated_on: date

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.pattern_family:
            raise ValueError("Pattern descriptor requires stable identity and family")
        if self.pattern_type not in _PATTERN_TYPES:
            raise ValueError("Pattern descriptor type is outside the six-pattern freeze")
        if self.direction not in _PATTERN_DIRECTIONS:
            raise ValueError("Pattern descriptor direction is invalid")
        if not self.formed_on <= self.available_from <= self.evaluated_on:
            raise ValueError("Pattern descriptor dates must preserve causal order")


@dataclass(frozen=True)
class ConfirmationEvidenceSnapshot:
    state: Literal["pending", "confirmed", "rejected", "not_required"]
    reason: str
    observed_on: date | None
    observed_session_ordinal: int | None
    facts: tuple[EvidenceFactSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if self.state not in _CONFIRMATION_STATES:
            raise ValueError("Confirmation evidence state is invalid")
        observed = self.observed_on is not None
        if observed != (self.observed_session_ordinal is not None):
            raise ValueError("Confirmation observation date and ordinal must match")
        if not self.reason:
            raise ValueError("Confirmation evidence requires a reason")


@dataclass(frozen=True)
class EvidenceGeometrySnapshot:
    pivots: tuple[SourceFactSnapshot, ...]
    boundaries: tuple[SourceFactSnapshot, ...]
    facts: tuple[EvidenceFactSnapshot, ...]

    def __post_init__(self) -> None:
        if not self.pivots and not self.boundaries:
            raise ValueError("Pattern geometry requires source lineage")
        if not self.facts:
            raise ValueError("Pattern geometry requires technical facts")


@dataclass(frozen=True)
class EvidenceInvalidationSnapshot:
    invalidated: bool
    condition: str
    reason: str | None
    observed_on: date | None
    observed_session_ordinal: int | None
    facts: tuple[EvidenceFactSnapshot, ...] = ()

    def __post_init__(self) -> None:
        if not self.condition:
            raise ValueError("Invalidation evidence requires a condition")
        observed = self.observed_on is not None
        if observed != (self.observed_session_ordinal is not None):
            raise ValueError("Invalidation observation date and ordinal must match")
        if self.invalidated and (not self.reason or not observed):
            raise ValueError("Invalidated evidence requires reason and observation")
        if not self.invalidated and (self.reason is not None or observed):
            raise ValueError("Non-invalidated evidence cannot invent an event")


@dataclass(frozen=True)
class EvidenceProvenance:
    provider: Literal["IBKR"]
    source_bar_hash: str
    candidate_source_bar_hash: str
    detector_version: str
    indicator_layer_version: str
    calibration_version: str
    parameter_set_id: str
    parameter_hash: str
    detector_result_hash: str

    def __post_init__(self) -> None:
        if self.provider != "IBKR":
            raise ValueError("Stage 1 Pattern evidence provider must be IBKR")
        hashes = (
            self.source_bar_hash,
            self.candidate_source_bar_hash,
            self.parameter_hash,
            self.detector_result_hash,
        )
        if any(
            len(value) != 64
            or any(character not in "0123456789abcdef" for character in value.lower())
            for value in hashes
        ):
            raise ValueError("Pattern provenance hashes must be SHA-256 values")
        if not all(
            (
                self.detector_version,
                self.indicator_layer_version,
                self.calibration_version,
                self.parameter_set_id,
            )
        ):
            raise ValueError("Pattern provenance versions are required")


@dataclass(frozen=True)
class EvidenceSnapshotReference:
    uri: str | None = None
    media_type: SnapshotMediaType | None = None

    def __post_init__(self) -> None:
        if (self.uri is None) != (self.media_type is None):
            raise ValueError("Snapshot URI and media type must be provided together")
        if self.media_type not in {None, "image/svg+xml", "image/png"}:
            raise ValueError("Snapshot media type is not governed")


@dataclass(frozen=True)
class PatternEvidence:
    pattern: PatternEvidenceDescriptor
    structure_confirmation: ConfirmationEvidenceSnapshot
    direction_confirmation: ConfirmationEvidenceSnapshot
    geometry: EvidenceGeometrySnapshot
    invalidation: EvidenceInvalidationSnapshot
    provenance: EvidenceProvenance


@dataclass(frozen=True)
class PatternEvidenceBundle:
    """One canonical envelope for found evidence and distinct empty/error states."""

    instrument: PatternInstrumentIdentity
    timeframe: TimeframeValue
    result_state: PatternEvidenceResultState
    evidence: PatternEvidence | None = None
    evidence_snapshot: EvidenceSnapshotReference = EvidenceSnapshotReference()
    reason: str = ""
    schema_version: str = PATTERN_EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        found = self.result_state is PatternEvidenceResultState.PATTERN_FOUND
        if found != (self.evidence is not None):
            raise ValueError("PATTERN_FOUND must contain evidence and other states must not")
        if not found and not self.reason:
            raise ValueError("Non-found Pattern evidence states require an explicit reason")
        if not found and self.evidence_snapshot.uri is not None:
            raise ValueError("Non-found Pattern evidence cannot reference a chart snapshot")
        if self.schema_version != PATTERN_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("Unsupported Pattern evidence schema version")

    @property
    def bundle_hash(self) -> str:
        return stable_hash(self)

    def as_dict(self) -> dict[str, Any]:
        return canonicalize(self)
