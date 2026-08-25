# Pattern Evidence → Decision Integration Read-only Audit

> Stage 2A · Read-only architecture audit · 2026-08-25
> Source branch: `codex/pattern-evidence-decision-integration-audit`
> Source HEAD: `1babc55ccb1d99d3fc7663eb13b4ab37f9bf5c59`

## A. Executive Conclusion

```text
READY_WITH_ARCHITECTURE_CHANGES
```

The current repository already has the hard parts of the governed evidence boundary:

- immutable `PatternEvidenceBundle` values and distinct result states;
- deterministic `PatternEvidenceAdapter` conversion;
- an existing `PatternAIContextAdapter` allowlist projection;
- deterministic confirmed-only Top 3 presentation selection;
- a generic assistant-message `metadata_json` snapshot field that can persist the bundle without a schema migration;
- a Decision pipeline in which data enrichment can be inserted before AI expression;
- explicit ActionDraft, ExecutionPlan, ExecutionBatch, Order, and Broker boundaries downstream of Decision.

It does **not** yet have a production Decision integration owner, an application-assembled six-detector Pattern provider, a typed invocation eligibility contract, or a fail-open wrapper around the full Pattern path. No current Decision object owns Pattern evidence, and no current AI or frontend contract consumes it. These are additive architecture gaps, not blockers in the frozen bundle or persistence model.

The target is one optional sidecar collection step after existing single-symbol data resolution and before `ExpressingAgent`. It returns a typed immutable Decision evidence snapshot, attaches that snapshot to `ExecutionOutput` as supporting evidence, persists the canonical snapshot on the assistant message, and never changes existing Decision status, review, `actionable`, or any execution input.

Six unambiguous answers for Stage 2B are:

| Question | Frozen answer |
| --- | --- |
| Where Pattern enters | In `backend/services/decision_service_v3.py`, after a successful non-aborted `ExecutingAgent` result and resolved target identity, before `ExpressingAgent`; the same bounded step is applied per eligible symbol inside explicit compare handling. |
| What is persisted | The complete canonical bundle collection, all attempted result states, bundle hashes, and the deterministic presentation selection, as immutable assistant-message metadata. |
| What AI can read | Only `PATTERN_FOUND` facts projected by the existing `PatternAIContextAdapter`; raw bundles and non-found/error reasons are not prompt input. AI use begins only in Stage 2C. |
| What Decision can use | Governed supporting/citation context only. Pattern cannot determine the Decision, actionability, numerical plan, or execution. |
| What happens when Pattern fails | The failure remains a distinct Pattern outcome or observable integration error; the existing Decision continues unchanged. |
| Where Pattern authority stops | At the Decision/message evidence snapshot and bounded AI explanation context. It does not cross into ActionDraft, ExecutionPlan, ExecutionBatch, OrderRecord, or Broker code. |

This verdict is integration readiness, **not** detector Production Promotion and not `PRODUCTION_READY`. The repository contains development/pilot calibration assembly and real-review tooling, but no application-level production provider assembly. Stage 2B must not silently treat development calibration constants as production-approved configuration.

## B. Current Decision Architecture

### B.1 Actual request and orchestration path

```text
POST /api/decision/chat
  backend/api/decision.py:37-76
        ↓
decision_service.run_chat_stream
  backend/services/decision_service.py:615-626
        ↓
run_chat_stream_v3
  backend/services/decision_service_v3.py
        ↓
PlanningAgent
        ↓
ExecutingAgent
        ↓
ExpressingAgent
        ↓
ReviewingAgent
        ↓
_write_stores_v3 + SSE done
```

For a normal single-symbol Decision, `ExecutingAgent` finishes at `decision_service_v3.py:221-263`. The resolved `target_position.name` is written back at lines 271-281, and `ExpressingAgent` begins at lines 283-293. That gap is the precise application-level insertion point.

For `position_multi`, `_handle_position_multi` loops through symbols and runs `ExecutingAgent` followed immediately by `ExpressingAgent` at `decision_service_v3.py:441-474`, then creates a comparative LLM summary at lines 500-566. An eligible compare integration must collect Pattern evidence after each successful per-symbol execution and before that symbol's expression.

### B.2 Current owners

