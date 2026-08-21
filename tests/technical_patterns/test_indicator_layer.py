from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from backend.services.technical_patterns.core import PatternInputMapper
from backend.services.technical_patterns.indicators import (
    IndicatorDefinition,
    IndicatorKind,
    TalibIndicatorLayer,
)

from .conftest import canonical_series_from_case


def _core_input():
    sessions = []
    current = date(2025, 1, 2)
    while len(sessions) < 80:
        if current.weekday() < 5:
            sessions.append(current.isoformat())
        current += timedelta(days=1)
    highs = [101.0 + index * 0.4 + (index % 5) * 0.2 for index in range(80)]
    lows = [value - 2.0 - (index % 3) * 0.1 for index, value in enumerate(highs)]
    series = canonical_series_from_case(
        {
            "instrument_id": "fixture:indicators",
            "con_id": 88,
            "sessions": sessions,
            "highs": highs,
            "lows": lows,
        },
        source_hash="indicator-source",
    )
    return PatternInputMapper().map_series(series)


def test_talib_layer_produces_aligned_versioned_deterministic_columns():
    core_input = _core_input()
    definitions = (
        IndicatorDefinition("ema20", IndicatorKind.EMA, (20,)),
        IndicatorDefinition("rsi14", IndicatorKind.RSI, (14,)),
        IndicatorDefinition("atr14", IndicatorKind.ATR, (14,)),
        IndicatorDefinition("macd", IndicatorKind.MACD, (12, 26, 9)),
        IndicatorDefinition("volume_sma20", IndicatorKind.SMA, (20,), source="volume"),
    )
    layer = TalibIndicatorLayer()

    first = layer.calculate(core_input, definitions)
    second = layer.calculate(core_input, definitions)

    assert first.backend_name == "TA-Lib"
    assert first.backend_version != "unknown"
    assert first.result_hash == second.result_hash
    assert all(len(column.values) == len(core_input.bars) for column in first.columns)
    assert first.column("ema20").first_valid_session_ordinal == 19
    assert first.column("rsi14").first_valid_session_ordinal == 14
    assert first.column("atr14").first_valid_session_ordinal == 14
    assert first.column("macd.line").first_valid_session_ordinal == 33
    assert first.column("volume_sma20").first_valid_session_ordinal == 19
    assert first.column("ema20").values[18] is None
    assert first.column("ema20").values[19] is not None


def test_detector_package_has_no_direct_talib_calls():
    root = Path(__file__).parents[2] / "backend/services/technical_patterns"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "talib_layer.py":
            continue
        source = path.read_text(encoding="utf-8")
        if "import talib" in source or "from talib" in source or "talib." in source:
            offenders.append(str(path.relative_to(root)))

    assert offenders == []
