"""
雪盈证券持仓数据适配器。

雪盈 API 特点:
- 字段最少(9个),缺少 market_value / unrealized_pnl / currency / name
- 需要自行计算缺失字段
- symbol 是裸代码(LI),exchange=USEX 推断市场和币种
- SDK 数据通过私有属性 _data 访问
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from services.broker_sync.schema import Position


EXCHANGE_TO_MARKET = {
    "USEX": "US",
    "HKEX": "HK",
    "SEHK": "HK",
    "NYSE": "US",
    "NASDAQ": "US",
    "ARCA": "US",
    "BATS": "US",
}

EXCHANGE_TO_CURRENCY = {
    "USEX": "USD",
    "HKEX": "HKD",
    "SEHK": "HKD",
    "NYSE": "USD",
    "NASDAQ": "USD",
    "ARCA": "USD",
    "BATS": "USD",
}

SEC_TYPE_MAP = {
    "STK": "equity",
    "OPT": "option",
    "FUT": "future",
    "BOND": "bond",
    "FUND": "fund",
    "WAR": "warrant",
}


class SnowballAdapter:
    """雪盈证券持仓数据适配器。"""

    BROKER_NAME = "snowball"
    COST_METHOD = "weighted_average"

    def __init__(self, account_id: str):
        self.account_id = account_id

    def normalize_symbol(self, raw_symbol: str, exchange: str) -> str:
        """LI + USEX → LI.US, 700 + HKEX → 00700.HK"""
        market = EXCHANGE_TO_MARKET.get(exchange, "US")
        clean = raw_symbol.strip()
        if market == "HK":
            clean = clean.zfill(5)
        return f"{clean}.{market}"

    def _safe_decimal(self, value: Any, default: str = "0") -> Decimal:
        if value is None:
            return Decimal(default)
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal(default)

    def _serialize_item(self, item: Any) -> dict:
        if hasattr(item, "__dict__"):
            return {k: str(v) if isinstance(v, Decimal) else v for k, v in item.__dict__.items()}
        elif isinstance(item, dict):
            return item
        return {"raw": str(item)}

    def item_to_position(self, item: Any, snapshot_time: datetime | None = None) -> Position:
        """雪盈持仓对象 → WealthPilot Position。"""
        if snapshot_time is None:
            snapshot_time = datetime.now(timezone.utc)

        # 兼容 dict 和 SimpleNamespace
        get = item.get if isinstance(item, dict) else lambda k, d=None: getattr(item, k, d)

        raw_symbol = str(get("symbol", "")).strip()
        exchange = str(get("exchange", "USEX")).upper()
        market = EXCHANGE_TO_MARKET.get(exchange, "US")
        currency = EXCHANGE_TO_CURRENCY.get(exchange, "USD")
        symbol = self.normalize_symbol(raw_symbol, exchange)

        quantity = self._safe_decimal(get("position"))
        avg_cost = self._safe_decimal(get("average_price"))
        cost_basis = quantity * avg_cost

        current_price = self._safe_decimal(get("market_price"))

        # 缺失字段自行计算
        market_value = quantity * current_price
        unrealized_pnl = (current_price - avg_cost) * quantity
        unrealized_pnl_pct = (
            (current_price - avg_cost) / avg_cost
            if avg_cost != Decimal("0")
            else Decimal("0")
        )

        realized_pnl = self._safe_decimal(get("realized_pnl"))

        sec_type_raw = str(get("security_type", "STK")).upper()
        asset_class = SEC_TYPE_MAP.get(sec_type_raw, "equity")

        name = raw_symbol  # 雪盈不返回中文名

        return Position(
            broker=self.BROKER_NAME,
            account_id=self.account_id,
            symbol=symbol,
            raw_symbol=raw_symbol,
            name=name,
            asset_class=asset_class,
            market=market,
            quantity=quantity,
            available_quantity=None,
            avg_cost=avg_cost,
            cost_method=self.COST_METHOD,
            cost_basis=cost_basis,
            current_price=current_price,
            market_value=market_value,
            currency=currency,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            realized_pnl=realized_pnl,
            day_pnl=None,
            snapshot_time=snapshot_time,
            sync_source="api",
            raw_data=self._serialize_item(item),
        )

    def items_to_positions(self, items: list, snapshot_time: datetime | None = None) -> list[Position]:
        if snapshot_time is None:
            snapshot_time = datetime.now(timezone.utc)
        return [self.item_to_position(item, snapshot_time) for item in items]
