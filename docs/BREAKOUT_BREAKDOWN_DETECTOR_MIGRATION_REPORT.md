# Breakout / Breakdown Detector Migration Report

> Stage: 1C-1
>
> Date: 2026-08-21
>
> Branch: `codex/breakout-breakdown-detector-migration`
>
> Stage 1B base: `d1d17c4b87e71e0f14d05638078eace82db8fc61`
>
> Tovest source freeze: `tpg-v1.10@937edb62727f4d8c36d41b36e93521d077da20f9`

## A. Migration Summary

Stage 1C-1 migrates only the `level_break` family members `breakout` and `breakdown` into the WealthPilot-owned Pattern stack:

```text
CanonicalPatternSeries
        ↓
PatternCoreInput (closed exchange sessions)
        ↓
Canonical Indicator Layer (TA-Lib)
        ↓
BreakoutDetector / BreakdownDetector
        ↓
PatternCandidate
        ↓
Structure + Direction Confirmation
        ↓
Technical Invalidation + Lifecycle
        ↓
PatternResult
```

The output is technical evidence only. A bearish `breakdown` is not a short instruction, and neither detector emits BUY, SELL, entry, stop-loss, take-profit, position, allocation, order, or execution semantics.

No rectangle, triangle, double-top, double-bottom, `PatternEvidenceBundle` production mapping, Decision, UI, Portfolio, ExecutionPlan, Broker, Order, Scanner, Scheduler, Telegram, or Trade Plan integration was added.

## B. Detector Architecture

The concrete implementation is contained in:

```text
backend/services/technical_patterns/detectors/level_break.py
```

The shared `LevelBreakDetector` owns causal discovery, while `BreakoutDetector` and `BreakdownDetector` supply only direction, pattern type, and boundary role. Separate evaluators preserve the Stage 1B contracts:

- `LevelBreakStructureConfirmation`: confirms a closed-session price crossing;
- `LevelBreakDirectionConfirmation`: confirms volume plus source-boundary quality, and for breakdown also requires `EMA20 <= EMA50`;
- `LevelBreakInvalidation`: scans only post-trigger closed sessions against an explicit technical invalidation boundary.

### Breakout

- source level: maximum high in the configured pre-trigger lookback;
- source boundary: resistance zone whose width is the greater of an explicit percent width and source-session ATR width;
- trigger: close at or above zone upper edge plus explicit decisive/ATR margin;
- direction confirmation: prior-session volume average threshold plus explicit boundary touch/age evidence;
- invalidation: later closed-session close at or below the stored breakout invalidation boundary;
- expiry: trigger session ordinal plus configured sessions.

### Breakdown

- source level: minimum low in the configured pre-trigger lookback;
- source boundary: support zone with the same explicit width contract;
- trigger: strict close below source support minus any explicitly configured margin;
- direction confirmation: prior-session volume average, explicit boundary quality, and bearish EMA alignment;
- invalidation: later closed-session close at or above the stored breakdown invalidation boundary;
- expiry: trigger session ordinal plus configured sessions.

The boundary is built only from bars before the trigger. Its source reference is available on the last pre-trigger closed session; trigger/confirmation evidence is available on the trigger session. Evidence facts retain boundary and bar lineage. No future bar participates in discovery, confirmation, identity, or lifecycle before its session becomes available.

## C. Indicator Dependencies

Both detectors declare the following versioned dependencies through `required_indicators()`:

| Dependency | Definition | Use |
| --- | --- | --- |
| `EMA20` | TA-Lib EMA, period 20 | context; bearish confirmation gate for breakdown |
| `EMA50` | TA-Lib EMA, period 50 | context; bearish confirmation gate for breakdown |
| `ATR14` | TA-Lib ATR, period 14 | source-zone width and decisive break margin |
| `VOLUME_SMA20` | TA-Lib SMA over volume, configured period 20 | trigger volume divided by the prior closed session's average |

The detector package has no `talib` import and makes no direct indicator call. The dependency chain remains:

```text
Detector → CanonicalIndicatorLayer → TalibIndicatorLayer → TA-Lib
```

The detector version is `wp-level-break-detector-v1`; candidate output also records the exact calibration version, parameter-set ID, identity version, and indicator-layer version.

## D. Calibration Contract

Every run requires an exact key:

```text
market
+ economic_asset_class
+ timeframe
+ pattern_family
+ pattern_type
+ calibration_version
```

Level-break parameters are all required explicitly. Missing names raise `CalibrationNotConfigured`; no numeric detector default is used. The contract includes lookback, percent and ATR zone width, decisive margin, prior-volume window and threshold, boundary touch/age requirements, invalidation buffer, and session expiry.

`build_us_level_break_development_parameter_sets()` creates four exact development-only records:

```text
US / EQUITY      / 1d / level_break / breakout
US / EQUITY      / 1d / level_break / breakdown
US / FIXED_INCOME / 1d / level_break / breakout
US / FIXED_INCOME / 1d / level_break / breakdown
```

Their version is `wp-us-level-break-development-v1`, origin is explicitly `wealthpilot_us_hypothesis_not_validated`, and stage is `development_only`. These are scaffolding values, not a claim of completed production calibration. Tovest BTC values exist only inside the frozen code-regression fixture and are never registered as US parameters or fallback.

