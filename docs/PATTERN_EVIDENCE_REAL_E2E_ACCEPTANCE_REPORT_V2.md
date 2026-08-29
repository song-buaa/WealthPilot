# Pattern Evidence Final Real E2E Acceptance Report V2

Date: 2026-08-29

Branch: `codex/pattern-final-e2e-rerun`

Start HEAD: `45e72fb44b3065785500b6604f3a6c49bd67d49b`

Runtime: current IBKR read-only historical data, real Decision/LLM, isolated SQLite acceptance database unless explicitly disclosed in section P

## A. Executive Conclusion

The remediated Pattern Evidence product path passed its functional acceptance gates:

```text
Current IBKR
→ approved runtime registry/provider
→ PatternEvidenceBundle
→ Decision / governed AI context
→ SSE
→ message metadata persistence
→ backend-process restart and history restoration
→ frontend rendering
```

Replay invariance, real single-symbol evidence, silent no-visible-pattern behavior, compare attribution, non-promoted-scope enforcement, failure isolation, AI allowlisting, SSE/persistence/reload parity, UI authority boundaries, Dataset v2 integrity, and all automated quality gates passed.

The final production verdict is nevertheless **blocked** because the acceptance procedure did not preserve the required `Production DB change = 0` safety invariant. During the Pattern-disabled control rerun, the server was restarted with `DATABASE_URL` instead of the repository's actual `WEALTHPILOT_DB_PATH` setting. This created one conversation and two messages in `data/wealthpilot.db`. Those exact rows were removed immediately and no pre-existing business row was changed, but the SQLite file SHA-256 changed. The incident and recovery are recorded in section P; it is not represented as a zero-write run.

No product-code defect was found and no product code was changed.

## B. Replay-equivalence Reconfirmation

The previous hard blocker is closed at the algorithm/runtime level.

- `tests/technical_patterns/test_pattern_window_invariance.py`: **12 passed**.
- Frozen real SPY source was replayed at the same `as_of=2026-08-29T08:00:00+00:00` through 300-bar and 1950-bar envelopes.
- Both inputs normalized to the same latest 300 closed sessions.
- Exact current visible detector result equality passed for:
  - pattern: Breakout
  - candidate ID: `pat_c84a64645a4a0f6eca18`
  - identity contract: `WP-PATTERN-CORE-IDENTITY-2.0`
  - formation / availability: `2026-08-04` / `2026-08-04`
  - lifecycle: `EXPIRED`
  - structure: `CONFIRMED`
  - direction: `PENDING`
  - invalidation: `false`
  - governed facts, detector result, and normalized source-bar hash: exact equality
- Representative cross-window coverage passed for all six Equity families plus all three promoted Fixed Income scopes.

Result: **PASS**.

## C. Runtime Registry State

The approved runtime registry remains exactly 9 of 12 scopes:

| Economic asset class | Promoted pattern types |
| --- | --- |
| EQUITY | Breakout, Breakdown, Rectangle, Ascending Triangle, Double Top, Double Bottom |
| FIXED_INCOME | Breakout, Ascending Triangle, Double Top |

The following scopes remain unpromoted and closed: Fixed Income Breakdown, Rectangle, and Double Bottom. No promotion manifest, calibration, or registry entry changed.

## D. Real IBKR Single-symbol PATTERN_FOUND

The real product Decision E2E used SPY and current IBKR daily history.

| Field | Observed value |
| --- | --- |
| Symbol / identity | `SPY:US` / IBKR conId `756733` |
| Evaluated closed session | `2026-08-28` |
| Pattern | Double Bottom |
| Candidate ID | `pat_cb3dabbf8b299d2ccf48` |
| Bundle hash | `07cf4e08c862e4f87984435f0c326b4574c7da2c6528a8e18683bf860f20f0d9` |
| Lifecycle | `CONFIRMED` |
| Structure state | `CONFIRMED` on `2026-07-31` |
| Direction state | `CONFIRMED` on `2026-08-03` |

