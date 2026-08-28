# Real IBKR Pattern Runtime Promotion Report

## A. Executive Conclusion

Immutable Dataset v2 repaired the evaluation-authority gap without changing any
detector or threshold. Nine existing candidate scopes passed Development sanity,
Holdout, and Untouched from the same persisted canonical OHLCV artifacts.

```text
READY_FOR_RUNTIME_PROMOTION = 9 / 12
PATTERN_RUNTIME_PROMOTION_PARTIAL
READY_FOR_STAGE_2E2_REAL_E2E_ACCEPTANCE
```

The remaining three Fixed Income scopes retain
`INSUFFICIENT_REAL_CASE_EVIDENCE`. This is not a production-readiness claim.

## B. Evaluation Authority

- Dataset version: `wp-real-ibkr-pattern-dataset-v2`
- Dataset manifest: `032c71380c775b4901c8ae73e1d1c730facfa41e032df8df30a413dad98dc12c`
- Runtime validation manifest: `aca53383ef4b3ed58729310b3d8a05cd8995f53f3446fa15be9d0a5fd79f1ea7`
- Artifact root: `data/pattern_evaluation/v2/`
- Development/Holdout/Untouched IBKR refetches: 0

The one-time IBKR capture is no longer re-created during validation. Every
partition is read from deterministic canonical JSON and verified against its
artifact, ordered-session, bar-content, and partition hashes.

## C. Governance and Calibration Freeze

The existing Product Owner decision remains:

```text
AI_ASSISTED_REVIEW_ACCEPTED_FOR_V1_PROMOTION
```

It remains explicitly different from independent human review. Existing
Development threshold values were cloned into Dataset-v2-bound
`runtime-candidate-v2` versions with adjustment-attempt count 0. Each freeze
binds Dataset v2, review/governance records, detector, indicator and adapter
lineage.

## D. Development Sanity

All nine eligible scopes produced structurally valid, causal detector evidence
and the governed negative-control minimum. No detector regression was observed.
No detector code or parameter changed. The three evidence-insufficient scopes
were not opened.

## E. Holdout and Untouched

All nine eligible scopes passed both partitions from immutable artifacts only.
Each validation record proves that the exact frozen parameter hash is identical
through Development freeze, Holdout and Untouched. No tuning, universe change,
symbol replacement or outcome-based partition change occurred.

## F. Promotion Result

Promoted exact scopes:

- Breakout: EQUITY, FIXED_INCOME
- Breakdown: EQUITY
- Rectangle: EQUITY
- Ascending Triangle: EQUITY, FIXED_INCOME
- Double Top: EQUITY, FIXED_INCOME
- Double Bottom: EQUITY

Not promoted:

- Breakdown / FIXED_INCOME
- Rectangle / FIXED_INCOME
- Double Bottom / FIXED_INCOME

The authoritative hashes are in
[`REAL_IBKR_PATTERN_RUNTIME_PROMOTION_MATRIX.md`](./REAL_IBKR_PATTERN_RUNTIME_PROMOTION_MATRIX.md).

## G. Approved Runtime Registry

The registry snapshot contains exactly nine candidates. Lookup requires exact
market, economic asset class, timeframe, family and type. Missing scopes raise
`RuntimeCalibrationNotPromoted`; wildcard, nearest, Development, pilot,
cross-asset and BTC/crypto fallback are absent.

## H. Runtime Provider Activation

The existing Decision sidecar factory now builds the promoted read-only IBKR
provider. Runtime reads current IBKR data through the existing adapter, maps it
to Pattern Core, resolves only approved calibration scopes, and returns the
existing `PatternEvidenceBundle` contract. Dataset v2 is never runtime market
data. An unpromoted exact scope returns before an IBKR connection is opened.

Runtime detector input is bounded to the latest 300 fully closed sessions. This
exceeds the strictest promoted `minimum_history_bars` contract (80) while keeping
the six-pattern replay inside the existing 30-second Decision sidecar budget.
The bounded input receives a new canonical source hash; no stale full-history
hash is reused.

The provider source uses `readonly=True`, `StartupFetch(0)`, and exposes no
account, portfolio or order method.

## I. Runtime/Evaluation Separation

| Concern | Authority | Live IBKR permitted |
| --- | --- | --- |
| Calibration/validation | Immutable Dataset v2 | No |
| Product Pattern runtime | Current IBKR + existing adapter | Yes, read-only |

Regression tests patch the live source to fail if artifact validation attempts
to construct it, and separately prove the runtime factory returns the promoted
current-IBKR provider.

## J. Safety and Read Accounting

The single capture used 17 ContractDetails, 17 Daily TRADES, and 102 paged
SCHEDULE requests. Account, portfolio, and order requests were 0. Broker,
Order, Portfolio, ExecutionPlan, Production DB, Decision contract, UI, detector,
calibration-threshold and Tovest mutations were 0.

## K. Next Gate

Stage 2E-2 may perform a separate real end-to-end acceptance of the nine
promoted scopes against current IBKR runtime data. Deployment and any
`PRODUCTION_READY` declaration remain outside this task.
