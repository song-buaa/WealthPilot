"""国金证券 QMT 推送数据 → WealthPilot Position 转换器。"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from services.broker_sync.schema import Position
from backend.services.instruments.classification import (
    AssetClassificationEvidence,
    broker_position_classification_fields,
    classify_instrument,
)

CURRENCY_TO_MARKET = {
    "CNY": "SH",
    "HKD": "HK",
}


class GuojinAdapter:
    """国金证券持仓数据适配器。"""

    BROKER_NAME = "guojin"
    COST_METHOD = "weighted_average"

    def __init__(self, account_id: str):
        self.account_id = account_id

    def to_position(self, raw: dict, snapshot_time: datetime | None = None) -> Position:
        """QMT 推送的单条 position dict → WealthPilot Position。"""
        if snapshot_time is None:
            snapshot_time = datetime.now(timezone.utc)

        symbol = raw["symbol"]            # "510310:SH" — 已是标准格式
        raw_symbol = raw.get("raw_symbol", symbol)
        currency = raw.get("currency", "CNY")

        # market: 从 symbol 的 :MARKET 部分提取,fallback 用 currency 推断
        market = symbol.split(":")[-1] if ":" in symbol else CURRENCY_TO_MARKET.get(currency, "SH")

        quantity = Decimal(str(raw["quantity"]))
        cost_price = Decimal(str(raw.get("cost_price", 0)))
        last_price = Decimal(str(raw.get("last_price", 0)))
        market_value = Decimal(str(raw.get("market_value", 0)))
        cost_basis = quantity * cost_price

        unrealized_pnl = market_value - cost_basis
        unrealized_pnl_pct = (
            unrealized_pnl / cost_basis if cost_basis else Decimal("0")
        )

        # asset_class: A 股 ETF 代码 51xxxx/15xxxx/16xxxx 识别为 etf,其余 equity
        ticker = symbol.split(":")[0] if ":" in symbol else symbol
        vehicle_hint = "ETF" if ticker[:2] in ("51", "15", "16") else "COMMON_STOCK"
        evidence = AssetClassificationEvidence(
            broker=self.BROKER_NAME,
            broker_security_type=str(raw.get("security_type") or "QMT_POSITION"),
            stock_type=vehicle_hint,
            vehicle_type_hint=vehicle_hint,
            explicit_economic_asset_class=raw.get("economic_asset_class"),
            explicit_source="BROKER_DETERMINISTIC_METADATA" if raw.get("economic_asset_class") else None,
            long_name=raw.get("name", ticker),
            category=raw.get("category"),
            subcategory=raw.get("subcategory"),
            industry=raw.get("industry"),
            currency=currency,
        )
        classification = classify_instrument(evidence)

        return Position(
            broker=self.BROKER_NAME,
            account_id=self.account_id,
            symbol=symbol,
            raw_symbol=raw_symbol,
            name=raw.get("name", ticker),
            name_en=None,
            **broker_position_classification_fields(
                classification,
                evidence=evidence,
            ),
            market=market,
            quantity=quantity,
            available_quantity=Decimal(str(raw["available_quantity"])) if raw.get("available_quantity") is not None else None,
            avg_cost=cost_price,
            cost_method=self.COST_METHOD,
            cost_basis=cost_basis,
            current_price=last_price,
            market_value=market_value,
            currency=currency,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            realized_pnl=None,
            day_pnl=None,
            option_meta=None,
            snapshot_time=snapshot_time,
            sync_source="api",
            raw_data=raw,
        )

    def to_positions(self, raw_list: list[dict], snapshot_time: datetime | None = None) -> list[Position]:
        """批量转换。"""
        if snapshot_time is None:
            snapshot_time = datetime.now(timezone.utc)
        return [self.to_position(r, snapshot_time) for r in raw_list]

    def from_native_export(self, row: dict, snapshot_time: datetime) -> Position:
        """CNY broker valuation/cost/P&L; HKD quotes remain separate evidence."""
        from backend.utils.symbol import normalize_symbol
        security = normalize_symbol(row["code"], row["market"])
        ticker, market = security.split(":")
        symbol = (f"{ticker}.{row['channel'].upper()}:{market}"
                  if row["channel"] in {"hgt", "sgt"} else security)
        qty = row["quantity"]
        # The business model has one price currency. Never put an HKD quote
        # alongside a CNY cost/value. These unit values aren't live quotes.
        unit_cost = row["cost_basis"]/qty if qty else Decimal(0)
        unit_value = row["market_value"]/qty if qty else Decimal(0)
        original = self.to_position(dict(symbol=symbol, raw_symbol=row["code"], name=row["name"],
            quantity=qty, available_quantity=row["available_quantity"], cost_price=unit_cost,
            last_price=unit_value, market_value=row["market_value"], currency="CNY",
            security_type="QNX_NATIVE_POSITION"), snapshot_time)
        return original.model_copy(update=dict(cost_basis=row["cost_basis"],
            unrealized_pnl=row["unrealized_pnl"], unrealized_pnl_pct=row["unrealized_pnl_pct"],
            raw_data={**row, "source_channel":"guojin_qnx_native", "security_symbol":security,
                      "price_basis":"BROKER_CNY_VALUE_PER_UNIT"}))
