"""
数据加载模块 (Data Loader)

职责：加载决策引擎所需的全部数据，统一封装为 LoadedData。

数据来源（MVP）：
    - 用户画像：mock JSON（Portfolio 模型暂不含风险偏好，待后续扩展）
    - 持仓数据：通过公共聚合模块 app.utils.position_aggregator
              （与「投资纪律」页面使用完全相同的多平台融合逻辑和口径）
    - 投资纪律：Portfolio 模型 + discipline/config.py
    - 投研观点：ResearchViewpoint 模型（无数据时 fallback 到 mock）

口径说明：
    当前仓位 (weight) = 该标的聚合市值 / 所有投资类持仓总市值
    与「投资纪律 - 持仓集中度」完全一致，全系统唯一口径。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from app.database import get_session
from app.discipline.config import RULES as DISCIPLINE_RULES
from app.models import Portfolio, ResearchViewpoint
from app.state import portfolio_id as default_portfolio_id
from app.utils.position_aggregator import (
    AggregatedPosition,
    aggregate_investment_positions,
    find_target,
)


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class UserProfile:
    """用户投资画像（MVP 阶段 mock）"""
    risk_level: str = "中高"    # 低 / 中 / 中高 / 高
    goal: str = "长期增值"
    investment_years: int = 5   # 预计投资年限


@dataclass
class PositionInfo:
    """
    单一标的持仓信息（聚合后，每个标的唯一一条）。

    weight 始终等于 market_value_cny / total_assets，
    与「投资纪律 - 持仓集中度」口径完全一致。
    """
    name: str
    ticker: str
    asset_class: str
    weight: float              # 占投资组合总市值比例（0~1）
    market_value_cny: float    # 聚合市值（所有平台之和）
    cost_price: float          # 聚合成本（cost_value，非单价）
    current_price: float       # 当前价格（聚合持仓中首条，参考用）
    profit_loss_rate: float    # 加权盈亏率（小数）
    platforms: list[str] = field(default_factory=list)  # 持仓平台列表

    @classmethod
    def from_aggregated(cls, agg: AggregatedPosition) -> "PositionInfo":
        """从 AggregatedPosition 转换，保持字段语义一致。"""
        return cls(
            name=agg.name,
            ticker=agg.ticker,
            asset_class=agg.asset_class,
            weight=agg.weight,
            market_value_cny=agg.market_value_cny,
            cost_price=agg.cost_value,      # 聚合成本总额
            current_price=0.0,              # 聚合后无单价概念，置 0
            profit_loss_rate=agg.profit_loss_rate,   # 已是小数
            platforms=list(agg.platforms),
        )


@dataclass
class InvestmentRules:
    """投资纪律约束"""
    max_single_position: float  # 单一持仓上限（0~1）
    max_equity_pct: float       # 权益上限
    min_cash_pct: float         # 最低流动性
    max_leverage_ratio: float   # 最大杠杆率


@dataclass
class DataWarning:
    """数据质量告警，供 decision_flow 决定是否降级结论。"""
    level: str      # "error" | "warning"
    message: str


@dataclass
class LoadedData:
    """决策引擎所需全部数据，由 load() 返回"""
    profile: UserProfile
    positions: list[PositionInfo]           # 所有持仓（聚合后，每标的唯一一条）
    target_position: Optional[PositionInfo] # 被决策标的（聚合后）
    rules: InvestmentRules
    research: list[str]                     # 投研观点文本列表
    total_assets: float                     # 总投资性资产（人民币，与 discipline 同口径）

    # 原始数据（供 UI 展示用）
    raw_portfolio: Optional[object] = None

    # 歧义匹配：聚合后仍有多个候选时非空（此时 target_position 为 None）
    ambiguous_matches: list[PositionInfo] = field(default_factory=list)

    # 数据质量告警
    data_warnings: list[DataWarning] = field(default_factory=list)

    @property
    def has_required_data(self) -> bool:
        """前置校验用：三要素是否齐全"""
        return (
            self.profile is not None
            and len(self.positions) > 0
            and self.rules is not None
        )

    @property
    def has_data_errors(self) -> bool:
        """是否存在 error 级别的数据质量问题（应中断最终结论）"""
        return any(w.level == "error" for w in self.data_warnings)


# ── Mock 数据（用于 demo / 数据缺失时的 fallback）────────────────────────────

_MOCK_RESEARCH = {
    "理想汽车": [
        "看好 2025 年新车型产品周期，L9 / MEGA 销量稳定",
        "短期销量承压，市场竞争加剧，需关注月度交付数据",
        "公司现金流健康，自研芯片进展超预期",
    ],
    "腾讯": [
        "游戏业务回暖，海外收入增长明显",
        "监管环境趋于稳定，港股估值具备吸引力",
    ],
    "英伟达": [
        "AI 算力需求持续爆发，数据中心业务增长强劲",
        "估值偏高，短期存在波动风险",
    ],
}

_DEFAULT_MOCK_RESEARCH = [
    "暂无该标的的投研观点，建议自行研究或参考市场报告。"
]


# ── 核心函数 ───────────────────────────────────────────────────────────────────

def load(asset_name: Optional[str], pid: int = default_portfolio_id) -> LoadedData:
    """
    加载决策所需的全部数据。

    持仓数据通过公共聚合模块 app.utils.position_aggregator 加载，
    与「投资纪律 - 持仓集中度」使用完全相同的多平台融合逻辑。

    Args:
        asset_name: 被决策标的名称（来自意图解析），可为 None
        pid: portfolio_id

    Returns:
        LoadedData 实例
    """
    warnings: list[DataWarning] = []
    session = get_session()
    try:
        # ── 1. Portfolio（策略设置）──────────────────────────────────────────
        portfolio = session.query(Portfolio).filter_by(id=pid).first()
        if portfolio is None:
            portfolio = _mock_portfolio()
            warnings.append(DataWarning(
                level="warning",
                message="未找到投资组合配置，使用保守默认值。"
            ))

        # ── 2. 持仓数据（通过公共聚合模块，口径与投资纪律完全一致）───────────
        agg_positions, total_assets = aggregate_investment_positions(pid)

        if not agg_positions:
            warnings.append(DataWarning(
                level="error",
                message="投资账户中暂无持仓数据，无法进行决策分析。"
            ))

        # ── 3. total_assets 异常检查 ─────────────────────────────────────────
        if total_assets <= 0:
            warnings.append(DataWarning(
                level="error",
                message=f"总资产异常（{total_assets:.2f}），数据可能存在问题，建议核实后重试。"
            ))

        # ── 4. 转换为 PositionInfo（保持对下游的兼容性）─────────────────────
        positions = [PositionInfo.from_aggregated(p) for p in agg_positions]

        # ── 5. 查找目标持仓（聚合后精确匹配）────────────────────────────────
        target_position: Optional[PositionInfo] = None
        ambiguous_matches: list[PositionInfo] = []

        if asset_name:
            agg_target, agg_ambiguous = find_target(agg_positions, asset_name)
            if agg_target:
                target_position = PositionInfo.from_aggregated(agg_target)
            elif agg_ambiguous:
                ambiguous_matches = [PositionInfo.from_aggregated(p) for p in agg_ambiguous]

        # ── 6. 投资纪律规则 ──────────────────────────────────────────────────
        rules = InvestmentRules(
            max_single_position=_safe_pct(portfolio.max_single_stock_pct, default=0.25),
            max_equity_pct=_safe_pct(portfolio.max_equity_pct, default=0.80),
            min_cash_pct=DISCIPLINE_RULES["liquidity_limits"]["min_cash_pct"],
            max_leverage_ratio=_safe_pct(portfolio.max_leverage_ratio, default=0.50),
        )

        # ── 7. 投研观点 ──────────────────────────────────────────────────────
        research = _load_research(session, pid, asset_name)

        # ── 8. 用户画像（MVP mock）──────────────────────────────────────────
        profile = UserProfile()

        return LoadedData(
            profile=profile,
            positions=positions,
            target_position=target_position,
            rules=rules,
            research=research,
            total_assets=total_assets,
            raw_portfolio=portfolio,
            ambiguous_matches=ambiguous_matches,
            data_warnings=warnings,
        )

    finally:
        session.close()


# ── 辅助函数 ───────────────────────────────────────────────────────────────────

def _load_research(session, pid: int, asset_name: Optional[str]) -> list[str]:
    """从 ResearchViewpoint 表加载投研观点，无数据时 fallback 到 mock。"""
    if not asset_name:
        return _DEFAULT_MOCK_RESEARCH

    viewpoints = session.query(ResearchViewpoint).filter(
        ResearchViewpoint.object_name.ilike(f"%{asset_name}%")
    ).order_by(ResearchViewpoint.updated_at.desc()).limit(5).all()

    if viewpoints:
        result = []
        for vp in viewpoints:
            if vp.thesis:
                result.append(vp.thesis)
            if vp.supporting_points:
                try:
                    pts = json.loads(vp.supporting_points)
                    result.extend(pts[:2] if isinstance(pts, list) else [str(pts)])
                except Exception:
                    result.append(str(vp.supporting_points)[:100])
        return result[:5] if result else _DEFAULT_MOCK_RESEARCH

    for key, views in _MOCK_RESEARCH.items():
        if key in asset_name or asset_name in key:
            return views

    return _DEFAULT_MOCK_RESEARCH


def _safe_pct(value, default: float) -> float:
    """
    安全读取百分比字段，处理 None 和 > 1 的情况。

    规则：
    - None     → 返回默认值
    - 负值     → 抛出 ValueError（非法输入）
    - 0        → 返回 0.0（合法边界值）
    - (0, 1]   → 直接返回
    - (1, 100] → 除以 100
    """
    if value is None:
        return default
    v = float(value)
    if v < 0:
        raise ValueError(
            f"百分比字段包含非法负值：{value}。请检查策略配置，确保所有百分比 ≥ 0。"
        )
    if v > 1.0:
        v = v / 100.0
    return v


class _MockPortfolio:
    """当数据库中没有 Portfolio 时使用的保守默认值对象。"""
    max_single_stock_pct = 25.0
    max_equity_pct = 80.0
    min_cash_pct = 10.0
    max_leverage_ratio = 50.0
    min_equity_pct = 0.0
    min_fixed_income_pct = 0.0
    max_fixed_income_pct = 60.0
    max_cash_pct = 100.0
    min_alternative_pct = 0.0
    max_alternative_pct = 20.0


def _mock_portfolio() -> _MockPortfolio:
    return _MockPortfolio()
