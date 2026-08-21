# Breakout Calibration Pilot Report

> Stage: 1D-1
>
> Date: 2026-08-21
>
> Branch: `codex/breakout-calibration-pilot`
>
> Stage 1D base: `465956934bb8bbe16a4dcc5a4e434a3c7d9b6599`

## A. Executive Conclusion

Stage 1D-1 validates the complete Breakout calibration workflow across two exact US Daily scopes:

```text
US / EQUITY       / 1d / level_break / breakout
US / FIXED_INCOME / 1d / level_break / breakout
```

The Pilot executes:

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

The Breakout detector implementation was not changed. No financial return, loss, win rate, ranking, probability, alpha, or trade result participates in labels or parameter selection.

This is a deterministic process-validation Pilot. Its source provider is explicitly:

```text
WEALTHPILOT_DETERMINISTIC_BREAKOUT_PILOT_V1
```

It does not claim empirical production calibration. The local IBKR Gateway was not listening during this task, and no network or Provider request was made. Real source-hashed IBKR datasets and an independent product-owner review remain prerequisites for any later production promotion.

## B. Dataset

### Manifest contract

`BreakoutCalibrationDatasetManifest` contains two exact immutable `CalibrationDatasetManifest` objects because economic asset class is part of the calibration key. Every dataset item contains:

| Required field | Pilot contract |
| --- | --- |
| instrument | AAPL, SPY, XLK, AGG, TLT, or LQD fixture identity |
| market | `US` |
| economic_asset_class | `EQUITY` or `FIXED_INCOME` |
| timeframe | `1d` |
| date_range | exact generated closed-session window |
| provider | `WEALTHPILOT_DETERMINISTIC_BREAKOUT_PILOT_V1` |
| source_bar_hash | SHA-256 over the exact deterministic OHLCV sequence |
| adjustment_policy | `SYNTHETIC_NO_CORPORATE_ACTION_ADJUSTMENT` |
| calendar_version | `WP_US_WEEKDAY_PILOT_CALENDAR_V1` |
| partition | development, holdout, or untouched validation |
| label | visible for Development; null while Holdout/Untouched is sealed |
| review_status | `COMPLETED` for Development; `SEALED` before later review |

Combined Pilot manifest hash:

```text
67fe906bd182c9844fcea75981c79dde879e9e407c6e5458c49a3abc54584e0d
```

Exact manifest hashes:

| Scope | Manifest hash |
| --- | --- |
| Equity | `fcb941cb90cd53c097943de74906b2b388d4480eb3877d389c50312e9542fb0b` |
| Fixed Income | `d0da079ae7f102bcb563315219ac95f75b3e21f3de58062f648b422edacc7748` |

### Partition separation

Each exact scope enforces:

```text
end(development) < start(holdout)
end(holdout) < start(untouched_validation)
```

Source bar hashes and dataset identities are disjoint. Holdout and untouched labels are absent from the manifest at freeze and become review records only when their partition is opened.

### Sample coverage

| Scope | Development | Holdout | Untouched | Total |
| --- | ---: | ---: | ---: | ---: |
| Equity | 6 | 3 | 3 | 12 |
| Fixed Income | 5 | 2 | 2 | 9 |
| Combined | 11 | 5 | 5 | 21 |

Asset roles cover:

- ordinary stock: AAPL;
- broad-market ETF: SPY;
- sector ETF: XLK;
- fixed-income ETFs: AGG, TLT, and LQD.

The frozen catalog covers bull, bear, sideways, high-volatility, and low-volatility contexts, together with all six required Breakout definition cases:

- clean breakout;
- fake breakout;
- low-volume breakout;
- gap breakout;
- insufficient structure;
- failed breakout.

These symbol names express US asset roles only. Their OHLCV and instrument IDs are deterministic fixtures rather than downloaded historical records.

## C. Label Distribution

Labels answer whether a Breakout definition is satisfied, not whether the subsequent move made money.

### Equity

| Partition | Positive | Negative | Ambiguous | Review disagreement |
| --- | ---: | ---: | ---: | ---: |
| Development | 3 | 3 | 0 | 0 |
| Holdout | 2 | 1 | 0 | 0 |
| Untouched validation | 1 | 2 | 0 | 0 |
| Total | 6 | 6 | 0 | 0 |

