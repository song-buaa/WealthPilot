# Real IBKR Pattern Dataset v2 Rebaseline Report

> Capture: 2026-08-28T08:00:00+00:00 · Evaluation authority: immutable artifacts

## A. Executive Conclusion

Immutable Real Dataset v2 now persists the actual canonical OHLCV used by
calibration validation. All 17 frozen-universe instruments were captured once,
and all three chronological partitions replay from committed artifacts without
an IBKR session. Nine previously Holdout-PASS scopes passed Development sanity,
Holdout v2, and Untouched v2; the three pre-existing Fixed Income evidence gaps
were not reopened.

```text
PATTERN_RUNTIME_PROMOTION_PARTIAL
READY_FOR_STAGE_2E2_REAL_E2E_ACCEPTANCE
```

This is not a `PRODUCTION_READY` claim.

## B. Why v1 Was Non-reproducible

The v1 process retained partition hashes but discarded their canonical OHLCV.
The source-hash audit later reproduced the same as-of, session set, SCHEDULE
lineage and adjustment policy, yet found 17/17 partition bar hashes changed.
IBKR historical TRADES is mutable over time, so hashes alone could prove drift
but could not reproduce the original evaluation dataset.

## C. Dataset v2 Capture

The governed capture used the frozen 17-symbol universe, `TRADES`, `1 day`,
`useRTH=true`, fully closed sessions, and the existing split-adjusted adapter.
Every instrument produced exactly 1,950 canonical bars. Capture as-of was fixed
once for the universe at `2026-08-28T08:00:00+00:00`.

- Dataset manifest hash: `032c71380c775b4901c8ae73e1d1c730facfa41e032df8df30a413dad98dc12c`
- Artifact location: `data/pattern_evaluation/v2/<SYMBOL>.json`
- Total retained size: approximately 5.7 MiB
- Validation manifest hash: `aca53383ef4b3ed58729310b3d8a05cd8995f53f3446fa15be9d0a5fd79f1ea7`

## D. Artifact Contract

Each deterministic JSON artifact stores stable instrument identity, economic
asset class, currency/timezone, adjustment and calendar lineage, adapter
version, capture metadata, and every canonical date/open/high/low/close/volume
value. `artifact_hash` is calculated from identity-relevant canonical content;
capture wall-clock metadata is recorded but excluded from artifact identity.

Readers recompute both the artifact hash and `source_bar_hash`. One modified bar
therefore changes the artifact hash and every partition hash containing it.

## E. Universe

- Common stocks: AAPL, MSFT, NVDA, JPM, XOM, JNJ
- Equity ETFs: SPY, QQQ, IWM, XLK, XLF, XLE
- Fixed-income ETFs: AGG, TLT, IEF, SHY, LQD

No symbol was added, removed, replaced, or selected after detector output.

## F. Partition Freeze

The boundaries were fixed before v2 detector replay:

| Partition | Requested range | Bars per instrument |
| --- | --- | ---: |
| Development | 2019-01-01 through 2022-12-31 | 1,008 |
| Holdout | 2023-01-01 through 2024-12-31 | 502 |
| Untouched Validation | 2025-01-01 through 2026-08-27 | 414 |

Each instrument/partition record binds actual first/last sessions, ordered
session-set hash, canonical bars hash, and a partition hash including provider,
calendar and adjustment semantics. Bars outside a partition do not affect its
hash.

## G. Development Sanity

The nine eligible candidate scopes ran with their existing threshold values.
All produced structurally valid, causal evidence and five governed negative
controls. Parameter adjustment attempts were **0**; detector changes were **0**.
No scope entered `NEEDS_RECALIBRATION`.

The three excluded scopes were not run:

- Breakdown / FIXED_INCOME
- Rectangle / FIXED_INCOME
- Double Bottom / FIXED_INCOME

## H. Calibration v2 Freeze

All threshold values remain unchanged, but each candidate has a new explicit
`runtime-candidate-v2` identity because calibration identity includes Dataset v2.
Every freeze binds exact scope, parameter set/hash, Dataset v2 manifest,
Development partition hashes, accepted AI-assisted review governance, detector,
indicator, adapter, and freeze timestamp.

## I. Holdout v2

All nine eligible scopes passed Holdout from Dataset v2 artifacts only. Each
scope yielded at least five structurally valid detected cases and five fixed-rule
negative controls. There was no parameter tuning and no IBKR read.

## J. Untouched v2

Only Holdout-passing scopes opened Untouched. All nine passed from the same
artifact authority. For every scope:

```text
parameter_hash_development_freeze
= parameter_hash_holdout
= parameter_hash_untouched
```

Untouched IBKR reads were 0, and no universe or detector change occurred.

## K. Promotion Impact

- `READY_FOR_RUNTIME_PROMOTION`: 9
- `INSUFFICIENT_REAL_CASE_EVIDENCE`: 3
- `NEEDS_RECALIBRATION`: 0
- `DATA_QUALITY_BLOCKED`: 0

The exact results and hashes are in
`docs/pattern_review/REAL_IBKR_PATTERN_RUNTIME_VALIDATION_V2_MANIFEST.json`.

## L. Runtime Registry

The approved registry contains only the nine ready scopes. Exact market, asset
class, timeframe, family, and type must match. There is no wildcard, pilot,
Development, cross-asset, crypto/BTC, or nearest-scope fallback. The three
evidence-insufficient scopes are absent, not mapped to another calibration.

## M. Runtime/Evaluation Separation

```text
Calibration validation
  -> committed Dataset v2 artifact
  -> frozen v2 calibration

Product runtime
  -> current IBKR read-only historical data
  -> existing IBKR Pattern Data Adapter
  -> exact approved registry scope
  -> existing detector / PatternEvidenceBundle
```

The validation module has no provider dependency. The runtime provider never
reads Dataset v2. Unsupported scopes stop before opening an IBKR connection.

## N. Safety / IBKR Read Counts

The one-time capture performed 17 ContractDetails, 17 historical TRADES, and
102 paged SCHEDULE requests. It performed 0 account, portfolio, or order
requests. Broker, Order, Portfolio, ExecutionPlan, Production DB, and Tovest
mutations were all 0. Holdout/Untouched reads after capture were 0.

## O. Remaining Blockers

The following require a future, separately governed evidence-expansion task:
Breakdown/FIXED_INCOME, Rectangle/FIXED_INCOME, and Double Bottom/FIXED_INCOME.
Dataset v2 does not cure their v1 review-case insufficiency and this task did not
expand the universe or cherry-pick replacements.

## P. Stage 2E-2 Readiness

The nine promoted scopes are ready for a separate real end-to-end acceptance of
the current-IBKR runtime path. Deployment, production-readiness declaration,
Decision semantics changes, UI changes, and trade authority remain out of scope.
