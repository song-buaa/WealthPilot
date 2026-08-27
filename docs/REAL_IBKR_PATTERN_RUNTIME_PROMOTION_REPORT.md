# Real IBKR Pattern Runtime Promotion Report

## A. Executive Conclusion

Stage 2E-1A recorded the Product Owner's explicit review-governance substitution.
Stage 2E-1B then froze all twelve current Real Development threshold sets and
opened the pre-registered real Holdout with those immutable parameters.

Nine scopes passed Holdout. Three Fixed Income scopes stopped for insufficient
review-case coverage. Before any Untouched detector ran, the source-integrity
gate found that 16 of 17 pre-registered Untouched hashes no longer reproduced
from IBKR. The data contract requires exact source lineage and forbids universe
cherry-picking, so every Holdout-passing scope is data-quality blocked.

```text
READY_FOR_RUNTIME_PROMOTION = 0 / 12
PATTERN_RUNTIME_PROMOTION_BLOCKED
```

No runtime scope was activated and no production-readiness claim is made.

## B. Governance Acceptance

The Product Owner decision is recorded separately in
[`PATTERN_EVIDENCE_V1_REVIEW_GOVERNANCE_ACCEPTANCE.md`](./PATTERN_EVIDENCE_V1_REVIEW_GOVERNANCE_ACCEPTANCE.md):

```text
governance_acceptance =
AI_ASSISTED_REVIEW_ACCEPTED_FOR_V1_PROMOTION
```

The acceptance applies only to Pattern Evidence v1, US Daily, EQUITY and
FIXED_INCOME, and the six launch Patterns. It does not claim independent human
review, does not set `human_review_complete=true`, and does not waive Holdout or
Untouched Validation.

## C. Real Development Freeze

The 120-case Real Development evidence manifest remained the source of reviewed
parameter lineage:

- dataset manifest hash:
  `a44a3fe2a77d6b36b41fcae29ab4f664ddc3c077331d2c9829bf20ef2494e4f2`;
- review manifest hash:
  `c5c70ba339c93809e8e6244639265fc0cabed576f970b1dd074b5efac78866e4`;
- AI-assisted cases: 120/120 `PASS`;
- threshold adjustment attempts in Stage 2E-1B: **0**.

No Development evidence clearly required a threshold change. Each existing
threshold set was therefore cloned without value changes into an explicit,
immutable `runtime-candidate-v1` calibration key. The key change intentionally
produces a new parameter-set ID/hash while preserving every detector threshold.

Each freeze record binds the Development parameter identity, final candidate
identity, freeze timestamp, dataset/review/governance hashes, detector version,
indicator version, and Pattern Data Adapter version. It is a candidate freeze,
not runtime approval.

## D. Frozen Calibration Versions

| Pattern | Asset | Candidate version | Final parameter hash | Adjustments |
| --- | --- | --- | --- | ---: |
| Breakout | EQUITY | `wp-us-level-break-runtime-candidate-v1` | `0aeea84fccc730f4d3ebb5067b317e767aa195a0d2d39bf752aa8c29faa42d8e` | 0 |
| Breakout | FIXED_INCOME | `wp-us-level-break-runtime-candidate-v1` | `9779614203c8bb4c64dea3ea22f7aebd92fe4c025e888dd9f0498c5d04ddbe74` | 0 |
| Breakdown | EQUITY | `wp-us-level-break-runtime-candidate-v1` | `42e4f448b4ec7b695f4deb63261cf51e503ceae50ba055a59f71fde83b81e0a8` | 0 |
| Breakdown | FIXED_INCOME | `wp-us-level-break-runtime-candidate-v1` | `edcd922c9cbab4f2dfe2a88ec4d3186932806e45f438822fc25c51466357bcc5` | 0 |
| Rectangle | EQUITY | `wp-us-rectangle-runtime-candidate-v1` | `e4a7c775559834e702334249477ab5296f120c0be739dbf0071601a841235752` | 0 |
| Rectangle | FIXED_INCOME | `wp-us-rectangle-runtime-candidate-v1` | `1720a0096f2b6e9808ec2c4c9f3418393d5f6867798a9f33cb31a0588c5ca87e` | 0 |
| Ascending Triangle | EQUITY | `wp-us-ascending-triangle-runtime-candidate-v1` | `1d733373f04205a5ef2aa27c8fea9120d4c3a36006b19107b530b0a06a7c4ba8` | 0 |
| Ascending Triangle | FIXED_INCOME | `wp-us-ascending-triangle-runtime-candidate-v1` | `223477aef2a692f1f16c2e2c8cd50fc7b2bdf4b4f0ad755bce7cfab1c244e0cc` | 0 |
| Double Top | EQUITY | `wp-us-double-reversal-runtime-candidate-v1` | `65c8e15c2fbe8f3a6c7f32c63555444c4fcedc6698f4905e9b82a52f821413d8` | 0 |
| Double Top | FIXED_INCOME | `wp-us-double-reversal-runtime-candidate-v1` | `bb955e984433cfc4465697c819e7fbae48af577bdf8c9fe581902f6d59fce121` | 0 |
| Double Bottom | EQUITY | `wp-us-double-reversal-runtime-candidate-v1` | `7564ba6a91676504709eb6d84f21be0fce453f44fb8ba7449c026f84274936b6` | 0 |
| Double Bottom | FIXED_INCOME | `wp-us-double-reversal-runtime-candidate-v1` | `52ee41188dac96f7a1038634f4a3dcfc0bdebae65375e744cc99239e5c9ff243` | 0 |

