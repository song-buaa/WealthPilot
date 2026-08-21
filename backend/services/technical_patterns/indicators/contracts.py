"""Provider-neutral contracts for canonical technical indicators."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from ..core.contracts import PatternCoreInput
from ..core.identity import stable_hash


INDICATOR_LAYER_VERSION = "wp-canonical-indicators-v1"


class IndicatorKind(str, Enum):
    EMA = "EMA"
    RSI = "RSI"
    ATR = "ATR"
    MACD = "MACD"
    SMA = "SMA"


@dataclass(frozen=True)
class IndicatorDefinition:
    code: str
    kind: IndicatorKind
    periods: tuple[int, ...]
    source: str = "close"

    def __post_init__(self) -> None:
        if not self.code.strip() or any(period <= 0 for period in self.periods):
            raise ValueError("indicator definitions require a code and positive periods")
        expected = 3 if self.kind is IndicatorKind.MACD else 1
        if len(self.periods) != expected:
            raise ValueError(f"{self.kind.value} requires exactly {expected} period value(s)")
        if self.source not in {"close", "volume"}:
            raise ValueError("indicator source must be close or volume")
        if self.kind in {IndicatorKind.RSI, IndicatorKind.EMA, IndicatorKind.MACD, IndicatorKind.ATR} and self.source != "close":
            raise ValueError(f"{self.kind.value} uses canonical OHLC/close input")


@dataclass(frozen=True)
class IndicatorColumn:
    code: str
    values: tuple[float | None, ...]
    first_valid_session_ordinal: int | None


@dataclass(frozen=True)
class IndicatorSeries:
    instrument_id: str
    timeframe: str
    source_bar_hash: str
    evaluation_session_ordinal: int
    layer_version: str
    backend_name: str
    backend_version: str
    definitions: tuple[IndicatorDefinition, ...]
    columns: tuple[IndicatorColumn, ...]

    @property
    def result_hash(self) -> str:
        return stable_hash(self)

    def column(self, code: str) -> IndicatorColumn:
        try:
            return next(item for item in self.columns if item.code == code)
        except StopIteration as exc:
            raise KeyError(code) from exc


class CanonicalIndicatorLayer(Protocol):
    def calculate(
        self,
        core_input: PatternCoreInput,
        definitions: tuple[IndicatorDefinition, ...],
    ) -> IndicatorSeries: ...
