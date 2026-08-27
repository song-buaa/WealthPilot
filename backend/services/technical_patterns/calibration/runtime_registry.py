"""Immutable runtime-candidate freezes and exact approved-scope lookup.

Development parameter values are frozen without threshold changes.  A freeze is
not an approval: only separately validated promotion evidence can populate the
approved registry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..indicators.contracts import INDICATOR_LAYER_VERSION
from .ascending_triangle import build_us_ascending_triangle_development_parameter_sets
from .double_reversal import build_us_double_reversal_development_parameter_sets
from .level_break import build_us_level_break_development_parameter_sets
from .rectangle import build_us_rectangle_development_parameter_sets
from .registry import (
    CalibrationKey,
    CalibrationNotConfigured,
    CalibrationRegistry,
    DetectorParameterSet,
)


GOVERNANCE_ACCEPTANCE = "AI_ASSISTED_REVIEW_ACCEPTED_FOR_V1_PROMOTION"
GOVERNANCE_ACCEPTANCE_RECORD = (
    "docs/PATTERN_EVIDENCE_V1_REVIEW_GOVERNANCE_ACCEPTANCE.md"
)
GOVERNANCE_ACCEPTANCE_RECORD_HASH = (
    "f11781a6fb034c6cd6ad192bb6610876ca8d1613aa2fe12a7ae53a0d40c693bd"
)
REAL_DATASET_MANIFEST_HASH = (
    "a44a3fe2a77d6b36b41fcae29ab4f664ddc3c077331d2c9829bf20ef2494e4f2"
)
REAL_REVIEW_MANIFEST_HASH = (
    "c5c70ba339c93809e8e6244639265fc0cabed576f970b1dd074b5efac78866e4"
)
PATTERN_DATA_ADAPTER_VERSION = "wp-ibkr-pattern-adapter-v1-schedule-paging-v1"
RUNTIME_CANDIDATE_FREEZE_TIMESTAMP = "2026-08-27T20:43:11+08:00"

_RUNTIME_VERSION_BY_FAMILY = {
    "level_break": "wp-us-level-break-runtime-candidate-v1",
    "range": "wp-us-rectangle-runtime-candidate-v1",
    "triangle": "wp-us-ascending-triangle-runtime-candidate-v1",
    "reversal": "wp-us-double-reversal-runtime-candidate-v1",
}
class RuntimePromotionVerdict(str, Enum):
    READY_FOR_RUNTIME_PROMOTION = "READY_FOR_RUNTIME_PROMOTION"
    NEEDS_RECALIBRATION = "NEEDS_RECALIBRATION"
    INSUFFICIENT_REAL_CASE_EVIDENCE = "INSUFFICIENT_REAL_CASE_EVIDENCE"
    DATA_QUALITY_BLOCKED = "DATA_QUALITY_BLOCKED"


class RuntimeCalibrationNotPromoted(CalibrationNotConfigured):
    """Raised when the exact Pattern runtime scope has no promotion record."""


@dataclass(frozen=True, order=True)
class RuntimeCalibrationScope:
    market: str
    economic_asset_class: str
    timeframe: str
    pattern_family: str
    pattern_type: str

    def __post_init__(self) -> None:
        values = (
            self.market,
            self.economic_asset_class,
            self.timeframe,
            self.pattern_family,
            self.pattern_type,
        )
        if any(not value.strip() for value in values):
            raise ValueError("runtime calibration scope requires all five dimensions")
        object.__setattr__(self, "market", self.market.strip().upper())
        object.__setattr__(
            self,
            "economic_asset_class",
            self.economic_asset_class.strip().upper(),
        )
        object.__setattr__(self, "timeframe", self.timeframe.strip().lower())
        object.__setattr__(self, "pattern_family", self.pattern_family.strip().lower())
        object.__setattr__(self, "pattern_type", self.pattern_type.strip().lower())

    @classmethod
    def from_key(cls, key: CalibrationKey) -> "RuntimeCalibrationScope":
        return cls(
            market=key.market,
            economic_asset_class=key.economic_asset_class,
            timeframe=key.timeframe,
            pattern_family=key.pattern_family,
            pattern_type=key.pattern_type,
        )


@dataclass(frozen=True)
class FrozenRuntimeCalibrationCandidate:
    scope: RuntimeCalibrationScope
    development_parameter_set_id: str
    development_parameter_hash: str
    adjustment_attempt_count: int
    parameters: DetectorParameterSet
    freeze_timestamp: str
    dataset_manifest_hash: str
    review_manifest_hash: str
    governance_acceptance: str
    governance_acceptance_record: str
    governance_acceptance_record_hash: str
    detector_version: str
    indicator_layer_version: str
    pattern_data_adapter_version: str

    def __post_init__(self) -> None:
        if RuntimeCalibrationScope.from_key(self.parameters.key) != self.scope:
            raise ValueError("frozen parameters do not match their exact runtime scope")
        if "runtime-candidate" not in self.parameters.key.calibration_version:
            raise ValueError("runtime candidate requires an explicit candidate version")
        if self.adjustment_attempt_count < 0:
            raise ValueError("adjustment attempt count cannot be negative")
        if self.governance_acceptance != GOVERNANCE_ACCEPTANCE:
            raise ValueError("runtime freeze requires the accepted v1 governance decision")
        datetime.fromisoformat(self.freeze_timestamp)
        hashes = (
            self.development_parameter_hash,
            self.parameters.parameters_hash,
            self.dataset_manifest_hash,
            self.review_manifest_hash,
            self.governance_acceptance_record_hash,
        )
        if any(len(value) != 64 for value in hashes):
            raise ValueError("runtime freeze lineage hashes must be SHA-256 values")
        if not all(
            (
                self.development_parameter_set_id,
                self.governance_acceptance_record,
                self.detector_version,
                self.indicator_layer_version,
                self.pattern_data_adapter_version,
            )
        ):
            raise ValueError("runtime freeze requires complete version lineage")

    @property
    def calibration_version(self) -> str:
        return self.parameters.key.calibration_version

    @property
    def final_parameter_set_id(self) -> str:
        return self.parameters.parameter_set_id

    @property
    def final_parameter_hash(self) -> str:
        return self.parameters.parameters_hash


@dataclass(frozen=True)
class RuntimeScopePromotionEvidence:
    scope: RuntimeCalibrationScope
    verdict: RuntimePromotionVerdict
    calibration_version: str
    parameter_hash: str
    holdout_result: str
    untouched_result: str
    governance_acceptance: str

    def __post_init__(self) -> None:
        if self.verdict is RuntimePromotionVerdict.READY_FOR_RUNTIME_PROMOTION:
            if (
                self.holdout_result != "PASS"
                or self.untouched_result != "PASS"
                or self.governance_acceptance != GOVERNANCE_ACCEPTANCE
            ):
                raise ValueError(
                    "runtime promotion requires accepted governance and both unseen passes"
                )


class ApprovedRuntimeCalibrationRegistry:
    """Exact, immutable snapshot containing only validated promoted scopes."""

    def __init__(
        self,
        candidates: tuple[FrozenRuntimeCalibrationCandidate, ...] = (),
        promotions: tuple[RuntimeScopePromotionEvidence, ...] = (),
    ) -> None:
        by_scope: dict[RuntimeCalibrationScope, FrozenRuntimeCalibrationCandidate] = {}
        for candidate in candidates:
            if candidate.scope in by_scope:
                raise ValueError(f"duplicate runtime candidate scope: {candidate.scope}")
            by_scope[candidate.scope] = candidate

        approved: dict[RuntimeCalibrationScope, FrozenRuntimeCalibrationCandidate] = {}
        for promotion in promotions:
            if promotion.verdict is not RuntimePromotionVerdict.READY_FOR_RUNTIME_PROMOTION:
                continue
            try:
                candidate = by_scope[promotion.scope]
            except KeyError as exc:
                raise ValueError("promotion references an unknown frozen candidate") from exc
            if (
                promotion.calibration_version != candidate.calibration_version
                or promotion.parameter_hash != candidate.final_parameter_hash
            ):
                raise ValueError("promotion evidence drifted from the frozen candidate")
            if promotion.scope in approved:
                raise ValueError(f"duplicate promoted runtime scope: {promotion.scope}")
            approved[promotion.scope] = candidate

        self._approved = approved
        self._calibrations = CalibrationRegistry(
            tuple(candidate.parameters for candidate in approved.values())
        )

    def resolve(
        self,
        scope: RuntimeCalibrationScope,
    ) -> FrozenRuntimeCalibrationCandidate:
        try:
            return self._approved[scope]
        except KeyError as exc:
            raise RuntimeCalibrationNotPromoted(
                "exact Pattern runtime scope is not promoted; no cross-scope or "
                "development fallback is permitted"
            ) from exc

    def resolve_parameters(
        self,
        scope: RuntimeCalibrationScope,
    ) -> DetectorParameterSet:
        candidate = self.resolve(scope)
        return self._calibrations.resolve(candidate.parameters.key)

    def snapshot(self) -> tuple[FrozenRuntimeCalibrationCandidate, ...]:
        return tuple(self._approved[scope] for scope in sorted(self._approved))


def _development_parameter_sets() -> tuple[DetectorParameterSet, ...]:
    return (
        build_us_level_break_development_parameter_sets()
        + build_us_rectangle_development_parameter_sets()
        + build_us_ascending_triangle_development_parameter_sets()
        + build_us_double_reversal_development_parameter_sets()
    )


def _detector_version_by_pattern() -> dict[str, str]:
    """Resolve detector lineage lazily to preserve the calibration import boundary.

    Detector modules consume the public calibration contracts.  Importing their
    version constants while calibration itself is being initialized therefore
    creates a first-request-only cycle.  Candidate construction happens after
    module initialization, so the lineage lookup belongs at this runtime edge.
    """

    from ..detectors import (
        ASCENDING_TRIANGLE_DETECTOR_VERSION,
        DOUBLE_REVERSAL_DETECTOR_VERSION,
        LEVEL_BREAK_DETECTOR_VERSION,
        RECTANGLE_DETECTOR_VERSION,
    )

    return {
        "breakout": LEVEL_BREAK_DETECTOR_VERSION,
        "breakdown": LEVEL_BREAK_DETECTOR_VERSION,
        "rectangle": RECTANGLE_DETECTOR_VERSION,
        "ascending_triangle": ASCENDING_TRIANGLE_DETECTOR_VERSION,
        "double_top": DOUBLE_REVERSAL_DETECTOR_VERSION,
        "double_bottom": DOUBLE_REVERSAL_DETECTOR_VERSION,
    }


def build_runtime_candidate_freezes() -> tuple[FrozenRuntimeCalibrationCandidate, ...]:
    """Freeze all twelve reviewed Development thresholds without adjustment."""

    detector_versions = _detector_version_by_pattern()
    freezes: list[FrozenRuntimeCalibrationCandidate] = []
    for development in _development_parameter_sets():
        runtime_parameters = DetectorParameterSet(
            key=CalibrationKey(
                market=development.key.market,
                economic_asset_class=development.key.economic_asset_class,
                timeframe=development.key.timeframe,
                pattern_family=development.key.pattern_family,
                pattern_type=development.key.pattern_type,
                calibration_version=_RUNTIME_VERSION_BY_FAMILY[
                    development.key.pattern_family
                ],
            ),
            values=development.values,
            minimum_history_bars=development.minimum_history_bars,
        )
        scope = RuntimeCalibrationScope.from_key(runtime_parameters.key)
        freezes.append(
            FrozenRuntimeCalibrationCandidate(
                scope=scope,
                development_parameter_set_id=development.parameter_set_id,
                development_parameter_hash=development.parameters_hash,
                adjustment_attempt_count=0,
                parameters=runtime_parameters,
                freeze_timestamp=RUNTIME_CANDIDATE_FREEZE_TIMESTAMP,
                dataset_manifest_hash=REAL_DATASET_MANIFEST_HASH,
                review_manifest_hash=REAL_REVIEW_MANIFEST_HASH,
                governance_acceptance=GOVERNANCE_ACCEPTANCE,
                governance_acceptance_record=GOVERNANCE_ACCEPTANCE_RECORD,
                governance_acceptance_record_hash=GOVERNANCE_ACCEPTANCE_RECORD_HASH,
                detector_version=detector_versions[scope.pattern_type],
                indicator_layer_version=INDICATOR_LAYER_VERSION,
                pattern_data_adapter_version=PATTERN_DATA_ADAPTER_VERSION,
            )
        )
    return tuple(sorted(freezes, key=lambda item: item.scope))
