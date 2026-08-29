# WealthPilot 消费分析 Economic Event ORM + Normalizer v1 Report

日期：2026-08-29
范围：Raw 事实到可追溯 Economic Event 的 SQLite/SQLAlchemy 持久化与离线确定性 normalizer。
不在范围：消费分类、Analytics、UI、AI、MCP、网络汇率、Pattern 或导航变更。

## 1. Baseline 与 Design Closeout

| Item | Value |
| --- | --- |
| Design commit | `35bf51fdd3548caea301994c348f938cb732ee16` |
| Design closeout | `git merge --ff-only` into `main` |
| `main` / `origin/main` after closeout | `35bf51fdd3548caea301994c348f938cb732ee16` |
| Design branch | merged, pushed, then local branch removed |
| Implementation branch | `codex/consumption-economic-event` |
| Implementation start HEAD | `35bf51fdd3548caea301994c348f938cb732ee16` |

## 2. Final Persistence Model

### EconomicEvent (`consumption_economic_events`)

An Event is an immutable normalized fact with a deterministic, SHA-256 `semantic_key` unique with `normalizer_version`. It stores the frozen 12-type taxonomy, factual/analytics dates, positive amount plus direction, original currency, CNY base amount/rate/source, resolution state/reason, optional `original_event_id`, deterministic rule-source JSON and non-raw provenance JSON. Indexed fields cover type/date, original-event lookup and resolution lookup.

`gross_amount`, `refund_amount` and `net_amount` deliberately do **not** live as mutable Event fields. They are a derived consumption projection and therefore live in append-only revisions below. This prevents an unlogged update of an original purchase.

### EventRawLink (`consumption_event_raw_links`)

Each association has `event_id`, `raw_transaction_id`, `link_role`, `rule_source`, structured evidence, `is_active` and creation time. A partial SQLite unique index permits one Raw in at most one active semantic Event while retaining inactive historical links for a future re-normalization. An Event can have N links: candidate duplicates retain every Raw, and internal transfer legs share one Event.

### EconomicEventProjectionRevision (`consumption_event_projection_revisions`)

For each consumption Event this table records `revision_number`, gross/refund/net amounts, optional CNY net projection, reason/rule source, `supersedes_revision_id`, active state and timestamp. The partial active index guarantees one current projection; the event/revision unique constraint preserves history.

### Account ownership

`consumption_accounts.ownership_status` is the only Raw-schema evolution: `UNCONFIRMED` by default and `CONFIRMED_OWNED` only through explicit setup/fixture confirmation. `app.database._ensure_consumption_account_ownership_column()` safely adds it to an existing SQLite table and creates its index idempotently. Empty DB, existing DB and repeated init are covered by tests.

## 3. Taxonomy, Amount, Date and Cardinality

All 12 Design taxonomy values remain unchanged. Event amounts are positive magnitude; `economic_direction` is OUTFLOW/INFLOW/NEUTRAL, while Raw retains the bank-provided signed amount.

`event_date` uses Raw transaction date and only falls back to posting date. Matched refunds keep their actual refund event date but use the original purchase date for `analytics_effective_date`; unmatched refunds keep that field null. The active normalizer relation remains **Raw 0..1 active Event; Event 1..N Raw links**. Composite real rows were not observed, so no exception to this cardinality is introduced.

## 4. Production Evidence Rules v1

Rules are narrow, offline and source-explicit. They do not classify merchants or ordinary debit transfers.

| Source evidence | Event type | Protection |
| --- | --- | --- |
| 信用卡还款 / 自动还款 | CREDIT_CARD_REPAYMENT | never consumption |
| 朝朝宝转入 / 转出 | LIQUIDITY_SWEEP | neutral movement |
| 银证转账、基金/理财申赎 | INVESTMENT_TRANSFER | never income/consumption |
| 代发工资 / 公积金管理中心代发 | INCOME | ordinary inbound remains OTHER |
| 个贷放款 / 本金偿还 / 贷款利息 | LOAN_DISBURSEMENT / DEBT_REPAYMENT / FEE_INTEREST | no income inference |
| 活动现金红包 / 信用卡返现 | REBATE | distinct from refund/income |
| credit-card residual after explicit exclusions | CONSUMPTION | card-source fact, no merchant taxonomy |
| debit-card residual | OTHER / NEEDS_REVIEW | no transfer-as-consumption inference |