| Concern | Current owner | Pattern support today |
| --- | --- | --- |
| Intent and route | `PlanningOutput` in `backend/agents/contracts.py`; `PlanningAgent` and `decision_graph.py` | No typed Pattern invocation scope. |
| Loaded data, rules, signals, market data | `ExecutionOutput` in `backend/agents/contracts.py:129-192` | No evidence field. |
| AI result and actionability | `ExpressionOutput`; `backend/agents/expressing_agent.py` | No Pattern context. `actionable` is derived only from `decisionType`. |
| In-memory explain result | legacy `DecisionResult` in `_DECISION_STORE` | No Pattern field; process-local and lost on restart. |
| Durable message snapshot | `ConversationMessage.metadata_json` | Generic JSON field already suitable. |
| Frontend explain | `ExplainData` and `ExplainPanel` | No Pattern field/card. |

No existing object owns Decision supporting evidence as a general typed collection. The smallest additive ownership is a frozen `DecisionPatternEvidenceSnapshot` value held by an optional `ExecutionOutput.pattern_evidence` field. `ExecutionOutput` is already the PEER hand-off from factual loading to expression; attaching a read-only value there does not grant execution authority.

The snapshot, rather than `decision_engine.decision_flow.DecisionResult`, should be authoritative. The latter is legacy in-memory explain state and `AGENTS.md` explicitly protects `decision_engine/` from unrelated core changes.

### B.3 Current symbol resolution

`ExecutingAgent._execute_position` delegates to `wp-load-context` / `decision_engine.data_loader.load`. `LoadedData.target_position` supplies a user-resolved name plus ticker/symbol/asset class for a holding or virtual new entry. This is adequate to decide *which requested symbol* is in scope, but it is not the final Pattern identity contract: it does not guarantee `conId`, ISIN, exchange, currency, or calendar identity.

The target Pattern provider must therefore:

1. consume only the symbol selected by the existing Decision path;
2. build a bounded `InstrumentQuery` from its resolved ticker/market/currency hints;
3. let `IBKRPatternDataAdapter` / `IBKRHistoricalDataSource` uniquely resolve ContractDetails;
4. fail closed to a non-blocking Pattern result when identity is ambiguous or incomplete;
5. never scan all portfolio positions to discover candidates.

## C. Current AI Context Flow

### C.1 Existing path

For an existing position, `ExpressingAgent._express_position_decision` calls `decision_engine.llm_engine.reason` with `LoadedData`, rule result, signals, history, and market data (`backend/agents/expressing_agent.py:391-445`). `llm_engine.reason` then:

1. builds a JSON payload in `_build_payload` (`decision_engine/llm_engine.py:1250+`);
2. builds and formats `DecisionContext` (`decision_engine/llm_engine.py:916-931`, `decision_engine/decision_context.py:399+`);
3. appends conversation content and the JSON payload to the model request (`llm_engine.py:936-959`).

The new-entry branch uses a separate direct prompt in `ExpressingAgent._express_new_entry` (`expressing_agent.py:469-528`) and must be handled explicitly in Stage 2C rather than assumed to share `llm_engine.reason`.

### C.2 Existing evidence and citation semantics

- `evidenceSources` in `llm_engine.py` is a flat model-produced enum limited to `profile`, `position`, `discipline`, `research`, `recent_records`, `news`, and `user_message`. It is not a typed factual evidence container and has no source hashes or lifecycle semantics.
- RAG knowledge citations live on `LoadedData`, are serialized into explain data, and are rendered by `KnowledgeCitations`. They describe documents, not technical geometry.
- `wp-citation-rules` is part of the declared Skill/prompt convention. It is not a reusable runtime Fact Guard adapter.
- No general `Fact Guard` implementation was found in the current production path. Stage 2B must not claim one exists or put raw detector values into an untyped citation string.

Therefore Pattern must not be squeezed into `evidenceSources` or `KnowledgeCitations`. It needs one additive typed context field while reusing the frozen Pattern projection policy.

### C.3 Frozen AI boundary

```text
DecisionPatternEvidenceSnapshot
        ↓ PATTERN_FOUND only
existing PatternAIContextAdapter.project(bundle)
        ↓ allowlisted PatternAIContext values
optional pattern_evidence_context in the LLM payload
        ↓
existing LLM reasoning
```

Raw geometry objects, rejected candidates, detector internals, exception text, calibration search state, and non-found/error reasons must not enter the prompt. Structure confirmation, direction confirmation, lifecycle status, source hashes, allowlisted facts, and the risk note remain separate in the projection.

Stage 2B should carry and persist the evidence but must not modify AI prompts. Stage 2C is the only stage that adds the optional projected context to both existing-position and approved new-entry AI paths.

## D. Current Persistence Flow

### D.1 Durable path

