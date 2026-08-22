# Real IBKR Six-Pattern Calibration & Human Chart Review Report

> Stage 1E · Real data evidence gate · 2026-08-22

## A. Executive Conclusion

The frozen 17-instrument Universe resolved successfully and produced source-hashed IBKR Daily TRADES series through the latest fully closed session. Only the Development partition was run through the six detectors. Static evidence and a machine-readable manifest were generated with every human-review field left null.

No independent human review existed at generation time. Parameter promotion, Holdout detection, Untouched Validation detection, and Production Promotion therefore did not run.

```text
READY_FOR_HUMAN_CHART_REVIEW
```

## B. Real IBKR Universe

- Instruments: 17 (6 common stocks, 6 equity ETFs, 5 fixed-income ETFs).
- All symbols were fixed before ContractDetails resolution and before any detector output.
- Identity resolution: 17/17 unique; no replacement was required.
- Universe manifest hash: `21d29a28c7cffaedf8ea32c8e203dc00381ababdd6ccf65b5cd640652761be07`.

## C. Dataset / Source Hashes

- Dataset entries: 51; READY: 51.
- Dataset manifest hash: `a44a3fe2a77d6b36b41fcae29ab4f664ddc3c077331d2c9829bf20ef2494e4f2`.
- Contract: `IBKR / TRADES / 1 day / useRTH=true`.
- Adjustment: `IBKR_TRADES_SPLIT_ADJUSTED_DIVIDENDS_UNADJUSTED`.
- No forward fill, fake OHLC, fake volume, or unfinished Daily bar.
- Holdout and Untouched bars were hashed for lineage but not opened to Detector execution.
- Dataset acquisition used 17 serial Adapter loads. Each load made one bounded `8 Y / 1 day / TRADES / useRTH=true` historical request after ContractDetails and paged SCHEDULE resolution.
- Dataset acquisition latency: 76.272 seconds total, 4.487 seconds mean, 4.156–5.059 seconds per symbol.
- Authorized `reqHistoricalDataAsync` calls during the complete task: 21 (3 AAPL duration-expansion diagnostics, 1 AAPL 1,950-bar capacity proof, 17 frozen-Universe dataset loads).
- Account, position, execution, open-order, and market-data subscription calls for this Stage 1E workflow: 0.

## D. Pattern Case Inventory

| Pattern | Asset class | Detected | Review detected | Negative controls | Evidence status |
| --- | --- | ---: | ---: | ---: | --- |
| breakout | EQUITY | 304 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| breakout | FIXED_INCOME | 84 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| breakdown | EQUITY | 171 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| breakdown | FIXED_INCOME | 194 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| rectangle | EQUITY | 19 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| rectangle | FIXED_INCOME | 13 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| ascending_triangle | EQUITY | 112 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| ascending_triangle | FIXED_INCOME | 79 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| double_top | EQUITY | 267 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| double_top | FIXED_INCOME | 63 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| double_bottom | EQUITY | 223 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |
| double_bottom | FIXED_INCOME | 53 | 5 | 5 | READY_FOR_HUMAN_CHART_REVIEW |

Development detector candidates across all scopes: 1582.

Negative controls are fixed quarter-anchor windows with no target Pattern available in the prior 80 sessions. They are intentionally unlabeled and allow a human reviewer to identify false negatives or ambiguity. The current Detector Framework does not expose definition-rejected proposals, so these controls must not be described as detector-rejected near misses.

## E. Human Review Pack

- Selected review cases: 120.
- Evidence format: static SVG with OHLC path, volume, detector geometry, availability/confirmation/invalidation markers where present.
- `human_review_label`, notes, reviewer, and timestamp are all null.
- Human manifest hash: `401d26a9bb9ab0a84ed9621c57153eb53b43bd070080d448051c4f550ed946f3`.

## F. Calibration Attempts

Not run. Pilot parameters remain Development starting hypotheses.

## G. Frozen Versions

No production calibration version was frozen.

## H. Holdout

Not opened to Detector execution. Waiting for a completed and frozen independent Human Review Manifest.

## I. Untouched Validation

Not opened to Detector execution.

## J. Per-Pattern Promotion Matrix

All scopes are `NOT_EVALUATED_HUMAN_REVIEW_PENDING`. This is not a Production Promotion verdict.

## K. Known Limitations

- Static charts mark source-pivot availability; the current result contract does not expose each pivot's original source-bar coordinate.
- Definition-rejected proposals are not surfaced by several detector discovery paths; deterministic negative controls are used instead and require human interpretation.
- All 12 Pattern/asset scopes met the five-detected-case Review Pack target; this is evidence coverage, not a production-quality verdict.
- The real Gateway showed that oversized SCHEDULE requests time out; the Adapter now uses bounded 365-session backward pages.

## L. Safety and Next Step

```text
IBKR historical read = authorized
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Decision integration = 0
Public network outside authorized IBKR = 0
```

A human reviewer must now inspect the Review Index and fill the Human Review Manifest. Only after that manifest is frozen may Development calibration continue, followed by a new parameter freeze, Holdout, and finally Untouched Validation.

## M. Quality Gates

```text
Stage 1E + Pattern Data targeted: 21 passed
Technical Pattern + Pattern Data: 224 passed
Full pytest: 763 passed / 7 skipped / 0 failed
Python compileall: PASS
Frontend lint: PASS (0 errors / 0 warnings)
Frontend build: PASS (existing >500 kB advisory only)
Offline M5: 18/18
Offline M5 provider: offline_fixture
Offline M5 public_network_attempts: 0
SVG XML validation: 120/120 PASS
Human review fields null: 120/120 PASS
```

```text
READY_FOR_HUMAN_CHART_REVIEW
```
