"""Human-authored thesis/review contracts. A label is not source verification."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Literal
from urllib.parse import urlsplit, parse_qsl

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator

ID = r"^[A-Za-z0-9_-]{1,80}$"
Text = str


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Security(Record):
    symbol: str = Field(min_length=1, max_length=50)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    share_class: str | None = Field(default=None, max_length=200)
    identity_reference: str | None = Field(default=None, max_length=2000)
    identity_gap: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def canonical(self):
        from backend.utils.symbol import parse_symbol, normalize_symbol
        ticker, market = parse_symbol(self.symbol)
        if normalize_symbol(ticker, market) != self.symbol:
            raise ValueError("E1_CANONICAL_SYMBOL_REQUIRED")
        if not (self.identity_reference or self.identity_gap):
            raise ValueError("E1_IDENTITY_REFERENCE_OR_GAP_REQUIRED")
        return self


Kind = Literal["FACT", "SOURCE_OPINION", "USER_RECOLLECTION", "HINDSIGHT", "HYPOTHESIS"]


class Claim(Record):
    claim_id: str = Field(pattern=ID)
    text: str = Field(min_length=1, max_length=4000)
    kind: Kind


class EvidenceRef(Record):
    evidence_id: str = Field(pattern=ID)
    claim_id: str = Field(pattern=ID)
    relation: Literal["supports", "opposes", "contextual"]
    kind: Kind
    description: str = Field(min_length=1, max_length=4000)
    source_ref: str = Field(min_length=1, max_length=2000)
    source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    published_at: AwareDatetime | None = None
    known_at: AwareDatetime | None = None
    fetched_at: AwareDatetime | None = None
    time_precision: Literal["INSTANT", "DAY", "UNKNOWN"] = "UNKNOWN"
    unknown_time_reason: str | None = Field(default=None, max_length=2000)
    verification: Literal["UNVERIFIED", "MANUALLY_CHECKED"] = "UNVERIFIED"
    verification_reference: str | None = Field(default=None, max_length=2000)
    gaps: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def source_and_time(self):
        p = urlsplit(self.source_ref)
        if p.scheme in {"http", "https"}:
            if not p.hostname or p.username or p.password or any(
                    k.lower() in {"apikey", "api_key", "token", "access_token", "password"}
                    for k, _ in parse_qsl(p.query)):
                raise ValueError("E1_UNSAFE_SOURCE_REFERENCE")
        elif p.scheme not in {"document", "research-card", "fixture", "local"}:
            raise ValueError("E1_SOURCE_REFERENCE_SCHEME")
        if any(v is None for v in (self.published_at, self.known_at, self.fetched_at)) and not self.unknown_time_reason:
            raise ValueError("E1_UNKNOWN_TIME_REASON_REQUIRED")
        if self.verification == "MANUALLY_CHECKED" and not self.verification_reference:
            raise ValueError("E1_VERIFICATION_REFERENCE_REQUIRED")
        if self.known_at and self.published_at and self.known_at < self.published_at:
            raise ValueError("E1_KNOWN_BEFORE_PUBLICATION")
        return self


class ThesisDraft(Record):
    thesis_id: str = Field(pattern=ID)
    security: Security
    decision_as_of: AwareDatetime | None
    decision_time_gap: str | None = None
    event_at: AwareDatetime | None = None
    event_time_gap: str | None = None
    event: str = Field(min_length=1, max_length=4000)
    causal_chain: list[str] = Field(min_length=1, max_length=30)
    expectation_gap: str = Field(min_length=1, max_length=4000)
    time_horizon: str = Field(min_length=1, max_length=2000)
    hypothesis_due_at: AwareDatetime | None = None
    invalidation_conditions: list[str] = Field(min_length=1, max_length=30)
    user_original_judgment: str = Field(min_length=1, max_length=6000)
    original_judgment_kind: Literal["USER_RECOLLECTION", "HYPOTHESIS"]
    user_reflection: str | None = Field(default=None, max_length=4000)
    claims: list[Claim] = Field(min_length=1, max_length=60)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=120)
    status: Literal["DRAFT", "ACTIVE", "WATCH", "INVALIDATED", "CLOSED"] = "DRAFT"
    change_reason: str = Field(min_length=1, max_length=2000)
    synthetic: bool = False

    @model_validator(mode="after")
    def references(self):
        claims = {c.claim_id for c in self.claims}
        if len(claims) != len(self.claims) or len({e.evidence_id for e in self.evidence}) != len(self.evidence):
            raise ValueError("E1_DUPLICATE_CLAIM_OR_EVIDENCE")
        if any(e.claim_id not in claims for e in self.evidence):
            raise ValueError("E1_DANGLING_CLAIM_REFERENCE")
        if self.decision_as_of is None and not self.decision_time_gap:
            raise ValueError("E1_DECISION_TIME_GAP_REQUIRED")
        if self.event_at is None and not self.event_time_gap:
            raise ValueError("E1_EVENT_TIME_GAP_REQUIRED")
        return self


class ReviewDraft(Record):
    thesis_id: str = Field(pattern=ID)
    thesis_revision: int = Field(gt=0, strict=True)
    review_as_of: AwareDatetime
    action_intent: Literal["RESEARCH", "BUY", "ADD", "HOLD"]
    requested_verdict: Literal["SUPPORT", "WATCH", "NOT_SUPPORTED", "UNKNOWN"]
    authored_by: Literal["USER", "HUMAN_ASSISTANT_COLLABORATION"]
    rationale: str = Field(min_length=1, max_length=6000)
    evidence_changes: list[str] = Field(default_factory=list, max_length=50)
    evidence_ids: list[str] = Field(default_factory=list, max_length=120)
    supporting_scenario: str = Field(min_length=1, max_length=4000)
    opposing_scenario: str = Field(min_length=1, max_length=4000)
    price_and_waiting_cost: str = Field(min_length=1, max_length=4000)
    price_evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    invalidation_conditions: list[str] = Field(min_length=1, max_length=30)
    time_hypothesis_status: Literal["UNTESTED", "MET", "NOT_MET", "UNKNOWN"] = "UNKNOWN"
    next_review_at: AwareDatetime | None = None
    next_review_event: str | None = Field(default=None, max_length=2000)
    not_run_reason: str | None = Field(default=None, max_length=2000)


def now():
    return datetime.now(timezone.utc)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def load_json(path):
    """Explicit local input only. Reject duplicate keys/NaN, never follow source refs."""
    from pathlib import Path
    p = Path(path)
    if p.is_symlink() or p.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("E1_INPUT_FILE_INVALID")
    def pairs(items):
        value = {}
        for k, v in items:
            if k in value:
                raise ValueError("E1_DUPLICATE_JSON_KEY")
            value[k] = v
        return value
    return json.loads(p.read_text(), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("E1_NONFINITE_JSON")))


def evidence_qualification(evidence, at):
    """PIT eligibility of supplied references, NOT independent truth certification."""
    reasons = []
    for name in ("published_at", "known_at", "fetched_at"):
        t = getattr(evidence, name)
        if t is None:
            reasons.append(name.upper() + "_UNKNOWN")
    if evidence.known_at and evidence.known_at > at:
        reasons.append("KNOWN_AFTER_DECISION")
    if evidence.published_at and evidence.published_at > at:
        reasons.append("PUBLISHED_AFTER_DECISION")
    if evidence.kind != "FACT":
        reasons.append("NOT_A_VERIFIED_FACT")
    if evidence.verification != "MANUALLY_CHECKED":
        reasons.append("SOURCE_NOT_CHECKED")
    if evidence.time_precision != "INSTANT":
        reasons.append("INTRADAY_TIME_UNVERIFIED")
    reasons.extend(evidence.gaps)
    return dict(evidence_id=evidence.evidence_id, claim_id=evidence.claim_id,
                relation=evidence.relation, eligible=not reasons, reasons=sorted(set(reasons)),
                authority="Supplied classification and manual-check reference; no automatic source verification")