`_write_stores_v3` calls `save_conversation_turn` (`decision_service_v3.py:727-741`). The latter creates the user and assistant `ConversationMessage` rows and serializes optional assistant metadata into `metadata_json` (`decision_service.py:241-305`).

`ConversationMessage.metadata_json` is a nullable `Text` field specifically intended for structured message metadata (`app/models.py:343-354`). `/api/conversations/{conversation_id}/messages` decodes and returns the entire dictionary (`backend/api/conversations.py:151-177`). Trade Intent confirmation changes only `metadata["trade_intent"]` and preserves other keys (`backend/services/trade_intent/persistence.py:56-75`).

This makes message metadata a valid immutable v1 snapshot owner without a migration.

### D.2 Non-authoritative paths

- `_DECISION_STORE` and `_ALLOC_EXPLAIN_STORE` are in-memory explain caches. They are cleared on process restart and are not suitable as evidence authority.
- `DecisionHistory` stores only a thin long-term summary and truncated rationale. It is not a full message snapshot.
- Existing conversation history reconstruction passes role/content/intent/asset, not `metadata_json`, to the LLM. Persisting raw Pattern bundles therefore does not accidentally inject them into later prompts.

### D.3 Target metadata contract

The assistant message should own one additive key:

```json
{
  "trade_intent": "existing value when present",
  "pattern_evidence": {
    "snapshot_schema_version": "wp-decision-pattern-evidence-snapshot-v1",
    "invocation_scope": "SINGLE or COMPARE",
    "requested_symbols": ["..."],
    "bundles": ["PatternEvidenceBundle.as_dict() values, including non-found states"],
    "bundle_hashes": ["..."],
    "top_evidence_candidate_ids": ["..."],
    "remaining_evidence_candidate_ids": ["..."]
  }
}
```

`bundles` is the source of truth. Selection fields are deterministic derived presentation metadata and must reference, not duplicate or mutate, the bundles. Every emitted canonical outcome remains distinguishable. `NO_PATTERN` must never replace `ENGINE_ERROR`, and a collection-level success must not hide an emitted detector/provider failure. The frozen non-found bundle has no `pattern_type`; Stage 2B must not extend it ad hoc merely to label a failed detector.

The metadata assembly helper must merge keys; it must not replace existing Trade Intent metadata. Once the assistant row is written, Pattern evidence is immutable v1 history. A later refresh creates a new Decision/message snapshot rather than updating the prior one.

## E. Current Execution Authority Boundary

The current downstream path is deliberately user-triggered:

```text
Decision text + structured decisionType
        ↓ explicit UI action
POST /api/action/drafts/generate
  backend/api/action.py:140-177
        ↓
ActionDraft / SymbolStrategy
        ↓ explicit plan persistence or confirmation
POST /api/execution-plan/persist-draft
  backend/api/execution_plan.py:78+
or ExecutionBatch creation from confirmed Trade Intent
  backend/api/execution_batch.py:148+
        ↓ explicit human confirmation
OrderManager.place_order / ExecutionBatchService.submit_next_leg
        ↓
BrokerAdapter.place_order
```

Execution authority begins when explicit action/execution endpoints create or mutate ActionDraft, ExecutionPlan, ExecutionBatch, SymbolStrategy, OrderRecord, or call a Broker adapter. It does **not** begin in the Decision evidence sidecar.

The existing `actionable` flag is a presentation gate derived exclusively from the LLM `structured_payload.decisionType` in `backend/agents/expressing_agent.py:55-84`. Pattern collection must not set, override, promote, or suppress it.

Pattern authority is frozen to terminate at:

```text
assistant message Decision evidence snapshot
+ optional allowlisted AI explanation context
```

It must never supply `quantity`, position size, Entry, stop loss, take profit, leverage, order type, limit price, execution timing, or a broker instruction.

## F. Pattern Integration Entry Point

### F.1 One target flow

```text
User request
        ↓
existing PlanningAgent route and existing symbol resolution
        ↓
existing ExecutingAgent result
        ↓ successful, non-aborted, eligible scope only
DecisionPatternEvidenceCollector (new optional sidecar)
        ↓
Pattern provider
  ContractDetails + Historical Data + SCHEDULE
        ↓
IBKRPatternDataAdapter → CanonicalPatternSeries
        ↓
PatternCoreInput mapper → frozen six-detector framework
        ↓
PatternEvidenceAdapter → tuple[PatternEvidenceBundle, ...]
        ↓
DecisionPatternEvidenceSnapshot
  ├── full immutable bundle collection for persistence
  └── existing select_for_presentation for deterministic IDs
        ↓
ExecutionOutput.pattern_evidence (optional supporting facts)
        ↓
existing ExpressingAgent → ReviewingAgent → Decision
        ↓
assistant-message metadata snapshot
        ↓
existing downstream execution boundary remains unchanged
```

