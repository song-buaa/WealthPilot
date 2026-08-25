# PatternEvidenceBundle Contract v1

> Schema version: `wp-pattern-evidence-bundle-v1`
>
> Authority: technical evidence snapshot only
>
> Status: frozen for downstream integration development

## 1. Canonical envelope

WealthPilot uses one canonical envelope for both found evidence and distinct non-found/error outcomes:

```ts
type PatternEvidenceBundle = {
  schema_version: "wp-pattern-evidence-bundle-v1"

  instrument: {
    instrument_id: string
    symbol: string
    market: string
    economic_asset_class: string
    con_id?: number | null
    isin?: string | null
    currency?: string | null
  }

  timeframe: "1d"

  result_state:
    | "PATTERN_FOUND"
    | "NO_PATTERN"
    | "INSUFFICIENT_HISTORY"
    | "DATA_UNAVAILABLE"
    | "DATA_QUALITY_BLOCKED"
    | "ENGINE_ERROR"

  evidence?: PatternEvidence | null

  evidence_snapshot: {
    uri?: string | null
    media_type?: "image/svg+xml" | "image/png" | null
  }

  reason: string
}
```

Invariants:

- `PATTERN_FOUND` requires `evidence` and may reference one static snapshot.
- Every other result state forbids `evidence` and snapshot URI and requires a reason.
- URI and media type are both present or both absent.
- The envelope and every nested value object are immutable.
- `bundle_hash` is the deterministic SHA-256 of canonical serialization.

## 2. Found evidence payload

```ts
type PatternEvidence = {
  pattern: {
    candidate_id: string
    pattern_type:
      | "breakout"
      | "breakdown"
      | "rectangle"
      | "ascending_triangle"
      | "double_top"
      | "double_bottom"
    pattern_family: string
    direction: "bullish" | "bearish" | "neutral"
    lifecycle_status: "CONFIRMED" | "INVALIDATED" | "EXPIRED"
    formed_on: date
    available_from: date
    evaluated_on: date
  }

  structure_confirmation: ConfirmationEvidenceSnapshot
  direction_confirmation: ConfirmationEvidenceSnapshot
  geometry: EvidenceGeometrySnapshot
  invalidation: EvidenceInvalidationSnapshot
  provenance: EvidenceProvenance
}
```

`candidate` is not a product lifecycle value. The adapter rejects it as internal-only.

## 3. Confirmation contracts

```ts
type ConfirmationEvidenceSnapshot = {
  state: "pending" | "confirmed" | "rejected" | "not_required"
  reason: string
  observed_on?: date | null
  observed_session_ordinal?: number | null
  facts: EvidenceFactSnapshot[]
}
```

Structure and direction are independent objects. They must never be collapsed into one boolean.

Canonical examples:

| Pattern | Structure | Direction |
| --- | --- | --- |
| Rectangle | `confirmed` | `not_required` |
| Ascending Triangle before later upside close | `confirmed` | `pending` |
| Double Top before neckline breakdown | `confirmed` | `pending` |
| Double Bottom before neckline breakout plus volume gate | `confirmed` | `pending` |

## 4. Geometry and fact lineage

```ts
type EvidenceGeometrySnapshot = {
  pivots: SourceFactSnapshot[]
  boundaries: SourceFactSnapshot[]
  facts: EvidenceFactSnapshot[]
}

type EvidenceFactSnapshot = {
  code: string
  value: boolean | number | string
  available_from: date
  available_from_session_ordinal: number
  source_ids: string[]
}

type SourceFactSnapshot = {
  source_type: "pivot" | "boundary"
  source_id: string
  available_from: date
  available_from_session_ordinal: number
}
```

Every fact retains causal availability and source lineage. The adapter reuses `EvidenceFact` and `SourceFactReference` semantics rather than inventing a second detector model.

## 5. Invalidation

```ts
type EvidenceInvalidationSnapshot = {
  invalidated: boolean
  condition: string
  reason?: string | null
  observed_on?: date | null
  observed_session_ordinal?: number | null
  facts: EvidenceFactSnapshot[]
}
```

`INVALIDATED` is technical evidence, not an engine error. A true invalidation requires reason and observation. A false invalidation must not invent either.

## 6. Provenance

```ts
type EvidenceProvenance = {
  provider: "IBKR"
  source_bar_hash: sha256
  candidate_source_bar_hash: sha256
  detector_version: string
  indicator_layer_version: string
  calibration_version: string
  parameter_set_id: string
  parameter_hash: sha256
  detector_result_hash: sha256
}
```

The adapter requires the exact frozen `parameter_hash`; it has no fallback. These fields make a persisted message/decision snapshot auditable without creating a Pattern lifecycle database.

## 7. Result-state mapping

| Source outcome | Bundle state |
| --- | --- |
| Visible `PatternResult` | `PATTERN_FOUND` |
| Successful evaluation with no visible evidence | `NO_PATTERN` |
| `PatternDataStatus.INSUFFICIENT_HISTORY` | `INSUFFICIENT_HISTORY` |
| `PatternDataStatus.DATA_UNAVAILABLE` | `DATA_UNAVAILABLE` |
| `PatternDataStatus.DATA_QUALITY_BLOCKED` | `DATA_QUALITY_BLOCKED` |
| Unexpected engine exception at the product boundary | `ENGINE_ERROR` |

The safe boundary records only the exception type in the public reason. It does not leak arbitrary internal exception text and does not block an existing caller.

## 8. AI-safe projection

Raw bundles must not be copied wholesale into a prompt. `PatternAIContextAdapter` projects:

- stable instrument and Pattern identity;
- lifecycle status;
- separate structure and direction states/dates;
- invalidation state/date;
- Pattern-specific allowlisted technical facts;
- source and detector result hashes;
- optional snapshot URI;
- mandatory risk note.

Non-found/error bundles project to no AI context. Internal detector noise is omitted.

## 9. Deterministic presentation

`sort_pattern_evidence` uses only lifecycle relevance, structure recency, direction state, frozen Pattern type order, and candidate identity. It contains no model call and no payoff metric.

`select_for_presentation` returns:

```ts
type PatternEvidenceSelection = {
  top_evidence: PatternEvidenceBundle[]       // confirmed-only, max 3 by default
  remaining_evidence: PatternEvidenceBundle[]
}
```

The name “top” means display position, not primary signal or recommendation.

## 10. Explicitly forbidden semantics

The contract contains no fields for:

```text
entry
stop_loss
take_profit
position_size
leverage
expected_return
probability
win_rate
confidence
action
buy
sell
order
```

There are no broker execution identifiers or numeric plan fields. Existing internal `confidence_class` describes structural completeness only and is deliberately not exported.

## 11. Consumer boundary

- UI may show governed facts and static evidence presentation.
- AI may produce bounded factual explanation.
- Decision may later cite evidence as supporting context.
- Pattern evidence may not create actions, plans, positions, or orders.
- Any downstream integration must preserve graceful operation for all five non-found/error states.

## 12. Freeze statement

Changing field semantics, result-state meanings, lifecycle visibility, forbidden authority, or structure/direction separation requires a new schema version and governance review.

Current status:

```text
PATTERN_EVIDENCE_GOVERNANCE_READY
READY_FOR_PATTERN_EVIDENCE_INTEGRATION
```

This contract is not a `PRODUCTION_READY` declaration.
