from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from backend.services.technical_patterns.calibration import (
    CalibrationKey,
    CalibrationRegistry,
    DetectorParameterSet,
)
from backend.services.technical_patterns.core import PatternInputMapper
from backend.services.technical_patterns.detectors import (
    CandidateProposal,
    ConfirmationAssessment,
    ConfirmationState,
    ConfirmationType,
    DetectorDescriptor,
    DetectorFramework,
    EvidenceFact,
    InvalidationAssessment,
    PatternDirection,
    PatternFamily,
    PatternType,
    SourceFactReference,
    SourceFactType,
)
from backend.services.technical_patterns.indicators import IndicatorSeries

from .conftest import canonical_series_from_case


def _core_input(count: int = 9):
    sessions = []
    current = date(2025, 2, 3)
    while len(sessions) < count:
        if current.weekday() < 5:
            sessions.append(current.isoformat())
        current += timedelta(days=1)
    highs = [110.0 + (index % 3) for index in range(count)]
    lows = [100.0 + (index % 2) for index in range(count)]
    series = canonical_series_from_case(
        {"instrument_id": "fixture:framework", "con_id": 101, "sessions": sessions, "highs": highs, "lows": lows},
        source_hash=f"source-{count}",
    )
    return PatternInputMapper().map_series(series)


def _key() -> CalibrationKey:
    return CalibrationKey("TEST", "EQUITY", "1d", "level_break", "breakout", "fixture-v1")


def _parameters() -> DetectorParameterSet:
    return DetectorParameterSet(_key(), (("threshold", 1.0),), minimum_history_bars=4)


class RecordingIndicatorLayer:
    def __init__(self):
        self.seen_bar_counts: list[int] = []

    def calculate(self, core_input, definitions):
        self.seen_bar_counts.append(len(core_input.bars))
        return IndicatorSeries(
            instrument_id=core_input.instrument_id,
            timeframe=core_input.timeframe,
            source_bar_hash=core_input.source_bar_hash,
            evaluation_session_ordinal=core_input.bars[-1].session_ordinal,
            layer_version="fixture-indicators-v1",
            backend_name="fixture",
            backend_version="1",
            definitions=definitions,
            columns=(),
        )


class FixtureDetector:
    descriptor = DetectorDescriptor(
        PatternFamily.LEVEL_BREAK,
        PatternType.BREAKOUT,
        PatternDirection.BULLISH,
        "fixture-detector-v1",
    )

    def __init__(self, proposals: tuple[CandidateProposal, ...]):
        self.proposals = proposals
        self.seen_bar_counts: list[int] = []

    def required_indicators(self, parameters):
        return ()

    def discover(self, context, parameters, indicators):
        self.seen_bar_counts.append(len(context.core_input.bars))
        return self.proposals


def _proposal(core_input, *, direction_required: bool = True, future_source: bool = False):
    source_ordinal = 6 if future_source else 2
    source_date = core_input.bars[source_ordinal].session_date
    available_ordinal = 4
    available_date = core_input.bars[available_ordinal].session_date
    return CandidateProposal(
        formed_session_ordinal=2,
        available_from_session_ordinal=available_ordinal,
        source_pivots=(
            SourceFactReference(SourceFactType.PIVOT, "pivot-1", source_date, source_ordinal),
        ),
        source_boundaries=(
            SourceFactReference(SourceFactType.BOUNDARY, "boundary-1", available_date, available_ordinal),
        ),
        geometry_facts=(
            EvidenceFact("close_margin_pct", 0.012, available_date, available_ordinal, ("bar-4", "boundary-1")),
        ),
        structure_facts=(
            EvidenceFact("boundary_valid", True, available_date, available_ordinal, ("boundary-1",)),
        ),
        direction_confirmation_required=direction_required,
        expires_at_session_ordinal=8,
    )


