# Pattern Core Window-Invariance Remediation Report

## A. Executive Conclusion

The Pattern Core replay-window defect is repaired without changing detector
strategy, calibration thresholds, Dataset v2, promotion scope, or downstream
product contracts.

```text
PATTERN_CORE_WINDOW_INVARIANCE_READY
READY_TO_RERUN_FINAL_REAL_E2E
```

This is not a `PRODUCTION_READY` declaration. Production readiness still
requires rerunning Stage 2E-2 from its real Decision/SSE/persistence/UI gates.

## B. Original Failure

The blocked Stage 2E-2 case compared SPY Equity Breakout at fixed
`as_of=2026-08-29T08:00:00+00:00`. Latest-300 and 1,950-bar execution described
the same 2026-08-04 event, but produced different candidate IDs and slightly
different EMA-derived facts. Running every detector over 1,950 bars was not a
viable workaround because Rectangle alone took about 129.875 seconds.

## C. Candidate Identity Root Cause

The v1 candidate hash included window-relative formation and availability
ordinals, complete source references containing ordinals, and all geometry and
structure facts. It therefore included both window position and floating
indicator output. Pivot-derived source IDs could also carry the current causal
dataset hash, so they could not be the sole cross-window anchor.

## D. New Identity Contract

The prospective identity schema is
`wp-pattern-candidate-identity-v2`, represented by Core identity version
`WP-PATTERN-CORE-IDENTITY-2.0`.

Candidate identity now uses instrument/timeframe/Pattern identity, formation
and availability dates, ordinal-free source availability dates, stable
Pattern-specific canonical bar anchors, detector version, and parameter-set
identity. It excludes ordinals, runtime length, source hash, cache data,
indicator values, and output fact values.

Level Break anchors use the stable boundary and trigger bar. Rectangle,
Ascending Triangle, Double Top and Double Bottom use ordered typed source Pivot
bar IDs. Tests prove that equivalent anchors remain identical across shifted
windows and distinct anchors cannot collide.

Existing v1 message snapshots are not rewritten. Downstream readers already
treat candidate IDs as opaque values, so old and v2 snapshots remain readable
without a DB migration. The full contract is in
`docs/PATTERN_CORE_IDENTITY_CONTRACT.md`.

## E. Indicator Warm-up Root Cause

TA-Lib EMA initialization consumes the supplied prefix. Stage 2E-2 compared a
direct 300-bar Core input with an unnormalized 1,950-bar Core input, so EMA20
and EMA50 started from different histories. The small numeric difference was a
real input-contract divergence and was not accepted through tolerance or
rounding.

## F. New Warm-up Contract

Every promoted product-runtime envelope is normalized to the same latest 300
fully closed sessions before `PatternInputMapper` and TA-Lib:

```text
80 bars fixed discovery/indicator warm-up
+ 220 bars current discovery horizon
= 300 normalized runtime bars
```

Eighty is derived from the largest promoted `minimum_history_bars` and exceeds
the longest canonical indicator period, EMA50. The normalized bars and
`source_bar_hash` are therefore byte-for-byte equal whether the caller supplied
300, 600, or 1,950 bars. No output tolerance masks drift, and Dataset v2 replay
remains separate from product-runtime normalization.

## G. SPY Replay Equivalence

The exact prior real-IBKR capture was replayed at the original fixed `as_of`.

| Field | 300 source envelope | 1,950 source envelope |
| --- | --- | --- |
| Normalized input | 300 bars | 300 bars |
| Candidate ID | `pat_c84a64645a4a0f6eca18` | `pat_c84a64645a4a0f6eca18` |
| Formed / available | 2026-08-04 | 2026-08-04 |
| Lifecycle | `EXPIRED` | `EXPIRED` |
| Structure | `confirmed` | `confirmed` |
| Direction | `pending` | `pending` |
| Invalidated | false | false |
| Governed facts | exact | exact |
| Full detector result | exact | exact |

The normalized Breakout run used 300 bars; indicator preprocessing took about
0.0015s, detector work 0.0112s, and bundle construction 0.0012s.

## H. Six-pattern Cross-window Tests

A reusable contract suite covers:

- Equity: Breakout, Breakdown, Rectangle, Ascending Triangle, Double Top,
  Double Bottom on SPY;
- Fixed Income approved scopes: Breakout, Ascending Triangle, Double Top on
  LQD;
- exact normalized bar/source hashes and exact `DetectorRunResult` equality;
- raw-window candidate identity equality for the original SPY family;
- distinct-anchor identity separation;
- runtime warm-up derivation from approved history/indicator requirements.

