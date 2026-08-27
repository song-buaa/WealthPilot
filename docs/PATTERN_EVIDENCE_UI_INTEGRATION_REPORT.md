# Pattern Evidence UI Integration Report

> Stage 2D · Canonical read-only evidence presentation · 2026-08-27

## A. Executive Conclusion

```text
PATTERN_EVIDENCE_UI_READY
READY_FOR_STAGE_2E_RUNTIME_PROMOTION_AND_E2E
```

The Decision UI can now render the existing canonical Pattern Evidence
snapshot for both the current SSE turn and restored conversation history. The
frontend uses the backend-provided Top/Remaining candidate IDs exactly, keeps
structure confirmation, direction confirmation, and lifecycle separate, and
shows only governed technical facts. The section is evidence-only, collapsed
by default, and has no Decision, ActionDraft, execution, broker, or order
authority.

This conclusion does not promote the runtime provider or activate production
Pattern data. The default runtime outcome remains `DATA_UNAVAILABLE` with
`runtime_pattern_provider_not_promoted`, which intentionally produces no
visible Pattern section.

## B. Branch / HEAD / Commit

| Item | Value |
| --- | --- |
| Branch | `codex/pattern-evidence-ui` |
| Start HEAD | `488043639b54cb57341beeca3227383e49053fce` |
| Stage 2D commit | This report's implementation commit — `feat(technical-patterns): render pattern evidence in decision ui` |
| Push / merge / tag | `NO / NO / NO` |

The branch starts at the accepted Stage 2C HEAD. No backend snapshot, evidence
policy, detector, calibration, prompt, persistence, or runtime-provider
contract is changed.

## C. Frontend Contract

`frontend/src/lib/api.ts` now represents the canonical backend serialization
with one shared `DecisionPatternEvidenceSnapshotDTO` and nested bundle DTOs.
The same type is used by SSE `done.pattern_evidence` and
`ConversationMessageDTO.metadata.pattern_evidence`; no unrelated UI snapshot
schema is introduced.

A narrow runtime reader validates the frozen schema version, invocation scope,
all six result states, six governed Pattern types, confirmations, lifecycle,
facts, and optional SVG/PNG snapshot pairing. Missing or legacy metadata remains
backward compatible and yields no Pattern presentation.

## D. Current-turn Rendering

The existing generic SSE stream remains unchanged. On `done`, `Decision.tsx`
reads `pattern_evidence` through the shared runtime reader and attaches the
canonical snapshot to the completed assistant message. The existing right-side
Explain panel receives that message snapshot; no additional endpoint, detector
call, or frontend computation is introduced.

## E. Restored-history Rendering

Both existing conversation restoration paths read
`message.metadata.pattern_evidence` through the same runtime reader used for
SSE. The restored assistant message therefore reaches the same Explain panel,
presentation builder, and component as a current-turn message.

The parity test uses one canonical fixture and proves equal parsed snapshots,
equal presentation models, and byte-identical server-rendered evidence HTML for
the SSE and history paths. Bundle hashes, Top/Remaining IDs, lifecycle, order,
and visible facts are therefore stable across refresh.

## F. Pattern Card Design

`PatternEvidenceSection` is a focused read-only component placed alongside
supporting evidence in the existing Explain panel, before the existing
web-search and market-signal blocks. It uses the current inline visual language
and existing `lucide-react` dependency; no new design system, global style,
chart library, or icon package is added.

The section is collapsed by default and omitted entirely when there is no
visible `PATTERN_FOUND` bundle. Each compact card presents:

- localized Pattern name and requested symbol;
- lifecycle text;
- separate structure and direction confirmation state/date;
- Pattern-specific, governed technical facts;
- historical invalidation text/date when applicable;
- an optional static SVG/PNG snapshot with meaningful alt text;
- a concise evidence-only risk note.

Expansion uses semantic buttons with `aria-expanded`/`aria-controls`; status is
always expressed in text rather than color alone, and long values use safe text
wrapping.

## G. Structure / Direction / Lifecycle Presentation

The component never collapses these three dimensions into a signal badge:

- structure confirmation has its own state and observation date;
- direction confirmation has its own state and observation date, including
  `pending` and `not_required`;
- lifecycle is displayed independently as current confirmed, invalidated
  historical, or expired historical evidence.

