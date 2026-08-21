# Ascending Triangle Detector Migration Report

> Stage: 1C-3
>
> Date: 2026-08-21
>
> Branch: `codex/ascending-triangle-detector-migration`
>
> Stage 1C-2 base: `f29a5c9533c7ed93dcd5f82e1edcb4db6c11fce9`
>
> Tovest source freeze: `tpg-v1.10@937edb62727f4d8c36d41b36e93521d077da20f9`

## A. Migration Summary

Stage 1C-3 migrates only `ascending_triangle`, the first Geometry Pattern, into the WealthPilot-owned Pattern stack:

```text
CanonicalPatternSeries
        ↓
PatternCoreInput (closed exchange sessions)
        ↓
PivotEngine
        ↓
BoundaryTrendEngine + Geometry Helpers
        ↓
AscendingTriangleDetector
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

The result is evidence, not a BUY signal. `PatternDirection.BULLISH` records only the structure's directional context. No order, entry, position, allocation, target, stop, or execution meaning is produced.

No descending/symmetrical triangle, wedge, double-top, double-bottom, `PatternEvidenceBundle` production mapping, Decision, UI, Portfolio, ExecutionPlan, Broker, Order, Scanner, Scheduler, Telegram, or Trade Plan integration was added.

## B. Detector Architecture

The implementation is contained in:

```text
backend/services/technical_patterns/detectors/ascending_triangle.py
```

At each closed-session evaluation point, the detector builds an independent causal prefix and:

1. replays `PivotEngine` to obtain only confirmed, available pivots;
2. replays `BoundaryTrendEngine` and requires a stable active resistance boundary backed by at least two source highs;
3. examines only explicitly bounded alternating Pivot suffixes;
4. uses Stage 1A Geometry Helpers to fit upper and lower lines on session ordinals;
5. selects the longest qualifying suffix and deduplicates the same economic episode with stable semantic geometry material;
6. emits exact Pivot and resistance Boundary lineage through the Stage 1B candidate contract.

Candidate identity remains independent of database and UI IDs. Detector version, exact calibration version, parameter-set identity, causal source-bar hash, source pivots, source boundary, and geometry facts remain explicit.

## C. Geometry Design

The required geometry is evaluated on dense exchange-session ordinals:

- upper line: materially horizontal resistance;
- lower line: rising support with an explicit minimum slope;
- touch sequence: alternating resistance/support pivots with independent minimum counts;
- line quality: explicit maximum fit error;
- convergence: positive start and confirmed gaps plus minimum contraction;
- apex: finite, after structure confirmation, within explicit progress and horizon bounds;
- duration: first-to-last source Pivot session span;
- containment: closed prices during formation remain inside the fitted structure within explicit tolerance;
- boundary stability: the resistance zone width remains below its explicit limit.

The evidence contract records both line slopes/intercepts, percentage slopes, fit errors, start/confirmed gaps, contraction, absolute apex session ordinal, apex progress, confirmation-session resistance/support, structure duration, touch sequence, touch counts, and source lineage.

There is no `86400`, fixed timeframe seconds table, wall-clock duration, fixed UTC offset, or calendar-day arithmetic. Weekends and exchange holidays do not count as geometry sessions.

### Confirmation separation

Structure Confirmation answers only:

```text
Does confirmed, available Ascending Triangle geometry exist?
```

At that point:

```text
structure_confirmation = CONFIRMED
direction_confirmation = PENDING
lifecycle = CANDIDATE
```

Direction Confirmation is a separate later fact. It requires a later closed-session close above the fitted resistance line plus the explicit decisive-close margin. Only then does lifecycle become `CONFIRMED`.

A triangle therefore never becomes direction-confirmed merely because the structure exists.

### Invalidation

Invalidation remains technical:

- a later closed-session close below rising support, including the configured buffer, records `closed_session_below_rising_support`;
- reaching the fitted apex without an earlier decisive resistance break records `apex_reached_without_resistance_break`;
- the exact observed session and causal bar/boundary/Pivot lineage are retained;
- session expiry is independently configured and evaluated by Lifecycle Core.

These facts mean that the prior geometry no longer qualifies. They do not mean that a trade failed.

## D. Indicator Dependencies

The frozen Tovest Ascending Triangle path derives structure and direction confirmation from confirmed Pivots, Boundary facts, geometry, and a decisive closed price. It does not require EMA, RSI, MACD, ATR, or Volume. The WealthPilot detector therefore declares:

```text
indicator_dependencies = []
```

No indicator was introduced for framework uniformity, and the detector has no direct `talib` import or invocation. If empirical US calibration later proves that volume is necessary, it must be added through:

```text
Detector
    ↓
Canonical Indicator Layer
    ↓
