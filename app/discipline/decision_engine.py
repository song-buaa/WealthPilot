"""
Decision Engine — 策略判断层

对应规则（SOFT_RULE）：
    规则 1  动态仓位管理（逆向操作）
    规则 2  左侧交易原则 + 做T策略
    规则 7  止损与逻辑判断（硬止损/软止损复核）
    规则 10 长期持仓底仓机制

输出：DecisionResult
    recommendation = BUY / SELL / HOLD / REDUCE / ADD

⚠️  Decision Engine 不得覆盖 Risk Engine 的 BLOCK 决定，
    Risk Engine 优先级绝对最高，engine_runner 保证执行顺序。
"""

from typing import List, Tuple

from .config import RULES
from .models import (
    PortfolioState, PositionState, MarketContext,
    TradeAction, RiskCheckResult, DecisionResult,
)

# 建议优先级（数字越小优先级越高）
_PRIORITY = {"SELL": 0, "REDUCE": 1, "HOLD": 2, "ADD": 3, "BUY": 4}

_BUY_ACTIONS = {"BUY", "ADD"}
_SELL_ACTIONS = {"SELL", "REDUCE"}


# ─────────────────────────────────────────────────────────
# 规则实现
# ─────────────────────────────────────────────────────────

def _rule7_stop_loss(pos: PositionState) -> Tuple[str, List[str], List[str]]:
    """规则 7：止损与逻辑判断"""
    cfg = RULES["stop_loss_rules"]
    reasons: List[str] = []
    warnings: List[str] = []
    rec = "HOLD"

    if not pos.logic_intact:
        # 硬止损：逻辑破坏 → 立即减仓或清仓
        reasons.append(
            "[规则7·硬止损] 该标的长期逻辑已判断为破坏"
            "（核心产品竞争力消失 / 商业模式根本变化 / 管理层诚信问题 / 赛道萎缩）。"
            "建议立即减仓或清仓。止损 ≠ 跌了就卖，逻辑破坏才是止损信号。"
        )
        rec = "SELL"

    elif pos.drawdown_pct <= -cfg["soft_stop_review_trigger_pct"]:
        # 软止损：回撤 ≥30% → 强制复核，不自动卖出
        warnings.append(
            f"[规则7·软止损] {pos.name or pos.symbol} 从成本回撤已达"
            f" {abs(pos.drawdown_pct)*100:.1f}%"
            f"（阈值 {cfg['soft_stop_review_trigger_pct']*100:.0f}%）。"
            f"⚠️ 软止损 = 强制重新评估逻辑，而非机械卖出。"
            f"请重新判断：长期逻辑是否仍然成立？"
            f"若成立 → 视利空为左侧加仓机会；若破坏 → 触发硬止损。"
        )

    return rec, reasons, warnings


def _rule10_core_floor(pos: PositionState, action: TradeAction) -> Tuple[List[str], List[str]]:
    """规则 10：长期持仓底仓机制"""
    if not pos.is_core_holding or action.action_type not in _SELL_ACTIONS:
        return [], []

    cfg = RULES["single_asset_limits"]
    floor = cfg["core_holding_floor_pct"]
    projected = pos.weight - action.amount_pct
    warnings: List[str] = []

    if projected < floor:
        warnings.append(
            f"[规则10·底仓] {pos.name or pos.symbol} 为核心持仓（持有1年以上），"
            f"卖出后仓位将降至 {projected*100:.1f}%，"
            f"低于底仓下限 {floor*100:.0f}%。"
            f"建议保留至少 {floor*100:.0f}% 底仓，防止卖飞后心理负担影响后续决策，"
            f"并保留长期成长红利。"
        )

    return [], warnings


def _rule1_dynamic_position(
    pos: PositionState, market: MarketContext
) -> Tuple[str, List[str], List[str]]:
    """规则 1：动态仓位管理（逆向操作）"""
    reasons: List[str] = []
    warnings: List[str] = []
    rec = "HOLD"

    if not pos.logic_intact:
        warnings.append(
            "[规则1] ⚠️ 长期逻辑已判断为破坏，不适用逆向加仓原则。"
            "请参照规则7（止损）执行。"
        )
        return rec, reasons, warnings

    if market.trend == "up":
        reasons.append(
            "[规则1·逆向减仓] 利好密集 + 股价上涨趋势，"
            "建议分批减仓，落袋为安。长期看好公司 ≠ 仓位永远不动。"
        )
        rec = "REDUCE"

    elif market.trend == "down" and market.major_negative_event:
        reasons.append(
            "[规则1·逆向加仓] 利空冲击 + 股价下跌，但长期逻辑完好，"
            "建议逆向分批加仓。须配合规则2（左侧交易）和规则3（加仓节奏）执行。"
        )
        rec = "ADD"

    return rec, reasons, warnings


