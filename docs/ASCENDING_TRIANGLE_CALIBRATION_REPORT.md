# Ascending Triangle Calibration Pilot Report

> Stage: 1D-4
>
> Date: 2026-08-22
>
> Branch: `codex/ascending-triangle-calibration-pilot`
>
> Stage 1D-3 base: `261315bdba4806751f9a53525d447e0a0862d010`

## A. Executive Conclusion

Stage 1D-4 validates the existing Ascending Triangle Detector through the
accepted Stage 1D calibration workflow for two exact scopes:

```text
US / EQUITY       / 1d / triangle / ascending_triangle
US / FIXED_INCOME / 1d / triangle / ascending_triangle
```

The Pilot tests geometry definition consistency: stable horizontal resistance,
rising support, fit, convergence, apex, touch sequence, containment, duration,
and fail-closed history handling. It does not predict future price direction,
optimize returns, or define a bullish trading strategy. The Detector and its
runtime Development calibration were not changed.

The complete flow executed as:

```text
Dataset Preparation
        ↓
Development Definition Review
        ↓
Two Versioned Parameter Attempts
        ↓
Immutable Parameter + Dataset Freeze
        ↓
Chronological Holdout
        ↓
One-time Untouched Validation
        ↓
Governance Promotion Assessment
```

The declared source is:

```text
WEALTHPILOT_DETERMINISTIC_ASCENDING_TRIANGLE_PILOT_V1
```

Therefore this is process validation only, not production calibration. No IBKR
or other external Provider request was made. Production promotion still
requires real source-hashed US Stock/ETF data and independent human chart
review.

## B. Dataset

`AscendingTriangleCalibrationDatasetManifest` reuses the Stage 1D immutable
Dataset Manifest, Freeze, and Validation contracts. It maintains separate exact
Equity and Fixed Income manifests because `economic_asset_class` is part of the
calibration key.

Each dataset freezes:

| Field | Pilot value/meaning |
| --- | --- |
| instrument | AAPL, SPY, XLK, AGG, TLT, or LQD fixture role |
| market | `US` |
| economic_asset_class | `EQUITY` or `FIXED_INCOME` |
| timeframe | `1d` |
| date_range | exact deterministic closed-session range |
| provider | `WEALTHPILOT_DETERMINISTIC_ASCENDING_TRIANGLE_PILOT_V1` |
| source_bar_hash | SHA-256 of the exact OHLCV fixture |
| adjustment_policy | `SYNTHETIC_NO_CORPORATE_ACTION_ADJUSTMENT` |
| calendar_version | `WP_US_WEEKDAY_PILOT_CALENDAR_V1` |
| partition | development, holdout, or untouched validation |
| label | visible only for Development in the frozen manifest |
| review_status | completed for Development; sealed for later partitions |

Manifest hashes:

| Scope | Hash |
| --- | --- |
| Combined Pilot | `7fd51fa6983868201746c6eafc2e9223817d4be4593a8592f9203fd4b651fc5b` |
| Equity | `3f91671f415dc75a22a088975452dee0baba86b6e63030cb312aa4056438fd4f` |
| Fixed Income | `67712b940768fcebebbaf08acb53fa99291edfee47d7d4e61a217ef33b8e47de` |

### Partition and coverage

| Scope | Development | Holdout | Untouched | Total |
| --- | ---: | ---: | ---: | ---: |
| Equity | 14 | 4 | 4 | 22 |
| Fixed Income | 14 | 4 | 4 | 22 |
| Combined | 28 | 8 | 8 | 44 |

Both manifests enforce:

```text
end(development) < start(holdout)
end(holdout) < start(untouched_validation)
```

Dataset identities and source hashes are disjoint. Holdout and Untouched labels
are null and `SEALED` at freeze. Asset coverage includes ordinary stock,
broad-market ETF, sector ETF, and fixed-income ETF roles. Regimes include bull,
bear, sideways, high-volatility, and low-volatility cases.

Every Development partition includes all 14 requested Geometry cases:

1. clean Ascending Triangle;
2. Rectangle mistaken as Triangle;
3. rising-support slope too weak;
4. descending support;
5. unstable resistance;
6. insufficient pivots;
7. insufficient touches;
8. poor line fit;
9. weak convergence;
10. meaningless apex;
11. apex too close;
12. apex too far;
13. structure broken before direction confirmation;
14. insufficient history.

Symbols describe fixture roles, not authoritative conId/ISIN mappings.

## C. Label Distribution

