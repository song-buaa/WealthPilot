"""Transport contracts, independent of ChatGPT plan and business runtime."""
from decimal import Decimal
from typing import Literal
from datetime import timezone
from pydantic import BaseModel, ConfigDict, Field, model_validator, AwareDatetime


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


class TradeHistoryQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account_ref: str | None = Field(default=None,pattern=r'^history_account_[0-9a-f]{24}$')
    symbol: str | None = Field(default=None,pattern=r'^[A-Za-z0-9:._-]{1,128}$')
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    limit: int = Field(default=50,ge=1,le=200,strict=True)
    offset: int = Field(default=0,ge=0,le=10000,strict=True)
    snapshot_id: str | None = Field(default=None,pattern=r'^asset-view:v1:[1-9][0-9]{0,17}$')

    @model_validator(mode='after')
    def interval(self):
        if self.start:self.start=self.start.astimezone(timezone.utc)
        if self.end:self.end=self.end.astimezone(timezone.utc)
        if self.start and self.end and not 0 < (self.end-self.start).total_seconds() <= 3660*86400:
            raise ValueError('HISTORY_COVERAGE_INVALID')
        return self


class TradeHistoryResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    uncertain_events: list[dict] = Field(default_factory=list)
    schema_version: Literal['wealthpilot.trade-history.v1']
    synthetic: bool
    queried_at: str
    snapshot_id: str | None
    status: Literal['ok','partial']
    issues: list[str]
    scope: str
    accounts: list[dict]
    items: list[dict]
    attribution: list[dict]
    coverage: list[dict]
    total: int
    next_offset: int | None


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