### F.2 Invocation eligibility

Target eligibility is deterministic:

| Request | Invoke? | Reason |
| --- | --- | --- |
| `position_single` with one explicit, successfully resolved symbol | Yes | Frozen v1 single-symbol scope. |
| Explicit comparison of 2-3 resolved symbols | Yes, once per symbol | Frozen v1 compare scope and hard maximum. |
| `position_multi` that means “same operation on many symbols” | No | It is not necessarily an explicit comparison. |
| Switch transaction / multi-leg Trade Intent | No | Frozen exclusion; Pattern cannot enrich execution intent. |
| More than 3 compared symbols | No | No truncation or silent partial fan-out. |
| PortfolioReview / AssetAllocation / PerformanceAnalysis | No | Portfolio-wide fan-out forbidden. |
| General/Education/clarify/low-confidence | No | No resolved explicit Decision symbol. |
| Scanner/scheduler/background job | No | Frozen v1 invocation excludes them. |

The current `position_multi` route is insufficient as the compare gate. `intent_engine/intent_recognizer.py` explicitly uses `multi_assets` for same-operation requests and the sell side of a switch, while `decision_graph.py` labels the downstream route “multi-asset comparison.” Stage 2B therefore needs a typed `PatternInvocationScope` (`NONE`, `SINGLE`, `COMPARE`) produced by a deterministic eligibility function. `COMPARE` requires explicit comparison language in the current user request, 2-3 unique resolved symbols, no Trade Intent, and no execution/multi-leg interpretation. It must not infer compare merely from `len(multi_assets) > 1`.

The collector runs only after the existing per-symbol `ExecutingAgent` succeeded. An aborted Decision must not trigger a Pattern call merely because a ticker appeared in the user text.

### F.3 Existing runtime gap

The repository has read-only IBKR Pattern data source/adapter, core mapper, detector framework, six detector implementations, calibration registries, evidence adapters, and real-review assembly. Searches found no application-level six-detector provider used by Decision; concrete framework construction currently lives in tests, calibration pilots, and `technical_patterns/real_review.py`.

Stage 2B must add an injected provider boundary rather than importing review-pack builders into Decision. The provider may only activate an exact governance-approved calibration registry. If such a registry is unavailable, the result is a non-blocking unavailable/configuration outcome; development/BTC/pilot fallbacks are forbidden.

## G. PatternAIContextAdapter Target

`PatternAIContextAdapter` already exists in:

```text
backend/services/technical_patterns/evidence/policy.py
```

It already:

- returns context only for `PATTERN_FOUND`;
- applies per-pattern fact allowlists;
- preserves pattern type, direction, lifecycle, structure state, and direction state;
- carries source and detector hashes;
- includes the governed risk note;
- emits no trading authority language.

It must remain the single projection owner. Stage 2B does not create or relocate it. Stage 2C calls it from the PEER/LLM adaptation boundary, then passes serialized `PatternAIContext` values through a new optional `pattern_evidence_context` parameter to the existing position prompt builder. The adapter does not belong in frontend code, the Decision router, or the raw IBKR adapter.

The new-entry direct prompt is a separate call site and must receive the same projection or explicitly remain Pattern-free; it must not bypass the adapter. Multi-symbol comparison summaries must receive only per-symbol projected context and only when `PatternInvocationScope.COMPARE` is eligible.

## H. Failure Isolation Design

The existing Decision succeeds today because it does not call Pattern. Inserting Pattern directly into `run_chat_stream_v3` without local isolation would be unsafe: the outer catch at `decision_service_v3.py:328-330` converts any uncaught exception into an `internal_error` SSE event.

Stage 2B needs two isolation layers:

1. **Provider boundary:** converts expected provider/data/detector/evidence outcomes into distinct `PatternEvidenceBundle` values.
2. **Decision sidecar boundary:** catches every exception escaping provider creation, timeout enforcement, collection, selection, and serialization; records an observable failure and returns an empty-safe/`ENGINE_ERROR` snapshot without modifying Decision status.