The frozen parameter hash used before Holdout is the same hash recorded in every
Holdout result. No Holdout tuning occurred.

## E. Holdout Results

The real Holdout covers `2023-01-01` through `2024-12-31`. All 17 partition
source hashes reproduced exactly before detector access. Detector replay used a
fixed 200-session causal warm-up, the frozen detector and indicator versions,
and the candidate hashes above.

| Pattern | Asset | Detected total | Reviewed detected | Negative controls | Labels | Result |
| --- | --- | ---: | ---: | ---: | --- | --- |
| Breakout | EQUITY | 198 | 5 | 5 | PASS 10 | PASS |
| Breakout | FIXED_INCOME | 29 | 5 | 5 | PASS 10 | PASS |
| Breakdown | EQUITY | 56 | 5 | 5 | PASS 10 | PASS |
| Breakdown | FIXED_INCOME | 90 | 5 | 4 | PASS 9 | `INSUFFICIENT_REAL_CASE_EVIDENCE` |
| Rectangle | EQUITY | 8 | 5 | 5 | PASS 10 | PASS |
| Rectangle | FIXED_INCOME | 3 | 3 | 5 | PASS 8 | `INSUFFICIENT_REAL_CASE_EVIDENCE` |
| Ascending Triangle | EQUITY | 69 | 5 | 5 | PASS 10 | PASS |
| Ascending Triangle | FIXED_INCOME | 25 | 5 | 5 | PASS 10 | PASS |
| Double Top | EQUITY | 163 | 5 | 5 | PASS 10 | PASS |
| Double Top | FIXED_INCOME | 49 | 5 | 5 | PASS 10 | PASS |
| Double Bottom | EQUITY | 131 | 5 | 5 | PASS 10 | PASS |
| Double Bottom | FIXED_INCOME | 47 | 5 | 4 | PASS 9 | `INSUFFICIENT_REAL_CASE_EVIDENCE` |

All reviewed Holdout cases passed identity, geometry, causal-order, structure,
lifecycle, and exact calibration-lineage checks. No false-positive,
false-negative, ambiguous, future-fact, or data-quality label was recorded. The
three insufficient scopes were stopped without changing anchors, adding symbols,
or tuning after exposure.

The immutable Holdout validation manifest hash is
`7fd3c1d07a13226ee9ce21a54fa36062718c351b8ab9bbf8bc38e6926491c93b`.

## F. Untouched Validation Results

The Untouched partition was pre-registered for `2025-01-01` through
`2026-08-21`. Before detector execution, the same read-only IBKR request was
canonicalized and sliced to the frozen dates. The integrity gate found:

```text
source hash matches = 1 / 17 (LQD)
source hash mismatches = 16 / 17
Untouched detector runs = 0
```

All twelve EQUITY instruments drifted. Four of five FIXED_INCOME instruments
(AGG, TLT, IEF, SHY) drifted; only LQD matched. Development and Holdout hashes
still matched 17/17, isolating the drift to the newest partition rather than the
adapter serialization or identity contract.

The task forbids universe changes and requires exact source-hashed real data.
Running only LQD, rewriting the pre-registered hashes, or accepting revised bars
as the same sealed partition would violate that contract. The nine
Holdout-passing scopes are therefore `DATA_QUALITY_BLOCKED` before detector
access. The three Holdout-insufficient scopes did not qualify to open Untouched.

Full expected/actual mismatch hashes are retained in
[`REAL_IBKR_PATTERN_RUNTIME_VALIDATION_MANIFEST.json`](./pattern_review/REAL_IBKR_PATTERN_RUNTIME_VALIDATION_MANIFEST.json).

## G. Promotion Matrix

The authoritative per-scope result is
[`REAL_IBKR_PATTERN_RUNTIME_PROMOTION_MATRIX.md`](./REAL_IBKR_PATTERN_RUNTIME_PROMOTION_MATRIX.md):

