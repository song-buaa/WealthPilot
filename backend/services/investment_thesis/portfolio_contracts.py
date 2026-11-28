"""Combination decisions use E1 storage without becoming symbol theses."""
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, StrictBool, model_validator

from backend.services.allocation_scenarios import TransitionRequest
from .contracts import Record

Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Key = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
Note = Annotated[str, Field(min_length=1, max_length=2000)]


class ResearchLink(Record):
    thesis_id: Key
    revision: int = Field(gt=0, strict=True)
    review_id: Key | None = None
    sha256: Hash


class DecisionItem(Record):
    item_id: Key
    kind: Literal["PLANNED_TRANSITION_HOLD", "CONDITIONAL_HOLD", "OPERATIONAL_BLOCKED",
                  "FORMAL_POLICY_EXCEPTION", "RISK_EVENT", "REVOKE_DECISION"]
    reason: Note
    review_due_at: AwareDatetime
    expires_at: AwareDatetime
    revoke_when: Note
    risk_key: Hash | None = None
    stage_index: int | None = Field(default=None, ge=1, le=8, strict=True)
    alternative_path: Literal["A", "B"] | None = None
    event_action: Literal["OPEN", "MONITOR", "RESOLVE", "EXPIRE", "REOPEN", "BLOCK"] | None = None
    research: list[ResearchLink] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def meaning(self):
        if bool(self.event_action) != (self.kind == "RISK_EVENT") or self.event_action and not self.risk_key:
            raise ValueError("E1_PORTFOLIO_EVENT_ACTION_INVALID")
        if self.kind == "PLANNED_TRANSITION_HOLD" and self.stage_index is None:
            raise ValueError("E1_PORTFOLIO_STAGE_REQUIRED")
        if self.kind == "CONDITIONAL_HOLD" and not self.research:
            raise ValueError("E1_PORTFOLIO_RESEARCH_LINK_REQUIRED")
        if self.kind == "OPERATIONAL_BLOCKED" and self.alternative_path is None:
            raise ValueError("E1_PORTFOLIO_ALTERNATIVE_REQUIRED")
        if self.kind == "FORMAL_POLICY_EXCEPTION" and not self.risk_key:
            raise ValueError("E1_PORTFOLIO_EXCEPTION_SCOPE_REQUIRED")
        if self.review_due_at > self.expires_at:
            raise ValueError("E1_PORTFOLIO_REVIEW_AFTER_EXPIRY")
        return self


class PortfolioSourceRequest(Record):
    snapshot_id: Annotated[str, Field(pattern=r"^asset-view:v1:[1-9][0-9]{0,17}$")]
    snapshot_sha256: Hash
    policy_bundle_sha256: Hash


class PortfolioPreviewRequest(PortfolioSourceRequest):
    bundle_id: Key
    request_id: Key
    expected_revision: int = Field(ge=0, strict=True)
    scenario: TransitionRequest | None = None
    selected_path: Literal["A", "B"] | None = None
    items: list[DecisionItem] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def bindings(self):
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("E1_PORTFOLIO_DUPLICATE_ITEM")
        risks = [item.risk_key for item in self.items if item.risk_key and item.kind != "FORMAL_POLICY_EXCEPTION"]
        if len(risks) != len(set(risks)):
            raise ValueError("E1_PORTFOLIO_DUPLICATE_RISK_ACTION")
        if any(item.kind == "REVOKE_DECISION" for item in self.items):
            if (len(self.items) != 1 or not self.expected_revision or self.items[0].risk_key or self.scenario
                    or self.items[0].stage_index is not None):
                raise ValueError("E1_PORTFOLIO_REVOCATION_SCOPE_INVALID")
        if bool(self.scenario) != bool(self.selected_path):
            raise ValueError("E1_PORTFOLIO_PATH_REQUIRED")
        if self.scenario and (self.selected_path not in self.scenario.paths or
                any(getattr(self.scenario, key) != getattr(self, key)
                    for key in ("snapshot_id", "snapshot_sha256", "policy_bundle_sha256"))):
            raise ValueError("E1_PORTFOLIO_SCENARIO_SCOPE_MISMATCH")
        for item in self.items:
            if item.stage_index is not None and (not self.scenario or
                    item.stage_index > len(self.scenario.paths[self.selected_path])):
                raise ValueError("E1_PORTFOLIO_STAGE_NOT_FOUND")
            if item.alternative_path and (not self.scenario or item.alternative_path not in self.scenario.paths
                                          or item.alternative_path == self.selected_path):
                raise ValueError("E1_PORTFOLIO_ALTERNATIVE_NOT_FOUND")
        return self


class PortfolioConfirmation(Record):
    request_id: Key
    preview_token: str = Field(min_length=20, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    preview_fingerprint: Hash
    selected_item_ids: list[Key] = Field(max_length=24)
    confirmed: StrictBool = False
    confirmation: Literal["保存这次组合决定", "取消"] | None = None

    @model_validator(mode="after")
    def selection(self):
        if len(set(self.selected_item_ids)) != len(self.selected_item_ids):
            raise ValueError("E1_PORTFOLIO_DUPLICATE_SELECTION")
        return self


class PortfolioQuery(Record):
    bundle_id: Key | None = None
    risk_key: Hash | None = None
    revision: int | None = Field(default=None, ge=1, strict=True)
    limit: int = Field(default=20, ge=1, le=50, strict=True)

    @model_validator(mode="after")
    def revision_requires_bundle(self):
        if self.risk_key is not None and (self.bundle_id or self.revision is not None):
            raise ValueError("E1_PORTFOLIO_QUERY_INVALID")
        if self.revision is not None and not self.bundle_id:
            raise ValueError("E1_PORTFOLIO_BUNDLE_REQUIRED")
        return self
