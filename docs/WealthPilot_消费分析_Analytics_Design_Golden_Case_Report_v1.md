# WealthPilot 消费分析 Analytics Design & Golden Case Report v1

## Baseline

- Classification implementation closeout: `codex/consumption-classification` ff-only merged and pushed.
- `main` / `origin/main` after closeout: `32b3c4b9b9cddac674ff101bb407dbb08b192461`.
- Design branch: `codex/consumption-analytics-design`.
- Start HEAD: `32b3c4b9b9cddac674ff101bb407dbb08b192461`.

## Analytics source of truth

The production-facing future service must read only the **active EconomicEvent projection** and the **active ConsumptionInterpretation revision**. It must not sum Raw transactions. This design’s pure evaluator accepts immutable in-memory DTOs that represent those active rows; it has no ORM, API, UI, persistence, network, or LLM dependency.

## Monthly total and category structure

`monthly_total_spending_cny` is the known CNY net amount of every `ELIGIBLE` Event, grouped strictly by `analytics_effective_date`. Refund Events are not an independent negative category: the active projection of the original purchase already holds its refund-adjusted net amount. Eligibility-review and ineligible Events are outside every spending amount.

`DAILY`, `TRAVEL`, and `HOUSING` contain only `ELIGIBLE + CLASSIFIED` Events. `ELIGIBLE + classification NEEDS_REVIEW` contributes to total spending as `unclassified_eligible_cny`. The latter is an analysis status, never a fourth primary category.

The default category share is `category_amount / total_eligible_amount`. Consequently Daily + Travel + Housing + unclassified eligible equals the known total. A classified-only share may be rendered as a secondary metric, never as the default headline.

## Coverage metrics

| Metric | Meaning |
|---|---|
| Source coverage | `EXPLICIT`, `OBSERVED_ONLY`, or `UNKNOWN` per account/month. Missing expected accounts yield `UNKNOWN`; observed-only yields `SOURCE_LIMITED`, never `COMPLETE`. |
| Eligibility review | Event count only; review events are not a claim of missing consumption money. |
| Classification coverage | `classified_eligible_amount / known_total_eligible_amount`; `N/A` when the denominator is zero. |
| Amount completeness | `amount_unresolved_count` and original-currency amount preserve `FX_REQUIRED` uncertainty. The displayed total is a known partial total, never a silent zero. |

Month status is `COMPLETE`, `PARTIAL`, `SOURCE_LIMITED`, or `UNKNOWN`. The default trend contains the last 12 calendar months including the current month. Current month has `is_partial_month=true`, an `as_of_date`, and no default comparison to a complete prior month. Comparisons require both months to be complete; complete-month averages use only complete, amount-resolved months and expose the actual month count.

## Contracts

- `ActiveEventProjection`: event type, analytics-effective date, account scope, original amount, active CNY net amount, and FX source.
- `ActiveInterpretation`: active eligibility, classification status, and optional taxonomy.
- `SourceCoverageInput`: account/month source coverage and observed-through boundary.
- `MonthlySpendingPoint`: total, three categories, unclassified, coverage, reviews, unresolved amount state, source/current-month state, and comparison availability.
- `SpendingSummary`: ordered 12-month points, deterministic secondary breakdowns, and complete-month average.
- `SecondaryBreakdown`: amount, event count, share of total, and share within primary category.

Account scope is explicit through `expected_account_ids`; an eventual service may pass all connected accounts or a selected bounded subset. Event-layer exclusions make credit-card repayment non-spending even if a stale interpretation exists.

## Secondary, recurring, and contributor decisions

Secondary categories aggregate only classified eligible events. They expose amount, count, share of total and share within primary across the requested window.

Recurring / one-off is **P1**: the P0 contract does not auto-infer recurrence. Rent can later be marked recurring by an explicit rule; travel can later be presented as trip-driven/one-off. This avoids pretending a single statement-derived occurrence is a recurrence fact.

Top secondary category is P0 contract output. Merchant/contributor ranking is **DEFERRED to Analytics v1.1**: Raw description is not a durable merchant identity and this design does not create a Merchant Entity or turn source text into one. A future contributor label must be normalized/redacted upstream.

## Date and refund semantics

All grouping uses `analytics_effective_date`, not Event date, posting date, or Raw date. Full, partial, and multiple refund Golden Cases prove the original purchase month contributes respectively 0, 3000, and 2500 CNY; refund months do not gain negative spending.

## Golden Case matrix

All fixtures are synthetic and de-identified under `tests/fixtures/consumption/analytics/<a..t>/`, each containing `input_events.json`, `input_interpretations.json`, `input_coverage.json`, `expected_analytics.json`, and `case_manifest.json`.

| Cases | Frozen behavior |
|---|---|
| A–C | full, partial, and multiple refunds use active original projections and do not create negative refund months |
| D–G | unclassified eligible remains in total; eligibility review is excluded; rent/category confirmation replays historical structure |
| H–K | hard exclusion, native CNY, bank settlement CNY, and unresolved FX behavior |
| L–M | amount-based coverage and total-denominator category shares |
| N–P | current partial, missing account, and observed-only coverage semantics |
| Q–R | eligibility review excluded from denominator and classification review included in total/unclassified |
| S–T | rent promotion and travel reclassification consume current active interpretations |

Result: **A–T PASS deterministically**.

## Real-source targeted validation

The three provided statement sources are read only locally and only into an in-memory validation database. The validation checks monthly arithmetic invariants: classified + unclassified equals known eligible total, refunds do not create a negative refund month, repayments remain excluded, eligibility review stays outside total, and current/source coverage state is retained. No real statement, row, identifier, raw description, or real amount is committed or printed by this report. Current high review coverage is expected and is not optimized away.

## Compatibility and isolation

Event / Classification Compatibility: **COMPATIBLE**. No Event, EventRawLink, projection, interpretation, rule, travel-context, account-prior, or production persistence schema changes are required.

- real statement / real row / real amount fixture committed: **NO**
- LLM/MCP/network: **NO**
- production analytics service, DB table, API, UI, chart, review UI: **NO**
- Pattern / IA / frontend modified: **NO**

## Open items and readiness

1. Analytics Service implementation needs a read-only ORM query adapter that materializes the active Event projection and active interpretation DTOs without reading Raw for aggregation.
2. Source coverage needs product configuration for the expected connected-account set and verified import completeness policy.
3. Merchant identity/contributor normalization and bounded review-detail service remain separate later work.
4. Historical FX resolution remains a separate deterministic data-policy task; unresolved amounts remain explicitly partial.

Next readiness: **READY_FOR_ANALYTICS_IMPLEMENTATION**.
