# WealthPilot 消费分析 Economic Event Design & Golden Case Report v1

日期：2026-08-29
范围：`RawTransaction → EconomicEvent` 的纯内存契约、确定性参考 normalizer 与脱敏 Golden Cases。
不在范围：ORM / 事件表 / migration / 分类 / Analytics / UI / AI / MCP / 网络汇率。

## 1. Baseline

| Item | Value |
| --- | --- |
| Start HEAD | `9138718f2d96bde1448cf6ca1256ec3868ae107b` |
| `main` / `origin/main` at start | `9138718f2d96bde1448cf6ca1256ec3868ae107b` |
| Branch | `codex/consumption-economic-event-design` |
| Inputs reviewed | PRD v0.2.2; Current Code / Data Architecture Audit v1; Adapter Spike Report v1; Raw Persistence / Schema Freeze Report v1 |
| Raw schema change | NO |
| Real statement / raw row committed or logged | NO |

The requested audit was found in the supplied project folder’s `备用/` subdirectory. It is historical input only; current code, the Raw Schema Freeze report and tests remain authoritative for implemented facts.

## 2. Final Event Taxonomy

Only `CONSUMPTION` is eligible for a future consumption aggregate. This is a semantic boundary, not implementation of Analytics.

| Event Type | Decision | Meaning | Enters Consumption Analytics? |
| --- | --- | --- |
| `CONSUMPTION` | KEEP | A source-supported real purchase | YES, using net amount only |
| `REFUND` | KEEP | Returned value, optionally linked to one purchase | NO; adjusts linked purchase projection |
| `CREDIT_CARD_REPAYMENT` | KEEP | Settlement of card liability | NO |
| `INTERNAL_TRANSFER` | KEEP | Movement between proven owned accounts | NO |
| `INVESTMENT_TRANSFER` | KEEP | Bank/broker/fund/wealth subscription or redemption movement | NO |
| `LIQUIDITY_SWEEP` | KEEP | Cash-management sweep/redemption | NO |
| `INCOME` | KEEP | Source-explicit salary/provident fund or equivalent | NO |
| `LOAN_DISBURSEMENT` | KEEP | Loan proceeds | NO |
| `DEBT_REPAYMENT` | KEEP | Loan principal payment | NO |
| `FEE_INTEREST` | KEEP | Financial fee or interest | NO |
| `REBATE` | KEEP | Cashback, red packet or bank benefit | NO |
| `OTHER` | KEEP | Facts that cannot safely be typed yet | NO |

No category is split or merged in this phase. Consumption purpose categories such as DAILY/TRAVEL/HOUSING and merchant taxonomy are explicitly deferred.

## 3. Executable Event Contract

`backend/services/consumption/economic_events.py` is deliberately a plain dataclass contract. It takes an in-memory projection of existing Raw facts and a separate `NormalizationContext`; it neither imports the Raw ORM model nor opens a database.

Required event facts are: deterministic provisional `event_id`; `event_type`; `event_date`; ordered `raw_transaction_ids`; positive-magnitude `amount` and original `currency`; `economic_direction`; resolution status/reason; rule sources; `normalizer_version`; and CNY-base conversion state (`base_currency`, `base_amount`, `fx_source`, `fx_rate`). `event_date` may be null only when neither Raw date is available.

Optional factual/linking fields are `analytics_effective_date`, `original_event_id`, and CNY amount/rate when conversion cannot yet be justified. `gross_amount`, `refund_amount` and `net_amount` are derived purchase-projection fields: they apply only to a `CONSUMPTION` event. The independent `REFUND` event remains immutable evidence. Future persistence must record an auditable derived-projection/revision rather than overwrite the original purchase fact.

Raw input retains its source signed amount. Event amounts are always positive magnitudes; `economic_direction` carries OUTFLOW/INFLOW/NEUTRAL. This means an aggregate never has to infer a bank’s sign convention.

## 4. Raw ↔ Event Linking Model

At one normalizer version, a Raw is either unlinked or links to one EconomicEvent candidate: **Raw `0..1` → Event**. A resolved event can link **N Raw rows**. The reference supports N:1 only for a proved internal-transfer pair and cross-batch candidate duplicate; the later association table must preserve per-link provenance/rule evidence.

`1 Raw → N Event` is not allowed for the current semantic projection. A refund links to its original **event**, not by cloning its Raw into the purchase event. Future correction/version history may supersede a projection, but must be modeled as versioned audit evidence rather than concurrent semantic double-use of one Raw.

- Card purchase and debit-card repayment are two separate Events; no transaction-level repayment allocation is invented.
- `CANDIDATE_DUPLICATE` may form one event with two Raw links only when a higher-confidence source-equivalent rule succeeds. Raw facts are never deleted.
- `AMBIGUOUS` never merges merely because a candidate fingerprint matches; it yields independent events/candidates.
- Internal transfer needs known ownership for both accounts, opposite equal amounts, same currency and a bounded effective-date window (reference: three days). Otherwise it is `OTHER / NEEDS_REVIEW`.

## 5. Amount, Date and FX Semantics

