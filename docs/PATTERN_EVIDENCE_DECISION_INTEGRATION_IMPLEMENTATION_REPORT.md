# Pattern Evidence → Decision Integration Implementation Report

> Stage 2B · Read-only supporting-evidence transport · 2026-08-25

## A. Executive Conclusion

```text
PATTERN_DECISION_EVIDENCE_INTEGRATION_READY
READY_FOR_STAGE_2C_AI_EXPLANATION_INTEGRATION
```

Stage 2B adds an optional, bounded Pattern Evidence sidecar to the existing
Decision path.  An eligible resolved Decision can now carry one immutable
canonical evidence snapshot through `ExecutionOutput`, the current-turn `done`
event, and durable assistant-message metadata.  The sidecar is fail open for
Decision and fail closed for Pattern.  It does not alter Decision text,
ReviewingAgent authority, `actionable`, or any action/execution entity.

This conclusion means only that Pattern evidence can safely travel with a
Decision.  Pattern does not yet enter an AI prompt, has no UI, has no execution
authority, and is not Production Ready.

## B. Branch / HEAD / Commits

| Item | Value |
| --- | --- |
| Branch | `codex/pattern-evidence-decision-integration` |
| Stage 2A audit commit | `d329ca8ca6ae84d063f232e87e1ad755d2ca15ed` — `docs(technical-patterns): audit pattern decision integration` |
| Stage 2B start HEAD | `d329ca8ca6ae84d063f232e87e1ad755d2ca15ed` |
| Stage 2B implementation commit | This report's implementation commit — `feat(technical-patterns): integrate pattern evidence with decisions` |
| Push / merge / tag | `NO / NO / NO` |

The Stage 2A audit was committed separately before the implementation branch
was created.  Audit-only and implementation changes are not mixed.

## C. Architecture Implemented

```text
existing PlanningAgent
        ↓
existing ExecutingAgent + existing target resolution
        ↓ successful / non-aborted / eligible only
DecisionPatternEvidenceCollector
        ↓ one injected provider call per resolved symbol
tuple[PatternEvidenceBundle, ...]
        ↓ existing select_for_presentation
DecisionPatternEvidenceSnapshot
        ├── ExecutionOutput.pattern_evidence
        ├── SSE done.pattern_evidence
        └── ConversationMessage.metadata_json.pattern_evidence
        ↓
existing ExpressingAgent → ReviewingAgent → Decision
```

Single-symbol collection is inserted after target resolution and before
`ExpressingAgent`.  Explicit compare handling now resolves every requested
symbol first, applies one combined scope decision, collects once per symbol,
then starts per-symbol expression.  This prevents partial early fan-out when a
later compare target fails or aborts.

New owner:

- `backend/services/technical_patterns/decision_integration.py`: scope,
  provider protocol, collector, frozen snapshot, canonical serialization.

Modified hand-off and orchestration only:

- `backend/agents/contracts.py`
- `backend/services/decision_service_v3.py`

No detector, calibration, evidence-policy, frontend, database schema,
ActionDraft, ExecutionPlan, ExecutionBatch, order, or broker implementation was
changed.

## D. PatternInvocationScope

`PatternInvocationScope` is a typed enum with `NONE`, `SINGLE`, and `COMPARE`.

| Scope | Deterministic eligibility |
| --- | --- |
| `SINGLE` | `position_single`, exactly one existing Decision-resolved symbol, successful non-aborted execution, no Trade Intent/switch/multi-leg interpretation. |
| `COMPARE` | Explicit comparison language, exactly 2–3 unique successfully resolved symbols, all requested symbols resolved, no Trade Intent/switch/multi-leg interpretation. |
| `NONE` | Portfolio/general/clarify/low-confidence, abort/failure, same-operation `position_multi`, switch/multi-leg, unresolved identity, duplicates, or more than three compare symbols. |

`position_multi` is not treated as comparison by default.  A comparison with
more than three symbols is not truncated and invokes the provider zero times.
No scanner, scheduler, background, or portfolio-wide path calls the collector.

## E. Snapshot Contract

`DecisionPatternEvidenceSnapshot` is a frozen dataclass containing:

- schema version;
- invocation scope;
- requested symbols in request order;
- the complete canonical bundle tuple as source of truth;
- bundle hashes derived from canonical bundle content;
- confirmed-only Top 3 candidate IDs and remaining found candidate IDs derived
  by the existing Pattern presentation policy.

Snapshot construction validates scope cardinality, unique requested symbols,
bundle hash equality, and candidate-ID references.  Bundle ordering is stable
by requested-symbol order plus canonical bundle hash.  JSON pre-validation
uses `allow_nan=False`, rehydrates the JSON, and verifies every bundle hash.
The snapshot itself also exposes a deterministic `snapshot_hash`.

All six result states remain distinct:

```text
PATTERN_FOUND
NO_PATTERN
INSUFFICIENT_HISTORY
DATA_UNAVAILABLE
DATA_QUALITY_BLOCKED
ENGINE_ERROR
```

Partial valid evidence plus an `ENGINE_ERROR` outcome remains a multi-bundle
snapshot; one error does not erase other detector outcomes.

## F. Runtime Provider Status

```text
runtime six-detector provider activated = NO
```

