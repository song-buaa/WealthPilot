# Real IBKR Pattern Runtime Promotion Matrix

> Stage: `2E-1`
>
> Verdict date: `2026-08-27`
>
> Market / timeframe: `US / 1d`
>
> Overall status: `PATTERN_RUNTIME_PROMOTION_BLOCKED`

| Pattern | EQUITY | FIXED_INCOME |
| --- | --- | --- |
| Breakout | `INSUFFICIENT_REAL_CASE_EVIDENCE` | `INSUFFICIENT_REAL_CASE_EVIDENCE` |
| Breakdown | `INSUFFICIENT_REAL_CASE_EVIDENCE` | `INSUFFICIENT_REAL_CASE_EVIDENCE` |
| Rectangle | `INSUFFICIENT_REAL_CASE_EVIDENCE` | `INSUFFICIENT_REAL_CASE_EVIDENCE` |
| Ascending Triangle | `INSUFFICIENT_REAL_CASE_EVIDENCE` | `INSUFFICIENT_REAL_CASE_EVIDENCE` |
| Double Top | `INSUFFICIENT_REAL_CASE_EVIDENCE` | `INSUFFICIENT_REAL_CASE_EVIDENCE` |
| Double Bottom | `INSUFFICIENT_REAL_CASE_EVIDENCE` | `INSUFFICIENT_REAL_CASE_EVIDENCE` |

## Scope rationale

Every scope has source-hashed real IBKR Development evidence and ten AI-assisted
engineering-review cases: five detected candidates and five negative controls.
That is not sufficient for runtime promotion because all twelve scopes share the
same three unresolved promotion prerequisites:

1. no immutable production calibration freeze exists; the recorded calibration
   versions remain `development-v1`;
2. Holdout and Untouched Validation are both
   `HASHED_NOT_OPENED_TO_DETECTOR`, so neither has a verdict;
3. independent human chart review was not performed, and the repository contains
   no explicit owner acceptance with
   `governance_acceptance = AI_ASSISTED_REVIEW_ACCEPTED_FOR_V1_PROMOTION`.

The verdict is therefore evidence insufficiency, not detector failure and not a
recalibration judgment. No scope has been entered into a runtime-approved
calibration registry.

## Activation consequence

The runtime activation set is empty. The existing safe provider remains
authoritative and returns `DATA_UNAVAILABLE` with
`runtime_pattern_provider_not_promoted`; Decision, AI explanation, and UI remain
non-blocking. No pilot, Development, cross-asset, cross-market, wildcard, nearest,
or BTC fallback is permitted.
