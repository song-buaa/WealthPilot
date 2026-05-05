"""老虎证券 SDK Position → WealthPilot Position 转换器。"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from services.broker_sync.schema import Position


# 老虎 sec_type → WealthPilot asset_class
SEC_TYPE_TO_ASSET_CLASS = {
    "STK": "equity",
    "ETF": "etf",
    "OPT": "option",
    "FUT": "future",
    "FUND": "fund",
    "BOND": "bond",
    "WAR": "warrant",
}

# currency → market 推断
CURRENCY_TO_MARKET = {
    "USD": "US",
    "HKD": "HK",
    "CNH": "CN",
    "CNY": "CN",
}


class TigerAdapter:
    """老虎证券持仓数据适配器。"""

    BROKER_NAME = "tiger"
    COST_METHOD = "fifo"  # 老虎用 FIFO 成本

    def __init__(self, account_id: str):
        self.account_id = account_id

    def normalize_symbol(self, raw_symbol: str, currency: str) -> str:
        """券商代码 → WealthPilot 归一化代码。"""
        clean = raw_symbol.lstrip("'")
        market = CURRENCY_TO_MARKET.get(currency, "US")

        if market == "HK":
            clean = clean.zfill(5)
            return f"{clean}.HK"
        elif market == "US":
            return f"{clean}.US"
        elif market == "CN":
            return f"{clean}.CN"
        else:
            return clean

    def map_asset_class(self, sec_type: str) -> str:
        return SEC_TYPE_TO_ASSET_CLASS.get(sec_type, "equity")

    def _serialize_raw(self, sdk_position: Any) -> dict:
        """把 SDK Position 对象转成 dict,处理嵌套 contract 对象。"""
        result = {}
        for k, v in sdk_position.__dict__.items():
            if hasattr(v, "__dict__") and not isinstance(v, (str, int, float, bool, type(None))):
                # 嵌套对象(比如 contract)递归 dict 化
                result[k] = {
                    sk: str(sv) if isinstance(sv, Decimal) else sv
                    for sk, sv in v.__dict__.items()
                }
            elif isinstance(v, Decimal):
                result[k] = str(v)
            else:
                result[k] = v
        return result

    def _normalize_pnl_pct(self, raw_value: Any) -> Decimal:
        """
        把老虎的 unrealized_pnl_percent 转成统一的小数格式。

        WealthPilot 内部约定:0.305 表示 +30.5%。
        经验证,老虎 SDK 已使用小数格式(0.305 而非 30.5),无需转换。
        """
        if raw_value is None:
            return Decimal("0")
        return Decimal(str(raw_value))

    def to_position(self, sdk_position: Any, snapshot_time: datetime | None = None) -> Position:
        """老虎 SDK Position → WealthPilot Position。"""
        if snapshot_time is None:
            snapshot_time = datetime.now(timezone.utc)

        contract = sdk_position.contract
        currency = contract.currency
        raw_symbol = contract.symbol.lstrip("'") if hasattr(contract, "symbol") else ""

        # market: 优先用 SDK 返回的,SDK 没给时用 currency 推断
        sdk_market = getattr(contract, "market", None)
        market = sdk_market if sdk_market else CURRENCY_TO_MARKET.get(currency, "US")

        quantity = Decimal(str(sdk_position.quantity))
        avg_cost = Decimal(str(sdk_position.average_cost))

        return Position(
            broker=self.BROKER_NAME,
            account_id=self.account_id,
            symbol=self.normalize_symbol(raw_symbol, currency),
            raw_symbol=raw_symbol,
            name=getattr(contract, "name", None) or raw_symbol,
            name_en=None,
            asset_class=self.map_asset_class(contract.sec_type),
            market=market,
            quantity=quantity,
            available_quantity=None,
            avg_cost=avg_cost,
            cost_method=self.COST_METHOD,
            cost_basis=quantity * avg_cost,
            current_price=Decimal(str(sdk_position.market_price)),
            market_value=Decimal(str(sdk_position.market_value)),
            currency=currency,
            unrealized_pnl=Decimal(str(sdk_position.unrealized_pnl)),
            unrealized_pnl_pct=self._normalize_pnl_pct(
                sdk_position.unrealized_pnl_percent
            ),
            realized_pnl=Decimal(str(sdk_position.realized_pnl))
                if sdk_position.realized_pnl is not None else None,
            day_pnl=Decimal(str(sdk_position.today_pnl))
                if hasattr(sdk_position, "today_pnl") and sdk_position.today_pnl is not None
                else None,
            option_meta=None,
            snapshot_time=snapshot_time,
            sync_source="api",
            raw_data=self._serialize_raw(sdk_position),
        )

    def to_positions(self, sdk_positions: list, snapshot_time: datetime | None = None) -> list[Position]:
        """批量转换。"""
        if snapshot_time is None:
            snapshot_time = datetime.now(timezone.utc)
        return [self.to_position(p, snapshot_time) for p in sdk_positions]