### Fixed Income

| Partition | Positive | Negative | Ambiguous | Review disagreement |
| --- | ---: | ---: | ---: | ---: |
| Development | 3 | 2 | 0 | 0 |
| Holdout | 1 | 1 | 0 | 0 |
| Untouched validation | 1 | 1 | 0 | 0 |
| Total | 5 | 4 | 0 | 0 |

A failed Breakout is labeled `positive` when the boundary, price, volume, and direction definition was confirmed before a later technical invalidation. Later failure does not retroactively turn the original technical fact into a negative sample.

## D. Parameter Versions

Calibration version:

```text
wp-us-breakout-pilot-calibration-v1
```

Two Development-only attempts were recorded independently for each economic class:

| Scope | Attempt | Minimum boundary touches | Definition matches | Parameter hash |
| --- | ---: | ---: | ---: | --- |
| Equity | 1 | 1 | 5/6 | `07c25932d00ceec2a34486a4839460499c8beaf21361f795c60c3991f3f472bd` |
| Equity | 2 frozen | 2 | 6/6 | `2214a7be2d8d0b6a7369de46201c9db1d1cc014816b049bdf0e8721170df90a3` |
| Fixed Income | 1 | 1 | 4/5 | `20ed685ccc7f0bfe938bb4d95bf585a1510d3368d2bab4d2f0d4c8f04a92747e` |
| Fixed Income | 2 frozen | 2 | 5/5 | `fb4d4e684e6bf81fc83de94f9eda2c0766ca0108d777216f738cab52063bb40e` |

Attempt 1 inherited the one-touch development hypothesis. In both scopes it confirmed the deliberately insufficient-structure sample, producing one definition false positive. Attempt 2 changed only the boundary-definition requirement relevant to that failure: at least two available resistance touches. It used Development evidence only.

All other numeric values remain explicit US development hypotheses. The frozen parameter metadata is:

```text
calibration_stage = pilot_frozen_not_production
parameter_origin = stage1d1_definition_fixture_pilot
```

Frozen version IDs:

| Scope | Version ID |
| --- | --- |
| Equity | `calver_51690126e99b820ab758` |
| Fixed Income | `calver_cecd4293db95950f5195` |

Every freeze binds the exact six-dimensional key, parameter hash, dataset manifest hash, two-attempt history, reviewer lineage, and freeze date. No parameter was changed after Holdout opened.

## E. Development Result

| Scope | Attempt 1 | Attempt 2 |
| --- | --- | --- |
| Equity | FAIL: 1 insufficient-structure false positive | PASS: 6/6 definition matches |
| Fixed Income | FAIL: 1 insufficient-structure false positive | PASS: 5/5 definition matches |

Definition behavior after freeze:

- clean and gap Breakouts confirm with an authoritative boundary and sufficient volume;
- fake and low-volume Breakouts do not receive direction confirmation;
- insufficient structure remains unconfirmed because one touch is not authoritative;
- failed Breakouts confirm first and later transition to technical invalidation.

No payoff after the sample window was read or scored.

## F. Holdout Result

Holdout opened only after both parameter sets and manifests were frozen.

| Scope | Samples | Positive / Negative | False positive | False negative | Result |
| --- | ---: | --- | ---: | ---: | --- |
| Equity | 3 | 2 / 1 | 0 | 0 | PASS |
| Fixed Income | 2 | 1 / 1 | 0 | 0 | PASS |

Evaluation IDs:

```text
Equity       caleval_b7fba0ddda27c0d0049c
Fixed Income caleval_799bf87495719e90a31e
```

No Holdout evidence was used to alter parameters.

## G. Untouched Validation Result

Untouched validation opened only after the corresponding frozen Holdout passed.

| Scope | Samples | Positive / Negative | False positive | False negative | Result |
| --- | ---: | --- | ---: | ---: | --- |
| Equity | 3 | 1 / 2 | 0 | 0 | PASS |
| Fixed Income | 2 | 1 / 1 | 0 | 0 | PASS |

Evaluation IDs:

```text
Equity       caleval_a2fadbe8271e15b413de
Fixed Income caleval_a5673b3f9339c057bc96
```

The same frozen parameter and manifest hashes were used for Holdout and Untouched Validation.

## H. Review and Anti-overfitting Evidence

Every sample review records:

- frozen dataset identity and partition;
- positive or negative definition label;
- definition-conformance result;
- false-positive and false-negative facts;
- boundary ambiguity state;
- reviewer identity, notes, and date.

The deterministic review fixture contains no unresolved ambiguous or disagreement cases. The Stage 1D human-review gate is exercised by frozen reviewer records for process validation; this is not a substitute for independent human review of real market charts.

Anti-overfitting controls passed:

1. both attempts reference Development only;
2. Holdout and Untouched labels remain sealed at freeze;
3. the final attempt hash exactly matches the frozen parameter hash;
4. Holdout uses the frozen parameter and partition hashes;
5. Untouched cannot open before Holdout passes;
6. opened non-development source hashes enter the exposure ledger and cannot be reused as unseen evidence;
7. evaluations and frozen versions are immutable;
8. repeated execution produces the same combined result hash:

```text
d120d363510cede50153dcaf6f2db570878a6adbe0539c87753f4c57c0870f4c
```

## I. Failure Modes

Observed during Development:

```text
one_touch_boundary_false_positive
```

Covered and correctly handled after freeze:

- price break without sufficient volume;
- apparent Breakout followed by immediate re-entry without direction confirmation;
- price/volume break over a boundary with insufficient structural touches;
- confirmed Breakout followed by later technical invalidation;
- gap Breakout with otherwise complete definition evidence.

Known evidence limitations:

- no live or historical IBKR request was made;
- no real corporate-action-adjusted bar series was calibrated;
- weekday sessions are deterministic fixtures, not an exchange calendar replay;
- fixture symbol names are asset-role representatives, not authoritative conId/ISIN mappings;
- Holdout and Untouched oracle labels live in the deterministic test catalog, so
  sealing validates the framework contract but is not independent empirical blinding;
- reviewer records validate workflow mechanics, not independent product-owner chart review;
- sample size is deliberately small and unsuitable for production threshold claims.

## J. Promotion Recommendation

The Stage 1D framework result for both exact scopes is:

```text
Detector PASS
Calibration Frozen PASS
Holdout PASS
Untouched Validation PASS
Review-contract PASS
Coverage-contract PASS
PromotionRecommendation = READY_FOR_GOVERNANCE_REVIEW
```

This recommendation means the calibration process implementation is reviewable. It does not mean the Breakout detector is production-calibrated.

Production promotion remains blocked until a later authorized execution supplies:

```text
real source-hashed US Stock/ETF data
+ authoritative exchange calendar and adjustment semantics
+ independent human chart review
+ the same frozen Development/Holdout/Untouched discipline
```

No runtime calibration registry was changed from `development_only`, and the detector remains unavailable to Decision or product UI.

## K. Automated Validation

| Gate | Result |
| --- | --- |
| Breakout Pilot + Stage 1D framework + level-break targeted | `40 passed, 0 failed` |
| Technical Pattern + Pattern Data targeted | `164 passed, 0 failed` |
| Full pytest | `703 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS (`0 errors, 0 warnings`) |
| Frontend build | PASS (existing non-blocking large-chunk advisory only) |
| Offline M5 | `18/18`, `public_network_attempts=0` |

Targeted regression tests verify manifest authority fields, sealed labels,
chronology, all six definition cases, two-attempt Development-only refinement,
parameter/dataset hash freeze, Holdout-before-Untouched ordering, failed-Breakout
lifecycle semantics, label distribution, deterministic reruns, and absence of
financial-outcome or product-integration contracts.

## L. Safety

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

## M. Final Acceptance

```text
Breakout Dataset Manifest PASS
Calibration Workflow PASS
Partition Separation PASS
Parameter Freeze PASS
Holdout Validation PASS
Untouched Validation PASS
Anti-overfitting Gate PASS
```

Final process verdict:

```text
BREAKOUT_CALIBRATION_PROCESS_VALIDATED
```
