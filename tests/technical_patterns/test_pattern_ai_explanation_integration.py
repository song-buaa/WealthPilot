from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.agents.contracts import ExecutionOutput, ExpressionOutput
from backend.agents.expressing_agent import ExpressingAgent, _is_actionable
from backend.services.technical_patterns.ai_integration import (
    DecisionPatternAIContext,
    PatternAIInstrumentContext,
    build_pattern_ai_context,
    format_pattern_ai_prompt_section,
)
from backend.services.technical_patterns.core.lifecycle import LifecycleState
from backend.services.technical_patterns.decision_integration import (
    DecisionPatternEvidenceSnapshot,
    PatternInvocationScope,
)
from backend.services.technical_patterns.detectors.contracts import (
    ConfirmationState,
    PatternType,
)
from backend.services.technical_patterns.evidence import (
    PatternAIContextAdapter,
    PatternEvidenceBundle,
    PatternEvidenceResultState,
)
from decision_engine import llm_engine
from tests.technical_patterns.test_pattern_evidence_contract import _bundle


def _snapshot(
    *bundles: PatternEvidenceBundle,
    scope: PatternInvocationScope = PatternInvocationScope.SINGLE,
) -> DecisionPatternEvidenceSnapshot:
    symbols = (
        ("AAPL:US",)
        if scope is PatternInvocationScope.SINGLE
        else ("AAPL:US", "SPY:US")
    )
    return DecisionPatternEvidenceSnapshot.from_bundles(
        scope,
        symbols,
        tuple(bundles),
    )


def _direct_context(bundle: PatternEvidenceBundle) -> DecisionPatternAIContext:
    projected = PatternAIContextAdapter.project(bundle)
    assert projected is not None
    return DecisionPatternAIContext(
        PatternInvocationScope.SINGLE,
        (PatternAIInstrumentContext("AAPL:US", (projected,)),),
    )


def test_found_top_evidence_uses_existing_adapter_allowlist_only():
    bundle = _bundle(PatternType.BREAKOUT)
    context = build_pattern_ai_context(_snapshot(bundle))
    section = format_pattern_ai_prompt_section(context)

    assert context is not None
    assert len(context.instruments[0].patterns) == 1
    assert '"boundary_axis"' in section
    assert '"break_close"' in section
    assert "internal_fit_noise" not in section
    assert "structure_confirmed" not in section
    assert "candidate_source_bar_hash" not in section
    assert "parameter_hash" not in section
    assert "detector_version" not in section
    assert "calibration_version" not in section


@pytest.mark.parametrize(
    "state",
    (
        PatternEvidenceResultState.NO_PATTERN,
        PatternEvidenceResultState.INSUFFICIENT_HISTORY,
        PatternEvidenceResultState.DATA_UNAVAILABLE,
        PatternEvidenceResultState.DATA_QUALITY_BLOCKED,
        PatternEvidenceResultState.ENGINE_ERROR,
    ),
)
def test_non_found_states_never_create_a_prompt_section(state):
    found = _bundle(PatternType.BREAKOUT)
    unavailable = PatternEvidenceBundle(
        instrument=found.instrument,
        timeframe="1d",
        result_state=state,
        reason="private-provider-detail-must-not-enter-prompt",
    )
    context = build_pattern_ai_context(_snapshot(unavailable))

    assert context is None
    assert format_pattern_ai_prompt_section(context) == ""


def test_selection_is_governed_confirmed_only_top_three_without_reranking():
    bundles = tuple(
        _bundle(PatternType.BREAKOUT, candidate_suffix=suffix)
        for suffix in ("one", "two", "three", "four")
    )
    snapshot = _snapshot(*bundles)
    context = build_pattern_ai_context(snapshot)

    assert context is not None
    candidate_hashes = {
        pattern.detector_result_hash
        for instrument in context.instruments
        for pattern in instrument.patterns
    }
    expected = {
        PatternAIContextAdapter.project(bundle).detector_result_hash
        for bundle in snapshot.bundles
        if bundle.evidence.pattern.candidate_id
        in snapshot.top_evidence_candidate_ids
    }
    assert len(candidate_hashes) == 3
    assert candidate_hashes == expected


