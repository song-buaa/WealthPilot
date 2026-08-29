# Pattern Evidence Real IBKR Final E2E Acceptance Report

## A. Executive conclusion

Stage 2E-2 is **blocked**. Real IBKR read-only acquisition and the promoted
runtime sidecar operate correctly after one narrow schedule-capacity fix, but
the mandatory replay-equivalence gate failed before the real Decision / AI /
SSE / persistence / browser sequence was opened.

For the same SPY contract, approved Equity Breakout calibration, and fixed
`as_of=2026-08-29T08:00:00+00:00`, the latest-300-bar replay and a 1,950-bar
replay described the same current visible event with different stable candidate
identities and slightly different indicator-derived facts. This satisfies the
task's explicit stop condition: a 300-bar replay materially changes current
visible evidence.

Final verdict:

```text
PATTERN_EVIDENCE_PRODUCTION_BLOCKED
```

## B. Runtime registry and source freeze

- Branch start: `codex/pattern-final-e2e-acceptance@da134f989e2168c2e7857103e4fca55fcf02a8ae`.
- Dataset: immutable `wp-real-ibkr-pattern-dataset-v2`.
- Dataset manifest SHA-256: `032c71380c775b4901c8ae73e1d1c730facfa41e032df8df30a413dad98dc12c`.
- Validation manifest SHA-256: `aca53383ef4b3ed58729310b3d8a05cd8995f53f3446fa15be9d0a5fd79f1ea7`.
- Frozen content reverified: 17 instruments, 33,150 bars, 51 partition
  assignments, with all referenced artifact and source hashes intact.
- Runtime registry contains exactly the nine approved scopes and no fallback:
  Equity Breakout, Breakdown, Rectangle, Ascending Triangle, Double Top,
  Double Bottom; Fixed Income Breakout, Ascending Triangle, Double Top.
- Fixed Income Breakdown, Rectangle, and Double Bottom remain absent with
  `INSUFFICIENT_REAL_CASE_EVIDENCE`.

No Dataset v2 manifest, bar artifact, validation artifact, calibration
parameter, detector, or promotion decision was changed.

## C. Real IBKR provider acceptance

The probe connected to IB Gateway at `127.0.0.1:4001` with the repository's
dedicated pattern-data client and read-only API surface. Live runtime acquisition
used only ContractDetails, historical adjusted `TRADES`, and `SCHEDULE`.

The first AAPL call correctly failed closed as `DATA_QUALITY_BLOCKED` because a
two-year historical response contained slightly more than 500 sessions while
runtime SCHEDULE capacity was 500. The adapter validates the complete response
before trimming it to 300 bars, so this was a narrow runtime-provider capacity
bug rather than a detector or calibration issue. The fix raises the bounded
SCHEDULE capacity to 800 while preserving the 300-bar detector window and all
quality gates.

After the fix, a deterministic 17-instrument real-current scan completed. All
instruments except SHY produced at least one user-visible pattern result; SHY
returned the explicit `NO_PATTERN` reason `no_user_visible_pattern_evidence`.
No provider call silently substituted frozen data.

## D. Single-symbol real evidence

AAPL completed in 14.594 seconds and produced six real-current bundles:
Breakdown, Breakout, Rectangle, Double Bottom, Double Top, and Ascending
Triangle. All six were `INVALIDATED`, which is valid historical technical
evidence rather than a current trading instruction.

The bounded source-bar hash was
`44b1c7a61279a673081b0727552c6a5acd12c8d3e11b3f0fb07f7518cb1f4f7f`.
Every bundle used the exact approved runtime parameter hash for its
market/asset/timeframe/pattern scope, retained IBKR source provenance, and was
generated under the 30-second sidecar budget.

## E. Explicit no-visible-pattern case

SHY Fixed Income completed in 6.283 seconds with:

```text
NO_PATTERN: no_user_visible_pattern_evidence
```

This proves the real provider can distinguish a successful query with no
eligible visible evidence from a provider or data-quality failure.

## F. Real Decision comparison

Not executed. The replay-equivalence hard gate failed first, so the task's stop
rule prohibited opening a real Decision run with Pattern Evidence enabled and
disabled. Consequently no claim is made about real-current decision neutrality
or recommendation deltas in this acceptance run.

The existing deterministic Decision integration suite passed and continues to
prove fail-open sidecar behavior and unchanged authoritative recommendation
fields, but it cannot replace the stopped real E2E comparison.

## G. Non-promoted scope enforcement

The registry remains exact-key, fail-closed, and fallback-free. Automated
contract tests verify that Fixed Income Breakdown, Rectangle, and Double Bottom
cannot open a data source or detector path and return the expected unpromoted
outcome. These scopes were deliberately not invoked against IBKR in this run;
doing so would weaken the requirement that an unpromoted request fail before
provider access.

## H. Failure isolation and fail-open behavior

- The real SCHEDULE coverage mismatch surfaced as `DATA_QUALITY_BLOCKED`; it was
  not converted to an empty account, empty series, or `NO_PATTERN`.
- Targeted tests cover provider timeout, connection failure, missing expected
  session, unpromoted scope, and Decision-side fail-open isolation.
- Pattern sidecar failures do not change the authoritative Decision result in
  deterministic integration tests.
- No real Decision-side timeout injection was performed after the mandatory
  equivalence stop.

## I. SSE, persistence, and reload

Real-current SSE and persistence/reload acceptance was not executed because the
hard gate stopped the E2E sequence. The Stage 2B/2C/2D targeted suites passed:
backend contract/integration tests validate SSE and persisted evidence payloads,
and six frontend Pattern Evidence tests validate render and reload behavior.
Those results are regression evidence only, not a substitute for the required
real-current E2E.

