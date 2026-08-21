"""Value-only contracts for Pattern candidates and technical outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import TypeAlias

from ..core.identity import IDENTITY_VERSION, stable_hash
from ..core.lifecycle import LifecycleSnapshot


EvidenceValue: TypeAlias = bool | int | float | str


class PatternFamily(str, Enum):
    LEVEL_BREAK = "level_break"
    RANGE = "range"
    TRIANGLE = "triangle"
    REVERSAL = "reversal"


class PatternType(str, Enum):
    BREAKOUT = "breakout"
    BREAKDOWN = "breakdown"
    RECTANGLE = "rectangle"
    ASCENDING_TRIANGLE = "ascending_triangle"
    DOUBLE_TOP = "double_top"
    DOUBLE_BOTTOM = "double_bottom"


class PatternDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SourceFactType(str, Enum):
    BAR = "bar"
    PIVOT = "pivot"
    BOUNDARY = "boundary"
    RANGE = "range"
    INDICATOR = "indicator"


class ConfirmationType(str, Enum):
    STRUCTURE = "structure"
    DIRECTION = "direction"


class ConfirmationState(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    NOT_REQUIRED = "not_required"


@dataclass(frozen=True)
class SourceFactReference:
    source_type: SourceFactType
    source_id: str
    available_from: date
    available_from_session_ordinal: int

    def __post_init__(self) -> None:
        if not self.source_id or self.available_from_session_ordinal < 0:
            raise ValueError("source references require stable identity and availability")


@dataclass(frozen=True)
class EvidenceFact:
    code: str
    value: EvidenceValue
    available_from: date
    available_from_session_ordinal: int
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.code or self.available_from_session_ordinal < 0 or not self.source_ids:
            raise ValueError("evidence facts require code, source lineage and availability")


@dataclass(frozen=True)
class DetectorDescriptor:
    pattern_family: PatternFamily
    pattern_type: PatternType
    direction: PatternDirection
    detector_version: str

    def __post_init__(self) -> None:
        if not self.detector_version:
            raise ValueError("detector_version is required")


@dataclass(frozen=True)
class CandidateProposal:
    formed_session_ordinal: int
    available_from_session_ordinal: int
    source_pivots: tuple[SourceFactReference, ...]
    source_boundaries: tuple[SourceFactReference, ...]
    geometry_facts: tuple[EvidenceFact, ...]
    structure_facts: tuple[EvidenceFact, ...]
    direction_confirmation_required: bool
    expires_at_session_ordinal: int | None = None

    def __post_init__(self) -> None:
        if self.formed_session_ordinal < 0 or self.available_from_session_ordinal < self.formed_session_ordinal:
            raise ValueError("candidate availability cannot precede formation")
        if not self.source_pivots and not self.source_boundaries:
            raise ValueError("candidate requires pivot or boundary source lineage")
        if any(item.source_type is not SourceFactType.PIVOT for item in self.source_pivots):
            raise ValueError("source_pivots may contain pivot references only")
        if any(item.source_type is not SourceFactType.BOUNDARY for item in self.source_boundaries):
            raise ValueError("source_boundaries may contain boundary references only")
        if self.expires_at_session_ordinal is not None and self.expires_at_session_ordinal < self.available_from_session_ordinal:
            raise ValueError("candidate expiry cannot precede availability")


@dataclass(frozen=True)
class PatternCandidate:
    candidate_id: str
    instrument_id: str
    timeframe: str
    pattern_family: PatternFamily
    pattern_type: PatternType
    direction: PatternDirection
    formed_on: date
    formed_session_ordinal: int
    available_from: date
    available_from_session_ordinal: int
    evaluated_on: date
    evaluation_session_ordinal: int
    source_bar_hash: str
    source_pivots: tuple[SourceFactReference, ...]
    source_boundaries: tuple[SourceFactReference, ...]
    geometry_facts: tuple[EvidenceFact, ...]
    structure_facts: tuple[EvidenceFact, ...]
    direction_confirmation_required: bool
    expires_at_session_ordinal: int | None
    detector_version: str
    calibration_version: str
    parameter_set_id: str
    indicator_layer_version: str
    identity_version: str = IDENTITY_VERSION


@dataclass(frozen=True)
class ConfirmationAssessment:
    candidate_id: str
    confirmation_type: ConfirmationType
    state: ConfirmationState
    reason: str
    observed_on: date | None = None
    observed_session_ordinal: int | None = None
    facts: tuple[EvidenceFact, ...] = ()

    def __post_init__(self) -> None:
        observed = self.observed_on is not None or self.observed_session_ordinal is not None
        if observed != (self.observed_on is not None and self.observed_session_ordinal is not None):
            raise ValueError("confirmation observation date and ordinal must be provided together")
        if self.state in {ConfirmationState.CONFIRMED, ConfirmationState.REJECTED} and not observed:
            raise ValueError("terminal confirmation assessment requires an observed session")
        if self.state is ConfirmationState.NOT_REQUIRED and self.confirmation_type is not ConfirmationType.DIRECTION:
            raise ValueError("only direction confirmation may be not required")
        if not self.reason:
            raise ValueError("confirmation assessment requires a reason")


@dataclass(frozen=True)
class InvalidationAssessment:
    candidate_id: str
    condition: str
    invalidated: bool
    reason: str | None = None
    observed_on: date | None = None
    observed_session_ordinal: int | None = None
    facts: tuple[EvidenceFact, ...] = ()

    def __post_init__(self) -> None:
        if not self.condition:
            raise ValueError("invalidation condition is required")
        observed = self.observed_on is not None or self.observed_session_ordinal is not None
        if observed != (self.observed_on is not None and self.observed_session_ordinal is not None):
            raise ValueError("invalidation observation date and ordinal must be provided together")
        if self.invalidated and (not self.reason or not observed):
            raise ValueError("an invalidated candidate requires reason and observed session")
        if not self.invalidated and (self.reason is not None or observed):
            raise ValueError("a non-invalidated assessment must not invent invalidation facts")


@dataclass(frozen=True)
class PatternResult:
    candidate: PatternCandidate
    structure_confirmation: ConfirmationAssessment
    direction_confirmation: ConfirmationAssessment
    invalidation: InvalidationAssessment
    lifecycle: LifecycleSnapshot

    @property
    def status(self) -> str:
        return self.lifecycle.state.value

    @property
    def result_hash(self) -> str:
        return stable_hash(
            {
                "candidate": self.candidate,
                "structure_confirmation": self.structure_confirmation,
                "direction_confirmation": self.direction_confirmation,
                "invalidation": self.invalidation,
                "lifecycle": self.lifecycle,
            }
        )
