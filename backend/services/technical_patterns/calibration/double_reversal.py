"""Explicit US development calibrations for Double Top / Double Bottom evidence."""

from __future__ import annotations

from .registry import CalibrationKey, DetectorParameterSet


US_DOUBLE_REVERSAL_DEVELOPMENT_VERSION = "wp-us-double-reversal-development-v1"


def _values(asset_class: str, pattern_type: str) -> tuple[tuple[str, bool | int | float | str], ...]:
    is_fixed_income = asset_class == "FIXED_INCOME"
    return (
        ("boundary_tolerance_pct", 0.003 if is_fixed_income else 0.006),
        ("bottom_volume_ratio_minimum", 1.20),
        ("calibration_stage", "development_only"),
        ("direction_break_margin_pct", 0.10 if is_fixed_income else 0.20),
        ("expiry_sessions", 80),
        ("extreme_similarity_max_ratio", 0.015 if is_fixed_income else 0.025),
        ("invalidation_buffer_pct", 0.10 if is_fixed_income else 0.20),
        ("maximum_structure_duration_sessions", 120),
        ("minimum_extreme_separation_sessions", 8),
        ("minimum_intervening_reaction_ratio", 0.010 if is_fixed_income else 0.015),
        ("minimum_preceding_trend_ratio", 0.010 if is_fixed_income else 0.020),
        ("neckline_tolerance_pct", 0.10 if is_fixed_income else 0.25),
        ("parameter_origin", "wealthpilot_us_hypothesis_not_validated"),
        ("pattern_type_contract", pattern_type),
        ("pivot_left_window_bars", 3),
        ("pivot_minimum_bar_separation", 2),
        ("pivot_minimum_price_separation_pct", 0.005 if is_fixed_income else 0.02),
        ("pivot_plateau_tolerance_pct", 0.001),
        ("pivot_right_confirmation_bars", 2),
        ("source_pivot_count", 4),
        ("volume_average_sessions", 20),
    )


def build_us_double_reversal_development_parameter_sets() -> tuple[DetectorParameterSet, ...]:
    return tuple(
        DetectorParameterSet(
            key=CalibrationKey(
                market="US",
                economic_asset_class=asset_class,
                timeframe="1d",
                pattern_family="reversal",
                pattern_type=pattern_type,
                calibration_version=US_DOUBLE_REVERSAL_DEVELOPMENT_VERSION,
            ),
            values=_values(asset_class, pattern_type),
            minimum_history_bars=40,
        )
        for pattern_type in ("double_top", "double_bottom")
        for asset_class in ("EQUITY", "FIXED_INCOME")
    )
