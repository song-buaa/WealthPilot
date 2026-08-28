from __future__ import annotations

import ast
import asyncio
import inspect
import json
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from backend.agents.contracts import (
    AgentTaskStatus,
    ExecutionOutput,
    ExpressionOutput,
    PlanningOutput,
    ReviewOutput,
)
from backend.services.technical_patterns.core.identity import stable_hash
from backend.services.technical_patterns.detectors.contracts import (
    ConfirmationState,
    PatternType,
)
from backend.services.technical_patterns.evidence import (
    PatternEvidenceAdapter,
    PatternEvidenceBundle,
    PatternEvidenceResultState,
    PatternInstrumentIdentity,
    select_for_presentation,
)
from backend.services.technical_patterns import decision_integration as integration
from backend.services.technical_patterns.decision_integration import (
    DECISION_PATTERN_EVIDENCE_SNAPSHOT_SCHEMA_VERSION,
    DecisionPatternEvidenceCollector,
    DecisionPatternEvidenceSnapshot,
    PatternDecisionTarget,
    PatternInvocationScope,
    resolve_pattern_invocation_scope,
    serialize_pattern_evidence_snapshot,
    target_from_execution_output,
)
from backend.services import decision_service_v3
from decision_engine.llm_engine import LLMResult
from tests.technical_patterns.test_pattern_evidence_contract import _bundle


def _target(symbol: str = "AAPL", market: str = "US") -> PatternDecisionTarget:
    currency = "USD" if market == "US" else "HKD"
    return PatternDecisionTarget(
        requested_symbol=f"{symbol}:{market}",
        symbol=symbol,
        market=market,
        currency=currency,
        economic_asset_class="EQUITY",
    )


def _state_bundle(
    state: PatternEvidenceResultState,
    *,
    target: PatternDecisionTarget | None = None,
) -> PatternEvidenceBundle:
    target = target or _target()
    return PatternEvidenceBundle(
        instrument=target.unavailable_instrument,
        timeframe="1d",
        result_state=state,
        reason=f"test_{state.value.lower()}",
    )


class _RecordingProvider:
    def __init__(self, result_factory=None):
        self.calls: list[str] = []
        self.result_factory = result_factory or (
            lambda target: (_state_bundle(PatternEvidenceResultState.NO_PATTERN, target=target),)
        )

    def collect(self, target):
        self.calls.append(target.requested_symbol)
        return self.result_factory(target)


@pytest.mark.parametrize(
    ("route", "query", "targets", "trade_intent", "aborted", "expected"),
    (
        ("position_single", "分析 AAPL", (_target(),), None, False, PatternInvocationScope.SINGLE),
        ("portfolio", "分析组合", (_target(),), None, False, PatternInvocationScope.NONE),
        ("general", "什么是 ETF", (), None, False, PatternInvocationScope.NONE),
        ("clarify", "帮我看看", (), None, False, PatternInvocationScope.NONE),
        ("low_confidence", "看看", (), None, False, PatternInvocationScope.NONE),
        ("position_single", "分析 AAPL", (_target(),), None, True, PatternInvocationScope.NONE),
        ("position_single", "卖 AAPL 买 SPY", (_target(),), None, False, PatternInvocationScope.NONE),
        ("position_single", "分析 AAPL", (_target(),), object(), False, PatternInvocationScope.NONE),
    ),
)
def test_invocation_scope_single_and_exclusions(
    route, query, targets, trade_intent, aborted, expected
):
    assert resolve_pattern_invocation_scope(
        route=route,
        user_input=query,
        targets=targets,
        trade_intent=trade_intent,
        aborted=aborted,
        requested_symbol_count=len(targets),
    ) is expected


def test_explicit_compare_two_or_three_invokes_once_per_resolved_symbol():
    for targets in (
        (_target("AAPL"), _target("SPY")),
        (_target("AAPL"), _target("SPY"), _target("QQQ")),
    ):
        scope = resolve_pattern_invocation_scope(
            route="position_multi",
            user_input="比较 AAPL、SPY 和 QQQ，哪个更适合？",
            targets=targets,
            trade_intent=None,
            requested_symbol_count=len(targets),
        )
        provider = _RecordingProvider()
        snapshot = DecisionPatternEvidenceCollector(
            provider_factory=lambda: provider
        ).collect(scope, targets)

        assert scope is PatternInvocationScope.COMPARE
        assert provider.calls == [target.requested_symbol for target in targets]
        assert snapshot is not None
        assert snapshot.requested_symbols == tuple(
            target.requested_symbol for target in targets
        )


