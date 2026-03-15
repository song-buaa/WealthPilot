"""
WealthPilot - 分析引擎
负责资产配置分析、偏离度计算、风险检测
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from app.models import Portfolio, Position, Liability, get_session
from app.config import DEVIATION_THRESHOLD, HIGH_SEVERITY_THRESHOLD


@dataclass
class BalanceSheet:
    """个人资产负债表"""
    total_assets: float = 0.0
    total_liabilities: float = 0.0
    net_worth: float = 0.0
    leverage_ratio: float = 0.0  # 负债 / 总资产 (%)

    # 大类资产分布
    equity_value: float = 0.0
    fixed_income_value: float = 0.0
    cash_value: float = 0.0
    alternative_value: float = 0.0

    # 大类资产占比 (%)
    equity_pct: float = 0.0
    fixed_income_pct: float = 0.0
    cash_pct: float = 0.0
    alternative_pct: float = 0.0

    # 平台分布
    platform_distribution: Dict[str, float] = field(default_factory=dict)

    # 持仓集中度 (单一资产占比)
    concentration: Dict[str, float] = field(default_factory=dict)


@dataclass
class DeviationAlert:
    """策略偏离告警"""
    alert_type: str       # 策略偏离 / 纪律触发 / 风险暴露
    severity: str         # 高 / 中 / 低
    title: str
    description: str
    current_value: float
    target_value: float
    deviation: float      # 偏离度 (百分点)


def analyze_portfolio(portfolio_id: int) -> Optional[BalanceSheet]:
    """对指定投资组合进行全面分析，生成资产负债表"""
    session = get_session()
    try:
        portfolio = session.query(Portfolio).filter_by(id=portfolio_id).first()
        if not portfolio:
            return None

        positions = portfolio.positions
        liabilities = portfolio.liabilities

        bs = BalanceSheet()

        # 计算各大类资产市值
        for pos in positions:
            value = pos.market_value_cny
            bs.total_assets += value

            if pos.asset_class == "权益":
                bs.equity_value += value
            elif pos.asset_class == "固收":
                bs.fixed_income_value += value
            elif pos.asset_class == "现金":
                bs.cash_value += value
            elif pos.asset_class == "另类":
                bs.alternative_value += value

            # 平台分布
            platform = pos.platform
            bs.platform_distribution[platform] = bs.platform_distribution.get(platform, 0) + value

            # 持仓集中度：用 id 作为 key 避免同名资产互相覆盖
            # 格式："{id}:{name}"，UI 层用 split(":")[1] 取显示名
            bs.concentration[f"{pos.id}:{pos.name}"] = value

        # 计算负债
        for liab in liabilities:
            bs.total_liabilities += liab.amount

        # 计算净资产
        bs.net_worth = bs.total_assets - bs.total_liabilities

        # 计算占比
        if bs.total_assets > 0:
            bs.equity_pct = round(bs.equity_value / bs.total_assets * 100, 1)
            bs.fixed_income_pct = round(bs.fixed_income_value / bs.total_assets * 100, 1)
            bs.cash_pct = round(bs.cash_value / bs.total_assets * 100, 1)
            bs.alternative_pct = round(bs.alternative_value / bs.total_assets * 100, 1)
            bs.leverage_ratio = round(bs.total_liabilities / bs.total_assets * 100, 1)

            # 集中度转为百分比
            for name in bs.concentration:
                bs.concentration[name] = round(bs.concentration[name] / bs.total_assets * 100, 1)

        return bs
    finally:
        session.close()


def check_deviations(portfolio_id: int, balance_sheet: BalanceSheet) -> List[DeviationAlert]:
    """检查策略偏离和风险暴露，生成告警列表"""
    session = get_session()
    alerts = []

    try:
        portfolio = session.query(Portfolio).filter_by(id=portfolio_id).first()
        if not portfolio or balance_sheet.total_assets == 0:
            return alerts

        # ── 1. 策略偏离检测（区间模式）──
        # min=0 且 max=100 视为"不设约束"，跳过检测
        def _check_range(label, current_pct, min_pct, max_pct):
            if min_pct == 0.0 and max_pct == 100.0:
                return  # 不设约束
            if current_pct < min_pct:
                dev = current_pct - min_pct  # 负值
                severity = "高" if abs(dev) > HIGH_SEVERITY_THRESHOLD else "中"
                alerts.append(DeviationAlert(
                    alert_type="策略偏离",
                    severity=severity,
                    title=f"{label}欠配",
                    description=f"当前{label}占比 {current_pct}%，低于目标下限 {min_pct}%，偏离 {dev:.1f} 个百分点。",
                    current_value=current_pct,
                    target_value=min_pct,
                    deviation=dev,
                ))
            elif current_pct > max_pct:
                dev = current_pct - max_pct  # 正值
                severity = "高" if dev > HIGH_SEVERITY_THRESHOLD else "中"
                alerts.append(DeviationAlert(
                    alert_type="策略偏离",
                    severity=severity,
                    title=f"{label}超配",
                    description=f"当前{label}占比 {current_pct}%，高于目标上限 {max_pct}%，偏离 {dev:+.1f} 个百分点。",
                    current_value=current_pct,
                    target_value=max_pct,
                    deviation=dev,
                ))

        _check_range("权益资产", balance_sheet.equity_pct,
                     portfolio.min_equity_pct, portfolio.max_equity_pct)
        _check_range("固收资产", balance_sheet.fixed_income_pct,
                     portfolio.min_fixed_income_pct, portfolio.max_fixed_income_pct)
        _check_range("现金资产", balance_sheet.cash_pct,
                     portfolio.min_cash_pct, portfolio.max_cash_pct)
        _check_range("另类资产", balance_sheet.alternative_pct,
                     portfolio.min_alternative_pct, portfolio.max_alternative_pct)

        # ── 2. 纪律触发检测 ──
        for key, pct in balance_sheet.concentration.items():
            display_name = key.split(":", 1)[1]  # 剥掉 "id:" 前缀，取可读名称
            if pct > portfolio.max_single_stock_pct:
                alerts.append(DeviationAlert(
                    alert_type="纪律触发",
                    severity="高",
                    title=f"单一持仓超限: {display_name}",
                    description=f"{display_name} 占总资产 {pct}%，超过单一持仓上限 {portfolio.max_single_stock_pct}%。建议减仓至上限以下。",
                    current_value=pct,
                    target_value=portfolio.max_single_stock_pct,
                    deviation=pct - portfolio.max_single_stock_pct,
                ))

        # ── 3. 风险暴露检测 ──
        if balance_sheet.leverage_ratio > portfolio.max_leverage_ratio:
            alerts.append(DeviationAlert(
                alert_type="风险暴露",
                severity="高",
                title="杠杆率过高",
                description=f"当前杠杆率 {balance_sheet.leverage_ratio}%，超过安全阈值 {portfolio.max_leverage_ratio}%。建议降低负债或增加资产。",
                current_value=balance_sheet.leverage_ratio,
                target_value=portfolio.max_leverage_ratio,
                deviation=balance_sheet.leverage_ratio - portfolio.max_leverage_ratio,
            ))

        # 按严重程度排序
        severity_order = {"高": 0, "中": 1, "低": 2}
        alerts.sort(key=lambda a: severity_order.get(a.severity, 3))

        return alerts
    finally:
        session.close()
