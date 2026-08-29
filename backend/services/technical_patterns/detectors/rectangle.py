"""Neutral Rectangle structure detector over confirmed Pattern Core facts."""

from __future__ import annotations

from dataclasses import replace

from ..calibration import CalibrationNotConfigured, DetectorParameterSet
from ..core import (
    BoundaryParameters,
    BoundaryTrendEngine,
    PatternCoreInput,
    PivotEngine,
    PivotParameters,
    RangeStructureEngine,
)
from ..core.identity import stable_hash
from ..core.range_structure import RangeSnapshot, RangeStructure
from ..indicators import IndicatorDefinition, IndicatorSeries
from .contracts import (
    CandidateProposal,
    ConfirmationAssessment,
    ConfirmationState,
    ConfirmationType,
    DetectorDescriptor,
    EvidenceFact,
    InvalidationAssessment,
    PatternCandidate,
    PatternDirection,
    PatternFamily,
    PatternType,
    SourceFactReference,
    SourceFactType,
)
from .framework import DetectorContext


RECTANGLE_DETECTOR_VERSION = "wp-rectangle-detector-v1"

_PARAMETER_NAMES = (
    "boundary_tolerance_pct",
    "expiry_sessions",
    "invalidation_buffer_pct",
    "maximum_boundary_zone_width_pct",
    "maximum_range_width_pct",
    "minimum_range_width_pct",
    "minimum_structure_span_sessions",
    "minimum_touches_per_side",
    "pivot_left_window_bars",
    "pivot_minimum_bar_separation",
    "pivot_minimum_price_separation_pct",
    "pivot_plateau_tolerance_pct",
    "pivot_right_confirmation_bars",
)