def test_more_than_three_compare_symbols_is_not_truncated_or_invoked():
    targets = tuple(_target(symbol) for symbol in ("AAPL", "SPY", "QQQ", "IWM"))
    scope = resolve_pattern_invocation_scope(
        route="position_multi",
        user_input="比较 AAPL、SPY、QQQ 和 IWM",
        targets=targets,
        trade_intent=None,
        requested_symbol_count=4,
    )
    provider = _RecordingProvider()
    snapshot = DecisionPatternEvidenceCollector(
        provider_factory=lambda: provider
    ).collect(scope, targets)

    assert scope is PatternInvocationScope.NONE
    assert snapshot is None
    assert provider.calls == []


def test_position_multi_is_not_implicitly_compare_and_partial_resolution_is_none():
    targets = (_target("AAPL"), _target("SPY"))
    assert resolve_pattern_invocation_scope(
        route="position_multi",
        user_input="分析 AAPL 和 SPY",
        targets=targets,
        trade_intent=None,
        requested_symbol_count=2,
    ) is PatternInvocationScope.NONE
    assert resolve_pattern_invocation_scope(
        route="position_multi",
        user_input="比较 AAPL、SPY 和 QQQ",
        targets=targets,
        trade_intent=None,
        requested_symbol_count=3,
    ) is PatternInvocationScope.NONE


def test_compare_switch_or_trade_intent_is_none():
    targets = (_target("AAPL"), _target("SPY"))
    assert resolve_pattern_invocation_scope(
        route="position_multi",
        user_input="比较后卖出 AAPL 买入 SPY",
        targets=targets,
        trade_intent=None,
        requested_symbol_count=2,
    ) is PatternInvocationScope.NONE
    assert resolve_pattern_invocation_scope(
        route="position_multi",
        user_input="比较 AAPL 和 SPY",
        targets=targets,
        trade_intent=object(),
        requested_symbol_count=2,
    ) is PatternInvocationScope.NONE


def test_execution_target_uses_only_resolved_position_identity():
    position = SimpleNamespace(
        symbol="AAPL:US",
        ticker="AAPL",
        asset_class="权益",
    )
    exec_out = ExecutionOutput(
        loaded_data=SimpleNamespace(target_position=position),
        market_data=SimpleNamespace(
            symbol="AAPL:US",
            quote=SimpleNamespace(symbol="AAPL:US", currency="USD"),
        ),
    )
    target = target_from_execution_output(exec_out)

    assert target == _target()
    assert target_from_execution_output(ExecutionOutput()) is None


def test_execution_output_optional_contract_is_backward_compatible():
    output = ExecutionOutput()
    assert output.pattern_evidence is None
    assert output.status is AgentTaskStatus.PENDING


@pytest.mark.parametrize(
    "failure",
    (TimeoutError("timeout"), ConnectionError("offline")),
)
def test_provider_timeout_and_connection_failure_are_data_unavailable(failure):
    class Provider:
        def collect(self, target):
            raise failure

    snapshot = DecisionPatternEvidenceCollector(
        provider_factory=Provider
    ).collect(PatternInvocationScope.SINGLE, (_target(),))

    assert snapshot is not None
    assert snapshot.bundles[0].result_state is PatternEvidenceResultState.DATA_UNAVAILABLE
    assert snapshot.bundles[0].reason == "provider_unavailable"


def test_provider_construction_and_unexpected_collection_failures_are_sanitized():
    def fail_factory():
        raise RuntimeError("secret construction detail")

    construction = DecisionPatternEvidenceCollector(
        provider_factory=fail_factory
    ).collect(PatternInvocationScope.SINGLE, (_target(),))

    class Provider:
        def collect(self, target):
            raise ValueError("secret adapter detail")

    collection = DecisionPatternEvidenceCollector(
        provider_factory=Provider
    ).collect(PatternInvocationScope.SINGLE, (_target(),))

    assert construction is not None and collection is not None
    assert construction.bundles[0].result_state is PatternEvidenceResultState.ENGINE_ERROR
    assert construction.bundles[0].reason == "provider_construction_error"
    assert collection.bundles[0].result_state is PatternEvidenceResultState.ENGINE_ERROR
    assert collection.bundles[0].reason == "collection_error"


