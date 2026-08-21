# Double Reversal Detector Migration Report

> Stage: 1C-4
>
> Date: 2026-08-21
>
> Branch: `codex/double-reversal-detector-migration`
>
> Stage 1C-3 base: `e68d803dfed0c541ba2273c2440d4d4dc06d114e`
>
> Tovest source freeze: `tpg-v1.10@937edb62727f4d8c36d41b36e93521d077da20f9`

## A. Migration Summary

Stage 1C-4 migrates the last two launch detectors, `double_top` and `double_bottom`, into the WealthPilot-owned Pattern stack:

```text
CanonicalPatternSeries
        ↓
PatternCoreInput (closed exchange sessions)
        ↓
PivotEngine
        ↓
BoundaryTrendEngine / Neckline Logic
        ↓
DoubleReversalDetector
        ├── DoubleTopDetector
        └── DoubleBottomDetector
        ↓
PatternCandidate
        ↓
Structure Confirmation
        ↓
Direction Confirmation
        ↓
Technical Invalidation + Lifecycle
        ↓
PatternResult
```

The shared implementation preserves the frozen Tovest four-Pivot reversal structure but does not turn a structure into a recommendation. `PatternDirection.BEARISH` and `PatternDirection.BULLISH` describe technical context only. No BUY, SELL, entry, position, allocation, target order, stop order, or execution instruction is produced.

No other reversal family, Head and Shoulders, Wedge, `PatternEvidenceBundle` production mapping, Decision, UI, Portfolio, ExecutionPlan, Broker, Order, Scanner, Scheduler, Telegram, or Trade Plan integration was added.

## B. Detector Architecture

The shared implementation is contained in:

```text
backend/services/technical_patterns/detectors/double_reversal.py
```

For each closed-session evaluation point, discovery builds an independent causal prefix and:

1. replays `PivotEngine` and consumes only confirmed, available Pivots;
2. takes one bounded suffix of exactly four strictly ordered alternating Pivots;
3. requires `low-high-low-high` for Double Top or `high-low-high-low` for Double Bottom;
4. replays `BoundaryTrendEngine` and requires an active, available neckline Boundary backed by the intervening Pivot;
5. checks similarity, preceding trend, intervening reaction, extreme separation, and structure duration against explicit calibration;
6. emits exact Pivot and Boundary source lineage, geometry facts, and a stable semantic episode identity;
7. keeps structure and later directional confirmation as separate contracts.

Candidate identity is independent of database and UI IDs. It includes the detector version, exact calibration/parameter-set identity, causal source facts, geometry, and source lineage through the Stage 1B framework.

## C. Neckline Design

The neckline is a horizontal technical boundary derived from the confirmed intervening Pivot:

| Pattern | Pivot sequence | Extreme pair | Neckline source | Boundary role |
| --- | --- | --- | --- | --- |
| Double Top | low → high → low → high | two highs | intervening swing low | support |
| Double Bottom | high → low → high → low | two lows | intervening swing high | resistance |

The candidate records:

- first and second extreme prices;
- extreme reference and similarity ratio;
- extreme separation and structure duration in session ordinals;
- neckline price, configured tolerance band, source Pivot ID, Boundary ID and role;
- neckline availability session;
- preceding-trend and intervening-reaction ratios;
- pre-confirmation extreme invalidation boundary;
- classical measured-move reference as technical geometry only;
- exact source Pivot and Boundary lineage.

The neckline is explicitly marked as `technical_evidence_only`. It is not an entry price, limit price, stop, recommendation, or execution field.

### Confirmation separation

Structure Confirmation answers only:

```text
Do two comparable confirmed extremes and an available neckline exist?
```

At structure availability:

```text
structure_confirmation = CONFIRMED
direction_confirmation = PENDING
lifecycle = CANDIDATE
```

Direction Confirmation requires a later independently closed session:

- Double Top: close below the neckline plus the explicit break margin;
- Double Bottom: close above the neckline plus the explicit break margin and the required relative-volume threshold.

Only then does technical lifecycle become `CONFIRMED`. A Double Top is therefore not a SELL signal, and a Double Bottom is not a BUY signal.

