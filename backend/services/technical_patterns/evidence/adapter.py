"""Deterministic adapter from existing Pattern Core facts to product evidence."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from backend.services.pattern_data.contracts import PatternDataStatus

from ..core.contracts import PatternCoreInput
from ..core.lifecycle import LifecycleState
from ..detectors.contracts import (
    ConfirmationAssessment,
    EvidenceFact,
    InvalidationAssessment,
    PatternResult,
    SourceFactReference,
)
from .contracts import (
    ConfirmationEvidenceSnapshot,
    EvidenceFactSnapshot,
    EvidenceGeometrySnapshot,
    EvidenceInvalidationSnapshot,
    EvidenceProvenance,
    EvidenceSnapshotReference,
    PatternEvidence,
    PatternEvidenceBundle,
    PatternEvidenceDescriptor,
    PatternEvidenceResultState,
    PatternInstrumentIdentity,
    ProductLifecycleStatus,
    SnapshotMediaType,
    SourceFactSnapshot,
)


_DATA_STATE_MAP = {
    PatternDataStatus.INSUFFICIENT_HISTORY: (
        PatternEvidenceResultState.INSUFFICIENT_HISTORY
    ),
    PatternDataStatus.DATA_UNAVAILABLE: PatternEvidenceResultState.DATA_UNAVAILABLE,
    PatternDataStatus.DATA_QUALITY_BLOCKED: (
        PatternEvidenceResultState.DATA_QUALITY_BLOCKED
    ),
}
_LIFECYCLE_MAP = {
    LifecycleState.CONFIRMED: ProductLifecycleStatus.CONFIRMED,
    LifecycleState.INVALIDATED: ProductLifecycleStatus.INVALIDATED,
    LifecycleState.EXPIRED: ProductLifecycleStatus.EXPIRED,
}


def _fact(item: EvidenceFact) -> EvidenceFactSnapshot:
    return EvidenceFactSnapshot(
        code=item.code,
        value=item.value,
        available_from=item.available_from,
        available_from_session_ordinal=item.available_from_session_ordinal,
        source_ids=item.source_ids,
    )


def _facts(*groups: tuple[EvidenceFact, ...]) -> tuple[EvidenceFactSnapshot, ...]:
    values: dict[tuple[str, int, tuple[str, ...]], EvidenceFactSnapshot] = {}
    for group in groups:
        for item in group:
            key = (
                item.code,
                item.available_from_session_ordinal,
                item.source_ids,
            )
            snapshot = _fact(item)
            if key in values and values[key] != snapshot:
                raise ValueError("Conflicting evidence facts share one stable identity")
            values[key] = snapshot
    return tuple(values[key] for key in sorted(values))


def _source(item: SourceFactReference) -> SourceFactSnapshot:
    return SourceFactSnapshot(
        source_type=item.source_type.value,
        source_id=item.source_id,
        available_from=item.available_from,
        available_from_session_ordinal=item.available_from_session_ordinal,
    )


def _confirmation(
    item: ConfirmationAssessment,
    *,
    candidate_facts: tuple[EvidenceFact, ...] = (),
) -> ConfirmationEvidenceSnapshot:
    return ConfirmationEvidenceSnapshot(
        state=item.state.value,
        reason=item.reason,
        observed_on=item.observed_on,
        observed_session_ordinal=item.observed_session_ordinal,
        facts=_facts(candidate_facts, item.facts),
    )


def _invalidation(item: InvalidationAssessment) -> EvidenceInvalidationSnapshot:
    return EvidenceInvalidationSnapshot(
        invalidated=item.invalidated,
        condition=item.condition,
        reason=item.reason,
        observed_on=item.observed_on,
        observed_session_ordinal=item.observed_session_ordinal,
        facts=_facts(item.facts),
    )


class PatternEvidenceAdapter:
    """Expose existing detector facts without granting downstream authority."""

    @staticmethod
    def instrument(
        core_input: PatternCoreInput,
        *,
        economic_asset_class: str,
    ) -> PatternInstrumentIdentity:
        return PatternInstrumentIdentity(
            instrument_id=core_input.instrument_id,
            symbol=core_input.symbol,
            con_id=core_input.con_id,
            isin=core_input.isin or None,
            market=core_input.market,
            economic_asset_class=economic_asset_class,
            currency=core_input.currency,
        )

    @staticmethod
    def from_pattern_result(
        core_input: PatternCoreInput,
        result: PatternResult,
        *,
        economic_asset_class: str,
        parameter_hash: str,
        snapshot_uri: str | None = None,
        snapshot_media_type: SnapshotMediaType | None = None,
    ) -> PatternEvidenceBundle:
        candidate = result.candidate
        if (
            candidate.instrument_id != core_input.instrument_id
            or candidate.timeframe != core_input.timeframe
        ):
            raise ValueError("Pattern result does not match its canonical input")
        lifecycle = _LIFECYCLE_MAP.get(result.lifecycle.state)
        if lifecycle is None:
            raise ValueError("CANDIDATE is internal-only and cannot be product-visible")
        descriptor = PatternEvidenceDescriptor(
            candidate_id=candidate.candidate_id,
            pattern_type=candidate.pattern_type.value,
            pattern_family=candidate.pattern_family.value,
            direction=candidate.direction.value,
            lifecycle_status=lifecycle,
            formed_on=candidate.formed_on,
            available_from=candidate.available_from,
            evaluated_on=candidate.evaluated_on,
        )
        evidence = PatternEvidence(
            pattern=descriptor,
            structure_confirmation=_confirmation(
                result.structure_confirmation,
                candidate_facts=candidate.structure_facts,
            ),
            direction_confirmation=_confirmation(result.direction_confirmation),
            geometry=EvidenceGeometrySnapshot(
                pivots=tuple(_source(item) for item in candidate.source_pivots),
                boundaries=tuple(
                    _source(item) for item in candidate.source_boundaries
                ),
                facts=_facts(candidate.geometry_facts),
            ),
            invalidation=_invalidation(result.invalidation),
            provenance=EvidenceProvenance(
                provider="IBKR",
                source_bar_hash=core_input.source_bar_hash,
                candidate_source_bar_hash=candidate.source_bar_hash,
                detector_version=candidate.detector_version,
                indicator_layer_version=candidate.indicator_layer_version,
                calibration_version=candidate.calibration_version,
                parameter_set_id=candidate.parameter_set_id,
                parameter_hash=parameter_hash,
                detector_result_hash=result.result_hash,
            ),
        )
        return PatternEvidenceBundle(
            instrument=PatternEvidenceAdapter.instrument(
                core_input,
                economic_asset_class=economic_asset_class,
            ),
            timeframe="1d",
            result_state=PatternEvidenceResultState.PATTERN_FOUND,
            evidence=evidence,
            evidence_snapshot=EvidenceSnapshotReference(
                uri=snapshot_uri,
                media_type=snapshot_media_type,
            ),
        )

    @staticmethod
    def no_pattern(
        instrument: PatternInstrumentIdentity,
        *,
        reason: str = "no_user_visible_pattern_evidence",
    ) -> PatternEvidenceBundle:
        return PatternEvidenceBundle(
            instrument=instrument,
            timeframe="1d",
            result_state=PatternEvidenceResultState.NO_PATTERN,
            reason=reason,
        )

    @staticmethod
    def from_data_status(
        instrument: PatternInstrumentIdentity,
        status: PatternDataStatus,
        *,
        reason: str,
    ) -> PatternEvidenceBundle:
        if status is PatternDataStatus.READY:
            return PatternEvidenceAdapter.no_pattern(instrument, reason=reason)
        return PatternEvidenceBundle(
            instrument=instrument,
            timeframe="1d",
            result_state=_DATA_STATE_MAP[status],
            reason=reason,
        )

    @staticmethod
    def capture_engine_failure(
        instrument: PatternInstrumentIdentity,
        producer: Callable[[], PatternEvidenceBundle],
    ) -> PatternEvidenceBundle:
        """Fail open for callers while preserving an explicit ENGINE_ERROR state."""

        try:
            return producer()
        except Exception as exc:  # noqa: BLE001 - product boundary must degrade safely
            return PatternEvidenceBundle(
                instrument=instrument,
                timeframe="1d",
                result_state=PatternEvidenceResultState.ENGINE_ERROR,
                reason=f"pattern_engine_error:{type(exc).__name__}",
            )

    @staticmethod
    def parameter_hash_for(
        parameter_set_id: str,
        parameter_hashes: Mapping[str, str],
    ) -> str:
        """Require the frozen calibration hash; never invent a fallback."""

        try:
            return parameter_hashes[parameter_set_id]
        except KeyError as exc:
            raise ValueError("Pattern evidence requires a frozen parameter hash") from exc
