# WealthPilot 消费分析 Eligibility + Classification Design & Golden Case Report v1

日期：2026-08-29
范围：EconomicEvent 的纯内存消费资格与分类解释 contract、deterministic Golden Cases 与本地只读验证。
不在范围：Classification/Rule/Travel ORM、Analytics、API、UI、AI、MCP、网络服务或 Event Layer schema 变更。

## 1. Baseline

| Item | Value |
| --- | --- |
| Event implementation commit | `6f30e40d66f064a563db72e401a216e410a91be8` |
| Event closeout | ff-only into `main`, then pushed |
| `main` / `origin/main` after closeout | `6f30e40d66f064a563db72e401a216e410a91be8` |
| Design branch | `codex/consumption-classification-design` |
| Design start HEAD | `6f30e40d66f064a563db72e401a216e410a91be8` |

## 2. Eligibility Contract

Eligibility answers only whether an Event belongs to the consumption-analysis object set. It is not a second Event taxonomy.

| Status | Meaning |
| --- | --- |
| `ELIGIBLE` | A consumption Event, or an `OTHER` explicitly promoted by a bounded user rule/confirmation |
| `INELIGIBLE` | Permanently outside consumption analysis |
| `NEEDS_REVIEW` | `OTHER` whose consumption nature is still unknown |

This deliberately separates eligibility uncertainty from category uncertainty. An explicitly confirmed `OTHER` can be `ELIGIBLE` while its classification remains `NEEDS_REVIEW`; it is not discarded merely because its DAILY/TRAVEL/HOUSING category is unknown.

## 3. Hard Exclusion Matrix

| Event Type | Eligibility |
| --- | --- |
| `CONSUMPTION` | ELIGIBLE |
| `REFUND` | INELIGIBLE as an independent classified item; matched refund inherits its original Event reference |
| `CREDIT_CARD_REPAYMENT` | INELIGIBLE |
| `INTERNAL_TRANSFER` | INELIGIBLE |
| `INVESTMENT_TRANSFER` | INELIGIBLE |
| `LIQUIDITY_SWEEP` | INELIGIBLE |
| `INCOME` | INELIGIBLE |
| `LOAN_DISBURSEMENT` | INELIGIBLE |
| `DEBT_REPAYMENT` | INELIGIBLE |
| `FEE_INTEREST` | INELIGIBLE |
| `REBATE` | INELIGIBLE |
| `OTHER` | NEEDS_REVIEW unless explicit user interpretation applies |

Hard exclusion is the highest priority and cannot be overridden by account prior, source wording, merchant rule or a user classification attempt.

## 4. Frozen Category Taxonomy

Primary categories are exactly `DAILY`, `TRAVEL`, `HOUSING`.

| Primary | Secondary codes |
| --- | --- |
| DAILY | `FOOD_DINING`, `TRANSPORT_AUTO`, `SHOPPING`, `HOME_LIVING`, `DIGITAL_COMMUNICATION`, `HEALTH_INSURANCE`, `SPORTS_HOBBY`, `PET`, `OTHER` |
| TRAVEL | `LONG_DISTANCE_TRANSPORT`, `ACCOMMODATION`, `LOCAL_TRANSPORT`, `FOOD_DINING`, `ACTIVITIES_EXPERIENCES`, `TRAVEL_SHOPPING`, `OTHER` |
| HOUSING | `RENT`, `PROPERTY_FEE`, `OTHER` |

Property fee is always `HOUSING / PROPERTY_FEE`, never DAILY `HOME_LIVING`.

## 5. Classification Contract and Provenance

The executable pure contract returns `event_id`, eligibility status/source/reason, classification status, optional primary/secondary categories, classification source/reason, optional bounded rule ID, and optional `inherited_from_event_id`. Required sources are explicit, never an opaque model score: `USER_RULE`, `USER_CONFIRMATION`, `MERCHANT_RULE`, `ACCOUNT_PURPOSE_PRIOR`, `TRAVEL_CONTEXT`, `BANK_EXPLICIT`, `SYSTEM_RULE`, and `UNKNOWN`.

The next ORM phase should persist this as a versioned interpretation projection. It must not mutate the EconomicEvent fact or its normalizer history when a user promotes/rejects an `OTHER` Event.

## 6. Frozen Deterministic Priority

