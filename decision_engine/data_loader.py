"""
数据加载模块 (Data Loader)

职责：加载决策引擎所需的全部数据，统一封装为 LoadedData。

数据来源（MVP）：
    - 用户画像：mock JSON（Portfolio 模型暂不含风险偏好，待后续扩展）
    - 持仓数据：SQLite（Position 模型）
    - 投资纪律：Portfolio 模型 + discipline/config.py
    - 投研观点：ResearchViewpoint 模型（无数据时 fallback 到 mock）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from app.database import get_session
from app.discipline.config import RULES as DISCIPLINE_RULES
from app.models import Portfolio, Position, ResearchViewpoint
from app.state import portfolio_id as default_portfolio_id


# ── 数据类 ────────────────────────────────────────────────────────────────────

@dataclass
class UserProfile:
    """用户投资画像（MVP 阶段 mock）"""
    risk_level: str = "中高"    # 低 / 中 / 中高 / 高
    goal: str = "长期增值"
    investment_years: int = 5   # 预计投资年限


@dataclass
class PositionInfo:
    """单一标的持仓信息"""
    name: str
    ticker: str
    asset_class: str
    weight: float              # 占总投资性资产比例（0~1）
    market_value_cny: float
    cost_price: float
    current_price: float
    profit_loss_rate: float    # 收益率（小数）


@dataclass
class InvestmentRules:
    """投资纪律约束"""
    max_single_position: float  # 单一持仓上限（0~1）
    max_equity_pct: float       # 权益上限
    min_cash_pct: float         # 最低流动性
    max_leverage_ratio: float   # 最大杠杆率


@dataclass
class LoadedData:
    """决策引擎所需全部数据，由 load() 返回"""
    profile: UserProfile
    positions: list[PositionInfo]           # 所有持仓
    target_position: Optional[PositionInfo] # 被决策标的当前持仓（唯一匹配时有值）
    rules: InvestmentRules
    research: list[str]                     # 投研观点文本列表
    total_assets: float                     # 总投资性资产（人民币）

    # 原始数据（供 UI 展示用）
    raw_portfolio: Optional[object] = None

    # 歧义匹配：找到多个候选持仓时非空（此时 target_position 为 None）
    ambiguous_matches: list[PositionInfo] = field(default_factory=list)

    @property
    def has_required_data(self) -> bool:
        """前置校验用：三要素是否齐全"""
        return (
            self.profile is not None
            and len(self.positions) > 0
            and self.rules is not None
        )


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

    Args:
        asset_name: 被决策标的名称（来自意图解析），可为 None
        pid: portfolio_id

    Returns:
        LoadedData 实例
    """
    session = get_session()
    try:
        # ── 1. Portfolio（策略设置）──────────────────────────────────────────
        portfolio = session.query(Portfolio).filter_by(id=pid).first()
        if portfolio is None:
            # 找不到 portfolio，使用保守默认值
            portfolio = _mock_portfolio()

        # ── 2. 持仓数据 ──────────────────────────────────────────────────────
        db_positions = session.query(Position).filter_by(
            portfolio_id=pid, segment="投资"
        ).all()

        total_assets = sum(p.market_value_cny or 0 for p in db_positions) or 1.0

        positions = [
            PositionInfo(
                name=p.name,
                ticker=p.ticker or "",
                asset_class=p.asset_class or "未知",
                weight=(p.market_value_cny or 0) / total_assets,
                market_value_cny=p.market_value_cny or 0,
                cost_price=p.cost_price or 0,
                current_price=p.current_price or 0,
                profit_loss_rate=p.profit_loss_rate or 0,
            )
            for p in db_positions
        ]

        # ── 3. 找到被决策标的的当前持仓 ─────────────────────────────────────
        target_position = None
        ambiguous_matches: list[PositionInfo] = []
        if asset_name:
            matches = _find_all_positions(positions, asset_name)
            if len(matches) == 1:
                target_position = matches[0]
            elif len(matches) > 1:
                ambiguous_matches = matches  # 多候选：交由调用方处理

        # ── 4. 投资纪律规则 ──────────────────────────────────────────────────
        rules = InvestmentRules(
            max_single_position=_safe_pct(portfolio.max_single_stock_pct, default=0.25),
            max_equity_pct=_safe_pct(portfolio.max_equity_pct, default=0.80),
            min_cash_pct=DISCIPLINE_RULES["liquidity_limits"]["min_cash_pct"],
            max_leverage_ratio=_safe_pct(portfolio.max_leverage_ratio, default=0.50),
        )

        # ── 5. 投研观点 ──────────────────────────────────────────────────────
        research = _load_research(session, pid, asset_name)

        # ── 6. 用户画像（MVP mock）──────────────────────────────────────────
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
        )

    finally:
        session.close()


