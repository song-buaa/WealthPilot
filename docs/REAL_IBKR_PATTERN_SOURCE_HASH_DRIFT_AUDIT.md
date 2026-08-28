# Real IBKR Pattern Source Hash Drift Audit

## A. Executive Conclusion

```text
REAL_FROZEN_PARTITION_DATA_DRIFT_CONFIRMED
```

The 16/17 Stage 2E-1 mismatch was **not** caused by comparing a rolling
1950-bar fetch hash with a frozen Untouched-partition hash. Code inspection and
the preserved execution transcript prove that both Stage 1E and Stage 2E-1
explicitly sliced `2025-01-01..2026-08-21` before calling
`build_source_bar_hash`.

The 2026-08-28 read-only rerun used the exact prior
`as_of=2026-08-22T12:00:00Z`. All 17 series reproduced the original full
envelope (`1950` bars, `2018-11-15..2026-08-21`), the exact SCHEDULE lineage,
the same adjustment policy, and the same 410 closed sessions in the frozen
partition. Nevertheless, all 17 frozen-partition hashes differed from Stage
1E. The only remaining canonical hash input is OHLCV, so every instrument is
classified `FROZEN_PARTITION_BAR_VALUE_DRIFT`.

Per the fail-closed task rule, Untouched detectors did not run, no hash was
rewritten, and no runtime scope was promoted.

## B. Current Hash Semantics

`CanonicalPatternSeries.source_bar_hash` is the deterministic SHA-256 of every
canonical bar returned by one adapter read. It remains the public full-fetch
provenance field and its meaning was not changed.

Stage 1E's dataset builder already computes its manifest `source_bar_hash`
after slicing each partition:

```text
series.bars
  -> requested partition bounds
  -> ordered CanonicalPatternBar values
  -> build_source_bar_hash(partition bars)
```

The Stage 2E-1 validation script recovered from the execution transcript used
the same partition slice before comparing the manifest hash. Therefore the
recorded `actual_hash` values in the runtime validation manifest are partition
hashes, not full-fetch hashes.

For future validation audits, additive internal contracts now distinguish:

```text
source_fetch_hash
validation_partition_hash
session_set_hash
```

The validation-partition material binds instrument identity, frozen bounds,
ordered session dates, canonical OHLCV, adjustment policy, calendar policy,
timeframe, provider, `TRADES`, and `useRTH=true`. Bars outside the frozen
partition, wall-clock time, request IDs, and cache metadata are excluded. This
does not redefine the public `source_bar_hash` contract and is not wired as a
promotion bypass.

## C. 17-instrument Drift Matrix

All rows below are from the final fixed-`as_of` read. Hashes are shown by their
first 12 hexadecimal characters here; representative exact hashes are in
Section D and the full machine-readable audit remained in the local temporary
evidence file.

| Symbol | Asset | Original full | Current full | Original partition | Current partition | Bars / first / last | Classification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AAPL | EQUITY | `49a7043800fa` | `1b9281c144ad` | `888ec47a74ed` | `e2058aadbbaf` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| MSFT | EQUITY | `d75d56b9ff84` | `37b1a8eebb37` | `7965e6470c7b` | `b5ec70e914bc` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| NVDA | EQUITY | `40855a590bc2` | `32bc49b922d8` | `3b1da841958c` | `4c526ae54435` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| JPM | EQUITY | `0bf56b8efed7` | `6751de1339e8` | `0417074dbc78` | `020672b99a09` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| XOM | EQUITY | `c4e48f3c0941` | `87ecf2473f10` | `1a9281e265c3` | `7635f6862d20` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| JNJ | EQUITY | `1060fa645638` | `de7fb830a5cb` | `0e8ba93f83bc` | `a64765879df6` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| SPY | EQUITY | `0e13ff354bad` | `d2e773b64f3b` | `1c633b713a8d` | `847c17461315` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| QQQ | EQUITY | `1a2347eeb15b` | `278b3c204085` | `39c555120770` | `2087bf7ac63f` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| IWM | EQUITY | `8f86dc488587` | `ca862a690cd3` | `efe7ac7e9a5a` | `cb2e9aa64d4e` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| XLK | EQUITY | `5ded9e3b9aeb` | `62864fffb402` | `91ec33dbe36d` | `7fa962c3eb7b` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| XLF | EQUITY | `c4ef5cab871b` | `b26bfbbb4d90` | `0b2dc7aa840b` | `cd2844867fd7` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| XLE | EQUITY | `1e668bd4c820` | `faf9e874686a` | `47a5a50c3efc` | `ea5efc7cc2dc` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| AGG | FIXED_INCOME | `4457502a808a` | `ce9e76883a43` | `b3a7af17b45d` | `4264164d4a47` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| TLT | FIXED_INCOME | `abe731621efe` | `c3d49fa4b28f` | `088fbc12a380` | `a4cfc0a322cd` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| IEF | FIXED_INCOME | `176808822bf6` | `23ba03409c93` | `00e1651e273b` | `cd0cb2860a2b` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| SHY | FIXED_INCOME | `44be3a466525` | `659395d8e184` | `c5d1f73f08c2` | `7b2e31d0aab4` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |
| LQD | FIXED_INCOME | `5999abe7b24a` | `b4df5721adbe` | `02615e34b905` | `12aa6fcc9630` | 410 / 2025-01-02 / 2026-08-21 | `FROZEN_PARTITION_BAR_VALUE_DRIFT` |