| Failure | Current component behavior | If naively inserted | Stage 2B isolation |
| --- | --- | --- | --- |
| IBKR connect/request timeout | `IBKRPatternDataAdapter` maps bounded source errors to `DATA_UNAVAILABLE`; source calls have timeouts | An assembly/constructor exception could escape | Apply an overall collector deadline; persist `DATA_UNAVAILABLE` with sanitized reason; Decision continues. |
| Pattern Data adapter quality error | Maps to `DATA_QUALITY_BLOCKED` | Safe only if result is respected | Preserve exact state and missing-session evidence internally; no AI context; Decision continues. |
| Insufficient closed history | Maps to `INSUFFICIENT_HISTORY` | Safe only if not raised downstream | Convert once and do not run detectors; Decision continues. |
| Detector/calibration error | Detector framework raises typed or runtime errors | Would reach outer Decision catch | Wrap each detector independently; retain valid results and emit an `ENGINE_ERROR` bundle with a sanitized detector-stage reason; continue other detectors and Decision. Missing exact approved calibration must never fall back. |
| Evidence adapter error | `capture_engine_failure` can already produce `ENGINE_ERROR` | Unsafe if caller bypasses it | Reuse `capture_engine_failure` per result; preserve other valid bundles. |
| Selection/ranking error | No Decision caller exists | Could block before expression | Catch in collector; persist raw valid bundles when possible, omit derived selection, log error; Decision continues. |
| AI context projection error | `PatternAIContextAdapter.project` can raise on malformed governed values | Stage 2C could block LLM request | Stage 2C catches per bundle and omits Pattern context; existing AI request continues. |
| Metadata canonicalization/JSON serialization error | `save_conversation_turn` would roll back both messages; `_write_stores_v3` then logs and continues | Decision succeeds but durable chat/evidence is lost | Validate/canonicalize Pattern metadata before message save. On Pattern-only failure omit Pattern metadata and log `pattern_snapshot_serialization_failed`; do not discard the existing chat message. |
| Message DB write failure | `_write_stores_v3` already logs and does not block SSE completion | Durable snapshot unavailable | Retain existing fail-open Decision behavior and emit structured operational logging; do not retry with mutations outside normal message persistence. |

In every case:

```text
ExecutionOutput.status unchanged
ExecutionOutput.aborted unchanged
ReviewingAgent input authority unchanged
ExpressionOutput.actionable unchanged
Decision text generation continues
Action/Execution entity count unchanged
```

Errors exposed in persisted Pattern metadata must be sanitized type/reason codes, not raw account data, credentials, or unbounded exception strings.

## I. Persistence Target

**Chosen owner:** the assistant `ConversationMessage.metadata_json` snapshot.

Reasons:

1. It is the only current durable per-turn snapshot that survives backend restart.
2. It already supports additive structured metadata without a schema migration.
3. The API returns it generically.
4. Trade Intent mutation preserves unrelated metadata keys.
5. It matches the Stage 1F rule: immutable Decision/message snapshot, not an independent Pattern lifecycle database.

The in-flight owner is `DecisionPatternEvidenceSnapshot`; `ExecutionOutput.pattern_evidence` carries it through the PEER turn. At write time it is canonicalized once into `metadata_json`. Pattern result timestamps and hashes reflect the evaluated closed-bar series; the message `created_at` is merely snapshot storage time.

Do not create:

- `Pattern`, `PatternCandidate`, or `PatternLifecycle` ORM tables;
- mutable “latest Pattern” rows;
- foreign keys from Pattern to ExecutionPlan or Order;
- a refresh job that rewrites prior messages;
- a duplicate chart-as-fact store.

## J. Result State Mapping

Every attempted result is persisted and observable. Only found evidence is a Decision/AI/UI evidence candidate.

| State | Persist canonical state | AI (Stage 2C) | Frontend contract | Product UI (Stage 2D) | Logging / observability | Decision behavior |
| --- | --- | --- | --- | --- | --- | --- |
| `PATTERN_FOUND` | Yes, full governed bundle + hash | Yes, allowlist projection only | Available as optional evidence metadata | Collapsed evidence card; confirmed-only Top 3 + remaining | Info/metrics by pattern/lifecycle | Continue with supporting context. |
| `NO_PATTERN` | Yes, reason and identity; no evidence/snapshot | No | State remains distinguishable in metadata | Silent | Info counter | Continue unchanged. |
| `INSUFFICIENT_HISTORY` | Yes | No | Distinguishable | Silent | Warning/counter with bounded reason | Continue unchanged. |
| `DATA_UNAVAILABLE` | Yes | No | Distinguishable | Silent | Warning/counter; timeout/provider category | Continue unchanged. |
| `DATA_QUALITY_BLOCKED` | Yes | No | Distinguishable | Silent | Warning/counter; quality category | Continue unchanged. |
| `ENGINE_ERROR` | Yes | No | Distinguishable | Silent | Error counter with sanitized exception type | Continue unchanged. |

