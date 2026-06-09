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
    "guojin": "国金证券",
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

        逻辑：
        1. upsert 本次 snapshot 中的所有持仓
        2. 删除该 platform 中本次 snapshot 不再包含的持仓（已卖出清仓）

        返回报告字典:
        {
            "inserted": int,
            "updated": int,
            "removed": int,
            "errors": list,
        }
        """
        inserted = 0
        updated = 0
        errors = []

        snap_list = list(snapshots)

        for snap in snap_list:
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
            return {"inserted": 0, "updated": 0, "removed": 0, "errors": errors}

        # 删除已清仓的持仓：本次 snapshot 覆盖的 platform 中，
        # ticker 不在本次 snapshot 里的记录应被删除
        removed = self._remove_stale_positions(snap_list)

        self.session.commit()
        return {"inserted": inserted, "updated": updated, "removed": removed, "errors": []}

    def _remove_stale_positions(self, snap_list: list) -> int:
        """删除本次 snapshot 不再包含的持仓（已卖出/清仓）。

        按 platform 分组：对于本次同步涉及的每个 platform，
        找出 Position 表中属于该 platform 但 ticker 不在本次 snapshot 中的记录并删除。
        """
        from collections import defaultdict

        # 按 platform 分组，收集本次 snapshot 的所有 ticker
        platform_tickers: dict[str, set[str]] = defaultdict(set)
        for snap in snap_list:
            platform = BROKER_TO_PLATFORM.get(snap.broker)
            if platform:
                ticker = self._denormalize_ticker(snap.symbol)
                platform_tickers[platform].add(ticker)

        removed = 0
        for platform, current_tickers in platform_tickers.items():
            stale = self.session.query(BusinessPosition).filter(
                BusinessPosition.platform == platform,
                ~BusinessPosition.ticker.in_(current_tickers),
            ).all()
            for pos in stale:
                self.session.delete(pos)
                removed += 1

        return removed

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
                symbol=snap.symbol,  # v3.11: 存完整 TICKER:MARKET 真值
                platform=platform,
                name=snap.name,
                asset_class=resolved_asset_class,
                segment="投资",
                currency=snap.currency,  # v3.4 修复: 存原币种(USD/HKD),不是 CNY
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
            existing.currency = snap.currency  # v3.4 修复: 存原币种(USD/HKD)
            existing.symbol = snap.symbol      # v3.11: 每次 sync 更新 symbol 真值
            # name / asset_class / segment 受保护,不覆盖
            # 但如果 asset_class 是非法值(如英文 'equity'),强制修正
            if existing.asset_class not in LEGAL_CN_CLASSES:
                existing.asset_class = resolved_asset_class
            return False

    _KNOWN_MARKETS = {"US", "HK", "SH", "SZ"}

    @staticmethod
    def _denormalize_ticker(symbol: str) -> str:
        """从 normalize 后的 symbol 还原纯 ticker。

        支持新旧格式 + 兼容 ticker 自身含点的特殊标的（BRK.B 等）：
          "AAPL:US"          → "AAPL"
          "BRK.B:US"         → "BRK.B"
          "AAPL.US"          → "AAPL"          (旧格式)
          "BRK.B.US"         → "BRK.B"         (旧格式,保护 .B)
          "LU2416422678.USD" → "LU2416422678.USD"  (USD 不是市场,保留)
        """
        if not symbol:
            return symbol

        # 新格式优先：TICKER:MARKET
        if ":" in symbol:
            return symbol.split(":", 1)[0]

        # 旧格式：仅当最后一段是 KNOWN_MARKET 才剥离
        if "." in symbol:
            parts = symbol.rsplit(".", 1)
            if len(parts) == 2 and parts[1].upper() in PositionUpsertService._KNOWN_MARKETS:
                return parts[0]

        return symbol


def backfill_bare_names(session: Session) -> int:
    """补全 name == ticker（裸代码）的持仓行的中文名。

    按 symbol（如 LI:US）分组，组内若存在 name≠ticker 的行（有中文名）
    则作为 donor，回填给组内 name==ticker 的行。

    自限制：一旦补上中文名，name≠ticker，后续不再触发。
    """
    all_positions = session.query(BusinessPosition).filter(
        BusinessPosition.market_value_cny > 0,
        BusinessPosition.symbol.isnot(None),
        BusinessPosition.symbol != "",
    ).all()

    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for p in all_positions:
        groups[p.symbol].append(p)

    count = 0
    for symbol, members in groups.items():
        donor_name = None
        for p in members:
            if p.ticker and p.name and p.name != p.ticker:
                donor_name = p.name
                break

        if not donor_name:
            continue

        for p in members:
            if p.ticker and p.name == p.ticker:
                p.name = donor_name
                count += 1

    if count:
        session.commit()
        print(f"[backfill] 补全 {count} 条裸代码名称", flush=True)
    return count
