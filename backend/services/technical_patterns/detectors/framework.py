"""Causal orchestration boundary for future concrete Pattern detectors."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from ..calibration import CalibrationKey, CalibrationProvider, DetectorParameterSet
from ..core.contracts import PatternCoreInput
from ..core.identity import stable_hash, stable_id
from ..core.lifecycle import LifecycleCore, LifecycleSnapshot
from ..indicators import CanonicalIndicatorLayer, IndicatorDefinition, IndicatorSeries
from .contracts import (
    CandidateProposal,
    ConfirmationAssessment,
    ConfirmationState,
    ConfirmationType,
    DetectorDescriptor,
    InvalidationAssessment,
    PatternCandidate,
    PatternResult,
)


DETECTOR_FRAMEWORK_VERSION = "wp-pattern-detector-framework-v1"


class DetectorFrameworkError(RuntimeError):
    pass


class CausalityViolation(DetectorFrameworkError):
    pass


class InsufficientPatternHistory(DetectorFrameworkError):
    pass


@dataclass(frozen=True)
class DetectorContext:
    core_input: PatternCoreInput
    evaluation_session_ordinal: int

    @property
    def evaluation_session(self):
        return self.core_input.bars[-1].session_date


class CandidateDetector(Protocol):
    descriptor: DetectorDescriptor

    def required_indicators(
        self, parameters: DetectorParameterSet
    ) -> tuple[IndicatorDefinition, ...]: ...

    def discover(
        self,
        context: DetectorContext,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> tuple[CandidateProposal, ...]: ...


class StructureConfirmationEvaluator(Protocol):
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> ConfirmationAssessment: ...


class DirectionConfirmationEvaluator(Protocol):
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> ConfirmationAssessment: ...


class InvalidationEvaluator(Protocol):
    def evaluate(
        self,
        context: DetectorContext,
        candidate: PatternCandidate,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
    ) -> InvalidationAssessment: ...


@dataclass(frozen=True)
class RejectedCandidate:
    proposal_index: int
    reason: str


@dataclass(frozen=True)
class DetectorRunResult:
    descriptor: DetectorDescriptor
    calibration_key: CalibrationKey
    evaluation_session_ordinal: int
    evaluation_source_bar_hash: str
    results: tuple[PatternResult, ...]
    rejected_candidates: tuple[RejectedCandidate, ...]
    framework_version: str = DETECTOR_FRAMEWORK_VERSION

    @property
    def result_hash(self) -> str:
        return stable_hash(self)


class DetectorFramework:
    def __init__(
        self,
        *,
        calibrations: CalibrationProvider,
        indicators: CanonicalIndicatorLayer,
    ) -> None:
        self._calibrations = calibrations
        self._indicators = indicators

    def run(
        self,
        core_input: PatternCoreInput,
        *,
        evaluation_session_ordinal: int,
        calibration_key: CalibrationKey,
        detector: CandidateDetector,
        structure_confirmation: StructureConfirmationEvaluator,
        invalidation: InvalidationEvaluator,
        direction_confirmation: DirectionConfirmationEvaluator | None = None,
    ) -> DetectorRunResult:
        context = self._causal_context(core_input, evaluation_session_ordinal)
        parameters = self._calibrations.resolve(calibration_key)
        self._validate_binding(context, calibration_key, detector.descriptor)
        if len(context.core_input.bars) < parameters.minimum_history_bars:
            raise InsufficientPatternHistory(
                f"{len(context.core_input.bars)} bars available; "
                f"calibration requires {parameters.minimum_history_bars}"
            )
        definitions = detector.required_indicators(parameters)
        indicator_series = self._indicators.calculate(context.core_input, definitions)
        self._validate_indicators(context, indicator_series)
        proposals = detector.discover(context, parameters, indicator_series)

        results: list[PatternResult] = []
        rejected: list[RejectedCandidate] = []
        for index, proposal in enumerate(proposals):
            try:
                candidate = self._materialize_candidate(
                    context,
                    detector.descriptor,
                    calibration_key,
                    parameters,
                    indicator_series,
                    proposal,
                )
                structure = structure_confirmation.evaluate(context, candidate, parameters, indicator_series)
                if candidate.direction_confirmation_required:
                    if direction_confirmation is None:
                        raise DetectorFrameworkError("direction confirmation evaluator is required")
                    direction = direction_confirmation.evaluate(context, candidate, parameters, indicator_series)
                else:
                    direction = ConfirmationAssessment(
                        candidate_id=candidate.candidate_id,
                        confirmation_type=ConfirmationType.DIRECTION,
                        state=ConfirmationState.NOT_REQUIRED,
                        reason="direction_confirmation_not_required_by_candidate",
                    )
                invalidation_result = invalidation.evaluate(context, candidate, parameters, indicator_series)
                self._validate_assessments(context, candidate, structure, direction, invalidation_result)
                lifecycle = self._build_lifecycle(context, candidate, structure, direction, invalidation_result)
                results.append(PatternResult(candidate, structure, direction, invalidation_result, lifecycle))
            except CausalityViolation as exc:
                rejected.append(RejectedCandidate(index, str(exc)))

        return DetectorRunResult(
            descriptor=detector.descriptor,
            calibration_key=calibration_key,
            evaluation_session_ordinal=evaluation_session_ordinal,
            evaluation_source_bar_hash=context.core_input.source_bar_hash,
            results=tuple(results),
            rejected_candidates=tuple(rejected),
        )

    @staticmethod
    def _causal_context(core_input: PatternCoreInput, evaluation_ordinal: int) -> DetectorContext:
        bars = tuple(bar for bar in core_input.bars if bar.session_ordinal <= evaluation_ordinal)
        if not bars or bars[-1].session_ordinal != evaluation_ordinal:
            raise CausalityViolation("evaluation session is outside the canonical closed-bar series")
        source_hash = stable_hash(
            {
                "instrument_id": core_input.instrument_id,
                "timeframe": core_input.timeframe,
                "bars": bars,
            }
        )
        causal_input = replace(
            core_input,
            bars=bars,
            last_closed_session=bars[-1].session_date,
            source_bar_hash=source_hash,
            dataset_version=source_hash,
        )
        return DetectorContext(causal_input, evaluation_ordinal)

    @staticmethod
    def _validate_binding(
        context: DetectorContext,
        key: CalibrationKey,
        descriptor: DetectorDescriptor,
    ) -> None:
        if key.market != context.core_input.market.upper() or key.timeframe != context.core_input.timeframe:
            raise DetectorFrameworkError("calibration market/timeframe does not match PatternCoreInput")
        if key.pattern_family != descriptor.pattern_family.value or key.pattern_type != descriptor.pattern_type.value:
            raise DetectorFrameworkError("calibration pattern binding does not match detector descriptor")

    @staticmethod
    def _validate_indicators(context: DetectorContext, indicators: IndicatorSeries) -> None:
        if (
            indicators.instrument_id != context.core_input.instrument_id
            or indicators.timeframe != context.core_input.timeframe
            or indicators.source_bar_hash != context.core_input.source_bar_hash
            or indicators.evaluation_session_ordinal != context.evaluation_session_ordinal
        ):
            raise DetectorFrameworkError("indicator output is not bound to the causal detector input")
        if any(len(column.values) != len(context.core_input.bars) for column in indicators.columns):
            raise DetectorFrameworkError("indicator columns are not aligned to the causal bar prefix")

    @staticmethod
    def _materialize_candidate(
        context: DetectorContext,
        descriptor: DetectorDescriptor,
        key: CalibrationKey,
        parameters: DetectorParameterSet,
        indicators: IndicatorSeries,
        proposal: CandidateProposal,
    ) -> PatternCandidate:
        evaluation = context.evaluation_session_ordinal
        by_ordinal = {bar.session_ordinal: bar.session_date for bar in context.core_input.bars}
        if proposal.available_from_session_ordinal > evaluation:
            raise CausalityViolation("future candidate availability was rejected")
        references = proposal.source_pivots + proposal.source_boundaries
        if any(item.available_from_session_ordinal > proposal.available_from_session_ordinal for item in references):
            raise CausalityViolation("future pivot or boundary reference was rejected")
        facts = proposal.geometry_facts + proposal.structure_facts
        if any(item.available_from_session_ordinal > proposal.available_from_session_ordinal for item in facts):
            raise CausalityViolation("future geometry or structure fact was rejected")
        for item in references:
            if by_ordinal.get(item.available_from_session_ordinal) != item.available_from:
                raise CausalityViolation("source reference session/date mismatch was rejected")
        for item in facts:
            if by_ordinal.get(item.available_from_session_ordinal) != item.available_from:
                raise CausalityViolation("candidate evidence session/date mismatch was rejected")
        try:
            formed_on = by_ordinal[proposal.formed_session_ordinal]
            available_from = by_ordinal[proposal.available_from_session_ordinal]
        except KeyError as exc:
            raise CausalityViolation("candidate formation/availability is outside the causal bar prefix") from exc
        identity_material = {
            "instrument_id": context.core_input.instrument_id,
            "timeframe": context.core_input.timeframe,
            "pattern_family": descriptor.pattern_family,
            "pattern_type": descriptor.pattern_type,
            "direction": descriptor.direction,
            "formed_session_ordinal": proposal.formed_session_ordinal,
            "available_from_session_ordinal": proposal.available_from_session_ordinal,
            "source_pivots": references[: len(proposal.source_pivots)],
            "source_boundaries": proposal.source_boundaries,
            "geometry_facts": proposal.geometry_facts,
            "structure_facts": proposal.structure_facts,
            "detector_version": descriptor.detector_version,
            "parameter_set_id": parameters.parameter_set_id,
        }
        candidate_id = stable_id("pat", identity_material)
        return PatternCandidate(
            candidate_id=candidate_id,
            instrument_id=context.core_input.instrument_id,
            timeframe=context.core_input.timeframe,
            pattern_family=descriptor.pattern_family,
            pattern_type=descriptor.pattern_type,
            direction=descriptor.direction,
            formed_on=formed_on,
            formed_session_ordinal=proposal.formed_session_ordinal,
            available_from=available_from,
            available_from_session_ordinal=proposal.available_from_session_ordinal,
            evaluated_on=context.evaluation_session,
            evaluation_session_ordinal=evaluation,
            source_bar_hash=context.core_input.source_bar_hash,
            source_pivots=proposal.source_pivots,
            source_boundaries=proposal.source_boundaries,
            geometry_facts=proposal.geometry_facts,
            structure_facts=proposal.structure_facts,
            direction_confirmation_required=proposal.direction_confirmation_required,
            expires_at_session_ordinal=proposal.expires_at_session_ordinal,
            detector_version=descriptor.detector_version,
            calibration_version=key.calibration_version,
            parameter_set_id=parameters.parameter_set_id,
            indicator_layer_version=indicators.layer_version,
        )

    @staticmethod
    def _validate_assessments(
        context: DetectorContext,
        candidate: PatternCandidate,
        structure: ConfirmationAssessment,
        direction: ConfirmationAssessment,
        invalidation: InvalidationAssessment,
    ) -> None:
        if structure.candidate_id != candidate.candidate_id or structure.confirmation_type is not ConfirmationType.STRUCTURE:
            raise DetectorFrameworkError("structure confirmation is not bound to the candidate")
        if direction.candidate_id != candidate.candidate_id or direction.confirmation_type is not ConfirmationType.DIRECTION:
            raise DetectorFrameworkError("direction confirmation is not bound to the candidate")
        if invalidation.candidate_id != candidate.candidate_id:
            raise DetectorFrameworkError("invalidation is not bound to the candidate")
        assessments = (structure, direction)
        ordinals = tuple(
            item.observed_session_ordinal for item in assessments if item.observed_session_ordinal is not None
        )
        if invalidation.observed_session_ordinal is not None:
            ordinals += (invalidation.observed_session_ordinal,)
        if any(
            ordinal < candidate.available_from_session_ordinal
            or ordinal > context.evaluation_session_ordinal
            for ordinal in ordinals
        ):
            raise CausalityViolation("future or pre-candidate confirmation/invalidation fact was rejected")
        dates = {bar.session_ordinal: bar.session_date for bar in context.core_input.bars}
        for item in assessments:
            if item.observed_session_ordinal is not None and dates[item.observed_session_ordinal] != item.observed_on:
                raise CausalityViolation("confirmation session/date mismatch")
            fact_limit = (
                item.observed_session_ordinal
                if item.observed_session_ordinal is not None
                else context.evaluation_session_ordinal
            )
            if any(fact.available_from_session_ordinal > fact_limit for fact in item.facts):
                raise CausalityViolation("confirmation used evidence unavailable at its observed session")
            if any(dates.get(fact.available_from_session_ordinal) != fact.available_from for fact in item.facts):
                raise CausalityViolation("confirmation evidence session/date mismatch")
        if invalidation.observed_session_ordinal is not None and dates[invalidation.observed_session_ordinal] != invalidation.observed_on:
            raise CausalityViolation("invalidation session/date mismatch")
        invalidation_limit = (
            invalidation.observed_session_ordinal
            if invalidation.observed_session_ordinal is not None
            else context.evaluation_session_ordinal
        )
        if any(fact.available_from_session_ordinal > invalidation_limit for fact in invalidation.facts):
            raise CausalityViolation("invalidation used evidence unavailable at its observed session")
        if any(dates.get(fact.available_from_session_ordinal) != fact.available_from for fact in invalidation.facts):
            raise CausalityViolation("invalidation evidence session/date mismatch")

    @staticmethod
    def _build_lifecycle(
        context: DetectorContext,
        candidate: PatternCandidate,
        structure: ConfirmationAssessment,
        direction: ConfirmationAssessment,
        invalidation: InvalidationAssessment,
    ) -> LifecycleSnapshot:
        snapshot = LifecycleCore.candidate(
            candidate.candidate_id,
            formed_on=candidate.formed_on,
            formed_session_ordinal=candidate.formed_session_ordinal,
        )
        structure_ready = structure.state is ConfirmationState.CONFIRMED
        direction_ready = (
            direction.state is ConfirmationState.CONFIRMED
            or direction.state is ConfirmationState.NOT_REQUIRED
        )
        confirmation_ordinal = None
        if structure_ready and direction_ready:
            observed = [structure.observed_session_ordinal]
            if direction.observed_session_ordinal is not None:
                observed.append(direction.observed_session_ordinal)
            confirmation_ordinal = max(item for item in observed if item is not None)
        invalidation_ordinal = invalidation.observed_session_ordinal if invalidation.invalidated else None
        event_ordinals = {
            item
            for item in (
                confirmation_ordinal,
                invalidation_ordinal,
                candidate.expires_at_session_ordinal,
                context.evaluation_session_ordinal,
            )
            if item is not None and item <= context.evaluation_session_ordinal
        }
        dates = {bar.session_ordinal: bar.session_date for bar in context.core_input.bars}
        for ordinal in sorted(event_ordinals):
            snapshot = LifecycleCore.evaluate(
                snapshot,
                session_date=dates[ordinal],
                session_ordinal=ordinal,
                confirmation=ordinal == confirmation_ordinal,
                invalidation_reason=(invalidation.reason if ordinal == invalidation_ordinal else None),
                expires_at_session_ordinal=candidate.expires_at_session_ordinal,
            )
        return snapshot
