# Rectangle Calibration Pilot Report

> Stage: 1D-3
>
> Date: 2026-08-21
>
> Branch: `codex/rectangle-calibration-pilot`
>
> Stage 1D-2 base: `545d55ad65ad5660e3b49737f00214cb147b4c6b`

## Executive Conclusion

Stage 1D-3 applies the accepted calibration workflow to the first neutral,
purely structural Pattern. It validates two exact scopes:

```text
US / EQUITY       / 1d / range / rectangle
US / FIXED_INCOME / 1d / range / rectangle
```

The Pilot verifies that Rectangle can be represented as reproducible range
structure evidence. It does not predict a breakout, attach bullish or bearish
semantics, or optimize a financial outcome. The Rectangle Detector was not
changed.

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
WEALTHPILOT_DETERMINISTIC_RECTANGLE_PILOT_V1
```

No IBKR or other external Provider request was made. Real source-hashed market
datasets and independent human chart review remain mandatory before production
promotion.

## A. Dataset

### Manifest contract

`RectangleCalibrationDatasetManifest` contains separate immutable Equity and
Fixed Income manifests because `economic_asset_class` is part of the exact
calibration key.

Each dataset freezes:

| Field | Pilot value/meaning |
| --- | --- |
| instrument | AAPL, SPY, XLK, AGG, TLT, or LQD fixture role |
| market | `US` |
| economic_asset_class | `EQUITY` or `FIXED_INCOME` |
| timeframe | `1d` |
| date_range | exact deterministic closed-session range |
| provider | `WEALTHPILOT_DETERMINISTIC_RECTANGLE_PILOT_V1` |
| source_bar_hash | SHA-256 of the exact OHLCV fixture |
| adjustment_policy | `SYNTHETIC_NO_CORPORATE_ACTION_ADJUSTMENT` |
| calendar_version | `WP_US_WEEKDAY_PILOT_CALENDAR_V1` |
| partition | development, holdout, or untouched validation |
| label | visible only for Development in the frozen manifest |
| review_status | completed for Development; sealed for later partitions |

Manifest hashes:

| Scope | Hash |
| --- | --- |
| Combined Pilot | `6193a766025e538c33916aa1bfbc72508f0a9eab5e7cc8338ef32bb33ee5e1f2` |
| Equity | `4ea2bd28d4718bcdbef985fdc9a142170aed02bc779fa29bbe2b5c4c62196e64` |
| Fixed Income | `8bdaa689e7a9ba2668d37270f0755b5a5e9253f9ea96edde1f8576770fe2bd9e` |

### Partition separation

Both exact manifests enforce:

```text
end(development) < start(holdout)
end(holdout) < start(untouched_validation)
```

Dataset identities and source hashes are disjoint. Holdout and Untouched labels
are null and `SEALED` in the frozen manifest. Their review records are created
only when the corresponding partition is opened.

### Coverage

| Scope | Development | Holdout | Untouched | Total |
| --- | ---: | ---: | ---: | ---: |
| Equity | 8 | 3 | 3 | 14 |
| Fixed Income | 8 | 3 | 3 | 14 |
| Combined | 16 | 6 | 6 | 28 |

Asset roles:

- ordinary US stock: AAPL;
- broad-market ETF: SPY;
- sector ETF: XLK;
- fixed-income ETFs: AGG, TLT, and LQD.

Regimes cover bull, bear, sideways, high-volatility, and low-volatility
contexts. Each Development partition includes every requested definition case:

- clean Rectangle;
- false Rectangle;
- trend mistaken as Rectangle;
- range too narrow;
- range too wide;
- insufficient touches;
- unstable boundaries;
- insufficient history.

Symbols describe asset roles only. OHLCV and instrument IDs are deterministic
fixtures, not downloaded historical records.

## B. Label Distribution

Labels answer only whether valid Rectangle structure exists.

The two economic classes use the same distribution:

| Partition | Positive | Negative | Ambiguous | Review disagreement |
| --- | ---: | ---: | ---: | ---: |
| Development | 1 | 7 | 0 | 0 |
| Holdout | 1 | 2 | 0 | 0 |
| Untouched validation | 1 | 2 | 0 | 0 |
| Total per class | 3 | 11 | 0 | 0 |

`positive` means the available boundaries, touches, duration, width, and
containment satisfy the Rectangle definition. It does not mean the range will
break upward or downward.

## C. Parameter Versions

Calibration version:

```text
wp-us-rectangle-pilot-calibration-v1
```

| Scope | Attempt | Minimum range width | Definition matches | Parameter hash |
| --- | ---: | ---: | ---: | --- |
| Equity | 1 | 0.5% | 7/8 | `70691aeea26802c7f78b7d7536300847cfa5fb3c8d555cb9d869331c09149a60` |
| Equity | 2 frozen | 2.0% | 8/8 | `56d12566276adebb58260d2881633ce1a7714d4b84762102ff26024c436132d4` |
| Fixed Income | 1 | 0.5% | 7/8 | `8a6128dbcc6b6a75b44c15764bc9f30cd1afd713e9c3cb491f090e1e20d62fb9` |
| Fixed Income | 2 frozen | 2.0% | 8/8 | `fc16cd2649dae30fddfa5e81485104fe11d5bd7697f5308cb4d0ff85080ae11f` |

Attempt 1 admitted the Development 1% too-narrow range as a false structure in
each economic class. Attempt 2 raised the minimum width to 2% using Development
evidence only. The 10% clean Rectangle remained confirmed, while trend, false
range, too-wide, insufficient-touch, unstable-boundary, and insufficient-history
cases continued to fail closed.

Other numeric values remain explicit Pilot hypotheses. Frozen metadata is:

```text
calibration_stage = pilot_frozen_not_production
parameter_origin = stage1d3_definition_fixture_pilot
```

Frozen version IDs:

| Scope | Version ID |
| --- | --- |
| Equity | `calver_19ab9cf36ac6e08e23dd` |
| Fixed Income | `calver_4ba21e18c608e64b4361` |

The exact key, parameter hash, manifest hash, two-attempt history, reviewer
lineage, and freeze date are immutable. No BTC or crypto fallback is registered.

## D. Development Result

| Scope | Attempt 1 | Attempt 2 |
| --- | --- | --- |
| Equity | FAIL: one too-narrow false structure | PASS: 8/8 |
| Fixed Income | FAIL: one too-narrow false structure | PASS: 8/8 |

Frozen definition behavior:

- clean ranges confirm only after two support and two resistance touches;
- structure confirmation is `CONFIRMED` while direction confirmation is
  `NOT_REQUIRED`;
- every confirmed candidate remains `NEUTRAL`;
- monotonic trends and malformed ranges do not produce Rectangle results;
- width and boundary-stability limits reject unreasonable structures;
- missing history raises the existing fail-closed history contract rather than
  fabricating evidence;
- `indicator_dependencies = []`; no indicator was added for uniformity.

No breakout, BUY, SELL, bullish, or bearish trading semantics are introduced.

## E. Holdout Result

Holdout opened only after parameters and manifests were frozen.

| Scope | Samples | Positive / Negative | False positive | False negative | Result |
| --- | ---: | --- | ---: | ---: | --- |
| Equity | 3 | 1 / 2 | 0 | 0 | PASS |
| Fixed Income | 3 | 1 / 2 | 0 | 0 | PASS |

Evaluation IDs:

```text
Equity       caleval_57907e18d9be2a337f05
Fixed Income caleval_b6c28835613fc206ad83
```

No Holdout observation was used to change the frozen parameters.

## F. Untouched Validation Result

Untouched Validation opened only after the corresponding Holdout passed.

| Scope | Samples | Positive / Negative | False positive | False negative | Result |
| --- | ---: | --- | ---: | ---: | --- |
| Equity | 3 | 1 / 2 | 0 | 0 | PASS |
| Fixed Income | 3 | 1 / 2 | 0 | 0 | PASS |

Evaluation IDs:

```text
Equity       caleval_e4f807d5bdf783815f9c
Fixed Income caleval_b9ac9ea7f0482692ef7f
```

Holdout and Untouched use the same exact frozen parameter and manifest hashes.

## G. Failure Modes

Observed during Development:

```text
too_narrow_range_false_structure
```

Covered after freeze:

- a monotonic trend mistaken for a range;
- a non-Rectangle with materially displaced support;
- ranges below the minimum or above the maximum width;
- only three alternating boundary touches;
- two supports too far apart to form a stable boundary;
- fewer bars than the frozen minimum-history requirement.

Evidence limitations:

- no live or historical IBKR query was performed;
- no real corporate-action-adjusted series was calibrated;
- weekday sessions are deterministic fixtures rather than exchange-calendar
  replay;
- symbols are role labels rather than authoritative conId/ISIN mappings;
- Holdout and Untouched oracle labels are packaged in the deterministic catalog,
  so sealing validates contract mechanics rather than empirical blinding;
- reviewer records exercise the review gate but do not replace independent
  human chart review;
- the same 2% frozen Pilot threshold is intentionally used for both classes and
  cannot be treated as a production Fixed Income threshold;
- the small fixture set cannot justify production promotion.

Deterministic combined result hash:

```text
1e2dae5e6b0b6ae5e3e62be2359aab6fededc524fb2f0a66ad1cc9b8c9e9ba34
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

