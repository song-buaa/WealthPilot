# Double Bottom Calibration Pilot Report

> Stage: 1D-6
>
> Date: 2026-08-22
>
> Branch: `codex/double-bottom-calibration-pilot`
>
> Stage 1D-5 base: `17fff3367d4cf43cb0d6971fbc254071acdab856`

## A. Executive Conclusion

Stage 1D-6 validates the existing Double Bottom Detector through the accepted
Stage 1D calibration workflow for two exact scopes:

```text
US / EQUITY       / 1d / reversal / double_bottom
US / FIXED_INCOME / 1d / reversal / double_bottom
```

The Pilot checks bullish-reversal definition consistency and the frozen Volume
Hard Gate: two similar troughs, session separation, intervening reaction,
preceding downtrend, neckline evidence, structure/direction separation, causal
relative volume, lifecycle invalidation, and fail-closed source requirements.
It does not validate future upside, optimize entry outcomes, or change the
Detector and its Development calibration.

The complete process executed as:

```text
Dataset Preparation
        ↓
Development Definition + Volume Review
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
WEALTHPILOT_DETERMINISTIC_DOUBLE_BOTTOM_PILOT_V1
```

Therefore this is process validation only, not production calibration. No IBKR
or other external Provider request was made. Production promotion still
requires real source-hashed US Stock/ETF data and independent human chart
review.

## B. Dataset

`DoubleBottomCalibrationDatasetManifest` reuses the Stage 1D immutable Dataset
Manifest, Freeze, and Validation contracts. Equity and Fixed Income remain
separate because `economic_asset_class` is part of the exact calibration key.

Each dataset freezes instrument, market, economic asset class, timeframe, exact
date range, provider, source-bar hash, adjustment policy, calendar version,
partition, label state, review state, asset role, regime, and edge-case role.

| Scope | Development | Holdout | Untouched | Total |
| --- | ---: | ---: | ---: | ---: |
| Equity | 16 | 4 | 4 | 24 |
| Fixed Income | 16 | 4 | 4 | 24 |
| Combined | 32 | 8 | 8 | 48 |

Manifest hashes:

| Scope | Hash |
| --- | --- |
| Combined Pilot | `a8a15914f97731852b1b2468a0014fcede4fae41d6756a63c1601cc7de22ab7c` |
| Equity | `6a24fc111fc36059964e5669824bdb01f8e379e9e95b2b6b79e371f5d8708af9` |
| Fixed Income | `a19758881ab184cf8e7156e6da5c128043365770dc4bfee01f24fa531c1be55d` |

Both manifests enforce:

```text
end(development) < start(holdout)
end(holdout) < start(untouched_validation)
```

Holdout and Untouched labels are null and `SEALED` at freeze. Dataset identities
and source hashes are disjoint. Asset coverage includes common-stock,
broad-market ETF, sector ETF, and fixed-income ETF roles; all five required
regimes and applicable market edge cases are represented.

Each Development partition covers all 16 frozen reversal/volume cases:

1. clean Double Bottom;
2. single trough;
3. asymmetric double trough;
4. troughs too close;
5. troughs too far apart;
6. intervening reaction too shallow;
7. weak or missing preceding downtrend;
8. unclear neckline;
9. trend continuation mistaken as reversal;
10. structure invalidated before neckline breakout;
11. neckline breakout with sufficient relative volume;
12. the same price breakout with insufficient relative volume;
13. neckline not broken, direction pending;
14. post-confirmation neckline failure;
15. insufficient pivots;
16. insufficient history.

The post-confirmation failure is an additional lifecycle case needed to record
both pre-confirmation and post-confirmation invalidation counts. Symbols and
conIds describe deterministic fixture roles, not authoritative security
identity mappings.

## C. Label Distribution

Labels describe technical-definition consistency only. They contain no PnL,
return, win-rate, long-profit, or future-upside outcome.

The two economic classes use the same distribution:

| Partition | Positive | Negative | Ambiguous | Review disagreement |
| --- | ---: | ---: | ---: | ---: |
| Development | 6 | 10 | 0 | 0 |
| Holdout | 2 | 2 | 0 | 0 |
| Untouched validation | 2 | 2 | 0 | 0 |
| Total per class | 10 | 14 | 0 | 0 |

