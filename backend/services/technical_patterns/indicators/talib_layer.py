"""The only Pattern package boundary allowed to call TA-Lib."""

from __future__ import annotations

import importlib
import math
from typing import Any

import numpy as np

from ..core.contracts import PatternCoreInput
from .contracts import (
    INDICATOR_LAYER_VERSION,
    IndicatorColumn,
    IndicatorDefinition,
    IndicatorKind,
    IndicatorSeries,
)


class IndicatorBackendUnavailable(RuntimeError):
    pass


class TalibIndicatorLayer:
    """Calculate aligned, versioned indicator columns with explicit warm-up."""

    def __init__(self, backend: Any | None = None) -> None:
        if backend is None:
            try:
                backend = importlib.import_module("talib")
            except ImportError as exc:
                raise IndicatorBackendUnavailable(
                    "TA-Lib is required for canonical Pattern indicators"
                ) from exc
        self._backend = backend
        self._backend_version = str(getattr(backend, "__version__", "unknown"))

    def calculate(
        self,
        core_input: PatternCoreInput,
        definitions: tuple[IndicatorDefinition, ...],
    ) -> IndicatorSeries:
        if len(definitions) != len({item.code for item in definitions}):
            raise ValueError("indicator definition codes must be unique")
        close = np.asarray([bar.close for bar in core_input.bars], dtype="float64")
        high = np.asarray([bar.high for bar in core_input.bars], dtype="float64")
        low = np.asarray([bar.low for bar in core_input.bars], dtype="float64")
        volume = np.asarray([bar.volume for bar in core_input.bars], dtype="float64")
        columns: list[IndicatorColumn] = []
        for definition in definitions:
            if definition.kind is IndicatorKind.EMA:
                outputs = ((definition.code, self._backend.EMA(close, timeperiod=definition.periods[0])),)
            elif definition.kind is IndicatorKind.RSI:
                outputs = ((definition.code, self._backend.RSI(close, timeperiod=definition.periods[0])),)
            elif definition.kind is IndicatorKind.ATR:
                outputs = ((definition.code, self._backend.ATR(high, low, close, timeperiod=definition.periods[0])),)
            elif definition.kind is IndicatorKind.SMA:
                source = close if definition.source == "close" else volume
                outputs = ((definition.code, self._backend.SMA(source, timeperiod=definition.periods[0])),)
            elif definition.kind is IndicatorKind.MACD:
                line, signal, histogram = self._backend.MACD(
                    close,
                    fastperiod=definition.periods[0],
                    slowperiod=definition.periods[1],
                    signalperiod=definition.periods[2],
                )
                outputs = (
                    (f"{definition.code}.line", line),
                    (f"{definition.code}.signal", signal),
                    (f"{definition.code}.histogram", histogram),
                )
            else:  # pragma: no cover - Enum makes this defensive only
                raise ValueError(f"unsupported indicator kind: {definition.kind}")
            columns.extend(self._column(code, values, len(core_input.bars)) for code, values in outputs)

        return IndicatorSeries(
            instrument_id=core_input.instrument_id,
            timeframe=core_input.timeframe,
            source_bar_hash=core_input.source_bar_hash,
            evaluation_session_ordinal=core_input.bars[-1].session_ordinal,
            layer_version=INDICATOR_LAYER_VERSION,
            backend_name="TA-Lib",
            backend_version=self._backend_version,
            definitions=definitions,
            columns=tuple(columns),
        )

    @staticmethod
    def _column(code: str, raw_values: Any, expected_length: int) -> IndicatorColumn:
        values = tuple(float(value) for value in raw_values)
        if len(values) != expected_length:
            raise ValueError(f"indicator {code} returned an unaligned output")
        normalized: list[float | None] = []
        for value in values:
            if math.isnan(value):
                normalized.append(None)
            elif not math.isfinite(value):
                raise ValueError(f"indicator {code} returned a non-finite value")
            else:
                normalized.append(value)
        first_valid = next((index for index, value in enumerate(normalized) if value is not None), None)
        return IndicatorColumn(code, tuple(normalized), first_valid)