This means the Rectangle calibration-process implementation is reviewable. It
does not mean Rectangle is production-calibrated or connected to Decision.

Production promotion remains blocked until a later authorized execution adds:

```text
real source-hashed US Stock/ETF data
+ authoritative exchange calendar and adjustment semantics
+ independent human chart review
+ unchanged Development/Holdout/Untouched discipline
```

The existing runtime calibration remains `development_only`; no product
integration was added.

## Automated Validation

| Gate | Result |
| --- | --- |
| Rectangle Pilot + Detector targeted | `26 passed, 0 failed` |
| Technical Pattern + Pattern Data targeted | `185 passed, 0 failed` |
| Full pytest | `724 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS (`0 errors, 0 warnings`) |
| Frontend build | PASS (existing non-blocking large-chunk advisory only) |
| Offline M5 | `18/18`, `public_network_attempts=0` |

Targeted coverage includes manifest authority and sealing, all eight Rectangle
definition cases, two-attempt Development-only refinement, neutral structure
semantics, explicit `NOT_REQUIRED` direction confirmation, exact immutable
freezes, BTC fallback rejection, Holdout-before-Untouched ordering,
deterministic hashes, and the absence of payoff or product-integration fields.

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
Rectangle Dataset Manifest PASS
Calibration Workflow PASS
Partition Separation PASS
Parameter Freeze PASS
Holdout Validation PASS
Untouched Validation PASS
Anti-overfitting Gate PASS
```

Final process verdict:

```text
RECTANGLE_CALIBRATION_PROCESS_VALIDATED
```
