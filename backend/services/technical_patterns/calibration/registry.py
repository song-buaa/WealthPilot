"""Exact, fail-closed calibration lookup for Pattern detectors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeAlias

from ..core.identity import stable_hash, stable_id


ParameterValue: TypeAlias = bool | int | float | str


class CalibrationNotConfigured(LookupError):
    """Raised when no exact market/asset/timeframe/pattern calibration exists."""


@dataclass(frozen=True, order=True)
class CalibrationKey:
    market: str
    economic_asset_class: str
    timeframe: str
    pattern_family: str
    pattern_type: str
    calibration_version: str

    def __post_init__(self) -> None:
        normalized = {
            "market": self.market,
            "economic_asset_class": self.economic_asset_class,
            "timeframe": self.timeframe,
            "pattern_family": self.pattern_family,
            "pattern_type": self.pattern_type,
            "calibration_version": self.calibration_version,
        }
        if any(not str(value).strip() for value in normalized.values()):
            raise ValueError("calibration keys require all six binding dimensions")
        object.__setattr__(self, "market", self.market.strip().upper())
        object.__setattr__(self, "economic_asset_class", self.economic_asset_class.strip().upper())
        object.__setattr__(self, "timeframe", self.timeframe.strip().lower())
        object.__setattr__(self, "pattern_family", self.pattern_family.strip().lower())
        object.__setattr__(self, "pattern_type", self.pattern_type.strip().lower())
        object.__setattr__(self, "calibration_version", self.calibration_version.strip())


@dataclass(frozen=True)
class DetectorParameterSet:
    key: CalibrationKey
    values: tuple[tuple[str, ParameterValue], ...]
    minimum_history_bars: int
    parameter_set_id: str = ""

    def __post_init__(self) -> None:
        if self.minimum_history_bars <= 0:
            raise ValueError("minimum_history_bars must be positive")
        names = tuple(name for name, _ in self.values)
        if any(not name.strip() for name in names) or len(names) != len(set(names)):
            raise ValueError("parameter names must be non-empty and unique")
        ordered = tuple(sorted(self.values, key=lambda item: item[0]))
        object.__setattr__(self, "values", ordered)
        expected_id = stable_id(
            "cal",
            {
                "key": self.key,
                "values": ordered,
                "minimum_history_bars": self.minimum_history_bars,
            },
        )
        if self.parameter_set_id and self.parameter_set_id != expected_id:
            raise ValueError("parameter_set_id does not match canonical calibration material")
        object.__setattr__(self, "parameter_set_id", expected_id)

    @property
    def parameters_hash(self) -> str:
        return stable_hash(
            {
                "key": self.key,
                "values": self.values,
                "minimum_history_bars": self.minimum_history_bars,
            }
        )

    def require(self, name: str) -> ParameterValue:
        try:
            return dict(self.values)[name]
        except KeyError as exc:
            raise CalibrationNotConfigured(
                f"calibration {self.parameter_set_id} has no explicit parameter {name!r}"
            ) from exc


class CalibrationProvider(Protocol):
    def resolve(self, key: CalibrationKey) -> DetectorParameterSet: ...


class CalibrationRegistry:
    """In-memory immutable-value registry with exact lookup and no fallback."""

    def __init__(self, parameter_sets: tuple[DetectorParameterSet, ...] = ()) -> None:
        self._entries: dict[CalibrationKey, DetectorParameterSet] = {}
        for parameter_set in parameter_sets:
            self.register(parameter_set)

    def register(self, parameter_set: DetectorParameterSet) -> None:
        existing = self._entries.get(parameter_set.key)
        if existing is not None and existing != parameter_set:
            raise ValueError(f"conflicting calibration for exact key: {parameter_set.key}")
        self._entries[parameter_set.key] = parameter_set

    def resolve(self, key: CalibrationKey) -> DetectorParameterSet:
        try:
            return self._entries[key]
        except KeyError as exc:
            raise CalibrationNotConfigured(
                "no exact detector calibration for "
                f"{key.market}/{key.economic_asset_class}/{key.timeframe}/"
                f"{key.pattern_family}/{key.pattern_type}/{key.calibration_version}; "
                "cross-market, cross-asset and BTC fallback are forbidden"
            ) from exc

    def snapshot(self) -> tuple[DetectorParameterSet, ...]:
        return tuple(self._entries[key] for key in sorted(self._entries))
