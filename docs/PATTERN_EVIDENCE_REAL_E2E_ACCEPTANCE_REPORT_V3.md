# Pattern Evidence Final Isolated-DB Acceptance Report V3

Date: 2026-08-29

Branch: `codex/pattern-final-isolated-acceptance`

Start HEAD: `cfab69894c27482f82874d26a6646d229f5aae3c`

## A. Executive Conclusion

The final Pattern Evidence v1 acceptance was rerun with a fresh, explicitly resolved SQLite database outside the repository. The backend's authoritative `WEALTHPILOT_DB_PATH` setting was asserted before every launch, observed independently in each live process, and kept distinct from the production database by path, realpath, and inode.

The complete real product path passed:

```text
Current IBKR read-only history
→ approved Pattern runtime
→ PatternEvidenceBundle
→ Decision / governed AI context
→ SSE
→ isolated message persistence
→ backend restart / history restoration
→ frontend UI / page reload
```

Most importantly, the production database remained byte-for-byte unchanged. Its size and SHA-256 were identical before and after all acceptance services and quality gates.

Final verdict:

```text
PATTERN_EVIDENCE_PRODUCTION_READY_WITH_LIMITATIONS
```

The only Pattern v1 production-scope limitations are the three intentionally unpromoted Fixed Income scopes listed in section S. No product code changed.

## B. Acceptance Environment Isolation

The repository code was inspected before launch. `app/database.py` resolves SQLite from:

```python
DB_PATH = os.environ.get("WEALTHPILOT_DB_PATH", <repository default>)
```

`DATABASE_URL` is not the authoritative setting and was explicitly unset for setup, both enabled backend launches, the restart, the disabled control, and automated Python gates.

Acceptance environment:

```text
WEALTHPILOT_DB_PATH=/private/tmp/wealthpilot-pattern-isolated-acceptance.rCtzWv/acceptance.db
DATABASE_URL=UNSET
PUBLIC_DEMO_MODE=false
BROKER_MODE=mock
IBKR_READ_ONLY_MODE=true
ENABLE_IBKR_LIVE_TRADING=false
```

The acceptance DB was initialized from the normal repository schema and seeded only with one acceptance portfolio plus SPY, AAPL, and SHY positions. It was not copied from the production DB.

## C. Production DB Before-state

Before any backend process started:

| Field | Value |
| --- | --- |
| Absolute path | `/Users/songbin/Documents/GitHub/WealthPilot/data/wealthpilot.db` |
| Size | `10,637,312` bytes |
| SHA-256 | `71cd70e2ca8352e6ca8adf6f50327ea0b6cf2b37d18eb1263a839b2658c63a63` |

No migration or write-mode acceptance connection was opened against this path.

## D. Isolated DB Path Assertions

Pre-start assertions used the actual `app.database.DB_PATH` value:

```text
expected acceptance path
= resolved app.database.DB_PATH
= /private/tmp/wealthpilot-pattern-isolated-acceptance.rCtzWv/acceptance.db

resolved acceptance path != production path
acceptance realpath != production realpath
acceptance inode 199078070 != production inode 142737071
acceptance file is not a symlink
DATABASE_URL = UNSET
```

Each live process was independently inspected after startup. Its process-scoped `WEALTHPILOT_DB_PATH` matched the expected absolute path and `DATABASE_URL` was absent. The temporary acceptance module also asserted the resolved path at import and would have stopped startup on a mismatch.

The same pre-start and live-process assertions were repeated for the backend restart and Pattern-disabled control. All passed.

Initial isolated state:

```text
Conversations = 0
ConversationMessage = 0
ActionDraft = 0
AllocationIntent = 0
SymbolStrategy = 0
ExecutionPlan = 0
ExecutionBatch = 0
ExecutionLeg = 0
OrderRecord = 0
```

## E. Replay-equivalence Reconfirmation

`tests/technical_patterns/test_pattern_window_invariance.py` passed all 12 tests.

The suite reconfirmed:

- SPY Equity Breakout through 300- and 1,950-bar envelopes;
- identical normalized latest 300 closed sessions and source hash;
- Candidate Identity v2 equality across shifted raw-window ordinals;
- exact current detector-result and governed-fact equality;
- all six Equity launch families;
- the three promoted Fixed Income families on LQD;
- distinct structure anchors cannot collide.

The frozen reference remains:

```text
candidate_id = pat_c84a64645a4a0f6eca18
identity = WP-PATTERN-CORE-IDENTITY-2.0
formed / available = 2026-08-04 / 2026-08-04
lifecycle = EXPIRED
structure = CONFIRMED
direction = PENDING
invalidated = false
```

Result: **PASS**.

## F. Runtime Registry State

The exact approved registry remains 9 of 12 scopes:

| Economic asset class | Approved Pattern types |
| --- | --- |
| EQUITY | Breakout, Breakdown, Rectangle, Ascending Triangle, Double Top, Double Bottom |
| FIXED_INCOME | Breakout, Ascending Triangle, Double Top |