class ConfirmationEvaluator:
    def __init__(self, confirmation_type, state, ordinal=None):
        self.confirmation_type = confirmation_type
        self.state = state
        self.ordinal = ordinal

    def evaluate(self, context, candidate, parameters, indicators):
        observed = None
        if self.ordinal is not None:
            if self.ordinal < len(context.core_input.bars):
                observed = context.core_input.bars[self.ordinal].session_date
            else:
                observed = context.evaluation_session + timedelta(days=self.ordinal - context.evaluation_session_ordinal)
        facts = ()
        if self.ordinal is not None:
            facts = (EvidenceFact(
                f"{self.confirmation_type.value}_fact",
                True,
                observed,
                self.ordinal,
                ("future-bar" if self.ordinal >= len(context.core_input.bars) else context.core_input.bars[self.ordinal].bar_id,),
            ),)
        return ConfirmationAssessment(
            candidate_id=candidate.candidate_id,
            confirmation_type=self.confirmation_type,
            state=self.state,
            reason=f"fixture_{self.state.value}",
            observed_on=observed,
            observed_session_ordinal=self.ordinal,
            facts=facts,
        )


class InvalidationRule:
    def __init__(self, ordinal=None):
        self.ordinal = ordinal

    def evaluate(self, context, candidate, parameters, indicators):
        observed = context.core_input.bars[self.ordinal].session_date if self.ordinal is not None else None
        return InvalidationAssessment(
            candidate_id=candidate.candidate_id,
            condition="close_below_invalidation_boundary",
            invalidated=self.ordinal is not None,
            reason="fixture_close_below_boundary" if self.ordinal is not None else None,
            observed_on=observed,
            observed_session_ordinal=self.ordinal,
            facts=(
                EvidenceFact("invalidation_close", 99.0, observed, self.ordinal, (context.core_input.bars[self.ordinal].bar_id,)),
            ) if self.ordinal is not None else (),
        )


def _run(core_input, proposal, *, evaluation=7, structure_state=ConfirmationState.CONFIRMED,
         structure_ordinal=4, direction_state=ConfirmationState.CONFIRMED, direction_ordinal=5,
         invalidation_ordinal=None, indicator_layer=None):
    indicator_layer = indicator_layer or RecordingIndicatorLayer()
    framework = DetectorFramework(
        calibrations=CalibrationRegistry((_parameters(),)),
        indicators=indicator_layer,
    )
    detector = FixtureDetector((proposal,))
    result = framework.run(
        core_input,
        evaluation_session_ordinal=evaluation,
        calibration_key=_key(),
        detector=detector,
        structure_confirmation=ConfirmationEvaluator(ConfirmationType.STRUCTURE, structure_state, structure_ordinal),
        direction_confirmation=ConfirmationEvaluator(ConfirmationType.DIRECTION, direction_state, direction_ordinal),
        invalidation=InvalidationRule(invalidation_ordinal),
    )
    return result, detector, indicator_layer


def test_candidate_confirmation_and_lifecycle_are_deterministic_and_separate():
    core_input = _core_input()
    proposal = _proposal(core_input)

    first, _, _ = _run(core_input, proposal)
    second, _, _ = _run(core_input, proposal)
    pattern = first.results[0]

    assert pattern.candidate.candidate_id.startswith("pat_")
    assert pattern.structure_confirmation.confirmation_type is ConfirmationType.STRUCTURE
    assert pattern.direction_confirmation.confirmation_type is ConfirmationType.DIRECTION
    assert pattern.status == "confirmed"
    assert [item.to_state.value for item in pattern.lifecycle.transitions] == ["confirmed"]
    assert first.result_hash == second.result_hash
    assert pattern.result_hash == second.results[0].result_hash


def test_structure_confirmation_does_not_imply_direction_confirmation():
    core_input = _core_input()
    result, _, _ = _run(
        core_input,
        _proposal(core_input),
        direction_state=ConfirmationState.PENDING,
        direction_ordinal=None,
    )

    assert result.results[0].structure_confirmation.state is ConfirmationState.CONFIRMED
    assert result.results[0].direction_confirmation.state is ConfirmationState.PENDING
    assert result.results[0].status == "candidate"


def test_invalidation_is_technical_fact_and_wins_on_same_session():
    core_input = _core_input()
    result, _, _ = _run(
        core_input,
        _proposal(core_input),
        direction_ordinal=5,
        invalidation_ordinal=5,
    )

    pattern = result.results[0]
    assert pattern.status == "invalidated"
    assert pattern.invalidation.reason == "fixture_close_below_boundary"
    assert [item.to_state.value for item in pattern.lifecycle.transitions] == ["invalidated"]