def _rule2_left_side(
    pos: PositionState, market: MarketContext,
    action: TradeAction, t_strategy_drawdown: float
) -> Tuple[str, List[str], List[str]]:
    """规则 2：左侧交易原则 + 做T策略"""
    reasons: List[str] = []
    warnings: List[str] = []
    rec = "HOLD"

    if action.action_type in _BUY_ACTIONS:
        # 做T策略（优先判断）
        if t_strategy_drawdown <= -0.20:
            reasons.append(
                f"[规则2·做T] 卖出后回调幅度 {abs(t_strategy_drawdown)*100:.0f}% ≥ 20%，"
                f"建议在买回原仓位基础上适度加仓。"
            )
            rec = "ADD"
        elif t_strategy_drawdown <= -0.10:
            reasons.append(
                f"[规则2·做T] 卖出后回调幅度 {abs(t_strategy_drawdown)*100:.0f}% ≈ 10%，"
                f"建议按原仓位比例买回，完成T字操作。"
            )
            rec = "BUY"
        # 常规左侧买入
        elif market.trend == "down":
            reasons.append(
                "[规则2·左侧买入] 当前处于下跌趋势，符合左侧建仓原则。"
                "不追求最低点，越跌越买，但须保留流动性（规则6）。"
            )
            rec = "BUY"
        elif market.trend == "up":
            warnings.append(
                "[规则2·追涨警告] 当前处于上涨趋势，不应追涨入场。"
                "左侧买入原则：在下跌左侧建仓，不在反弹已确立后追涨。"
            )
            rec = "HOLD"

    elif action.action_type in _SELL_ACTIONS:
        if market.trend == "up":
            reasons.append(
                "[规则2·左侧卖出] 当前处于上涨趋势，符合左侧卖出原则。"
                "涨得越多，卖得越多；不追求最高点。"
            )
            rec = "REDUCE"
        elif market.trend == "down":
            warnings.append(
                "[规则2·卖出警告] 已进入下跌趋势时才开始卖出，违反左侧卖出原则。"
                "请判断：若非逻辑破坏（规则7），下跌应视为加仓机会，而非卖出时机。"
            )

    return rec, reasons, warnings


# ─────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────

def run(
    portfolio: PortfolioState,
    position: PositionState,
    market: MarketContext,
    action: TradeAction,
    risk_result: RiskCheckResult,
    t_strategy_drawdown: float = 0.0,
) -> DecisionResult:
    """
    Decision Engine 主入口。

    参数：
        t_strategy_drawdown  做T参考回调幅度（负数，如 -0.12 = 卖出后回调 12%）
                             默认 0.0 = 未使用做T策略

    ⚠️ 本函数仅在 Risk Engine 未 BLOCK、Psychology Engine 未 COOLDOWN 时被调用。
    """
    all_reasons: List[str] = []
    all_warnings: List[str] = []
    recommendations: List[str] = []

    # 规则7：止损（最高优先级——逻辑破坏直接推翻其他建议）
    r7_rec, r7_reasons, r7_warnings = _rule7_stop_loss(position)
    all_reasons.extend(r7_reasons)
    all_warnings.extend(r7_warnings)
    if r7_rec != "HOLD":
        recommendations.append(r7_rec)

    # 规则10：底仓保护
    _, r10_warnings = _rule10_core_floor(position, action)
    all_warnings.extend(r10_warnings)

    # 规则1：动态仓位管理
    r1_rec, r1_reasons, r1_warnings = _rule1_dynamic_position(position, market)
    all_reasons.extend(r1_reasons)
    all_warnings.extend(r1_warnings)
    if r1_rec != "HOLD":
        recommendations.append(r1_rec)

    # 规则2：左侧交易 + 做T
    r2_rec, r2_reasons, r2_warnings = _rule2_left_side(
        position, market, action, t_strategy_drawdown
    )
    all_reasons.extend(r2_reasons)
    all_warnings.extend(r2_warnings)
    if r2_rec != "HOLD":
        recommendations.append(r2_rec)

    # Risk Engine WARNING 信息追加（不覆盖，仅提示）
    all_warnings.extend(risk_result.warnings)

    # 综合建议：优先级 SELL > REDUCE > HOLD > ADD > BUY
    if recommendations:
        final_rec = min(recommendations, key=lambda r: _PRIORITY.get(r, 99))
    else:
        final_rec = "HOLD"

    return DecisionResult(
        recommendation=final_rec,
        reasons=all_reasons,
        warnings=all_warnings,
    )
