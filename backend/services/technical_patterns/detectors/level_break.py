"""Causal breakout and breakdown detectors over canonical Daily sessions.

This module is deliberately provider-neutral.  It consumes only
``PatternCoreInput`` plus the canonical indicator layer output; it never calls
TA-Lib, IBKR, or a product service directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..calibration import CalibrationNotConfigured, DetectorParameterSet
from ..core.identity import stable_id
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


LEVEL_BREAK_DETECTOR_VERSION = "wp-level-break-detector-v1"
EMA_FAST_PERIOD = 20
EMA_SLOW_PERIOD = 50
ATR_PERIOD = 14

_PARAMETER_NAMES = (
    "atr_margin_multiplier",
    "decisive_margin_pct",
    "expiry_sessions",
    "invalidation_buffer_pct",
    "lookback_bars",
    "minimum_boundary_age_sessions",
    "minimum_boundary_touches",
    "zone_atr_width_multiplier",
    "zone_width_pct",
    "volume_average_bars",
    "volume_ratio_threshold",
)


def _number(parameters: DetectorParameterSet, name: str, *, minimum: float = 0.0) -> float:
    value = parameters.require(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < minimum:
        raise CalibrationNotConfigured(f"{name!r} must be an explicit number >= {minimum}")
    return float(value)


def _integer(parameters: DetectorParameterSet, name: str, *, minimum: int = 1) -> int:
    value = parameters.require(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise CalibrationNotConfigured(f"{name!r} must be an explicit integer >= {minimum}")
    return value


def _validate_parameters(parameters: DetectorParameterSet) -> None:
    for name in _PARAMETER_NAMES:
        parameters.require(name)
    _integer(parameters, "lookback_bars", minimum=2)
    _integer(parameters, "volume_average_bars", minimum=2)
    _integer(parameters, "minimum_boundary_touches")
    _integer(parameters, "minimum_boundary_age_sessions")
    _integer(parameters, "expiry_sessions")
    for name in (
        "atr_margin_multiplier",
        "decisive_margin_pct",
        "invalidation_buffer_pct",
        "zone_atr_width_multiplier",
        "zone_width_pct",
        "volume_ratio_threshold",
    ):
        _number(parameters, name)
    first_eligible_position = max(
        _integer(parameters, "lookback_bars", minimum=2),
        _integer(parameters, "volume_average_bars", minimum=2),
        EMA_SLOW_PERIOD - 1,
    )
    if parameters.minimum_history_bars < first_eligible_position + 1:
        raise CalibrationNotConfigured(
            "minimum_history_bars does not cover boundary, prior-volume and EMA warm-up requirements"
        )


def _indicator_value(indicators: IndicatorSeries, code: str, position: int) -> float | None:
    return indicators.column(code).values[position]


def _volume_indicator_code(parameters: DetectorParameterSet) -> str:
    return f"VOLUME_SMA{_integer(parameters, 'volume_average_bars', minimum=2)}"


def _fact(candidate: PatternCandidate, code: str) -> EvidenceFact:
    for item in candidate.geometry_facts + candidate.structure_facts:
        if item.code == code:
            return item
    raise ValueError(f"candidate {candidate.candidate_id} has no evidence fact {code!r}")


@dataclass(frozen=True)
class _LevelBreakDefinition:
    pattern_type: PatternType
    direction: PatternDirection
    boundary_role: Literal["support", "resistance"]


class LevelBreakDetector:
    """Shared boundary-break discovery with direction-specific price rules."""

    definition: _LevelBreakDefinition

    def __init__(self, definition: _LevelBreakDefinition) -> None:
        self.definition = definition
        self.descriptor = DetectorDescriptor(
            PatternFamily.LEVEL_BREAK,
            definition.pattern_type,
            definition.direction,
            LEVEL_BREAK_DETECTOR_VERSION,
        )

    def required_indicators(
        self, parameters: DetectorParameterSet
    ) -> tuple[IndicatorDefinition, ...]:
        _validate_parameters(parameters)
        volume_period = _integer(parameters, "volume_average_bars", minimum=2)
        return (
            IndicatorDefinition(f"EMA{EMA_FAST_PERIOD}", IndicatorKind.EMA, (EMA_FAST_PERIOD,)),
            IndicatorDefinition(f"EMA{EMA_SLOW_PERIOD}", IndicatorKind.EMA, (EMA_SLOW_PERIOD,)),
            IndicatorDefinition(f"ATR{ATR_PERIOD}", IndicatorKind.ATR, (ATR_PERIOD,)),
            IndicatorDefinition(_volume_indicator_code(parameters), IndicatorKind.SMA, (volume_period,), source="volume"),
        )

    def discover(
        self,
        context: DetectorContext,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> tuple[CandidateProposal, ...]:
        _validate_parameters(parameters)
        bars = context.core_input.bars
        lookback = _integer(parameters, "lookback_bars", minimum=2)
        volume_period = _integer(parameters, "volume_average_bars", minimum=2)
        first_trigger = max(lookback, volume_period, EMA_SLOW_PERIOD - 1)
        proposals: list[CandidateProposal] = []
        for position in range(first_trigger, len(bars)):
            proposal = self._proposal_at(context, parameters, indicators, position)
            if proposal is not None:
                proposals.append(proposal)
        return tuple(proposals)

    def _proposal_at(
        self,
        context: DetectorContext,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
        position: int,
    ) -> CandidateProposal | None:
        bars = context.core_input.bars
        trigger = bars[position]
        lookback = _integer(parameters, "lookback_bars", minimum=2)
        history = bars[position - lookback : position]
        atr = _indicator_value(indicators, f"ATR{ATR_PERIOD}", position)
        prior_volume_average = _indicator_value(indicators, _volume_indicator_code(parameters), position - 1)
        ema20 = _indicator_value(indicators, f"EMA{EMA_FAST_PERIOD}", position)
        ema50 = _indicator_value(indicators, f"EMA{EMA_SLOW_PERIOD}", position)
        if atr is None or prior_volume_average is None or prior_volume_average <= 0:
            return None

        is_breakout = self.definition.pattern_type is PatternType.BREAKOUT
        levels = tuple(bar.high if is_breakout else bar.low for bar in history)
        axis = max(levels) if is_breakout else min(levels)
        relative_extreme = levels.index(axis)
        source_position = position - lookback + relative_extreme
        source_atr = _indicator_value(indicators, f"ATR{ATR_PERIOD}", source_position) or 0.0
        zone_width = max(
            axis * _number(parameters, "zone_width_pct") / 100.0,
            source_atr * _number(parameters, "zone_atr_width_multiplier"),
        )
        zone_low = axis - zone_width
        zone_high = axis + zone_width
        decisive_margin = max(
            axis * _number(parameters, "decisive_margin_pct") / 100.0,
            atr * _number(parameters, "atr_margin_multiplier"),
        )
        break_edge = zone_high if is_breakout else axis
        threshold = break_edge + decisive_margin if is_breakout else break_edge - decisive_margin
        price_break = trigger.close >= threshold if is_breakout else trigger.close < threshold
        if not price_break:
            return None

        touched = tuple(
            bar
            for bar in history
            if (zone_low <= bar.high <= zone_high if is_breakout else zone_low <= bar.low <= zone_high)
        )
        first_touch = min((bar.session_ordinal for bar in touched), default=history[-1].session_ordinal)
        boundary_age = trigger.session_ordinal - first_touch
        touch_count = len(touched)
        minimum_touches = _integer(parameters, "minimum_boundary_touches")
        minimum_age = _integer(parameters, "minimum_boundary_age_sessions")
        boundary_authoritative = touch_count >= minimum_touches and boundary_age >= minimum_age

        volume_ratio = trigger.volume / prior_volume_average
        volume_threshold = _number(parameters, "volume_ratio_threshold")
        volume_confirmed = volume_ratio >= volume_threshold
        ema_alignment = (
            ema20 is not None
            and ema50 is not None
            and (ema20 >= ema50 if is_breakout else ema20 <= ema50)
        )
        boundary_date = history[-1].session_date
        boundary_ordinal = history[-1].session_ordinal
        source_bar_ids = tuple(bar.bar_id for bar in history)
        boundary_id = stable_id(
            "bnd",
            {
                "instrument_id": context.core_input.instrument_id,
                "timeframe": context.core_input.timeframe,
                "role": self.definition.boundary_role,
                "axis": axis,
                "zone_low": zone_low,
                "zone_high": zone_high,
                "source_bar_ids": source_bar_ids,
                "parameter_set_id": parameters.parameter_set_id,
            },
        )
        boundary_ref = SourceFactReference(
            SourceFactType.BOUNDARY,
            boundary_id,
            boundary_date,
            boundary_ordinal,
        )
        invalidation_buffer = axis * _number(parameters, "invalidation_buffer_pct") / 100.0
        invalidation_boundary = (
            min(axis - invalidation_buffer, trigger.low)
            if is_breakout
            else max(axis + invalidation_buffer, trigger.high)
        )

        boundary_sources = (boundary_id,) + source_bar_ids
        trigger_sources = (trigger.bar_id, boundary_id)
        geometry = (
            EvidenceFact("boundary_axis", axis, boundary_date, boundary_ordinal, boundary_sources),
            EvidenceFact("boundary_zone_low", zone_low, boundary_date, boundary_ordinal, boundary_sources),
            EvidenceFact("boundary_zone_high", zone_high, boundary_date, boundary_ordinal, boundary_sources),
            EvidenceFact("boundary_touch_count", touch_count, boundary_date, boundary_ordinal, boundary_sources),
            EvidenceFact("boundary_age_sessions", boundary_age, boundary_date, boundary_ordinal, boundary_sources),
            EvidenceFact(
                "minimum_boundary_touches", minimum_touches, boundary_date, boundary_ordinal, (boundary_id,)
            ),
            EvidenceFact("minimum_boundary_age_sessions", minimum_age, boundary_date, boundary_ordinal, (boundary_id,)),
            EvidenceFact("boundary_authoritative", boundary_authoritative, boundary_date, boundary_ordinal, (boundary_id,)),
        )
        structure = (
            EvidenceFact("price_break_confirmed", True, trigger.session_date, trigger.session_ordinal, trigger_sources),
            EvidenceFact("break_close", trigger.close, trigger.session_date, trigger.session_ordinal, trigger_sources),
            EvidenceFact("break_edge", break_edge, trigger.session_date, trigger.session_ordinal, trigger_sources),
            EvidenceFact("break_threshold", threshold, trigger.session_date, trigger.session_ordinal, trigger_sources),
            EvidenceFact("decisive_margin", decisive_margin, trigger.session_date, trigger.session_ordinal, trigger_sources),
            EvidenceFact("volume_ratio", volume_ratio, trigger.session_date, trigger.session_ordinal, (trigger.bar_id,)),
            EvidenceFact("volume_ratio_threshold", volume_threshold, trigger.session_date, trigger.session_ordinal, (trigger.bar_id,)),
            EvidenceFact("volume_confirmed", volume_confirmed, trigger.session_date, trigger.session_ordinal, (trigger.bar_id,)),
            EvidenceFact("ema20", ema20 if ema20 is not None else "unavailable", trigger.session_date, trigger.session_ordinal, (trigger.bar_id,)),
            EvidenceFact("ema50", ema50 if ema50 is not None else "unavailable", trigger.session_date, trigger.session_ordinal, (trigger.bar_id,)),
            EvidenceFact("ema_direction_aligned", ema_alignment, trigger.session_date, trigger.session_ordinal, (trigger.bar_id,)),
            EvidenceFact(
                "invalidation_boundary",
                invalidation_boundary,
                trigger.session_date,
                trigger.session_ordinal,
                trigger_sources,
            ),
        )
        return CandidateProposal(
            formed_session_ordinal=trigger.session_ordinal,
            available_from_session_ordinal=trigger.session_ordinal,
            source_pivots=(),
            source_boundaries=(boundary_ref,),
            geometry_facts=geometry,
            structure_facts=structure,
            direction_confirmation_required=True,
            expires_at_session_ordinal=trigger.session_ordinal + _integer(parameters, "expiry_sessions"),
        )


class BreakoutDetector(LevelBreakDetector):
    def __init__(self) -> None:
        super().__init__(_LevelBreakDefinition(PatternType.BREAKOUT, PatternDirection.BULLISH, "resistance"))


class BreakdownDetector(LevelBreakDetector):
    def __init__(self) -> None:
        super().__init__(_LevelBreakDefinition(PatternType.BREAKDOWN, PatternDirection.BEARISH, "support"))


class LevelBreakStructureConfirmation:
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> ConfirmationAssessment:
        fact = _fact(candidate, "price_break_confirmed")
        state = ConfirmationState.CONFIRMED if fact.value is True else ConfirmationState.REJECTED
        return ConfirmationAssessment(
            candidate_id=candidate.candidate_id,
            confirmation_type=ConfirmationType.STRUCTURE,
            state=state,
            reason="closed_session_price_cleared_boundary" if fact.value is True else "boundary_break_not_confirmed",
            observed_on=candidate.available_from,
            observed_session_ordinal=candidate.available_from_session_ordinal,
            facts=(fact,),
        )


class LevelBreakDirectionConfirmation:
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> ConfirmationAssessment:
        volume = _fact(candidate, "volume_confirmed")
        authority = _fact(candidate, "boundary_authoritative")
        facts = [volume, authority]
        ready = volume.value is True and authority.value is True
        if candidate.pattern_type is PatternType.BREAKDOWN:
            alignment = _fact(candidate, "ema_direction_aligned")
            facts.append(alignment)
            ready = ready and alignment.value is True
        return ConfirmationAssessment(
            candidate_id=candidate.candidate_id,
            confirmation_type=ConfirmationType.DIRECTION,
            state=ConfirmationState.CONFIRMED if ready else ConfirmationState.PENDING,
            reason="volume_and_direction_evidence_confirmed" if ready else "direction_evidence_incomplete",
            observed_on=candidate.available_from if ready else None,
            observed_session_ordinal=candidate.available_from_session_ordinal if ready else None,
            facts=tuple(facts),
        )


class LevelBreakInvalidation:
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> InvalidationAssessment:
        boundary = float(_fact(candidate, "invalidation_boundary").value)
        is_breakout = candidate.pattern_type is PatternType.BREAKOUT
        condition = (
            "closed_session_close_at_or_below_breakout_invalidation_boundary"
            if is_breakout
            else "closed_session_close_at_or_above_breakdown_invalidation_boundary"
        )
        for bar in context.core_input.bars:
            if bar.session_ordinal <= candidate.available_from_session_ordinal:
                continue
            invalidated = bar.close <= boundary if is_breakout else bar.close >= boundary
            if invalidated:
                fact = EvidenceFact(
                    "invalidation_close",
                    bar.close,
                    bar.session_date,
                    bar.session_ordinal,
                    (bar.bar_id,),
                )
                return InvalidationAssessment(
                    candidate_id=candidate.candidate_id,
                    condition=condition,
                    invalidated=True,
                    reason="closed_session_reentered_invalid_side_of_structure",
                    observed_on=bar.session_date,
                    observed_session_ordinal=bar.session_ordinal,
                    facts=(fact,),
                )
        return InvalidationAssessment(candidate.candidate_id, condition, False)
