# US Pattern Calibration & Validation Framework Report

> Stage: 1D
>
> Date: 2026-08-21
>
> Branch: `codex/us-pattern-calibration-validation`
>
> Stage 1C-4 base: `92f26661f161e2c5ccee3da0bb7c2f1121940236`

## A. Executive Conclusion

Stage 1D establishes a deterministic, fail-closed framework for calibrating and validating the six launch Patterns on US Stock/ETF data. It does not calibrate the detectors, claim that any Pattern is production-ready, optimize a trading outcome, or integrate Pattern results with Decision or execution systems.

The enforced sequence is:

```text
Frozen Dataset Manifest
        ↓
Development Definition Review
        ↓
Parameter Attempt History
        ↓
Immutable Parameter + Manifest Freeze
        ↓
Chronological Holdout Review
        ↓
One-time Untouched Validation Review
        ↓
Governance Promotion Assessment
```

Code and golden parity are prerequisites, not production evidence. Even a complete passing validation produces only `READY_FOR_GOVERNANCE_REVIEW`; it never promotes a detector automatically.

## B. Calibration Architecture

The framework extends the existing exact calibration registry without changing detector runtime behavior:

```text
CanonicalPatternSeries / frozen source hashes
        ↓
CalibrationDatasetManifest
        ↓
CalibrationAttemptRecord (development only)
        ↓
FrozenCalibrationVersion
        ├── DetectorParameterSet hash
        └── Dataset manifest hash
        ↓
PatternSampleReview
        ↓
PatternValidationEvaluation
        ↓
PatternValidationReport + PromotionAssessment
```

The primary implementation lives in:

- `backend/services/technical_patterns/calibration/datasets.py`
- `backend/services/technical_patterns/calibration/validation.py`
- `backend/services/technical_patterns/calibration/registry.py`

The framework is provider-neutral. The manifest records the provider, bar hash, adjustment policy, and calendar authority but does not fetch live or historical data itself.

## C. Dataset Manifest Design

`CalibrationDataset` represents one immutable instrument/date-range evidence item. Its contract contains every required field:

| Field | Contract purpose |
| --- | --- |
| `instrument` | Reviewed symbol/instrument identity |
| `market` | Exact normalized market binding |
| `economic_asset_class` | Exact canonical economic class |
| `timeframe` | Exact normalized timeframe |
| `date_range` | Inclusive chronological evidence window |
| `source_provider` | Data authority used to build the series |
| `source_bar_hash` | Immutable source-series lineage |
| `adjustment_policy` | Corporate-action semantics |
| `calendar_version` | Exchange-session authority |
| `label` | `positive`, `negative`, `ambiguous`, or `review_disagreement` |
| `partition` | development, holdout, or untouched validation |
| `review_status` | sealed, in review, or completed |

It also records asset coverage, market regimes, and edge cases. Stable dataset IDs and SHA-256 hashes derive from canonical serialized content, not database or UI IDs.

`CalibrationDatasetManifest` binds exactly one:

```text
US market
+ economic asset class
+ Daily timeframe
+ pattern family
+ pattern type
+ manifest version
```

It requires all three partitions, disjoint dataset identities and source hashes, and strict global chronology:

```text
end(development) < start(holdout)
end(holdout) < start(untouched_validation)
```

Development labels must be completed before freeze. Holdout and untouched labels must remain `SEALED` and null in the frozen manifest; the label field exists but cannot leak answers into parameter development.

## D. Sample Coverage Contract

Coverage is definition-validation evidence, not a performance portfolio. The framework checks the following before a version can reach governance review:

| Dimension | Required coverage |
| --- | --- |
| Equity assets | common US stock, broad-market ETF, sector ETF |
| Fixed Income assets | fixed-income ETF |
| Regimes | bull, bear, sideways, high volatility, low volatility |
| Gaps/actions | earnings gap, overnight gap, split, dividend |
| Sessions/liquidity | holiday, half day, low liquidity |

Because calibration keys contain `economic_asset_class`, Equity and Fixed Income use separate manifests and frozen versions. Coverage is aggregated across the three chronological partitions of one exact calibration scope; it is never satisfied by silently mixing economic classes.

No real dataset has been selected or reviewed in Stage 1D. The instruments and ranges used by tests are deterministic contract fixtures only.

## E. Validation Process

### Development

- Human labels and definition reviews are visible.
- Parameter refinement is allowed.
- Every attempt records a sequential attempt number, exact parameter hash, development partition hash, review IDs, reason, and date.
- Any attempt referencing holdout or untouched evidence is rejected.

### Parameter freeze

- The final attempt hash must match the exact `DetectorParameterSet` hash.
- The six-dimensional calibration key must exactly match the manifest.
- The frozen record includes immutable parameter-set ID/hash, manifest ID/hash, attempt count, and freeze date.
- A changed record under the same calibration key is rejected rather than overwritten.

### Chronological holdout

- Reviews cannot predate the freeze.
- Every and only the frozen holdout sample must be reviewed.
- Parameter, manifest, and partition hashes must match the frozen version.
- Definition conformance, false-positive review, false-negative review, boundary review, and human review are explicit gates.
- Any unresolved `review_disagreement` fails the evaluation.

### Untouched validation

- The partition remains unopened until the frozen holdout passes.
- Its review must occur after holdout review.
- It uses the same exact frozen parameters and manifest.
- It is a one-time final definition validation, not another tuning set.

## F. Pattern Review Framework

Review asks whether the detected structure matches its technical definition. It deliberately excludes profit, return, win rate, rank, probability, and trade outcome.

Each review records:

- one of `positive`, `negative`, `ambiguous`, or `review_disagreement`;
- whether the Pattern definition conforms;
- false-positive and false-negative observations;
- boundary ambiguity;
- reviewer identity, notes, and review date.

