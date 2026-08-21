"""Fail-closed calibration versioning and human validation workflow.

The framework records definition quality, false positives/negatives, ambiguity,
and review disagreement. Financial return, win-rate, ranking, and probability
are intentionally absent from every contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum

from ..core.identity import stable_hash, stable_id
from .datasets import (
    CalibrationDatasetManifest,
    CalibrationPartition,
    PatternReviewLabel,
)
from .registry import CalibrationKey, DetectorParameterSet


SIX_PATTERN_BINDINGS = (
    ("level_break", "breakout"),
    ("level_break", "breakdown"),
    ("range", "rectangle"),
    ("triangle", "ascending_triangle"),
    ("reversal", "double_top"),
    ("reversal", "double_bottom"),
)


class CalibrationWorkflowError(RuntimeError):
    pass


class PromotionRecommendation(str, Enum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    READY_FOR_GOVERNANCE_REVIEW = "ready_for_governance_review"


@dataclass(frozen=True)
class CalibrationAttemptRecord:
    calibration_key: CalibrationKey
    attempt_number: int
    parameters_hash: str
    development_partition_hash: str
    based_on_partitions: tuple[CalibrationPartition, ...]
    development_review_ids: tuple[str, ...]
    change_reason: str
    attempted_on: date
    attempt_id: str = ""

    def __post_init__(self) -> None:
        if (
            self.attempt_number < 1
            or not self.parameters_hash
            or not self.development_partition_hash
        ):
            raise ValueError("calibration attempt requires sequence and immutable hashes")
        if not self.change_reason.strip():
            raise ValueError("calibration attempt requires an explainable change reason")
        if set(self.based_on_partitions) != {CalibrationPartition.DEVELOPMENT}:
            raise ValueError("parameter attempts may use development evidence only")
        if not self.development_review_ids or any(
            not item.strip() for item in self.development_review_ids
        ):
            raise ValueError("parameter attempts require development review history")
        expected_id = stable_id(
            "caltry",
            {
                "calibration_key": self.calibration_key,
                "attempt_number": self.attempt_number,
                "parameters_hash": self.parameters_hash,
                "development_partition_hash": self.development_partition_hash,
                "based_on_partitions": self.based_on_partitions,
                "development_review_ids": self.development_review_ids,
                "change_reason": self.change_reason,
                "attempted_on": self.attempted_on,
            },
        )
        if self.attempt_id and self.attempt_id != expected_id:
            raise ValueError("attempt_id does not match canonical attempt history")
        object.__setattr__(self, "attempt_id", expected_id)


@dataclass(frozen=True)
class FrozenCalibrationVersion:
    calibration_key: CalibrationKey
    parameter_set_id: str
    parameters_hash: str
    dataset_manifest_id: str
    dataset_manifest_hash: str
    attempt_count: int
    frozen_on: date
    version_id: str = ""

    def __post_init__(self) -> None:
        if min(self.attempt_count, len(self.parameter_set_id), len(self.parameters_hash)) <= 0:
            raise ValueError("frozen calibration requires parameter identity and attempt history")
        if not self.dataset_manifest_id or not self.dataset_manifest_hash:
            raise ValueError("frozen calibration requires dataset manifest identity")
        expected_id = stable_id(
            "calver",
            {
                "calibration_key": self.calibration_key,
                "parameter_set_id": self.parameter_set_id,
                "parameters_hash": self.parameters_hash,
                "dataset_manifest_id": self.dataset_manifest_id,
                "dataset_manifest_hash": self.dataset_manifest_hash,
                "attempt_count": self.attempt_count,
                "frozen_on": self.frozen_on,
            },
        )
        if self.version_id and self.version_id != expected_id:
            raise ValueError("version_id does not match immutable calibration material")
        object.__setattr__(self, "version_id", expected_id)


@dataclass(frozen=True)
class PatternSampleReview:
    dataset_id: str
    partition: CalibrationPartition
    reviewer_ids: tuple[str, ...]
    label: PatternReviewLabel
    definition_conforms: bool | None
    false_positive: bool
    false_negative: bool
    boundary_ambiguous: bool
    notes: str
    reviewed_on: date
    review_id: str = ""

    def __post_init__(self) -> None:
        if (
            not self.dataset_id
            or not self.reviewer_ids
            or any(not item.strip() for item in self.reviewer_ids)
        ):
            raise ValueError("sample review requires dataset and reviewer identity")
        if len(set(self.reviewer_ids)) != len(self.reviewer_ids):
            raise ValueError("sample review reviewer identities must be unique")
        if self.partition is CalibrationPartition.DEVELOPMENT:
            raise ValueError("holdout/untouched evaluation reviews cannot be development reviews")
        if self.label is PatternReviewLabel.REVIEW_DISAGREEMENT:
            if len(self.reviewer_ids) < 2 or not self.notes.strip():
                raise ValueError("review disagreement requires multiple reviewers and notes")
        if self.label is PatternReviewLabel.AMBIGUOUS and not self.boundary_ambiguous:
            raise ValueError("ambiguous review must record boundary ambiguity")
        expected_id = stable_id(
            "calreview",
            {
                "dataset_id": self.dataset_id,
                "partition": self.partition,
                "reviewer_ids": self.reviewer_ids,
                "label": self.label,
                "definition_conforms": self.definition_conforms,
                "false_positive": self.false_positive,
                "false_negative": self.false_negative,
                "boundary_ambiguous": self.boundary_ambiguous,
                "notes": self.notes,
                "reviewed_on": self.reviewed_on,
            },
        )
        if self.review_id and self.review_id != expected_id:
            raise ValueError("review_id does not match canonical human review")
        object.__setattr__(self, "review_id", expected_id)


@dataclass(frozen=True)
class PatternValidationEvaluation:
    calibration_version_id: str
    partition: CalibrationPartition
    parameters_hash: str
    dataset_manifest_hash: str
    partition_hash: str
    reviews: tuple[PatternSampleReview, ...]
    definition_review_pass: bool
    false_positive_review_pass: bool
    false_negative_review_pass: bool
    boundary_review_pass: bool
    human_review_pass: bool
    failure_modes: tuple[str, ...]
    completed_on: date
    evaluation_id: str = ""

    def __post_init__(self) -> None:
        if self.partition not in {
            CalibrationPartition.HOLDOUT,
            CalibrationPartition.UNTOUCHED_VALIDATION,
        }:
            raise ValueError("validation evaluation must be holdout or untouched_validation")
        if (
            not self.calibration_version_id
            or not self.parameters_hash
            or not self.dataset_manifest_hash
            or not self.partition_hash
        ):
            raise ValueError("validation evaluation requires frozen version and dataset hashes")
        if not self.reviews or len({item.dataset_id for item in self.reviews}) != len(self.reviews):
            raise ValueError("validation evaluation requires one unique review per sample")
        if any(item.partition is not self.partition for item in self.reviews):
            raise ValueError("review partition does not match evaluation partition")
        expected_id = stable_id(
            "caleval",
            {
                "calibration_version_id": self.calibration_version_id,
                "partition": self.partition,
                "parameters_hash": self.parameters_hash,
                "dataset_manifest_hash": self.dataset_manifest_hash,
                "partition_hash": self.partition_hash,
                "reviews": tuple(sorted(self.reviews, key=lambda item: item.review_id)),
                "definition_review_pass": self.definition_review_pass,
                "false_positive_review_pass": self.false_positive_review_pass,
                "false_negative_review_pass": self.false_negative_review_pass,
                "boundary_review_pass": self.boundary_review_pass,
                "human_review_pass": self.human_review_pass,
                "failure_modes": self.failure_modes,
                "completed_on": self.completed_on,
            },
        )
        if self.evaluation_id and self.evaluation_id != expected_id:
            raise ValueError("evaluation_id does not match canonical validation evidence")
        object.__setattr__(self, "evaluation_id", expected_id)

    @property
    def passed(self) -> bool:
        checks = (
            self.definition_review_pass,
            self.false_positive_review_pass,
            self.false_negative_review_pass,
            self.boundary_review_pass,
            self.human_review_pass,
        )
        unresolved = any(
            item.label is PatternReviewLabel.REVIEW_DISAGREEMENT for item in self.reviews
        )
        return all(checks) and not unresolved

    @property
    def label_counts(self) -> tuple[tuple[str, int], ...]:
        return tuple(
            (label.value, sum(item.label is label for item in self.reviews))
            for label in PatternReviewLabel
        )


@dataclass(frozen=True)
class PromotionAssessment:
    calibration_version_id: str
    detector_pass: bool
    calibration_frozen: bool
    holdout_pass: bool
    untouched_validation_pass: bool
    human_review_pass: bool
    coverage_pass: bool
    eligible_for_governance_review: bool
    blocking_reasons: tuple[str, ...]

    @property
    def result_hash(self) -> str:
        return stable_hash(self)


@dataclass(frozen=True)
class PatternValidationReport:
    pattern_type: str
    calibration_version: str
    dataset_manifest_hash: str
    positive_cases: int
    negative_cases: int
    ambiguous_cases: int
    review_disagreement_cases: int
    failure_modes: tuple[str, ...]
    promotion_recommendation: PromotionRecommendation
    report_id: str = ""

    def __post_init__(self) -> None:
        counts = (
            self.positive_cases,
            self.negative_cases,
            self.ambiguous_cases,
            self.review_disagreement_cases,
        )
        if min(counts) < 0 or not self.pattern_type or not self.calibration_version:
            raise ValueError("Pattern validation report requires identity and non-negative counts")
        expected_id = stable_id(
            "calreport",
            {
                "pattern_type": self.pattern_type,
                "calibration_version": self.calibration_version,
                "dataset_manifest_hash": self.dataset_manifest_hash,
                "counts": counts,
                "failure_modes": self.failure_modes,
                "promotion_recommendation": self.promotion_recommendation,
            },
        )
        if self.report_id and self.report_id != expected_id:
            raise ValueError("report_id does not match canonical validation report")
        object.__setattr__(self, "report_id", expected_id)


class CalibrationValidationFramework:
    """In-memory deterministic ledger enforcing freeze and partition order."""

    def __init__(self) -> None:
        self._versions: dict[str, FrozenCalibrationVersion] = {}
        self._version_by_key: dict[CalibrationKey, FrozenCalibrationVersion] = {}
        self._manifests: dict[str, CalibrationDatasetManifest] = {}
        self._evaluations: dict[tuple[str, CalibrationPartition], PatternValidationEvaluation] = {}
        self._exposed_nondevelopment_hashes: set[str] = set()

    def register_version(
        self,
        parameters: DetectorParameterSet,
        manifest: CalibrationDatasetManifest,
        attempts: tuple[CalibrationAttemptRecord, ...],
        *,
        frozen_on: date,
    ) -> FrozenCalibrationVersion:
        key = parameters.key
        if (
            key.market != manifest.market
            or key.economic_asset_class != manifest.economic_asset_class
            or key.timeframe != manifest.timeframe
            or key.pattern_family != manifest.pattern_family
            or key.pattern_type != manifest.pattern_type
        ):
            raise CalibrationWorkflowError(
                "calibration key does not match dataset manifest binding"
            )
        if not attempts or tuple(item.attempt_number for item in attempts) != tuple(
            range(1, len(attempts) + 1)
        ):
            raise CalibrationWorkflowError("parameter attempts must be complete and sequential")
        if any(item.attempted_on > frozen_on for item in attempts) or any(
            right.attempted_on < left.attempted_on
            for left, right in zip(attempts, attempts[1:])
        ):
            raise CalibrationWorkflowError(
                "parameter attempt history must be chronological before freeze"
            )
        development_hash = manifest.partition_hash(CalibrationPartition.DEVELOPMENT)
        if any(
            item.calibration_key != key
            or item.development_partition_hash != development_hash
            for item in attempts
        ):
            raise CalibrationWorkflowError(
                "attempt history is not bound to this key/development set"
            )
        if attempts[-1].parameters_hash != parameters.parameters_hash:
            raise CalibrationWorkflowError(
                "final attempt does not match the frozen parameters hash"
            )
        record = FrozenCalibrationVersion(
            calibration_key=key,
            parameter_set_id=parameters.parameter_set_id,
            parameters_hash=parameters.parameters_hash,
            dataset_manifest_id=manifest.manifest_id,
            dataset_manifest_hash=manifest.manifest_hash,
            attempt_count=len(attempts),
            frozen_on=frozen_on,
        )
        existing = self._version_by_key.get(key)
        if existing == record:
            return existing
        if existing is not None and existing != record:
            raise CalibrationWorkflowError("calibration version is immutable once registered")
        nondevelopment_hashes = {
            item.source_bar_hash
            for partition in (
                CalibrationPartition.HOLDOUT,
                CalibrationPartition.UNTOUCHED_VALIDATION,
            )
            for item in manifest.partition(partition)
        }
        if nondevelopment_hashes & self._exposed_nondevelopment_hashes:
            raise CalibrationWorkflowError(
                "previously exposed holdout/validation evidence cannot be reused as unseen"
            )
        self._versions[record.version_id] = record
        self._version_by_key[key] = record
        self._manifests[record.version_id] = manifest
        return record

    def record_evaluation(self, evaluation: PatternValidationEvaluation) -> None:
        try:
            version = self._versions[evaluation.calibration_version_id]
            manifest = self._manifests[evaluation.calibration_version_id]
        except KeyError as exc:
            raise CalibrationWorkflowError(
                "evaluation references an unknown frozen version"
            ) from exc
        if (
            evaluation.parameters_hash != version.parameters_hash
            or evaluation.dataset_manifest_hash != version.dataset_manifest_hash
            or evaluation.partition_hash != manifest.partition_hash(evaluation.partition)
        ):
            raise CalibrationWorkflowError("evaluation hash drifted from the frozen version")
        if evaluation.completed_on < version.frozen_on or any(
            item.reviewed_on < version.frozen_on for item in evaluation.reviews
        ):
            raise CalibrationWorkflowError(
                "holdout/validation review cannot predate parameter freeze"
            )
        expected_ids = {
            item.dataset_id for item in manifest.partition(evaluation.partition)
        }
        if {item.dataset_id for item in evaluation.reviews} != expected_ids:
            raise CalibrationWorkflowError("evaluation must review every and only partition sample")
        holdout_key = (version.version_id, CalibrationPartition.HOLDOUT)
        if (
            evaluation.partition is CalibrationPartition.UNTOUCHED_VALIDATION
            and (
                holdout_key not in self._evaluations
                or not self._evaluations[holdout_key].passed
            )
        ):
            raise CalibrationWorkflowError(
                "untouched validation cannot be opened before frozen holdout passes"
            )
        if evaluation.partition is CalibrationPartition.UNTOUCHED_VALIDATION:
            holdout = self._evaluations[holdout_key]
            if evaluation.completed_on < holdout.completed_on:
                raise CalibrationWorkflowError(
                    "untouched validation must follow chronological holdout review"
                )
        ledger_key = (version.version_id, evaluation.partition)
        existing = self._evaluations.get(ledger_key)
        if existing is not None and existing != evaluation:
            raise CalibrationWorkflowError("partition evaluation is immutable once recorded")
        self._evaluations[ledger_key] = evaluation
        self._exposed_nondevelopment_hashes.update(
            item.source_bar_hash for item in manifest.partition(evaluation.partition)
        )

    def promotion_assessment(
        self, version_id: str, *, detector_pass: bool
    ) -> PromotionAssessment:
        try:
            version = self._versions[version_id]
            manifest = self._manifests[version_id]
        except KeyError as exc:
            raise CalibrationWorkflowError("unknown frozen calibration version") from exc
        holdout = self._evaluations.get((version_id, CalibrationPartition.HOLDOUT))
        untouched = self._evaluations.get(
            (version_id, CalibrationPartition.UNTOUCHED_VALIDATION)
        )
        holdout_pass = holdout is not None and holdout.passed
        untouched_pass = untouched is not None and untouched.passed
        human_pass = bool(
            holdout is not None
            and untouched is not None
            and holdout.human_review_pass
            and untouched.human_review_pass
            and holdout.passed
            and untouched.passed
        )
        coverage_pass = not manifest.coverage_gaps()
        checks = {
            "detector_not_passed": detector_pass,
            "calibration_not_frozen": True,
            "holdout_not_passed": holdout_pass,
            "untouched_validation_not_passed": untouched_pass,
            "human_review_not_passed": human_pass,
            "dataset_coverage_incomplete": coverage_pass,
        }
        blockers = tuple(reason for reason, passed in checks.items() if not passed)
        return PromotionAssessment(
            calibration_version_id=version_id,
            detector_pass=detector_pass,
            calibration_frozen=True,
            holdout_pass=holdout_pass,
            untouched_validation_pass=untouched_pass,
            human_review_pass=human_pass,
            coverage_pass=coverage_pass,
            eligible_for_governance_review=not blockers,
            blocking_reasons=blockers,
        )

    def build_report(self, version_id: str, *, detector_pass: bool) -> PatternValidationReport:
        version = self._versions[version_id]
        manifest = self._manifests[version_id]
        evaluations = tuple(
            self._evaluations[key]
            for key in (
                (version_id, CalibrationPartition.HOLDOUT),
                (version_id, CalibrationPartition.UNTOUCHED_VALIDATION),
            )
            if key in self._evaluations
        )
        counts = {
            label: sum(
                item.label is label
                for evaluation in evaluations
                for item in evaluation.reviews
            )
            for label in PatternReviewLabel
        }
        assessment = self.promotion_assessment(version_id, detector_pass=detector_pass)
        failure_modes = tuple(
            dict.fromkeys(
                item
                for evaluation in evaluations
                for item in evaluation.failure_modes
            )
        )
        return PatternValidationReport(
            pattern_type=version.calibration_key.pattern_type,
            calibration_version=version.calibration_key.calibration_version,
            dataset_manifest_hash=manifest.manifest_hash,
            positive_cases=counts[PatternReviewLabel.POSITIVE],
            negative_cases=counts[PatternReviewLabel.NEGATIVE],
            ambiguous_cases=counts[PatternReviewLabel.AMBIGUOUS],
            review_disagreement_cases=counts[PatternReviewLabel.REVIEW_DISAGREEMENT],
            failure_modes=failure_modes,
            promotion_recommendation=(
                PromotionRecommendation.READY_FOR_GOVERNANCE_REVIEW
                if assessment.eligible_for_governance_review
                else PromotionRecommendation.INSUFFICIENT_EVIDENCE
            ),
        )
