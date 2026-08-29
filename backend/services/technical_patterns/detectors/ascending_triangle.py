"""Ascending Triangle geometry over confirmed, available Pattern Core facts."""

from __future__ import annotations

from dataclasses import replace

from ..calibration import CalibrationNotConfigured, DetectorParameterSet
from ..core import BoundaryParameters, BoundaryTrendEngine, PatternCoreInput, PivotEngine, PivotParameters
from ..core.contracts import Boundary, Pivot
from ..core.geometry import SessionPoint, TwoLineGeometry, build_two_line_geometry, line_price
from ..core.identity import stable_hash
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


ASCENDING_TRIANGLE_DETECTOR_VERSION = "wp-ascending-triangle-detector-v1"

_PARAMETER_NAMES = (
    "boundary_tolerance_pct",
    "breakout_close_margin_pct",
    "containment_tolerance_pct",
    "expiry_sessions",
    "horizontal_resistance_max_slope_pct_per_session",
    "horizontal_to_support_max_slope_ratio",
    "invalidation_buffer_pct",
    "maximum_apex_horizon_sessions",
    "maximum_apex_progress_at_confirmation",
    "maximum_line_fit_error_pct",
    "maximum_resistance_zone_width_pct",
    "maximum_source_pivots",
    "minimum_apex_progress",
    "minimum_contraction_pct",
    "minimum_source_pivots",
    "minimum_structure_span_sessions",
    "minimum_touches_per_side",
    "pivot_left_window_bars",
    "pivot_minimum_bar_separation",
    "pivot_minimum_price_separation_pct",
    "pivot_plateau_tolerance_pct",
    "pivot_right_confirmation_bars",
    "support_min_slope_pct_per_session",
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
    minimum_source = _integer(parameters, "minimum_source_pivots", minimum=4)
    maximum_source = _integer(parameters, "maximum_source_pivots", minimum=minimum_source)
    touches = _integer(parameters, "minimum_touches_per_side", minimum=2)
    _integer(parameters, "minimum_structure_span_sessions", minimum=1)
    _integer(parameters, "pivot_minimum_bar_separation")
    _integer(parameters, "expiry_sessions", minimum=1)
    _integer(parameters, "maximum_apex_horizon_sessions", minimum=1)
    for name in (
        "boundary_tolerance_pct",
        "breakout_close_margin_pct",
        "containment_tolerance_pct",
        "horizontal_resistance_max_slope_pct_per_session",
        "horizontal_to_support_max_slope_ratio",
        "invalidation_buffer_pct",
        "maximum_apex_progress_at_confirmation",
        "maximum_line_fit_error_pct",
        "maximum_resistance_zone_width_pct",
        "minimum_apex_progress",
        "minimum_contraction_pct",
        "pivot_minimum_price_separation_pct",
        "pivot_plateau_tolerance_pct",
        "support_min_slope_pct_per_session",
    ):
        _number(parameters, name)
    if _number(parameters, "minimum_apex_progress") >= _number(
        parameters, "maximum_apex_progress_at_confirmation"
    ):
        raise CalibrationNotConfigured("minimum_apex_progress must be below maximum_apex_progress_at_confirmation")
    if _number(parameters, "horizontal_to_support_max_slope_ratio") > 1.0:
        raise CalibrationNotConfigured("horizontal_to_support_max_slope_ratio must be <= 1")
    if minimum_source < touches * 2 or maximum_source < minimum_source:
        raise CalibrationNotConfigured("source pivot bounds do not cover independent touches")
    causal_minimum = left + right + minimum_source + 1
    if parameters.minimum_history_bars < causal_minimum:
        raise CalibrationNotConfigured(
            "minimum_history_bars does not cover pivot confirmation and Ascending Triangle sources"
        )


def _prefix(core_input: PatternCoreInput, evaluation_ordinal: int) -> PatternCoreInput:
    bars = tuple(bar for bar in core_input.bars if bar.session_ordinal <= evaluation_ordinal)
    source_hash = stable_hash(
        {"instrument_id": core_input.instrument_id, "timeframe": core_input.timeframe, "bars": bars}
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


def _line(candidate: PatternCandidate, role: str, session_ordinal: float) -> float:
    slope = float(_fact(candidate, f"{role}_slope_per_session").value)
    intercept = float(_fact(candidate, f"{role}_intercept").value)
    return slope * session_ordinal + intercept


class AscendingTriangleDetector:
    """Discover bounded suffixes with horizontal resistance and rising support."""

    descriptor = DetectorDescriptor(
        PatternFamily.TRIANGLE,
        PatternType.ASCENDING_TRIANGLE,
        PatternDirection.BULLISH,
        ASCENDING_TRIANGLE_DETECTOR_VERSION,
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
        for evaluation_ordinal in range(
            parameters.minimum_history_bars - 1,
            context.evaluation_session_ordinal + 1,
        ):
            prefix = _prefix(context.core_input, evaluation_ordinal)
            pivots = self._pivot_engine(parameters).replay(
                prefix, evaluation_session_ordinal=evaluation_ordinal
            ).confirmed
            if len(pivots) < _integer(parameters, "minimum_source_pivots", minimum=4):
                continue
            boundaries = self._boundary_engine(parameters).replay(
                prefix, pivots, evaluation_session_ordinal=evaluation_ordinal
            ).boundaries
            proposal = self._longest_qualified(prefix, parameters, pivots, boundaries)
            if proposal is None:
                continue
            facts = {item.code: item.value for item in proposal.geometry_facts}
            anchor_count = _integer(parameters, "minimum_source_pivots", minimum=4)
            episode_key = stable_hash(
                {
                    "instrument_id": prefix.instrument_id,
                    "timeframe": prefix.timeframe,
                    "formed_session_ordinal": proposal.formed_session_ordinal,
                    "anchor_availability": tuple(
                        item.available_from_session_ordinal
                        for item in proposal.source_pivots[:anchor_count]
                    ),
                    "upper_slope": facts["upper_slope_per_session"],
                    "upper_intercept": facts["upper_intercept"],
                    "lower_slope": facts["lower_slope_per_session"],
                    "lower_intercept": facts["lower_intercept"],
                    "detector_version": ASCENDING_TRIANGLE_DETECTOR_VERSION,
                    "parameter_set_id": parameters.parameter_set_id,
                }
            )
            if episode_key not in seen_episode_keys:
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

    @classmethod
    def _longest_qualified(
        cls,
        core_input: PatternCoreInput,
        parameters: DetectorParameterSet,
        pivots: tuple[Pivot, ...],
        boundaries: tuple[Boundary, ...],
    ) -> CandidateProposal | None:
        minimum = _integer(parameters, "minimum_source_pivots", minimum=4)
        maximum = min(_integer(parameters, "maximum_source_pivots", minimum=minimum), len(pivots))
        for length in range(maximum, minimum - 1, -1):
            proposal = cls._qualify(core_input, parameters, pivots[-length:], boundaries)
            if proposal is not None:
                return proposal
        return None

    @staticmethod
    def _qualify(
        core_input: PatternCoreInput,
        parameters: DetectorParameterSet,
        source: tuple[Pivot, ...],
        boundaries: tuple[Boundary, ...],
    ) -> CandidateProposal | None:
        if any(pivot.status != "confirmed" for pivot in source):
            return None
        ordered = tuple(sorted(source, key=lambda item: item.source_session_ordinal))
        if ordered != source or any(
            left.pivot_type == right.pivot_type for left, right in zip(source, source[1:])
        ):
            return None
        highs = tuple(item for item in source if item.pivot_type == "swing_high")
        lows = tuple(item for item in source if item.pivot_type == "swing_low")
        minimum_touches = _integer(parameters, "minimum_touches_per_side", minimum=2)
        if len(highs) < minimum_touches or len(lows) < minimum_touches:
            return None

        high_ids = {item.pivot_id for item in highs}
        resistance = next(
            (
                boundary
                for boundary in boundaries
                if boundary.status == "active"
                and boundary.boundary_role == "resistance"
                and boundary.available_from_ordinal <= core_input.bars[-1].session_ordinal
                and len(high_ids.intersection(boundary.source_pivot_ids)) >= minimum_touches
            ),
            None,
        )
        if resistance is None:
            return None

        start_ordinal = source[0].source_session_ordinal
        confirmed_source_ordinal = source[-1].source_session_ordinal
        span = confirmed_source_ordinal - start_ordinal
        if span < _integer(parameters, "minimum_structure_span_sessions", minimum=1):
            return None
        base_price = max(sum(item.price for item in source) / len(source), 1.0)
        geometry = build_two_line_geometry(
            tuple(SessionPoint(item.source_session_ordinal, item.price) for item in highs),
            tuple(SessionPoint(item.source_session_ordinal, item.price) for item in lows),
            base_price=base_price,
            start_session_ordinal=start_ordinal,
            confirmed_session_ordinal=confirmed_source_ordinal,
        )
        if geometry.apex_session_offset is None:
            return None
        upper_slope_pct = geometry.upper.slope_per_session / base_price
        lower_slope_pct = geometry.lower.slope_per_session / base_price
        horizontal = (
            abs(upper_slope_pct)
            <= _number(parameters, "horizontal_resistance_max_slope_pct_per_session")
            and abs(geometry.upper.slope_per_session)
            <= abs(geometry.lower.slope_per_session)
            * _number(parameters, "horizontal_to_support_max_slope_ratio")
        )
        rising = lower_slope_pct >= _number(parameters, "support_min_slope_pct_per_session")
        fit_ok = max(geometry.upper.max_error_pct, geometry.lower.max_error_pct) <= _number(
            parameters, "maximum_line_fit_error_pct"
        )
        contraction_ok = geometry.contraction_pct >= _number(parameters, "minimum_contraction_pct")
        apex_ordinal = geometry.apex_session_offset
        apex_span = apex_ordinal - start_ordinal
        apex_progress = span / apex_span if apex_span > 0 else -1.0
        apex_ok = (
            apex_ordinal > confirmed_source_ordinal
            and _number(parameters, "minimum_apex_progress")
            <= apex_progress
            <= _number(parameters, "maximum_apex_progress_at_confirmation")
            and apex_ordinal - confirmed_source_ordinal
            <= _integer(parameters, "maximum_apex_horizon_sessions", minimum=1)
        )
        resistance_width_pct = (
            (resistance.price_high - resistance.price_low)
            / max(abs(resistance.price), 1.0)
            * 100.0
        )
        resistance_stable = resistance_width_pct <= _number(
            parameters, "maximum_resistance_zone_width_pct"
        )
        containment_margin = _number(parameters, "containment_tolerance_pct") / 100.0
        bars_by_ordinal = {bar.session_ordinal: bar for bar in core_input.bars}
        structure_bars = tuple(
            bar
            for ordinal, bar in bars_by_ordinal.items()
            if start_ordinal <= ordinal <= confirmed_source_ordinal
        )
        contained = all(
            line_price(geometry.lower, bar.session_ordinal) * (1.0 - containment_margin)
            <= bar.close
            <= line_price(geometry.upper, bar.session_ordinal) * (1.0 + containment_margin)
            for bar in structure_bars
        )
        if not all((horizontal, rising, fit_ok, contraction_ok, apex_ok, resistance_stable, contained)):
            return None

        available_ordinal = max(
            resistance.available_from_ordinal,
            *(pivot.available_from_ordinal for pivot in source),
        )
        available_bar = bars_by_ordinal[available_ordinal]
        pivot_refs = tuple(
            SourceFactReference(
                SourceFactType.PIVOT,
                pivot.pivot_id,
                pivot.available_from,
                pivot.available_from_ordinal,
            )
            for pivot in source
        )
        resistance_ref = SourceFactReference(
            SourceFactType.BOUNDARY,
            resistance.boundary_id,
            resistance.available_from,
            resistance.available_from_ordinal,
        )
        lineage = tuple(item.pivot_id for item in source) + (resistance.boundary_id,)
        upper_at_confirmation = line_price(geometry.upper, confirmed_source_ordinal)
        lower_at_confirmation = line_price(geometry.lower, confirmed_source_ordinal)
        geometry_facts = (
            EvidenceFact("upper_slope_per_session", geometry.upper.slope_per_session, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("upper_intercept", geometry.upper.intercept, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("upper_slope_pct_per_session", upper_slope_pct, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("upper_fit_error_pct", geometry.upper.max_error_pct, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("lower_slope_per_session", geometry.lower.slope_per_session, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("lower_intercept", geometry.lower.intercept, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("lower_slope_pct_per_session", lower_slope_pct, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("lower_fit_error_pct", geometry.lower.max_error_pct, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("start_gap", geometry.start_gap, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("confirmed_gap", geometry.confirmed_gap, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("contraction_pct", geometry.contraction_pct, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("apex_session_ordinal", apex_ordinal, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("apex_progress_at_confirmation", apex_progress, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("resistance_at_confirmation", upper_at_confirmation, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("support_at_confirmation", lower_at_confirmation, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("structure_span_sessions", span, available_bar.session_date, available_ordinal, lineage),
        )
        sequence = "".join("R" if item.pivot_type == "swing_high" else "S" for item in source)
        structure_facts = (
            EvidenceFact("ascending_triangle_structure_confirmed", True, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("horizontal_resistance_confirmed", horizontal, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("rising_support_confirmed", rising, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("convergence_confirmed", contraction_ok and apex_ok, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("containment_confirmed", contained, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("resistance_boundary_stable", resistance_stable, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("resistance_touch_count", len(highs), available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("support_touch_count", len(lows), available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("touch_sequence", sequence, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("directional_context", "bullish_structure_context_only", available_bar.session_date, available_ordinal, lineage),
        )
        return CandidateProposal(
            formed_session_ordinal=start_ordinal,
            available_from_session_ordinal=available_ordinal,
            source_pivots=pivot_refs,
            source_boundaries=(resistance_ref,),
            geometry_facts=geometry_facts,
            structure_facts=structure_facts,
            direction_confirmation_required=True,
            expires_at_session_ordinal=available_ordinal
            + _integer(parameters, "expiry_sessions", minimum=1),
            identity_anchors=tuple(
                f"{pivot.pivot_type}:{bar_id}"
                for pivot in source
                for bar_id in pivot.source_bar_ids
            ),
        )


class AscendingTriangleStructureConfirmation:
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> ConfirmationAssessment:
        fact = _fact(candidate, "ascending_triangle_structure_confirmed")
        state = ConfirmationState.CONFIRMED if fact.value is True else ConfirmationState.REJECTED
        return ConfirmationAssessment(
            candidate_id=candidate.candidate_id,
            confirmation_type=ConfirmationType.STRUCTURE,
            state=state,
            reason=(
                "confirmed_available_ascending_triangle_geometry_exists"
                if fact.value is True
                else "ascending_triangle_geometry_not_confirmed"
            ),
            observed_on=candidate.available_from,
            observed_session_ordinal=candidate.available_from_session_ordinal,
            facts=(fact,),
        )


class AscendingTriangleDirectionConfirmation:
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> ConfirmationAssessment:
        margin = _number(parameters, "breakout_close_margin_pct") / 100.0
        apex = float(_fact(candidate, "apex_session_ordinal").value)
        for bar in context.core_input.bars:
            if bar.session_ordinal <= candidate.available_from_session_ordinal:
                continue
            if bar.session_ordinal >= apex:
                break
            resistance = _line(candidate, "upper", bar.session_ordinal)
            if bar.close > resistance * (1.0 + margin):
                fact = EvidenceFact(
                    "ascending_triangle_upside_close_confirmed",
                    bar.close,
                    bar.session_date,
                    bar.session_ordinal,
                    (bar.bar_id,) + tuple(item.source_id for item in candidate.source_boundaries),
                )
                return ConfirmationAssessment(
                    candidate_id=candidate.candidate_id,
                    confirmation_type=ConfirmationType.DIRECTION,
                    state=ConfirmationState.CONFIRMED,
                    reason="closed_session_close_cleared_resistance",
                    observed_on=bar.session_date,
                    observed_session_ordinal=bar.session_ordinal,
                    facts=(fact,),
                )
        return ConfirmationAssessment(
            candidate_id=candidate.candidate_id,
            confirmation_type=ConfirmationType.DIRECTION,
            state=ConfirmationState.PENDING,
            reason="structure_exists_without_confirmed_upside_close",
        )


class AscendingTriangleInvalidation:
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> InvalidationAssessment:
        invalidation_margin = _number(parameters, "invalidation_buffer_pct") / 100.0
        breakout_margin = _number(parameters, "breakout_close_margin_pct") / 100.0
        apex = float(_fact(candidate, "apex_session_ordinal").value)
        breakout_seen = False
        condition = "lower_trendline_break_or_apex_resistance_failure"
        for bar in context.core_input.bars:
            if bar.session_ordinal <= candidate.available_from_session_ordinal:
                continue
            if bar.session_ordinal >= apex and not breakout_seen:
                fact = EvidenceFact(
                    "ascending_triangle_apex_reached",
                    bar.session_ordinal,
                    bar.session_date,
                    bar.session_ordinal,
                    (bar.bar_id,) + tuple(item.source_id for item in candidate.source_boundaries),
                )
                return InvalidationAssessment(
                    candidate_id=candidate.candidate_id,
                    condition=condition,
                    invalidated=True,
                    reason="apex_reached_without_resistance_break",
                    observed_on=bar.session_date,
                    observed_session_ordinal=bar.session_ordinal,
                    facts=(fact,),
                )
            support = _line(candidate, "lower", bar.session_ordinal)
            resistance = _line(candidate, "upper", bar.session_ordinal)
            if bar.close < support * (1.0 - invalidation_margin):
                fact = EvidenceFact(
                    "ascending_triangle_lower_trendline_break",
                    bar.close,
                    bar.session_date,
                    bar.session_ordinal,
                    (bar.bar_id,) + tuple(item.source_id for item in candidate.source_pivots),
                )
                return InvalidationAssessment(
                    candidate_id=candidate.candidate_id,
                    condition=condition,
                    invalidated=True,
                    reason="closed_session_below_rising_support",
                    observed_on=bar.session_date,
                    observed_session_ordinal=bar.session_ordinal,
                    facts=(fact,),
                )
            if bar.close > resistance * (1.0 + breakout_margin):
                breakout_seen = True
        return InvalidationAssessment(candidate.candidate_id, condition, False)