### Technical invalidation

Before direction confirmation, a buffered close beyond the two-extreme structure invalidates the candidate as `closed_session_breached_extreme_structure`, matching the frozen source safety behavior.

After direction confirmation:

- Double Top: a later closed-session recovery above the neckline records `closed_session_recovered_above_neckline`;
- Double Bottom: a later closed-session failure below the neckline records `closed_session_failed_below_neckline`.

The observed session and bar/Pivot/Boundary lineage are retained. These mean that a technical structure is no longer valid; they do not mean that a trade failed. Session expiry remains a separate configured Lifecycle Core fact.

## D. Indicator Dependencies and TA-Lib Boundary

The frozen Tovest implementation has an intentional asymmetry:

- Double Top downside confirmation uses relative volume only as context;
- Double Bottom upside confirmation requires relative volume of at least `1.20` in the frozen fixture.

Consequently, the audit result is:

| Indicator | Dependency | Role |
| --- | --- | --- |
| EMA | none | not requested |
| RSI | none | not requested |
| MACD | none | not requested |
| ATR | none | not requested |
| Volume SMA | explicit | prior-session average for causal relative-volume evidence |

Both concrete detectors declare one versioned `SMA(volume)` definition. They do not import or invoke TA-Lib directly. The only path is:

```text
Detector
    ↓
Canonical Indicator Layer
    ↓
TA-Lib
```

The current bar's volume is divided by the Volume SMA value from the prior closed session. This preserves the source trailing-volume meaning without allowing the confirmation bar to contaminate its own baseline.

## E. Calibration Contract

Every run requires the exact six-dimensional key:

```text
market
+ economic_asset_class
+ timeframe
+ pattern_family
+ pattern_type
+ calibration_version
```

All required values are explicit: Pivot windows/separation/plateau tolerance, four-Pivot source count, peak/trough similarity, minimum extreme separation, maximum structure duration, intervening-reaction and preceding-trend ratios, Boundary tolerance, neckline tolerance, direction-break margin, Double Bottom volume threshold/lookback, invalidation buffer, and expiry sessions.

Missing values raise `CalibrationNotConfigured`; the concrete detector also rejects a calibration whose `pattern_type_contract` does not match. No hidden numeric default exists.

`build_us_double_reversal_development_parameter_sets()` registers exactly:

```text
US / EQUITY       / 1d / reversal / double_top
US / FIXED_INCOME / 1d / reversal / double_top
US / EQUITY       / 1d / reversal / double_bottom
US / FIXED_INCOME / 1d / reversal / double_bottom
```

The version is `wp-us-double-reversal-development-v1`, stage is `development_only`, and origin is `wealthpilot_us_hypothesis_not_validated`. These values are engineering hypotheses, not promoted empirical calibration. CRYPTO/BTC lookup fails closed and no crypto fallback exists.

## F. Golden Parity Result

Fixture:

```text
tests/technical_patterns/fixtures/tovest_tpg_v1_10_double_reversal_golden.json
```

The fixture freezes the exact Tovest commit and blob IDs:

```text
double_pattern.py       b77a38902cd7104ef6bb28ccd7b0b0df4230ab1f
reversal_common.py      98c5d1e2b2dd5a026223797984a85d16e49235b7
test_reversal_patterns  bd4504a49531b5ac9b27ce330c52b0549c6d938e
```

The oracle was executed from a temporary `git archive`; the Tovest working tree was neither switched nor modified.

Comparison covers both patterns:

- exact four-Pivot sequence and two extreme prices;
- similarity, preceding-trend, and intervening-reaction ratios;
- neckline, invalidation boundary, measured-move reference, and volume role;
- structure `CONFIRMED` while direction remains `PENDING`;
- later direction confirmation, including the Double Bottom `1.50` relative-volume fixture;
- pre-confirmation extreme-breach source semantics;
- WealthPilot post-confirmation neckline invalidation and session expiry;
- deterministic candidate identity, result hashes, and Pivot/Boundary lineage.

Result:

```text
Double Top structure parity       PASS
Double Bottom structure parity    PASS
Direction confirmation parity     PASS
Identity / lineage parity         PASS
Lifecycle parity                  PASS
```

Intentional adaptations are explicit in the fixture:

- source fixed timeframe seconds become dense exchange-session ordinal distance;
- WealthPilot stable lineage identity replaces Tovest runtime/product identity;
- source four-Pivot geometry must also carry a real available `BoundaryTrendEngine` neckline reference;
- source `confirmed_structure` maps to separate structure-confirmed/direction-pending state;
- WealthPilot adds explicit post-confirmation neckline recovery/failure and configured session expiry;
- frozen BTC fixture values are code-regression evidence only and are not registered as US calibration.

## G. US Adaptation

The production implementation contains no BTC symbol, Binance Provider, continuous 24×7 clock, fixed interval seconds, fixed UTC offset, or crypto-volatility assumption. It consumes provider-neutral Daily `PatternCoreInput`, whose adapter boundary already carries IBKR session dates, timezone, adjustment policy, calendar version, closed-bar lineage, and source hash.

All separation, duration, availability, confirmation, invalidation, and expiry arithmetic uses dense exchange-session ordinals. Weekends and exchange holidays do not count as reversal sessions. Independently truncated replay equals a longer source evaluated at the same session, so future Pivots, necklines, volume, and confirmation bars cannot leak backward.

Production calibration promotion still requires disjoint US Stock/ETF development, holdout, and untouched validation datasets.

## H. Tests

Targeted coverage includes:

- clean Double Top and Double Bottom four-Pivot structures;
- exact peaks/troughs, similarity, separation, duration, neckline, invalidation boundary, and lineage;
- structure confirmed while direction remains pending;
- later Double Top downside close without a volume hard gate;
- Double Bottom upside close requiring relative volume;
- pre-confirmation extreme breach and post-confirmation neckline recovery/failure;
- session-ordinal expiry;
- single peak, single valley, asymmetric double peak, asymmetric double trough, shallow/unclear neckline, weak preceding trend, and trend continuation;
- insufficient history, missing calibration, pattern-binding mismatch, and missing parameter rejection;
- future Pivot, neckline, volume, and direction-confirmation isolation;
- independently truncated prefix replay equality;
- repeated candidate identity and result-hash stability;
- exact US Equity/Fixed-Income calibrations and forbidden BTC fallback;
- frozen Tovest structure, confirmation, identity, lineage, invalidation, and lifecycle parity;
- absence of Provider, Product, Decision, Broker, Order, direct TA-Lib, and fixed-clock coupling.

Repository validation:

| Gate | Result |
| --- | --- |
| Double Reversal targeted | `28 passed, 0 failed` |
| Technical Pattern + Pattern Data targeted | `141 passed, 0 failed` |
| Full pytest | `680 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS, 0 errors/warnings |
| Frontend build | PASS; existing non-blocking large-chunk notice only |
| Deterministic Offline M5 | `18/18`; provider `offline_fixture`; public network attempts `0` |

## I. Known Limitations

- US thresholds remain development hypotheses; no empirical promotion is claimed.
- Only closed Daily sessions are supported by the current canonical data boundary.
- Split adjustment and exchange-calendar authority remain upstream adapter responsibilities.
- The Double Bottom volume threshold preserves frozen source semantics but still requires US empirical calibration before promotion.
- Discovery causally replays Pivot and Boundary state per session; scanner-scale optimization and benchmarking are outside this stage.
- Measured-move reference is geometry evidence only and is not connected to a price target, action, or order.
- No probability, ranking, recommendation, portfolio interpretation, product rendering, Decision, or execution integration is present.

## Final Acceptance

```text
Double Top detector PASS
Double Bottom detector PASS

Candidate contract PASS
Structure confirmation PASS
Direction confirmation PASS
Invalidation contract PASS
Lifecycle PASS

TA-Lib boundary PASS
Golden parity PASS
No-future-fact PASS
Calibration lookup PASS
Negative cases PASS

Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Tovest modification = 0
Decision integration = 0

READY_FOR_SIX_PATTERN_COMPLETE
```
