# Breakdown Calibration Pilot Report

> Stage: 1D-2
>
> Date: 2026-08-21
>
> Branch: `codex/breakdown-calibration-pilot`
>
> Stage 1D-1 base: `4eb69aba7c4721f1ca2a255960aca528b66c99e8`

## Executive Conclusion

Stage 1D-2 applies the accepted Breakout calibration workflow to the opposite Level Break direction. It validates two exact scopes:

```text
US / EQUITY       / 1d / level_break / breakdown
US / FIXED_INCOME / 1d / level_break / breakdown
```

The Pilot verifies that Breakdown can be treated as reproducible bearish technical evidence without turning it into a short-selling strategy. The detector implementation was not changed. No financial payoff, return, loss, win rate, ranking, probability, alpha, entry, cover, or order field participates in calibration.

The complete flow executed as:

```text
Dataset Preparation
        ↓
Development Definition Review
        ↓
Two Parameter Attempts
        ↓
Immutable Parameter + Dataset Freeze
        ↓
Chronological Holdout Validation
        ↓
One-time Untouched Validation
        ↓
Governance Promotion Assessment
```

This is deterministic process-validation evidence. Its declared source is:

```text
WEALTHPILOT_DETERMINISTIC_BREAKDOWN_PILOT_V1
```

The local IBKR Gateway was not listening during this task, so no historical Provider request was made. Real source-hashed market datasets and independent product-owner chart review remain mandatory before production promotion.

## A. Dataset

### Manifest contract

`BreakdownCalibrationDatasetManifest` contains separate immutable Equity and Fixed Income manifests because `economic_asset_class` is part of the exact calibration key.

Each dataset freezes:

| Field | Pilot value/meaning |
| --- | --- |
| instrument | AAPL, SPY, XLK, AGG, TLT, or LQD fixture role |
| market | `US` |
| economic_asset_class | `EQUITY` or `FIXED_INCOME` |
| timeframe | `1d` |
| date_range | exact deterministic closed-session range |
| provider | `WEALTHPILOT_DETERMINISTIC_BREAKDOWN_PILOT_V1` |
| source_bar_hash | SHA-256 of the exact OHLCV fixture |
| adjustment_policy | `SYNTHETIC_NO_CORPORATE_ACTION_ADJUSTMENT` |
| calendar_version | `WP_US_WEEKDAY_PILOT_CALENDAR_V1` |
| partition | development, holdout, or untouched validation |
| label | visible only for Development in the frozen manifest |
| review_status | completed for Development; sealed for later partitions |

Manifest hashes:

| Scope | Hash |
| --- | --- |
| Combined Pilot | `980448823572c723fc4277233f605d952c97c28a79abf529df5e1163f8d46b0a` |
| Equity | `d62d6a5a3908ba35f53bf8a92df097179a0bbc3c1e12fa47064f8d4ffaab8c82` |
| Fixed Income | `e28f5da62c030946ee37405692ca3a5358c042e70f9865138d1176265faf307b` |

### Partition separation

Both exact manifests enforce:

```text
end(development) < start(holdout)
end(holdout) < start(untouched_validation)
```

Dataset identities and source hashes are disjoint. Holdout and Untouched labels are null and `SEALED` in the frozen manifest. Their review records are created only when the corresponding partition is opened.

### Coverage

| Scope | Development | Holdout | Untouched | Total |
| --- | ---: | ---: | ---: | ---: |
| Equity | 7 | 3 | 3 | 13 |
| Fixed Income | 7 | 2 | 2 | 11 |
| Combined | 14 | 5 | 5 | 24 |

Asset roles:

- ordinary US stock: AAPL;
- broad-market ETF: SPY;
- sector ETF: XLK;
- fixed-income ETFs: AGG, TLT, and LQD.

Regimes cover bull, bear, sideways, high-volatility, and low-volatility contexts.

The frozen catalog includes all seven requested definition cases:

