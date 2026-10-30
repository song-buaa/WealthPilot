"""Policy snapshots are derivatives, never independently editable authorities."""
from __future__ import annotations

from decimal import Decimal
from datetime import date
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def canonical(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceClause(Contract):
    document: Literal["philosophy", "policy"] = "policy"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    section: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    text: str


class Rule(Contract):
    rule_id: str
    policy_version: int = Field(ge=1)
    source_clause: SourceClause
    metric: str
    scope: str
    operator: Literal["gt", "gte", "prohibited", "qualitative"]
    threshold: Decimal | None
    action: Literal["WARNING", "HUMAN_REVIEW", "HARD_BLOCK", "CONTEXT_ONLY"]
    severity: Literal["WARNING", "HUMAN_REVIEW", "HARD_BLOCK", "CONTEXT_ONLY"]
    data_quality_requirement: str
    manual_review_required: bool
    auto_trade_allowed: Literal[False] = False
    subject: str | None = None

    @model_validator(mode="after")
    def semantics(self):
        if self.action != self.severity:
            raise ValueError("ACTION_SEVERITY_MISMATCH")
        if self.operator in {"gt", "gte"}:
            if self.threshold is None or not self.threshold.is_finite() or not 0 <= self.threshold <= 1:
                raise ValueError("INVALID_THRESHOLD")
            if self.action == "HARD_BLOCK":
                raise ValueError("REVIEW_THRESHOLD_IS_NOT_HARD_BLOCK")
        elif self.threshold is not None:
            raise ValueError("NO_INVENTED_QUALITATIVE_THRESHOLD")
        if self.manual_review_required != (self.action == "HUMAN_REVIEW"):
            raise ValueError("REVIEW_ACTION_MISMATCH")
        return self


class Allocation(Contract):
    denominator: Literal["QUALIFIED_INVESTABLE_ASSETS"]
    neutral: dict[str, Decimal]
    etf_internal: dict[str, Decimal]
    direct_equity: dict[str, Decimal]
    growth_total: Decimal
    stable_total: Decimal
    specified_equity_total: Decimal
    flex_china_max_exclusive: Decimal
    flex_alternatives_max: Decimal
    flex_joint_max: Decimal
    us_core_floor: Decimal
    temporary_cash_max: Decimal
    china_equity_etf_target: Decimal
    cash_within_stable: Literal[True] = True
    unused_active_budget: Literal["US_ETF_CONDITIONAL"] = "US_ETF_CONDITIONAL"
    source_clauses: dict[str, SourceClause]

    @model_validator(mode="after")
    def closes(self):
        all_values = [*self.neutral.values(), *self.etf_internal.values(), *self.direct_equity.values(),
                      self.growth_total, self.stable_total, self.specified_equity_total,
                      self.flex_china_max_exclusive, self.flex_alternatives_max, self.flex_joint_max,
                      self.us_core_floor, self.temporary_cash_max, self.china_equity_etf_target]
        if any(not n.is_finite() or not 0 <= n <= 1 for n in all_values):
            raise ValueError("INVALID_ALLOCATION_NUMBER")
        required = {"us_etf", "us_direct", "china", "gold", "btc", "ib01", "cbu3", "cash"}
        if set(self.neutral) != required or sum(self.neutral.values()) != 1:
            raise ValueError("NEUTRAL_BUDGET_NOT_CLOSED")
        if not self.etf_internal or sum(self.etf_internal.values()) != 1:
            raise ValueError("ETF_BUDGET_NOT_CLOSED")
        if sum(self.direct_equity.values()) != self.neutral["us_direct"]:
            raise ValueError("DIRECT_EQUITY_NOT_CLOSED")
        specified = sum(v for k, v in self.direct_equity.items() if k != "unallocated")
        if specified != self.specified_equity_total:
            raise ValueError("SPECIFIED_EQUITY_NOT_CLOSED")
        growth = sum(self.neutral[k] for k in ("us_etf", "us_direct", "china", "gold", "btc"))
        stable = sum(self.neutral[k] for k in ("ib01", "cbu3", "cash"))
        if growth != self.growth_total or stable != self.stable_total or growth + stable != 1:
            raise ValueError("MAJOR_BUDGET_NOT_CLOSED")
        if self.growth_total - self.flex_joint_max != self.us_core_floor:
            raise ValueError("FLEX_FLOOR_MISMATCH")
        c, a = self.neutral["china"], self.neutral["gold"] + self.neutral["btc"]
        if not (c < self.flex_china_max_exclusive and a <= self.flex_alternatives_max and c + a <= self.flex_joint_max):
            raise ValueError("FLEX_BUDGET_INVALID")
        if self.temporary_cash_max > self.stable_total or self.china_equity_etf_target != 0:
            raise ValueError("CASH_OR_CHINA_ETF_INVALID")
        return self


class Snapshot(Contract):
    schema_version: Literal["wp.policy.v1"] = "wp.policy.v1"
    parser_version: Literal["approved-markdown-v1"] = "approved-markdown-v1"
    policy_version: int = Field(ge=1)
    source_hashes: dict[Literal["philosophy", "policy"], str]
    content_approved_on: str
    allocation: Allocation
    rules: list[Rule]
    # Full clauses preserve non-numeric obligations, retirement and transition context.
    contextual_clauses: dict[str, SourceClause]

    @field_validator("content_approved_on")
    @classmethod
    def valid_date(cls, value):
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def provenance(self):
        if set(self.source_hashes) != {"philosophy", "policy"}:
            raise ValueError("EXACTLY_TWO_SOURCE_DOCUMENTS_REQUIRED")
        ids = [r.rule_id for r in self.rules]
        if len(set(ids)) != len(ids) or not ids:
            raise ValueError("MISSING_OR_DUPLICATE_RULE")
        for r in self.rules:
            if r.policy_version != self.policy_version:
                raise ValueError("MIXED_POLICY_VERSIONS")
        refs = [r.source_clause for r in self.rules] + list(self.allocation.source_clauses.values()) + list(self.contextual_clauses.values())
        if any(ref.source_sha256 != self.source_hashes[ref.document] for ref in refs):
            raise ValueError("SOURCE_MAPPING_MISMATCH")
        return self


class PolicyReference(Contract):
    """Optional future E1/analysis reference, without modifying their schemas."""
    bundle_id: str
    bundle_version: int
    bundle_sha256: str
    structured_sha256: str
    scope: str


class MetricEvidence(Contract):
    value: Decimal | None = None
    complete: bool = False
    fresh: bool = False
    identity_verified: bool = False
    denominator_qualified: bool = False
    as_of: str | None = None

    @field_validator("value")
    @classmethod
    def finite(cls, value):
        if value is not None and (not value.is_finite() or value < 0):
            raise ValueError("INVALID_METRIC")
        return value
