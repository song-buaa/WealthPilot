from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from backend.services.pattern_data.contracts import PatternDataStatus
from backend.services.technical_patterns.core.contracts import (
    CorePatternBar,
    PatternCoreInput,
)
from backend.services.technical_patterns.core.lifecycle import (
    LifecycleSnapshot,
    LifecycleState,
)
from backend.services.technical_patterns.detectors.contracts import (
    ConfirmationAssessment,
    ConfirmationState,
    ConfirmationType,
    EvidenceFact,
    InvalidationAssessment,
    PatternCandidate,
    PatternDirection,
    PatternFamily,
    PatternResult,
    PatternType,
    SourceFactReference,
    SourceFactType,
)
from backend.services.technical_patterns.evidence import (
    PATTERN_VISIBILITY_POLICIES,
    PatternAIContextAdapter,
    PatternEvidenceAdapter,
    PatternEvidenceResultState,
    PatternInstrumentIdentity,
    ProductLifecycleStatus,
    select_for_presentation,
    sort_pattern_evidence,
)


_FAMILY = {
    PatternType.BREAKOUT: PatternFamily.LEVEL_BREAK,
    PatternType.BREAKDOWN: PatternFamily.LEVEL_BREAK,
    PatternType.RECTANGLE: PatternFamily.RANGE,
    PatternType.ASCENDING_TRIANGLE: PatternFamily.TRIANGLE,
    PatternType.DOUBLE_TOP: PatternFamily.REVERSAL,
    PatternType.DOUBLE_BOTTOM: PatternFamily.REVERSAL,
}
_DIRECTION = {
    PatternType.BREAKOUT: PatternDirection.BULLISH,
    PatternType.BREAKDOWN: PatternDirection.BEARISH,
    PatternType.RECTANGLE: PatternDirection.NEUTRAL,
    PatternType.ASCENDING_TRIANGLE: PatternDirection.BULLISH,
    PatternType.DOUBLE_TOP: PatternDirection.BEARISH,
    PatternType.DOUBLE_BOTTOM: PatternDirection.BULLISH,
}


def _core_input() -> PatternCoreInput:
    sessions = (date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6))
    return PatternCoreInput(
        instrument_id="IBKR:265598",
        con_id=265598,
        isin="US0378331005",
        symbol="AAPL",
        market="US",
        currency="USD",
        timezone="US/Eastern",
        timeframe="1d",
        adjustment_policy="IBKR_TRADES_SPLIT_ADJUSTED_DIVIDENDS_UNADJUSTED",
        calendar_version="IBKR_SCHEDULE_V1:test",
        last_closed_session=sessions[-1],
        source_bar_hash="a" * 64,
        dataset_version="dataset-v1",
        bars=tuple(
            CorePatternBar(
                session_date=session,
                session_ordinal=index,
                available_from=session,
                open=100.0 + index,
                high=102.0 + index,
                low=99.0 + index,
                close=101.0 + index,
                volume=1_000_000.0 + index,
                bar_id=f"bar_{index}",
            )
            for index, session in enumerate(sessions)
        ),
    )