- clean breakdown;
- fake breakdown;
- low-volume breakdown;
- gap breakdown;
- insufficient structure;
- failed breakdown;
- support failure without bearish confirmation.

Symbols describe asset roles only. OHLCV and instrument IDs are deterministic fixtures, not downloaded historical records.

## B. Label Distribution

Labels describe definition consistency only.

### Equity

| Partition | Positive | Negative | Ambiguous | Review disagreement |
| --- | ---: | ---: | ---: | ---: |
| Development | 3 | 4 | 0 | 0 |
| Holdout | 2 | 1 | 0 | 0 |
| Untouched validation | 1 | 2 | 0 | 0 |
| Total | 6 | 7 | 0 | 0 |

### Fixed Income

| Partition | Positive | Negative | Ambiguous | Review disagreement |
| --- | ---: | ---: | ---: | ---: |
| Development | 3 | 4 | 0 | 0 |
| Holdout | 1 | 1 | 0 | 0 |
| Untouched validation | 1 | 1 | 0 | 0 |
| Total | 5 | 6 | 0 | 0 |

A failed Breakdown is `positive` when support, price, volume, and bearish direction evidence was confirmed before a later recovery invalidated the technical structure. That lifecycle fact is not a trading outcome.

## C. Parameter Versions

Calibration version:

```text
wp-us-breakdown-pilot-calibration-v1
```

| Scope | Attempt | Minimum support touches | Definition matches | Parameter hash |
| --- | ---: | ---: | ---: | --- |
| Equity | 1 | 1 | 6/7 | `ee002457acf4ee49b890ac731d1742819ab8775a2d0b27fda621f06d43b8b141` |
| Equity | 2 frozen | 2 | 7/7 | `8c844e61bfb924f11d18f7e3abe03142017f97f73297e8a6753f94c6bbf175db` |
| Fixed Income | 1 | 1 | 6/7 | `4eaefcd485694a3fb3eedd394e67d1baf8c1eee1cc63f802902cee640cf74e3d` |
| Fixed Income | 2 frozen | 2 | 7/7 | `9c8169a2d5dbc937c26682f3d817da74e5424fe60b80e4d7f360d5b4bdccbae1` |

Attempt 1 inherited the one-touch support hypothesis and produced one insufficient-structure false positive in each economic class. Attempt 2 required two causally available support touches and used Development evidence only.

Other numeric values remain explicit US hypotheses. Frozen metadata is:

```text
calibration_stage = pilot_frozen_not_production
parameter_origin = stage1d2_definition_fixture_pilot
```

Frozen version IDs:

| Scope | Version ID |
| --- | --- |
| Equity | `calver_d53f7175386dc00e0e68` |
| Fixed Income | `calver_40fcfc4fed51e3ea78c5` |

The exact key, parameter hash, manifest hash, two-attempt history, reviewer lineage, and freeze date are immutable. No BTC or crypto fallback is registered.

## D. Development Result

| Scope | Attempt 1 | Attempt 2 |
| --- | --- | --- |
| Equity | FAIL: one insufficient-structure false positive | PASS: 7/7 |
| Fixed Income | FAIL: one insufficient-structure false positive | PASS: 7/7 |

Frozen definition behavior:

- clean and gap Breakdown cases confirm support, price, volume, and bearish direction evidence;
- fake and low-volume cases do not receive direction confirmation;
- one-touch support failure remains unconfirmed;
- a support price/volume failure with `EMA20 > EMA50` remains a candidate because bearish direction confirmation is absent;
- a confirmed Breakdown may later become technically invalidated after recovery above its invalidation boundary.

The EMA guard is therefore not cosmetic: price falling below support is insufficient by itself for a confirmed Breakdown result.

## E. Holdout Result

Holdout opened only after parameters and manifests were frozen.

| Scope | Samples | Positive / Negative | False positive | False negative | Result |
| --- | ---: | --- | ---: | ---: | --- |
| Equity | 3 | 2 / 1 | 0 | 0 | PASS |
| Fixed Income | 2 | 1 / 1 | 0 | 0 | PASS |

