# Real IBKR Pattern Runtime Promotion Report

## A. Executive Conclusion

Stage 2E-1 completed the promotion review but cannot promote any of the twelve
`US × {EQUITY, FIXED_INCOME} × 1d × six Pattern` scopes.

```text
PATTERN_RUNTIME_PROMOTION_BLOCKED
```

This is a fail-closed governance result, not a detector regression. The real
Development evidence pack is intact, and its AI-assisted engineering review is
complete. However, no scope has an immutable production calibration freeze,
Holdout has not been opened to detector execution, Untouched Validation has not
been opened, and the owner has not recorded the explicit governance substitution
required to use AI-assisted review for v1 promotion.

Accordingly:

- runtime-approved scopes: **0 / 12**;
- runtime registry records: **0**;
- runtime provider activation change: **none**;
- real IBKR reads in this task: **0**;
- independent human chart review: **not performed**;
- production readiness: **not claimed**.

## B. Real Development Calibration Summary

The committed Real IBKR dataset manifest is internally bound by hash
`a44a3fe2a77d6b36b41fcae29ab4f664ddc3c077331d2c9829bf20ef2494e4f2`.
It records 17 Development, 17 Holdout, and 17 Untouched Validation instrument
partitions. All 51 entries are source-hashed `IBKR / TRADES / 1 day / useRTH=true`
series with the frozen Stage 0 adjustment and calendar metadata.

Only Development was opened to detectors. The AI-assisted review manifest is
bound by hash
`c5c70ba339c93809e8e6244639265fc0cabed576f970b1dd074b5efac78866e4`.
It contains 120 cases across twelve scopes:

- 60 detected candidates;
- 60 negative-control no-detections;
- 120 `PASS` labels;
- reviewer: `AI-assisted-engineering-review`;
- ten cases per scope: five detected plus five negative controls.

This review validates evidence consistency, manifest integrity, identity,
geometry, causality, and contract shape. It does not constitute independent
human chart review and does not authorize promotion.

No Development parameter exploration or parameter change was performed in
Stage 2E-1. The repository contains no governed parameter-attempt ledger leading
from these real cases to a newly frozen production calibration. Existing pilot
and Development parameter sets therefore remain hypotheses, not runtime
authority.

## C. Frozen Calibration Versions

No runtime calibration version was frozen. The observed Development versions and
parameter hashes are retained below solely as lineage evidence; none is present
in a runtime-approved registry.

| Pattern | Asset class | Development version | Parameter hash | Runtime frozen |
| --- | --- | --- | --- | --- |
| Breakout | EQUITY | `wp-us-level-break-development-v1` | `07b52fb1ffb813aedfd4ab5fc1cee5a50882754eef669cf0bb9d6140e62fbc04` | No |
| Breakout | FIXED_INCOME | `wp-us-level-break-development-v1` | `77204b512761817cbc2a5e3b3a5e956efac13fe9605639d3f35637422c7d44f6` | No |
| Breakdown | EQUITY | `wp-us-level-break-development-v1` | `7b379f52c730b743c9a314e38b9d3bf84bd0335632044ae6844e300bb577624b` | No |
| Breakdown | FIXED_INCOME | `wp-us-level-break-development-v1` | `cc389e09689edfd91fa664e2c82f9962ebf2e4a916041fb6619bc9e7be44a650` | No |
| Rectangle | EQUITY | `wp-us-rectangle-development-v1` | `fa2b2f348d54f8d5e3a4d304e8ba35e857dd702994dc37c617f3dea16d13047d` | No |
| Rectangle | FIXED_INCOME | `wp-us-rectangle-development-v1` | `ce0e5f3208eb2439d0777939c406e75ed3eb37fa6bf523aa005adaa9aee9a534` | No |
| Ascending Triangle | EQUITY | `wp-us-ascending-triangle-development-v1` | `b8616363e4acd36e4071f8813d1c643406fbfd805b37a4b078eb58e04636dc1e` | No |
| Ascending Triangle | FIXED_INCOME | `wp-us-ascending-triangle-development-v1` | `b85671a45db076094e37ddb7ec688a59bc71f773ec491237f5780a0cf8c58422` | No |
| Double Top | EQUITY | `wp-us-double-reversal-development-v1` | `c9e225163e06a8295dc2be5096262705ab0d8523d6c3523773c6d9cd8a22da8f` | No |
| Double Top | FIXED_INCOME | `wp-us-double-reversal-development-v1` | `e4284f08f58675eb0a5f7f318d175e1220246cdfa265906df2b2dd007b73bffd` | No |
| Double Bottom | EQUITY | `wp-us-double-reversal-development-v1` | `5935687436cd34fcba41e21dbbfabdeceb0d5348e92abee0343d7de3a8d9b90f` | No |
| Double Bottom | FIXED_INCOME | `wp-us-double-reversal-development-v1` | `e3b5eabaa54e21136c812bef073e7fc041b0dafd6153b292ca61b43912514aee` | No |