## D. Frozen Partition Equality Evidence

The exact representative evidence requested by the task is:

| Symbol | Original full hash | Current full hash | Original frozen partition hash | Current frozen partition hash |
| --- | --- | --- | --- | --- |
| AAPL | `49a7043800fa45f6d5cb5e6de76674bdbaad9319dfea5e6f64cf63fb0e6c67a9` | `1b9281c144ad2378d5ad6a0af49f890c5e94e30bba41c205dfa3f2e005073f56` | `888ec47a74ed37c7a6450ed99d5fe3f25083373ea82b77c9d4cb6b1ceefd5893` | `e2058aadbbafe5af8e993ae3a1ca801f4f827b6e32ecc183724ce64301007b9b` |
| SPY | `0e13ff354badfc45bb63704dac74cce5d8bfed672d39724ea6a8e5551ed42bfb` | `d2e773b64f3bf3bad65993152d68d8f900cca28c70b2d26e2b236443fa300622` | `1c633b713a8da076a934034ee6d7db6c965a90df422619f3a3cacf6175ced614` | `847c17461315ee092734027036f7e3723bfc38c6a1906aa057604ba34e40f6c3` |
| QQQ | `1a2347eeb15b5c604612eeeeb4fa5be789f7118726137d4fe58616656da21158` | `278b3c204085d98c3c7d779fc06efa66c373bc9d1b1a5b57637494766cb3788d` | `39c555120770e8414a61a1a8a48930dd67c3455733cb37b6bafbc799800ca369` | `2087bf7ac63f1fa5616ed91b773c2e713d5599aceb91135140b59b5fce71b033` |
| IWM | `8f86dc4885872b8d8aede7ed3c95e669b94a29acb30adc07f6b8fdc5c7639f13` | `ca862a690cd34b579f281328713b7f38db6d89219aa7202d854f9e330e6ee468` | `efe7ac7e9a5a967c6458ee804252369e864799552dbbb8a1c9edab72d7a4b120` | `cb2e9aa64d4e6151fbd579ff03bcfff7a6aacef41a2b7100e018ce08272b01ac` |
| AGG | `4457502a808a040f5f0d9bb41e73d8096ad8d2174d2832a61cdc8242e5c381b0` | `ce9e76883a43f9cc9cc9e6f972b3aff8f24806ca97b9cf99719a0f2b868abbea` | `b3a7af17b45d190062611b3b4885488f8cd331d73e4e1f08013bc54fcabfe2ff` | `4264164d4a4756ac71b49ead44b41b85315e4a621ca73fe269a4b749587fabc9` |
| TLT | `abe731621efe2200ed64d510fa1707c04ae6df9ed3cce4a90dd2fa4bc6a3ac66` | `c3d49fa4b28f61c95d10b4b9441cd8fcaaf8f12ef9c3093a4904624f7858cd4e` | `088fbc12a38088a5368063955b446451d28d6b6c1679c4031d9fcb04f15269fd` | `a4cfc0a322cd30d8513d56f5e6efc6b7d8a25ebb9dc77e007321be528493d926` |
| IEF | `176808822bf6170c700fa2a29f01f1f9a22ea5bc00cdc8af39b354bf800fc7e2` | `23ba03409c93a2374866ccd03b5d728080c40cba16da6799e750e96adf8523f0` | `00e1651e273b134f244e33ea87f9d2b54a7afd21ef92fbbd2564be2c4959ead8` | `cd0cb2860a2b72d7ee257e0fc11b1479da05fdfd1917ec1e800bd0a6dbde89a4` |
| SHY | `44be3a466525ccf93eee44ae4b7b39f2bad91140cc5fb8607eb475c86e38f3aa` | `659395d8e1845b67539776e636781f18c0480be2e96cb78e9032dd8c17b06809` | `c5d1f73f08c257a35e437ffcc2e94150b3749338bc55607b61e0f2b609022551` | `7b2e31d0aab414757b012fe0bb76be95f1c0c53086c18562d35fdc56b2ecd50f` |
| LQD | `5999abe7b24ab0d8ab970d8a5ace9e35beb917ae9327d1d9f8b0be7eb091412a` | `b4df5721adbe651deb9e164c65b95a6a7a251d3bf67242e823486a826e63b0fd` | `02615e34b905e60412f837131f2fe953bc62dfec59ef5c802e5a98eebe242490` | `12aa6fcc9630f7f3a02e67972c7b5c1b6b984d36e2da455a7bb4f41025e06243` |

For every instrument:

```text
frozen requested range = identical
partition bar count = 410
first/last partition session = 2025-01-02 / 2026-08-21
full fixed-as_of envelope = 1950 bars, 2018-11-15 / 2026-08-21
adjustment policy = identical
calendar policy and exact full SCHEDULE digest = identical
adapter missing-session gate = PASS
canonical partition OHLCV hash = different
```

The original raw Stage 1E bar payload was temporary and was not retained in
Git, so this audit cannot name the individual revised dates/fields without
rewriting the frozen baseline. The deterministic bars-only hash inequality,
after exact session lineage was established, is sufficient to prove that one
or more canonical OHLCV values changed.

LQD is especially conclusive: it matched Stage 1E during the 2026-08-27
Stage 2E-1 read, but no longer matched on 2026-08-28 while retaining the exact
same partition and calendar lineage. Ten other symbols also changed again
between those two reads. The current partition hashes were stable across both
the rolling-`as_of` and fixed-`as_of` reads performed in this audit.

## E. Root Cause

```text
validation full-series/partition hash confusion = NOT PRESENT
frozen canonical partition content revision = CONFIRMED
exact upstream IBKR field/date revision = NOT RECOVERABLE FROM RETAINED V1 EVIDENCE
```

The practical root cause is mutable IBKR historical `TRADES` output inside a
partition that Stage 1E treated as immutable by retaining only a hash, not the
source bars. It may reflect upstream OHLCV revision or adjustment restatement;
this audit does not speculate which without the original bar payload.

## F. Repair

No promotion-path repair was applied because the prerequisite for repair was
not met. In particular, the implementation does **not** ignore a mismatch,
rewrite a frozen hash, redefine public `source_bar_hash`, or accept a new
baseline as the old partition.

The only implementation additions are audit infrastructure:

- an exact frozen-partition lineage contract and drift taxonomy;
- a reproducible read-only 17-instrument IBKR audit command;
- deterministic tests proving that bars outside a partition do not affect its
  hash while in-partition OHLCV/session/adjustment/calendar changes still block.

These additions are not connected to runtime promotion or Decision execution.

## G. Untouched Rerun Results

```text
eligible Holdout-PASS scopes = 9
Untouched detector executions = 0
Untouched PASS = 0
DATA_QUALITY_BLOCKED = 9
```

The rerun stopped at the mandatory pre-detector lineage gate. Development and
Holdout were not reopened. No parameter was changed or consumed by Untouched:

```text
parameter_hash_before_holdout = parameter_hash_after_holdout
parameter_hash_untouched = NOT_CONSUMED (detector did not run)
threshold adjustment attempts = 0
```

## H. Promotion Impact

The current promotion state remains unchanged:

| Verdict | Scope count |
| --- | ---: |
| `READY_FOR_RUNTIME_PROMOTION` | 0 |
| `DATA_QUALITY_BLOCKED` | 9 |
| `INSUFFICIENT_REAL_CASE_EVIDENCE` | 3 |

The three evidence-insufficient scopes remain exactly:

- Breakdown / FIXED_INCOME;
- Rectangle / FIXED_INCOME;
- Double Bottom / FIXED_INCOME.

The approved runtime registry remains empty and the runtime provider remains
unavailable. The existing promotion report, promotion matrix, and validation
manifest were not rewritten because no scope changed verdict.

## I. Safety / IBKR Read Counts

This task used only `IBKRHistoricalDataSource`, whose public surface contains
ContractDetails, historical data, and SCHEDULE and whose connection is
`readonly=True` with `StartupFetch(0)`.

Current-task accounting, including the rolling-window control, two fixed-as-of
runs, and one AAPL calendar-lineage diagnostic:

```text
ContractDetails requests = 52
Daily TRADES historical requests = 52
SCHEDULE requests = 312
Account requests = 0
Portfolio requests = 0
Order requests = 0
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
```

No account identifier or account data was requested or recorded.

## J. Remaining Blockers

1. All nine Holdout-PASS scopes remain blocked by genuine frozen-partition
   canonical value drift.
2. The v1 evidence set did not retain original canonical bars/session arrays,
   preventing field-level attribution of the upstream revision.
3. A future governed dataset version must retain immutable canonical partition
   payloads (or equivalent content-addressed artifacts) if field-level drift
   forensics is required. It must not silently replace the current freeze.
4. The three Fixed Income evidence gaps remain independent and out of scope.

```text
PATTERN_RUNTIME_PROMOTION_BLOCKED
```

## K. Quality Gates

```text
Pattern Data + Technical Pattern targeted = 321 passed
partition-lineage focused rerun = 7 passed
full pytest = 860 passed / 7 skipped / 0 failed
compileall = PASS
frontend lint = PASS
frontend build = PASS
Pattern Evidence UI regression = 6 passed
Offline M5 = 18/18, public_network_attempts=0
git diff --check = PASS
standard automated-test network access = 0
```

The frontend build retained the existing non-blocking Vite bundle-size warning;
there was no build failure and bundle splitting is outside this task.
