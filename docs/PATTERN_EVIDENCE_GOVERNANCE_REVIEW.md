# Pattern Evidence Governance Review

> Stage: 1F
>
> Contract status: `FROZEN_FOR_DOWNSTREAM_INTEGRATION`
>
> Project status: `PATTERN_EVIDENCE_GOVERNANCE_READY`
>
> Equivalent meaning: `READY_FOR_PATTERN_EVIDENCE_INTEGRATION`

## A. Executive decision

The product boundary is frozen as:

```text
Pattern = Decision Evidence / Technical Context
Pattern ≠ Signal ≠ Recommendation ≠ Execution Authority
```

All six launch Patterns may be exposed as technical evidence. None is a unique primary trading signal, none grants action authority, and reversal structures must be described as observed structures rather than predictions.

This governance approval means only that the product contract is ready for later Decision, AI-explanation, and UI integration work. It does not authorize Production Promotion.

Current truth remains:

- Pattern Engine technical validation: complete.
- Real IBKR evidence: available.
- AI-assisted engineering review: complete.
- Independent human chart review: not performed.
- Holdout: unopened.
- Untouched Validation: unopened.
- Decision integration: not implemented.
- Production Promotion: not authorized.

## B. Reviewed implementation truth

Stage 1F reused the existing provider-independent contracts:

- `PatternCoreInput` for canonical closed-session identity and source hash;
- `PatternResult` and `PatternCandidate` for detector facts and stable identity;
- separate `ConfirmationAssessment` objects for structure and direction;
- `LifecycleSnapshot` for `candidate`, `confirmed`, `invalidated`, and `expired`;
- `PatternDataStatus` for data-quality and availability outcomes.

No detector or calibration semantics required modification. The existing `confidence_class` field belongs only to internal boundary/trend structural completeness. It is not a probability, is not present in `PatternResult`, and is intentionally excluded from `PatternEvidenceBundle` and the AI-safe projection.

## C. Six-Pattern visibility review

All six Patterns are permitted in the Workspace, bounded AI context, and Decision evidence context. They default to collapsed because technical structure is supporting evidence, not a primary conclusion. Every visible Pattern carries a technical-context risk note.

- Breakout: bullish price-break context; structure, volume, and direction facts remain separately visible.
- Breakdown: bearish price-break context; it does not imply a short recommendation.
- Rectangle: neutral range structure; direction confirmation is `NOT_REQUIRED`.
- Ascending Triangle: bullish structural context; direction may remain `PENDING` until a later valid upside close.
- Double Top: bearish reversal structure; it must not be phrased as a prediction and direction may remain `PENDING` until neckline breakdown.
- Double Bottom: bullish reversal structure; direction requires both neckline breakout and the frozen volume hard gate.

The detailed matrix is in [PATTERN_EVIDENCE_GOVERNANCE_MATRIX.md](./PATTERN_EVIDENCE_GOVERNANCE_MATRIX.md).

## D. Product presentation families

The following presentation grouping is approved:

```text
Level Break Evidence
  breakout
  breakdown

Range / Continuation Structure
  rectangle
  ascending_triangle

Reversal Structure Evidence
  double_top
  double_bottom
```

This is presentation metadata only. It does not replace or alter detector families:

- `breakout` / `breakdown` retain `level_break`;
- `rectangle` retains `range`;
- `ascending_triangle` retains `triangle`;
- `double_top` / `double_bottom` retain `reversal`.

## E. Lifecycle visibility

Canonical product mapping:

| Detector lifecycle | Product status | Visibility | Meaning |
| --- | --- | --- | --- |
| `candidate` | none | Internal only | Formation has not crossed the frozen product-visible lifecycle boundary. |
| `confirmed` | `CONFIRMED` | Visible | Required structure and applicable direction contract reached technical confirmation. |
| `invalidated` | `INVALIDATED` | Visible | A later technical fact invalidated the structure; this is not an engine error. |
| `expired` | `EXPIRED` | Visible | The session-based evidence window expired; this is not an engine error. |

`PatternEvidenceAdapter` refuses direct product mapping of `candidate`. Direction and structure remain independent inside every visible bundle. Therefore an invalidated or expired evidence snapshot can truthfully preserve `structure=confirmed` and `direction=pending`.

## F. Evidence visibility policy

Raw `PatternEvidenceBundle` is the audit snapshot, not a direct prompt or UI dump. Product consumers must use a governed projection.

Permitted evidence includes:

- Breakout / Breakdown: boundary, break close, confirmation date, volume context, EMA alignment context, and invalidation.
- Rectangle: range high/low, width, touch counts, duration, and invalidation boundaries.
- Ascending Triangle: resistance, rising support, touches, convergence/apex context, later direction confirmation, and invalidation.
- Double Top / Bottom: two extremes, intervening reaction, neckline, similarity, duration, direction confirmation, invalidation, and applicable volume role.

