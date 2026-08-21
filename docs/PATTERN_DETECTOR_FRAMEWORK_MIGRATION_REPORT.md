# Pattern Detector Framework Migration Report

> Stage: 1B
>
> Date: 2026-08-21
>
> Branch: `codex/pattern-detector-framework-migration`
>
> Stage 1A base: `codex/pattern-core-foundation-migration@186740a4487467c19cd54205182719cd25702d08`
>
> Tovest source freeze: `tpg-v1.10@937edb62727f4d8c36d41b36e93521d077da20f9`

## A. Migration Summary

Stage 1B establishes a WealthPilot-owned, provider-independent Detector Framework over the validated Stage 1A Pattern Core:

```text
CanonicalPatternSeries
        ↓
PatternCoreInput (causal closed-session prefix)
        ↓
Exact Calibration Registry
        ↓
Canonical Indicator Layer (TA-Lib authority)
        ↓
Detector Framework
        ↓
PatternCandidate
        ↓
Structure Confirmation + Direction Confirmation
        ↓
Technical Invalidation + Lifecycle
        ↓
PatternResult
```

No concrete `breakout`, `breakdown`, `rectangle`, `ascending_triangle`, `double_top`, or `double_bottom` detector was implemented. No `PatternEvidenceBundle`, Decision, UI, portfolio, ExecutionPlan, broker, order, scheduler, scanner, publishing, Telegram, or Trade Plan integration was added.

Final Stage 1B status:

```text
READY_FOR_SIX_PATTERN_MIGRATION
```

## B. New Detector Architecture

```text
backend/services/technical_patterns/
├── calibration/
│   ├── __init__.py
│   └── registry.py
├── indicators/
│   ├── __init__.py
│   ├── contracts.py
│   └── talib_layer.py
└── detectors/
    ├── __init__.py
    ├── contracts.py
    ├── framework.py
    └── parity.py

tests/technical_patterns/
├── fixtures/
│   └── tovest_tpg_v1_10_detector_framework_golden.json
├── test_calibration_registry.py
├── test_detector_framework.py
├── test_detector_golden_parity.py
└── test_indicator_layer.py
```

The production `detectors/` package intentionally contains only contracts, framework orchestration, and parity utilities. A static architecture Gate fails if a concrete detector module or Provider/Product/Broker/Decision import appears in Stage 1B.

The framework receives only `PatternCoreInput`. It has no import or runtime dependency on IBKR, Tovest, portfolio state, Decision services, or product presentation.

## C. Contract Design

### C.1 Candidate

`PatternCandidate` carries only deterministic technical evidence:

- stable candidate identity and identity version;
- instrument and Daily timeframe;
- pattern family, type, and evidence direction;
- formed, available, and evaluated exchange-session ordinals/dates;
- causal source-bar hash;
- typed source pivot and boundary references with their availability;
- geometry and structure facts with source lineage;
- explicit direction-confirmation requirement and optional session expiry;
- detector, calibration, parameter-set, and indicator-layer versions.

The candidate ID is derived from source structures, session facts, geometry, detector version, and the exact parameter-set ID. It does not use a database ID, UI ID, order ID, or presentation text.

### C.2 Confirmation

Structure and direction confirmation are separate contracts and separate evaluator interfaces:

```text
StructureConfirmationEvaluator
DirectionConfirmationEvaluator
```

Structure confirmation alone does not confirm a directional pattern. A direction-independent structure uses the explicit `NOT_REQUIRED` state rather than pretending a directional fact exists. Confirmation assessments carry their own observed session, reason, facts, and lineage.

No confirmation state is translated into a trading signal. `PatternResult` is technical evidence only.

### C.3 Invalidation

`InvalidationAssessment` requires:

- a typed technical condition;
- explicit invalidated/not-invalidated state;
- invalidation reason and observed session when invalidated;
- causal evidence facts.

It feeds the Stage 1A technical lifecycle and never represents trade profit/loss, execution result, publishing eligibility, or content lifecycle.

### C.4 Lifecycle integration

The framework composes assessments over the existing `LifecycleCore`:

```text
candidate → confirmed → invalidated / expired
candidate → invalidated / expired
```

Structure and required direction confirmation must both be available before `confirmed`. Invalidation still wins if it is observed on the same session as confirmation. Terminal behavior remains owned by Stage 1A.

### C.5 Causality enforcement

Before a detector runs, the framework:

1. trims `PatternCoreInput` to `evaluation_session_ordinal`;
2. computes a new causal hash from that exact closed-bar prefix;
3. sends only the prefix and its aligned indicators to the detector;
4. rejects candidate, confirmation, invalidation, pivot, boundary, geometry, or structure facts that are unavailable at the evaluation session.

A rejected future-fact candidate is omitted from results and returned as an auditable `RejectedCandidate`; it cannot silently become evidence. Prefix replay over a longer source series and an independently truncated series produces the same result and identity.

## D. Parameter Registry

`CalibrationKey` binds all required dimensions exactly:

```text
market
+ economic_asset_class
+ timeframe
+ pattern_family
+ pattern_type
+ calibration_version
```

