"""Visibility, AI projection, and deterministic presentation governance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..detectors.contracts import EvidenceValue
from .contracts import (
    PatternDirectionValue,
    PatternEvidenceBundle,
    PatternEvidenceResultState,
    PatternTypeValue,
    ProductLifecycleStatus,
)


DisplayMode = Literal["collapsed", "expanded"]


@dataclass(frozen=True)
class PatternVisibilityPolicy:
    pattern_type: PatternTypeValue
    presentation_group: str
    workspace_visible: bool
    ai_context_allowed: bool
    decision_evidence_allowed: bool
    default_display: DisplayMode
    risk_note_required: bool
    direction_semantics: str
    risk_note: str


_TECHNICAL_CONTEXT_RISK_NOTE = (
    "Technical structure evidence only; not a recommendation or execution authority."
)
_REVERSAL_RISK_NOTE = (
    "Reversal structure evidence is descriptive, not a prediction or recommendation."
)
PATTERN_VISIBILITY_POLICIES = (
    PatternVisibilityPolicy(
        "breakout",
        "Level Break Evidence",
        True,
        True,
        True,
        "collapsed",
        True,
        "Bullish price-break context; direction confirmation remains separate.",
        _TECHNICAL_CONTEXT_RISK_NOTE,
    ),
    PatternVisibilityPolicy(
        "breakdown",
        "Level Break Evidence",
        True,
        True,
        True,
        "collapsed",
        True,
        "Bearish price-break context; it does not authorize a short position.",
        _TECHNICAL_CONTEXT_RISK_NOTE,
    ),
    PatternVisibilityPolicy(
        "rectangle",
        "Range / Continuation Structure",
        True,
        True,
        True,
        "collapsed",
        True,
        "Neutral range structure; direction confirmation is NOT_REQUIRED.",
        _TECHNICAL_CONTEXT_RISK_NOTE,
    ),
    PatternVisibilityPolicy(
        "ascending_triangle",
        "Range / Continuation Structure",
        True,
        True,
        True,
        "collapsed",
        True,
        "Bullish structural context; direction may remain PENDING until a later close.",
        _TECHNICAL_CONTEXT_RISK_NOTE,
    ),
    PatternVisibilityPolicy(
        "double_top",
        "Reversal Structure Evidence",
        True,
        True,
        True,
        "collapsed",
        True,
        "Bearish reversal structure; direction may remain PENDING until neckline break.",
        _REVERSAL_RISK_NOTE,
    ),
    PatternVisibilityPolicy(
        "double_bottom",
        "Reversal Structure Evidence",
        True,
        True,
        True,
        "collapsed",
        True,
        "Bullish reversal structure; direction requires neckline break and volume gate.",
        _REVERSAL_RISK_NOTE,
    ),
)
_POLICY_BY_PATTERN = {item.pattern_type: item for item in PATTERN_VISIBILITY_POLICIES}


_VISIBLE_FACT_CODES = {
    "breakout": frozenset(
        {
            "boundary_axis",
            "boundary_zone_high",
            "boundary_zone_low",
            "boundary_touch_count",
            "break_close",
            "break_threshold",
            "volume_confirmed",
            "volume_ratio",
            "ema_direction_aligned",
            "invalidation_boundary",
            "price_break_confirmed",
        }
    ),
    "breakdown": frozenset(
        {
            "boundary_axis",
            "boundary_zone_high",
            "boundary_zone_low",
            "boundary_touch_count",
            "break_close",
            "break_threshold",
            "volume_confirmed",
            "volume_ratio",
            "ema_direction_aligned",
            "invalidation_boundary",
            "price_break_confirmed",
        }
    ),
    "rectangle": frozenset(
        {
            "range_high",
            "range_low",
            "range_width",
            "range_width_pct",
            "support_touch_count",
            "resistance_touch_count",
            "structure_span_sessions",
            "invalidation_lower_boundary",
            "invalidation_upper_boundary",
        }
    ),
    "ascending_triangle": frozenset(
        {
            "resistance_at_confirmation",
            "support_at_confirmation",
            "resistance_touch_count",
            "support_touch_count",
            "contraction_pct",
            "apex_progress_at_confirmation",
            "apex_session_ordinal",
            "ascending_triangle_upside_close_confirmed",
        }
    ),
    "double_top": frozenset(
        {
            "first_extreme_price",
            "second_extreme_price",
            "intervening_reaction_ratio",
            "neckline_price",
            "extreme_similarity_ratio",
            "structure_duration_sessions",
            "double_top_downside_neckline_close_confirmed",
            "volume_confirmation_role",
        }
    ),
    "double_bottom": frozenset(
        {
            "first_extreme_price",
            "second_extreme_price",
            "intervening_reaction_ratio",
            "neckline_price",
            "extreme_similarity_ratio",
            "structure_duration_sessions",
            "double_bottom_upside_neckline_close_confirmed",
            "direction_confirmation_volume_ratio",
            "volume_confirmation_role",
        }
    ),
}


@dataclass(frozen=True)
class ProjectedEvidenceFact:
    code: str
    value: EvidenceValue


@dataclass(frozen=True)
class PatternAIContext:
    instrument_id: str
    symbol: str
    pattern_type: PatternTypeValue
    direction: PatternDirectionValue
    lifecycle_status: ProductLifecycleStatus
    structure_confirmation_state: str
    direction_confirmation_state: str
    structure_observed_on: str | None
    direction_observed_on: str | None
    invalidated: bool
    invalidated_on: str | None
    facts: tuple[ProjectedEvidenceFact, ...]
    source_bar_hash: str
    detector_result_hash: str
    evidence_snapshot_uri: str | None
    risk_note: str


class PatternAIContextAdapter:
    """Project only governed factual fields; never generate trading language."""

    @staticmethod
    def project(bundle: PatternEvidenceBundle) -> PatternAIContext | None:
        if (
            bundle.result_state is not PatternEvidenceResultState.PATTERN_FOUND
            or bundle.evidence is None
        ):
            return None
        evidence = bundle.evidence
        pattern_type = evidence.pattern.pattern_type
        policy = _POLICY_BY_PATTERN[pattern_type]
        if not policy.ai_context_allowed:
            return None
        allowed = _VISIBLE_FACT_CODES[pattern_type]
        candidates = (
            evidence.geometry.facts
            + evidence.structure_confirmation.facts
            + evidence.direction_confirmation.facts
            + evidence.invalidation.facts
        )
        visible = {
            item.code: ProjectedEvidenceFact(item.code, item.value)
            for item in candidates
            if item.code in allowed
        }
        return PatternAIContext(
            instrument_id=bundle.instrument.instrument_id,
            symbol=bundle.instrument.symbol,
            pattern_type=pattern_type,
            direction=evidence.pattern.direction,
            lifecycle_status=evidence.pattern.lifecycle_status,
            structure_confirmation_state=evidence.structure_confirmation.state,
            direction_confirmation_state=evidence.direction_confirmation.state,
            structure_observed_on=(
                evidence.structure_confirmation.observed_on.isoformat()
                if evidence.structure_confirmation.observed_on
                else None
            ),
            direction_observed_on=(
                evidence.direction_confirmation.observed_on.isoformat()
                if evidence.direction_confirmation.observed_on
                else None
            ),
            invalidated=evidence.invalidation.invalidated,
            invalidated_on=(
                evidence.invalidation.observed_on.isoformat()
                if evidence.invalidation.observed_on
                else None
            ),
            facts=tuple(visible[key] for key in sorted(visible)),
            source_bar_hash=evidence.provenance.source_bar_hash,
            detector_result_hash=evidence.provenance.detector_result_hash,
            evidence_snapshot_uri=bundle.evidence_snapshot.uri,
            risk_note=policy.risk_note,
        )


_LIFECYCLE_ORDER = {
    ProductLifecycleStatus.CONFIRMED: 0,
    ProductLifecycleStatus.INVALIDATED: 1,
    ProductLifecycleStatus.EXPIRED: 2,
}
_DIRECTION_STATE_ORDER = {"confirmed": 0, "not_required": 1, "pending": 2, "rejected": 3}
_PATTERN_TYPE_ORDER = {
    "breakout": 0,
    "breakdown": 1,
    "rectangle": 2,
    "ascending_triangle": 3,
    "double_top": 4,
    "double_bottom": 5,
}


def _sort_key(bundle: PatternEvidenceBundle) -> tuple[int, int, int, int, str]:
    if bundle.evidence is None:
        return (99, 0, 99, 99, bundle.result_state.value)
    evidence = bundle.evidence
    observed = evidence.structure_confirmation.observed_on
    recency = -(observed.toordinal() if observed else 0)
    return (
        _LIFECYCLE_ORDER[evidence.pattern.lifecycle_status],
        recency,
        _DIRECTION_STATE_ORDER[evidence.direction_confirmation.state],
        _PATTERN_TYPE_ORDER[evidence.pattern.pattern_type],
        evidence.pattern.candidate_id,
    )


def sort_pattern_evidence(
    bundles: tuple[PatternEvidenceBundle, ...],
) -> tuple[PatternEvidenceBundle, ...]:
    """Deterministic evidence order with no model or payoff ranking."""

    return tuple(sorted(bundles, key=_sort_key))


@dataclass(frozen=True)
class PatternEvidenceSelection:
    top_evidence: tuple[PatternEvidenceBundle, ...]
    remaining_evidence: tuple[PatternEvidenceBundle, ...]


def select_for_presentation(
    bundles: tuple[PatternEvidenceBundle, ...],
    *,
    top_limit: int = 3,
) -> PatternEvidenceSelection:
    if top_limit < 0:
        raise ValueError("top_limit must be non-negative")
    found = tuple(
        item
        for item in sort_pattern_evidence(bundles)
        if item.result_state is PatternEvidenceResultState.PATTERN_FOUND
        and item.evidence is not None
    )
    confirmed = tuple(
        item
        for item in found
        if item.evidence
        and item.evidence.pattern.lifecycle_status is ProductLifecycleStatus.CONFIRMED
    )
    top = confirmed[:top_limit]
    top_ids = {item.evidence.pattern.candidate_id for item in top if item.evidence}
    remaining = tuple(
        item
        for item in found
        if item.evidence and item.evidence.pattern.candidate_id not in top_ids
    )
    return PatternEvidenceSelection(top, remaining)