@pytest.mark.parametrize(
    "returned",
    (
        (),
        (_state_bundle(PatternEvidenceResultState.NO_PATTERN, target=_target("SPY")),),
    ),
)
def test_empty_or_cross_target_provider_output_fails_closed(returned):
    provider = _RecordingProvider(lambda target: returned)
    snapshot = DecisionPatternEvidenceCollector(
        provider_factory=lambda: provider
    ).collect(PatternInvocationScope.SINGLE, (_target(),))

    assert snapshot is not None
    assert snapshot.bundles[0].result_state is PatternEvidenceResultState.ENGINE_ERROR
    assert snapshot.bundles[0].reason == "collection_error"


def test_partial_valid_evidence_and_one_detector_error_are_all_retained():
    found_one = _bundle(PatternType.BREAKOUT, candidate_suffix="valid_one")
    found_two = _bundle(PatternType.RECTANGLE, candidate_suffix="valid_two")

    def detector_failure():
        raise RuntimeError("detector internal")

    failed = PatternEvidenceAdapter.capture_engine_failure(
        found_one.instrument,
        detector_failure,
    )
    provider = _RecordingProvider(
        lambda target: (found_one, failed, found_two)
    )
    snapshot = DecisionPatternEvidenceCollector(
        provider_factory=lambda: provider
    ).collect(PatternInvocationScope.SINGLE, (_target(),))

    assert snapshot is not None
    assert {item.result_state for item in snapshot.bundles} == {
        PatternEvidenceResultState.PATTERN_FOUND,
        PatternEvidenceResultState.ENGINE_ERROR,
    }
    assert len(snapshot.bundles) == 3


def test_all_six_result_states_round_trip_without_collapse():
    found = _bundle(
        PatternType.ASCENDING_TRIANGLE,
        candidate_suffix="pending",
        direction_state=ConfirmationState.PENDING,
    )
    bundles = (found,) + tuple(
        _state_bundle(state)
        for state in PatternEvidenceResultState
        if state is not PatternEvidenceResultState.PATTERN_FOUND
    )
    snapshot = DecisionPatternEvidenceSnapshot.from_bundles(
        PatternInvocationScope.SINGLE,
        ("AAPL:US",),
        bundles,
    )
    serialized = serialize_pattern_evidence_snapshot(snapshot)

    assert serialized is not None
    assert {item["result_state"] for item in serialized["bundles"]} == {
        state.value for state in PatternEvidenceResultState
    }
    found_value = next(
        item
        for item in serialized["bundles"]
        if item["result_state"] == "PATTERN_FOUND"
    )
    assert found_value["evidence"]["structure_confirmation"]["state"] == "confirmed"
    assert found_value["evidence"]["direction_confirmation"]["state"] == "pending"
    assert found_value["evidence"]["pattern"]["lifecycle_status"] == "CONFIRMED"
    for value, expected_hash in zip(
        serialized["bundles"], serialized["bundle_hashes"]
    ):
        assert stable_hash(json.loads(json.dumps(value))) == expected_hash


def test_snapshot_is_frozen_deterministic_and_hash_stable():
    bundles = (
        _bundle(PatternType.RECTANGLE, candidate_suffix="rectangle"),
        _bundle(PatternType.BREAKOUT, candidate_suffix="breakout"),
    )
    first = DecisionPatternEvidenceSnapshot.from_bundles(
        PatternInvocationScope.SINGLE, ("AAPL:US",), bundles
    )
    second = DecisionPatternEvidenceSnapshot.from_bundles(
        PatternInvocationScope.SINGLE, ("AAPL:US",), tuple(reversed(bundles))
    )

    assert first.snapshot_schema_version == DECISION_PATTERN_EVIDENCE_SNAPSHOT_SCHEMA_VERSION
    assert first.as_dict() == second.as_dict()
    assert first.snapshot_hash == second.snapshot_hash
    with pytest.raises(FrozenInstanceError):
        first.invocation_scope = PatternInvocationScope.NONE


def test_existing_selection_policy_is_the_only_ranking_owner(monkeypatch):
    bundles = tuple(
        _bundle(PatternType.BREAKOUT, candidate_suffix=str(index))
        for index in range(4)
    )
    calls = []
    real_selection = select_for_presentation

    def spy(values, *, top_limit=3):
        calls.append(values)
        return real_selection(values, top_limit=top_limit)

    monkeypatch.setattr(integration, "select_for_presentation", spy)
    snapshot = DecisionPatternEvidenceSnapshot.from_bundles(
        PatternInvocationScope.SINGLE, ("AAPL:US",), bundles
    )

    assert len(calls) == 1
    assert len(snapshot.top_evidence_candidate_ids) == 3
    assert len(snapshot.remaining_evidence_candidate_ids) == 1