“Frontend contract” does not mean automatic display. Stage 2B may expose all canonical states for truthful restoration and diagnostics; Stage 2D renders only governed `PATTERN_FOUND` evidence. The UI must not turn absence/error states into “no pattern.”

For six detectors, a successful all-six evaluation with zero visible evidence may emit one collection-level `NO_PATTERN`. If a provider emits an `ENGINE_ERROR` alongside partial valid evidence, aggregation must retain that error bundle and must not rewrite it as `NO_PATTERN`. The frozen non-found envelope is left unchanged rather than gaining an ungoverned `pattern_type` field.

## K. Ranking Ownership

**Single owner:** the Pattern Evidence policy layer:

```text
backend/services/technical_patterns/evidence/policy.py
  sort_pattern_evidence
  select_for_presentation
```

It already implements the frozen deterministic order:

1. lifecycle relevance;
2. structure-confirmation recency;
3. direction-confirmation state;
4. frozen Pattern type order;
5. stable candidate identity.

Only lifecycle `CONFIRMED` can enter `top_evidence`, capped at three. All other found bundles stay in `remaining_evidence`. Decision integration stores selection references; AI and frontend consume them. Neither the LLM, Decision adapter, nor frontend may independently sort, rank, select a “primary signal,” or infer payoff quality.

## L. Frontend Future Hook

### L.1 Current contract

- SSE supports `intent`, `stage`, `text`, `done`, `error`, `candidates`, and `trade_intent` (`frontend/src/lib/api.ts:1007-1010`).
- `done` supplies Decision conclusion/actionability but no evidence.
- `/decision/explain/{decision_id}` serializes process-local Decision data and has no evidence field.
- conversation message API already returns generic backend metadata, but the TypeScript `ConversationMessageDTO.metadata` currently types only `trade_intent` (`api.ts:386-393`).
- `ExplainPanel` renders intent, position, discipline, knowledge citations, web search, market signals, and analysis process (`frontend/src/pages/Decision.tsx:1477+`).

### L.2 Stage 2D hook

The future Pattern Evidence Card belongs in `ExplainPanel`, adjacent to other supporting evidence: after knowledge/web research and before the current “市场信号” block. It defaults collapsed and consumes the backend's deterministic selection without reranking.

`KnowledgeCitations` must not be reused as the Pattern card. It assumes document/file citations and semantic scores, while Pattern evidence has geometry, structure/direction confirmation, lifecycle, source hashes, and an optional static image. Stage 2D may reuse visual card/collapse primitives, not its data semantics.

Stage 2B should expose one optional typed `pattern_evidence` value from the same message snapshot for current-turn and restored-history APIs. A practical minimal transport is:

- persist on assistant message metadata;
- include the same snapshot in the current turn's `done` payload (or a dedicated non-streaming read endpoint keyed by message ID);
- keep `/conversations/{id}/messages` as restart/history authority.

Do not make the process-local explain cache the sole transport. Stage 2D can extend `SSEEvent`, `ConversationMessageDTO`, `Message`, and `ExplainPanel`; no frontend change belongs in Stage 2B.

## M. Reuse Matrix

