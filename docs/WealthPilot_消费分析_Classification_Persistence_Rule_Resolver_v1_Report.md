# WealthPilot 消费分析 Classification Persistence + Rule Resolver v1 报告

## Baseline

- Design closeout: `codex/consumption-classification-design` 已以 ff-only 收口。
- `main` / `origin/main` closeout head: `b785f3300c452058781019a14ef31804f29ac668`。
- Implementation branch: `codex/consumption-classification`。
- Start HEAD: `b785f3300c452058781019a14ef31804f29ac668`。

## Final persistence model

| Model | Purpose |
|---|---|
| `ConsumptionInterpretation` | EconomicEvent 的 append-only eligibility / classification projection；每个 event 只有一个 active revision。 |
| `ConsumptionInterpretationAudit` | Local user confirmation 的 actor、time、reason、old/new revision trace。 |
| `UserClassificationRule` | bounded deterministic rule：account、text、amount/tolerance、effective dates 与 status 可组合约束。 |
| `TravelContext` | 最小 destination/date-window context。 |
| `AccountPurposePreference` | 带 effective range 的账户用途 weak prior。 |

事实层保持不变：resolver 只读取 `EconomicEvent + active EventRawLink + RawTransaction` 的证据，不修改 Event/Raw，也不从 Raw 直接聚合消费。

## Eligibility / Classification states

Eligibility 单独持久化为 `ELIGIBLE`、`INELIGIBLE`、`NEEDS_REVIEW`；Classification 单独持久化为 `CLASSIFIED`、`NEEDS_REVIEW`、`NOT_APPLICABLE`。因此 `ELIGIBLE + classification NEEDS_REVIEW` 是有效且可查询的状态。`NOT_APPLICABLE` 只用于非消费（含 refund 本身）。

Taxonomy 固定为 `DAILY`、`TRAVEL`、`HOUSING`。二级类别固定继承设计：Daily 的 FOOD_DINING / TRANSPORT_AUTO / SHOPPING / HOME_LIVING / DIGITAL_COMMUNICATION / HEALTH_INSURANCE / SPORTS_HOBBY / PET / OTHER，Travel 的 LONG_DISTANCE_TRANSPORT / ACCOMMODATION / LOCAL_TRANSPORT / FOOD_DINING / ACTIVITIES_EXPERIENCES / TRAVEL_SHOPPING / OTHER，以及 Housing 的 RENT / PROPERTY_FEE / OTHER。

## Production rule priority

1. Hard exclusion；
2. Refund；
3. User explicit confirmation；
4. Active bounded User Rule；
5. Specific semantic rule；
6. Travel Context（只覆盖 generic dining / taxi）；
7. Generic merchant semantic rule；
8. Account purpose weak prior；
9. NEEDS_REVIEW。

Hard exclusion cannot be promoted. A replay returns an existing user-confirmed active revision rather than allowing lower-priority rules to replace it.

## User confirmation, rules, and contexts

User confirmation appends a new interpretation revision and immutable audit row (`LOCAL_USER`), retaining the old revision. OTHER may be promoted to eligible/classified, promoted to eligible/review, or explicitly rejected as ineligible.

User rules are deterministic and scope-bounded. The rent case is supported by linked raw description plus account, amount `6500 ± tolerance`, and effective date. Inactive rules and rules outside the inclusive effective-date window do not apply. Replaying unchanged events/rules/resolver version is semantic-idempotent and does not create another active revision.

Property-fee text wins over an account-purpose prior and maps to `HOUSING / PROPERTY_FEE`. Flight and hotel map to Travel. Vercel, Cursor, Cloudflare, and Google One map to `DAILY / DIGITAL_COMMUNICATION`; foreign currency is not Travel evidence. Travel Context applies at both date boundaries and only changes generic dining/taxi to Travel categories. Account preference remains evidence only and cannot classify by itself.

## Refund inheritance

A refund is independently `INELIGIBLE / NOT_APPLICABLE`. For a matched refund, `get_effective_classification()` reads the original consumption event’s active interpretation; it does not create a refund category. An unmatched refund has no inherited classification.

## Golden Cases

| Cases | Result |
|---|---|
| A–T design fixture set, now persisted through ORM resolver | PASS (20/20) |
| U eligible + classification review persistence | PASS |
| V confirmation revision and audit | PASS |
| W rule replay idempotency | PASS |
| X lower-priority rule cannot override confirmation | PASS |
| Y account prior alone cannot classify | PASS |
| Z rule effective-date inclusive boundary | PASS |
| AA Travel Context start/end boundary | PASS |
| AB inactive rule ignored | PASS |
| AC property fee beats account prior | PASS |
| AD foreign digital service is not Travel | PASS |
| AE matched refund reads original classification | PASS |

## Real-source targeted validation

The three user-provided sources were read locally only through the existing adapters into an in-memory database. No source row, account identifier, description, or statement was committed or logged. Aggregate results:

| Source | Eligibility: eligible / ineligible / needs review | Classification: classified / needs review / not applicable | DAILY / TRAVEL / HOUSING |
|---|---:|---:|---:|
| CMB Credit | 1148 / 2 / 0 | 200 / 948 / 2 | 165 / 25 / 10 |
| CCB Credit | 1170 / 0 / 0 | 208 / 962 / 0 | 168 / 34 / 6 |
| CMB Debit | 0 / 554 / 556 | 0 / 0 / 1110 | 0 / 0 / 0 |

The debit-card result intentionally does not auto-promote its 556 eligibility-review events. The tested scoped rent rule can safely promote a matching event; explicit non-consumption may be rejected; every remaining OTHER stays outside the classification queue until eligibility is established.

## Coverage philosophy and compatibility

High confidence is preferred over false coverage: an eligible event with unknown category remains classification `NEEDS_REVIEW`, rather than being guessed. Event Layer Compatibility: **COMPATIBLE**. No EconomicEvent taxonomy, EventRawLink cardinality, or Event projection semantics changed.

## Privacy / isolation

- real source committed: **NO**
- LLM/MCP/network: **NO**
- Pattern modified: **NO**
- IA modified: **NO**
- frontend / analytics / aggregation: **NO**

## Open items and next readiness

- Manual linking for an unmatched refund remains deferred until a focused Event-layer-safe service/API is specified.
- Analytics design still needs to define active-interpretation aggregation, refund netting projection consumption, and review-queue presentation boundaries.

Next readiness: **READY_WITH_OPEN_ITEMS**. The classification layer is stable enough for Analytics Design; the open items are deliberately deferred rather than hidden by a classification heuristic.