`event_date = transaction_date`, falling back only to `posting_date`; the fallback is not presented as an asserted transaction date. For a normal purchase, `analytics_effective_date = event_date`. A matched refund uses its own `event_date`, but its analytics-effective date is the original purchase event date. An unmatched refund has no analytics-effective date.

For full or partial refund, `gross_amount` is the original purchase magnitude; `refund_amount` is the proved matched refund magnitude; `net_amount = gross - refund`. The Golden reference caps a single matched refund at gross amount; multiple-refund accumulation and user-confirmed matching require versioned persistent implementation.

`base_currency` is frozen as CNY. Priority is:

1. CNY settlement supplied by bank: `BANK_SETTLEMENT`, preserving settlement-derived rate.
2. Native CNY: `NATIVE_CNY`, rate `1.00`.
3. No CNY settlement for a foreign amount: no invented conversion; `FX_REQUIRED` and `NEEDS_REVIEW`.

No Golden Case calls a rate provider. A future deterministic historical-rate fallback must persist its rate date and source before it can resolve case 3.

## 6. Resolution and Explainability

`event_type` says what occurred; `RESOLVED`, `UNMATCHED`, `AMBIGUOUS` and `NEEDS_REVIEW` say how certain or complete the determination is. Every candidate includes ordered rule sources from `DESCRIPTION_RULE`, `ACCOUNT_PAIR_MATCH`, `AMOUNT_DATE_MATCH` and/or `SOURCE_DEDUP`. The source description markers in fixtures are synthetic stand-ins for future bank-specific deterministic evidence rules; no merchant taxonomy is implied.

## 7. Golden Case Matrix

Fixtures live under `tests/fixtures/consumption/economic_events/<case>/`; every A–P directory contains `input_raw_transactions.json`, `expected_events.json`, and `case_manifest.json`, all synthetic.

| Case | Scenario | Expected Event | Result | Ambiguity |
| --- | --- | --- | --- | --- |
| A | ordinary credit-card purchase | one `CONSUMPTION` | PASS | none |
| B | purchase plus debit repayment | `CONSUMPTION` + `CREDIT_CARD_REPAYMENT` | PASS | repayment allocation absent |
| C | total statement repayment | purchases + independent repayment | PASS | no fabricated allocation |
| D | liquidity sweep plus payment | `LIQUIDITY_SWEEP` + `CONSUMPTION` | PASS | none |
| E | proven self transfer | one two-Raw `INTERNAL_TRANSFER` | PASS | ownership is external context |
| F | bank/broker/fund/wealth movements | `INVESTMENT_TRANSFER` | PASS | investment P&L deferred |
| G | full matched refund | refund + purchase net zero | PASS | production matching needs stronger proof |
| H | partial matched refund | refund + purchase net 3,000 | PASS | persistent projection audit deferred |
| I | unmatched refund | `REFUND / UNMATCHED` | PASS | human confirmation later |
| J | installments and fee | one purchase, repayments, fee, unresolved principal | PASS | no original-purchase reconstruction |
| K | salary/provident/transfer/unknown inbound | income, transfer, reviewable other | PASS | ordinary inbound unproven |
| L | loan proceeds/principal/interest | loan, debt repayment, fee | PASS | none |
| M | cashback/red packet | `REBATE` | PASS | none |
| N | cross-batch candidate duplicate | one event, two Raw links | PASS | production evidence retained per link |
| O | same-batch ambiguous repeats | two events, never silent merge | PASS | source cannot prove duplication |
| P | bank CNY settlement/native CNY/no settlement | bank/native/FX-required outcomes | PASS | historical FX deferred |

## 8. Double-counting Safety

The fixture suite explicitly proves that card repayment, installment payment, liquidity sweep, internal transfer and investment movement do not produce a second `CONSUMPTION`. It also proves source duplicate collapse preserves both Raw links while ambiguous repeats remain separate. Future aggregate queries must consume resolved `CONSUMPTION.net_amount`, never Raw rows and never all event types.

## 9. Raw Schema Compatibility

**COMPATIBLE.** Current RawTransaction holds source dates, signed original/settlement amounts, currency, description, account/instrument links and duplicate state needed by this reference. Account ownership is deliberately a future resolver/context concern, not a Raw column. The needed future persistence additions are Event and Event–Raw association tables; none are introduced here.

## 10. Open Items

1. ORM phase must choose append-only event/projection revision and Event–Raw association audit schema, including multiple-refund accumulation.
2. Production evidence rules need bank-specific supported descriptions/references; this reference intentionally uses synthetic explicit markers only.
3. A historical FX fallback needs a versioned source, `fx_date`, repeatable fixtures and no-network failure behavior.
4. Account ownership confirmation lifecycle must be designed before automatic internal-transfer resolution.

## 11. Next Readiness

**READY_WITH_OPEN_ITEMS.** The semantic contract, cardinality, anti-double-counting boundaries and deterministic Golden baseline are ready for Event ORM + production normalizer design. The four open items are persistence/evidence design questions, not Raw Schema blockers.
