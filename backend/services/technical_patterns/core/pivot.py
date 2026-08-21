"""Causal Windowed Reversal Pivot Engine adapted to exchange sessions.

Algorithm semantics originate from the frozen Tovest tpg-v1.10 PivotEngine,
while time, identity, and input contracts are WealthPilot-owned adaptations.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Literal

from .contracts import CorePatternBar, PatternCoreInput, Pivot, PivotType
from .identity import stable_hash, stable_id


PIVOT_ALGORITHM_VERSION = "windowed-reversal-pivot-v1-wp-session"


@dataclass(frozen=True)
class PivotParameters:
    left_window_bars: int
    right_confirmation_bars: int
    minimum_price_separation_pct: float
    minimum_bar_separation: int
    plateau_tolerance_pct: float = 0.0

    def __post_init__(self) -> None:
        if self.left_window_bars < 1 or self.right_confirmation_bars < 1:
            raise ValueError("pivot windows must be positive")
        if min(self.minimum_price_separation_pct, self.minimum_bar_separation, self.plateau_tolerance_pct) < 0:
            raise ValueError("pivot separation and tolerance must be non-negative")


@dataclass
class _ActiveCandidate:
    pivot_type: PivotType
    index: int
    candidate_at_ordinal: int
    price: float
    plateau_bar_ids: list[str]
    plateau_last_index: int
    replacement_count: int = 0


@dataclass(frozen=True)
class CandidatePivot:
    pivot_type: PivotType
    source_session_ordinal: int
    candidate_at_ordinal: int
    price: float
    plateau_bar_ids: tuple[str, ...]
    replacement_count: int


@dataclass(frozen=True)
class PivotTimelineEvent:
    event: Literal["candidate", "candidate_replaced", "candidate_discarded", "confirmed", "confirmed_superseded"]
    evaluation_session_ordinal: int
    pivot_type: PivotType
    source_session_ordinal: int
    price: float
    pivot_id: str | None = None
    related_pivot_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PivotReplayResult:
    confirmed: tuple[Pivot, ...]
    superseded: tuple[Pivot, ...]
    candidates: tuple[CandidatePivot, ...]
    timeline: tuple[PivotTimelineEvent, ...]
    result_hash: str
    metrics: dict[str, int]


def _equal(left: float, right: float, tolerance_pct: float) -> bool:
    return abs(left - right) <= max(abs(left), abs(right), 1.0) * tolerance_pct


def _more_extreme(kind: PivotType, candidate: float, prior: float) -> bool:
    return candidate > prior if kind == "swing_high" else candidate < prior


def _not_worse(kind: PivotType, candidate: float, prior: float, tolerance: float) -> bool:
    margin = max(abs(candidate), abs(prior), 1.0) * tolerance
    return candidate >= prior - margin if kind == "swing_high" else candidate <= prior + margin


class PivotEngine:
    def __init__(self, *, parameter_version: str, parameters: PivotParameters):
        if not parameter_version:
            raise ValueError("pivot parameter_version is required; BTC defaults are forbidden")
        self.parameter_version = parameter_version
        self.parameters = parameters

    def replay(
        self,
        core_input: PatternCoreInput,
        *,
        evaluation_session_ordinal: int | None = None,
    ) -> PivotReplayResult:
        usable = tuple(
            bar for bar in core_input.bars
            if evaluation_session_ordinal is None or bar.session_ordinal <= evaluation_session_ordinal
        )
        self._validate_bars(usable)

        history_high: deque[float] = deque(maxlen=self.parameters.left_window_bars)
        history_low: deque[float] = deque(maxlen=self.parameters.left_window_bars)
        active: dict[PivotType, _ActiveCandidate | None] = {"swing_high": None, "swing_low": None}
        candidates: list[CandidatePivot] = []
        confirmed: list[Pivot] = []
        superseded: list[Pivot] = []
        timeline: list[PivotTimelineEvent] = []
        replacement_count = 0
        supersession_count = 0

        for index, bar in enumerate(usable):
            values: tuple[tuple[PivotType, float, deque[float]], ...] = (
                ("swing_high", bar.high, history_high),
                ("swing_low", bar.low, history_low),
            )
            for kind, value, prior in values:
                prior_extreme = max(prior) if kind == "swing_high" and prior else min(prior) if prior else value
                if len(prior) != self.parameters.left_window_bars or not _not_worse(
                    kind, value, prior_extreme, self.parameters.plateau_tolerance_pct
                ):
                    continue
                existing = active[kind]
                if existing is None:
                    active[kind] = _ActiveCandidate(kind, index, bar.session_ordinal, value, [bar.bar_id], index)
                    timeline.append(PivotTimelineEvent("candidate", bar.session_ordinal, kind, bar.session_ordinal, value, reason="left_window_local_extreme"))
                elif _equal(value, existing.price, self.parameters.plateau_tolerance_pct):
                    existing.plateau_bar_ids.append(bar.bar_id)
                    existing.plateau_last_index = index
                elif _more_extreme(kind, value, existing.price):
                    timeline.append(PivotTimelineEvent("candidate_replaced", bar.session_ordinal, kind, usable[existing.index].session_ordinal, existing.price, reason="more_extreme_unconsumed_candidate"))
                    active[kind] = _ActiveCandidate(kind, index, bar.session_ordinal, value, [bar.bar_id], index, existing.replacement_count + 1)
                    replacement_count += 1

            for kind in ("swing_high", "swing_low"):
                candidate = active[kind]
                if candidate is None or index - candidate.plateau_last_index < self.parameters.right_confirmation_bars:
                    continue
                right = usable[candidate.plateau_last_index + 1 : index + 1]
                value = candidate.price
                if any(_more_extreme(kind, item.high if kind == "swing_high" else item.low, value) for item in right):
                    timeline.append(PivotTimelineEvent("candidate_discarded", bar.session_ordinal, kind, usable[candidate.index].session_ordinal, value, reason="right_confirmation_broken"))
                    active[kind] = None
                    continue

                source = usable[candidate.index]
                last = confirmed[-1] if confirmed else None
                if last is not None and last.pivot_type != kind:
                    separation = abs(value - last.price) / last.price
                    if separation < self.parameters.minimum_price_separation_pct or index - candidate.index < self.parameters.minimum_bar_separation:
                        timeline.append(PivotTimelineEvent("candidate_discarded", bar.session_ordinal, kind, source.session_ordinal, value, reason="minimum_separation"))
                        active[kind] = None
                        continue

                identity = {
                    "instrument_id": core_input.instrument_id,
                    "timeframe": core_input.timeframe,
                    "dataset_version": core_input.dataset_version,
                    "pivot_type": kind,
                    "source_session": source.session_date,
                    "source_price": value,
                    "algorithm_version": PIVOT_ALGORITHM_VERSION,
                    "parameter_version": self.parameter_version,
                    "plateau_source_bar_ids": tuple(candidate.plateau_bar_ids),
                }
                pivot_id = stable_id("pvt", identity)
                pivot = Pivot(
                    pivot_id=pivot_id,
                    instrument_id=core_input.instrument_id,
                    timeframe=core_input.timeframe,
                    dataset_version=core_input.dataset_version,
                    pivot_type=kind,
                    price=value,
                    source_session=source.session_date,
                    source_session_ordinal=source.session_ordinal,
                    confirmed_at=bar.available_from,
                    confirmed_session_ordinal=bar.session_ordinal,
                    available_from=bar.available_from,
                    available_from_ordinal=bar.session_ordinal,
                    confirmation_bars=index - candidate.index,
                    status="confirmed",
                    algorithm_version=PIVOT_ALGORITHM_VERSION,
                    parameter_version=self.parameter_version,
                    source_bar_ids=tuple(candidate.plateau_bar_ids),
                )
                if last is not None and last.pivot_type == kind:
                    if _more_extreme(kind, pivot.price, last.price):
                        old = replace(last, status="superseded", superseded_by_pivot_id=pivot.pivot_id)
                        confirmed[-1] = old
                        superseded.append(old)
                        supersession_count += 1
                        timeline.append(PivotTimelineEvent("confirmed_superseded", bar.session_ordinal, kind, old.source_session_ordinal, old.price, old.pivot_id, pivot.pivot_id, "later_more_extreme_confirmed"))
                    else:
                        timeline.append(PivotTimelineEvent("candidate_discarded", bar.session_ordinal, kind, source.session_ordinal, value, reason="less_extreme_same_type"))
                        active[kind] = None
                        continue
                confirmed.append(pivot)
                candidates.append(CandidatePivot(kind, source.session_ordinal, candidate.candidate_at_ordinal, value, tuple(candidate.plateau_bar_ids), candidate.replacement_count))
                timeline.append(PivotTimelineEvent("confirmed", bar.session_ordinal, kind, source.session_ordinal, value, pivot.pivot_id, reason="right_confirmation_closed"))
                active[kind] = None

            history_high.append(bar.high)
            history_low.append(bar.low)

        live = tuple(pivot for pivot in confirmed if pivot.status == "confirmed")
        effective_ordinal = evaluation_session_ordinal if evaluation_session_ordinal is not None else (usable[-1].session_ordinal if usable else -1)
        material = {
            "engine": PIVOT_ALGORITHM_VERSION,
            "parameter_version": self.parameter_version,
            "instrument_id": core_input.instrument_id,
            "dataset_version": core_input.dataset_version,
            "confirmed": tuple(confirmed),
            "timeline": tuple(timeline),
        }
        metrics = {
            "input_bar_count": len(usable),
            "candidate_pivot_count": len(candidates),
            "confirmed_pivot_count": len(live),
            "superseded_pivot_count": len(superseded),
            "candidate_replacement_count": replacement_count,
            "confirmed_supersession_count": supersession_count,
            "alternation_violation_count": sum(1 for left, right in zip(live, live[1:]) if left.pivot_type == right.pivot_type),
            "duplicate_pivot_id_count": len(live) - len({pivot.pivot_id for pivot in live}),
            "future_pivot_violation_count": sum(pivot.available_from_ordinal > effective_ordinal for pivot in live),
            "future_source_bar_violation_count": sum(pivot.source_session_ordinal >= pivot.confirmed_session_ordinal for pivot in live),
            "retroactive_availability_violation_count": sum(pivot.confirmed_session_ordinal > pivot.available_from_ordinal for pivot in live),
            "plateau_group_count": sum(len(candidate.plateau_bar_ids) > 1 for candidate in candidates),
        }
        return PivotReplayResult(live, tuple(superseded), tuple(candidates), tuple(timeline), stable_hash(material), metrics)

    @staticmethod
    def _validate_bars(bars: tuple[CorePatternBar, ...]) -> None:
        for left, right in zip(bars, bars[1:]):
            if right.session_ordinal != left.session_ordinal + 1 or right.session_date <= left.session_date:
                raise ValueError("Pivot bars must be dense, strictly ordered exchange sessions")
