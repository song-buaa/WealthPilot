"""Governed product boundary for deterministic Technical Pattern evidence."""

from .adapter import PatternEvidenceAdapter
from .contracts import (
    PATTERN_EVIDENCE_SCHEMA_VERSION,
    ConfirmationEvidenceSnapshot,
    EvidenceFactSnapshot,
    EvidenceGeometrySnapshot,
    EvidenceInvalidationSnapshot,
    EvidenceProvenance,
    EvidenceSnapshotReference,
    PatternEvidence,
    PatternEvidenceBundle,
    PatternEvidenceDescriptor,
    PatternEvidenceResultState,
    PatternInstrumentIdentity,
    ProductLifecycleStatus,
    SourceFactSnapshot,
)
from .policy import (
    PATTERN_VISIBILITY_POLICIES,
    PatternAIContext,
    PatternAIContextAdapter,
    PatternEvidenceSelection,
    PatternVisibilityPolicy,
    select_for_presentation,
    sort_pattern_evidence,
)

__all__ = [
    "PATTERN_EVIDENCE_SCHEMA_VERSION",
    "PATTERN_VISIBILITY_POLICIES",
    "ConfirmationEvidenceSnapshot",
    "EvidenceFactSnapshot",
    "EvidenceGeometrySnapshot",
    "EvidenceInvalidationSnapshot",
    "EvidenceProvenance",
    "EvidenceSnapshotReference",
    "PatternAIContext",
    "PatternAIContextAdapter",
    "PatternEvidence",
    "PatternEvidenceAdapter",
    "PatternEvidenceBundle",
    "PatternEvidenceDescriptor",
    "PatternEvidenceResultState",
    "PatternEvidenceSelection",
    "PatternInstrumentIdentity",
    "PatternVisibilityPolicy",
    "ProductLifecycleStatus",
    "SourceFactSnapshot",
    "select_for_presentation",
    "sort_pattern_evidence",
]