@pytest.mark.parametrize(
    ("pattern_type", "direction_state", "direction_text"),
    (
        (PatternType.RECTANGLE, ConfirmationState.NOT_REQUIRED, "not_required"),
        (PatternType.ASCENDING_TRIANGLE, ConfirmationState.PENDING, "pending"),
        (PatternType.DOUBLE_TOP, ConfirmationState.PENDING, "pending"),
        (PatternType.DOUBLE_BOTTOM, ConfirmationState.PENDING, "pending"),
        (PatternType.DOUBLE_BOTTOM, ConfirmationState.CONFIRMED, "confirmed"),
    ),
)
def test_structure_and_direction_states_are_preserved_without_promotion(
    pattern_type, direction_state, direction_text
):
    bundle = _bundle(pattern_type, direction_state=direction_state)
    section = format_pattern_ai_prompt_section(_direct_context(bundle))

    assert '"structure_confirmation_state":"confirmed"' in section
    assert f'"direction_confirmation_state":"{direction_text}"' in section


@pytest.mark.parametrize(
    ("lifecycle_state", "expected"),
    (
        (LifecycleState.CONFIRMED, "CONFIRMED"),
        (LifecycleState.INVALIDATED, "INVALIDATED"),
        (LifecycleState.EXPIRED, "EXPIRED"),
    ),
)
def test_lifecycle_truth_is_retained(lifecycle_state, expected):
    bundle = _bundle(PatternType.ASCENDING_TRIANGLE, lifecycle_state=lifecycle_state)
    section = format_pattern_ai_prompt_section(_direct_context(bundle))

    assert f'"lifecycle_status":"{expected}"' in section
    assert "INVALIDATED/EXPIRED 是历史技术事实，不是当前有效确认" in section


def test_projection_and_serialization_fail_open(monkeypatch):
    bundle = _bundle(PatternType.BREAKOUT)
    snapshot = _snapshot(bundle)

    monkeypatch.setattr(
        PatternAIContextAdapter,
        "project",
        staticmethod(lambda _bundle: (_ for _ in ()).throw(ValueError("bad"))),
    )
    assert build_pattern_ai_context(snapshot) is None


def test_serialization_failure_omits_optional_section(monkeypatch):
    bundle = _bundle(PatternType.BREAKOUT)
    projected = PatternAIContextAdapter.project(bundle)
    assert projected is not None
    context = DecisionPatternAIContext(
        PatternInvocationScope.SINGLE,
        (PatternAIInstrumentContext("AAPL:US", (projected,)),),
    )
    monkeypatch.setattr(
        "backend.services.technical_patterns.ai_integration._context_payload",
        lambda _context: {"bad": float("nan")},
    )

    assert format_pattern_ai_prompt_section(context) == ""


def _capture_reason_messages(monkeypatch, pattern_context):
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "false")
    recorded: list[list[dict]] = []
    client = MagicMock()
    client.chat.completions.create.side_effect = lambda **kwargs: (
        recorded.append(kwargs["messages"])
        or SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=(
                            '{"decision":"HOLD","reasoning":[],"risk":[],'
                            '"strategy":[]}'
                        )
                    )
                )
            ]
        )
    )
    monkeypatch.setattr(llm_engine, "_get_client", lambda: client)
    monkeypatch.setattr(llm_engine, "_build_payload", lambda *a, **k: {"baseline": True})
    monkeypatch.setattr(llm_engine, "build_decision_context", lambda *a, **k: {})
    monkeypatch.setattr(llm_engine, "format_context_prompt", lambda _ctx: "BASE_CONTEXT")
    monkeypatch.setattr(llm_engine, "_extract_principles_prompt", lambda _data: "")

    llm_engine.reason(
        "分析 AAPL",
        SimpleNamespace(raw_portfolio=None),
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        [],
        None,
        pattern_context,
    )
    return recorded[0]