- `READY_FOR_RUNTIME_PROMOTION`: 0;
- `DATA_QUALITY_BLOCKED`: 9;
- `INSUFFICIENT_REAL_CASE_EVIDENCE`: 3;
- `NEEDS_RECALIBRATION`: 0.

## H. Runtime Registry

`ApprovedRuntimeCalibrationRegistry` was implemented as an exact, immutable,
no-fallback registry. Candidate freezes and promotion evidence are separate;
candidate status alone cannot populate the registry. It rejects hash/version
drift and performs no wildcard, nearest, cross-asset, cross-market, Development,
pilot, or BTC fallback.

Because zero scopes passed all gates, the effective approved registry snapshot is
empty. No Development or candidate parameter set is runtime authority.

## I. Runtime Provider Activation

No real runtime provider was activated. The Stage 2B factory continues to return
`UnavailableDecisionPatternEvidenceProvider`, producing the explicit safe state:

```text
DATA_UNAVAILABLE
reason = runtime_pattern_provider_not_promoted
```

Decision remains fail-open. No review builder, pilot workflow, synthetic fixture,
IBKR account request, or trading surface was imported into runtime.

## J. Partial Promotion Behavior

The registry contract supports partial exact-scope promotion, but no current
scope is eligible. A non-ready promotion record is omitted; an absent scope
raises `RuntimeCalibrationNotPromoted`. The application therefore cannot confuse
one candidate or one matching instrument with an approved scope.

## K. No-fallback Proof

Targeted tests prove that an exact approved scope cannot fall back across:

- EQUITY and FIXED_INCOME;
- US and BTC/crypto;
- timeframe;
- pattern type/family;
- runtime-candidate and Development versions.

Ready promotion evidence must bind both unseen `PASS` results and the frozen
parameter hash. Missing Untouched, missing governance, or hash drift is rejected.

## L. Downstream Contract Regression

No frozen Stage 2B/2C/2D contract changed. The default unavailable provider keeps
`DecisionPatternEvidenceSnapshot`, `PatternAIContextAdapter`, Top/Remaining
selection, and the UI DTO non-blocking and unchanged. Pattern Evidence still has
no Decision, ReviewingAgent, ActionDraft, ExecutionPlan, ExecutionBatch,
OrderRecord, Broker, or Portfolio authority.

## M. Safety / IBKR Read Accounting

The only live access was Pattern Data read-only acquisition for the frozen 17
symbols, with `as_of=2026-08-22T12:00:00+00:00`:

```text
ContractDetails requests = 17
Daily TRADES historical requests = 17
SCHEDULE requests = 102
symbols = 17
requested duration = 8 Y
bar size = 1 day
useRTH = true
account data requests = 0
```

Mutation accounting:

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB mutation = 0
Account data request = 0
```

The canonical cache and detailed replay output remained in a temporary local
directory and were not added to Git.

## N. Quality Gates

Final automated gates passed after repairing a cold-import cycle between the
detector package and the new runtime registry. The registry now resolves detector
version lineage lazily, and a subprocess regression test proves that a
detector-first import can construct all twelve freezes.

```text
Governance/runtime targeted tests = 18 passed
Technical Pattern + Pattern Data targeted tests = 313 passed
Pattern/Decision integration targeted tests = 86 passed
Full pytest = 853 passed / 7 skipped / 0 failed
compileall = PASS
frontend lint = PASS (0 errors / 0 warnings)
frontend build = PASS
Offline M5 = 18/18, public_network_attempts = 0
```

The frontend build retained its existing non-blocking bundle-size advisory.
Automated gates used mocks/fixtures and did not add public-network, account, or
mutation access. The real IBKR reads listed in section M were the only live data
activity in this task.

## O. Remaining Non-promoted Scopes

All twelve scopes remain non-promoted:

- nine require a new governed Untouched dataset version or an explicit policy for
  provider revisions, followed by a fresh untouched cycle;
- `breakdown/FIXED_INCOME` and `double_bottom/FIXED_INCOME` additionally lack the
  fifth frozen Holdout negative control;
- `rectangle/FIXED_INCOME` additionally has only three Holdout detected cases.

The opened Holdout cannot be reused as unseen evidence after any future tuning.
Any recalibration must start a new Development/freeze/validation cycle.

## P. Stage 2E-2 Readiness

Stage 2E-2 is not ready because no scope has both real Holdout and Untouched
`PASS`, and the approved runtime registry is empty.

```text
PATTERN_RUNTIME_PROMOTION_BLOCKED
```

This status does not mean detector failure, does not claim production readiness,
and grants no trading authority.
