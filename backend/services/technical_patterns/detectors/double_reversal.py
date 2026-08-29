"""Causal Double Top / Double Bottom evidence over confirmed Pattern Core facts.

The shared detector preserves the frozen Tovest four-Pivot structure while
adapting bar distance to exchange-session ordinals and indicator access to the
WealthPilot Canonical Indicator Layer.
"""

from __future__ import annotations

from dataclasses import replace

from ..calibration import CalibrationNotConfigured, DetectorParameterSet
from ..core import BoundaryParameters, BoundaryTrendEngine, PatternCoreInput, PivotEngine, PivotParameters
from ..core.contracts import Boundary, Pivot
from ..core.identity import stable_hash
from ..indicators import IndicatorDefinition, IndicatorKind, IndicatorSeries
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


DOUBLE_REVERSAL_DETECTOR_VERSION = "wp-double-reversal-detector-v1"

_PARAMETER_NAMES = (
    "boundary_tolerance_pct",
    "bottom_volume_ratio_minimum",
    "direction_break_margin_pct",
    "expiry_sessions",
    "extreme_similarity_max_ratio",
    "invalidation_buffer_pct",
    "maximum_structure_duration_sessions",
    "minimum_extreme_separation_sessions",
    "minimum_intervening_reaction_ratio",
    "minimum_preceding_trend_ratio",
    "neckline_tolerance_pct",
    "pattern_type_contract",
    "pivot_left_window_bars",
    "pivot_minimum_bar_separation",
    "pivot_minimum_price_separation_pct",
    "pivot_plateau_tolerance_pct",
    "pivot_right_confirmation_bars",
    "source_pivot_count",
    "volume_average_sessions",
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


def _validate_parameters(parameters: DetectorParameterSet, pattern_type: PatternType) -> None:
    for name in _PARAMETER_NAMES:
        parameters.require(name)
    if parameters.require("pattern_type_contract") != pattern_type.value:
        raise CalibrationNotConfigured("pattern_type_contract does not match the concrete detector")
    if _integer(parameters, "source_pivot_count", minimum=4) != 4:
        raise CalibrationNotConfigured("Double Reversal requires exactly four ordered source pivots")
    left = _integer(parameters, "pivot_left_window_bars", minimum=1)
    right = _integer(parameters, "pivot_right_confirmation_bars", minimum=1)
    volume = _integer(parameters, "volume_average_sessions", minimum=1)
    _integer(parameters, "pivot_minimum_bar_separation")
    _integer(parameters, "minimum_extreme_separation_sessions", minimum=1)
    _integer(parameters, "maximum_structure_duration_sessions", minimum=1)
    _integer(parameters, "expiry_sessions", minimum=1)
    for name in (
        "boundary_tolerance_pct",
        "bottom_volume_ratio_minimum",
        "direction_break_margin_pct",
        "extreme_similarity_max_ratio",
        "invalidation_buffer_pct",
        "minimum_intervening_reaction_ratio",
        "minimum_preceding_trend_ratio",
        "neckline_tolerance_pct",
        "pivot_minimum_price_separation_pct",
        "pivot_plateau_tolerance_pct",
    ):
        _number(parameters, name)
    minimum_span = _integer(parameters, "minimum_extreme_separation_sessions", minimum=1)
    maximum_span = _integer(parameters, "maximum_structure_duration_sessions", minimum=1)
    if maximum_span < minimum_span:
        raise CalibrationNotConfigured(
            "maximum_structure_duration_sessions must cover minimum_extreme_separation_sessions"
        )
    if parameters.minimum_history_bars < max(left + right + 5, volume + 1):
        raise CalibrationNotConfigured(
            "minimum_history_bars does not cover Pivot confirmation and volume warm-up"
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
    try:
        return next(
            fact
            for fact in candidate.geometry_facts + candidate.structure_facts
            if fact.code == code
        )
    except StopIteration as exc:
        raise ValueError(f"candidate {candidate.candidate_id} has no evidence fact {code!r}") from exc


def _volume_code(parameters: DetectorParameterSet) -> str:
    return f"VOLUME_SMA{_integer(parameters, 'volume_average_sessions', minimum=1)}"


def _volume_ratio(
    context: DetectorContext,
    indicators: IndicatorSeries,
    session_ordinal: int,
    parameters: DetectorParameterSet,
) -> float | None:
    index = next(
        (index for index, bar in enumerate(context.core_input.bars) if bar.session_ordinal == session_ordinal),
        None,
    )
    if index is None or index == 0:
        return None
    prior_average = indicators.column(_volume_code(parameters)).values[index - 1]
    if prior_average is None or prior_average <= 0:
        return None
    return context.core_input.bars[index].volume / prior_average


class DoubleReversalDetector:
    """Shared bounded four-Pivot detector; concrete classes bind direction/type."""

    descriptor: DetectorDescriptor

    def required_indicators(
        self, parameters: DetectorParameterSet
    ) -> tuple[IndicatorDefinition, ...]:
        _validate_parameters(parameters, self.descriptor.pattern_type)
        period = _integer(parameters, "volume_average_sessions", minimum=1)
        return (IndicatorDefinition(_volume_code(parameters), IndicatorKind.SMA, (period,), source="volume"),)

    def discover(
        self,
        context: DetectorContext,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> tuple[CandidateProposal, ...]:
        _validate_parameters(parameters, self.descriptor.pattern_type)
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
            if len(pivots) < 4:
                continue
            source = pivots[-4:]
            boundaries = self._boundary_engine(parameters).replay(
                prefix, pivots, evaluation_session_ordinal=evaluation_ordinal
            ).boundaries
            proposal = self._qualify(prefix, parameters, source, boundaries)
            if proposal is None:
                continue
            episode_key = stable_hash(
                {
                    "instrument_id": prefix.instrument_id,
                    "timeframe": prefix.timeframe,
                    "pattern_type": self.descriptor.pattern_type,
                    "sources": tuple(
                        (pivot.pivot_type, pivot.source_session_ordinal, pivot.price)
                        for pivot in source
                    ),
                    "detector_version": DOUBLE_REVERSAL_DETECTOR_VERSION,
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
                minimum_price_separation_pct=_number(
                    parameters, "pivot_minimum_price_separation_pct"
                ),
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

    def _qualify(
        self,
        core_input: PatternCoreInput,
        parameters: DetectorParameterSet,
        source: tuple[Pivot, ...],
        boundaries: tuple[Boundary, ...],
    ) -> CandidateProposal | None:
        if len(source) != 4 or any(pivot.status != "confirmed" for pivot in source):
            return None
        if any(
            left.source_session_ordinal >= right.source_session_ordinal
            or left.pivot_type == right.pivot_type
            for left, right in zip(source, source[1:])
        ):
            return None

        expected = (
            ("swing_low", "swing_high", "swing_low", "swing_high")
            if self.descriptor.pattern_type is PatternType.DOUBLE_TOP
            else ("swing_high", "swing_low", "swing_high", "swing_low")
        )
        if tuple(item.pivot_type for item in source) != expected:
            return None
        prior, first, neckline_pivot, second = source
        bearish = self.descriptor.pattern_type is PatternType.DOUBLE_TOP
        extreme_similarity = abs(first.price - second.price) / max(first.price, second.price)
        if bearish:
            reaction = (
                min(first.price, second.price) - neckline_pivot.price
            ) / min(first.price, second.price)
            preceding = (first.price - prior.price) / prior.price
            invalidation_boundary = max(first.price, second.price)
            measured_target = neckline_pivot.price - (
                (first.price + second.price) / 2.0 - neckline_pivot.price
            )
        else:
            reaction = (
                neckline_pivot.price - max(first.price, second.price)
            ) / max(first.price, second.price)
            preceding = (prior.price - first.price) / first.price
            invalidation_boundary = min(first.price, second.price)
            measured_target = neckline_pivot.price + (
                neckline_pivot.price - (first.price + second.price) / 2.0
            )
        separation = second.source_session_ordinal - first.source_session_ordinal
        duration = separation
        if not (
            extreme_similarity <= _number(parameters, "extreme_similarity_max_ratio")
            and reaction >= _number(parameters, "minimum_intervening_reaction_ratio")
            and preceding >= _number(parameters, "minimum_preceding_trend_ratio")
            and separation
            >= _integer(parameters, "minimum_extreme_separation_sessions", minimum=1)
            and duration
            <= _integer(parameters, "maximum_structure_duration_sessions", minimum=1)
        ):
            return None

        neckline_role = "support" if bearish else "resistance"
        neckline_boundary = next(
            (
                boundary
                for boundary in boundaries
                if boundary.status == "active"
                and boundary.boundary_role == neckline_role
                and neckline_pivot.pivot_id in boundary.source_pivot_ids
                and boundary.available_from_ordinal <= core_input.bars[-1].session_ordinal
            ),
            None,
        )
        if neckline_boundary is None:
            return None
        neckline_distance_pct = (
            abs(neckline_boundary.price - neckline_pivot.price)
            / max(abs(neckline_pivot.price), 1.0)
            * 100.0
        )
        if neckline_distance_pct > _number(parameters, "neckline_tolerance_pct"):
            return None

        available_ordinal = max(
            neckline_boundary.available_from_ordinal,
            *(pivot.available_from_ordinal for pivot in source),
        )
        bars_by_ordinal = {bar.session_ordinal: bar for bar in core_input.bars}
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
        boundary_ref = SourceFactReference(
            SourceFactType.BOUNDARY,
            neckline_boundary.boundary_id,
            neckline_boundary.available_from,
            neckline_boundary.available_from_ordinal,
        )
        lineage = tuple(pivot.pivot_id for pivot in source) + (neckline_boundary.boundary_id,)
        tolerance = _number(parameters, "neckline_tolerance_pct") / 100.0
        neckline_price = neckline_pivot.price
        geometry_facts = (
            EvidenceFact("first_extreme_price", first.price, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("second_extreme_price", second.price, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("extreme_reference_price", (first.price + second.price) / 2.0, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("extreme_similarity_ratio", extreme_similarity, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("neckline_price", neckline_price, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("neckline_lower_bound", neckline_price * (1.0 - tolerance), available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("neckline_upper_bound", neckline_price * (1.0 + tolerance), available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("neckline_geometry", "horizontal", available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("extreme_separation_sessions", separation, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("structure_duration_sessions", duration, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("intervening_reaction_ratio", reaction, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("preceding_trend_ratio", preceding, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("invalidation_boundary_price", invalidation_boundary, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("measured_move_reference", measured_target, available_bar.session_date, available_ordinal, lineage),
        )
        structure_facts = (
            EvidenceFact(f"{self.descriptor.pattern_type.value}_structure_confirmed", True, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("neckline_source_pivot_id", neckline_pivot.pivot_id, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("neckline_boundary_id", neckline_boundary.boundary_id, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("neckline_boundary_role", neckline_role, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("neckline_available_session_ordinal", neckline_boundary.available_from_ordinal, available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("direction_confirmation_boundary", "later_closed_session_neckline_break", available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("volume_confirmation_role", "contextual" if bearish else "required", available_bar.session_date, available_ordinal, lineage),
            EvidenceFact("technical_evidence_only", True, available_bar.session_date, available_ordinal, lineage),
        )
        return CandidateProposal(
            formed_session_ordinal=first.source_session_ordinal,
            available_from_session_ordinal=available_ordinal,
            source_pivots=pivot_refs,
            source_boundaries=(boundary_ref,),
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


class DoubleTopDetector(DoubleReversalDetector):
    descriptor = DetectorDescriptor(
        PatternFamily.REVERSAL,
        PatternType.DOUBLE_TOP,
        PatternDirection.BEARISH,
        DOUBLE_REVERSAL_DETECTOR_VERSION,
    )


class DoubleBottomDetector(DoubleReversalDetector):
    descriptor = DetectorDescriptor(
        PatternFamily.REVERSAL,
        PatternType.DOUBLE_BOTTOM,
        PatternDirection.BULLISH,
        DOUBLE_REVERSAL_DETECTOR_VERSION,
    )


class DoubleReversalStructureConfirmation:
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> ConfirmationAssessment:
        fact = _fact(candidate, f"{candidate.pattern_type.value}_structure_confirmed")
        state = ConfirmationState.CONFIRMED if fact.value is True else ConfirmationState.REJECTED
        return ConfirmationAssessment(
            candidate_id=candidate.candidate_id,
            confirmation_type=ConfirmationType.STRUCTURE,
            state=state,
            reason=(
                "confirmed_available_four_pivot_double_reversal_structure_exists"
                if fact.value is True
                else "double_reversal_structure_not_confirmed"
            ),
            observed_on=candidate.available_from,
            observed_session_ordinal=candidate.available_from_session_ordinal,
            facts=(fact,),
        )


class DoubleReversalDirectionConfirmation:
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> ConfirmationAssessment:
        neckline = float(_fact(candidate, "neckline_price").value)
        margin = _number(parameters, "direction_break_margin_pct") / 100.0
        bearish = candidate.pattern_type is PatternType.DOUBLE_TOP
        for bar in context.core_input.bars:
            if bar.session_ordinal <= candidate.available_from_session_ordinal:
                continue
            ratio = _volume_ratio(context, indicators, bar.session_ordinal, parameters)
            price_confirmed = (
                bar.close < neckline * (1.0 - margin)
                if bearish
                else bar.close > neckline * (1.0 + margin)
            )
            volume_confirmed = bearish or (
                ratio is not None
                and ratio >= _number(parameters, "bottom_volume_ratio_minimum")
            )
            if not (price_confirmed and volume_confirmed):
                continue
            direction_code = (
                "double_top_downside_neckline_close_confirmed"
                if bearish
                else "double_bottom_upside_neckline_close_confirmed"
            )
            lineage = (bar.bar_id,) + tuple(
                item.source_id for item in candidate.source_boundaries
            )
            facts = [
                EvidenceFact(
                    direction_code,
                    bar.close,
                    bar.session_date,
                    bar.session_ordinal,
                    lineage,
                )
            ]
            if ratio is not None:
                facts.append(
                    EvidenceFact(
                        "direction_confirmation_volume_ratio",
                        ratio,
                        bar.session_date,
                        bar.session_ordinal,
                        (bar.bar_id, f"indicator:{_volume_code(parameters)}:{context.core_input.source_bar_hash}"),
                    )
                )
            return ConfirmationAssessment(
                candidate_id=candidate.candidate_id,
                confirmation_type=ConfirmationType.DIRECTION,
                state=ConfirmationState.CONFIRMED,
                reason=(
                    "later_closed_session_broke_below_neckline"
                    if bearish
                    else "later_closed_session_broke_above_neckline_with_required_volume"
                ),
                observed_on=bar.session_date,
                observed_session_ordinal=bar.session_ordinal,
                facts=tuple(facts),
            )
        return ConfirmationAssessment(
            candidate_id=candidate.candidate_id,
            confirmation_type=ConfirmationType.DIRECTION,
            state=ConfirmationState.PENDING,
            reason=(
                "structure_exists_without_later_closed_downside_neckline_break"
                if bearish
                else "structure_exists_without_later_closed_upside_neckline_break_and_volume"
            ),
        )


class DoubleReversalInvalidation:
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> InvalidationAssessment:
        neckline = float(_fact(candidate, "neckline_price").value)
        extreme = float(_fact(candidate, "invalidation_boundary_price").value)
        break_margin = _number(parameters, "direction_break_margin_pct") / 100.0
        invalidation_margin = _number(parameters, "invalidation_buffer_pct") / 100.0
        bearish = candidate.pattern_type is PatternType.DOUBLE_TOP
        direction_seen = False
        condition = (
            "pre_confirmation_extreme_breach_or_post_confirmation_neckline_recovery"
            if bearish
            else "pre_confirmation_extreme_breach_or_post_confirmation_neckline_failure"
        )
        for bar in context.core_input.bars:
            if bar.session_ordinal <= candidate.available_from_session_ordinal:
                continue
            if not direction_seen:
                extreme_breached = (
                    bar.close > extreme * (1.0 + invalidation_margin)
                    if bearish
                    else bar.close < extreme * (1.0 - invalidation_margin)
                )
                if extreme_breached:
                    fact = EvidenceFact(
                        "double_reversal_extreme_structure_breach",
                        bar.close,
                        bar.session_date,
                        bar.session_ordinal,
                        (bar.bar_id,) + tuple(item.source_id for item in candidate.source_pivots),
                    )
                    return InvalidationAssessment(
                        candidate.candidate_id,
                        condition,
                        True,
                        "closed_session_breached_extreme_structure",
                        bar.session_date,
                        bar.session_ordinal,
                        (fact,),
                    )
                ratio = _volume_ratio(context, indicators, bar.session_ordinal, parameters)
                direction_seen = (
                    bar.close < neckline * (1.0 - break_margin)
                    if bearish
                    else bar.close > neckline * (1.0 + break_margin)
                    and ratio is not None
                    and ratio >= _number(parameters, "bottom_volume_ratio_minimum")
                )
                continue

            neckline_invalidated = (
                bar.close > neckline * (1.0 + invalidation_margin)
                if bearish
                else bar.close < neckline * (1.0 - invalidation_margin)
            )
            if neckline_invalidated:
                fact = EvidenceFact(
                    "double_top_neckline_recovery"
                    if bearish
                    else "double_bottom_neckline_failure",
                    bar.close,
                    bar.session_date,
                    bar.session_ordinal,
                    (bar.bar_id,) + tuple(item.source_id for item in candidate.source_boundaries),
                )
                return InvalidationAssessment(
                    candidate.candidate_id,
                    condition,
                    True,
                    "closed_session_recovered_above_neckline"
                    if bearish
                    else "closed_session_failed_below_neckline",
                    bar.session_date,
                    bar.session_ordinal,
                    (fact,),
                )
        return InvalidationAssessment(candidate.candidate_id, condition, False)
