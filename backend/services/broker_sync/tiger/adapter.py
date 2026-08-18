"""老虎证券 SDK Position → WealthPilot Position 转换器。"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from services.broker_sync.schema import Position
from backend.services.instruments.classification import (
    AssetClassificationEvidence,
    broker_position_classification_fields,
    classify_instrument,
)


# 老虎 sec_type → canonical vehicle type。经济分类仍由 central classifier 决定。
SEC_TYPE_TO_VEHICLE = {
    "STK": "COMMON_STOCK",
    "ETF": "ETF",
    "OPT": "OPTION",
    "FUT": "FUTURE",
    "FUND": "FUND",
    "BOND": "BOND",
    "WAR": "WARRANT",
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
        """券商代码 → WealthPilot 归一化代码 (TICKER:MARKET)。"""
        from utils.symbol import normalize_symbol as _normalize
        clean = raw_symbol.lstrip("'")
        market = CURRENCY_TO_MARKET.get(currency, "US")
        return _normalize(clean, market)

    def map_asset_class(self, sec_type: str) -> str:
        return {
            "COMMON_STOCK": "equity",
            "ETF": "etf",
            "OPTION": "option",
            "FUTURE": "future",
            "FUND": "fund",
            "BOND": "bond",
            "WARRANT": "warrant",
        }.get(SEC_TYPE_TO_VEHICLE.get(sec_type, "UNKNOWN"), "unknown")

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

        # 老虎 SDK quantity 是整数编码(position_scale 表示小数位数)
        # position_qty 是真实份额(小数),基金等小数份额场景必须用 position_qty
        raw_qty = getattr(sdk_position, "position_qty", None)
        if raw_qty is None or raw_qty == 0:
            raw_qty = sdk_position.quantity
        quantity = Decimal(str(raw_qty))
        avg_cost = Decimal(str(sdk_position.average_cost))
        sec_type = str(getattr(contract, "sec_type", "") or "").upper()
        vehicle_hint = SEC_TYPE_TO_VEHICLE.get(sec_type)
        # Tiger exposes ETF as a distinct native sec_type, so STK is valid
        # broker evidence for COMMON_STOCK in this adapter only.
        stock_type = "COMMON" if sec_type == "STK" else None
        evidence = AssetClassificationEvidence(
            broker=self.BROKER_NAME,
            broker_security_type=sec_type,
            stock_type=stock_type,
            vehicle_type_hint=vehicle_hint,
            long_name=getattr(contract, "name", None) or raw_symbol,
            currency=currency,
        )
        classification = classify_instrument(evidence)

        return Position(
            broker=self.BROKER_NAME,
            account_id=self.account_id,
            symbol=self.normalize_symbol(raw_symbol, currency),
            raw_symbol=raw_symbol,
            name=getattr(contract, "name", None) or raw_symbol,
            name_en=None,
            **broker_position_classification_fields(
                classification,
                evidence=evidence,
            ),
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
