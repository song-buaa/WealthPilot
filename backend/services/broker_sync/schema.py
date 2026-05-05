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
    broker: Literal["tiger", "futu", "snowball"]
    account_id: str
    symbol: str  # 归一化代码: AAPL.US / 00068.HK
    raw_symbol: str  # 券商原始代码,去掉前导 ' 后的形态
    name: str  # 中文名优先
    name_en: str | None = None

    # ===== 资产分类 =====
    asset_class: Literal["equity", "etf", "option", "fund", "bond", "warrant", "future"]
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
