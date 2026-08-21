"""Provider-independent Pattern Core foundation.

This package deliberately contains no detector, Decision, portfolio, broker,
order, publishing, or execution-plan integration.
"""

from .boundary import BoundaryParameters, BoundaryTrendEngine
from .contracts import CorePatternBar, PatternCoreInput, Pivot
from .geometry import LineFit, SessionPoint, fit_line, line_price
from .input_mapper import PatternInputMapper
from .lifecycle import LifecycleCore, LifecycleSnapshot, LifecycleState
from .pivot import PivotEngine, PivotParameters
from .range_structure import RangeStructureEngine

__all__ = [
    "BoundaryParameters",
    "BoundaryTrendEngine",
    "CorePatternBar",
    "LifecycleCore",
    "LifecycleSnapshot",
    "LifecycleState",
    "LineFit",
    "PatternCoreInput",
    "PatternInputMapper",
    "Pivot",
    "PivotEngine",
    "PivotParameters",
    "RangeStructureEngine",
    "SessionPoint",
    "fit_line",
    "line_price",
]
