"""Explicit US development calibrations for the neutral Rectangle detector."""

from __future__ import annotations

from .registry import CalibrationKey, DetectorParameterSet


US_RECTANGLE_DEVELOPMENT_VERSION = "wp-us-rectangle-development-v1"


def _values(asset_class: str) -> tuple[tuple[str, bool | int | float | str], ...]:
    is_fixed_income = asset_class == "FIXED_INCOME"
    return (
        ("boundary_tolerance_pct", 0.003 if is_fixed_income else 0.006),
        ("calibration_stage", "development_only"),
        ("expiry_sessions", 40),
        ("invalidation_buffer_pct", 0.10 if is_fixed_income else 0.20),
        ("maximum_boundary_zone_width_pct", 0.60 if is_fixed_income else 1.00),
        ("maximum_range_width_pct", 12.0 if is_fixed_income else 20.0),
        ("minimum_range_width_pct", 0.50 if is_fixed_income else 2.00),
        ("minimum_structure_span_sessions", 10),
        ("minimum_touches_per_side", 2),
        ("parameter_origin", "wealthpilot_us_hypothesis_not_validated"),
        ("pivot_left_window_bars", 3),
        ("pivot_minimum_bar_separation", 2),
        ("pivot_minimum_price_separation_pct", 0.005 if is_fixed_income else 0.02),
        ("pivot_plateau_tolerance_pct", 0.001),
        ("pivot_right_confirmation_bars", 2),
    )


def build_us_rectangle_development_parameter_sets() -> tuple[DetectorParameterSet, ...]:
    return tuple(
        DetectorParameterSet(
            key=CalibrationKey(
                market="US",
                economic_asset_class=asset_class,
                timeframe="1d",
                pattern_family="range",
                pattern_type="rectangle",
                calibration_version=US_RECTANGLE_DEVELOPMENT_VERSION,
            ),
            values=_values(asset_class),
            minimum_history_bars=30,
        )
        for asset_class in ("EQUITY", "FIXED_INCOME")
    )