Positive means a valid Double Bottom structure exists using currently
available facts. It does not mean the user should buy. A valid structure may
remain direction-pending because price has not broken the neckline, because
volume is insufficient, or because the structure later becomes invalidated.

## D. Parameter Attempts

Calibration version:

```text
wp-us-double-bottom-pilot-calibration-v1
```

| Scope | Attempt | Relative volume minimum | Definition matches | Structure-only / Pending / Direction-confirmed / Volume-blocked / Pre-invalid / Post-invalid | Parameter hash |
| --- | ---: | ---: | ---: | --- | --- |
| Equity | 1 | 1.05× | 15/16 | 2 / 3 / 3 / 0 / 1 / 1 | `ad9755b670f40440a9612238904f260cdb755aa0253943e9bec6bf7412a48325` |
| Equity | 2 frozen | 1.20× | 16/16 | 3 / 4 / 2 / 1 / 1 / 1 | `456cf13fe9cddcc0bc44f67717a364ba446b8d0bd780d0167b4cb74d4a7de7d3` |
| Fixed Income | 1 | 1.05× | 15/16 | 2 / 3 / 3 / 0 / 1 / 1 | `3408cba3727ec6e30bdfadad9777f06efc8e337fe9174c355f92dfe497bbba0e` |
| Fixed Income | 2 frozen | 1.20× | 16/16 | 3 / 4 / 2 / 1 / 1 / 1 | `09a272df7b76ad027782c492a115b5bd6d0b287f8123c443bc9eb4ce47578a18` |

Attempt 1 admitted the 1.10× weak-volume price breakout as direction-confirmed
under a 1.05× threshold. Attempt 2 raised the hard gate to 1.20× using
Development evidence only. The identical price breakout then remained pending,
while the 1.50× volume event remained confirmed.

The Pilot also freezes explicit Pivot windows, trough similarity, separation,
maximum duration, minimum reaction, minimum preceding downtrend, neckline
tolerance, direction break margin, five-session volume lookback, invalidation
buffer, and expiry. There are no symbol hardcodes, BTC fallbacks, crypto
defaults, or hidden parameters.

## E. Frozen Calibration Version

Frozen metadata:

```text
calibration_stage = pilot_frozen_not_production
parameter_origin = stage1d6_volume_fixture_pilot
parameter_attempt_count = 2
volume_average_sessions = 5
bottom_volume_ratio_minimum = 1.20
freeze_date = 2026-08-21
```

| Scope | Frozen version ID |
| --- | --- |
| Equity | `calver_2cf1cc493f4d4ee4b668` |
| Fixed Income | `calver_b3c3b0c7d7e7c250ebce` |

Each version binds the exact six-dimensional key, parameter hash, dataset
manifest hash, Development-only attempt history, review lineage, and freeze
date. Holdout was not opened before freeze. Previously exposed Holdout or
Untouched source hashes cannot be reused as unseen evidence.

## F. Development Result

| Scope | Attempt 1 | Frozen attempt 2 |
| --- | --- | --- |
| Equity | FAIL: weak-volume direction false confirmation | PASS: 16/16 |
| Fixed Income | FAIL: weak-volume direction false confirmation | PASS: 16/16 |

Frozen behavior:

- exactly four ordered, confirmed Pivot facts form the source structure;
- trough similarity, spacing, reaction, preceding downtrend, duration, and
  neckline evidence are required;
- single-trough, asymmetric, too-close, too-far, shallow-reaction,
  weak-downtrend, continuation, insufficient-Pivot, and insufficient-history
  cases fail closed;