The repository still has no governance-approved production calibration
assembly.  Stage 2B therefore exposes an injected
`DecisionPatternEvidenceProvider` protocol and installs a safe default
`UnavailableDecisionPatternEvidenceProvider`.  For an otherwise eligible
Decision, that default returns a canonical `DATA_UNAVAILABLE` bundle with the
sanitized reason `runtime_pattern_provider_not_promoted`.

No pilot/development calibration, BTC fallback, review-pack runtime import,
IBKR session, or public network call is activated by this implementation.

## G. Failure Isolation Results

Two fail-open layers are implemented and tested:

1. Provider boundary:
   - timeout/connection failure → `DATA_UNAVAILABLE`;
   - unexpected construction/collection error → sanitized `ENGINE_ERROR`;
   - typed provider states pass through unchanged;
   - partial valid + detector error outcomes are retained.
2. Decision sidecar boundary:
   - provider construction, collection, selection, snapshot construction,
     timeout, and serialization exceptions cannot escape into Decision;
   - the sidecar has an explicit 30-second Decision-level timeout;
   - an omitted Pattern snapshot leaves the existing flow untouched.

The end-to-end mocked Decision test proves `ExpressingAgent` and
`ReviewingAgent` still run, text completes, `actionable` is unchanged, and the
same snapshot reaches persistence and the `done` event.

## H. Persistence Results

The durable authority is the existing assistant
`ConversationMessage.metadata_json` field.  Metadata is assembled additively:

```json
{
  "trade_intent": "preserved when present",
  "pattern_evidence": "canonical DecisionPatternEvidenceSnapshot"
}
```

No migration or Pattern ORM table was added.  Existing Trade Intent metadata
survives the merge.  Prior messages are never rewritten.  Conversation text
does not contain raw Pattern JSON.  If Pattern-only serialization fails,
`pattern_evidence` is omitted while other metadata and the normal assistant
message remain eligible for persistence.  Existing message-persistence failure
handling also remains non-blocking for the streaming response.

## I. Current-turn Transport

Eligible single and compare `done` events contain an optional
`pattern_evidence` value.  The exact same already-serialized dictionary is
passed to message persistence, so current-turn transport and restored message
metadata have identical bundle content and hashes.  No frontend component was
added or changed.

## J. AI Boundary Proof

Stage 2B does not modify:

- `backend/agents/expressing_agent.py`;
- `decision_engine/llm_engine.py`;
- DecisionContext serialization;
- new-entry prompts;
- compare-summary prompts.

The targeted test asserts those prompt owners contain no `pattern_evidence`
consumption.  The sidecar snapshot is attached to `ExecutionOutput`, but no raw
bundle, state reason, geometry, or projection is added to model input.  The
existing `PatternAIContextAdapter` remains the sole governed future projection
owner for Stage 2C.

## K. Execution Authority Proof

The integration module imports no Action, ExecutionPlan, ExecutionBatch,
OrderManager, or Broker module.  Its contracts have no quantity, position size,
entry, stop, take-profit, leverage, limit-price, order-type, or execution-timing
field.  It never sets Decision status, `actionable`, or ReviewingAgent output.

Automated full-suite coverage passed for the existing Action, IBKR,
ExecutionBatch, Trade Intent, and Portfolio paths.  Runtime evidence collection
uses only an injected read-only provider boundary; the safe default performs no
external call.

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Public network attempts = 0
```

## L. Tests / Quality Gates

| Gate | Result |
| --- | --- |
| Pattern Decision Integration targeted | `33 passed` |
| Technical Pattern + Pattern Data | `274 passed` |
| Full pytest | `813 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS, 0 errors / 0 warnings |
| Frontend build | PASS; pre-existing non-blocking >500 kB chunk warning |
| Offline M5 | `18/18`, provider=`offline_fixture`, `public_network_attempts=0` |
| `git diff --check` | PASS |

The targeted suite covers invocation counts and exclusions, all six result
states, confirmation/direction/lifecycle preservation, deterministic hashes,
ranking ownership, partial detector failure, provider construction/connection/
timeout failure, selection/snapshot/serialization failure, metadata merge,
current-turn equality, message persistence failure, AI isolation, and execution
authority imports.

## M. Known Limitations

1. The production six-detector provider is intentionally not assembled or
   activated until an exact governance-approved runtime calibration exists.
2. Eligible Decisions therefore currently record `DATA_UNAVAILABLE`, not real
   Pattern findings, under the default provider.
3. Target extraction is conservative and uses only symbols already represented
   in WealthPilot's global `TICKER:MARKET` contract.  Unsupported market codes
   or incomplete legacy identities produce `NONE`; Stage 2B does not widen the
   global symbol architecture.
4. There is no Pattern Evidence UI and no historical refresh/mutation flow.
5. Pattern evidence does not enter AI reasoning until a separate Stage 2C
   implementation uses the existing governed projection adapter.

## N. Stage 2C Readiness

The transport, immutable snapshot, message authority, current-turn parity, and
failure isolation needed by Stage 2C are now present.  Stage 2C can consume only
`PATTERN_FOUND` bundles through the existing `PatternAIContextAdapter`, while
continuing to exclude raw bundles and non-found/error reasons from prompts.

Stage 2C must not be conflated with runtime provider promotion.  Production
Pattern findings remain blocked until the calibration/provider governance gate
is separately satisfied.

```text
PATTERN_DECISION_EVIDENCE_INTEGRATION_READY
READY_FOR_STAGE_2C_AI_EXPLANATION_INTEGRATION
NOT_PRODUCTION_READY
```