Labels describe geometry definition consistency only.

The two economic classes use the same distribution:

| Partition | Positive | Negative | Ambiguous | Review disagreement |
| --- | ---: | ---: | ---: | ---: |
| Development | 2 | 12 | 0 | 0 |
| Holdout | 2 | 2 | 0 | 0 |
| Untouched validation | 1 | 3 | 0 | 0 |
| Total per class | 5 | 17 | 0 | 0 |

The two Development positives are a clean pending-direction structure and a
valid structure later invalidated before direction confirmation. The latter is
positive geometry evidence plus a later technical lifecycle fact; it is not a
profitable or failed trade label.

## D. Parameter Attempts

Calibration version:

```text
wp-us-ascending-triangle-pilot-calibration-v1
```

| Scope | Attempt | Minimum contraction | Definition matches | Structure-only / Pending / Direction-confirmed | Parameter hash |
| --- | ---: | ---: | ---: | --- | --- |
| Equity | 1 | 0.12 | 13/14 | 3 / 3 / 0 | `d3c67e436b0064ba2aa4a9b05736df5ac43a18b849b532ea79aab59e37f4977f` |
| Equity | 2 frozen | 0.25 | 14/14 | 2 / 2 / 0 | `d3f01223a981a1061f8b27c66e81849d5104429aedd43b1af1108b70f2753bfc` |
| Fixed Income | 1 | 0.12 | 13/14 | 3 / 3 / 0 | `4120887ac28eb93c1075ba8b024a07e8983ab3abbe9a990d8091933c7759384d` |
| Fixed Income | 2 frozen | 0.25 | 14/14 | 2 / 2 / 0 | `497e4667857831b23d61c7b65b067aa86ff5c55af814b601c9c2088b18e2721a` |

Attempt 1 admitted the Development weak-convergence fixture as a false
structure in each economic class. Attempt 2 raised the minimum contraction to
0.25 using Development evidence only. The clear geometry remained confirmed.

All other parameters are explicit and hashed, including Pivot windows and
separation, three touches per side, resistance tolerance, slope limits, line-fit
error, containment, duration, apex bounds, invalidation buffer, expiry, and
decisive-close margin. There are no hidden defaults, symbol hardcodes, BTC
fallbacks, or crypto parameters.

## E. Frozen Calibration Version

Frozen metadata:

```text
calibration_stage = pilot_frozen_not_production
parameter_origin = stage1d4_geometry_fixture_pilot
parameter_attempt_count = 2
freeze_date = 2026-08-21
```

| Scope | Frozen version ID |
| --- | --- |
| Equity | `calver_0e780a31dbac9d333654` |
| Fixed Income | `calver_332c6b89e93a0064184e` |

Each immutable version binds the six-dimensional key, exact parameter hash,
dataset manifest hash, Development-only attempt history, review lineage, and
freeze date. Holdout was not opened before freeze, and exposed Holdout evidence
cannot be reused as unseen evidence in a later cycle.

## F. Development Result

| Scope | Attempt 1 | Frozen attempt 2 |
| --- | --- | --- |
| Equity | FAIL: one weak-convergence false structure | PASS: 14/14 |
| Fixed Income | FAIL: one weak-convergence false structure | PASS: 14/14 |

Frozen behavior:

- three stable resistance and three rising-support Pivot facts are required;
- horizontal resistance and rising support are fitted by session ordinal;
- Rectangle, descending support, trend-like, unstable-resistance, insufficient
  source, poor-fit, weak-convergence, and invalid apex fixtures fail closed;
- insufficient history raises the existing explicit history error;
- no indicator dependency is introduced;
- clean geometry produces structure confirmation with pending direction;
- a later support-line break invalidates a previously valid structure without
  retroactively confirming direction.

The Detector does not emit a rejected-geometry reason code when discovery
returns no candidate. Consequently the manifest proves each targeted fixture's
fail-closed outcome, but does not claim isolated gate telemetry for overlapping
apex/convergence conditions. This is recorded as an observability limitation,
not silently treated as production evidence.

## G. Holdout Result

Holdout opened only after the parameter and dataset freeze.

| Scope | Samples | Positive / Negative | False positive | False negative | Result |
| --- | ---: | --- | ---: | ---: | --- |
| Equity | 4 | 2 / 2 | 0 | 0 | PASS |
| Fixed Income | 4 | 2 / 2 | 0 | 0 | PASS |

Evaluation IDs:

```text
Equity       caleval_3f8956845dbde609ab5d
Fixed Income caleval_16a1ca937c36cab37369
```

No Holdout result was used to modify the frozen parameters.

## H. Untouched Validation Result

Untouched Validation opened only after the corresponding Holdout passed.

| Scope | Samples | Positive / Negative | False positive | False negative | Result |
| --- | ---: | --- | ---: | ---: | --- |
| Equity | 4 | 1 / 3 | 0 | 0 | PASS |
| Fixed Income | 4 | 1 / 3 | 0 | 0 | PASS |

Evaluation IDs:

```text
Equity       caleval_aa1036c2a45a27250a39
Fixed Income caleval_f016df51c6913e065ba9
```

Holdout and Untouched use the same exact frozen parameter and manifest hashes.

## I. Failure Modes

Observed during Development:

```text
weak_convergence_false_structure
```

Covered after freeze:

- parallel Rectangle support;
- weak or descending support slope;
- unstable or insufficient resistance touches;
- insufficient Pivot facts;
- poor support-line fit;
- weak convergence;
- parallel/meaningless apex geometry;
- apex too close or too far;
- structure invalidation before directional confirmation;
- insufficient history.

Evidence limitations:

- no live or historical IBKR query was performed;
- no real corporate-action-adjusted series was calibrated;
- weekday sessions are deterministic fixtures, not exchange-calendar replay;
- Holdout and Untouched oracle labels are packaged in the fixture catalog, so
  sealing validates workflow mechanics rather than empirical blinding;
- fixture reviewer records exercise the review contract but do not replace
  independent human chart review;
- overlapping geometry guards are not exposed as per-rejection diagnostics;
- the same Pilot parameters are used for both economic classes and are not
  production Fixed Income thresholds;
- case counts are reported directly; no percentage accuracy is manufactured.

Deterministic combined result hash:

```text
d9eec9f3bda3a156efc723cbcc00aac8b12752d9e19a23a5477d6cada2ec8f70
```

## J. Structure / Direction Boundary Review

The existing contract is preserved exactly:

```text
Ascending Triangle geometry exists
→ structure_confirmation = CONFIRMED
→ direction_confirmation = PENDING
```

The candidate descriptor retains its existing bullish structural context, but
that is not a confirmed future direction. Only a later closed session clearing
the resistance by the separate decisive-close margin may set direction to
`CONFIRMED`. No Pilot fixture contains that later breakout fact.

Frozen Development counts per economic class:

```text
structure-only confirmed = 2
direction pending = 2
direction confirmed = 0
```

One of the two structure-only cases later becomes technically invalidated while
direction remains pending. Calibration did not lower the breakout margin or
reuse structure confirmation as directional confirmation.

## K. Promotion Recommendation

Both exact scopes produce:

```text
Detector PASS
Calibration Frozen PASS
Holdout PASS
Untouched Validation PASS
Fixture review-contract PASS
Coverage-contract PASS
PromotionRecommendation = READY_FOR_GOVERNANCE_REVIEW
```

This is the maximum allowed result for deterministic fixtures. It means the
calibration-process implementation is reviewable; it does not mean
`PRODUCTION_READY`.

Production promotion remains blocked until a later authorized cycle adds:

```text
real source-hashed US Stock/ETF data
+ authoritative exchange calendar and adjustment semantics
+ independent human review of resistance, support, Pivot sequence and apex
+ unchanged Development/Holdout/Untouched discipline
```

The existing runtime calibration remains `development_only`, and no Decision or
product integration was added.

## Automated Validation

| Gate | Result |
| --- | --- |
| Ascending Triangle Pilot + Detector + Golden Parity | `33 passed, 0 failed` |
| Technical Pattern + Pattern Data targeted | `196 passed, 0 failed` |
| Full pytest | `735 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS (`0 errors, 0 warnings`) |
| Frontend build | PASS (existing non-blocking large-chunk advisory only) |
| Offline M5 | `18/18`, `public_network_attempts=0` |

## Safety

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Tovest modification = 0
Decision integration = 0
External Provider call = 0
```

## Final Acceptance

```text
Ascending Triangle Dataset Manifest PASS
Calibration Workflow PASS
Partition Separation PASS
Parameter Freeze PASS
Geometry Definition Review PASS
Holdout Validation PASS
Untouched Validation PASS
Anti-overfitting Gate PASS
Structure/Direction Boundary PASS
```

Final process verdict:

```text
ASCENDING_TRIANGLE_CALIBRATION_PROCESS_VALIDATED
```
