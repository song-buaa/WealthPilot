"""
快照 → Position 业务表 同步服务。

读取一次同步产生的 PositionSnapshot 列表,upsert 到 Position 业务表。
- 字段覆盖策略:混合模式(核心数据覆盖,用户语义字段保护)
- 汇率:复用 app.fx_service.fx_service.convert()
- ticker 去归一化:AAPL.US → AAPL
- asset_class:调 classify_position 做 5 大类中文归类
"""
from typing import Iterable

from sqlalchemy.orm import Session

from app.allocation.classifier import classify_position
from app.allocation.types import ALLOC_TO_CN
from app.fx_service import fx_service
from app.models import Position as BusinessPosition
from services.broker_sync.models import PositionSnapshot


# broker → platform 映射
BROKER_TO_PLATFORM = {
    "tiger": "老虎证券",
    "futu": "富途证券",
    "snowball": "雪盈证券",
}

# 受保护字段(首次写入,后续不覆盖)
PROTECTED_FIELDS = {"name", "asset_class", "segment"}

# 合法中文 5 大类(Position 表 asset_class 应存这些值)
LEGAL_CN_CLASSES = {"权益", "固收", "货币", "另类", "衍生", "未分类"}

# 英文 sec_type → 中文基础映射(作为名称匹配失败时的兜底)
SEC_TYPE_TO_CN_BASIC = {
    "equity": "权益",
    "etf": "权益",
    "option": "衍生",
    "future": "衍生",
    "warrant": "衍生",
    "bond": "固收",
    "fund": "权益",  # 基金默认权益,名称匹配可覆盖
}


def _resolve_asset_class(sec_type_en: str, name: str, ticker: str) -> str:
    """
    把 adapter 输出的英文 sec_type 转换成 WealthPilot 5 大类中文。

    逻辑(名称优先):
    1. 先用名称做关键词匹配(更精确:债券ETF→固收,黄金→另类)
    2. 名称匹配失败时,用 sec_type 基础映射兜底(equity→权益,option→衍生)
    """
    from app.allocation.classifier import classify_by_name_or_tag
    from app.allocation.types import AllocAssetClass

    # 第 1 步:名称关键词匹配(更精确)
    name_result = classify_by_name_or_tag(name)
    if name_result != AllocAssetClass.UNCLASSIFIED:
        cn = ALLOC_TO_CN.get(name_result)
        return cn if cn else "未分类"

    # 第 2 步:名称无法判定时,用 sec_type 基础映射
    cn_basic = SEC_TYPE_TO_CN_BASIC.get(sec_type_en.lower(), "")
    if cn_basic:
        return cn_basic

    return "未分类"


class PositionUpsertService:
    """把 PositionSnapshot 同步到 Position 业务表。"""

    def __init__(self, session: Session):
        self.session = session

    def upsert_from_snapshots(
        self,
        snapshots: Iterable[PositionSnapshot],
    ) -> dict:
        """
        把 snapshots 同步到 Position 业务表。

        返回报告字典:
        {
            "inserted": int,
            "updated": int,
            "errors": list,
        }
        """
        inserted = 0
        updated = 0
        errors = []

        for snap in snapshots:
            try:
                if self._upsert_single(snap):
                    inserted += 1
                else:
                    updated += 1
            except Exception as e:
                errors.append({
                    "symbol": snap.symbol,
                    "error": f"{type(e).__name__}: {e}",
                })

        if errors:
            self.session.rollback()
            return {"inserted": 0, "updated": 0, "errors": errors}

        self.session.commit()
        return {"inserted": inserted, "updated": updated, "errors": []}

    def _upsert_single(self, snap: PositionSnapshot) -> bool:
        """单条 upsert。返回 True 表示新增,False 表示更新。"""
        ticker = self._denormalize_ticker(snap.symbol)
        platform = BROKER_TO_PLATFORM.get(snap.broker)
        if not platform:
            raise ValueError(f"未知 broker: {snap.broker}")

        # 1. 汇率换算
        market_value_cny, fx_rate, fx_date = fx_service.convert(
            float(snap.market_value), snap.currency, "CNY"
        )

        # 2. 查现有行
        existing = self.session.query(BusinessPosition).filter_by(
            ticker=ticker, platform=platform
        ).first()

        # 3. 计算盈亏
        pnl_original = float(snap.unrealized_pnl)
        pnl_cny = pnl_original * fx_rate
        # Position 表 profit_loss_rate 存百分数(如 30.5 表示 +30.5%)
        pnl_pct = float(snap.unrealized_pnl_pct) * 100

        # 4. 计算 asset_class(中文 5 大类)
        resolved_asset_class = _resolve_asset_class(snap.asset_class, snap.name, ticker)

        if existing is None:
            # 新建(需要 portfolio_id,默认用 1)
            new_pos = BusinessPosition(
                portfolio_id=1,
                ticker=ticker,
                platform=platform,
                name=snap.name,
                asset_class=resolved_asset_class,
                segment="投资",
                currency="CNY",
                quantity=float(snap.quantity),
                cost_price=float(snap.avg_cost),
                current_price=float(snap.current_price),
                market_value_cny=market_value_cny,
                original_currency=snap.currency,
                original_value=float(snap.market_value),
                fx_rate_to_cny=fx_rate,
                fx_rate_date=fx_date,
                profit_loss_value=pnl_cny,
                profit_loss_rate=pnl_pct,
                profit_loss_original_value=pnl_original,
            )
            self.session.add(new_pos)
            return True
        else:
            # 更新:只覆盖非保护字段
            existing.quantity = float(snap.quantity)
            existing.cost_price = float(snap.avg_cost)
            existing.current_price = float(snap.current_price)
            existing.market_value_cny = market_value_cny
            existing.original_currency = snap.currency
            existing.original_value = float(snap.market_value)
            existing.fx_rate_to_cny = fx_rate
            existing.fx_rate_date = fx_date
            existing.profit_loss_value = pnl_cny
            existing.profit_loss_rate = pnl_pct
            existing.profit_loss_original_value = pnl_original
            existing.currency = "CNY"
            # name / asset_class / segment 受保护,不覆盖
            # 但如果 asset_class 是非法值(如英文 'equity'),强制修正
            if existing.asset_class not in LEGAL_CN_CLASSES:
                existing.asset_class = resolved_asset_class
            return False

    @staticmethod
    def _denormalize_ticker(symbol: str) -> str:
        """AAPL.US → AAPL,00068.HK → 00068。"""
        if "." in symbol:
            return symbol.split(".")[0]
        return symbol