def _number(parameters: DetectorParameterSet, name: str, *, minimum: float = 0.0) -> float:
    value = parameters.require(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < minimum:
        raise CalibrationNotConfigured(f"{name!r} must be an explicit number >= {minimum}")
    return float(value)


def _integer(parameters: DetectorParameterSet, name: str, *, minimum: int = 0) -> int:
    value = parameters.require(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CalibrationNotConfigured(f"{name!r} must be an explicit integer >= {minimum}")
    return value


def _validate_parameters(parameters: DetectorParameterSet) -> None:
    for name in _PARAMETER_NAMES:
        parameters.require(name)
    left = _integer(parameters, "pivot_left_window_bars", minimum=1)
    right = _integer(parameters, "pivot_right_confirmation_bars", minimum=1)
    touches = _integer(parameters, "minimum_touches_per_side", minimum=2)
    span = _integer(parameters, "minimum_structure_span_sessions", minimum=1)
    _integer(parameters, "pivot_minimum_bar_separation")
    _integer(parameters, "expiry_sessions", minimum=1)
    for name in (
        "boundary_tolerance_pct",
        "invalidation_buffer_pct",
        "maximum_boundary_zone_width_pct",
        "maximum_range_width_pct",
        "minimum_range_width_pct",
        "pivot_minimum_price_separation_pct",
        "pivot_plateau_tolerance_pct",
    ):
        _number(parameters, name)
    minimum_width = _number(parameters, "minimum_range_width_pct")
    maximum_width = _number(parameters, "maximum_range_width_pct")
    if minimum_width >= maximum_width:
        raise CalibrationNotConfigured("minimum_range_width_pct must be below maximum_range_width_pct")
    causal_minimum = left + right + (touches * 2 - 1) * max(span // (touches * 2 - 1), 1) + 1
    if parameters.minimum_history_bars < causal_minimum:
        raise CalibrationNotConfigured(
            "minimum_history_bars does not cover pivot confirmation and Rectangle touch requirements"
        )


def _prefix(core_input: PatternCoreInput, evaluation_ordinal: int) -> PatternCoreInput:
    bars = tuple(bar for bar in core_input.bars if bar.session_ordinal <= evaluation_ordinal)
    source_hash = stable_hash(
        {
            "instrument_id": core_input.instrument_id,
            "timeframe": core_input.timeframe,
            "bars": bars,
        }
    )
    return replace(
        core_input,
        bars=bars,
        last_closed_session=bars[-1].session_date,
        source_bar_hash=source_hash,
        dataset_version=source_hash,
    )


def _fact(candidate: PatternCandidate, code: str) -> EvidenceFact:
    for fact in candidate.geometry_facts + candidate.structure_facts:
        if fact.code == code:
            return fact
    raise ValueError(f"candidate {candidate.candidate_id} has no evidence fact {code!r}")


class RectangleDetector:
    """Discover the first qualifying episode for each confirmed boundary pair."""

    descriptor = DetectorDescriptor(
        PatternFamily.RANGE,
        PatternType.RECTANGLE,
        PatternDirection.NEUTRAL,
        RECTANGLE_DETECTOR_VERSION,
    )

    def required_indicators(
        self, parameters: DetectorParameterSet
    ) -> tuple[IndicatorDefinition, ...]:
        _validate_parameters(parameters)
        return ()

    def discover(
        self,
        context: DetectorContext,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> tuple[CandidateProposal, ...]:
        _validate_parameters(parameters)
        proposals: list[CandidateProposal] = []
        seen_episode_keys: set[str] = set()
        start = parameters.minimum_history_bars - 1
        for evaluation_ordinal in range(start, context.evaluation_session_ordinal + 1):
            prefix = _prefix(context.core_input, evaluation_ordinal)
            pivot_result = self._pivot_engine(parameters).replay(
                prefix,
                evaluation_session_ordinal=evaluation_ordinal,
            )
            boundary_result = self._boundary_engine(parameters).replay(
                prefix,
                pivot_result.confirmed,
                evaluation_session_ordinal=evaluation_ordinal,
            )
            range_result = RangeStructureEngine().replay(
                prefix,
                (
                    RangeSnapshot(
                        boundary_result.boundaries,
                        boundary_result.trend,
                        evaluation_ordinal,
                    ),
                ),
            )
            active = next((item for item in range_result.ranges if item.status == "active"), None)
            if active is None:
                continue
            active_boundaries = {item.boundary_id: item for item in boundary_result.boundaries}
            try:
                support = active_boundaries[active.support_boundary_id]
                resistance = active_boundaries[active.resistance_boundary_id]
            except KeyError:
                continue
            episode_key = stable_hash(
                {
                    "instrument_id": prefix.instrument_id,
                    "timeframe": prefix.timeframe,
                    "support_primary_price": support.price,
                    "support_created_session_ordinal": support.created_session_ordinal,
                    "resistance_primary_price": resistance.price,
                    "resistance_created_session_ordinal": resistance.created_session_ordinal,
                    "detector_version": RECTANGLE_DETECTOR_VERSION,
                    "parameter_set_id": parameters.parameter_set_id,
                }
            )
            if episode_key in seen_episode_keys:
                continue
            proposal = self._qualify(
                prefix,
                parameters,
                active,
                pivot_result.confirmed,
                boundary_result.boundaries,
            )
            if proposal is not None:
                proposals.append(proposal)
                seen_episode_keys.add(episode_key)
        return tuple(proposals)

    @staticmethod
    def _pivot_engine(parameters: DetectorParameterSet) -> PivotEngine:
        return PivotEngine(
            parameter_version=parameters.parameter_set_id,
            parameters=PivotParameters(
                left_window_bars=_integer(parameters, "pivot_left_window_bars", minimum=1),
                right_confirmation_bars=_integer(parameters, "pivot_right_confirmation_bars", minimum=1),
                minimum_price_separation_pct=_number(parameters, "pivot_minimum_price_separation_pct"),
                minimum_bar_separation=_integer(parameters, "pivot_minimum_bar_separation"),
                plateau_tolerance_pct=_number(parameters, "pivot_plateau_tolerance_pct"),
            ),
        )

    @staticmethod
    def _boundary_engine(parameters: DetectorParameterSet) -> BoundaryTrendEngine:
        return BoundaryTrendEngine(
            parameter_version=parameters.parameter_set_id,
            parameters=BoundaryParameters(_number(parameters, "boundary_tolerance_pct")),
        )

    @staticmethod
    def _qualify(
        core_input: PatternCoreInput,
        parameters: DetectorParameterSet,
        current_range: RangeStructure,
        pivots,
        boundaries,
    ) -> CandidateProposal | None:
        by_boundary_id = {item.boundary_id: item for item in boundaries}
        try:
            support = by_boundary_id[current_range.support_boundary_id]
            resistance = by_boundary_id[current_range.resistance_boundary_id]
        except KeyError:
            return None
        if (
            support.status != "active"
            or resistance.status != "active"
            or support.available_from_ordinal > current_range.evaluation_session_ordinal
            or resistance.available_from_ordinal > current_range.evaluation_session_ordinal
        ):
            return None

        source_ids = set(support.source_pivot_ids + resistance.source_pivot_ids)
        touches = tuple(
            sorted(
                (pivot for pivot in pivots if pivot.pivot_id in source_ids),
                key=lambda pivot: (pivot.source_session_ordinal, pivot.available_from_ordinal, pivot.pivot_id),
            )
        )
        if not touches:
            return None
        support_ids = set(support.source_pivot_ids)
        resistance_ids = set(resistance.source_pivot_ids)
        sequence = tuple("S" if pivot.pivot_id in support_ids else "R" for pivot in touches)
        minimum_touches = _integer(parameters, "minimum_touches_per_side", minimum=2)
        alternating = all(left != right for left, right in zip(sequence, sequence[1:]))
        touch_basis_ok = (
            sequence.count("S") >= minimum_touches
            and sequence.count("R") >= minimum_touches
            and len(sequence) >= minimum_touches * 2
        )
        structure_span = touches[-1].source_session_ordinal - touches[0].source_session_ordinal
        duration_ok = structure_span >= _integer(parameters, "minimum_structure_span_sessions", minimum=1)

        first_source = touches[0].source_session_ordinal
        last_source = touches[-1].source_session_ordinal
        relevant = tuple(
            pivot
            for pivot in pivots
            if first_source <= pivot.source_session_ordinal <= last_source
        )
        containment_ok = all(
            support.price_low <= pivot.price <= resistance.price_high
            for pivot in relevant
        )
        support_width_pct = (support.price_high - support.price_low) / max(abs(support.price), 1.0) * 100.0
        resistance_width_pct = (
            (resistance.price_high - resistance.price_low)
            / max(abs(resistance.price), 1.0)
            * 100.0
        )
        maximum_boundary_width = _number(parameters, "maximum_boundary_zone_width_pct")
        boundaries_stable = (
            support_width_pct <= maximum_boundary_width
            and resistance_width_pct <= maximum_boundary_width
        )
        range_width_pct = current_range.range_width_pct * 100.0
        width_ok = (
            _number(parameters, "minimum_range_width_pct")
            <= range_width_pct
            <= _number(parameters, "maximum_range_width_pct")
        )
        geometry_ok = support.price_high < resistance.price_low
        if not all((alternating, touch_basis_ok, duration_ok, containment_ok, boundaries_stable, width_ok, geometry_ok)):
            return None

        available_ordinal = max(
            current_range.available_from_ordinal,
            support.available_from_ordinal,
            resistance.available_from_ordinal,
            *(pivot.available_from_ordinal for pivot in relevant),
        )
        by_ordinal = {bar.session_ordinal: bar for bar in core_input.bars}
        available_bar = by_ordinal[available_ordinal]
        support_ref = SourceFactReference(
            SourceFactType.BOUNDARY,
            support.boundary_id,
            support.available_from,
            support.available_from_ordinal,
        )
        resistance_ref = SourceFactReference(
            SourceFactType.BOUNDARY,
            resistance.boundary_id,
            resistance.available_from,
            resistance.available_from_ordinal,
        )
        pivot_refs = tuple(
            SourceFactReference(
                SourceFactType.PIVOT,
                pivot.pivot_id,
                pivot.available_from,
                pivot.available_from_ordinal,
            )
            for pivot in relevant
        )
        lineage = (current_range.range_id, support.boundary_id, resistance.boundary_id) + tuple(
            pivot.pivot_id for pivot in relevant
        )
        geometry = (
            EvidenceFact("range_id", current_range.range_id, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("range_low", current_range.range_low, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("range_high", current_range.range_high, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("range_width", current_range.range_width, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("range_width_pct", range_width_pct, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("support_zone_low", support.price_low, support.available_from, support.available_from_ordinal, (support.boundary_id,)),
            EvidenceFact("support_zone_high", support.price_high, support.available_from, support.available_from_ordinal, (support.boundary_id,)),
            EvidenceFact("resistance_zone_low", resistance.price_low, resistance.available_from, resistance.available_from_ordinal, (resistance.boundary_id,)),
            EvidenceFact("resistance_zone_high", resistance.price_high, resistance.available_from, resistance.available_from_ordinal, (resistance.boundary_id,)),
            EvidenceFact("support_touch_count", sequence.count("S"), available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("resistance_touch_count", sequence.count("R"), available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("touch_sequence", "".join(sequence), available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("structure_span_sessions", structure_span, available_bar.session_date, available_ordinal, lineage),
        )
        structure = (
            EvidenceFact("rectangle_structure_confirmed", True, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("alternating_touches", alternating, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("containment_confirmed", containment_ok, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("boundary_stability_confirmed", boundaries_stable, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("range_width_confirmed", width_ok, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("direction_bias", "neutral", available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("invalidation_lower_boundary", support.price_low, available_bar.session_date, available_ordinal, (support.boundary_id,)),
            EvidenceFact("invalidation_upper_boundary", resistance.price_high, available_bar.session_date, available_ordinal, (resistance.boundary_id,)),
        )
        return CandidateProposal(
            formed_session_ordinal=touches[0].source_session_ordinal,
            available_from_session_ordinal=available_ordinal,
            source_pivots=pivot_refs,
            source_boundaries=(support_ref, resistance_ref),
            geometry_facts=geometry,
            structure_facts=structure,
            direction_confirmation_required=False,
            expires_at_session_ordinal=available_ordinal + _integer(parameters, "expiry_sessions", minimum=1),
            identity_anchors=tuple(
                f"{pivot.pivot_type}:{bar_id}"
                for pivot in relevant
                for bar_id in pivot.source_bar_ids
            ),
        )


class RectangleStructureConfirmation:
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> ConfirmationAssessment:
        fact = _fact(candidate, "rectangle_structure_confirmed")
        state = ConfirmationState.CONFIRMED if fact.value is True else ConfirmationState.REJECTED
        return ConfirmationAssessment(
            candidate_id=candidate.candidate_id,
            confirmation_type=ConfirmationType.STRUCTURE,
            state=state,
            reason="confirmed_available_range_structure_exists" if fact.value is True else "range_structure_not_confirmed",
            observed_on=candidate.available_from,
            observed_session_ordinal=candidate.available_from_session_ordinal,
            facts=(fact,),
        )


class RectangleInvalidation:
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> InvalidationAssessment:
        lower = float(_fact(candidate, "invalidation_lower_boundary").value)
        upper = float(_fact(candidate, "invalidation_upper_boundary").value)
        buffer_pct = _number(parameters, "invalidation_buffer_pct")
        lower_threshold = lower * (1.0 - buffer_pct / 100.0)
        upper_threshold = upper * (1.0 + buffer_pct / 100.0)
        condition = "closed_session_close_breaks_confirmed_rectangle_boundary"
        for bar in context.core_input.bars:
            if bar.session_ordinal <= candidate.available_from_session_ordinal:
                continue
            if bar.close < lower_threshold or bar.close > upper_threshold:
                direction = "below_support" if bar.close < lower_threshold else "above_resistance"
                fact = EvidenceFact(
                    "rectangle_boundary_break",
                    direction,
                    bar.session_date,
                    bar.session_ordinal,
                    (bar.bar_id,) + tuple(item.source_id for item in candidate.source_boundaries),
                )
                return InvalidationAssessment(
                    candidate_id=candidate.candidate_id,
                    condition=condition,
                    invalidated=True,
                    reason=f"closed_session_{direction}",
                    observed_on=bar.session_date,
                    observed_session_ordinal=bar.session_ordinal,
                    facts=(fact,),
                )
        return InvalidationAssessment(candidate.candidate_id, condition, False)