Internal fit noise, implementation counters, rejected proposal details, and non-governed detector internals are not passed into AI context.

Static SVG/PNG is an evidence presentation artifact only. Structured facts remain authoritative.

## G. AI consumption boundary

The frozen path is:

```text
PatternEvidenceBundle
        ↓
PatternAIContextAdapter
        ↓
bounded factual context + source hashes + risk note
```

The projection uses a Pattern-specific fact-code allowlist. It may state facts such as structure, levels, confirmation state, or later invalidation. It must not infer an action, payoff probability, win rate, position size, leverage, or an execution instruction.

Allowed example:

> A confirmed double-bottom structure formed around the recorded troughs, with the neckline near the supplied level. The structure was later invalidated.

Disallowed examples include “buy now,” “high upside probability,” “70% win rate,” or “use leverage.” Such authority would require a future, separate, explicitly governed layer.

## H. Decision consumption boundary

A future Decision adapter may consume Pattern evidence as:

- supporting evidence;
- technical context;
- a reasoning citation;
- conflict or confirmation context.

It may not use Pattern evidence as direct action authority, numeric ExecutionPlan authority, position-sizing authority, or order-creation authority.

No Decision integration is implemented in Stage 1F. The canonical result envelope is deliberately non-blocking: `NO_PATTERN`, data failure, or `ENGINE_ERROR` remains a distinct value outcome. The existing Decision system must continue without Pattern evidence when any of those states occurs.

## I. Invocation governance

The v1 invocation contract is frozen but not implemented here.

Allowed:

- explicit single symbol;
- explicit comparison of up to three symbols.

Not allowed:

- portfolio-wide fan-out;
- a generic query without a symbol;
- automatic full-universe scan;
- multi-leg Trade Intent invocation;
- scanner or scheduler behavior.

## J. Deterministic selection and presentation

No model performs evidence ranking. The deterministic order is:

1. lifecycle relevance: `CONFIRMED`, then `INVALIDATED`, then `EXPIRED`;
2. structure-confirmation recency, newest first;
3. direction state: `CONFIRMED`, `NOT_REQUIRED`, `PENDING`, `REJECTED`;
4. frozen Pattern type order;
5. stable candidate identity.

Presentation selects at most three `CONFIRMED` bundles as `top_evidence`. Every other found bundle remains in `remaining_evidence`. “Top” means presentation order only, never primary signal, profit ranking, or recommendation.

## K. Persistence and snapshot boundary

v1 stores Pattern evidence only as an immutable message/decision snapshot. It does not create an independent Pattern lifecycle database.

Every found snapshot retains:

- canonical instrument identity;
- `source_bar_hash` and `candidate_source_bar_hash`;
- `detector_version` and detector result hash;
- `indicator_layer_version`;
- `calibration_version`;
- `parameter_set_id` and `parameter_hash`;
- structure, direction, geometry, invalidation, and lifecycle facts.

An optional snapshot reference may point to static SVG or PNG. URI and media type must be supplied together. The image is not fact authority.

## L. Result-state governance

The six states are frozen and never collapsed into a generic error:

```text
PATTERN_FOUND
NO_PATTERN
INSUFFICIENT_HISTORY
DATA_UNAVAILABLE
DATA_QUALITY_BLOCKED
ENGINE_ERROR
```

In particular:

```text
NO_PATTERN ≠ ENGINE_ERROR
DATA_QUALITY_BLOCKED ≠ NO_PATTERN
```

Only `PATTERN_FOUND` may carry a Pattern evidence payload or evidence snapshot. Every other state carries an explicit reason and no fabricated Pattern facts.

## M. Implementation and contract tests

Stage 1F adds only the governed evidence boundary:

- immutable value contracts and hashes;
- deterministic adapter from existing `PatternResult`;
- distinct non-found/error outcomes;
- six-Pattern visibility policy;
- AI-safe fact projection;
- deterministic sorting and confirmed-only Top 3 presentation selection.

Contract tests cover all six Patterns, structure/direction separation, forbidden authority fields, distinct result states, complete provenance, optional snapshot URI, non-blocking engine errors, internal candidate rejection, allowlisted AI context, visibility policy, and deterministic order.

## N. Remaining blockers

These are intentionally outside Stage 1F:

- independent governance decision on whether human chart review is required before Production Promotion;
- frozen Holdout execution;
- frozen Untouched Validation execution;
- Decision/AI/UI integration implementation and product acceptance;
- any Production Promotion decision.

None blocks the contract from entering downstream integration development. All block any claim of `PRODUCTION_READY`.

## O. Safety and verdict

```text
Detector algorithm change = 0
Calibration parameter change = 0
Holdout execution = 0
Untouched Validation execution = 0
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Decision integration = 0
Production rollout = 0
```

Final status:

```text
PATTERN_EVIDENCE_GOVERNANCE_READY
READY_FOR_PATTERN_EVIDENCE_INTEGRATION
```

Not:

```text
PRODUCTION_READY
```