A valid future freeze must immutably bind the exact scope, parameter-set ID and
hash, dataset and review hashes, detector version, indicator-layer version,
Pattern Data Adapter version, and freeze timestamp. It must not overwrite these
Development records.

## D. Holdout Results

No Holdout result exists. The dataset manifest explicitly states:

```text
holdout = HASHED_NOT_OPENED_TO_DETECTOR
```

The review manifest independently confirms `holdout_detector_run=false`.
Opening Holdout before an accepted Development freeze would violate the required
chronological workflow and permanently consume the sealed partition. Stage 2E-1
therefore did not run it and did not manufacture a verdict from deterministic
fixtures.

## E. Untouched Validation Results

No Untouched Validation result exists. The dataset manifest explicitly states:

```text
untouched_validation = HASHED_NOT_OPENED_TO_DETECTOR
```

The review manifest confirms `untouched_validation_detector_run=false`.
Untouched Validation may be opened only after Holdout passes with exactly the
same frozen detector, parameters, adapter semantics, and universe contract.
That prerequisite is absent, so the partition remained sealed.

## F. Promotion Matrix

The authoritative matrix is
[`REAL_IBKR_PATTERN_RUNTIME_PROMOTION_MATRIX.md`](./REAL_IBKR_PATTERN_RUNTIME_PROMOTION_MATRIX.md).
All twelve cells are `INSUFFICIENT_REAL_CASE_EVIDENCE`.

The shared reason is missing freeze/Holdout/Untouched/governance evidence. This
verdict does not claim that detector semantics failed, and it does not authorize
parameter changes.

## G. Runtime Registry

No `ApprovedRuntimeCalibrationRegistry` was created because it would have zero
approved records. Creating code that embeds Development parameter sets under a
runtime name would be a silent pilot fallback and a false promotion claim.

The effective approved registry is therefore the empty set:

```text
approved_runtime_scopes = []
```

Exact-scope matching, immutable versioning, and no-fallback guarantees remain
requirements for the first non-empty registry. The future registry must reject:

- missing exact scope;
- EQUITY-to-FIXED_INCOME substitution;
- US-to-other-market substitution;
- Development or pilot fallback;
- wildcard or nearest-match lookup;
- BTC/crypto fallback.

## H. Runtime Provider Assembly

No real runtime provider was assembled or activated. The existing application
seam in `backend/services/technical_patterns/decision_integration.py` still
constructs `UnavailableDecisionPatternEvidenceProvider`. It returns the governed
safe result:

```text
result_state = DATA_UNAVAILABLE
reason = runtime_pattern_provider_not_promoted
```

This is the correct runtime behavior while the approved scope set is empty. No
review-pack orchestration, pilot calibration workflow, synthetic fixture, IBKR
account request, or brokerage authority entered runtime code.

## I. Mixed-scope Behavior

Mixed promotion is conceptually valid, but the current activation set has no
promoted member. Therefore no mixed-scope execution path is reachable in this
stage. Future partial promotion must execute only exact approved scopes, emit a
safe non-promoted outcome for absent scopes, and preserve other valid evidence.
It must not collapse one absent scope into provider-wide failure.