@pytest.mark.parametrize("boundary", ("selection", "snapshot"))
def test_selection_or_snapshot_failure_omits_sidecar_without_escaping(
    monkeypatch, boundary
):
    if boundary == "selection":
        monkeypatch.setattr(
            integration,
            "select_for_presentation",
            lambda values: (_ for _ in ()).throw(RuntimeError("selection")),
        )
    else:
        monkeypatch.setattr(
            DecisionPatternEvidenceSnapshot,
            "from_bundles",
            classmethod(lambda cls, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("snapshot"))),
        )

    result = DecisionPatternEvidenceCollector(
        provider_factory=lambda: _RecordingProvider()
    ).collect(PatternInvocationScope.SINGLE, (_target(),))

    assert result is None


def test_metadata_serialization_failure_omits_only_pattern(monkeypatch):
    snapshot = DecisionPatternEvidenceSnapshot.from_bundles(
        PatternInvocationScope.SINGLE,
        ("AAPL:US",),
        (_state_bundle(PatternEvidenceResultState.NO_PATTERN),),
    )
    monkeypatch.setattr(snapshot.__class__, "as_dict", lambda self: {"bad": float("nan")})

    assert serialize_pattern_evidence_snapshot(snapshot) is None

    intent = SimpleNamespace(model_dump=lambda mode: {"value": "intent"})
    metadata = decision_service_v3._assistant_metadata(intent, None)
    assert metadata == {"trade_intent": {"value": "intent"}}


def test_trade_intent_and_pattern_metadata_merge_and_done_transport_match():
    pattern = {"snapshot_schema_version": "v1", "bundle_hashes": ["abc"]}
    intent = SimpleNamespace(model_dump=lambda mode: {"value": "intent"})
    metadata = decision_service_v3._assistant_metadata(intent, pattern)
    plan = PlanningOutput(
        route="position_single",
        intent={"primary_intent": "PositionDecision"},
    )
    expression = ExpressionOutput(
        llm_result=LLMResult("HOLD", [], [], []),
        structured_payload={},
        raw_text="unchanged",
        actionable=False,
    )
    payload = decision_service_v3._build_done_payload(
        plan,
        expression,
        ReviewOutput(),
        "decision-test",
        pattern_evidence_metadata=pattern,
    )

    assert metadata == {
        "trade_intent": {"value": "intent"},
        "pattern_evidence": pattern,
    }
    assert payload["pattern_evidence"] is pattern
    assert payload["actionable"] is False


def test_decision_level_sidecar_construction_failure_is_fail_open(monkeypatch):
    class BrokenCollector:
        def __init__(self):
            raise RuntimeError("construction")

    monkeypatch.setattr(
        decision_service_v3,
        "DecisionPatternEvidenceCollector",
        BrokenCollector,
    )
    snapshot, metadata = asyncio.run(
        decision_service_v3._collect_decision_pattern_evidence(
            PatternInvocationScope.SINGLE,
            (_target(),),
        )
    )

    assert snapshot is None
    assert metadata is None