def test_existing_position_baseline_is_unchanged_and_found_context_is_additive(
    monkeypatch,
):
    baseline = _capture_reason_messages(monkeypatch, None)
    context = _direct_context(_bundle(PatternType.BREAKOUT))
    enriched = _capture_reason_messages(monkeypatch, context)

    assert baseline[0]["content"] + "\n\n" in enriched[0]["content"]
    assert baseline[-1] == enriched[-1]
    assert "技术形态证据（只读辅助上下文）" not in baseline[0]["content"]
    assert "技术形态证据（只读辅助上下文）" in enriched[0]["content"]


def test_new_entry_uses_same_projected_context_and_keeps_actionability(monkeypatch):
    captured: dict = {}
    client = MagicMock()

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="谨慎评估"))]
        )

    client.chat.completions.create.side_effect = create
    execution = ExecutionOutput()
    execution.loaded_data = SimpleNamespace(
        target_position=SimpleNamespace(name="苹果", ticker="AAPL"),
        av_fundamentals=None,
        total_assets=100_000,
        full_discipline_rules=None,
    )
    execution.rule_result = None
    output = ExpressionOutput()
    context = _direct_context(_bundle(PatternType.ASCENDING_TRIANGLE, direction_state=ConfirmationState.PENDING))

    with patch("intent_engine._llm_client.get_client", return_value=client), patch(
        "intent_engine._llm_client.MODEL_MAIN", "test-model"
    ):
        asyncio.run(
            _consume(
                ExpressingAgent()._express_new_entry(
                    output, execution, "AAPL 可以建仓吗", context
                )
            )
        )

    assert "技术形态证据（只读辅助上下文）" in captured["messages"][0]["content"]
    assert '"direction_confirmation_state":"pending"' in captured["messages"][0]["content"]
    assert output.structured_payload["decisionType"] == "buy_init"
    assert _is_actionable(output)[0] is True


async def _consume(generator):
    return [item async for item in generator]


def test_compare_context_keeps_symbol_attribution_and_forbids_pattern_ranking():
    aapl = _bundle(PatternType.BREAKOUT)
    spy_source = _bundle(PatternType.BREAKOUT, candidate_suffix="spy")
    spy = replace(
        spy_source,
        instrument=type(aapl.instrument)(
            instrument_id="IBKR:756733",
            symbol="SPY",
            market="US",
            economic_asset_class="EQUITY",
            con_id=756733,
            isin="US78462F1030",
            currency="USD",
        ),
    )
    snapshot = _snapshot(aapl, spy, scope=PatternInvocationScope.COMPARE)
    context = build_pattern_ai_context(snapshot)
    section = format_pattern_ai_prompt_section(context, compare=True)

    assert context is not None
    assert '"requested_symbol":"AAPL:US"' in section
    assert '"requested_symbol":"SPY:US"' in section
    assert "不得合并跨标的事实" in section
    assert "不得用形态证据做排序、打分、胜负判断或资金分配" in section

    aapl_only = build_pattern_ai_context(
        snapshot,
        requested_symbols=("AAPL:US",),
    )
    aapl_section = format_pattern_ai_prompt_section(aapl_only)
    assert '"requested_symbol":"AAPL:US"' in aapl_section
    assert '"requested_symbol":"SPY:US"' not in aapl_section


def test_compare_without_context_has_no_pattern_section(monkeypatch):
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="compare"))]
    )
    monkeypatch.setattr(llm_engine, "_get_client", lambda: client)

    llm_engine.compare_multi_assets("比较 AAPL 和 SPY", [{"name": "AAPL"}], {})
    messages = client.chat.completions.create.call_args.kwargs["messages"]

    assert "技术形态证据（只读辅助上下文）" not in messages[-1]["content"]


def test_ai_integration_does_not_change_decision_or_execution_authority():
    output = ExpressionOutput(structured_payload={"decisionType": "hold"})
    assert _is_actionable(output) == (False, None)
    output.structured_payload = {"decisionType": "buy_more"}
    assert _is_actionable(output)[0] is True
