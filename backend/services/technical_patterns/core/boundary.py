"""Confirmed-pivot Boundary Registry and Trend Context foundation."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .contracts import Boundary, PatternCoreInput, Pivot, TrendContext
from .identity import stable_hash, stable_id


BOUNDARY_REGISTRY_VERSION = "confirmed-pivot-boundary-v1-wp-session"
TREND_CONTEXT_VERSION = "confirmed-pivot-hhhl-v1-wp-session"


@dataclass(frozen=True)
class BoundaryParameters:
    tolerance_pct: float

    def __post_init__(self) -> None:
        if not 0 <= self.tolerance_pct < 1:
            raise ValueError("boundary tolerance must be in [0, 1)")


@dataclass(frozen=True)
class BoundaryTrendResult:
    boundaries: tuple[Boundary, ...]
    trend: TrendContext
    cache_key: str
    result_hash: str
    metrics: dict[str, int]


class BoundaryTrendEngine:
    def __init__(self, *, parameter_version: str, parameters: BoundaryParameters):
        if not parameter_version:
            raise ValueError("boundary parameter_version is required; BTC defaults are forbidden")
        self.parameter_version = parameter_version
        self.parameters = parameters

    def replay(
        self,
        core_input: PatternCoreInput,
        pivots: tuple[Pivot, ...],
        *,
        evaluation_session_ordinal: int,
    ) -> BoundaryTrendResult:
        evaluation_bar = self._evaluation_bar(core_input, evaluation_session_ordinal)
        usable = tuple(
            pivot for pivot in pivots
            if pivot.status == "confirmed" and pivot.available_from_ordinal <= evaluation_session_ordinal
        )
        self._validate_pivots(core_input, usable)

        boundaries: list[Boundary] = []
        for pivot in usable:
            role = "support" if pivot.pivot_type == "swing_low" else "resistance"
            active = [entry for entry in boundaries if entry.boundary_role == role and entry.status == "active"]
            merged = next((entry for entry in active if self._near(entry.price, pivot.price)), None)
            if merged is not None:
                boundaries[boundaries.index(merged)] = replace(
                    merged,
                    source_pivot_ids=merged.source_pivot_ids + (pivot.pivot_id,),
                    price_low=min(merged.price_low, pivot.price),
                    price_high=max(merged.price_high, pivot.price),
                    last_confirmed_at=pivot.confirmed_at,
                    available_from=max(merged.available_from, pivot.available_from),
                    available_from_ordinal=max(merged.available_from_ordinal, pivot.available_from_ordinal),
                    evaluation_session=evaluation_bar.session_date,
                    evaluation_session_ordinal=evaluation_session_ordinal,
                    touch_count=merged.touch_count + 1,
                    confirmed_touch_count=merged.confirmed_touch_count + 1,
                )
                continue

            boundary_id = stable_id(
                "bnd",
                {
                    "instrument_id": core_input.instrument_id,
                    "timeframe": core_input.timeframe,
                    "role": role,
                    "primary_pivot_id": pivot.pivot_id,
                    "registry_version": BOUNDARY_REGISTRY_VERSION,
                    "parameter_version": self.parameter_version,
                },
            )
            near_prior = next(
                (
                    entry for entry in active
                    if abs(entry.price - pivot.price) / max(abs(entry.price), abs(pivot.price), 1.0)
                    <= 2 * self.parameters.tolerance_pct
                ),
                None,
            )
            if near_prior is not None and self._more_extreme(role, pivot.price, near_prior.price):
                boundaries[boundaries.index(near_prior)] = replace(
                    near_prior,
                    status="superseded",
                    superseded_by=boundary_id,
                    evaluation_session=evaluation_bar.session_date,
                    evaluation_session_ordinal=evaluation_session_ordinal,
                )
            elif active and any(self._more_extreme(role, pivot.price, entry.price) for entry in active):
                for entry in active:
                    if self._more_extreme(role, pivot.price, entry.price):
                        boundaries[boundaries.index(entry)] = replace(
                            entry,
                            status="invalidated",
                            invalidation_reason="confirmed_same_role_extreme_beyond_merge_band",
                            evaluation_session=evaluation_bar.session_date,
                            evaluation_session_ordinal=evaluation_session_ordinal,
                        )
            boundaries.append(
                Boundary(
                    boundary_id=boundary_id,
                    instrument_id=core_input.instrument_id,
                    timeframe=core_input.timeframe,
                    dataset_version=core_input.dataset_version,
                    boundary_role=role,
                    source_pivot_ids=(pivot.pivot_id,),
                    primary_pivot_id=pivot.pivot_id,
                    price=pivot.price,
                    price_low=pivot.price,
                    price_high=pivot.price,
                    created_at=pivot.confirmed_at,
                    created_session_ordinal=pivot.confirmed_session_ordinal,
                    available_from=pivot.available_from,
                    available_from_ordinal=pivot.available_from_ordinal,
                    last_confirmed_at=pivot.confirmed_at,
                    evaluation_session=evaluation_bar.session_date,
                    evaluation_session_ordinal=evaluation_session_ordinal,
                    touch_count=1,
                    confirmed_touch_count=1,
                    status="active",
                )
            )

        highs = [pivot for pivot in usable if pivot.pivot_type == "swing_high"][-2:]
        lows = [pivot for pivot in usable if pivot.pivot_type == "swing_low"][-2:]
        if len(highs) == len(lows) == 2:
            if highs[-1].price > highs[0].price and lows[-1].price > lows[0].price:
                state, confidence, evidence = "bullish", "complete", ("higher_high", "higher_low")
            elif highs[-1].price < highs[0].price and lows[-1].price < lows[0].price:
                state, confidence, evidence = "bearish", "complete", ("lower_high", "lower_low")
            else:
                state, confidence, evidence = "neutral", "mixed", ("conflicting_confirmed_structure",)
        else:
            state, confidence, evidence = "neutral", "partial", ("insufficient_confirmed_pivot_pairs",)

        source = tuple(pivot.pivot_id for pivot in highs + lows)
        source_pivots = highs + lows
        available_from = max((pivot.available_from for pivot in source_pivots), default=None)
        available_ordinal = max((pivot.available_from_ordinal for pivot in source_pivots), default=None)
        trend_id = stable_id(
            "trd",
            {
                "instrument_id": core_input.instrument_id,
                "timeframe": core_input.timeframe,
                "evaluation_session": evaluation_bar.session_date,
                "source": source,
                "trend_version": TREND_CONTEXT_VERSION,
            },
        )
        trend = TrendContext(
            trend_context_id=trend_id,
            instrument_id=core_input.instrument_id,
            timeframe=core_input.timeframe,
            trend_state=state,
            source_pivot_ids=source,
            source_boundary_ids=tuple(entry.boundary_id for entry in boundaries if entry.status == "active"),
            structure_evidence=evidence,
            available_from=available_from,
            available_from_ordinal=available_ordinal,
            evaluation_session=evaluation_bar.session_date,
            evaluation_session_ordinal=evaluation_session_ordinal,
            confidence_class=confidence,
        )
        cache_material = {
            "instrument_id": core_input.instrument_id,
            "dataset_version": core_input.dataset_version,
            "timeframe": core_input.timeframe,
            "parameter_version": self.parameter_version,
            "boundary_version": BOUNDARY_REGISTRY_VERSION,
            "trend_version": TREND_CONTEXT_VERSION,
            "evaluation_session_ordinal": evaluation_session_ordinal,
            "pivots": tuple(pivot.pivot_id for pivot in usable),
        }
        cache_key = stable_hash(cache_material)
        result_hash = stable_hash({"boundaries": tuple(boundaries), "trend": trend, "cache_key": cache_key})
        metrics = {
            "future_boundary_violation_count": sum(entry.available_from_ordinal > evaluation_session_ordinal for entry in boundaries),
            "future_trend_violation_count": int(available_ordinal is not None and available_ordinal > evaluation_session_ordinal),
            "retroactive_registry_violation_count": sum(entry.created_session_ordinal > entry.available_from_ordinal for entry in boundaries),
            "boundary_stable_id_violation_count": len(boundaries) - len({entry.boundary_id for entry in boundaries}),
            "active_boundary_count": sum(entry.status == "active" for entry in boundaries),
            "superseded_boundary_count": sum(entry.status == "superseded" for entry in boundaries),
            "invalidated_boundary_count": sum(entry.status == "invalidated" for entry in boundaries),
        }
        return BoundaryTrendResult(tuple(boundaries), trend, cache_key, result_hash, metrics)

    def _near(self, left: float, right: float) -> bool:
        return abs(left - right) / max(abs(left), abs(right), 1.0) <= self.parameters.tolerance_pct

    @staticmethod
    def _more_extreme(role: str, candidate: float, existing: float) -> bool:
        return candidate < existing if role == "support" else candidate > existing

    @staticmethod
    def _evaluation_bar(core_input: PatternCoreInput, ordinal: int):
        try:
            return next(bar for bar in core_input.bars if bar.session_ordinal == ordinal)
        except StopIteration as exc:
            raise ValueError("evaluation session ordinal is outside the canonical series") from exc

    @staticmethod
    def _validate_pivots(core_input: PatternCoreInput, pivots: tuple[Pivot, ...]) -> None:
        if any(
            pivot.instrument_id != core_input.instrument_id
            or pivot.timeframe != core_input.timeframe
            or pivot.dataset_version != core_input.dataset_version
            for pivot in pivots
        ):
            raise ValueError("Boundary Registry requires one instrument/timeframe/dataset pivot stream")
        if any(right.available_from_ordinal < left.available_from_ordinal for left, right in zip(pivots, pivots[1:])):
            raise ValueError("confirmed pivots must be ordered by availability")
