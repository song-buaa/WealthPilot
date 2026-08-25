# Pattern Evidence → AI Explanation Integration Report

> Stage 2C · Governed read-only explanation context · 2026-08-25

## A. Executive Conclusion

```text
PATTERN_AI_EXPLANATION_INTEGRATION_READY
READY_FOR_STAGE_2D_PATTERN_EVIDENCE_UI
```

The existing Decision explanation paths can now consume an optional, bounded
Pattern Evidence context. Only `PATTERN_FOUND` bundles selected by the Stage 2B
confirmed-only Top Evidence policy are eligible, and every eligible bundle is
projected by the already-frozen `PatternAIContextAdapter`. Pattern evidence can
improve wording, factual citations, and conflict descriptions; it cannot change
Decision type, `actionable`, ReviewingAgent authority, or execution state.

This conclusion does not promote the runtime provider or any Pattern family to
production. The default runtime outcome remains `DATA_UNAVAILABLE` and produces
no Pattern prompt section.

## B. Branch / HEAD / Commit

| Item | Value |
| --- | --- |
| Branch | `codex/pattern-evidence-ai-explanation` |
| Start HEAD | `69e2170feaa86f9b6fc4aa5da7b25401d5223338` |
| Stage 2C commit | This report's implementation commit — `feat(technical-patterns): add pattern evidence to ai explanations` |
| Push / merge / tag | `NO / NO / NO` |

The branch starts from the accepted Stage 2B implementation. No Stage 2B
snapshot, evidence-policy, detector, calibration, frontend, or persistence
contract is modified.

## C. Existing-position Integration

`ExpressingAgent._express_position_decision` projects the optional
`ExecutionOutput.pattern_evidence` snapshot before the LLM call. For compare
fan-out it filters the combined snapshot to the current Decision-resolved
symbol. It passes only `DecisionPatternAIContext` to
`decision_engine.llm_engine.reason`; the raw snapshot never becomes an
`llm_engine` argument.

`llm_engine.reason` appends the optional technical-evidence section to the
existing system prompt. If the context is absent or cannot be serialized, the
existing prompt and payload remain unchanged.

## D. New-entry Integration

The existing direct new-entry prompt receives the same already-projected
`DecisionPatternAIContext` produced by the parent PositionDecision path. It does
not independently read or project the raw snapshot. Its pre-existing
`decisionType=buy_init` and existing actionable rule remain unchanged; Pattern
facts are supporting explanation context only.

## E. Compare Integration

Pattern context reaches the compare-summary prompt only when the frozen Stage
2B scope is `COMPARE` (an explicit comparison of two or three fully resolved
symbols). The context envelope retains `requested_symbol` attribution and the
prompt explicitly forbids:

- merging facts across symbols;
- Pattern-based ranking, scoring, winner selection, or allocation;
- changing each symbol's existing Decision conclusion.

Per-symbol explanation fan-out receives only that symbol's projected evidence.
Non-found states are omitted independently.

## F. Prompt / Context Contract

The new `technical_patterns/ai_integration.py` module is deliberately thin:

```text
DecisionPatternEvidenceSnapshot
        ↓ confirmed-only Top Evidence IDs (existing Stage 2B policy)
PATTERN_FOUND bundles only
        ↓ existing PatternAIContextAdapter
PatternAIContext values
        ↓ symbol attribution + deterministic JSON formatting
existing explanation prompt
```

It does not define a second fact allowlist or projection policy. The serialized
fields are exactly the existing adapter output: identity, pattern type,
direction, lifecycle, structure/direction confirmation states and observation
dates, invalidation state/date, allowlisted facts, approved provenance hashes,
optional snapshot URI, and the governed risk note.

The prompt labels this as read-only supporting technical evidence and forbids
probability, win-rate, return, position-size, leverage, Entry/SL/TP, order, or
execution inference.

## G. Raw Bundle Exclusion Proof

