"""Bounded Pattern Evidence context for existing Decision AI explanations.

The governed ``PatternAIContextAdapter`` remains the sole projection and
allowlist owner.  This module only preserves Decision symbol attribution,
consumes the Stage 2B Top Evidence selection, and formats the already-projected
facts for prompts.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .decision_integration import (
    DecisionPatternEvidenceSnapshot,
    PatternInvocationScope,
)
from .evidence import (
    PatternAIContext,
    PatternAIContextAdapter,
    PatternEvidenceResultState,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PatternAIInstrumentContext:
    """Existing adapter projections attributed to one Decision symbol."""

    requested_symbol: str
    patterns: tuple[PatternAIContext, ...]


@dataclass(frozen=True)
class DecisionPatternAIContext:
    """Small immutable envelope around governed PatternAIContext values."""

    invocation_scope: PatternInvocationScope
    instruments: tuple[PatternAIInstrumentContext, ...]


def _bundle_requested_symbol(bundle: Any) -> str:
    return f"{bundle.instrument.symbol}:{bundle.instrument.market}"


def build_pattern_ai_context(
    snapshot: DecisionPatternEvidenceSnapshot | None,
    *,
    requested_symbols: tuple[str, ...] | None = None,
) -> DecisionPatternAIContext | None:
    """Project the Stage 2B confirmed-only Top Evidence selection.

    Projection failures are isolated per bundle.  No remaining evidence is
    promoted when the governed Top Evidence selection is empty.
    """

    if snapshot is None or not snapshot.top_evidence_candidate_ids:
        return None

    selected_ids = set(snapshot.top_evidence_candidate_ids)
    requested_filter = (
        set(requested_symbols) if requested_symbols is not None else None
    )
    grouped: dict[str, list[PatternAIContext]] = {
        symbol: []
        for symbol in snapshot.requested_symbols
        if requested_filter is None or symbol in requested_filter
    }

    for bundle in snapshot.bundles:
        requested_symbol = _bundle_requested_symbol(bundle)
        if requested_symbol not in grouped:
            continue
        if (
            bundle.result_state is not PatternEvidenceResultState.PATTERN_FOUND
            or bundle.evidence is None
            or bundle.evidence.pattern.candidate_id not in selected_ids
        ):
            continue
        try:
            projected = PatternAIContextAdapter.project(bundle)
        except Exception as exc:  # fail open: AI explanation remains available
            logger.warning(
                "Pattern AI projection skipped for %s: %s",
                requested_symbol,
                type(exc).__name__,
            )
            continue
        if projected is not None:
            grouped[requested_symbol].append(projected)

    instruments = tuple(
        PatternAIInstrumentContext(symbol, tuple(grouped[symbol]))
        for symbol in snapshot.requested_symbols
        if symbol in grouped and grouped[symbol]
    )
    if not instruments:
        return None
    return DecisionPatternAIContext(snapshot.invocation_scope, instruments)


def _value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _context_payload(context: DecisionPatternAIContext) -> dict[str, Any]:
    """Serialize only fields already approved by PatternAIContextAdapter."""

    return {
        "invocation_scope": context.invocation_scope.value,
        "instruments": [
            {
                "requested_symbol": instrument.requested_symbol,
                "patterns": [
                    {
                        "instrument_id": pattern.instrument_id,
                        "symbol": pattern.symbol,
                        "pattern_type": _value(pattern.pattern_type),
                        "direction": _value(pattern.direction),
                        "lifecycle_status": _value(pattern.lifecycle_status),
                        "structure_confirmation_state": (
                            pattern.structure_confirmation_state
                        ),
                        "direction_confirmation_state": (
                            pattern.direction_confirmation_state
                        ),
                        "structure_observed_on": pattern.structure_observed_on,
                        "direction_observed_on": pattern.direction_observed_on,
                        "invalidated": pattern.invalidated,
                        "invalidated_on": pattern.invalidated_on,
                        "facts": [
                            {"code": fact.code, "value": fact.value}
                            for fact in pattern.facts
                        ],
                        "source_bar_hash": pattern.source_bar_hash,
                        "detector_result_hash": pattern.detector_result_hash,
                        "evidence_snapshot_uri": pattern.evidence_snapshot_uri,
                        "risk_note": pattern.risk_note,
                    }
                    for pattern in instrument.patterns
                ],
            }
            for instrument in context.instruments
        ],
    }


def format_pattern_ai_prompt_section(
    context: DecisionPatternAIContext | None,
    *,
    compare: bool = False,
) -> str:
    """Return a factual supporting section, or empty text on any failure."""

    if context is None:
        return ""
    try:
        payload = json.dumps(
            _context_payload(context),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except Exception as exc:  # fail open: omit the optional section
        logger.warning(
            "Pattern AI context serialization skipped: %s", type(exc).__name__
        )
        return ""

    compare_rule = (
        "保留 requested_symbol 归属；不得合并跨标的事实，也不得用形态证据"
        "做排序、打分、胜负判断或资金分配。"
        if compare
        else "只将这些事实作为当前标的的辅助技术证据。"
    )
    return (
        "## 技术形态证据（只读辅助上下文）\n"
        "以下 JSON 仅含治理白名单事实。严格保持 structure_confirmation_state、"
        "direction_confirmation_state 与 lifecycle_status 的原始语义；pending 不得"
        "提升为 confirmed，not_required 不得解释为方向确认。INVALIDATED/EXPIRED "
        "是历史技术事实，不是当前有效确认，也不是引擎错误。\n"
        f"{compare_rule}\n"
        "形态证据只能用于措辞、事实引用和冲突说明；不得据此改变 decisionType、"
        "actionable、操作方向、仓位、价格、止损止盈、收益/概率判断或任何执行参数。\n"
        f"```json\n{payload}\n```"
    )
