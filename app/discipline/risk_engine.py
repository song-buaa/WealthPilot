"""
Risk Engine — 硬性约束执行层

对应规则（HARD_RULE）：
    规则 1  杠杆工具分级管理
    规则 4  流动性管理（子弹纪律）
    规则 3  单一标的仓位上限
    规则 2  偏离度控制（含于跨资产配置规则）
    规则 6  加仓节奏纪律

输出：RiskCheckResult
    status = ALLOW  — 全部通过
    status = WARNING — 存在预警，不阻断
    status = BLOCK  — 存在违规，必须拦截
"""

from datetime import date
from typing import List, Tuple

from .config import RULES
from .models import PortfolioState, PositionState, TradeAction, RiskCheckResult

_BUY_ACTIONS = {"BUY", "ADD"}
_SELL_ACTIONS = {"SELL", "REDUCE"}


# ─────────────────────────────────────────────────────────
# 各规则检查函数
# ─────────────────────────────────────────────────────────

def _check_circuit_breaker(portfolio: PortfolioState, action: TradeAction) -> List[str]:
    """账户级防御熔断 — 仅拦截加仓操作"""
    if action.action_type not in _BUY_ACTIONS:
        return []
    cfg = RULES["portfolio_circuit_breaker"]
    if abs(portfolio.drawdown_pct) >= cfg["drawdown_trigger_pct"]:
        return [
            f"[账户熔断] 账户当前回撤 {abs(portfolio.drawdown_pct)*100:.1f}%"
            f" ≥ 触发阈值 {cfg['drawdown_trigger_pct']*100:.0f}%，"
            f"已进入防御状态，全面暂停一切加仓。"
            f"恢复条件：回撤收窄至 {cfg['resume_threshold_pct']*100:.0f}% 以下，"
            f"且通过规则11检查清单。"
        ]
    return []


def _check_leverage(portfolio: PortfolioState, action: TradeAction) -> List[str]:
    """规则 1：杠杆工具分级管理"""
    violations = []

    # Level 0 — 完全禁止
    if action.is_margin_trading:
        violations.append(
            "[规则1·Level0] 融资融券：绝对禁止工具。"
            "下跌时杠杆自动放大，触发强制平仓，在最低点锁死损失。操作已拦截。"
        )
    if action.is_options:
        violations.append(
            "[规则1·Level0] 期权：绝对禁止工具。"
            "存在归零风险，一次方向错误即损失全部本金。操作已拦截。"
        )
    if action.is_credit_loan:
        violations.append(
            "[规则1·Level0] 借贷投资（信用贷等）：绝对禁止工具。操作已拦截。"
        )

    # Level 1 — 杠杆 ETF 上限 5%
    if action.is_leverage_etf and action.action_type in _BUY_ACTIONS:
        existing_etf = sum(
            p.weight for p in portfolio.positions
            if p.asset_class == "leverage_etf" and p.symbol != action.symbol
        )
        projected = existing_etf + action.amount_pct
        limit = RULES["leverage_limits"]["level_1_max_pct"]
        if projected > limit:
            violations.append(
                f"[规则1·Level1] 加仓后杠杆ETF总持仓将达 {projected*100:.1f}%，"
                f"超过上限 {limit*100:.0f}%。操作已拦截。"
            )
    return violations


def _check_position_limit(
    portfolio: PortfolioState, action: TradeAction
) -> Tuple[List[str], List[str]]:
    """规则 3：单一标的仓位上限"""
    cfg = RULES["single_asset_limits"]
    violations: List[str] = []
    warnings: List[str] = []

    pos = next((p for p in portfolio.positions if p.symbol == action.symbol), None)
    current_weight = pos.weight if pos else 0.0

    # 当前已超上限 → 无论何种操作都须提醒强制减仓
    if current_weight > cfg["max_position_pct"]:
        violations.append(
            f"[规则3·超限] {action.symbol} 当前仓位 {current_weight*100:.1f}%"
            f" > 硬性上限 {cfg['max_position_pct']*100:.0f}%。"
            f"须立即减仓至上限以下，任何情况不得突破。"
        )

    if action.action_type in _BUY_ACTIONS:
        projected = current_weight + action.amount_pct

        if projected > cfg["max_position_pct"]:
            violations.append(
                f"[规则3·超限] 加仓后 {action.symbol} 仓位将达 {projected*100:.1f}%"
                f"，超过硬性上限 {cfg['max_position_pct']*100:.0f}%。操作已拦截。"
            )
        elif current_weight >= cfg["warning_position_pct"]:
            # 已在警戒区（30%~40%）→ 禁止继续加仓
            violations.append(
                f"[规则3·警戒区] {action.symbol} 当前仓位 {current_weight*100:.1f}%"
                f" 已处于警戒区（{cfg['warning_position_pct']*100:.0f}%"
                f"~{cfg['max_position_pct']*100:.0f}%），禁止继续加仓。"
            )
        elif projected > cfg["warning_position_pct"]:
            warnings.append(
                f"[规则3·预警] 加仓后 {action.symbol} 仓位将达 {projected*100:.1f}%"
                f"，进入警戒区，请评估是否继续加仓。"
            )

    return violations, warnings