`ambiguous` requires an explicit boundary-ambiguity fact. `review_disagreement` requires at least two reviewers plus written notes and cannot be counted as a passing result.

## G. Calibration Registry and Anti-overfitting Gates

The existing exact registry key remains:

```text
market
+ economic_asset_class
+ timeframe
+ pattern_family
+ pattern_type
+ calibration_version
```

Stage 1D adds immutable version evidence around it:

- parameter hash;
- dataset manifest hash;
- development attempt count and review lineage;
- holdout and untouched partition hashes;
- stable evaluation and report IDs.

Anti-overfitting is enforced by contract:

1. tuning inputs must be development-only;
2. holdout and untouched labels are sealed at freeze;
3. holdout cannot be relabeled as holdout after parameter adjustment;
4. source hashes from an opened holdout/untouched set are recorded as exposed and cannot be reused as unseen evidence in a later version;
5. untouched validation cannot open before a passing holdout;
6. evaluations are immutable once recorded;
7. all result comparison remains definition-focused rather than financial-outcome-focused.

The current ledger is deterministic and in-memory. Calibration execution must preserve the same immutable manifest, attempt, review, evaluation, and report artifacts in version-controlled evidence or an equivalently append-only store; re-creating an empty process must not be used to erase exposure history.

## H. Six Pattern Validation Plan

Each Pattern has an independent manifest, calibration version, human review set, failure-mode list, and promotion recommendation:

| Pattern | Family | Positive review focus | Negative / ambiguous focus | Common failure modes to record |
| --- | --- | --- | --- | --- |
| breakout | level break | confirmed resistance break with causal evidence | wick/noise and insufficient confirmation | stale boundary, gap-only artifact, missing volume context |
| breakdown | level break | confirmed support break with causal evidence | transient breach and recovery | stale boundary, gap-only artifact, missing volume context |
| rectangle | range | stable upper/lower boundaries and adequate touches | trend, unstable or overly broad/narrow range | boundary drift, insufficient touches, unclear duration |
| ascending triangle | triangle | flat resistance plus rising support | flat/declining support or unstable ceiling | slope ambiguity, converging too early/late, weak touches |
| double top | reversal | two comparable highs plus valid neckline | single peak, trend continuation, unclear neckline | extreme mismatch, shallow reaction, future neckline leak |
| double bottom | reversal | two comparable lows plus valid neckline | single trough, trend continuation, unclear neckline | extreme mismatch, shallow reaction, volume/neckline ambiguity |

The registry already exposes exact development-only hypotheses for all six Patterns across `US/EQUITY/1d` and `US/FIXED_INCOME/1d`: twelve parameter sets in total. Stage 1D preserves their `development_only` status. None is promoted by this task.

Every later Pattern Validation Report must contain:

- dataset manifest hash;
- frozen calibration version;
- positive, negative, ambiguous, and disagreement counts;
- observed failure modes;
- promotion recommendation.

## I. Production Promotion Gate

The framework evaluates the required conjunction:

```text
Detector PASS
+ Calibration frozen
+ Holdout PASS
+ Untouched Validation PASS
+ Human Review PASS
+ Required sample coverage PASS
```

Missing any item yields `INSUFFICIENT_EVIDENCE` with explicit blocking reasons. Passing all items yields only:

```text
READY_FOR_GOVERNANCE_REVIEW
```

Production promotion remains an explicit later governance decision. There is no automatic detector registration, Decision integration, UI exposure, scanning, ranking, or execution effect.

## J. Automated Validation

Targeted tests cover:

- all required manifest authority, label, partition, and review fields;
- deterministic IDs and hashes;
- sealed holdout/untouched labels;
- strict chronological separation and disjoint source evidence;
- sample coverage gap reporting;
- development-only parameter attempt history;
- immutable parameter/manifest freeze and exact key binding;
- hash drift and incomplete review rejection;
- holdout-before-untouched ordering;
- failed holdout and review-disagreement blocking;
- exposed evidence reuse prevention;
- full definition-validation assessment without automatic production promotion;
- code/golden parity alone remaining insufficient;
- independent exact development calibration for all six Patterns and both economic classes;
- absence of financial-outcome, Decision, Portfolio, Broker, Order, or direct provider coupling.

Repository validation:

| Gate | Result |
| --- | --- |
| Calibration framework + registry + level-break targeted | `36 passed, 0 failed` |
| Technical Pattern + Pattern Data targeted | `154 passed, 0 failed` |
| Full pytest | `693 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS (`0 errors, 0 warnings`) |
| Frontend build | PASS (existing non-blocking large-chunk advisory only) |
| Offline M5 | `18/18`, `public_network_attempts=0` |

## K. Known Limitations and Next Step

Stage 1D intentionally does not:

- select or download real IBKR calibration datasets;
- review or label real Pattern samples;
- change detector parameters;
- decide numerical pass thresholds from observed samples;
- claim holdout or untouched validation success;
- promote any existing development calibration;
- persist exposure history outside the deterministic contract layer;
- integrate Pattern evidence with Decision, UI, Portfolio, ExecutionPlan, Broker, Order, Scanner, or Scheduler.

The next authorized stage may execute calibration by creating reviewed, source-hashed manifests for each exact Pattern/economic-class scope, recording development attempts, freezing parameters, and then opening holdout and untouched validation in the enforced order.

## L. Safety and Final Verdict

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Tovest modification = 0
Decision integration = 0
```

Framework acceptance:

```text
Calibration Registry PASS
Dataset Manifest PASS
Partition Separation PASS
Validation Workflow PASS
Anti-overfitting Gate PASS
```

Final verdict:

```text
READY_FOR_PATTERN_CALIBRATION_EXECUTION
```