1. Hard non-consumption exclusion.
2. Refund inheritance/unmatched-refund exclusion.
3. User explicit confirmation.
4. Bounded user classification/eligibility rule.
5. Specific high-confidence semantic rule (property fee, flight, hotel, digital service).
6. Travel Context for otherwise generic dining/local transport.
7. Generic merchant semantic rule.
8. Account-purpose prior as evidence only; it cannot independently choose a category.
9. `NEEDS_REVIEW`.

This lets a travel date context override a generic Meituan/dining or taxi rule, but cannot override a specific housing, hotel/flight, or digital-service semantic fact. It prevents a CCB travel preference from retroactively converting historical local purchases into travel.

## 7. Account Purpose and Travel Context

An account-purpose prior contains `account_id`, preferred primary category, and `effective_from`/optional `effective_to`. It is intentionally weak: it can explain a recommendation or leave an Event category for review, never decide a category by itself.

Travel Context P0 contains only `destination`, `start_date`, `end_date`. It is classification evidence, not itinerary, booking, GPS or travel-product data. Within a context, generic dining maps to `TRAVEL / FOOD_DINING` and taxi maps to `TRAVEL / LOCAL_TRANSPORT`.

## 8. User Correction and Rule Memory

A one-off correction is Event-scoped evidence. Reusable memory is a narrow user rule with an explicit match text, optional account, amount/tolerance and effective dates. The fixed-rent Golden rule requires all of account + counterparty text + 6,500±10 + effective date. This enables safe historical/future batch application without creating an unlimited merchant rule. A user may also explicitly mark an `OTHER` Event INELIGIBLE, removing it from the consumer queue.

## 9. OTHER and Refund Handling

`OTHER` begins at `NEEDS_REVIEW`; it is not automatically spend. A source/user rule can promote it to `ELIGIBLE`, possibly still category-unknown. A user can reject it as `INELIGIBLE`. No rule attempts to turn all debit-card OTHER rows into consumption.

Matched REFUND has no independent DAILY/TRAVEL/HOUSING category. Its result references the original consumption Event, whose Event Layer projection adjusts spending. Unmatched REFUND is independently INELIGIBLE and waits for future matching.

## 10. Golden Case Matrix

All synthetic/de-identified fixtures reside in `tests/fixtures/consumption/classification/<a..t>/`, each with `input_events.json`, `context.json`, `expected_classification.json`, and `case_manifest.json`.

| Cases | Result |
| --- | --- |
| A–F | daily dining, property fee, dated CCB prior, travel meal, flight and hotel PASS |
| G–H | bounded rent promotion and personal-transfer rejection PASS |
| I | hard exclusion cannot be overridden PASS |
| J–K | Travel Context dining/taxi PASS |
| L–M | foreign digital service is DAILY; CNY flight is TRAVEL PASS |
| N–O | user correction and same-merchant travel context PASS |
| P–Q | matched refund inheritance and unmatched refund exclusion PASS |
| R–S | category-unknown eligible promotion and non-consumption rejection PASS |
| T | bounded same-rule batch application PASS |

## 11. Real-source Targeted Validation

Only local in-memory parsing and aggregate counting were used. No raw statement, raw description or identifier was printed, persisted, committed, sent to an LLM/MCP, or sent over the network.

| Source | Events | Eligibility | Directly classified | Needs review |
| --- | ---: | --- | ---: | ---: |
| CMB credit | 1,150 | 1,148 eligible; 2 ineligible | 200 | 0 |
| CCB credit | 1,170 | 1,170 eligible | 206 | 0 |
| CMB debit | 1,110 | 554 ineligible; 556 review | 0 | 556 |

The CMB debit outcome is intentional: its 556 `OTHER` rows are not forced into spending. They remain candidates for bounded user confirmation; non-consumption confirmation removes them rather than creating a classification backlog.

## 12. Event Layer Compatibility and Readiness

**Event Layer Compatibility: COMPATIBLE.** Classification consumes a read-only Event projection and linked Raw evidence. No change was made to `EconomicEvent`, `EventRawLink`, `ProjectionRevision`, normalizer or database schema.

Open items for Classification ORM implementation:

1. Choose the append-only eligibility/classification interpretation projection and user-rule persistence tables.
2. Define user-confirmation audit actor/timestamp boundary and rule-replay lifecycle.
3. Decide how a user manually links an unmatched refund before its inherited classification becomes visible.

**Next Readiness: READY_FOR_CLASSIFICATION_SCHEMA_IMPLEMENTATION.**
