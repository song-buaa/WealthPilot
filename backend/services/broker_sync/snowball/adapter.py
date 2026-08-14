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

IBKR_SEC_TYPE_MAP = {
    "STK": "equity",
    "ETF": "etf",
    "BOND": "bond",
    "CASH": "cash",
    "FUND": "fund",
    "OPT": "option",
    "FOP": "option",
    "FUT": "future",
    "WAR": "warrant",
}

_FIXED_INCOME_NAME_HINTS = ("BOND", "TREAS", "FIXED INCOME", "CREDIT")


class SnowballAdapter:
    """雪盈证券持仓数据适配器。"""

    BROKER_NAME = "snowball"
    COST_METHOD = "weighted_average"

    def __init__(self, account_id: str):
        self.account_id = account_id

    def normalize_symbol(self, raw_symbol: str, exchange: str) -> str:
        """LI + USEX → LI:US, 700 + HKEX → 0700:HK"""
        from utils.symbol import normalize_symbol as _normalize
        market = EXCHANGE_TO_MARKET.get(exchange, "US")
        return _normalize(raw_symbol.strip(), market)

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


class IBKRPortfolioAdapter:
    """把现有 IBKRBrokerAdapter 的只读值对象映射到持仓同步契约。

    内部 broker 标识继续使用 ``snowball``，遵守 v3.10 已确定的同账户持仓
    通道契约；原始 channel 则保留为 ``ibkr`` 便于审计。
    """

    BROKER_NAME = "snowball"
    COST_METHOD = "weighted_average"

    def __init__(self, account_id: str):
        self.account_id = account_id

    @staticmethod
    def _market(raw: dict) -> str:
        exchange = str(raw.get("primary_exchange") or raw.get("exchange") or "").upper()
        currency = str(raw.get("currency") or "USD").upper()
        if exchange in {"SEHK", "HKFE"} or currency == "HKD":
            return "HK"
        if currency in {"CNY", "CNH"}:
            return "CN"
        return "US"

    @staticmethod
    def map_asset_class(raw: dict) -> str:
        """IBKR Contract 元数据 → broker sync asset_class，不依赖 symbol。"""
        sec_type = str(raw.get("sec_type") or "").upper()
        mapped = IBKR_SEC_TYPE_MAP.get(sec_type, "equity")
        if mapped != "equity":
            return mapped

        long_name = str(raw.get("long_name") or raw.get("name") or "").upper()
        if any(hint in long_name for hint in _FIXED_INCOME_NAME_HINTS):
            return "bond"
        exchange = str(raw.get("primary_exchange") or raw.get("exchange") or "").upper()
        if "ETF" in exchange:
            return "etf"
        return "equity"

    def security_to_position(
        self,
        raw: dict,
        snapshot_time: datetime,
    ) -> Position:
        from utils.symbol import normalize_symbol

        raw_symbol = str(raw.get("local_symbol") or raw.get("symbol") or "").strip()
        market = self._market(raw)
        symbol = normalize_symbol(raw_symbol, market)
        quantity = Decimal(str(raw.get("quantity") or 0))
        avg_cost = Decimal(str(raw.get("average_cost") or 0))
        current_price = Decimal(str(raw.get("current_price") or 0))
        market_value = Decimal(str(raw.get("market_value") or 0))
        unrealized_pnl = Decimal(str(raw.get("unrealized_pnl") or 0))
        cost_basis = quantity * avg_cost
        pnl_pct = unrealized_pnl / cost_basis if cost_basis else Decimal("0")
        name = str(raw.get("long_name") or raw_symbol)

        return Position(
            broker=self.BROKER_NAME,
            account_id=self.account_id,
            symbol=symbol,
            raw_symbol=raw_symbol,
            name=name,
            asset_class=self.map_asset_class(raw),
            market=market,
            quantity=quantity,
            available_quantity=None,
            avg_cost=avg_cost,
            cost_method=self.COST_METHOD,
            cost_basis=cost_basis,
            current_price=current_price,
            market_value=market_value,
            currency=str(raw.get("currency") or "USD").upper(),
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=pnl_pct,
            realized_pnl=Decimal(str(raw.get("realized_pnl") or 0)),
            day_pnl=None,
            snapshot_time=snapshot_time,
            sync_source="api",
            raw_data={**raw, "source_channel": "ibkr"},
        )

    def cash_to_position(self, raw: dict, snapshot_time: datetime) -> Position:
        from utils.symbol import normalize_symbol

        currency = str(raw["currency"]).upper()
        amount = Decimal(str(raw["amount"]))
        market = self._market({"currency": currency})
        raw_symbol = f"CASH-{currency}"
        return Position(
            broker=self.BROKER_NAME,
            account_id=self.account_id,
            symbol=normalize_symbol(raw_symbol, market),
            raw_symbol=raw_symbol,
            name=f"盈透账户现金（{currency}）",
            asset_class="cash",
            market=market,
            quantity=amount,
            available_quantity=None,
            avg_cost=Decimal("1"),
            cost_method=self.COST_METHOD,
            cost_basis=amount,
            current_price=Decimal("1"),
            market_value=amount,
            currency=currency,
            unrealized_pnl=Decimal("0"),
            unrealized_pnl_pct=Decimal("0"),
            realized_pnl=Decimal("0"),
            day_pnl=None,
            snapshot_time=snapshot_time,
            sync_source="api",
            raw_data={"tag": "CashBalance", "currency": currency, "source_channel": "ibkr"},
        )

    def to_positions(
        self,
        securities: list[dict],
        cash_balances: list[dict],
        snapshot_time: datetime | None = None,
    ) -> list[Position]:
        snapshot_time = snapshot_time or datetime.now(timezone.utc)
        positions = [self.security_to_position(item, snapshot_time) for item in securities]
        positions.extend(
            self.cash_to_position(item, snapshot_time)
            for item in cash_balances
            if str(item.get("currency") or "").upper() != "BASE"
            and Decimal(str(item.get("amount") or 0)) != 0
        )
        return positions
