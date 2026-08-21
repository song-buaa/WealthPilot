"""Explicit US development calibrations for level-break detectors.

These values are hypotheses for fixture and development use.  They are not
claimed to be validated production thresholds and intentionally do not reuse
Tovest's BTC calibration.
"""

from __future__ import annotations

from .registry import CalibrationKey, DetectorParameterSet


US_LEVEL_BREAK_DEVELOPMENT_VERSION = "wp-us-level-break-development-v1"


def _values(asset_class: str) -> tuple[tuple[str, bool | int | float | str], ...]:
    is_fixed_income = asset_class == "FIXED_INCOME"
    return (
        ("atr_margin_multiplier", 0.20 if is_fixed_income else 0.25),
        ("calibration_stage", "development_only"),
        ("decisive_margin_pct", 0.05 if is_fixed_income else 0.10),
        ("expiry_sessions", 15),
        ("invalidation_buffer_pct", 0.20 if is_fixed_income else 0.35),
        ("lookback_bars", 60),
        ("minimum_boundary_age_sessions", 3),
        ("minimum_boundary_touches", 1),
        ("parameter_origin", "wealthpilot_us_hypothesis_not_validated"),
        ("zone_atr_width_multiplier", 0.20),
        ("zone_width_pct", 0.15 if is_fixed_income else 0.25),
        ("volume_average_bars", 20),
        ("volume_ratio_threshold", 1.25 if is_fixed_income else 1.50),
    )


def build_us_level_break_development_parameter_sets() -> tuple[DetectorParameterSet, ...]:
    """Return exact Stock/ETF keys; callers must register them explicitly."""

    results: list[DetectorParameterSet] = []
    for asset_class in ("EQUITY", "FIXED_INCOME"):
        for pattern_type in ("breakout", "breakdown"):
            key = CalibrationKey(
                market="US",
                economic_asset_class=asset_class,
                timeframe="1d",
                pattern_family="level_break",
                pattern_type=pattern_type,
                calibration_version=US_LEVEL_BREAK_DEVELOPMENT_VERSION,
            )
            results.append(
                DetectorParameterSet(
                    key=key,
                    values=_values(asset_class),
                    minimum_history_bars=80,
                )
            )
    return tuple(results)
