# Pattern Core Foundation Migration Report

> Stage: 1A
>
> Date: 2026-08-21
>
> Branch: `codex/pattern-core-foundation-migration`
>
> WealthPilot base: `main@5492b1dbead99677e0e84d8a177a78e6e7157036`
>
> Stage 0 Adapter dependency: `codex/ibkr-pattern-data-adapter@982e64054cce436de9491e8208615a5b2e1657b6`
>
> Tovest source freeze: `tpg-v1.10@937edb62727f4d8c36d41b36e93521d077da20f9`

## A. Migration Summary

Stage 1A now provides a deterministic, provider-independent Pattern Core foundation over the Stage 0 `CanonicalPatternSeries` contract:

```text
CanonicalPatternSeries
        ↓
PatternInputMapper
        ↓
PatternCoreInput (closed Daily sessions)
        ↓
PivotEngine
        ↓
BoundaryTrendEngine
        ↓
RangeStructureEngine / Geometry Helpers
        ↓
Identity / Technical Lifecycle Core
```

No detector, indicator calibration, `PatternEvidenceBundle`, Decision Skill/Tool, UI, portfolio, ExecutionPlan, broker, order, scheduler, scanner, publishing, teacher content, Telegram, or Trade Plan integration was added.

The implementation follows the audited migration order. Detector migration did not begin before foundation parity was established.

Final Stage 1A status:

```text
READY_FOR_PATTERN_DETECTOR_MIGRATION
```

## B. Source and Branch Boundary

The Stage 0 Adapter candidate had not yet been merged to `main` when this task began. Its branch was exactly two commits ahead of and zero commits behind the latest `main`. Because this foundation must consume the real `CanonicalPatternSeries` contract, the Stage 1A branch was created from the validated Adapter HEAD rather than duplicating the contract or modifying `main`.

Tovest was used only as an immutable source oracle. The fixed Git object was inspected and exported to a temporary directory for source-side parity evidence. The Tovest checkout, source files, index, branch, and tracked history were not changed.

The committed Golden manifest freezes:

```text
tag    = tpg-v1.10
commit = 937edb62727f4d8c36d41b36e93521d077da20f9
tree   = 834b39f121d4a851c6c3710cc76ff6d67c2d8fb3
```

## C. New Module Boundary

```text
backend/services/technical_patterns/
├── __init__.py
└── core/
    ├── __init__.py
    ├── contracts.py
    ├── identity.py
    ├── input_mapper.py
    ├── pivot.py
    ├── boundary.py
    ├── range_structure.py
    ├── geometry.py
    └── lifecycle.py

tests/technical_patterns/
├── fixtures/
│   └── tovest_tpg_v1_10_foundation_golden.json
├── conftest.py
├── test_input_mapper.py
├── test_pivot_parity.py
├── test_boundary_range_parity.py
└── test_geometry_identity_lifecycle.py
```

Ownership boundaries:

| Layer | Owns | Explicitly does not own |
| --- | --- | --- |
| Stage 0 Pattern Data | IBKR ContractDetails, Historical Data, SCHEDULE, quality and cache | Pattern structures and detector parameters |
| Pattern Input Mapper | Closed-series validation, session ordinal, source identity mapping | IBKR runtime objects and fixed wall-clock inference |
| Pattern Core Foundation | Pivot, boundary/range, geometry, stable identity and technical lifecycle | Detector selection, calibration and Product output |
| Future Detector layer | Six pattern detectors and explicit US Stock/ETF calibration | Decision prose and trading authority |

There is no import from Tovest at runtime and no direct `IBKR Adapter → Detector` path.

## D. Migrated Components

### D.1 Pattern Input Mapper

`PatternInputMapper` converts `CanonicalPatternSeries` into a value-only `PatternCoreInput`:

- retains `instrument_id`, conId, ISIN, market, currency, timezone, adjustment policy, calendar version and source bar hash;
- uses `source_bar_hash` as the immutable dataset version;
- trims bars after `last_closed_session` and the optional as-of session;
- assigns a dense `session_ordinal` over actual exchange sessions;
- uses the source `session_date` as `available_from` rather than inventing a UTC close time;
- creates stable bar identities from instrument, session and OHLCV facts;
- fails closed on duplicates, invalid ordering and missing expected scheduled sessions.

Weekend and holiday dates are absent from the expected-session list and consume no ordinal. Friday-to-Monday is one session step.

### D.2 Pivot Engine

Migrated foundation behavior:

- candidate pivots from a left-window local extreme;
- right-closed-session confirmation;
- plateau grouping with earliest source identity;
- unconsumed candidate replacement;
- confirmed pivot supersession by a later same-type extreme;
- `available_from` and confirmation session ordinal;
- explicit confirmed, candidate and superseded outputs;
- stable identity, timeline, deterministic result hash and causal metrics.

The engine filters every input fact by `evaluation_session_ordinal`. A pivot cannot be confirmed by or exposed before its right confirmation session.

Pivot parameters and a parameter version are mandatory. There is no BTC default.

### D.3 Boundary / Trend / Range

Migrated foundation behavior:

- confirmed available swing lows become support;
- confirmed available swing highs become resistance;
- near boundaries merge and increment confirmed touch counts;
- a nearer, more-extreme boundary supersedes the prior boundary;
- a more-extreme boundary beyond the merge band invalidates prior same-role boundaries;
- HH/HL, LH/LL and mixed structures produce bullish, bearish or neutral trend context;
- the tightest legal active support/resistance bracket becomes the current range;
- range identity does not depend on Trend state;
- boundary/range availability, source lineage and future-fact metrics remain explicit.

Boundary tolerance and version are explicit constructor inputs. The Tovest BTC constant is represented only in the frozen parity fixture and is not a production default.

### D.4 Geometry

The migrated helpers operate on `SessionPoint(session_ordinal, price)`:

- session distance;
- least-squares line fit;
- per-session slope and intercept;
- maximum fit error;
- line price;
- two-line start/confirmation gap, contraction and apex session offset.

The new package contains none of:

```text
86400
timedelta(days=1)
TIMEFRAME_SECONDS
fixed UTC offset
```

Geometry therefore cannot accidentally count weekends, holidays, half days, or DST changes as extra Daily bars.

### D.5 Stable Identity

The WealthPilot-owned identity layer provides:

- deterministic recursive serialization for dataclasses, enums, dates, datetimes, Decimals, sequences and mappings;
- sorted-key compact canonical JSON;
- full SHA-256 hashes;
- stable prefixed identities;
- explicit `WP-PATTERN-CORE-IDENTITY-1.0` namespace.

Identity material is derived only from canonical source facts and versioned algorithm/parameter inputs. It contains no database ID or UI ID.

### D.6 Technical Lifecycle Core

The foundation lifecycle supports:

```text
candidate → confirmed → invalidated
candidate → confirmed → expired
candidate → invalidated / expired
```

It is evaluated on session ordinals. Invalidation wins when invalidation and expiry are observed on the same session, and terminal states cannot later change into another terminal state.

This lifecycle contains no publishing eligibility, content lifecycle, teacher-copy notice, or trading status.

## E. Crypto Adaptation

| Tovest assumption | Stage 1A treatment |
| --- | --- |
| 24×7 bar clock | Removed; dense exchange sessions are authoritative |
| Daily = fixed number of seconds | Removed |
| Continuous fixed-interval quality | Not migrated; Stage 0 Adapter owns SCHEDULE quality |
| BTC symbol identity | Replaced by canonical `instrument_id` |
| BTC volatility/tolerance defaults | No production default; parameter version required |
| Binance volume/provider facts | Not migrated |
| Product/publishing lifecycle | Not migrated |

TA-Lib is not invoked in Stage 1A because no feature-dependent detector has been migrated. The future Detector stage must consume the separate canonical indicator layer; it must not add indicator calculations inside Pivot, Boundary, Geometry, Identity, or Lifecycle.

