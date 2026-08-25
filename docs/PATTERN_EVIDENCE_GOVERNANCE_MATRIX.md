# Pattern Evidence Governance Matrix

> Product principle: `Pattern = Evidence ≠ Signal ≠ Recommendation ≠ Execution Authority`

## Six-Pattern matrix

| Pattern | Presentation group | Product Visible | AI Context | Decision Evidence | Default | Direction Semantics | Risk Note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Breakout | Level Break Evidence | Yes | Allowed via allowlist projection | Supporting evidence only | Collapsed | Bullish price-break context; structure/volume/direction remain separate | Required: technical context only, not a recommendation |
| Breakdown | Level Break Evidence | Yes | Allowed via allowlist projection | Supporting evidence only | Collapsed | Bearish price-break context; no short-position authority | Required: technical context only, not a recommendation |
| Rectangle | Range / Continuation Structure | Yes | Allowed via allowlist projection | Supporting evidence only | Collapsed | Neutral structure; direction is `NOT_REQUIRED` | Required: technical context only, not a recommendation |
| Ascending Triangle | Range / Continuation Structure | Yes | Allowed via allowlist projection | Supporting evidence only | Collapsed | Bullish structural context; direction may remain `PENDING` until later valid close | Required: technical context only, not a recommendation |
| Double Top | Reversal Structure Evidence | Yes | Allowed via allowlist projection | Supporting evidence only | Collapsed | Bearish reversal structure; direction may remain `PENDING` until neckline break | Required: descriptive structure, not prediction or recommendation |
| Double Bottom | Reversal Structure Evidence | Yes | Allowed via allowlist projection | Supporting evidence only | Collapsed | Bullish reversal structure; direction needs neckline break plus volume hard gate | Required: descriptive structure, not prediction or recommendation |

The presentation groups do not modify detector `pattern_family` values.

## Lifecycle matrix

| Internal state | Canonical product state | User-visible | Error? | Governance |
| --- | --- | --- | --- | --- |
| `candidate` | none | No | No | Internal formation evidence only; direct adapter mapping is rejected. |
| `confirmed` | `CONFIRMED` | Yes | No | Eligible for deterministic Top 3. |
| `invalidated` | `INVALIDATED` | Yes | No | Historical technical evidence; later fact invalidated structure. |
| `expired` | `EXPIRED` | Yes | No | Historical technical evidence; session window elapsed. |

Structure and direction confirmation remain separate for every visible lifecycle state.

## Result-state governance

| Result state | Pattern payload | Snapshot URI | Caller behavior | Meaning |
| --- | --- | --- | --- | --- |
| `PATTERN_FOUND` | Required | Optional static SVG/PNG | Consume governed evidence | At least one user-visible Pattern evidence item exists. |
| `NO_PATTERN` | Forbidden | Forbidden | Continue without Pattern context | Query succeeded; no user-visible Pattern evidence exists. |
| `INSUFFICIENT_HISTORY` | Forbidden | Forbidden | Continue without Pattern context | Closed-bar history is shorter than the exact calibration requirement. |
| `DATA_UNAVAILABLE` | Forbidden | Forbidden | Continue without Pattern context | Provider data could not be obtained. |
| `DATA_QUALITY_BLOCKED` | Forbidden | Forbidden | Continue without Pattern context | Expected-session or canonical data-quality gate failed. |
| `ENGINE_ERROR` | Forbidden | Forbidden | Continue without Pattern context and retain explicit error state | Unexpected Pattern processing failure; not `NO_PATTERN`. |

## Consumer authority matrix

| Consumer | May receive | Must not receive / infer |
| --- | --- | --- |
| Workspace UI | Governed levels, dates, lifecycle, structure/direction state, risk note, optional static snapshot | Recommendation, payoff probability, position sizing, order intent |
| AI explanation | Pattern-specific allowlisted facts, source hashes, lifecycle and risk note | “Buy/sell now,” probability, win rate, leverage, Entry/SL/TP |
| Decision reasoning | Supporting context, citation, confirmation/conflict evidence | Direct action authority, numeric plan authority, order creation |
| Persistence | Immutable bundle snapshot and provenance | Independent Pattern lifecycle database |
| Visualization | Static SVG/PNG presentation artifact | Fact authority |

## Deterministic presentation order

```text
1. lifecycle relevance
2. structure-confirmation recency
3. direction-confirmation state
4. frozen Pattern type order
5. stable candidate identity
```

Only `CONFIRMED` evidence can enter the Top 3. All remaining found evidence is expandable. There is no unique primary signal and no model-based ranking.

Final governance status: `PATTERN_EVIDENCE_GOVERNANCE_READY`, not `PRODUCTION_READY`.
