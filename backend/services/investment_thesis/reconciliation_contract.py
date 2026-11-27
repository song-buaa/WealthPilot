"""Explicit existing-snapshot selection. Client inputs cite records, not facts."""
from typing import Annotated
from pydantic import Field, model_validator

from .contracts import Record
from .portfolio_contracts import Key, Hash

OrderRef = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]


class ReconciliationRequest(Record):
    bundle_id: Key | None = None
    revision: int | None = Field(default=None, ge=1, strict=True)
    snapshot_id: Annotated[str, Field(pattern=r"^asset-view:v1:[1-9][0-9]{0,17}$")]
    snapshot_sha256: Hash
    order_links: dict[Key, OrderRef] = Field(default_factory=dict, max_length=24)

    @model_validator(mode="after")
    def references(self):
        if self.revision is not None and not self.bundle_id:
            raise ValueError("E1_PORTFOLIO_BUNDLE_REQUIRED")
        if len(set(self.order_links.values())) != len(self.order_links):
            raise ValueError("E1_PORTFOLIO_ORDER_REFERENCE_REUSED")
        return self