The Decision completed as `trim`, `actionable=true`. The snapshot contained five SPY bundles; the confirmed Double Bottom was the sole Top item and the four historical/invalidated items remained in backend-provided Remaining order. The governed Pattern projection reached the LLM, SSE, persistence, reload, and UI.

## E. Real No-visible-Pattern Case

SHY produced a governed `NO_PATTERN` bundle with reason `no_user_visible_pattern_evidence`.

- Decision completed as `hold`, `actionable=false`.
- AI response completed.
- Top and Remaining were empty.
- The Pattern UI section was absent.
- No synthetic “No Pattern” card was rendered.
- No `ENGINE_ERROR` state was rewritten into `NO_PATTERN`.
- No action, execution, order, broker, or portfolio side effect occurred.

Result: **PASS**.

## F. Real Compare Acceptance

An explicit two-symbol SPY/SHY PositionDecision compare completed through the real product path.

- Scope: `COMPARE`.
- Requested symbols: `SPY:US`, `SHY:US`.
- Six bundles persisted: five SPY `PATTERN_FOUND` bundles followed by the independent SHY `NO_PATTERN` result.
- Top/Remaining order exactly matched the backend snapshot.
- SPY governed facts were projected independently; SHY contributed no false visible evidence.
- No cross-symbol fact merge, Pattern winner, ranking, allocation inference, or recommendation inference was introduced.
- Process restart and history reload restored the identical compare snapshot.

A three-symbol compare was not added because the task only required it when naturally stable; the explicit two-symbol path exercised the product's multi-symbol attribution contract without broadening the run.

Result: **PASS**.

## G. Non-promoted Scope Enforcement

The three Fixed Income gaps were exercised through the runtime-promotion and exact-registry regressions.

```text
exact FIXED_INCOME registry miss
→ provider not opened for the unpromoted scope
→ DATA_UNAVAILABLE / governed omission
→ Decision continues
```

The registry rejects cross-asset, cross-market, nearest-scope, Development, pilot, BTC/crypto, and wildcard fallback. `test_runtime_provider_does_not_open_ibkr_for_unpromoted_scope` passed, proving detector/provider execution is not reached as an approved scope.

Result: **PASS**.

## H. Failure Isolation

Controlled tests covered provider timeout, connection failure, Pattern data failure, per-detector exception, sidecar-construction failure, registry unavailable/disabled behavior, AI projection/serialization failure, and mixed valid/error bundles.

- 74 focused runtime-promotion, registry, Decision-sidecar, and AI-integration tests passed.
- Failures produce governed `DATA_UNAVAILABLE` or omit the optional AI/UI section.
- Decision completion remains fail-open.
- Per-target failures do not corrupt another target.
- Persistence remains readable and no Pattern failure gains execution authority.

Result: **PASS**.

## I. AI Explanation Acceptance

The real SPY LLM context contained only the approved Pattern projection:

- opaque candidate identity, type, direction, lifecycle;
- structure/direction dates and states;
- invalidation state;
- governed fact codes/values/source references;
- approved provider/detector provenance and the neutral evidence-only risk note.

It did not contain raw detector internals, calibration thresholds, debug geometry, probability, win rate, expected return, Entry/SL/TP, leverage, position size, or order instructions. No static evidence URI was supplied.

The equivalent Pattern-enabled and Pattern-disabled SPY decisions both remained `trim` and `actionable=true`; only explanatory supporting context differed. ReviewingAgent, Trade Intent, and execution authority were unchanged.

Result: **PASS**.

## J. SSE / Persistence / Reload Parity

For both the real SPY single-symbol decision and SPY/SHY compare, these representations were compared:

1. `done.pattern_evidence` in the SSE stream;
2. `ConversationMessage.metadata.pattern_evidence`;
3. message data returned after a backend-process restart;
4. the frontend evidence model after a page reload.