The suite passed `12/12`. Existing registry tests continue to prove that Fixed
Income Breakdown, Rectangle and Double Bottom fail before provider access and
cannot use a fallback.

## I. Dataset v2 Regression

The full artifact-only Dataset v2 validator reran all Development, Holdout and
Untouched partitions. After excluding only the explicitly allowed changed
fields (`candidate_id` and its derived result hash), every scope matched the
frozen validation manifest exactly for case inventory, positive/negative
counts, ambiguity labels, structure/direction confirmation, lifecycle,
invalidation, facts and parameter lineage.

```text
Dataset manifest hash = 032c71380c775b4901c8ae73e1d1c730facfa41e032df8df30a413dad98dc12c
Dataset v2 semantic comparisons = 12 / 12 PASS
Approved scopes = 9 / 9 unchanged
Parameter adjustments = 0
Detector strategy changes = 0
IBKR reads after capture = 0
Dataset v2 mutations = 0
```

Golden Tovest parity fixtures were mechanically advanced only for v2 candidate
IDs, identity version and their derived hashes; all oracle structure and
numeric semantics remained unchanged.

## J. Runtime Registry Impact

The approved registry still contains exactly nine scopes:

- Equity: all six launch Patterns;
- Fixed Income: Breakout, Ascending Triangle, Double Top.

Fixed Income Breakdown, Rectangle and Double Bottom remain
`INSUFFICIENT_REAL_CASE_EVIDENCE`. No wildcard, cross-asset, Development, BTC or
nearest-scope fallback was added.

## K. Performance / 30s Budget

On the prior real SPY/LQD canonical captures, every normalized detector used
300 bars. Representative totals including bundle construction were:

| Symbol / asset | Pattern | Total |
| --- | --- | ---: |
| SPY / Equity | Breakout | 0.031s |
| SPY / Equity | Rectangle | 3.301s |
| SPY / Equity | Ascending Triangle | 2.515s |
| SPY / Equity | Double Top | 2.585s |
| SPY / Equity | Double Bottom | 2.743s |
| LQD / Fixed Income | Breakout | 0.009s |
| LQD / Fixed Income | Ascending Triangle | 2.825s |
| LQD / Fixed Income | Double Top | 2.736s |

A fresh current-Gateway full sidecar run completed in 13.048s for SPY and
7.888s for LQD. Both remained below the 30-second boundary. Indicator work was
sub-millisecond to about 0.0015s; structure detector time dominates. Bundle
construction was at most about 0.0014s when evidence existed.

## L. Downstream Contract Regression

No change was made to `PatternEvidenceBundle`, Decision snapshot,
`PatternAIContextAdapter`, Top/Remaining selection, SSE persistence fields, or
frontend DTO/UI. Stage 2B/2C/2D and Pattern Evidence regression tests verify
that v2 IDs remain ordinary opaque candidate IDs through serialization and
history-read compatibility. Old snapshots are untouched.

## M. Safety

Fresh real-current runtime verification used only:

```text
ContractDetails = 2
Historical TRADES = 2
SCHEDULE = 4
Account requests = 0
Portfolio requests = 0
Order requests = 0
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
```

The exact fixed-as-of SPY reproduction reused the prior ephemeral read-only
capture and added no broker read. No account identifier or credential was
printed. Automated tests use no public network.

## N. Remaining Limitations

- Three Fixed Income scopes remain intentionally unpromoted.
- v1 and v2 candidate IDs are not equal by design; historical snapshots retain
  their original opaque IDs.
- The complete real Decision → AI → SSE → persistence → UI → reload path still
  needs the separately governed Final E2E rerun.
- This task does not authorize deployment, release, merge or production use.

## O. Readiness to Rerun Final E2E

The original replay blocker is closed, all six families have cross-window
coverage, Dataset v2 event semantics are unchanged, the nine-scope registry is
frozen, and representative real sidecars remain within 30 seconds.

Final quality gates:

```text
Window-invariance targeted = 12 passed
Technical Pattern + Pattern Data = 336 passed
Full pytest = 880 passed / 7 skipped
compileall = PASS
frontend lint = PASS
frontend build = PASS (existing bundle-size warning only)
Pattern Evidence UI = 6 passed
Offline M5 = 18 / 18, public network attempts = 0
git diff --check = PASS
```

```text
PATTERN_CORE_WINDOW_INVARIANCE_READY
READY_TO_RERUN_FINAL_REAL_E2E
```