- structure confirmation does not invent direction confirmation;
- later neckline breakout without 1.20× relative volume remains pending;
- later neckline breakout with 1.50× relative volume confirms direction;
- extreme breach before confirmation and neckline failure after confirmation
  remain separate technical lifecycle facts.

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
Equity       caleval_cce8783979d44f3f42de
Fixed Income caleval_c5ff6bf9e04f60587387
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
Equity       caleval_30afbefdeab59508becb
Fixed Income caleval_2937deec4e8337942d43
```

Holdout and Untouched use the same exact frozen parameter and manifest hashes.

## I. Failure Modes

Observed during Development:

```text
weak_volume_direction_false_confirmation_under_1_05_threshold
```

Covered after freeze:

- ordinary trend continuation and single-low structures fail closed;
- weak preceding downtrend and shallow reaction do not form reversal evidence;
- too-close and too-far extrema do not form a valid episode;
- missing Pivot/history sources do not become empty-success results;
- price-only breakout stays pending when relative volume is below 1.20×;
- structure breach before confirmation and neckline failure after confirmation
  remain distinct lifecycle evidence;
- missing rejection telemetry limits gate-by-gate attribution for no-proposal
  fixtures.

These deterministic results cannot estimate population false-positive or
false-negative rates.

## J. Structure / Direction Boundary Review

The Pilot preserves the frozen boundary:

```text
valid Double Bottom structure
    → structure_confirmation = CONFIRMED
    → direction_confirmation = PENDING
```

Only a later closed session decisively above the neckline and satisfying the
independent Volume Hard Gate may produce:

```text
direction_confirmation = CONFIRMED
```

The frozen Development metrics per economic class are:

```text
structure-only confirmed = 3
direction pending        = 4
direction confirmed      = 2
volume-gate blocked      = 1
pre-confirm invalidated  = 1
post-confirm invalidated = 1
```

The pending count includes the pre-confirmation-invalidated episode because its
direction fact remains pending. Calibration did not lower the neckline-break or
volume threshold to increase confirmed-direction counts.

## K. Volume Hard Gate Review

Double Bottom preserves its deliberate asymmetry with Double Top:

```text
Double Bottom Volume = hard direction-confirmation gate
Double Top Volume    = context only
```

The data path remains:

```text
Detector
    ↓
Canonical Indicator Layer
    ↓
TA-Lib Volume SMA
```

For the frozen five-session baseline, all five prior closed-session volumes are
100. The confirmation bar volume is 150 and the Detector reports exactly 1.50×.
If the current bar had polluted its own SMA baseline, the ratio would instead
be approximately 1.36×. A separate causal replay appended a future 10,000-volume
bar and evaluated at the confirmation session; the confirmation assessment and
1.50× ratio remained identical. Thus neither the current bar nor future volume
enters the baseline.

The same price breakout with volume 110 reports the intended semantic outcome:

```text
1.10 < 1.20
→ structure CONFIRMED
→ direction PENDING
→ volume_gate_blocked = true
```

The Pilot does not calculate a private rolling baseline inside the Detector and
does not call TA-Lib directly.

## L. Promotion Recommendation

Both exact scopes return:

```text
READY_FOR_GOVERNANCE_REVIEW
```

They do not return `PRODUCTION_READY`. The remaining promotion evidence is:

1. real source-hashed US Stock/ETF daily series;
2. independent human chart reviewers;
3. reviewed ambiguous and disagreement cases from real charts;
4. evidence that the 1.20× hard gate is appropriate across real market and
   asset-class regimes rather than over-restrictive;
5. a new immutable calibration cycle if Holdout exposes a systematic issue;
6. production-governance approval after the full promotion gate.

## Stage 1D Completion Note

The six launch-pattern Calibration Pilots are now process-validated:

```text
Breakout              PASS
Breakdown             PASS
Rectangle             PASS
Ascending Triangle    PASS
Double Top            PASS
Double Bottom         PASS
```

This does not mean the six Patterns are production-ready. The next evidence
stage is explicitly:

```text
Real IBKR Source-Hashed Dataset
+ Independent Human Chart Review
+ Production Promotion Review
```

## Tests and Safety

Targeted verification:

```text
Double Bottom Pilot + Double Reversal detector + Golden parity: 40 passed
Technical Pattern + IBKR Pattern Data: 219 passed
Full pytest: 758 passed / 7 skipped / 0 failed
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
DOUBLE_BOTTOM_CALIBRATION_PROCESS_VALIDATED
```