Schema version, candidate IDs, bundle hashes, Top IDs, Remaining IDs, symbol attribution, lifecycle, structure/direction states, and visible facts were identical. After restart, reading these messages caused zero additional IBKR Pattern reads.

Result: **PASS**.

## K. Real UI Acceptance

The real Decision UI at `http://127.0.0.1:5173/#/decision` was inspected with the persisted SPY and compare conversations.

- Pattern Evidence was collapsed by default.
- The expanded card showed pattern name, symbol, lifecycle, separate structure/direction confirmation, governed key facts, and the evidence-only note.
- Top/Remaining behavior followed backend IDs.
- Page reload restored the same candidate IDs and content.
- SHY remained silent and usable.
- Compare grouped visible evidence only under SPY; it did not create a SHY card.
- Pattern components exposed presentation controls only. No Buy, Sell, Trade, Execute, Copy, Create Order, or broker CTA existed.

Playwright Pattern Evidence UI regression: **6 passed**.

Result: **PASS**.

## L. Identity v2 Compatibility

- New runtime evidence uses `WP-PATTERN-CORE-IDENTITY-2.0` and stable date/session plus structure-anchor identity.
- Historical snapshots remain JSON-readable through the same persistence/frontend contract.
- Existing candidate IDs are treated as opaque strings and are not rewritten.
- Equality between v1 and v2 candidate IDs is neither assumed nor required.
- No database migration is required.

Result: **PASS**.

## M. Dataset v2 Integrity

Dataset v2 remained evaluation authority only and was not used as current runtime input.

| Integrity item | Before and after value |
| --- | --- |
| Logical dataset manifest hash | `032c71380c775b4901c8ae73e1d1c730facfa41e032df8df30a413dad98dc12c` |
| Dataset manifest file SHA-256 | `b35f449834c5af43ca039b5678ef3fa0607845438cdb3a83b0871c36a57a6192` |
| Runtime validation manifest file SHA-256 | `00bc6ed04f5d91e6cd1bc480a9b2b757585f2633d3098275d313b9f3edbaea36` |
| Combined artifact-hash stream SHA-256 | `8b705427a78b9544c10617d55f8faf33b3680fc9021c79db5130236cf4b277ca` |
| Instruments / bars | 17 / 1950 each |
| Partitions | Development 2019–2022; Holdout 2023–2024; Untouched 2025–2026-08-27 |

Immutable dataset tests: **5 passed**. Dataset write and recalibration counts: **0**.

Result: **PASS**.

## N. Runtime Latency

Representative successful direct provider collections were:

| Symbol | Result | Latency |
| --- | --- | ---: |
| AAPL | six `PATTERN_FOUND` bundles | 14.574 s |
| SHY | governed `NO_PATTERN` | 5.689 s |

Median/typical of these representative cases was 10.132 s; maximum observed successful collection was 14.574 s. The explicit two-symbol sidecar completed within its bounded execution path. No systematic 30-second Pattern-sidecar breach was observed.

Result: **PASS**.

## O. Decision / Execution Authority Regression

The same SPY request was run with the promoted runtime enabled and with the safe Pattern-disabled control (`AV_DEV_MOCK=1`):

| Mode | Decision type | Actionable | Pattern result |
| --- | --- | --- | --- |
| Enabled | `trim` | `true` | real SPY evidence |
| Disabled control | `trim` | `true` | `DATA_UNAVAILABLE: runtime_pattern_provider_not_promoted` |

In the correctly isolated control database, before/after counts were exactly zero for ActionDraft, AllocationIntent, SymbolStrategy, ExecutionPlan, ExecutionBatch, ExecutionLeg, and OrderRecord. Pattern Evidence did not create or mutate execution authority.

Result: **PASS**.

## P. Safety / IBKR Read Accounting

### IBKR Pattern source

All IBKR operations were read-only `ContractDetails`, historical `TRADES`, and `SCHEDULE`:

| Read type | Preflight/harness | Product E2E | Total |
| --- | ---: | ---: | ---: |
| ContractDetails | 4 | 5 | 9 |
| Historical TRADES | 4 | 5 | 9 |
| SCHEDULE | 8 | 10 | 18 |
| Account | 0 | 0 | 0 |
| Portfolio | 0 | 0 | 0 |
| Order | 0 | 0 | 0 |

The preflight counts include two extra completed AAPL read cycles from acceptance-harness reporting errors; they are included rather than hidden. Broker mutations and order mutations were **0**.

### Production database acceptance incident

The original production DB SHA-256 captured before E2E was:

```text
9b69aed9f4987131f2782a1f444297b9d350fd8ff28c6f1e652f2568ae1f9248
```

During the Pattern-disabled control restart, `DATABASE_URL` was supplied, but this repository reads `WEALTHPILOT_DB_PATH`. The server therefore opened `data/wealthpilot.db` and created exactly:

- conversation `eb31f8c8-8530-44a9-950a-c735a674d276`;
- messages `5911` and `5912` for that conversation.

The server was stopped and only those exact rows were deleted in one transaction. Post-recovery checks show:

- conversation count for that ID: 0;
- message count for that ID: 0;
- maximum message ID / total count restored to `5910 / 5910`;
- `PRAGMA integrity_check`: `ok`;
- no existing action, execution, order, broker, portfolio, or Dataset v2 row was changed by this recovery.

However, the SQLite file SHA-256 after logical recovery is:

```text
71cd70e2ca8352e6ca8adf6f50327ea0b6cf2b37d18eb1263a839b2658c63a63
```

Therefore this acceptance run cannot truthfully assert `Production DB change = 0`. This is an operational acceptance-procedure failure, not a Pattern product-code failure, and it is treated as a hard blocker.

Safety result: **FAIL — production DB zero-write invariant not met**.

## Q. Quality Gates

| Gate | Result |
| --- | --- |
| Replay/window invariance | 12 passed |
| Runtime promotion/registry + Decision/AI targeted | 74 passed |
| Technical Pattern + Pattern Data | 341 passed |
| Dataset v2 integrity | included, 5 passed |
| Full pytest | 880 passed, 7 skipped, 0 failed |
| `compileall` | passed |
| Frontend lint | passed, 0 errors/warnings |
| Frontend build | passed; existing bundle-size advisory only |
| Pattern Evidence UI | 6 passed |
| Offline M5 | 18/18; provider `offline_fixture`; public network attempts 0 |
| `git diff --check` | passed before report creation and rerun before commit |

Automated regressions made zero public-network attempts. Real IBKR and real LLM acceptance calls are separately identified and were not part of the offline gate.

## R. Remaining Limitations

Product limitations that would otherwise qualify for `READY_WITH_LIMITATIONS`:

1. Fixed Income Breakdown remains unpromoted.
2. Fixed Income Rectangle remains unpromoted.
3. Fixed Income Double Bottom remains unpromoted.
4. The current external search model configured for the Decision research fallback returned a deprecation error during live E2E and degraded to no search results; core Decision/LLM and Pattern evidence still completed. This is outside the Pattern acceptance implementation and was not changed here.

Acceptance-procedure blocker:

- the production DB zero-write safety invariant was violated transiently and cannot be certified as zero change despite exact logical-row recovery.

## S. Final Production Readiness Verdict

```text
PATTERN_EVIDENCE_PRODUCTION_BLOCKED
```

Reason: all Pattern product and quality hard gates passed, but section P's required production-database safety gate did not. A new feature-development stage is not justified; the final acceptance should be rerun from the same accepted code using an explicitly validated `WEALTHPILOT_DB_PATH` isolated database before any release-management action.

Safety summary:

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Dataset v2 mutation = 0
Production DB zero-write certification = FAIL
Push = NO
Merge = NO
Tag = NO
```