`DetectorParameterSet` adds:

- explicitly named immutable values;
- explicit minimum history;
- deterministic parameter-set ID and full parameter hash.

`CalibrationRegistry.resolve()` performs exact lookup only. Missing keys raise `CalibrationNotConfigured`; no market, economic asset class, family, pattern, version, crypto, or BTC fallback exists. Parameters have no default-return API: an absent named value also fails closed.

No production US Stock/ETF parameter values were introduced in this stage. Calibration contents remain a Stage 1C concern for each concrete detector.

## E. TA-Lib Integration Boundary

The canonical indicator layer is now the only Pattern boundary permitted to call TA-Lib:

```text
Detector
    ↓
CanonicalIndicatorLayer
    ↓
TalibIndicatorLayer
    ↓
TA-Lib
```

The repository dependency is `TA-Lib>=0.6.5,<0.7`; the local acceptance run used `0.6.8`.

The layer currently supports explicit definitions for:

- EMA;
- RSI;
- ATR;
- MACD;
- SMA over close or volume.

Every definition requires explicit periods and source. Results preserve:

- source-bar hash and evaluation session;
- layer version;
- TA-Lib version;
- requested definitions;
- aligned output columns;
- deterministic `NaN → None` warm-up semantics;
- first valid session ordinal;
- deterministic result hash.

Detector modules contain no `talib` import or direct `talib.RSI()` / `talib.ATR()` call. Actual indicator profiles and periods will be supplied by each exact detector calibration rather than hidden framework constants.

## F. Golden Parity Framework

Golden fixture:

```text
tests/technical_patterns/fixtures/
└── tovest_tpg_v1_10_detector_framework_golden.json
```

The fixture freezes:

- Tovest tag, commit, tree, source paths, and blob IDs;
- the intentional wall-clock → exchange-session adaptation;
- the mapped framework-level candidate/confirmation/invalidation/lifecycle contract;
- the exact WealthPilot identity and source lineage.

`GoldenParityComparator` compares:

```text
pattern_type
direction
status
candidate
structure confirmation
direction confirmation
invalidation
lifecycle
identity
```

Enums, IDs, state transitions, source lineage, sessions, and mapping shapes require exact equality. Numeric facts use `abs <= 1e-8` and `rel <= 1e-9`.

This is intentionally a **mapped detector-framework contract parity** result. It does not claim that any of the six concrete Tovest detectors have migrated or passed parity. The fixture records:

```text
concrete_detector_parity = deferred_to_stage_1c
```

## G. Tests and Quality Gates

### G.1 Targeted Pattern stack

```text
tests/technical_patterns
+ backend/services/pattern_data/tests
= 52 passed / 0 failed
```

Coverage includes:

- candidate construction and repeated stable identity;
- separate structure/direction confirmation;
- direction-not-required semantics;
- confirmation and invalidation lifecycle transitions;
- same-session invalidation precedence;
- causal prefix replay and future-bar isolation;
- future pivot and future confirmation rejection;
- confirmation facts cannot arrive after their claimed confirmation session;
- six-dimensional exact calibration lookup;
- missing calibration and BTC fallback fail-closed;
- real TA-Lib EMA/RSI/ATR/MACD/volume-SMA execution;
- warm-up/NaN alignment and deterministic indicator hash;
- framework Golden parity and numeric tolerance failure diagnostics;
- absence of concrete detectors and forbidden runtime couplings.

### G.2 Repository gates

| Gate | Result |
| --- | --- |
| Full pytest | `591 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS, 0 errors/warnings |
| Frontend build | PASS; existing non-blocking large-chunk notice only |
| Offline M5 | Not required: Decision integration is explicitly absent |

All Pattern tests are deterministic and offline. They use no IBKR session, market Provider, LLM, Search, broker, order, personal database, or Tovest runtime.

## H. Remaining Detector Migration Plan

Stage 1C may now migrate detectors one at a time in the frozen order:

1. `breakout`;
2. `breakdown`;
3. `rectangle`;
4. `ascending_triangle`;
5. `double_top`;
6. `double_bottom`.

Each detector must supply, before acceptance:

- its own descriptor and explicit indicator definitions;
- exact US Stock/ETF calibration records with immutable versions;
- a detector-specific frozen Tovest fixture and field-level parity result;
- stock/ETF development, holdout, and untouched validation evidence;
- prefix/no-future-fact tests;
- negative and near-miss cases;
- no direct Provider, TA-Lib, Decision, Portfolio, ExecutionPlan, or Product dependency.

`PatternEvidenceBundle` mapping and Decision integration remain later stages after all six detector contracts and calibrations stabilize.

## I. Safety and Final Acceptance

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Tovest modification = 0
Decision integration = 0
Concrete detector migration = 0
```

Acceptance result:

```text
Candidate contract PASS
Confirmation contract PASS
Invalidation contract PASS
Lifecycle integration PASS
Parameter registry PASS
TA-Lib boundary PASS
Golden parity framework PASS
No-future-fact PASS

READY_FOR_SIX_PATTERN_MIGRATION
```