def _result(
    pattern_type: PatternType,
    *,
    candidate_suffix: str = "one",
    lifecycle_state: LifecycleState = LifecycleState.CONFIRMED,
    direction_state: ConfirmationState | None = None,
) -> PatternResult:
    core = _core_input()
    formed, available, evaluated = (bar.session_date for bar in core.bars)
    source_type = (
        SourceFactType.BOUNDARY
        if pattern_type in {PatternType.BREAKOUT, PatternType.BREAKDOWN}
        else SourceFactType.PIVOT
    )
    source = SourceFactReference(source_type, "source_one", available, 1)
    geometry = (
        EvidenceFact("boundary_axis", 101.5, available, 1, (source.source_id,)),
        EvidenceFact("internal_fit_noise", 0.0001, available, 1, (source.source_id,)),
    )
    structure = (
        EvidenceFact("break_close", 103.0, available, 1, (source.source_id,)),
        EvidenceFact("structure_confirmed", True, available, 1, (source.source_id,)),
    )
    candidate_id = f"pat_{pattern_type.value}_{candidate_suffix}"
    candidate = PatternCandidate(
        candidate_id=candidate_id,
        instrument_id=core.instrument_id,
        timeframe="1d",
        pattern_family=_FAMILY[pattern_type],
        pattern_type=pattern_type,
        direction=_DIRECTION[pattern_type],
        formed_on=formed,
        formed_session_ordinal=0,
        available_from=available,
        available_from_session_ordinal=1,
        evaluated_on=evaluated,
        evaluation_session_ordinal=2,
        source_bar_hash="b" * 64,
        source_pivots=(source,) if source_type is SourceFactType.PIVOT else (),
        source_boundaries=(source,) if source_type is SourceFactType.BOUNDARY else (),
        geometry_facts=geometry,
        structure_facts=structure,
        direction_confirmation_required=pattern_type is not PatternType.RECTANGLE,
        expires_at_session_ordinal=None,
        detector_version="detector-v1",
        calibration_version="calibration-v1",
        parameter_set_id="parameters-v1",
        indicator_layer_version="indicator-v1",
    )
    structure_confirmation = ConfirmationAssessment(
        candidate_id,
        ConfirmationType.STRUCTURE,
        ConfirmationState.CONFIRMED,
        "confirmed_structure",
        available,
        1,
        (structure[-1],),
    )
    resolved_direction = direction_state or (
        ConfirmationState.NOT_REQUIRED
        if pattern_type is PatternType.RECTANGLE
        else ConfirmationState.CONFIRMED
    )
    direction_confirmation = ConfirmationAssessment(
        candidate_id,
        ConfirmationType.DIRECTION,
        resolved_direction,
        "direction_evidence_state",
        evaluated if resolved_direction is ConfirmationState.CONFIRMED else None,
        2 if resolved_direction is ConfirmationState.CONFIRMED else None,
    )
    invalidated = lifecycle_state is LifecycleState.INVALIDATED
    invalidation = InvalidationAssessment(
        candidate_id,
        "technical_structure_boundary_breach",
        invalidated,
        "later_closed_session_invalidated_structure" if invalidated else None,
        evaluated if invalidated else None,
        2 if invalidated else None,
    )
    lifecycle = LifecycleSnapshot(
        pattern_id=candidate_id,
        state=lifecycle_state,
        formed_on=formed,
        formed_session_ordinal=0,
        evaluation_session=evaluated,
        evaluation_session_ordinal=2,
        confirmed_on=(evaluated if lifecycle_state is LifecycleState.CONFIRMED else None),
        confirmed_session_ordinal=(
            2 if lifecycle_state is LifecycleState.CONFIRMED else None
        ),
        invalidated_on=(evaluated if invalidated else None),
        invalidated_session_ordinal=2 if invalidated else None,
        expired_on=(evaluated if lifecycle_state is LifecycleState.EXPIRED else None),
        expired_session_ordinal=(
            2 if lifecycle_state is LifecycleState.EXPIRED else None
        ),
    )
    return PatternResult(
        candidate,
        structure_confirmation,
        direction_confirmation,
        invalidation,
        lifecycle,
    )


def _bundle(
    pattern_type: PatternType,
    **result_kwargs,
):
    return PatternEvidenceAdapter.from_pattern_result(
        _core_input(),
        _result(pattern_type, **result_kwargs),
        economic_asset_class="EQUITY",
        parameter_hash="c" * 64,
    )


@pytest.mark.parametrize("pattern_type", tuple(PatternType))
def test_all_six_patterns_map_to_one_deterministic_bundle_contract(pattern_type):
    first = _bundle(pattern_type)
    second = _bundle(pattern_type)

    assert first.result_state is PatternEvidenceResultState.PATTERN_FOUND
    assert first.evidence is not None
    assert first.evidence.pattern.pattern_type == pattern_type.value
    assert first.evidence.pattern.lifecycle_status is ProductLifecycleStatus.CONFIRMED
    assert first.bundle_hash == second.bundle_hash
    assert first.as_dict() == second.as_dict()
    with pytest.raises(FrozenInstanceError):
        first.reason = "changed"


def test_structure_direction_and_lifecycle_remain_separate():
    rectangle = _bundle(PatternType.RECTANGLE)
    pending_triangle = _bundle(
        PatternType.ASCENDING_TRIANGLE,
        lifecycle_state=LifecycleState.INVALIDATED,
        direction_state=ConfirmationState.PENDING,
    )

    assert rectangle.evidence is not None
    assert rectangle.evidence.structure_confirmation.state == "confirmed"
    assert rectangle.evidence.direction_confirmation.state == "not_required"
    assert pending_triangle.evidence is not None
    assert pending_triangle.evidence.structure_confirmation.state == "confirmed"
    assert pending_triangle.evidence.direction_confirmation.state == "pending"
    assert (
        pending_triangle.evidence.pattern.lifecycle_status
        is ProductLifecycleStatus.INVALIDATED
    )


def _all_keys(value):
    if isinstance(value, dict):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value), set())
    return set()


def test_contract_contains_no_trading_or_execution_authority_fields():
    keys = _all_keys(_bundle(PatternType.BREAKOUT).as_dict())
    forbidden = {
        "entry",
        "stop_loss",
        "take_profit",
        "position_size",
        "leverage",
        "expected_return",
        "probability",
        "win_rate",
        "confidence",
        "action",
        "buy",
        "sell",
        "order",
        "broker_order_id",
    }

    assert keys.isdisjoint(forbidden)