## F. Golden Parity Result

Golden fixture:

```text
tests/technical_patterns/fixtures/tovest_tpg_v1_10_foundation_golden.json
```

The fixture records two evidence levels:

1. **Source oracle** — raw result hashes emitted by the exact frozen Tovest code.
2. **Mapped target oracle** — exact WealthPilot identities and hashes after the intentional time-axis and identity-namespace adaptation.

Raw source and mapped target hashes are not expected to equal each other because the migration deliberately replaces wall-clock timestamps, crypto identity and source serialization. Structural semantics must match exactly; mapped identities/hashes must also remain exact and repeatable.

| Gate | Result | Evidence |
| --- | --- | --- |
| Pivot parity | PASS | confirmed/superseded type, price, source and confirmation ordinal, timeline, metrics, mapped IDs/hash |
| Boundary parity | PASS | role, bounds, touches, source pivots, trend, mapped IDs/cache/result hash |
| Geometry parity | PASS | slope `-0.5`, intercept `110.0`, max error `0.0`; session-only distance |
| Identity parity | PASS | source-compatible simple fixture ID plus exact mapped identities and repeated hashes |
| Lifecycle parity | PASS | candidate → confirmed → expired; invalidation precedence and terminality |
| Range parity | PASS | support/resistance pair, levels, width, lineage, mapped ID/cache/result hash |
| No-future-fact | PASS | prefix replay equals independent truncation; future pivots ignored; all future-fact counters zero |

The Golden Gate compares only Core structures. It does not compare or import Tovest Product output.

## G. Tests and Quality Gates

### G.1 Targeted

```text
tests/technical_patterns
+ backend/services/pattern_data/tests
= 33 passed / 0 failed
```

Coverage includes:

- closed/as-of mapping;
- weekend and holiday session density;
- missing expected session fail-closed;
- unfinished Daily bar trimming;
- candidate/confirmed/superseded pivot behavior;
- prefix replay and no future fact;
- boundary merge/touches/supersession/invalidation;
- range creation;
- geometry parity and weekend distance;
- stable identity/repeated execution;
- confirmation/invalidation/expiry lifecycle.

### G.2 Repository gates

| Gate | Result |
| --- | --- |
| Full pytest | `572 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS, 0 errors/warnings |
| Frontend build | PASS; existing non-blocking large-chunk notice only |
| Offline M5 | `18/18 passed`, public network attempts `0` |

No live IBKR, market data, LLM, Search, broker, or personal database call was required or made by these tests.

## H. Remaining Tovest Dependencies

No runtime dependency on Tovest remains in the migrated foundation. The following source concepts are intentionally deferred:

| Deferred work | Next-stage treatment |
| --- | --- |
| Canonical TA-Lib indicator layer | Freeze formula, warm-up, NaN and dependency versions before feature-dependent detectors |
| Six detector implementations | Migrate one family at a time after foundation Gate |
| US Stock/ETF calibration registry | Require market + canonical economic asset class + timeframe + family key; fail when unconfigured |
| Detector-specific lifecycle conditions | Compose over this technical lifecycle without publishing fields |
| `PatternEvidenceBundle` mapper | Add after detector contracts are stable |
| Decision Skill/Tool and persistence | Later vertical slice only |

Still skipped permanently:

- Binance Provider and crypto fixed-interval quality;
- Workspace and Tovest database;
- scheduler/scanner;
- Telegram;
- Product signal serializer;
- publishing/teacher lifecycle;
- Trade Plan and order behavior.

## I. Safety

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Tovest modification = 0
Decision integration = 0
Detector migration = 0
```

## J. Final Acceptance

The accepted Stage 1A boundary is now executable and deterministic:

```text
CanonicalPatternSeries
        ↓
Pattern Core Foundation
```

Acceptance result:

```text
Pivot parity PASS
Boundary parity PASS
Geometry parity PASS
Identity parity PASS
Lifecycle parity PASS
No-future-fact PASS

READY_FOR_PATTERN_DETECTOR_MIGRATION
```