def test_prefix_replay_ignores_future_bars_and_produces_same_result():
    full = _core_input()
    truncated = replace(
        full,
        bars=full.bars[:8],
        last_closed_session=full.bars[7].session_date,
        source_bar_hash="different-full-source-hash",
        dataset_version="different-full-source-hash",
    )
    full_layer = RecordingIndicatorLayer()
    short_layer = RecordingIndicatorLayer()

    full_result, full_detector, _ = _run(full, _proposal(full), indicator_layer=full_layer)
    short_result, short_detector, _ = _run(truncated, _proposal(truncated), indicator_layer=short_layer)

    assert full_result == short_result
    assert full_detector.seen_bar_counts == short_detector.seen_bar_counts == [8]
    assert full_layer.seen_bar_counts == short_layer.seen_bar_counts == [8]


def test_future_pivot_reference_is_ignored_with_auditable_rejection():
    core_input = _core_input()
    result, _, _ = _run(core_input, _proposal(core_input, future_source=True))

    assert result.results == ()
    assert len(result.rejected_candidates) == 1
    assert "future pivot or boundary" in result.rejected_candidates[0].reason


def test_direction_not_required_is_explicit_for_neutral_structure():
    core_input = _core_input()
    framework = DetectorFramework(
        calibrations=CalibrationRegistry((_parameters(),)),
        indicators=RecordingIndicatorLayer(),
    )
    result = framework.run(
        core_input,
        evaluation_session_ordinal=7,
        calibration_key=_key(),
        detector=FixtureDetector((_proposal(core_input, direction_required=False),)),
        structure_confirmation=ConfirmationEvaluator(ConfirmationType.STRUCTURE, ConfirmationState.CONFIRMED, 4),
        invalidation=InvalidationRule(),
    )

    assert result.results[0].direction_confirmation.state is ConfirmationState.NOT_REQUIRED
    assert result.results[0].status == "confirmed"


def test_framework_rejects_future_confirmation_fact():
    core_input = _core_input()
    result, _, _ = _run(
        core_input,
        _proposal(core_input),
        evaluation=6,
        direction_ordinal=7,
    )

    assert result.results == ()
    assert "future or pre-candidate" in result.rejected_candidates[0].reason


def test_confirmation_cannot_use_a_fact_that_arrives_after_confirmation_session():
    core_input = _core_input()

    class FutureEvidenceConfirmation:
        def evaluate(self, context, candidate, parameters, indicators):
            observed = context.core_input.bars[4].session_date
            future = context.core_input.bars[6]
            return ConfirmationAssessment(
                candidate_id=candidate.candidate_id,
                confirmation_type=ConfirmationType.STRUCTURE,
                state=ConfirmationState.CONFIRMED,
                reason="invalid_future_fact_fixture",
                observed_on=observed,
                observed_session_ordinal=4,
                facts=(EvidenceFact("future_fact", True, future.session_date, 6, (future.bar_id,)),),
            )

    framework = DetectorFramework(
        calibrations=CalibrationRegistry((_parameters(),)),
        indicators=RecordingIndicatorLayer(),
    )
    result = framework.run(
        core_input,
        evaluation_session_ordinal=7,
        calibration_key=_key(),
        detector=FixtureDetector((_proposal(core_input),)),
        structure_confirmation=FutureEvidenceConfirmation(),
        direction_confirmation=ConfirmationEvaluator(ConfirmationType.DIRECTION, ConfirmationState.CONFIRMED, 5),
        invalidation=InvalidationRule(),
    )

    assert result.results == ()
    assert "evidence unavailable" in result.rejected_candidates[0].reason


def test_detector_framework_has_no_provider_product_or_concrete_detector_coupling():
    package = Path(__file__).parents[2] / "backend/services/technical_patterns/detectors"
    production_modules = {path.name for path in package.glob("*.py")}
    forbidden_imports = (
        "from backend.services.pattern_data",
        "from backend.services.action",
        "from backend.services.portfolio",
        "from backend.services.decision",
        "import ib_async",
        "from ib_async",
        "import futu",
        "from futu",
        "import tigeropen",
        "from tigeropen",
    )

    assert production_modules == {"__init__.py", "contracts.py", "framework.py", "level_break.py", "parity.py"}
    for path in package.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert all(item not in source for item in forbidden_imports)
        assert "86400" not in source
        assert "timedelta(days=1)" not in source