No manifest, calibration, identity, warm-up, detector, or runtime-registry change occurred.

## G. Real PATTERN_FOUND

The real single-symbol product case used SPY current IBKR daily history.

| Field | Observed value |
| --- | --- |
| Symbol / identity | `SPY:US` / IBKR conId `756733` |
| Evaluated closed session | `2026-08-28` |
| Pattern | Double Bottom |
| Candidate ID | `pat_cb3dabbf8b299d2ccf48` |
| Bundle hash | `07cf4e08c862e4f87984435f0c326b4574c7da2c6528a8e18683bf860f20f0d9` |
| Lifecycle | `CONFIRMED` |
| Structure | `CONFIRMED` on `2026-07-31` |
| Direction | `CONFIRMED` on `2026-08-03` |

Decision completed as `trim`, `actionable=true`. The confirmed Double Bottom was the sole Top item; four historical/invalidated SPY bundles remained in backend Remaining order. The same snapshot reached AI, SSE, isolated persistence, restart restoration, and UI.

Result: **PASS**.

## H. Real NO_PATTERN

SHY produced a governed `NO_PATTERN` bundle with reason `no_user_visible_pattern_evidence`.

- Decision completed as `hold`, `actionable=false`.
- AI completed.
- Top and Remaining were empty.
- UI rendered no Pattern section.
- No fake “No Pattern” card appeared.
- No `ENGINE_ERROR` state was rewritten.

Result: **PASS**.

## I. Real Compare

An explicit SPY/SHY PositionDecision compare was accepted with:

```text
scope = COMPARE
requested symbols = [SPY:US, SHY:US]
bundles = 6
Top = [pat_cb3dabbf8b299d2ccf48]
Remaining = [
  pat_f6c672c460fcbe5baa57,
  pat_e5d2ae13c4ac6de64e0b,
  pat_a1f7532c6df3467a0c46,
  pat_c84a64645a4a0f6eca18
]
```

SPY's five visible bundles and SHY's independent `NO_PATTERN` bundle retained symbol attribution. The UI grouped visible evidence only under SPY. No cross-symbol merge, Pattern winner, ranking, allocation inference, or recommendation inference appeared.

The first compare attempt encountered the existing bounded sidecar `TimeoutError`. Decision completed safely with no Pattern persistence and no execution side effect. One recorded retry in the same isolated environment succeeded without changing the timeout or code. This is treated as a demonstrated fail-open transient rather than a hard-gate failure: the required real Compare, persistence, restart, and UI path subsequently passed, and no systematic timeout was observed across the accepted cases.

Result: **PASS**, with the transient recorded.

## J. Non-promoted Scope Enforcement

Focused promotion and registry regressions prove that Fixed Income Breakdown, Rectangle, and Double Bottom produce an exact registry miss before approved provider/detector execution.

No Equity, Development, pilot, BTC/crypto, nearest-scope, or wildcard fallback exists. Decision continues through governed `DATA_UNAVAILABLE`/omission behavior.

Result: **PASS**.

## K. AI Explanation

The recorded real SPY and Compare LLM context contained only the governed projection:

- opaque instrument/candidate context, Pattern type/direction/lifecycle;
- structure and direction confirmation state/date;
- invalidation state;
- allowlisted governed fact code/value pairs;
- approved provenance fields and neutral evidence-only note.

It did not contain calibration thresholds, raw detector internals, debug geometry, probability, win rate, expected return, Entry/SL/TP, leverage, position size, or order instructions. SHY contributed no false Pattern context.

Result: **PASS**.

## L. SSE / Persistence / Restart / Reload

For SPY, SHY, and the successful SPY/SHY Compare, canonical JSON comparisons asserted exact equality among:

1. SSE `done.pattern_evidence`;
2. isolated `ConversationMessage.metadata.pattern_evidence` before restart;
3. the API response after a new backend process started;
4. the frontend model after page reload.

Schema version, candidate IDs, bundle hashes, Top/Remaining IDs, symbols, lifecycle, structure/direction states, and visible governed facts were identical. Restart/history reads caused zero additional IBKR Pattern requests.

Result: **PASS**.

## M. UI Acceptance

The real frontend rendered the persisted SPY and Compare evidence correctly:

- Pattern section defaulted to collapsed before and after page reload;
- Double Bottom name, `SPY:US`, lifecycle, separate structure/direction dates, governed facts, and evidence-only note were visible;
- the expanded SPY section was text-identical after reload;
- Pattern-local buttons were limited to section presentation and “More evidence” controls;
- no Buy, Sell, Trade, Execute, Copy, Create Order, or broker CTA existed inside the Pattern component;
- SHY remained silent, with no `NO_PATTERN` or `ENGINE_ERROR` card;
- Compare showed SPY evidence only and no ranking language.

The separate Playwright Pattern Evidence suite passed 6/6.

Result: **PASS**.

## N. Execution Authority Regression

The equivalent SPY request produced:

| Runtime | Decision type | Actionable | Pattern state |
| --- | --- | --- | --- |
| Enabled | `trim` | `true` | real approved SPY evidence |
| Disabled control | `trim` | `true` | `DATA_UNAVAILABLE: runtime_pattern_provider_not_promoted` |

Isolated acceptance writes were:

| State | Conversations | Messages | Action/Execution/Order entities |
| --- | ---: | ---: | ---: |
| Before E2E | 0 | 0 | 0 |
| After enabled cases, including recorded Compare retry | 4 | 8 | 0 |
| After disabled control | 5 | 10 | 0 |

ActionDraft, AllocationIntent, SymbolStrategy, ExecutionPlan, ExecutionBatch, ExecutionLeg, and OrderRecord remained exactly zero throughout. Broker and portfolio mutation counts were zero.

Result: **PASS**.

## O. Dataset v2 Integrity

All before/after values were identical:

| Item | SHA-256 / value |
| --- | --- |
| Logical manifest hash | `032c71380c775b4901c8ae73e1d1c730facfa41e032df8df30a413dad98dc12c` |
| Dataset manifest file | `b35f449834c5af43ca039b5678ef3fa0607845438cdb3a83b0871c36a57a6192` |
| Runtime validation manifest file | `00bc6ed04f5d91e6cd1bc480a9b2b757585f2633d3098275d313b9f3edbaea36` |
| Combined artifact-hash stream | `8b705427a78b9544c10617d55f8faf33b3680fc9021c79db5130236cf4b277ca` |

Dataset v2 writes and recalibration: **0**.

Result: **PASS**.

## P. IBKR Safety / Read Counts

The enabled E2E used only the Pattern source's approved read-only methods:

| Operation | Exact count |
| --- | ---: |
| ContractDetails | 6 |
| Historical TRADES | 6 |
| SCHEDULE | 12 |
| Account requests | 0 |
| Portfolio requests | 0 |
| Order requests | 0 |

Counts include SPY, SHY, the fail-open Compare attempt, and the successful Compare retry. The Pattern-disabled control and post-restart history reads added zero IBKR requests.

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Dataset v2 mutation = 0
```

Result: **PASS**.

## Q. Production DB After-state / Zero-write Certification

All backend/frontend acceptance services were stopped before the authoritative final comparison.

| Field | Before | After | Equal |
| --- | --- | --- | --- |
| Size | `10,637,312` bytes | `10,637,312` bytes | yes |
| SHA-256 | `71cd70e2ca8352e6ca8adf6f50327ea0b6cf2b37d18eb1263a839b2658c63a63` | `71cd70e2ca8352e6ca8adf6f50327ea0b6cf2b37d18eb1263a839b2658c63a63` | yes |

```text
production_db_sha256_before == production_db_sha256_after
production_db_zero_write_certification = PASS
```

No cleanup or logical-row repair was needed or attempted.

Result: **PASS — byte-for-byte unchanged**.

## R. Quality Gates

| Gate | Result |
| --- | --- |
| Final DB-path assertions | passed before/after every backend start |
| Replay/window invariance | 12 passed |
| Runtime registry + Decision/AI focused | 74 passed |
| Technical Pattern + Pattern Data | 341 passed |
| Dataset v2 integrity | included, 5 passed |
| Full pytest | 880 passed, 7 skipped, 0 failed |
| `compileall` | passed |
| Frontend lint | passed, 0 errors/warnings |
| Frontend build | passed; existing bundle-size advisory only |
| Pattern Evidence UI | 6 passed |
| Offline M5 | 18/18; `offline_fixture`; public network attempts 0 |
| `git diff --check` | passed before report creation and rerun before commit |

Automated Python gates used a separate temporary `WEALTHPILOT_DB_PATH`. Real IBKR and real LLM calls were confined to the explicitly described acceptance E2E.

## S. Remaining Limitations

Three Fixed Income Pattern scopes intentionally remain outside the promoted Pattern v1 runtime:

1. Breakdown / FIXED_INCOME;
2. Rectangle / FIXED_INCOME;
3. Double Bottom / FIXED_INCOME.

They fail closed without fallback and do not block the declared nine-scope v1 runtime. The first Compare attempt's bounded timeout is retained as an operational observation; the Decision fail-open behavior and a subsequent full Compare acceptance both passed.

The external Decision research fallback still reports its pre-existing deprecated search-model error and degrades to no search results. Real core Decision/LLM and Pattern evidence completed; this is outside Pattern Evidence v1 and was not modified.

## T. Final Production-readiness Verdict

```text
PATTERN_EVIDENCE_PRODUCTION_READY_WITH_LIMITATIONS
```

All Pattern Evidence hard gates passed, including the previously blocked production-DB zero-write invariant. Pattern Evidence remains read-only supporting context with no Decision, ranking, execution, Broker, Order, or Portfolio authority.

```text
6-Pattern v1 is release-ready.
Next task = release management only.
Push = NO
Merge = NO
Tag = NO
```