Evaluation IDs:

```text
Equity       caleval_1b3fa53f6670729dcf0f
Fixed Income caleval_41b29a295b5536d174a1
```

No Holdout observation was used to change the frozen parameters.

## F. Untouched Validation Result

Untouched Validation opened only after the corresponding Holdout passed.

| Scope | Samples | Positive / Negative | False positive | False negative | Result |
| --- | ---: | --- | ---: | ---: | --- |
| Equity | 3 | 1 / 2 | 0 | 0 | PASS |
| Fixed Income | 2 | 1 / 1 | 0 | 0 | PASS |

Evaluation IDs:

```text
Equity       caleval_136e2eee15ca8d053b22
Fixed Income caleval_bbd00f9a868778ceb2a5
```

Holdout and Untouched use the same exact frozen parameter and manifest hashes.

## G. Failure Modes

Observed during Development:

```text
one_touch_support_false_positive
```

Covered after freeze:

- support price failure with insufficient relative volume;
- low-volume failure followed by immediate support recovery;
- apparent failure of a one-touch, non-authoritative support;
- support failure without bearish EMA alignment;
- confirmed Breakdown followed by later technical recovery/invalidation;
- gap Breakdown with otherwise complete evidence.

Evidence limitations:

- no live or historical IBKR query was performed;
- no real corporate-action-adjusted series was calibrated;
- weekday sessions are deterministic fixtures rather than exchange-calendar replay;
- symbols are role labels rather than authoritative conId/ISIN mappings;
- Holdout and Untouched oracle labels are packaged in the deterministic catalog, so sealing validates contract mechanics rather than empirical blinding;
- reviewer records exercise the review gate but do not replace independent human chart review;
- the small fixture set cannot justify production thresholds.

Deterministic combined result hash:

```text
f2cc0168284e94fdf10e252387db6f063af14cac4669eddf9b720f582f26db9c
```

## H. Promotion Recommendation

Both exact scopes produce:

```text
Detector PASS
Calibration Frozen PASS
Holdout PASS
Untouched Validation PASS
Review-contract PASS
Coverage-contract PASS
PromotionRecommendation = READY_FOR_GOVERNANCE_REVIEW
```

This means the Breakdown calibration-process implementation is reviewable. It does not mean Breakdown is production-calibrated, suitable for short selling, or connected to Decision.

Production promotion remains blocked until a later authorized execution adds:

```text
real source-hashed US Stock/ETF data
+ authoritative exchange calendar and adjustment semantics
+ independent human chart review
+ unchanged Development/Holdout/Untouched discipline
```

The existing runtime calibration remains `development_only` and no product integration was added.

## Automated Validation

| Gate | Result |
| --- | --- |
| Level Break Pilot + Stage 1D targeted | `51 passed, 0 failed` |
| Technical Pattern + Pattern Data targeted | `175 passed, 0 failed` |
| Full pytest | `714 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS (`0 errors, 0 warnings`) |
| Frontend build | PASS (existing non-blocking large-chunk advisory only) |
| Offline M5 | `18/18`, `public_network_attempts=0` |

Targeted regression coverage includes manifest authority and sealing, all seven
Breakdown cases, two-attempt Development-only refinement, exact immutable
freezes, BTC fallback rejection, bearish EMA confirmation, failed-Breakdown
invalidation, Holdout-before-Untouched ordering, deterministic hashes, and the
absence of payoff, short-strategy, or product-integration contracts.

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
Breakdown Dataset Manifest PASS
Calibration Workflow PASS
Partition Separation PASS
Parameter Freeze PASS
Holdout Validation PASS
Untouched Validation PASS
Anti-overfitting Gate PASS
```

Final process verdict:

```text
BREAKDOWN_CALIBRATION_PROCESS_VALIDATED
```
