"""WealthPilot-owned detector calibration contracts and exact registry."""

from .datasets import (
    AssetCoverage,
    CalibrationDataset,
    CalibrationDatasetManifest,
    CalibrationPartition,
    DatasetReviewStatus,
    MarketEdgeCase,
    MarketRegime,
    PatternReviewLabel,
)
from .ascending_triangle import (
    US_ASCENDING_TRIANGLE_DEVELOPMENT_VERSION,
    build_us_ascending_triangle_development_parameter_sets,
)
from .level_break import (
    US_LEVEL_BREAK_DEVELOPMENT_VERSION,
    build_us_level_break_development_parameter_sets,
)
from .double_reversal import (
    US_DOUBLE_REVERSAL_DEVELOPMENT_VERSION,
    build_us_double_reversal_development_parameter_sets,
)

from .registry import (
    CalibrationKey,
    CalibrationNotConfigured,
    CalibrationProvider,
    CalibrationRegistry,
    DetectorParameterSet,
)
from .rectangle import (
    US_RECTANGLE_DEVELOPMENT_VERSION,
    build_us_rectangle_development_parameter_sets,
)
from .runtime_registry import (
    GOVERNANCE_ACCEPTANCE,
    ApprovedRuntimeCalibrationRegistry,
    FrozenRuntimeCalibrationCandidate,
    RuntimeCalibrationNotPromoted,
    RuntimeCalibrationScope,
    RuntimePromotionVerdict,
    RuntimeScopePromotionEvidence,
    build_runtime_candidate_freezes,
)
from .validation import (
    SIX_PATTERN_BINDINGS,
    CalibrationAttemptRecord,
    CalibrationValidationFramework,
    CalibrationWorkflowError,
    FrozenCalibrationVersion,
    PatternSampleReview,
    PatternValidationEvaluation,
    PatternValidationReport,
    PromotionAssessment,
    PromotionRecommendation,
)

__all__ = [
    "AssetCoverage",
    "CalibrationAttemptRecord",
    "CalibrationDataset",
    "CalibrationDatasetManifest",
    "CalibrationKey",
    "CalibrationNotConfigured",
    "CalibrationPartition",
    "CalibrationProvider",
    "CalibrationRegistry",
    "CalibrationValidationFramework",
    "CalibrationWorkflowError",
    "DatasetReviewStatus",
    "DetectorParameterSet",
    "FrozenRuntimeCalibrationCandidate",
    "FrozenCalibrationVersion",
    "MarketEdgeCase",
    "MarketRegime",
    "PatternReviewLabel",
    "PatternSampleReview",
    "PatternValidationEvaluation",
    "PatternValidationReport",
    "PromotionAssessment",
    "PromotionRecommendation",
    "GOVERNANCE_ACCEPTANCE",
    "ApprovedRuntimeCalibrationRegistry",
    "RuntimeCalibrationNotPromoted",
    "RuntimeCalibrationScope",
    "RuntimePromotionVerdict",
    "RuntimeScopePromotionEvidence",
    "SIX_PATTERN_BINDINGS",
    "US_ASCENDING_TRIANGLE_DEVELOPMENT_VERSION",
    "US_DOUBLE_REVERSAL_DEVELOPMENT_VERSION",
    "US_LEVEL_BREAK_DEVELOPMENT_VERSION",
    "US_RECTANGLE_DEVELOPMENT_VERSION",
    "build_us_ascending_triangle_development_parameter_sets",
    "build_us_double_reversal_development_parameter_sets",
    "build_us_level_break_development_parameter_sets",
    "build_us_rectangle_development_parameter_sets",
    "build_runtime_candidate_freezes",
]
