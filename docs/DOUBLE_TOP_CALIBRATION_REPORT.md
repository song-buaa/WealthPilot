# Double Top Calibration Pilot Report

> Stage: 1D-5
>
> Date: 2026-08-22
>
> Branch: `codex/double-top-calibration-pilot`
>
> Stage 1D-4 base: `c22571701a90bded502b550c333479f20103f151`

## A. Executive Conclusion

Stage 1D-5 validates the existing Double Top Detector through the accepted
Stage 1D calibration workflow for two exact scopes:

```text
US / EQUITY       / 1d / reversal / double_top
US / FIXED_INCOME / 1d / reversal / double_top
```

The Pilot checks bearish-reversal definition consistency: two similar peaks,
session separation, intervening reaction, preceding uptrend, neckline evidence,
structure/direction separation, lifecycle invalidation, and fail-closed source
requirements. It does not validate future downside, optimize shorting outcomes,
or change the Detector and its Development calibration.

The complete process executed as:

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
WEALTHPILOT_DETERMINISTIC_DOUBLE_TOP_PILOT_V1
```

Therefore this is process validation only, not production calibration. No IBKR
or other external Provider request was made. Production promotion still
requires real source-hashed US Stock/ETF data and independent human chart
review.

## B. Dataset

`DoubleTopCalibrationDatasetManifest` reuses the Stage 1D immutable Dataset
Manifest, Freeze, and Validation contracts. Equity and Fixed Income remain
separate because `economic_asset_class` is part of the exact calibration key.

Each dataset freezes instrument, market, economic asset class, timeframe, exact
date range, provider, source-bar hash, adjustment policy, calendar version,
partition, label state, review state, asset role, regime, and edge-case role.

| Scope | Development | Holdout | Untouched | Total |
| --- | ---: | ---: | ---: | ---: |
| Equity | 15 | 4 | 4 | 23 |
| Fixed Income | 15 | 4 | 4 | 23 |
| Combined | 30 | 8 | 8 | 46 |

Manifest hashes:

| Scope | Hash |
| --- | --- |
| Combined Pilot | `c51be08b1103a3629a09bdfa44c57a0dbcabe4a93c87fe78566217780ca30e23` |
| Equity | `2914d8e6a1943745476ee59d07c18ad17337d20e5b7b427157ba4907b35b1b33` |
| Fixed Income | `a602934f3242e870b75b7b1ca296d436c7141f8033efec71422693082695335a` |

Both manifests enforce:

```text
end(development) < start(holdout)
end(holdout) < start(untouched_validation)
```

Holdout and Untouched labels are null and `SEALED` at freeze. Dataset identities
and source hashes are disjoint. Asset coverage includes common-stock,
broad-market ETF, sector ETF, and fixed-income ETF roles; all five required
regimes and applicable market edge cases are represented.

Each Development partition covers all 15 frozen reversal cases:

1. clean Double Top;
2. single peak;
3. asymmetric double peak;
4. peaks too close;
5. peaks too far apart;
6. intervening reaction too shallow;
7. weak or missing preceding uptrend;
8. unclear neckline;
9. trend continuation mistaken as reversal;
10. structure invalidated before neckline break;
11. neckline breakdown after valid structure;
12. neckline not broken, direction pending;
13. post-confirmation neckline recovery;
14. insufficient pivots;
15. insufficient history.

The post-confirmation recovery case is an additional lifecycle case needed to
record both pre-confirmation and post-confirmation invalidation counts. Symbols
and conIds describe deterministic fixture roles, not authoritative security
identity mappings.

## C. Label Distribution

Labels describe technical-definition consistency only. They contain no PnL,
return, win-rate, short-profit, or future-downside outcome.

The two economic classes use the same distribution:

| Partition | Positive | Negative | Ambiguous | Review disagreement |
| --- | ---: | ---: | ---: | ---: |
| Development | 5 | 10 | 0 | 0 |
| Holdout | 2 | 2 | 0 | 0 |
| Untouched validation | 2 | 2 | 0 | 0 |
| Total per class | 9 | 14 | 0 | 0 |

Positive means a valid Double Top structure exists using currently available
facts. It does not mean the user should sell or short. A valid structure may
remain direction-pending or later become technically invalidated.

## D. Parameter Attempts

Calibration version:

```text
wp-us-double-top-pilot-calibration-v1
```

| Scope | Attempt | Peak similarity max | Definition matches | Structure-only / Pending / Direction-confirmed / Pre-invalid / Post-invalid | Parameter hash |
| --- | ---: | ---: | ---: | --- | --- |
| Equity | 1 | 10.0% | 14/15 | 3 / 4 / 2 / 1 / 1 | `8b13786f0cef4ed394b689b5cb90f527bc894ba3594e14b679bea2961201722c` |
| Equity | 2 frozen | 2.5% | 15/15 | 2 / 3 / 2 / 1 / 1 | `b0fb57c2c12a7f5d44f0a373592676af65dff17d3e1059bd43bda02003bc78dd` |
| Fixed Income | 1 | 10.0% | 14/15 | 3 / 4 / 2 / 1 / 1 | `fb8ca04fa0cc214039910967205485d2433115938e79436bb3869ff2ee2040ce` |
| Fixed Income | 2 frozen | 2.5% | 15/15 | 2 / 3 / 2 / 1 / 1 | `c70bac0668ee5577dd0e6ef28c2e34cf7c4b558a1ba4da6a0d1a863893e8714f` |

Attempt 1 admitted the asymmetric-peak fixture as a false structure in each
economic class. Attempt 2 tightened peak similarity to 2.5%, using Development
evidence only. Clean structures, pending direction, later breakdown, and both
invalidation paths retained their expected semantics.

The Pilot also freezes explicit Pivot windows, peak separation, maximum
duration, minimum reaction, minimum preceding uptrend, neckline tolerance,
direction break margin, invalidation buffer, expiry, and volume context. There
are no symbol hardcodes, BTC fallbacks, crypto defaults, or hidden parameters.

## E. Frozen Calibration Version

Frozen metadata:

```text
calibration_stage = pilot_frozen_not_production
parameter_origin = stage1d5_reversal_fixture_pilot
parameter_attempt_count = 2
freeze_date = 2026-08-21
```

| Scope | Frozen version ID |
| --- | --- |
| Equity | `calver_ee5405138b98de44022b` |
| Fixed Income | `calver_58804619ea9177c2f3a7` |

Each version binds the exact six-dimensional key, parameter hash, dataset
manifest hash, Development-only attempt history, review lineage, and freeze
date. Holdout was not opened before freeze. Previously exposed Holdout or
Untouched source hashes cannot be reused as unseen evidence.

## F. Development Result

| Scope | Attempt 1 | Frozen attempt 2 |
| --- | --- | --- |
| Equity | FAIL: one asymmetric-peak false structure | PASS: 15/15 |
| Fixed Income | FAIL: one asymmetric-peak false structure | PASS: 15/15 |

Frozen behavior:

- exactly four ordered, confirmed Pivot facts form the source structure;
- the first and second peaks must satisfy the frozen 2.5% similarity limit;
- peak spacing, intervening reaction, preceding uptrend, duration, and neckline
  evidence are required;
- single-peak, asymmetric, too-close, too-far, shallow-reaction, weak-uptrend,
  continuation, insufficient-Pivot, and insufficient-history cases fail closed;
- structure confirmation does not invent direction confirmation;
- later neckline breakdown is a separate closed-session fact;
- extreme breach before direction and neckline recovery after direction remain
  separate technical lifecycle facts.

The current Detector does not emit rejected-candidate reason telemetry when
discovery returns no proposal. The unclear-neckline and shallow-reaction
fixtures therefore prove fail-closed outcomes but cannot isolate every
overlapping rejection gate from result output. This is an observability
limitation, not production evidence.

## G. Holdout Result

Holdout opened only after parameter and dataset freeze.

| Scope | Samples | Positive / Negative | False positive | False negative | Result |
| --- | ---: | --- | ---: | ---: | --- |
| Equity | 4 | 2 / 2 | 0 | 0 | PASS |
| Fixed Income | 4 | 2 / 2 | 0 | 0 | PASS |

Evaluation IDs:

```text
Equity       caleval_0cb54a5171a1c547f23e
Fixed Income caleval_3d64a2ad4ebf010b4222
```

No Holdout result changed the frozen parameters.

## H. Untouched Validation Result

Untouched Validation opened only after the corresponding Holdout passed.

| Scope | Samples | Positive / Negative | False positive | False negative | Result |
| --- | ---: | --- | ---: | ---: | --- |
| Equity | 4 | 2 / 2 | 0 | 0 | PASS |
| Fixed Income | 4 | 2 / 2 | 0 | 0 | PASS |

Evaluation IDs:

```text
Equity       caleval_be7522415d7ee36c5257
Fixed Income caleval_6b21025bc1f0709e0d42
```

Holdout and Untouched use the same exact frozen parameter and manifest hashes.

## I. Failure Modes

Observed during Development:

```text
asymmetric_peak_false_structure_under_10_percent_similarity
```

Covered after freeze:

- ordinary trend continuation and single-high structures fail closed;
- weak preceding trend and shallow reaction do not form reversal evidence;
- too-close and too-far extrema do not form a valid episode;
- missing Pivot/history sources do not become empty-success results;
- structure breach before confirmation and recovery after confirmation remain
  distinct lifecycle evidence;
- missing rejection telemetry limits gate-by-gate attribution for no-proposal
  fixtures.

These deterministic results cannot estimate population false-positive or
false-negative rates.

## J. Structure / Direction Boundary Review

The Pilot preserves the frozen boundary:

```text
valid Double Top structure
    → structure_confirmation = CONFIRMED
    → direction_confirmation = PENDING
