"""Transport contracts, independent of ChatGPT plan and business runtime."""
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExplicitOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=30)
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, strict=True)
    order_type: Literal["LIMIT", "CONDITIONAL_LIMIT"] = "LIMIT"
    limit_price: Decimal = Field(gt=0, allow_inf_nan=False)
    trigger_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def conditional_price(self):
        if self.order_type == "CONDITIONAL_LIMIT" and self.trigger_price is None:
            raise ValueError("TRIGGER_PRICE_REQUIRED")
        return self


class Details(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["wealthpilot.details.v1"] = "wealthpilot.details.v1"
    synthetic: bool
    queried_at: str
    status: Literal["ok", "partial", "unavailable"]
    issues: list[str]
    amount_unit: Literal["major"] = "major"
    scope: str
    items: list[dict]
    accounts: list[dict] = Field(default_factory=list)
    portfolio_summary: dict | None = None
    sources: dict[str, str] = Field(default_factory=dict)
    snapshot_id: str | None = None
    data_quality: dict | None = None
    issuer_exposure: dict | None = None


class PrepareResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    analysis_request_id: str
    state: Literal["preparing", "ready", "failed", "expired"]
    snapshot_id: str | None
    data_quality: dict
    web_view_url: str | None = None


class TradeSyncQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str | None = Field(default=None,pattern=r"^[A-Za-z0-9_-]{1,64}$")
    batch_id: str | None = Field(default=None,pattern=r"^[A-Za-z0-9_-]{1,64}$")
    symbol: str | None = Field(default=None,pattern=r"^[A-Za-z0-9:._-]{1,50}$")
    limit: int = Field(default=10,ge=1,le=20,strict=True)


class TradeSyncResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["wealthpilot.trade-sync.v1"]
    synthetic: bool
    queried_at: str
    status: Literal["ok","partial"]
    scope: str
    items: list[dict]


class TradePreview(ExplicitOrder):
    broker: str
    account_scope: str
    time_in_force: Literal["GTC"]
    price_basis: str
    estimated_notional: Decimal
    fees: str
    execution_path: str


class RiskPreview(BaseModel):
    passed: bool
    requires_confirmation: bool
    warnings: list[dict]
    confirmation_text_required: str | None
    discipline_available: bool
    fx_rate_to_cny: str
    fx_basis: str


class OrderState(BaseModel):
    order_id: str
    strategy_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: int
    filled_quantity: int
    status: str
    broker: str
    limit_price: str | None
    avg_filled_price: str | None
    updated_at: str


class TradeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["wealthpilot.trade.v1"] = "wealthpilot.trade.v1"
    synthetic: bool
    broker: str
    draft_id: str
    draft_status: str
    preview_hash: str
    preview: TradePreview
    risk: RiskPreview
    orders: list[OrderState]
    queried_at: str