## J. AI explanation contract

Real-current AI explanation acceptance was not executed after the hard stop.
Static and targeted tests confirm the existing allowlist-only fact boundary,
neutral/no-trade semantics, citation handling, and omission behavior when no
eligible evidence exists. No prompt, model setting, detector result, or AI
contract was modified.

## K. UI acceptance

The in-app browser runtime was initialized, but no real-current UI acceptance
actions were taken after the stop condition. Frontend targeted tests passed for
Pattern Evidence rendering, status and provenance presentation, absence of
BUY/SELL semantics, and reload behavior. Human-visible real UI acceptance
therefore remains outstanding.

## L. Latest-300 versus long-history replay equivalence

The hard gate failed on SPY Equity Breakout using the same IBKR contract, fixed
`as_of`, approved calibration, detector version, and current visible horizon.

| Field | Latest 300 bars | Long 1,950 bars |
| --- | --- | --- |
| Candidate ID | `pat_329ec4b64a285bf12c94` | `pat_b8d4ff0f82440b76fb7a` |
| Formed / available date | 2026-08-04 | 2026-08-04 |
| Lifecycle | `EXPIRED` | `EXPIRED` |
| Structure confirmation | confirmed 2026-08-04 | confirmed 2026-08-04 |
| Direction confirmation | pending | pending |
| Invalidation | not invalidated | not invalidated |
| Break threshold | 764.7193895349258 | 764.7193895353018 |
| Decisive margin | 2.4183895349258373 | 2.418389535301924 |
| EMA20 | 747.0611787874548 | 747.0611787874553 |
| EMA50 | 741.0319995473823 | 741.031865511561 |

Two causes were isolated:

1. Candidate identity material contains window-relative session ordinals for
   formation, availability, lineage, and facts. Truncating the same dated series
   therefore changes the stable ID.
2. EMA initialization depends on the available prefix, producing small but real
   fact drift between 300 and 1,950 bars.

This is not safely repairable as a Stage 2E-2 micro-fix. Replacing ordinals with
calendar/session identity changes Pattern Core identity semantics; specifying
indicator warm-up or visible-fact tolerances changes the frozen evidence
contract. Simply running the full long window is also unsuitable: Rectangle
alone took 129.875 seconds, above the 30-second sidecar budget.

Long-current evidence was captured only in an ephemeral `/tmp` location. It was
not written to Dataset v2 or production storage.

## M. Latency and runtime observations

The 17-instrument bounded real-current scan had:

- minimum: 6.283 seconds;
- median: 14.541 seconds;
- maximum: 16.754 seconds;
- all symbols: below the 30-second sidecar budget.

Separate long-series IBKR acquisition took 5.607 seconds for SPY and 5.011
seconds for LQD. On SPY, latest-300 Breakout detection took 0.037 seconds and
Rectangle took 2.628 seconds; long-1,950 Breakout took 0.132 seconds, while
Rectangle took 129.875 seconds. Bundle construction was not independently
instrumented. No performance tuning was performed.

## N. Decision authority regression status

No authority boundary was changed. Pattern Evidence remains read-only,
non-authoritative, non-actionable, and incapable of changing Decision,
Portfolio, ExecutionPlan, Broker, or Order state. The full automated regression
suite and Decision targeted suite passed. Real-current recommendation equality
remains unaccepted because Section L stopped the run before that comparison.

## O. Safety and read accounting

Across the real acceptance investigation, including diagnosis and equivalence
captures:

```text
ContractDetails requests = 24
Historical TRADES requests = 24
SCHEDULE requests = 64
Account requests = 0
Portfolio requests = 0
Order queries = 0
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Decision integration change = 0
Dataset v2 change = 0
```

The runtime source exposes no account, portfolio, order, or mutation method.
No account number or credential was printed. Temporary live captures remained
outside Git and outside the production database.

## P. Verification results

The focused schedule-capacity regression and IBKR adapter suite passed with 25
tests. After the narrow fix, the complete repository gate passed with 868 tests
and 7 skips; compileall, frontend lint, and frontend build passed; all six
frontend Pattern Evidence tests passed; and Offline M5 passed 18/18 with zero
public network attempts. The build emitted only the existing non-blocking bundle
size warning. The pre-fix baseline was 867 passed and 7 skipped, so the one-test
increase is the new capacity regression test.

## Q. Known limitations and blockers

- Fixed Income Breakdown, Rectangle, and Double Bottom intentionally remain
  unpromoted due to insufficient real-case evidence.
- Stable Pattern identity is not invariant to history-window truncation.
- Indicator facts are not strictly invariant to available warm-up history.
- A long-window runtime workaround violates the current 30-second sidecar
  budget for Rectangle.
- Real Decision enabled/disabled comparison, real SSE, persistence/reload, AI
  explanation, and browser UI acceptance were not opened after the hard stop.

## R. Recommendation and next gate

Do not promote Pattern Evidence to production from this branch. Open a separate,
explicitly governed Pattern Core remediation task to define window-invariant
identity and indicator warm-up/equivalence semantics, add cross-window contract
fixtures for all six patterns and both asset classes, and verify the 30-second
runtime budget. After that change is independently reviewed and frozen, rerun
Stage 2E-2 from the replay gate through real Decision, AI, SSE, persistence,
reload, and UI acceptance.

```text
PATTERN_EVIDENCE_PRODUCTION_BLOCKED
```
