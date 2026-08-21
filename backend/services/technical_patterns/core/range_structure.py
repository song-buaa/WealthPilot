"""Deterministic Range Structure built only from available boundaries."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from .contracts import Boundary, PatternCoreInput, TrendContext
from .identity import stable_hash, stable_id


RANGE_STRUCTURE_VERSION = "confirmed-boundary-range-v1-wp-session"


@dataclass(frozen=True)
class RangeStructure:
    range_id: str
    instrument_id: str
    timeframe: Literal["1d"]
    dataset_version: str
    support_boundary_id: str
    resistance_boundary_id: str
    support_price_low: float
    support_price_high: float
    resistance_price_low: float
    resistance_price_high: float
    range_low: float
    range_high: float
    range_width: float
    range_width_pct: float
    source_pivot_ids: tuple[str, ...]
    source_boundary_ids: tuple[str, str]
    support_touch_count: int
    resistance_touch_count: int
    trend_context_id: str
    trend_state: Literal["bullish", "bearish", "neutral"]
    status: Literal["active", "superseded", "invalidated"]
    created_session_ordinal: int
    available_from_ordinal: int
    evaluation_session_ordinal: int
    superseded_by: str | None = None
    invalidation_reason: str | None = None


@dataclass(frozen=True)
class RangeSnapshot:
    boundaries: tuple[Boundary, ...]
    trend: TrendContext
    evaluation_session_ordinal: int


@dataclass(frozen=True)
class RangeResult:
    ranges: tuple[RangeStructure, ...]
    cache_key: str
    result_hash: str
    metrics: dict[str, int]


class RangeStructureEngine:
    def replay(self, core_input: PatternCoreInput, snapshots: tuple[RangeSnapshot, ...]) -> RangeResult:
        if any(right.evaluation_session_ordinal < left.evaluation_session_ordinal for left, right in zip(snapshots, snapshots[1:])):
            raise ValueError("Range snapshots must be ordered by evaluation session")
        ranges: list[RangeStructure] = []
        for snapshot in snapshots:
            self._validate_snapshot(core_input, snapshot)
            candidate = self._candidate(snapshot)
            for previous in [entry for entry in ranges if entry.status == "active"]:
                current_boundaries = {entry.boundary_id: entry for entry in snapshot.boundaries}
                source_statuses = tuple(
                    current_boundaries[boundary_id].status if boundary_id in current_boundaries else "invalidated"
                    for boundary_id in previous.source_boundary_ids
                )
                if "invalidated" in source_statuses:
                    ranges[ranges.index(previous)] = replace(
                        previous,
                        status="invalidated",
                        invalidation_reason="required_boundary_invalidated",
                        evaluation_session_ordinal=snapshot.evaluation_session_ordinal,
                    )
                elif candidate is not None and set(previous.source_boundary_ids) != {candidate[0].boundary_id, candidate[1].boundary_id}:
                    next_id = self._materialize(core_input, *candidate, snapshot.trend, snapshot.evaluation_session_ordinal).range_id
                    ranges[ranges.index(previous)] = replace(
                        previous,
                        status="superseded",
                        superseded_by=next_id,
                        evaluation_session_ordinal=snapshot.evaluation_session_ordinal,
                    )
                elif candidate is None:
                    ranges[ranges.index(previous)] = replace(
                        previous,
                        status="invalidated",
                        invalidation_reason="no_legal_active_boundary_pair",
                        evaluation_session_ordinal=snapshot.evaluation_session_ordinal,
                    )
            if candidate is not None:
                materialized = self._materialize(core_input, *candidate, snapshot.trend, snapshot.evaluation_session_ordinal)
                existing = next((entry for entry in ranges if entry.range_id == materialized.range_id), None)
                if existing is None:
                    ranges.append(materialized)
                elif existing.status == "active":
                    ranges[ranges.index(existing)] = replace(
                        existing,
                        trend_context_id=materialized.trend_context_id,
                        trend_state=materialized.trend_state,
                        evaluation_session_ordinal=snapshot.evaluation_session_ordinal,
                    )

        cache_material = {
            "instrument_id": core_input.instrument_id,
            "dataset_version": core_input.dataset_version,
            "range_version": RANGE_STRUCTURE_VERSION,
            "snapshots": tuple(
                {
                    "evaluation_session_ordinal": item.evaluation_session_ordinal,
                    "boundaries": tuple(entry.boundary_id for entry in item.boundaries),
                    "trend": item.trend.trend_context_id,
                }
                for item in snapshots
            ),
        }
        cache_key = stable_hash(cache_material)
        result_hash = stable_hash({"ranges": tuple(ranges), "cache_key": cache_key})
        metrics = {
            "future_range_violation_count": sum(entry.available_from_ordinal > entry.evaluation_session_ordinal for entry in ranges),
            "retroactive_range_violation_count": sum(entry.created_session_ordinal > entry.available_from_ordinal for entry in ranges),
            "range_stable_id_violation_count": len(ranges) - len({entry.range_id for entry in ranges}),
            "range_count": len(ranges),
            "active_range_count": sum(entry.status == "active" for entry in ranges),
            "superseded_range_count": sum(entry.status == "superseded" for entry in ranges),
            "invalidated_range_count": sum(entry.status == "invalidated" for entry in ranges),
            "ranges_per_evaluation_max": 1,
        }
        return RangeResult(tuple(ranges), cache_key, result_hash, metrics)

    @staticmethod
    def _candidate(snapshot: RangeSnapshot) -> tuple[Boundary, Boundary] | None:
        usable = tuple(
            entry for entry in snapshot.boundaries
            if entry.status == "active" and entry.available_from_ordinal <= snapshot.evaluation_session_ordinal
        )
        supports = sorted((entry for entry in usable if entry.boundary_role == "support"), key=lambda item: item.price_high, reverse=True)
        resistances = sorted((entry for entry in usable if entry.boundary_role == "resistance"), key=lambda item: item.price_low)
        for support in supports:
            for resistance in resistances:
                if support.price_high < resistance.price_low:
                    return support, resistance
        return None

    @staticmethod
    def _materialize(
        core_input: PatternCoreInput,
        support: Boundary,
        resistance: Boundary,
        trend: TrendContext,
        evaluation_session_ordinal: int,
    ) -> RangeStructure:
        range_id = stable_id(
            "rng",
            {
                "instrument_id": core_input.instrument_id,
                "timeframe": core_input.timeframe,
                "support_boundary_id": support.boundary_id,
                "resistance_boundary_id": resistance.boundary_id,
                "range_version": RANGE_STRUCTURE_VERSION,
            },
        )
        low, high = support.price_high, resistance.price_low
        return RangeStructure(
            range_id=range_id,
            instrument_id=core_input.instrument_id,
            timeframe=core_input.timeframe,
            dataset_version=core_input.dataset_version,
            support_boundary_id=support.boundary_id,
            resistance_boundary_id=resistance.boundary_id,
            support_price_low=support.price_low,
            support_price_high=support.price_high,
            resistance_price_low=resistance.price_low,
            resistance_price_high=resistance.price_high,
            range_low=low,
            range_high=high,
            range_width=high - low,
            range_width_pct=(high - low) / low,
            source_pivot_ids=tuple(sorted(set(support.source_pivot_ids + resistance.source_pivot_ids))),
            source_boundary_ids=(support.boundary_id, resistance.boundary_id),
            support_touch_count=support.confirmed_touch_count,
            resistance_touch_count=resistance.confirmed_touch_count,
            trend_context_id=trend.trend_context_id,
            trend_state=trend.trend_state,
            status="active",
            created_session_ordinal=max(support.created_session_ordinal, resistance.created_session_ordinal),
            available_from_ordinal=max(support.available_from_ordinal, resistance.available_from_ordinal),
            evaluation_session_ordinal=evaluation_session_ordinal,
        )

    @staticmethod
    def _validate_snapshot(core_input: PatternCoreInput, snapshot: RangeSnapshot) -> None:
        if snapshot.trend.instrument_id != core_input.instrument_id or snapshot.trend.timeframe != core_input.timeframe:
            raise ValueError("Range Structure requires matching Trend Context")
        if any(
            entry.instrument_id != core_input.instrument_id
            or entry.timeframe != core_input.timeframe
            or entry.dataset_version != core_input.dataset_version
            for entry in snapshot.boundaries
        ):
            raise ValueError("Range Structure requires one instrument/timeframe/dataset boundary stream")