Rectangle remains neutral structure evidence with direction not required.
Ascending Triangle and reversal structures can remain direction-pending even
when their structure is confirmed. Invalidated and expired evidence is written
in past tense and is not presented as an engine failure.

## H. Result-state Filtering

| Backend result state | Normal product UI |
| --- | --- |
| `PATTERN_FOUND` selected by backend IDs | May render an evidence card |
| `NO_PATTERN` | Silent |
| `INSUFFICIENT_HISTORY` | Silent |
| `DATA_UNAVAILABLE` | Silent |
| `DATA_QUALITY_BLOCKED` | Silent |
| `ENGINE_ERROR` | Silent |

The runtime reader preserves every result state; the presentation layer filters
only for visibility. It never rewrites `ENGINE_ERROR` as `NO_PATTERN`, and an
all-non-found snapshot renders an empty string rather than an empty section or
an unavailable message.

## I. Compare Presentation

For explicit two- or three-symbol Compare snapshots, cards remain grouped under
the backend `requested_symbols` order. Every card keeps its own requested-symbol
attribution. Facts are never merged across symbols, and the UI adds no winner,
attractiveness score, allocation, or cross-symbol ranking.

## J. Ordering / Ranking Proof

The presentation builder first maps `PATTERN_FOUND` bundles by canonical
candidate ID, then consumes `top_evidence_candidate_ids` and
`remaining_evidence_candidate_ids` in backend order. It does not sort bundles
or derive a Top 3. A deterministic test shuffles bundle input while holding the
selection IDs fixed and proves identical presentation order.

Only a small Pattern-specific fact-label allowlist controls human-readable
labels. Fact values are copied from the canonical snapshot; the frontend does
not recompute geometry, confirmation, lifecycle, probability, risk, or trade
direction.

## K. No Trading Authority Proof

The new component imports only the canonical DTO, presentation formatter, React,
and the existing chevron icon. It imports and calls no ActionDraft,
ExecutionPlan, ExecutionBatch, Broker, or Order API. Tests guard those imports
and the absence of trading/action CTAs. The implementation does not modify
Decision fields, AI prompts, `actionable`, Portfolio state, or any execution
record.

## L. Tests / Quality Gates

| Gate | Result |
| --- | --- |
| Stage 2D targeted frontend rendering/contract tests | `6 passed` |
| Stage 2B/2C + evidence backend targeted | `69 passed` |
| Full pytest | `835 passed, 7 skipped, 0 failed` |
| Python compileall | PASS |
| Frontend lint | PASS, 0 errors / 0 warnings |
| Frontend build | PASS; pre-existing non-blocking >500 kB chunk warning |
| Offline M5 | `18/18`, provider=`offline_fixture`, `public_network_attempts=0` |
| `git diff --check` | PASS |

The Stage 2D tests cover current-turn/history parsing and actual SSR parity,
backward-compatible absence, every result state, all six Pattern families,
confirmed/invalidated/expired lifecycle, confirmed/pending/not-required
direction states, optional static evidence, exact Top/Remaining ordering,
two-/three-symbol Compare attribution, silent non-found output, and absence of
trading authority.

## M. Known Limitations

- The production runtime provider is intentionally not promoted; ordinary
  runtime Decisions continue to have no visible Pattern section.
- Stage 2D renders the static evidence snapshot supplied by the canonical
  backend contract. It does not provide an interactive chart workspace or
  recalculate chart facts.
- The frontend only labels governed fact codes. Unknown future fact codes remain
  hidden until governance explicitly adds a presentation label.
- Runtime promotion, live-provider E2E, and production Pattern-family promotion
  remain Stage 2E or later decisions.

## N. Stage 2E Readiness

The UI boundary is ready for a separate governed runtime-promotion and E2E
stage:

```text
Canonical Pattern Evidence Snapshot
        ↓ shared frontend DTO / runtime reader
backend Top + Remaining selection
        ↓
collapsed evidence-only Decision UI
```

Stage 2E must separately prove provider/data readiness and full runtime E2E. It
must not infer production readiness merely from this UI integration.

## Safety

```text
Broker mutation = 0
Order mutation = 0
Portfolio mutation = 0
ExecutionPlan mutation = 0
Production DB change = 0
Decision authority change = 0
Runtime provider promotion = 0
Public network attempts = 0
```
