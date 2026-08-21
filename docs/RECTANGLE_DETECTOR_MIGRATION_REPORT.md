# Rectangle Detector Migration Report

> Stage: 1C-2
>
> Date: 2026-08-21
>
> Branch: `codex/rectangle-detector-migration`
>
> Stage 1C-1 base: `4002dab698d08380ae3e52048dfdf6b18586b5a6`
>
> Tovest source freeze: `tpg-v1.10@937edb62727f4d8c36d41b36e93521d077da20f9`

## A. Migration Summary

Stage 1C-2 migrates only the neutral `rectangle` structure detector into the WealthPilot-owned Pattern stack:

```text
CanonicalPatternSeries
        ↓
PatternCoreInput (closed exchange sessions)
        ↓
PivotEngine
        ↓
BoundaryTrendEngine / RangeStructureEngine
        ↓
RectangleDetector
        ↓
PatternCandidate
        ↓
Structure Confirmation + Direction NOT_REQUIRED
        ↓
Technical Invalidation + Lifecycle
        ↓
PatternResult
```

Rectangle confirms that a bounded range exists. It does not predict a future break and emits no BUY, SELL, bullish, bearish, entry, exit, position, allocation, order, or execution semantics.

No triangle, double-top, double-bottom, Decision, UI, Portfolio, ExecutionPlan, Broker, Order, Scanner, Scheduler, Telegram, or Trade Plan integration was added.

## B. Detector Architecture

The implementation is contained in:

```text
backend/services/technical_patterns/detectors/rectangle.py
```

For each available closed session, discovery builds a causal prefix and replays the Stage 1A core in order:

1. `PivotEngine` produces confirmed pivots with explicit `available_from` sessions.
2. `BoundaryTrendEngine` groups only confirmed, available pivots into support and resistance boundaries.
3. `RangeStructureEngine` selects a legal active support/resistance pair.
4. `RectangleDetector` checks alternating touches, duration, containment, boundary stability, and range width.
5. A deterministic episode key prevents repeated candidates for the same economic boundary pair as later prefixes arrive.

Candidate lineage contains the exact confirmed source pivots and support/resistance boundaries. Candidate identity is independent of database and UI IDs and includes detector/calibration versions through the Stage 1B framework.

All time arithmetic uses `source_session_ordinal` and the source timestamps already carried by `PatternCoreInput`. The detector contains no fixed seconds, fixed UTC offset, or calendar-day duration assumption.

## C. Structure Confirmation Design

Rectangle confirmation means only:

```text
confirmed available range structure exists
```

The qualifying contract is:

- lower and upper boundaries are active and available at evaluation time;
- at least the configured confirmed touches exist on each side;
- touches alternate between support and resistance;
- the first-to-last source-pivot span meets the configured closed-session duration;
- relevant confirmed pivots remain inside the support/resistance envelope;
- both boundary zones satisfy the configured stability width;
- range width is between explicit minimum and maximum percentages;
- support remains strictly below resistance.

The candidate is always `PatternDirection.NEUTRAL`, sets `direction_confirmation_required=False`, and receives framework state `Direction Confirmation = NOT_REQUIRED`. No direction evaluator is installed.

Technical invalidation is evaluated only after candidate availability. A later closed-session close beyond the confirmed lower or upper boundary, including the explicit calibration buffer, records the observed session and reason `closed_session_below_support` or `closed_session_above_resistance`. This is a technical fact, not a trading instruction. Expiry is an explicit number of closed sessions from candidate availability.

## D. Indicator and TA-Lib Boundary

Rectangle depends only on price structure:

```text
indicator_dependencies = []
```

It does not require EMA, RSI, MACD, ATR, or Volume, and no indicator was added merely for framework uniformity. The detector has no `talib` import or direct TA-Lib call. The existing canonical indicator boundary remains intact and receives an empty dependency tuple for this detector.

## E. Calibration Contract

Every run requires an exact six-dimensional key:

```text
market
+ economic_asset_class
+ timeframe
+ pattern_family
+ pattern_type
+ calibration_version
```

All Rectangle values are explicit: pivot windows and separation, boundary tolerance, touch count, structure duration, boundary stability, minimum/maximum range width, invalidation buffer, and expiry sessions. Missing values raise `CalibrationNotConfigured`; there are no hidden numeric defaults.

`build_us_rectangle_development_parameter_sets()` registers only:

```text
US / EQUITY       / 1d / range / rectangle
US / FIXED_INCOME / 1d / range / rectangle
```