def _check_add_rhythm(portfolio: PortfolioState, action: TradeAction) -> List[str]:
    """规则 6：加仓节奏纪律"""
    if action.action_type not in _BUY_ACTIONS:
        return []

    cfg = RULES["position_sizing"]
    violations = []

    # 单次加仓不超过总资产 10%
    if action.amount_pct > cfg["max_single_add_pct"]:
        violations.append(
            f"[规则6·单次上限] 本次加仓 {action.amount_pct*100:.1f}%"
            f" 超过单次上限 {cfg['max_single_add_pct']*100:.0f}%（总投资性资产）。"
            f"禁止一次性建满仓位，须拆分为 ≥{cfg['min_batches_required']} 次执行。操作已拦截。"
        )

    # 两次加仓间隔 ≥ 1 个交易日
    pos = next((p for p in portfolio.positions if p.symbol == action.symbol), None)
    if pos and pos.last_add_date:
        delta = (date.today() - pos.last_add_date).days
        min_days = cfg["min_interval_between_adds_days"]
        if delta < min_days:
            violations.append(
                f"[规则6·间隔] {action.symbol} 上次加仓日期 {pos.last_add_date}，"
                f"距今仅 {delta} 天，须间隔至少 {min_days} 个交易日。"
                f"强制冷静期，防止同一波情绪连续操作。操作已拦截。"
            )

    return violations


def _check_liquidity(portfolio: PortfolioState, action: TradeAction) -> List[str]:
    """规则 4：流动性管理（子弹纪律）"""
    if action.action_type not in _BUY_ACTIONS:
        return []

    cfg = RULES["liquidity_limits"]
    projected_liquidity = portfolio.cash_ratio - action.amount_pct

    if projected_liquidity < cfg["min_cash_pct"]:
        return [
            f"[规则4·流动性] 操作后流动性资金（货币+固收）比例将降至 {projected_liquidity*100:.1f}%"
            f"，低于最低要求 {cfg['min_cash_pct']*100:.0f}%。"
            f"禁止满仓操作——永远不能把子弹打完。操作已拦截。"
        ]
    return []


def _check_deviation(portfolio: PortfolioState) -> Tuple[List[str], List[str]]:
    """规则 2：偏离度控制与再平衡（含于跨资产配置规则）"""
    cfg = RULES["rebalancing_rules"]
    violations: List[str] = []
    warnings: List[str] = []

    for pos in portfolio.positions:
        if pos.target_weight <= 0:
            continue  # 未设目标仓位，跳过

        deviation = pos.weight - pos.target_weight
        abs_dev = abs(deviation)
        direction = "超配" if deviation > 0 else "低配"

        if abs_dev > cfg["deviation_force_rebalance_pct"]:
            violations.append(
                f"[规则2·偏离度] {pos.name or pos.symbol} 偏离目标"
                f" {abs_dev*100:.1f}%（{direction}），"
                f"超过强制阈值 {cfg['deviation_force_rebalance_pct']*100:.0f}%，须立即再平衡。"
            )
        elif abs_dev > cfg["deviation_warning_pct"]:
            warnings.append(
                f"[规则2·偏离度] {pos.name or pos.symbol} 偏离目标"
                f" {abs_dev*100:.1f}%（{direction}），"
                f"下次操作时优先向目标靠拢。"
            )

    return violations, warnings


# ─────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────

def run(portfolio: PortfolioState, action: TradeAction) -> RiskCheckResult:
    """
    Risk Engine 主入口。

    执行顺序（优先级从高到低）：
        规则6 账户熔断 → 规则1 杠杆 → 规则3 仓位 → 规则9 节奏 → 规则4 流动性 → 规则2 偏离度

    返回 RiskCheckResult：
        status = BLOCK   有任意 BLOCK 级违规
        status = WARNING 无 BLOCK，有 WARNING
        status = ALLOW   全部通过
    """
    all_violations: List[str] = []
    all_warnings: List[str] = []

    # 1. 账户熔断（规则6）
    all_violations.extend(_check_circuit_breaker(portfolio, action))

    # 2. 杠杆（规则1）
    all_violations.extend(_check_leverage(portfolio, action))

    # 3. 仓位上限（规则3）
    pos_v, pos_w = _check_position_limit(portfolio, action)
    all_violations.extend(pos_v)
    all_warnings.extend(pos_w)

    # 4. 加仓节奏（规则9）
    all_violations.extend(_check_add_rhythm(portfolio, action))

    # 5. 流动性（规则4）
    all_violations.extend(_check_liquidity(portfolio, action))

    # 6. 偏离度（规则2）
    dev_v, dev_w = _check_deviation(portfolio)
    all_violations.extend(dev_v)
    all_warnings.extend(dev_w)

    # 汇总
    if all_violations:
        status = "BLOCK"
    elif all_warnings:
        status = "WARNING"
    else:
        status = "ALLOW"

    violated_rules = list({
        msg.split("·")[0].lstrip("[") for msg in all_violations + all_warnings
    })

    return RiskCheckResult(
        status=status,
        violated_rules=violated_rules,
        messages=all_violations,
        warnings=all_warnings,
    )
