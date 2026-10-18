"""
WealthPilot 多券商持仓统一数据模型。

设计原则:
- 金额一律 Decimal,不用 float
- 保留原始数据用于审计/debug
- broker-agnostic,老虎/富途/雪盈共用
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ConfigDict


class OptionMeta(BaseModel):
    """期权专属元数据,只在 asset_class == 'option' 时填充。"""
    model_config = ConfigDict(frozen=True)

    underlying_symbol: str
    option_type: Literal["call", "put"]
    strike: Decimal
    expiry: date
    contract_size: int = 100  # 美股期权默认 100


class Position(BaseModel):
    """统一持仓数据模型,跨券商通用。"""
    model_config = ConfigDict(frozen=True)

    # ===== 标识 =====
    broker: Literal["tiger", "futu", "snowball", "guojin"]
    account_id: str
    symbol: str  # 归一化代码: AAPL:US / 0068:HK (TICKER:MARKET 格式)
    raw_symbol: str  # 券商原始代码,去掉前导 ' 后的形态
    name: str  # 中文名优先
    name_en: str | None = None

    # ===== 资产分类 =====
    # asset_class 是历史 vehicle alias；经济分类只能读取 economic_asset_class。
    asset_class: Literal[
        "equity", "etf", "option", "fund", "bond", "warrant", "future", "cash", "unknown"
    ] = "unknown"
    broker_security_type: str = ""
    vehicle_type: Literal[
        "COMMON_STOCK", "ADR", "ETF", "BOND", "FUND", "REIT", "ETN", "CASH",
        "OPTION", "FUTURE", "WARRANT", "OTHER", "UNKNOWN",
    ] = "UNKNOWN"
    economic_asset_class: Literal[
        "EQUITY", "FIXED_INCOME", "CASH", "COMMODITY", "MULTI_ASSET",
        "ALTERNATIVE", "DERIVATIVE", "UNKNOWN",
    ] = "UNKNOWN"
    economic_asset_subclass: str | None = None
    classification_source: str = "UNRESOLVED"
    classification_confidence: Literal["HIGH", "MEDIUM", "LOW"] = "LOW"
    classification_verification_status: str = "UNVERIFIED"
    classification_version: str = "canonical-v1"
    classification_evidence: dict[str, Any] = Field(default_factory=dict)
    market: str = Field(..., description="市场代码: US/HK/CN/SG 等,跟随券商 SDK 返回")

    # ===== 数量与成本 =====
    quantity: Decimal = Field(..., description="持仓数量")
    available_quantity: Decimal | None = Field(None, description="可用数量,部分券商无此字段")
    avg_cost: Decimal = Field(..., description="平均成本价")
    cost_method: Literal["fifo", "weighted_average"] = Field(
        ..., description="成本计算方式: 老虎 fifo, 富途 weighted_average"
    )
    cost_basis: Decimal = Field(..., description="持仓总成本 = quantity * avg_cost")

    # ===== 行情 =====
    current_price: Decimal
    market_value: Decimal
    currency: Literal["USD", "HKD", "CNH", "CNY", "JPY", "SGD"]

    # ===== 盈亏(原币种,不做汇率换算)=====
    unrealized_pnl: Decimal = Field(..., description="未实现盈亏(原币种)")
    unrealized_pnl_pct: Decimal = Field(
        ..., description="浮盈比例,用小数表示(0.305 表示 +30.5%)"
    )
    realized_pnl: Decimal | None = Field(None, description="已实现盈亏,部分券商无")
    day_pnl: Decimal | None = Field(None, description="今日盈亏,部分券商无")

    # ===== 期权扩展 =====
    option_meta: OptionMeta | None = None

    # ===== 元数据 =====
    snapshot_time: datetime = Field(..., description="同步时点 (UTC)")
    sync_source: Literal["api", "csv_upload", "statement"]
    raw_data: dict[str, Any] = Field(
        default_factory=dict,
        description="券商 SDK 返回的完整原始数据,JSON 序列化后存库"
    )


def is_cash_balance(position):
    """Screen cash collisions; never assign classification or infer an owner."""
    from collections.abc import Mapping
    get = position.get if isinstance(position, Mapping) else lambda key: getattr(position, key, None)
    vehicle = str(get("vehicle_type") or "").upper()
    if vehicle == "CASH" or str(get("broker_security_type") or "").upper() == "CASH":
        return True
    codes = [str(get(key) or "").upper() for key in ("symbol", "ticker", "raw_symbol")]
    if any(code.startswith("CASH-") for code in codes):
        return True
    currency = str(get("currency") or "").upper()
    currency_only = all(not code or currency and code in {currency, currency+":FX"} for code in codes)
    return (currency_only and vehicle in {"", "UNKNOWN"}
            and (get("economic_asset_class") == "CASH" or get("asset_class") in {"货币", "现金", "cash"}))


def cash_position(broker, account_id, currency, amount, snapshot_time):
    """One native currency fact, including confirmed zero/negative balances."""
    from backend.utils.symbol import normalize_symbol
    from backend.services.instruments.classification import (
        AssetClassificationEvidence, classify_instrument, broker_position_classification_fields)
    currency = str(currency).upper()
    amount = Decimal(str(amount))
    if not amount.is_finite() or currency == "BASE":
        raise ValueError("INVALID_NATIVE_CASH")
    market = {"USD":"US","HKD":"HK","CNY":"CN","CNH":"CN"}.get(currency,"FX")
    raw_symbol = "CASH-" + currency
    evidence = AssetClassificationEvidence(broker=broker, broker_security_type="CASH",
        vehicle_type_hint="CASH", explicit_economic_asset_class="CASH",
        explicit_source="BROKER_DETERMINISTIC_METADATA", currency=currency)
    return Position(broker=broker, account_id=account_id, symbol=normalize_symbol(raw_symbol, market),
        raw_symbol=raw_symbol, name=f"账户现金（{currency}）",
        **broker_position_classification_fields(classify_instrument(evidence), evidence=evidence),
        market=market, quantity=amount, avg_cost=Decimal(1), cost_method="weighted_average",
        cost_basis=amount, current_price=Decimal(1), market_value=amount, currency=currency,
        unrealized_pnl=Decimal(0), unrealized_pnl_pct=Decimal(0), snapshot_time=snapshot_time,
        sync_source="api", raw_data={"tag":"CashBalance","currency":currency,
            "source_channel":"ibkr" if broker == "snowball" else broker})