The version is `wp-us-rectangle-development-v1`, the stage is `development_only`, and the origin is explicitly `wealthpilot_us_hypothesis_not_validated`. These values are engineering scaffolding and are not production calibration. A CRYPTO/BTC key fails closed; no crypto fallback exists.

## F. Golden Parity Result

Fixture:

```text
tests/technical_patterns/fixtures/tovest_tpg_v1_10_rectangle_golden.json
```

The fixture freezes the exact Tovest commit and blob IDs for Rectangle detection, discovery, Range Structure, and source tests. The oracle was previously executed from a temporary `git archive`; the Tovest repository remained read-only and unchanged.

The same numeric S-R-S-R sequence is mapped from source bar order to closed US session ordinals. Comparison covers:

- neutral Rectangle pattern type and confirmed status;
- support `100`, resistance `110`, range width `10`;
- two touches per side, `SRSR` order, and six-session structure span;
- four source pivots and two stable WealthPilot boundary references;
- structure confirmed and direction `NOT_REQUIRED`;
- deterministic candidate ID, candidate hash, result hash, and source lineage;
- `confirmed → invalidated` and `confirmed → expired` lifecycle paths.

Result:

```text
Rectangle structure parity PASS
Identity parity            PASS
Lifecycle parity           PASS
```

Intentional adaptations are recorded in the fixture:

- source bar order becomes exchange-session ordinal, not a 24×7 clock;
- WealthPilot stable lineage identity replaces Tovest runtime/product identity;
- a later closed-session boundary break is the explicit invalidation fact instead of source candidate-pair reset behavior;
- WealthPilot adds configured session-ordinal expiry because the frozen Tovest Rectangle fixture has no expiry contract;
- frozen Tovest values are code-regression evidence only and are never registered as US calibration.

## G. US Adaptation

The implementation contains no Binance Provider, BTC parameters, continuous-bar assumption, crypto-volatility default, fixed seconds, or fixed timezone offset. It consumes provider-neutral Daily `PatternCoreInput`, whose IBKR adapter boundary already supplies market sessions, timezone, adjustment policy, calendar version, closed-bar lineage, and source hash.

Weekend and holiday gaps therefore do not count as duration. Future boundaries and touches cannot participate until their source pivots are confirmed and their `available_from_session_ordinal` is within the causal prefix.

The next calibration task must populate and freeze separate Stock/ETF development, holdout, and untouched validation datasets before promoting a version beyond `development_only`.

## H. Tests

Targeted coverage includes:

- confirmed neutral Rectangle structure;
- exact support/resistance, width, touch, duration, and source-lineage facts;
- `Direction Confirmation = NOT_REQUIRED`;
- no indicator dependencies and no direct TA-Lib coupling;
- non-rectangle oscillation and one-direction trend;
- unstable boundaries, insufficient touches, too-wide and too-narrow ranges;
- insufficient history and missing calibration fail-closed;
- missing parameter rejection with no hidden fallback;
- future bar, future boundary, and future touch isolation;
- independently truncated prefix replay equality;
- deterministic candidate identity, source hash, and result hash;
- closed-session boundary invalidation and session-ordinal expiry;
- exact US Equity/Fixed-Income calibration and forbidden BTC fallback;
- frozen Tovest structure, identity, invalidation, and lifecycle parity;
- absence of Provider, Product, Decision, Broker, and Order coupling.

Repository validation:

| Gate | Result |
| --- | --- |
| Rectangle + framework targeted | `27 passed, 0 failed` |
| Technical Pattern + Pattern Data targeted | `91 passed, 0 failed` |
| Full pytest | `630 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS, 0 errors/warnings |
| Frontend build | PASS; existing non-blocking large-chunk notice only |
| Offline M5 | Not required; Decision integration is unchanged and explicitly absent |

## I. Known Limitations

- US thresholds remain development hypotheses; no empirical calibration promotion is claimed.
- Only closed Daily sessions are supported by the current canonical data boundary.
- Split adjustment and exchange-calendar authority remain upstream adapter responsibilities.
- Rectangle discovery causally replays Pivot/Boundary/Range for each session; no scanner-scale optimization or benchmark is included.
- A boundary break invalidates the structure; this stage does not convert that break into Breakout/Breakdown evidence or a trading signal.
- No ranking, probability, recommendation, portfolio interpretation, product rendering, or execution integration is present.

## Final Acceptance

```text
Rectangle detector PASS

Candidate contract PASS
Structure confirmation PASS
Direction NOT_REQUIRED PASS
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

READY_FOR_NEXT_PATTERN_MIGRATION
```