`CalibrationDatasetManifest` requires disjoint `development`, `holdout`, and untouched `validation` partitions. It rejects cross-partition instrument/hash overlap and enforces the same market, economic asset class, and timeframe binding. No dataset has been populated or tuned in this stage.

## E. Golden Parity Result

Fixture:

```text
tests/technical_patterns/fixtures/tovest_tpg_v1_10_level_break_golden.json
```

The fixture freezes the exact Tovest commit and blob IDs for breakout, breakdown, common boundary logic, level-break event core, feature calculation, and the two source calibration files. The oracle was executed from a temporary `git archive`; the Tovest working tree was read-only and unchanged.

The same numeric OHLCV sequence is mapped from source bar order to WealthPilot closed US session ordinals. Comparison covers:

- pattern type and bullish/bearish evidence direction;
- source boundary axis and zone;
- empty source-pivot lineage and one stable rolling-boundary lineage;
- trigger close, volume ratio, structure confirmation, and direction confirmation;
- stable candidate identity and candidate/result hashes;
- invalidation boundary;
- `confirmed → invalidated` and `confirmed → expired` lifecycle paths.

Result:

```text
Breakout Golden parity  PASS
Breakdown Golden parity PASS
Lifecycle parity        PASS
```

Intentional adaptations are recorded, not hidden:

- Tovest product/database IDs are replaced with deterministic WealthPilot identities;
- source bar order is represented by exchange-session ordinal, not a 24×7 clock;
- source wall-clock expiry is represented by a configured number of closed sessions;
- breakout boundary quality uses explicit touch/age facts rather than importing the crypto authority-score implementation;
- the BTC calibration fixture is code-regression evidence only.

## F. US Adaptation

The implementation contains no BTC symbol, Binance Provider, continuous-24×7, fixed-second, fixed-UTC-offset, or crypto-volatility assumption. It consumes provider-neutral Daily `PatternCoreInput`, which already carries IBKR-derived session dates, timezone, adjustment policy, calendar version, closed-bar lineage, and source hash.

Weekend and holiday handling is therefore upstream and ordinal-based: they do not count as sessions. Volume confirmation uses the average available on the prior closed session, preventing the trigger's own volume and all later volume from leaking into its threshold.

The next calibration task must populate separate Stock/ETF development, holdout, and untouched validation manifests, freeze their source hashes, and publish a new calibration version. It must not promote `development-v1` merely because code-regression fixtures pass.

## G. Tests

Targeted tests cover:

- clean resistance breakout and clean support breakdown;
- volume-confirmed positive cases;
- wick/inside-close invalid structures;
- price break with insufficient volume remaining an unconfirmed candidate;
- insufficient history and missing calibration fail-closed;
- exact US Equity/Fixed-Income key lookup and forbidden BTC fallback;
- future bar and future volume isolation;
- future pivot/reference and future confirmation-fact rejection in the Stage 1B framework;
- independently truncated prefix replay equality;
- deterministic candidate identity, result hash, and causal source hash;
- technical invalidation and session-ordinal expiry;
- disjoint development/holdout/validation dataset contracts;
- frozen Tovest candidate, confirmation, evidence, identity/hash, invalidation, and lifecycle parity;
- absence of Provider, Product, Decision, broker, and direct TA-Lib coupling.

Performance was measured locally over one deterministic 300-session symbol, 30 repetitions, with TA-Lib `0.6.8`:

| Segment | Median | p95 |
| --- | ---: | ---: |
| Indicator calculation | 0.2825 ms | 0.2925 ms |
| Detector discovery | 3.0247 ms | 3.5786 ms |
| Full framework run | 11.6404 ms | 12.3318 ms |

This is a single-symbol engineering measurement, not a scanner benchmark or production SLA.

Cache behavior is unchanged: IBKR Daily series use the Stage 0 read-through cache, refresh, request dedupe, and short negative TTL. The indicator/detector layer deliberately has no separate cache in Stage 1C-1, so its deterministic output is recomputed from the causal source hash on every run.

Repository validation:

| Gate | Result |
| --- | --- |
| Technical Pattern + Pattern Data targeted | `73 passed, 0 failed` |
| Full pytest | `612 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS, 0 errors/warnings |
| Frontend build | PASS; existing non-blocking large-chunk notice only |
| Offline M5 | Not required; Decision integration is unchanged and explicitly absent |

## H. Known Limitations

- US thresholds remain development hypotheses; empirical calibration and promotion are future work.
- The dataset interfaces are enforced but not populated with a broad Stock/ETF corpus in this stage.
- Rolling extrema produce source-boundary lineage without source pivots; pivot-derived level policies may be evaluated in a later calibration version.
- Only closed Daily sessions are supported by the current canonical data contract.
- Split-adjustment and market-calendar authority remain upstream adapter responsibilities.
- Detector discovery is a direct causal scan and has no scanner-scale benchmark or cache.
- No ranking, probability, trade recommendation, portfolio interpretation, or product rendering is present.

## Final Acceptance

```text
Breakout detector PASS
Breakdown detector PASS

Candidate contract PASS
Confirmation contract PASS
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
