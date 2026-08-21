"""WealthPilot-owned detector calibration contracts and exact registry."""

from .datasets import (
    CalibrationDataset,
    CalibrationDatasetManifest,
    CalibrationPartition,
)
from .level_break import (
    US_LEVEL_BREAK_DEVELOPMENT_VERSION,
    build_us_level_break_development_parameter_sets,
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

__all__ = [
    "CalibrationDataset",
    "CalibrationDatasetManifest",
    "CalibrationKey",
    "CalibrationNotConfigured",
    "CalibrationPartition",
    "CalibrationProvider",
    "CalibrationRegistry",
    "DetectorParameterSet",
    "US_LEVEL_BREAK_DEVELOPMENT_VERSION",
    "US_RECTANGLE_DEVELOPMENT_VERSION",
    "build_us_level_break_development_parameter_sets",
    "build_us_rectangle_development_parameter_sets",
]
