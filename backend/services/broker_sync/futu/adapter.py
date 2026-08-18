"""
富途证券持仓数据适配器。

富途 SDK 接口特点:
- position_list_query 返回 (ret_code, pandas.DataFrame)
- code 字段含市场前缀: "US.QQQ" / "HK.00068"
- pl_ratio 是百分数格式(8.76 = 8.76%),需要 /100 转成 0.0876
- 成本方式: weighted_average (摊薄成本)
- DataFrame 无 stock_type 时只使用可见名称 evidence；不足则 fail closed 为 UNKNOWN
"""
import math
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pandas as pd

from services.broker_sync.schema import Position
from backend.services.instruments.classification import (
    AssetClassificationEvidence,
    broker_position_classification_fields,
    classify_instrument,
)


class FutuAdapter:
    """富途证券持仓数据适配器。"""

    BROKER_NAME = "futu"
    COST_METHOD = "weighted_average"

    def __init__(self, account_id: str):
        self.account_id = account_id

    def normalize_symbol(self, futu_code: str) -> str:
        """
        富途代码 → WealthPilot 归一化代码 (TICKER:MARKET)。
        "US.QQQ" → "QQQ:US", "HK.00068" → "0068:HK"
        """
        from utils.symbol import normalize_symbol as _normalize
        if "." not in futu_code:
            return futu_code

        market_prefix, raw_code = futu_code.split(".", 1)
        return _normalize(raw_code, market_prefix)

    def get_raw_symbol(self, futu_code: str) -> str:
        """从富途代码提取裸 symbol。"""
        if "." in futu_code:
            return futu_code.split(".", 1)[1]
        return futu_code

    def get_market(self, futu_code: str) -> str:
        """从富途代码提取市场标识。"""
        if "." in futu_code:
            return futu_code.split(".", 1)[0].upper()
        return "US"

    def _safe_decimal(self, value: Any, default: str = "0") -> Decimal:
        """安全转换为 Decimal,处理 NaN 和 None。"""
        if value is None:
            return Decimal(default)
        try:
            if isinstance(value, float) and math.isnan(value):
                return Decimal(default)
            return Decimal(str(value))
        except Exception:
            return Decimal(default)

    def _serialize_row(self, row: pd.Series) -> dict:
        """把 DataFrame 行转成可 JSON 序列化的 dict。"""
        result = {}
        for k, v in row.to_dict().items():
            if isinstance(v, float) and math.isnan(v):
                result[k] = None
            elif hasattr(v, "item"):
                result[k] = v.item()
            else:
                result[k] = v
        return result

    def row_to_position(self, row: pd.Series, snapshot_time: datetime | None = None) -> Position:
        """DataFrame 一行 → WealthPilot Position。"""
        if snapshot_time is None:
            snapshot_time = datetime.now(timezone.utc)

        futu_code = row["code"]
        raw_symbol = self.get_raw_symbol(futu_code)
        market = self.get_market(futu_code)
        symbol = self.normalize_symbol(futu_code)

        name = str(row.get("stock_name", raw_symbol) or raw_symbol)

        quantity = self._safe_decimal(row.get("qty"))
        available_quantity = self._safe_decimal(row.get("can_sell_qty"))

        # 成本:优先 cost_price,其次 average_cost
        avg_cost = self._safe_decimal(row.get("cost_price") or row.get("average_cost"))
        cost_basis = quantity * avg_cost

        current_price = self._safe_decimal(row.get("nominal_price"))
        market_value = self._safe_decimal(row.get("market_val"))
        currency = str(row.get("currency", "USD"))

        unrealized_pnl = self._safe_decimal(row.get("unrealized_pl"))

        # 关键:富途 pl_ratio 是百分数(8.76 = 8.76%),转成小数 0.0876
        pl_ratio_raw = row.get("pl_ratio")
        if pl_ratio_raw is not None and not (isinstance(pl_ratio_raw, float) and math.isnan(pl_ratio_raw)):
            unrealized_pnl_pct = self._safe_decimal(pl_ratio_raw) / Decimal("100")
        else:
            unrealized_pnl_pct = Decimal("0")

        realized_pnl = self._safe_decimal(row.get("realized_pl")) if row.get("realized_pl") is not None else None
        day_pnl = self._safe_decimal(row.get("today_pl_val")) if row.get("today_pl_val") is not None else None

        stock_type_raw = row.get("stock_type")
        stock_type = (
            str(stock_type_raw).strip().upper()
            if stock_type_raw is not None
            and not (isinstance(stock_type_raw, float) and math.isnan(stock_type_raw))
            else ""
        )
        vehicle_hint = "ETF" if "ETF" in name.upper() else stock_type or None
        evidence = AssetClassificationEvidence(
            broker=self.BROKER_NAME,
            broker_security_type=stock_type or "UNKNOWN",
            stock_type=stock_type or None,
            vehicle_type_hint=vehicle_hint,
            long_name=name,
            currency=currency,
        )
        classification = classify_instrument(evidence)

        return Position(
            broker=self.BROKER_NAME,
            account_id=self.account_id,
            symbol=symbol,
            raw_symbol=raw_symbol,
            name=name,
            **broker_position_classification_fields(
                classification,
                evidence=evidence,
            ),
            market=market,
            quantity=quantity,
            available_quantity=available_quantity,
            avg_cost=avg_cost,
            cost_method=self.COST_METHOD,
            cost_basis=cost_basis,
            current_price=current_price,
            market_value=market_value,
            currency=currency,
            unrealized_pnl=unrealized_pnl,
            unrealized_pnl_pct=unrealized_pnl_pct,
            realized_pnl=realized_pnl,
            day_pnl=day_pnl,
            snapshot_time=snapshot_time,
            sync_source="api",
            raw_data=self._serialize_row(row),
        )

    def dataframe_to_positions(
        self,
        df: pd.DataFrame,
        snapshot_time: datetime | None = None,
    ) -> list[Position]:
        """整个 DataFrame → Position 列表。"""
        if snapshot_time is None:
            snapshot_time = datetime.now(timezone.utc)
        return [self.row_to_position(row, snapshot_time) for _, row in df.iterrows()]