def test_message_persistence_failure_does_not_escape(monkeypatch):
    from backend.services import decision_service

    def fail_persistence(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(decision_service, "save_conversation_turn", fail_persistence)
    plan = PlanningOutput(
        route="general",
        intent={"primary_intent": "Education"},
    )
    result = asyncio.run(
        decision_service_v3._write_stores_v3(
            "conversation-test",
            "decision-test",
            plan,
            ExecutionOutput(),
            ExpressionOutput(chat_answer="normal answer"),
            user_input="question",
            pattern_evidence_metadata={"bundle_hashes": ["abc"]},
        )
    )

    assert result is None


def test_stage_2b_snapshot_never_becomes_an_llm_engine_parameter():
    from decision_engine import llm_engine

    reason_parameters = inspect.signature(llm_engine.reason).parameters
    compare_parameters = inspect.signature(llm_engine.compare_multi_assets).parameters
    assert "pattern_evidence" not in reason_parameters
    assert "pattern_evidence" not in compare_parameters
    assert "pattern_ai_context" in reason_parameters
    assert "pattern_ai_context" in compare_parameters


def test_integration_module_has_no_execution_authority_imports():
    tree = ast.parse(inspect.getsource(integration))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden_prefixes = (
        "backend.services.action",
        "backend.services.execution_plan",
        "backend.services.execution_batch",
        "backend.services.action.brokers",
    )

    assert not any(
        module.startswith(forbidden_prefixes) for module in imported_modules
    )


def test_default_runtime_provider_is_promoted_read_only_ibkr_provider(monkeypatch):
    from backend.services.technical_patterns.runtime_provider import (
        PromotedIBKRPatternEvidenceProvider,
    )

    monkeypatch.delenv("AV_DEV_MOCK", raising=False)
    provider = integration.build_runtime_pattern_evidence_provider()
    assert isinstance(provider, PromotedIBKRPatternEvidenceProvider)


def test_single_decision_completes_with_identical_persisted_and_done_snapshot(
    monkeypatch,
):
    from app.utils import position_aggregator
    from backend.services import decision_service

    persisted: list[dict | None] = []
    reviewed: list[ExecutionOutput] = []
    expressed: list[tuple[str, str]] = []

    class ArtifactFreeCollector:
        def collect(self, scope, targets):
            return DecisionPatternEvidenceSnapshot.from_bundles(
                scope,
                tuple(target.requested_symbol for target in targets),
                (_bundle(PatternType.BREAKOUT),),
            )

    class PlanningAgent:
        def run(self, *args, **kwargs):
            return PlanningOutput(
                status=AgentTaskStatus.COMPLETED,
                route="position_single",
                intent={
                    "primary_intent": "PositionDecision",
                    "asset": "Apple",
                    "confidence": 0.99,
                },
            )

    class ExecutingAgent:
        def run(self, *args, **kwargs):
            position = SimpleNamespace(
                name="Apple",
                ticker="AAPL",
                symbol="AAPL:US",
                asset_class="权益",
            )
            return ExecutionOutput(
                status=AgentTaskStatus.COMPLETED,
                loaded_data=SimpleNamespace(target_position=position),
                market_data=SimpleNamespace(
                    symbol="AAPL:US",
                    quote=SimpleNamespace(symbol="AAPL:US", currency="USD"),
                ),
            )

    class ExpressingAgent:
        last_output = None

        async def run_streaming(self, plan, execution, user_input, history):
            expressed.append((user_input, "answer unchanged"))
            self.last_output = ExpressionOutput(
                status=AgentTaskStatus.COMPLETED,
                llm_result=LLMResult(
                    "HOLD", ["existing reason"], ["existing risk"], []
                ),
                chat_answer="answer unchanged",
                raw_text="answer unchanged",
                structured_payload={"decisionType": "hold"},
                actionable=False,
            )
            yield "answer unchanged"

    class ReviewingAgent:
        def run(self, plan, execution, expression, user_input):
            reviewed.append(execution)
            return ReviewOutput(
                status=AgentTaskStatus.COMPLETED,
                action="pass",
            )

    def save_turn(*args, **kwargs):
        persisted.append(kwargs.get("assistant_metadata"))
        return 42

    monkeypatch.setattr(decision_service_v3, "get_planning_agent", PlanningAgent)
    monkeypatch.setattr(decision_service_v3, "get_executing_agent", ExecutingAgent)
    monkeypatch.setattr(decision_service_v3, "get_expressing_agent", ExpressingAgent)
    monkeypatch.setattr(decision_service_v3, "get_reviewing_agent", ReviewingAgent)
    monkeypatch.setattr(
        decision_service_v3,
        "DecisionPatternEvidenceCollector",
        ArtifactFreeCollector,
    )
    monkeypatch.setattr(decision_service_v3, "parse_trade_intent", lambda *args: None)
    monkeypatch.setattr(decision_service, "restore_conversation_context", lambda *args: None)
    monkeypatch.setattr(decision_service, "save_conversation_turn", save_turn)
    monkeypatch.setattr(
        position_aggregator,
        "aggregate_investment_positions",
        lambda *args: ([], 0.0),
    )

    async def run():
        return [
            event
            async for event in decision_service_v3.run_chat_stream_v3(
                "分析 AAPL",
                "conversation-pattern-test",
                conversation_history=[],
            )
        ]

    events = asyncio.run(run())
    done_event = next(event for event in events if event.startswith("event: done"))
    done_payload = json.loads(done_event.split("data: ", 1)[1])
    text = "".join(
        json.loads(event.split("data: ", 1)[1])["delta"]
        for event in events
        if event.startswith("event: text")
    )

    assert text == "answer unchanged"
    assert len(reviewed) == 1
    assert reviewed[0].pattern_evidence is not None
    assert done_payload["actionable"] is False
    assert done_payload["pattern_evidence"] == persisted[-1]["pattern_evidence"]
    assert expressed == [("分析 AAPL", "answer unchanged")]
    assert "pattern_evidence" not in text
