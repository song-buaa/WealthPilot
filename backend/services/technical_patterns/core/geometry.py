"""Pure geometry helpers defined on dense exchange-session ordinals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


GEOMETRY_VERSION = "session-ordinal-geometry-v1"


@dataclass(frozen=True)
class SessionPoint:
    session_ordinal: int
    price: float

    def __post_init__(self) -> None:
        if self.session_ordinal < 0 or self.price <= 0:
            raise ValueError("geometry points require non-negative sessions and positive prices")


@dataclass(frozen=True)
class LineFit:
    slope_per_session: float
    intercept: float
    max_error_pct: float
    source_session_ordinals: tuple[int, ...]


@dataclass(frozen=True)
class TwoLineGeometry:
    upper: LineFit
    lower: LineFit
    start_session_ordinal: int
    confirmed_session_ordinal: int
    start_gap: float
    confirmed_gap: float
    contraction_pct: float
    apex_session_offset: float | None


def session_span(start_ordinal: int, end_ordinal: int) -> int:
    if end_ordinal < start_ordinal:
        raise ValueError("session span cannot be negative")
    return end_ordinal - start_ordinal


def fit_line(points: Iterable[SessionPoint], *, base_price: float) -> LineFit:
    source = tuple(points)
    if len(source) < 2:
        raise ValueError("line fit requires at least two source points")
    if base_price <= 0:
        raise ValueError("line fit base_price must be positive")
    ordinals = tuple(item.session_ordinal for item in source)
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("line fit source sessions must be unique")
    mean_x = sum(ordinals) / len(source)
    mean_y = sum(item.price for item in source) / len(source)
    denominator = sum((item.session_ordinal - mean_x) ** 2 for item in source)
    if denominator <= 0:
        return LineFit(0.0, mean_y, 1.0, ordinals)
    slope = sum((item.session_ordinal - mean_x) * (item.price - mean_y) for item in source) / denominator
    intercept = mean_y - slope * mean_x
    error = max(abs(item.price - (slope * item.session_ordinal + intercept)) / base_price for item in source)
    return LineFit(slope, intercept, error, ordinals)


def line_price(line: LineFit, session_ordinal: float) -> float:
    return line.slope_per_session * session_ordinal + line.intercept


def build_two_line_geometry(
    upper_points: Iterable[SessionPoint],
    lower_points: Iterable[SessionPoint],
    *,
    base_price: float,
    start_session_ordinal: int,
    confirmed_session_ordinal: int,
) -> TwoLineGeometry:
    if confirmed_session_ordinal <= start_session_ordinal:
        raise ValueError("geometry confirmation must follow its start session")
    upper = fit_line(upper_points, base_price=base_price)
    lower = fit_line(lower_points, base_price=base_price)
    start_gap = line_price(upper, start_session_ordinal) - line_price(lower, start_session_ordinal)
    confirmed_gap = line_price(upper, confirmed_session_ordinal) - line_price(lower, confirmed_session_ordinal)
    contraction = (start_gap - confirmed_gap) / start_gap if start_gap > 0 else 0.0
    denominator = upper.slope_per_session - lower.slope_per_session
    apex = None if denominator == 0 else (lower.intercept - upper.intercept) / denominator
    return TwoLineGeometry(upper, lower, start_session_ordinal, confirmed_session_ordinal, start_gap, confirmed_gap, contraction, apex)
