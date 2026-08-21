"""Explicit US development calibrations for Ascending Triangle evidence."""

from __future__ import annotations

from .registry import CalibrationKey, DetectorParameterSet


US_ASCENDING_TRIANGLE_DEVELOPMENT_VERSION = "wp-us-ascending-triangle-development-v1"


def _values(asset_class: str) -> tuple[tuple[str, bool | int | float | str], ...]:
    is_fixed_income = asset_class == "FIXED_INCOME"
    return (
        ("boundary_tolerance_pct", 0.003 if is_fixed_income else 0.006),
        ("breakout_close_margin_pct", 0.10 if is_fixed_income else 0.20),
        ("calibration_stage", "development_only"),
        ("containment_tolerance_pct", 0.30 if is_fixed_income else 0.60),
        ("expiry_sessions", 30),
        ("horizontal_resistance_max_slope_pct_per_session", 0.0005),
        ("horizontal_to_support_max_slope_ratio", 0.50),
        ("invalidation_buffer_pct", 0.10 if is_fixed_income else 0.20),
        ("maximum_apex_horizon_sessions", 80),
        ("maximum_apex_progress_at_confirmation", 0.90),
        ("maximum_line_fit_error_pct", 0.006 if is_fixed_income else 0.010),
        ("maximum_resistance_zone_width_pct", 0.60 if is_fixed_income else 1.00),
        ("maximum_source_pivots", 8),
        ("minimum_apex_progress", 0.15),
        ("minimum_contraction_pct", 0.12),
        ("minimum_source_pivots", 4),
        ("minimum_structure_span_sessions", 6),
        ("minimum_touches_per_side", 2),
        ("parameter_origin", "wealthpilot_us_hypothesis_not_validated"),
        ("pivot_left_window_bars", 3),
        ("pivot_minimum_bar_separation", 2),
        ("pivot_minimum_price_separation_pct", 0.005 if is_fixed_income else 0.02),
        ("pivot_plateau_tolerance_pct", 0.001),
        ("pivot_right_confirmation_bars", 2),
        ("support_min_slope_pct_per_session", 0.00012),
    )


def build_us_ascending_triangle_development_parameter_sets() -> tuple[DetectorParameterSet, ...]:
    return tuple(
        DetectorParameterSet(
            key=CalibrationKey(
                market="US",
                economic_asset_class=asset_class,
                timeframe="1d",
                pattern_family="triangle",
                pattern_type="ascending_triangle",
                calibration_version=US_ASCENDING_TRIANGLE_DEVELOPMENT_VERSION,
            ),
            values=_values(asset_class),
            minimum_history_bars=40,
        )
        for asset_class in ("EQUITY", "FIXED_INCOME")
    )