def test_result_states_are_distinct_and_engine_failure_does_not_escape():
    instrument = PatternInstrumentIdentity(
        "IBKR:265598", "AAPL", "US", "EQUITY", 265598, "US0378331005", "USD"
    )
    results = {
        PatternEvidenceAdapter.no_pattern(instrument).result_state,
        PatternEvidenceAdapter.from_data_status(
            instrument,
            PatternDataStatus.INSUFFICIENT_HISTORY,
            reason="history_short",
        ).result_state,
        PatternEvidenceAdapter.from_data_status(
            instrument,
            PatternDataStatus.DATA_UNAVAILABLE,
            reason="provider_unavailable",
        ).result_state,
        PatternEvidenceAdapter.from_data_status(
            instrument,
            PatternDataStatus.DATA_QUALITY_BLOCKED,
            reason="missing_session",
        ).result_state,
    }

    def fail():
        raise RuntimeError("internal detail")

    captured = PatternEvidenceAdapter.capture_engine_failure(instrument, fail)
    results.add(captured.result_state)
    assert results == {
        PatternEvidenceResultState.NO_PATTERN,
        PatternEvidenceResultState.INSUFFICIENT_HISTORY,
        PatternEvidenceResultState.DATA_UNAVAILABLE,
        PatternEvidenceResultState.DATA_QUALITY_BLOCKED,
        PatternEvidenceResultState.ENGINE_ERROR,
    }
    assert captured.reason == "pattern_engine_error:RuntimeError"


def test_provenance_is_retained_and_snapshot_reference_is_optional():
    without_snapshot = _bundle(PatternType.DOUBLE_BOTTOM)
    with_snapshot = PatternEvidenceAdapter.from_pattern_result(
        _core_input(),
        _result(PatternType.DOUBLE_BOTTOM),
        economic_asset_class="EQUITY",
        parameter_hash="c" * 64,
        snapshot_uri="evidence://patterns/pat_double_bottom_one.svg",
        snapshot_media_type="image/svg+xml",
    )

    assert without_snapshot.evidence_snapshot.uri is None
    assert with_snapshot.evidence_snapshot.media_type == "image/svg+xml"
    assert with_snapshot.evidence is not None
    provenance = with_snapshot.evidence.provenance
    assert provenance.provider == "IBKR"
    assert provenance.source_bar_hash == "a" * 64
    assert provenance.candidate_source_bar_hash == "b" * 64
    assert provenance.parameter_hash == "c" * 64
    assert provenance.detector_version == "detector-v1"
    assert provenance.indicator_layer_version == "indicator-v1"
    assert provenance.calibration_version == "calibration-v1"


def test_internal_candidate_lifecycle_is_not_product_visible():
    with pytest.raises(ValueError, match="internal-only"):
        _bundle(PatternType.BREAKOUT, lifecycle_state=LifecycleState.CANDIDATE)


def test_ai_projection_is_allowlisted_and_failure_states_project_to_nothing():
    bundle = _bundle(PatternType.BREAKOUT)
    context = PatternAIContextAdapter.project(bundle)
    failure = PatternEvidenceAdapter.no_pattern(bundle.instrument)

    assert context is not None
    assert {item.code for item in context.facts} == {"boundary_axis", "break_close"}
    assert "not a recommendation" in context.risk_note
    assert PatternAIContextAdapter.project(failure) is None


def test_visibility_matrix_covers_six_patterns_without_primary_signal():
    assert {item.pattern_type for item in PATTERN_VISIBILITY_POLICIES} == {
        item.value for item in PatternType
    }
    assert all(item.workspace_visible for item in PATTERN_VISIBILITY_POLICIES)
    assert all(item.ai_context_allowed for item in PATTERN_VISIBILITY_POLICIES)
    assert all(item.decision_evidence_allowed for item in PATTERN_VISIBILITY_POLICIES)
    assert all(item.default_display == "collapsed" for item in PATTERN_VISIBILITY_POLICIES)
    assert all(item.risk_note_required for item in PATTERN_VISIBILITY_POLICIES)


def test_deterministic_order_and_confirmed_only_top_three():
    bundles = (
        _bundle(PatternType.DOUBLE_TOP, candidate_suffix="fifth"),
        _bundle(
            PatternType.BREAKOUT,
            candidate_suffix="invalidated",
            lifecycle_state=LifecycleState.INVALIDATED,
        ),
        _bundle(PatternType.RECTANGLE, candidate_suffix="third"),
        _bundle(PatternType.BREAKDOWN, candidate_suffix="second"),
        _bundle(PatternType.ASCENDING_TRIANGLE, candidate_suffix="fourth"),
    )
    ordered_once = sort_pattern_evidence(bundles)
    ordered_twice = sort_pattern_evidence(tuple(reversed(bundles)))
    selection = select_for_presentation(bundles)

    assert [item.bundle_hash for item in ordered_once] == [
        item.bundle_hash for item in ordered_twice
    ]
    assert len(selection.top_evidence) == 3
    assert all(
        item.evidence
        and item.evidence.pattern.lifecycle_status is ProductLifecycleStatus.CONFIRMED
        for item in selection.top_evidence
    )
    assert len(selection.remaining_evidence) == 2
