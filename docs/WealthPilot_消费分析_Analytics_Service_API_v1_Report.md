# WealthPilot 消费分析 Analytics Service + Read-only API v1 报告

## Baseline

- Analytics Design 已由 `codex/consumption-analytics-design` 以 ff-only 收口；`main` / `origin/main` 为 `4d6a337cbba5166ec74958334d8aba76f9099aca`。
- 实现分支：`codex/consumption-analytics`；Start HEAD：`4d6a337cbba5166ec74958334d8aba76f9099aca`。

## Architecture and source of truth

```
SQLite / SQLAlchemy
  → ConsumptionAnalyticsQueryAdapter
  → ConsumptionAnalyticsService + immutable DTOs
  → GET /api/consumption/analytics
```

Adapter materializes only active `EconomicEventProjectionRevision` and active
`ConsumptionInterpretation` rows.  It joins active `EventRawLink` and Raw only
to establish event account scope; it never reads or aggregates Raw amounts.
The service is deterministic and read-only: it performs no classification replay,
write, LLM, MCP, or network call.

## Query adapter, scope, and coverage

`ConsumptionAnalyticsQueryAdapter` limits Events by account and
`analytics_effective_date`, deduplicates multi-link events, and excludes inactive
events/revisions/interpretations.  Source coverage comes from completed
`ImportBatch` metadata only: explicit statement ranges, observed-only ranges, and
the selected expected-account scope.

The minimal single-user expected-account policy is Option A/C: an explicit
`account_ids` request is the expected scope; absent that request all active
Consumption accounts are expected.  A missing expected account yields `UNKNOWN`,
so the API never calls a partial source set “all consumption.”  `OBSERVED_ONLY`
(including CMB credit statements) produces `SOURCE_LIMITED`, never `COMPLETE`.

## Monthly summary, trend, and categories

The response contains 1–24 ordered calendar-month points (default 12, including
the current month).  Every point exposes known `total_spending_cny`, `DAILY`,
`TRAVEL`, `HOUSING`, `unclassified_eligible_cny`, amount-based classification
coverage, eligibility/classification review counts, unresolved-amount state, and
coverage/comparison state.

Only `ELIGIBLE` events with an active known CNY projection enter amount totals.
`ELIGIBLE + NEEDS_REVIEW` classification enters both total and unclassified amount;
eligibility review and ineligible events stay outside the denominator.  Thus
`DAILY + TRAVEL + HOUSING + unclassified eligible = known total`.

Classification coverage is `classified eligible CNY / known eligible CNY` and is
`null` when no known eligible amount exists.  Secondary breakdowns aggregate only
classified eligible Events by primary/secondary category in deterministic
amount-descending, category-code order, with share of known total and share within
primary.  No merchant ranking or recurrence inference is included.

## Refund, FX, comparisons, and averages

Refunds are represented only through the active projection of the original
purchase; a refund Event never becomes a negative refund-month entry.  Native CNY
and bank-settlement CNY aggregate normally.  An eligible `FX_REQUIRED` Event with
no active base net amount is visible through `amount_complete=false`, unresolved
count, and deterministic `amount_unresolved_by_currency`, never silently as zero.

Current month is partial before month end, carries `as_of_date`, and has no direct
comparison.  Comparison is available only where both neighboring months are
complete.  `three_month_average` and `twelve_month_average` each expose
`amount_cny` plus `months_used`; only complete, amount-resolved months participate.

## API contract

`GET /api/consumption/analytics`

- `as_of`: optional ISO date; defaults to today.
- `months`: default `12`, bounded `1..24`.
- `account_ids`: optional repeated query parameter, defining a bounded account and
  completeness scope.

The endpoint serializes immutable DTOs and `Decimal` values as JSON-safe strings.
It has no write route and returns neither Raw rows, descriptions, statements,
unmasked account identifiers, nor ORM objects.

## Validation and performance

- Analytics Design Golden Cases A–T execute through the production ORM adapter and
  service: **PASS (20/20)**.
- Production cases U–AD: active Event/Interpretation filtering, partial/current
  comparison, complete comparison, incomplete-average exclusion, missing account,
  source-limited coverage, unresolved FX, calendar ordering, and bounded account
  filtering: **PASS**.
- Consumption suite: **156 passed**; Analytics service suite: **29 passed**.
- Three supplied statement types ran locally through Adapter → Raw persist → Event
  normalize → Classification resolve → Analytics Service in an in-memory database:
  **PASS**.  The monthly accounting invariant and current/source coverage flags
  held.  No real amount, source row, or identifier was printed.  End-to-end local
  run was about 12.5 s (parsing included); the 12-month read-only summary was about
  668 ms on that local corpus.  No caching or analytics persistence was added.

## Privacy and isolation

- real source committed: **NO**
- raw logged: **NO**
- API raw leak: **NO**
- LLM/MCP/network: **NO**
- analytics table/materialized summary: **NO**
- Event / Classification schema modified: **NO**
- Pattern modified: **NO**
- IA/frontend modified: **NO**

## Open items and readiness

- Consumption UI v1 must render coverage and amount-completeness states rather
  than presenting a partial source set as a complete household total.
- Review-detail paging and user review mutations remain a later, separate API/UI
  scope; this endpoint intentionally returns only bounded aggregate counts.
- Historical FX resolution remains deferred; unresolved FX remains explicit.

Next readiness: **READY_FOR_CONSUMPTION_UI**.
