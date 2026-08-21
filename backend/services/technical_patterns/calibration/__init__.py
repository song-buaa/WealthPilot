"""WealthPilot-owned detector calibration contracts and exact registry."""

from .datasets import (
    CalibrationDataset,
    CalibrationDatasetManifest,
    CalibrationPartition,
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

__all__ = [
    "CalibrationDataset",
    "CalibrationDatasetManifest",
    "CalibrationKey",
    "CalibrationNotConfigured",
    "CalibrationPartition",
    "CalibrationProvider",
    "CalibrationRegistry",
    "DetectorParameterSet",
    "US_ASCENDING_TRIANGLE_DEVELOPMENT_VERSION",
    "US_DOUBLE_REVERSAL_DEVELOPMENT_VERSION",
    "US_LEVEL_BREAK_DEVELOPMENT_VERSION",
    "US_RECTANGLE_DEVELOPMENT_VERSION",
    "build_us_ascending_triangle_development_parameter_sets",
    "build_us_double_reversal_development_parameter_sets",
    "build_us_level_break_development_parameter_sets",
    "build_us_rectangle_development_parameter_sets",
]