## J. Failure Isolation

Stage 2B isolation remains authoritative. Provider construction and collection
failures are sanitized to non-blocking `DATA_UNAVAILABLE` outcomes; Decision
continues. No changes were made to:

- Decision type or `actionable` authority;
- ReviewingAgent authority;
- `ActionDraft`, `ExecutionPlan`, `ExecutionBatch`, or `OrderRecord`;
- BrokerAdapter or Portfolio state;
- immutable Decision Pattern snapshots.

Because the provider stays unavailable, this stage introduced no network timeout,
IBKR identity, detector, adapter, or evidence-mapping failure surface.

## K. No-fallback Proof

The current runtime factory contains no calibration registry and no detector
assembly. Consequently there is no path from a missing approved scope to a
pilot, Development, generic US, cross-asset, nearest-match, wildcard, or BTC
parameter set. The existing targeted Decision test asserts the explicit reason
`runtime_pattern_provider_not_promoted`.

The calibration registry itself already performs exact six-dimensional lookup
and raises `CalibrationNotConfigured` when no exact key exists. Stage 2E-1 did
not weaken that contract.

## L. Decision / AI / UI Contract Regression

The frozen downstream contracts are unchanged:

- `DecisionPatternEvidenceSnapshot` remains canonical;
- `PatternAIContextAdapter` and its allowlist remain canonical;
- Top-3/remaining-evidence presentation policy remains canonical;
- the Stage 2D UI DTO and rendering semantics remain canonical;
- no Pattern result receives execution or portfolio authority.

With the unavailable provider, Decision receives an explicit non-blocking data
state. AI explanation and UI do not fabricate Pattern evidence.

## M. Tests / Quality Gates

All required gates passed on `2026-08-27`:

| Gate | Result |
| --- | --- |
| Review integrity + exact calibration + Stage 2B/2C contracts | `78 passed` |
| Technical Pattern + Pattern Data | `296 passed` |
| Full pytest | `835 passed, 7 skipped` |
| Python compileall | PASS |
| Frontend lint | PASS, 0 errors / 0 warnings |
| Frontend production build | PASS |
| Offline M5 | `18/18`, `public_network_attempts=0` |
| `git diff --check` | PASS |

The frontend build retains the known Vite large-chunk advisory; it is not a
build failure and no bundle-splitting work belongs to this stage. Standard
automated-test external network access was zero. No real IBKR read was needed or
performed for this promotion-blocked conclusion.

## N. Known Limitations

1. AI-assisted engineering review is not independent human chart review.
2. The repository does not contain
   `governance_acceptance = AI_ASSISTED_REVIEW_ACCEPTED_FOR_V1_PROMOTION`.
3. `human_review_complete=false` and
   `production_promotion_authorized=false` remain accurate historical facts.
4. No real-evidence Development parameter freeze has been recorded.
5. Holdout and Untouched Validation remain sealed and have no verdicts.
6. No scope can supply live Pattern evidence until the workflow is completed in
   order; product integration continues safely without it.

## O. Stage 2E-2 Readiness

Stage 2E-2 Real E2E Acceptance is **not ready** because it requires at least one
valid runtime-promoted scope and a real read-only provider assembly. To unblock:

1. record an explicit owner governance decision about the AI-assisted review
   substitution, without relabeling it as human review;
2. complete governed Real Development parameter review and freeze immutable
   scope records;
3. open and evaluate real Holdout exactly once with the frozen stack;
4. only after Holdout passes, open and evaluate Untouched Validation;
5. promote only passing scopes into an exact, immutable runtime registry;
6. implement and test the guarded read-only provider before Stage 2E-2.

Until then the final status is:

```text
PATTERN_RUNTIME_PROMOTION_BLOCKED
```

## Safety

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Decision integration change = 0
Detector algorithm change = 0
Calibration parameter change = 0
Real IBKR reads = 0
External test network = 0
```