TA-Lib
```

That would require a new calibration and detector version; it is not silently inferred here.

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

All parameters are explicit:

- Pivot windows, separation, plateau tolerance, minimum/maximum source Pivot count;
- resistance merge tolerance and maximum zone width;
- horizontal resistance slope and relative-flatness ratio;
- minimum rising-support slope;
- maximum line-fit error and containment tolerance;
- minimum contraction;
- minimum/maximum apex progress and maximum apex horizon;
- structure duration and touches per side;
- decisive close margin, invalidation buffer, and expiry sessions.

Missing values raise `CalibrationNotConfigured`; no hidden numeric defaults exist.

`build_us_ascending_triangle_development_parameter_sets()` registers only:

```text
US / EQUITY       / 1d / triangle / ascending_triangle
US / FIXED_INCOME / 1d / triangle / ascending_triangle
```

The version is `wp-us-ascending-triangle-development-v1`, stage is `development_only`, and origin is `wealthpilot_us_hypothesis_not_validated`. These values are engineering scaffolding, not promoted calibration. CRYPTO/BTC lookup fails closed and no crypto fallback exists.

## F. Golden Parity Result

Fixture:

```text
tests/technical_patterns/fixtures/tovest_tpg_v1_10_ascending_triangle_golden.json
```

The fixture freezes the exact Tovest commit and blob IDs for Triangle, source tests, Boundary Trend, and Pivot Engine. The oracle was executed from a temporary `git archive`; the Tovest working tree was not switched or modified.

The source `R-S-R-S` fixture is mapped to closed US session ordinals while preserving relative Pivot spacing. Comparison covers:

- Ascending Triangle subtype and four source Pivots;
- one confirmed resistance Boundary reference;
- upper/lower slopes and normalized percentage slopes;
- line-fit errors, start gap, confirmed gap, and contraction;
- apex distance/progress and twelve-session duration;
- two touches per side and ordered source lineage;
- source `confirmed_structure` mapped to structure `CONFIRMED` plus direction `PENDING`;
- decisive upper close mapped to direction `CONFIRMED`;
- lower-line invalidation and WealthPilot session expiry;
- deterministic candidate ID and candidate/result hashes.

Result:

```text
Geometry parity               PASS
Structure parity              PASS
Direction confirmation parity PASS
Identity parity               PASS
Lifecycle parity              PASS
```

Intentional adaptations are explicit in the fixture:

- source timeframe seconds become dense exchange-session ordinal distance;
- WealthPilot stable source-lineage identity replaces Tovest runtime/product identity;
- WealthPilot requires a real `BoundaryTrendEngine` resistance reference;
- source `confirmed_structure` maps to separate structure-confirmed/direction-pending state instead of collapsing both into one lifecycle state;
- source opposite downside break becomes the explicit lower-trendline technical reason;
- WealthPilot adds configured session-ordinal expiry;
- frozen BTC values are code-regression evidence only and are never registered as US calibration.

## G. US Adaptation

The production implementation contains no BTC symbol, Binance Provider, continuous 24×7 clock, fixed interval seconds, fixed timezone offset, or crypto-volatility assumption. It consumes provider-neutral Daily `PatternCoreInput`, whose adapter boundary already carries IBKR session dates, timezone, adjustment policy, calendar version, closed-bar lineage, and source hash.

Future Pivots, Boundaries, touches, geometry, and breakout closes are excluded until their `available_from_session_ordinal` enters the causal prefix. Independently truncated replay produces the same structure and identity as a longer source evaluated at the same session.

The next calibration task must populate and freeze separate Stock/ETF development, holdout, and untouched validation datasets before promoting a version beyond `development_only`.

## H. Tests

Targeted coverage includes:

- clean Ascending Triangle with stable horizontal resistance and rising support;
- exact slope, convergence, apex, duration, touch, and Boundary lineage facts;
- structure confirmed while direction remains pending;
- later decisive close direction confirmation;
- weak/non-decisive close remaining pending;
- Rectangle instead of Triangle, descending support, weak slope, unstable resistance, and non-ascending geometry;
- insufficient Pivots, insufficient history, meaningless apex, and missing calibration;
- broken support and apex-without-break technical invalidation;
- session-ordinal expiry;
- future Pivot, Boundary, touch, geometry, and direction-confirmation isolation;
- independently truncated prefix replay equality;
- repeated-execution identity/source/result stability;
- exact US Equity/Fixed-Income lookup and forbidden BTC fallback;
- missing parameter rejection with no hidden fallback;
- frozen Tovest geometry, structure, direction, invalidation, identity, and lifecycle parity;
- absence of Provider, Product, Decision, Broker, Order, and direct TA-Lib coupling.

Repository validation:

| Gate | Result |
| --- | --- |
| Ascending Triangle + framework targeted | `31 passed, 0 failed` |
| Technical Pattern + Pattern Data targeted | `113 passed, 0 failed` |
| Full pytest | `652 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS, 0 errors/warnings |
| Frontend build | PASS; existing non-blocking large-chunk notice only |
| Deterministic Offline M5 | `18/18`; public network attempts `0` |

## I. Known Limitations

- US thresholds remain development hypotheses; no empirical promotion is claimed.
- Direction confirmation currently uses a decisive closed price only; Volume is deliberately absent until a versioned US calibration demonstrates its necessity.
- Only closed Daily sessions are supported by the current canonical data boundary.
- Split adjustment and exchange-calendar authority remain upstream adapter responsibilities.
- Discovery causally replays Pivot and Boundary state per session; scanner-scale optimization and benchmarking are outside this stage.
- Only Ascending Triangle is implemented; Descending, Symmetrical, Wedge, and reversal families remain out of scope.
- No probability, ranking, recommendation, portfolio interpretation, product rendering, or execution integration is present.

## Final Acceptance

```text
Ascending Triangle detector PASS

Candidate contract PASS
Structure confirmation PASS
Direction confirmation PASS
Invalidation contract PASS
Lifecycle PASS

Geometry PASS
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

READY_FOR_NEXT_PATTERN_MIGRATION
```