Neither `reason` nor `compare_multi_assets` accepts a
`DecisionPatternEvidenceSnapshot`/`pattern_evidence` parameter. Deterministic
tests inspect the model messages and prove raw fields outside the adapter
allowlist are absent, including internal geometry noise, candidate lineage,
parameter/calibration versions, and provider state reasons. No raw detector
payload, exception detail, pilot data, or review metadata enters a prompt.

## H. Result-state Filtering

| Result state | AI context |
| --- | --- |
| `PATTERN_FOUND` + governed Top Evidence | Existing adapter projection |
| `NO_PATTERN` | None |
| `INSUFFICIENT_HISTORY` | None |
| `DATA_UNAVAILABLE` | None |
| `DATA_QUALITY_BLOCKED` | None |
| `ENGINE_ERROR` | None |

The default `runtime_pattern_provider_not_promoted` bundle therefore preserves
the exact pre-Stage-2C prompt behavior.

## I. Structure / Direction / Lifecycle Preservation

Tests freeze the following independent semantics:

- Rectangle: structure `confirmed`, direction `not_required`;
- Ascending Triangle: structure `confirmed`, direction may remain `pending`;
- Double Top: structure `confirmed`, direction may remain `pending`;
- Double Bottom: structure `confirmed`, direction remains `pending` until the
  existing governed neckline and volume gate confirms it;
- lifecycle values `CONFIRMED`, `INVALIDATED`, and `EXPIRED` serialize without
  promotion or reinterpretation.

The prompt explicitly states that `INVALIDATED`/`EXPIRED` are historical
technical facts—not current confirmation and not engine errors. Stage 2C v1
consumes the frozen confirmed-only Top 3 snapshot selection; lifecycle
serialization remains correct for any governed context passed to the formatter.

## J. Failure Isolation

Projection is isolated per selected bundle. A malformed bundle or adapter
exception skips that bundle without failing Decision. Context serialization
uses deterministic canonical JSON with `allow_nan=False`; any serialization
failure omits the entire optional section. Existing LLM timeout/error behavior
is unchanged.

## K. Decision Authority Regression

The implementation does not write any structured Decision field. In
particular, it does not change:

```text
Decision completion
Decision type
actionable
ReviewingAgent outcome
ActionDraft count
ExecutionPlan count
ExecutionBatch count
OrderRecord count
Broker mutation count
```

`_is_actionable` continues to depend only on the existing structured
`decisionType`. The compare and new-entry paths keep their existing Decision
semantics. No Action, Broker, Order, Portfolio, or database module is imported
by the Pattern AI integration boundary.

## L. Tests / Quality Gates

| Gate | Result |
| --- | --- |
| Stage 2C + Stage 2B + evidence targeted | `69 passed` |
| Technical Pattern + Expressing new-entry + LLM dispatch | `303 passed` |
| Full pytest | `835 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS, 0 errors / 0 warnings |
| Frontend build | PASS; pre-existing non-blocking >500 kB chunk warning |
| Offline M5 | `18/18`, provider=`offline_fixture`, `public_network_attempts=0` |
| `git diff --check` | PASS |

Normal automated tests use no live provider, public network, personal database,
or broker session.

## M. Known Limitations

- The production Pattern provider is intentionally not promoted. Default
  runtime decisions have no Pattern prompt section.
- Stage 2C uses only the existing confirmed-only Top 3 selection. Remaining
  evidence is not promoted into AI context.
- AI wording is nondeterministic; tests govern the structured prompt boundary,
  not arbitrary generated prose. No new post-generation censorship framework
  is introduced.
- There is no Pattern UI in this stage.

## N. Stage 2D Readiness

The explanation boundary is ready for a separate Stage 2D read-only UI task:

```text
PatternEvidenceBundle
        ↓ existing PatternAIContextAdapter
bounded factual context
        ↓
AI explanation
```

Stage 2D must continue to preserve the same evidence-policy owner, result-state
separation, lifecycle truth, and zero Decision/execution authority. Runtime
provider promotion remains a separate governance decision.

## Safety

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Decision integration authority change = 0
Public network attempts = 0
```
