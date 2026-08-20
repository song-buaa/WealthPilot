# IBKR Pattern Data Adapter Implementation Report

> Stage 0 implementation boundary: IBKR historical data to `CanonicalPatternSeries`
> Branch: `codex/ibkr-pattern-data-adapter`
> Date: 2026-08-20

## A. Implementation Summary

The Stage 0 adapter is implemented as an isolated `backend.services.pattern_data`
boundary. It does not call the Pattern detector or lifecycle, and it has no
Decision, Execution Plan, Portfolio, account, position, or order integration.

The production source uses `ib_async` on its own background thread and asyncio
event loop. It connects with `readonly=true` and `StartupFetch(0)`, so connection
startup does not request account, position, execution, or order state. Its public
surface only exposes:

- one-candidate `ContractDetails` resolution;
- `SCHEDULE` retrieval;
- fixed daily `TRADES` historical retrieval;
- shutdown.

All IB runtime objects are consumed inside `ibkr-pattern-data-loop`. Only frozen,
ordinary Python value objects cross back to the caller.

## B. CanonicalPatternSeries Schema

Successful adaptation returns `PatternDataResult(status=READY)` with a frozen
`CanonicalPatternSeries` containing:

| Field | Contract |
| --- | --- |
| `instrument_id` | Stable internal identity, currently `IBKR:<conId>` |
| `conId` | Resolved IBKR contract ID (`con_id` internally, `conId` on serialization) |
| `ISIN` | ISIN from `ContractDetails.secIdList` |
| `symbol` | IBKR canonical symbol; aliases remain identity inputs, not rewrites |
| `market` | Direct listing exchange, or primary exchange for SMART US listings |
| `currency` | Contract currency |
| `timezone` | Authoritative SCHEDULE timezone (`US/Eastern`, `MET`, etc.) |
| `adjustment_policy` | `IBKR_TRADES_SPLIT_ADJUSTED_DIVIDENDS_UNADJUSTED` |
| `calendar_version` | `IBKR_SCHEDULE_V1:<schedule hash>` |
| `last_closed_session` | Latest session whose SCHEDULE close is not later than `as_of` |
| `source_bar_hash` | SHA-256 over canonical, ordered OHLCV bars |
| `bars` | Ordered immutable daily OHLCV value objects |

Canonical numeric values use `Decimal` and deterministic string serialization.
The source hash therefore does not depend on object identity, locale, dictionary
ordering, or display formatting.

## C. Closed-Bar and Quality-Gate Behavior

The adapter requests `ContractDetails`, a bounded SCHEDULE window, and historical
data with the fixed contract:

```text
barSizeSetting = 1 day
whatToShow = TRADES
useRTH = true
keepUpToDate = false
```

SCHEDULE session end timestamps are interpreted with the timezone returned by
IBKR. A timezone-aware `as_of` is mandatory; a naive datetime is rejected rather
than assigned a fixed UTC offset. Any raw daily bar later than
`last_closed_session` is trimmed before canonicalization.

The adapter fails closed with `DATA_QUALITY_BLOCKED` when:

- ContractDetails has no ISIN;
- SCHEDULE does not cover the first returned bar;
- an expected closed session has no corresponding TRADES bar;
- bars are duplicated, unordered, non-finite, non-positive, negative-volume, or
  violate OHLC invariants.

No missing session is filled forward. No fake OHLC or volume is produced.
Provider connection/query failures return `DATA_UNAVAILABLE`; exhausting the
bounded duration sequence below target returns `INSUFFICIENT_HISTORY`.

## D. History Expansion

The default capacity target is 1,460 closed bars. The bounded request sequence is:

```text
2 Y -> 4 Y -> 6 Y -> 7 Y -> stop
```

Each response is independently trimmed and quality-checked. Retrieval stops as
soon as 1,460 valid closed bars exist. The adapter never loops indefinitely and
reports every attempted duration in `requested_durations`.

The SCHEDULE request spans 2,200 calendar days, sufficient to validate the target
bar window under the markets covered by the Stage 0 probe. This remains a
configurable bounded value, not an unbounded pagination loop.

## E. Cache Behavior

`DailyPatternDataCache` is a process-local read-through cache with:

- immediate cache hits for the same instrument/request/day;
- explicit `refresh=true` bypass;
- thread-safe single-flight request deduplication;
- 15-minute positive/default result TTL;
- 30-second `DATA_UNAVAILABLE` negative TTL.

The short negative TTL prevents transient IBKR connection, pacing, or provider
errors from becoming long-lived `DATA_UNAVAILABLE` results. Quality-blocked and
insufficient-history results are not converted to empty series. A process restart
clears the cache.

## F. Tests

Deterministic fixtures cover the identities observed in the Stage 0 probe:

- AAPL (`conId=265598`, US/Eastern);
- SPY (`conId=756733`, US/Eastern);
- CBU3 / CSBGU3 (`conId=79000224`, MET);
- IB01 (`conId=354802220`, MET).

Sixteen adapter tests cover:

- all four instrument contracts;
- schedule-derived closed sessions;
- removal of an unfinished US daily bar;
- LSE/US timezone and DST-capable `ZoneInfo` handling;
- the fixed split-adjusted IBKR `TRADES` request contract;
- missing expected session -> `DATA_QUALITY_BLOCKED`;
- bounded expansion to 1,460 bars;
- `INSUFFICIENT_HISTORY` after the maximum duration;
- deterministic bar and calendar hashes;
- cache hit and explicit refresh;
- concurrent request deduplication;
- negative-cache expiry;
- rejection of naive timestamps;
- production source `readonly=true`, `StartupFetch(0)`, dedicated-loop execution,
  and detached value snapshots.

Validation results:

```text
Pattern adapter targeted: 16 passed
Full pytest (including the 16 adapter tests): 555 passed, 7 skipped
Python compileall: passed
Frontend lint: passed (0 errors, 0 warnings)
Frontend build: passed
```

The adapter test directory is part of `pytest.ini`, so subsequent full pytest and
CI runs collect the 16 tests automatically.

## G. Limitations

- IBKR `TRADES` is treated according to the probe result: split adjusted, not
  dividend adjusted. The adapter makes this explicit and does not attempt a local
  second adjustment.
- SCHEDULE may legitimately contain a session for which a thinly traded ETF has
  no TRADES bar (the probe observed this for CBU3). This is intentionally blocking;
  resolving that policy belongs to a later product decision, not this adapter.
- The cache is local-memory only. Durable multi-process cache storage, distributed
  locking, and provider telemetry are outside Stage 0.
- The Stage 0 implementation is not wired into Pattern Core. That integration is
  intentionally deferred.
- This implementation task used deterministic fixtures and a mocked ib_async
  runtime. It did not reconnect to the live Gateway because the preceding contract
  probe already established live read-only evidence and this task changes no
  production integration.

## H. Safety Evidence

```text
Broker mutation = 0
Order mutation = 0
ExecutionPlan change = 0
Portfolio change = 0
Production DB change = 0
Live Gateway calls during implementation = 0
```

The source has no `place`, `submit`, `cancel`, `modify`, or `replace` method.

## I. Final Verdict

```text
READY_FOR_PATTERN_CORE_MIGRATION
```