# ── 辅助函数 ───────────────────────────────────────────────────────────────────

def _find_all_positions(positions: list[PositionInfo], asset_name: str) -> list[PositionInfo]:
    """按名称或 ticker 匹配持仓，返回所有匹配结果（用于歧义检测）。

    匹配优先级（精确优先）：
    1. 精确匹配（名称归一化后完全相等，或 ticker 完全相等）→ 若有命中则只返回精确结果
    2. 模糊匹配（名称双向包含 / ticker 包含）→ 仅在无精确匹配时使用

    产品规则：
    - 不支持单字匹配（asset_name 归一化后长度 < 2 时直接返回空）
    - ticker 匹配要求至少 2 字符，防止空字符串误匹配
    - 结果可能为多个（歧义），由调用方决定如何处理
    """
    if not asset_name:
        return []

    name_lower = asset_name.lower().replace(" ", "")
    # 产品策略：不支持单字匹配
    if len(name_lower) < 2:
        return []

    exact_matches: list[PositionInfo] = []
    partial_matches: list[PositionInfo] = []

    for p in positions:
        p_name = p.name.lower().replace(" ", "")
        p_ticker = p.ticker.strip().lower()

        # ── 精确匹配 ────────────────────────────────────────────────────────
        name_exact = (name_lower == p_name)
        ticker_exact = (len(p_ticker) >= 2 and name_lower == p_ticker)

        if name_exact or ticker_exact:
            exact_matches.append(p)
            continue

        # ── 模糊匹配 ────────────────────────────────────────────────────────
        name_partial = (
            (name_lower in p_name) or
            (p_name in name_lower and len(p_name) >= 2)
        )
        ticker_partial = (
            len(p_ticker) >= 2
            and (p_ticker in name_lower or name_lower in p_ticker)
        )
        if name_partial or ticker_partial:
            partial_matches.append(p)

    # 精确匹配优先；无精确匹配时才使用模糊结果
    return exact_matches if exact_matches else partial_matches


def _load_research(session, pid: int, asset_name: Optional[str]) -> list[str]:
    """从 ResearchViewpoint 表加载投研观点，无数据时 fallback 到 mock。"""
    if not asset_name:
        return _DEFAULT_MOCK_RESEARCH

    # 从数据库查询
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

    # Fallback：mock 数据（按标的名称匹配）
    for key, views in _MOCK_RESEARCH.items():
        if key in asset_name or asset_name in key:
            return views

    return _DEFAULT_MOCK_RESEARCH


def _safe_pct(value, default: float) -> float:
    """安全读取百分比字段，处理 None 和 > 1 的情况（有些字段存 0~100，有些存 0~1）。

    规则（产品决策）：
    - None     → 返回默认值（字段缺失）
    - 负值     → 抛出 ValueError（非法输入，不静默替换）
    - 0        → 返回 0.0（合法边界值）
    - (0, 1]   → 直接返回（已是小数形式）
    - (1, 100] → 除以 100 转换为小数
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
    return v  # 0.0 是合法边界值，直接返回


class _MockPortfolio:
    """当数据库中没有 Portfolio 时使用的保守默认值对象。"""
    max_single_stock_pct = 25.0
    max_equity_pct = 80.0
    min_cash_pct = 20.0
    max_leverage_ratio = 50.0
    min_equity_pct = 0.0
    min_fixed_income_pct = 0.0
    max_fixed_income_pct = 60.0
    min_cash_pct = 10.0
    max_cash_pct = 100.0
    min_alternative_pct = 0.0
    max_alternative_pct = 20.0


def _mock_portfolio() -> _MockPortfolio:
    return _MockPortfolio()