| Need | Existing module | Reuse? | Gap / decision |
| --- | --- | --- | --- |
| Read-only IBKR history and identity | `backend/services/pattern_data/ibkr_source.py` | Yes | Must be application-assembled with bounded timeout/client ID; no order API. |
| Closed-bar canonical series and data states | `pattern_data/ibkr_adapter.py`, `pattern_data/contracts.py` | Yes | No change to adapter contract. |
| Pattern input mapping | `technical_patterns/core/input_mapper.py` | Yes | Provider must pass the exact canonical series. |
| Six detector framework | `technical_patterns/detectors/*` | Yes | No application-level six-detector aggregate currently exists. |
| Exact calibration lookup | `technical_patterns/calibration/registry.py` | Yes | No fallback. Stage 2B needs approved runtime assembly; development/pilot config is not production approval. |
| Product evidence conversion | `technical_patterns/evidence/adapter.py` | Yes | Use `capture_engine_failure` per detector/result. |
| Immutable evidence contract | `technical_patterns/evidence/contracts.py` | Yes | Do not create another Pattern bundle. |
| AI factual projection | `technical_patterns/evidence/policy.py::PatternAIContextAdapter` | Yes | Stage 2C call-site integration only. |
| Ranking/Top 3 | `technical_patterns/evidence/policy.py` | Yes | Sole ranking owner. |
| Per-turn persistence | `ConversationMessage.metadata_json`; `save_conversation_turn` | Yes | Needs additive merge/validation helper. No migration. |
| Generic metadata decode | `trade_intent/persistence.py::decode_message_metadata` | Yes | Name is trade-intent-specific but behavior is generic; reuse now, consider neutral rename only in a separate cleanup. |
| Optional provider failure style | market-data optional enrichment and Pattern data result contracts | Partial | Need a dedicated Pattern Decision sidecar because the outer Decision try/catch is otherwise blocking. |
| Decision evidence owner | None | No | Add one typed immutable snapshot and optional `ExecutionOutput` reference; do not build a generic parallel evidence platform. |
| Invocation eligibility | Planning route + resolved `LoadedData` | Partial | Need explicit SINGLE/COMPARE/NONE guard; `position_multi` alone is unsafe. |
| Runtime Pattern provider | Review/pilot assembly in `technical_patterns/real_review.py` | No direct reuse | Review tooling owns manifests/charts and is not a product provider. Reuse its underlying Core components, not the review orchestrator. |
| Structured Fact Guard | None found | No | The frozen `PatternAIContextAdapter` is the Pattern-specific factual guard; do not claim or duplicate a general framework. |
| Citation UI | `KnowledgeCitations` | Visual primitives only | Data contract is document-specific and unsuitable for Pattern evidence. |

The only justified new application module is the Decision integration/collector boundary (and, if kept separate, its runtime provider assembly). Existing Pattern contracts, adapters, policy, cache, detectors, and persistence field are reused unchanged.

## N. Stage 2B Target File Plan

This is a plan only; Stage 2A makes none of these source changes.

### CREATE

| File | Target responsibility | Why existing code is insufficient |
| --- | --- | --- |
| `backend/services/technical_patterns/decision_integration.py` | Frozen `PatternInvocationScope`, immutable `DecisionPatternEvidenceSnapshot`, provider Protocol, eligibility guard, fail-open collector, canonical metadata projection | No current Decision-side owner or invocation/failure boundary exists. It extends, rather than duplicates, `evidence/*`. |
| `backend/services/technical_patterns/runtime.py` | Read-only application assembly for canonical identity/data/Core/six detectors with dependency injection and exact approved calibration registry | Existing assembly is test/review/pilot-specific. Decision must not import `real_review.py` or calibration pilot workflows. If no approved runtime registry is available, this module remains fail-closed and non-blocking. |
| `tests/technical_patterns/test_pattern_decision_integration.py` | Eligibility, state mapping, persistence shape, timeout/failure isolation, authority and ranking regression tests | Existing tests prove Pattern contracts but not Decision integration semantics. |

If Stage 2B can keep runtime assembly behind an injected Provider without production activation, `runtime.py` may be deferred. It must not be replaced with imports from review-pack code.

### MODIFY

| File | Minimal change |
| --- | --- |
| `backend/agents/contracts.py` | Add optional typed `ExecutionOutput.pattern_evidence`; default `None` preserves every existing caller. |
| `backend/services/decision_service_v3.py` | Invoke the optional collector at the exact single/per-compare insertion points; merge Pattern and Trade Intent metadata; persist snapshot; expose current-turn snapshot without changing actionability/review. |
| `backend/services/decision_service.py` | Only if needed for a neutral metadata merge helper or durable Pattern snapshot read. Do not add Pattern to legacy `DecisionResult` authority. |
| `backend/services/technical_patterns/__init__.py` | Export only the public integration boundary if project import style requires it; otherwise no change. |

No schema migration is required. `backend/api/conversations.py` already passes generic metadata through and should remain unchanged unless Stage 2B adds explicit Pydantic response validation.

### DO_NOT_TOUCH

Stage 2B must not modify:

```text
decision_engine/ core contracts, rule engine, signal engine, or DecisionResult
backend/services/pattern_data/ canonical data contract/adapter behavior
backend/services/technical_patterns/core/
backend/services/technical_patterns/detectors/
backend/services/technical_patterns/calibration/ parameters or promotion state
backend/services/technical_patterns/evidence/contracts.py
backend/services/technical_patterns/evidence/adapter.py
backend/services/technical_patterns/evidence/policy.py ranking/projection semantics
frontend/ (reserved for Stage 2D)
backend/api/action.py
backend/services/action/
backend/api/execution_plan.py
backend/services/execution_plan/
backend/api/execution_batch.py
backend/services/execution_batch/
backend/services/trade_intent/ parsing, confirmation, and execution semantics
Portfolio models/services/sync
Broker adapters and order code
database schema and migrations
```

Stage 2C alone may make a separately reviewed additive change to AI payload/prompt construction. Stage 2B must not pre-emptively insert Pattern language into the model request.

## O. Stage 2B Acceptance Gates

### O.1 Invocation gates

1. One explicit successfully resolved symbol invokes exactly one bounded provider collection.
2. Explicit compare invokes 2-3 unique resolved symbols, once each.
3. More than three compare symbols produces no Pattern fan-out and does not truncate silently.
4. Same-operation `position_multi`, switch requests, and multi-leg Trade Intent invoke Pattern zero times.
5. Portfolio/general/clarify/low-confidence/aborted paths invoke Pattern zero times.
6. No scanner, scheduler, or background portfolio fan-out is introduced.

### O.2 State and causality gates

1. All six result states round-trip through message metadata unchanged.
2. `NO_PATTERN != ENGINE_ERROR`; no collection aggregation collapses them.
3. Structure confirmation, direction confirmation, and lifecycle status round-trip independently.
4. Rectangle remains `structure=confirmed`, `direction=not_required` where applicable.
5. Ascending Triangle, Double Top, and Double Bottom may remain `direction=pending` without being promoted.
6. Bundle hashes match canonical persisted content after reload.
7. Current/open daily bars remain excluded by the existing Pattern Data adapter.

### O.3 Failure-isolation gates

Parameterize Decision integration tests for:

```text
provider connection failure
provider timeout
DATA_QUALITY_BLOCKED
INSUFFICIENT_HISTORY
detector exception
missing exact calibration
evidence adapter exception
selection exception
metadata serialization exception
message persistence exception
```

For each case assert:

```text
existing Decision reaches its prior terminal result
AI call count/inputs remain valid for Stage 2B (no Pattern prompt data)
ReviewingAgent still runs when it did before
actionable equals the no-Pattern baseline
ActionDraft count unchanged
ExecutionPlan count unchanged
ExecutionBatch count unchanged
OrderRecord count unchanged
Broker mutation count = 0
```

### O.4 Persistence and API gates

1. Pattern metadata merges with an existing `trade_intent` key without loss.
2. Prior assistant messages are never rewritten by refresh/re-evaluation.
3. Backend restart/history read returns the same bundle hashes and states.
4. Conversation history supplied to the LLM still contains message text only; raw Pattern metadata is not injected.
5. Current-turn transport and restored-message transport serialize the same canonical snapshot.
6. Invalid Pattern serialization cannot roll back an otherwise valid assistant message.
7. No migration or new Pattern lifecycle table appears in the diff.

### O.5 Ranking and authority gates

1. `select_for_presentation` is the only ranking call.
2. Top evidence is confirmed-only and capped at three.
3. Remaining found evidence is retained.
4. Input ordering cannot change output selection.
5. No LLM call ranks evidence.
6. Pattern fields never populate action, size, Entry/SL/TP, leverage, limit, quantity, or broker parameters.
7. Import/call spies prove no Action, ExecutionPlan, ExecutionBatch, OrderManager, or Broker module is called by the Pattern sidecar.

### O.6 Regression gates

After targeted tests, run the project's mandatory offline gates with external providers disabled:

```text
python -m pytest
python -m compileall -q app backend decision_engine intent_engine research_v2 tests
frontend npm run lint
frontend npm run build
Offline M5 18/18
```

Compare ordinary single-symbol, portfolio, general, and Trade Intent outputs with a disabled/no-op Pattern provider baseline. Stage 2B passes only if Decision completion, actionability, and all execution records are unchanged except for additive evidence metadata on eligible requests.

## Final Audit Status

```text
PATTERN_DECISION_INTEGRATION_AUDIT_READY
READY_FOR_STAGE_2B_IMPLEMENTATION
```

This is not `PRODUCTION_READY`.

Safety evidence for this audit:

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB mutation = 0
Source code change = 0
Schema/migration change = 0
Network/Broker access = 0
```

Final principle:

> Pattern enriches a Decision. Pattern never owns the Decision.