Synthetic `[TYPE]` markers remain test evidence only; production rules use the explicit phrases above.

## 5. Refund and Revision

A purchase creates revision 1 (gross = net). A linked refund creates its own REFUND Event/Raw link and then appends a new projection revision. Full and partial refunds update only the active projection. Multiple refunds accumulate: 5,000 + refunds 1,000 and 1,500 produces three retained revisions with active gross/refund/net = 5,000/2,500/2,500. Every REFUND fact remains independent. An unmatched refund has no original event or analytics-effective date and creates no purchase revision.

This is intentionally not a general event-sourcing framework: it is the minimum SQLite-friendly append-only projection history necessary for refund matching and future user confirmation.

## 6. FX

The deterministic resolver preserves the Design contract:

1. CNY settlement amount → `BANK_SETTLEMENT` and bank-derived rate.
2. CNY source amount → `NATIVE_CNY`, rate 1.
3. Foreign amount without CNY settlement → `FX_REQUIRED`, null base amount/rate, `NEEDS_REVIEW`.

No historical provider, network request or web lookup exists in this implementation.

## 7. Ownership, Transfer and Duplicate Resolution

Internal transfer requires an explicit internal-transfer marker, two `CONFIRMED_OWNED` accounts, opposite same-currency equal magnitudes and dates within three days. A non-confirmed pair is `OTHER / NEEDS_REVIEW`; multiple possible pairings are `OTHER / NEEDS_REVIEW` with an ambiguous reason. No account is inferred owned from names, institution or amount.

Candidate duplicate rows collapse only on the Raw layer’s deterministic same `match_fingerprint`, retaining all links and annotating `SOURCE_DEDUP`. `AMBIGUOUS` rows never collapse merely due to a superficial match fingerprint.

## 8. Idempotency and Versioning

`normalizer_version = economic-event-orm-v1` plus a semantic key built from version, event type and sorted Raw IDs makes an already-normalized Raw set a no-op on rerun. Active Raw-link uniqueness prevents a second active Event. New normalizer versions can retain prior history; a future re-normalization workflow must explicitly retire active links before activating a new semantic projection.

## 9. Golden Cases

| Cases | Result |
| --- | --- |
| A–P Design fixture regression | PASS |
| Q multiple partial refunds | PASS: revisions preserve 5,000 → 4,000 → 2,500 net |
| R rerun idempotency | PASS |
| S unconfirmed ownership | PASS: no internal transfer |
| T confirmed exact pair | PASS: two Raw links on one internal transfer |
| U multiple possible transfer pairs | PASS: ambiguous/no automatic pair |
| V production evidence rules | PASS |

## 10. Real-source Targeted Validation

Only local, read-only in-memory parsing was used. No rows/descriptions were printed, persisted, committed or sent externally.

| Source | Statements | Parsed rows | FX_REQUIRED | Aggregate high-confidence result |
| --- | ---: | ---: | ---: | --- |
| CMB credit PDF | 12 | 1,150 | 0 | 1,148 consumption; 2 fee/interest |
| CCB credit EML | 12 | 1,170 | 0 | 1,170 consumption |
| CMB debit PDF | 1 | 1,110 | 0 | 13 repayment; 485 sweep; 42 investment transfer; 2 income; 1 loan; 2 rebate; 9 refund; 556 OTHER |

Composite inspection result: **COMPOSITE_RAW_NOT_OBSERVED**. The check searched for a single normalized source line containing both principal and interest/fee indicators. Current Raw 0..1 active Event cardinality therefore remains valid for observed sources.

## 11. Privacy and Isolation

| Gate | Result |
| --- | --- |
| real source committed | NO |
| raw source logged or dumped | NO |
| LLM / MCP call | NO |
| network FX call | NO |
| Pattern modified | NO |
| IA / frontend navigation modified | NO |

## 12. Open Items and Readiness

1. Classification design may define consumption-purpose categories and user-confirmation UX; it must consume resolved Event projections rather than Raw rows.
2. A future explicit re-normalization operation must retire active EventRawLinks before activating an updated normalizer version.
3. Historical FX fallback remains a separate offline/deterministic design task.
4. Manual refund-match confirmation API/UI is deferred; the revision schema already supports the required audit history.

**Next Readiness: READY_FOR_CLASSIFICATION_DESIGN.**