```

Only a later closed session decisively below the neckline may produce:

```text
direction_confirmation = CONFIRMED
```

The frozen Development metrics per economic class are:

```text
structure-only confirmed = 2
direction pending        = 3
direction confirmed      = 2
pre-confirm invalidated  = 1
post-confirm invalidated = 1
```

The pending count includes the pre-confirmation-invalidated episode because its
direction fact remains pending. Calibration did not lower the neckline-break
margin to increase confirmed-direction counts.

## K. Indicator / Volume Role Review

Double Top source semantics remain:

```text
Volume = context only
```

The Canonical Indicator Layer still supplies the detector's volume moving
average context. Double Top direction confirmation is based on the later closed
session's neckline break and does not require a minimum volume ratio. The Pilot
does not call TA-Lib directly and does not change Double Bottom's separate
volume contract.

## L. Promotion Recommendation

Both exact scopes return:

```text
READY_FOR_GOVERNANCE_REVIEW
```

They do not return `PRODUCTION_READY`. The remaining promotion evidence is:

1. real source-hashed US Stock/ETF daily series;
2. independent human chart reviewers;
3. reviewed ambiguous and disagreement cases from real charts;
4. a new immutable calibration cycle if Holdout exposes a systematic issue;
5. production-governance approval after the full promotion gate.

## Tests and Safety

Targeted verification:

```text
Double Top Pilot + Double Reversal detector + Golden parity: 39 passed
Technical Pattern + IBKR Pattern Data: 207 passed
Full pytest: 746 passed / 7 skipped / 0 failed
compileall: PASS
frontend lint: PASS (0 errors / 0 warnings)
frontend build: PASS (existing >500 kB advisory only)
Offline M5: 18/18, provider=offline_fixture, public_network_attempts=0
```

Safety outcome:

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

Final state:

```text
DOUBLE_TOP_CALIBRATION_PROCESS_VALIDATED
```
