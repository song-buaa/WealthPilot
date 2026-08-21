"""Golden comparison utilities for future Tovest-to-WealthPilot detector parity."""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from typing import Any, Mapping

from ..core.identity import canonicalize
from .contracts import PatternResult


def normalize_pattern_result(result: PatternResult) -> dict[str, Any]:
    """Return the detector-only comparison contract, excluding Product output."""

    return canonicalize(
        {
            "pattern_type": result.candidate.pattern_type,
            "direction": result.candidate.direction,
            "status": result.status,
            "identity": result.candidate.candidate_id,
            "candidate": result.candidate,
            "confirmation": {
                "structure": result.structure_confirmation,
                "direction": result.direction_confirmation,
            },
            "invalidation": result.invalidation,
            "lifecycle": result.lifecycle,
        }
    )


@dataclass(frozen=True)
class ParityDifference:
    path: str
    expected: Any
    actual: Any
    reason: str


@dataclass(frozen=True)
class ParityResult:
    passed: bool
    differences: tuple[ParityDifference, ...]


class GoldenParityComparator:
    def __init__(self, *, absolute_tolerance: float = 1e-8, relative_tolerance: float = 1e-9) -> None:
        if absolute_tolerance < 0 or relative_tolerance < 0:
            raise ValueError("parity tolerances must be non-negative")
        self.absolute_tolerance = absolute_tolerance
        self.relative_tolerance = relative_tolerance

    def compare(self, expected: Mapping[str, Any], actual: PatternResult | Mapping[str, Any]) -> ParityResult:
        normalized_actual = normalize_pattern_result(actual) if dataclasses.is_dataclass(actual) else canonicalize(actual)
        differences: list[ParityDifference] = []
        self._compare(canonicalize(expected), normalized_actual, "$", differences)
        return ParityResult(not differences, tuple(differences))

    def _compare(self, expected: Any, actual: Any, path: str, differences: list[ParityDifference]) -> None:
        if isinstance(expected, dict):
            if not isinstance(actual, dict):
                differences.append(ParityDifference(path, expected, actual, "type_mismatch"))
                return
            if set(expected) != set(actual):
                differences.append(
                    ParityDifference(path, sorted(expected), sorted(actual), "mapping_keys_mismatch")
                )
                return
            for key in sorted(expected):
                self._compare(expected[key], actual[key], f"{path}.{key}", differences)
            return
        if isinstance(expected, list):
            if not isinstance(actual, list) or len(expected) != len(actual):
                differences.append(ParityDifference(path, expected, actual, "sequence_shape_mismatch"))
                return
            for index, (left, right) in enumerate(zip(expected, actual)):
                self._compare(left, right, f"{path}[{index}]", differences)
            return
        if isinstance(expected, float) and isinstance(actual, (float, int)) and not isinstance(actual, bool):
            if not math.isclose(expected, float(actual), rel_tol=self.relative_tolerance, abs_tol=self.absolute_tolerance):
                differences.append(ParityDifference(path, expected, actual, "numeric_tolerance_exceeded"))
            return
        if expected != actual:
            differences.append(ParityDifference(path, expected, actual, "exact_value_mismatch"))
