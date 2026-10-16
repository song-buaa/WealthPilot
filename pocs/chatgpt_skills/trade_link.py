"""Pure URL/preview preparation. No database, broker, network or LLM access."""
import re
from typing import Literal
from decimal import Decimal
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.utils.symbol import parse_symbol, normalize_symbol, _CURRENCY_TO_MARKET
from pocs.chatgpt_skills.contracts import ExplicitOrder
from backend.services.execution_batch.trusted_instruments import TRUSTED_INSTRUMENTS


class LinkOrder(ExplicitOrder):
    # An explicit instruction must supply the type, rather than silently default it.
    order_type: Literal["LIMIT", "CONDITIONAL_LIMIT"]
    time_in_force: Literal["GTC"] = "GTC"  # Existing single-order path uses GTC.

    @model_validator(mode="after")
    def normalize(self):
        if self.symbol.strip().upper() == "IB01":
            raise ValueError("IB01_REQUIRES_IBKR_BUDGET_LINK: broker=IBKR, target_amount, currency=USD")
        ticker, market = parse_symbol(self.symbol.upper())
        if ticker.upper() == "IB01":
            raise ValueError("IB01_REQUIRES_IBKR_BUDGET_LINK: broker=IBKR, target_amount, currency=USD")
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.-]*", ticker):
            raise ValueError("INVALID_SYMBOL")
        self.symbol = normalize_symbol(ticker, market)
        currency_market = _CURRENCY_TO_MARKET.get(self.currency)
        if not currency_market or normalize_symbol(ticker, currency_market) != self.symbol:
            raise ValueError("ORDER_CURRENCY_MARKET_MISMATCH")
        if self.order_type == "LIMIT" and self.trigger_price is not None:
            raise ValueError("LIMIT_DOES_NOT_USE_TRIGGER_PRICE")
        return self


class LinkPreviewRequest(LinkOrder):
    request_id: UUID


class IBKRLinkOrder(BaseModel):
    """IB01 uses the existing qualified-contract batch path, not currency→US guessing.

    The target is an approximate share notional; original broker fees/cash rules
    are reviewed separately in Web. No quantity or price is invented in MCP.
    """
    model_config = ConfigDict(extra="forbid")
    broker: Literal["IBKR"]
    symbol: str = Field(min_length=1, max_length=30)
    side: Literal["BUY"]
    order_type: Literal["LIMIT"]
    currency: Literal["USD"]
    target_amount: Decimal = Field(gt=0, allow_inf_nan=False)
    time_in_force: Literal["GTC"] = "GTC"

    @model_validator(mode="after")
    def normalize(self):
        parts = self.symbol.strip().upper().split(":")
        if (parts[0] != "IB01" or len(parts) > 2
                or (len(parts) == 2 and parts[1] not in {"UK", "LSE", "LSEETF"})):
            raise ValueError("IBKR_LINK_REQUIRES_VERIFIED_IB01_LSE_USD")
        self.symbol = "IB01:LSE"
        return self


class IBKRLinkPreviewRequest(IBKRLinkOrder):
    request_id: UUID


def ibkr_instrument() -> dict:
    trusted = TRUSTED_INSTRUMENTS["IB01"]
    return {"con_id": trusted.con_id, "symbol": trusted.symbol,
            "local_symbol": trusted.local_symbol, "isin": trusted.isin,
            "listing_exchange": trusted.exchange, "execution_exchange": "SMART",
            "primary_exchange": trusted.primary_exchange, "currency": trusted.currency}


class TradeLinkResult(BaseModel):
    schema_version: Literal["wealthpilot.trade-link.v1"] = "wealthpilot.trade-link.v1"
    url: str
    order: LinkOrder | IBKRLinkOrder
    execution_path: Literal["single_order", "ibkr_batch"] = "single_order"
    instrument: dict | None = None
    request_id: UUID
    requires_web_confirmation: Literal[True] = True
    draft_created: Literal[False] = False
    order_submitted: Literal[False] = False
    notice: str = "仅预填，未创建草稿或下单。请在 WealthPilot Web 核对券商模式、风险和完整订单后最终确认。"


def prepare_link(base_url: str, order: LinkOrder | IBKRLinkOrder) -> TradeLinkResult:
    base = urlsplit(base_url)
    if (base.scheme not in {"http", "https"} or not base.hostname or base.username
            or base.password or base.path not in {"", "/"} or base.query or base.fragment):
        raise ValueError("WEB_BASE_URL_NOT_CONFIGURED")
    if base.scheme == "http" and base.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("WEB_BASE_URL_REQUIRES_PRIVATE_HTTPS")
    request_id = uuid4()
    params = {"trade": "ibkr" if isinstance(order, IBKRLinkOrder) else "1", "request_id": str(request_id),
              **order.model_dump(mode="json", exclude_none=True)}
    return TradeLinkResult(url=base_url.rstrip("/")+"/#/action?"+urlencode(params),
                           order=order, request_id=request_id,
                           execution_path="ibkr_batch" if isinstance(order, IBKRLinkOrder) else "single_order",
                           instrument=ibkr_instrument() if isinstance(order, IBKRLinkOrder) else None)


def draft_payload(order: LinkOrder) -> dict:
    return {"symbol_strategies": [{**order.model_dump(mode="json"),
             "quantity_pct": None, "parent_intent_index": None,
             "value_sources": {"all": "用户明确指令；Web 人工核对"}}],
            "allocation_intents": [], "missing_fields": [],
            "risk_notes": ["来自 ChatGPT 的预填参数，请核对后继续；最终订单仍需单独确认。"]}
