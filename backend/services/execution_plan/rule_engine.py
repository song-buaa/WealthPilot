"""
执行计划规则引擎 — 确定性产出权威 plan dict。

输入: FactorSnapshot + 决策 side + 目标 + 当前持仓 + 锚点价
输出: plan dict (含 tranches、plan_summary_block、constraints_applied)
不调 LLM、不写库、不碰前端。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from app.discipline.config import get_rules
from backend.services.execution_plan import config as ep_config
from backend.services.execution_plan.hk_tick import round_to_tick, get_tick_size

logger = logging.getLogger(__name__)


@dataclass
class PlanInput:
    """规则引擎输入。"""
    symbol: str
    market: str                          # US / HK
    side: str                            # BUY / ADD / REDUCE / SELL
    target_position_pct: float           # 目标仓位占比 (0~1)
    current_position_pct: float = 0.0    # 当前仓位占比
    current_price: float = 0.0           # 当前价格
    total_assets: float = 0.0            # 总投资性资产
    user_anchor_prices: list[float] = field(default_factory=list)
    quick_mode: bool = False             # 快速单笔模式
    batch_count_override: Optional[int] = None  # Step C: 用户指定批数覆盖
    # 因子
    atr14: Optional[float] = None
    volatility_annual: Optional[float] = None
    price_percentile: Optional[float] = None
    drawdown_from_high: Optional[float] = None


@dataclass
class TranchePlan:
    """单批计划。"""
    sequence: int
    quantity: int
    trigger_type: str       # IMMEDIATE / PRICE_BELOW / PRICE_ABOVE
    trigger_price: Optional[float]
    limit_price: Optional[float]
    order_type: str         # LIMIT
    min_interval_days: int


@dataclass
class PlanResult:
    """规则引擎输出。"""
    tranches: list[TranchePlan]
    plan_summary_block: dict    # 结构化数字摘要(validator 校验对象)
    constraints_applied: dict   # 实际套用的约束参数
    warnings: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)  # 硬约束违反(被修正)
    tick_degraded: bool = False
    insufficient_data: bool = False       # 全因子降级且无锚点价
    insufficient_data_reason: str = ""    # 拒绝原因(给前端展示)


def generate_plan(inp: PlanInput, factor_snapshot: dict) -> PlanResult:
    """核心入口: 确定性产出执行计划。"""
    rules = get_rules()
    sal = rules.get("single_asset_limits", {})
    ps = rules.get("position_sizing", {})
    sl = rules.get("stop_loss_rules", {})

    max_position_pct = sal.get("max_position_pct", 0.40)
    max_single_add_pct = ps.get("max_single_add_pct", 0.10)
    min_batches_required = ps.get("min_batches_required", 2)
    min_interval_days = ps.get("min_interval_between_adds_days", 1)
    soft_stop_pct = sl.get("soft_stop_review_trigger_pct", 0.30)

    warnings: list[str] = []
    violations: list[str] = []

    # ── (D) 硬约束检查 ──
    is_buy_side = inp.side in ("BUY", "ADD")
    final_pct = inp.target_position_pct if is_buy_side else inp.current_position_pct - inp.target_position_pct
    if is_buy_side and inp.target_position_pct > max_position_pct:
        violations.append(
            f"目标仓位 {inp.target_position_pct:.0%} 超过纪律上限 {max_position_pct:.0%}，"
            f"已修正为 {max_position_pct:.0%}"
        )
        inp.target_position_pct = max_position_pct

    # 回撤复盘线
    requires_review = False
    if inp.drawdown_from_high is not None and abs(inp.drawdown_from_high) >= soft_stop_pct:
        requires_review = True
        warnings.append(
            f"当前回撤 {inp.drawdown_from_high:.1%} 达到纪律复盘线 {soft_stop_pct:.0%}，"
            f"标记 requires_review，不自动加仓"
        )

    # ── 增量计算 ──
    increment_pct = abs(inp.target_position_pct - inp.current_position_pct)
    if increment_pct <= 0:
        return PlanResult(
            tranches=[], plan_summary_block={}, constraints_applied={},
            warnings=["目标仓位与当前相同，无需执行"],
        )

    # ── 全因子降级守卫: 无因子且无锚点价 → 拒绝生成空壳计划 ──
    has_factors = (inp.atr14 is not None or inp.volatility_annual is not None
                   or inp.price_percentile is not None)
    has_price = inp.current_price and inp.current_price > 0
    has_anchors = bool(inp.user_anchor_prices)

    if not has_factors and not has_anchors:
        return PlanResult(
            tranches=[],
            plan_summary_block={},
            constraints_applied={},
            warnings=[],
            violations=[],
            insufficient_data=True,
            insufficient_data_reason=(
                "当前无行情/K线数据（ATR/波动率/分位均不可用），无法生成有依据的分批计划。"
                "请提供你的目标价位（锚点价），或稍后行情恢复后重试。"
            ),
        )

    # ── (C') 退化单笔豁免 ──
    n_one_exempt = (
        inp.side in ("BUY", "SELL")
        and increment_pct <= max_single_add_pct
        and inp.quick_mode
    )

    # ── (A)(C) 批数 N ──
    if inp.user_anchor_prices:
        # 锚点价优先
        n = len(inp.user_anchor_prices)
    elif inp.batch_count_override is not None:
        # Step C: 用户指定批数（仍受纪律约束）
        n = max(min_batches_required, min(inp.batch_count_override, ep_config.MAX_BATCHES))
        if inp.batch_count_override > ep_config.MAX_BATCHES:
            violations.append(f"指定{inp.batch_count_override}批超过纪律上限，已按{ep_config.MAX_BATCHES}批生成")
        if inp.batch_count_override < min_batches_required and not n_one_exempt:
            violations.append(f"指定{inp.batch_count_override}批低于纪律最少{min_batches_required}批，已按{min_batches_required}批生成")
    elif n_one_exempt:
        n = 1
    else:
        n_min = math.ceil(increment_pct / max_single_add_pct)
        n = max(n_min, min_batches_required)
        if inp.volatility_annual and inp.volatility_annual > ep_config.VOL_HIGH_THRESHOLD:
            n += 1
        n = min(n, ep_config.MAX_BATCHES)

    # ── 每批数量 ──
    total_shares = _calc_total_shares(inp)
    per_batch = max(1, total_shares // n)
    # 最后一批补齐余数
    batch_quantities = [per_batch] * n
    remainder = total_shares - per_batch * n
    if remainder > 0:
        batch_quantities[-1] += remainder

    # 每批约束: ≤ max_single_add_pct
    max_single_shares = _pct_to_shares(max_single_add_pct, inp)
    for i, qty in enumerate(batch_quantities):
        if qty > max_single_shares > 0:
            violations.append(f"第{i+1}批 {qty}股 超过单次上限 {max_single_shares}股，已修正")
            batch_quantities[i] = max_single_shares

    # ── (A)(B) 触发价 ──
    trigger_prices = _generate_trigger_prices(inp, n, warnings)
    tick_degraded = False

    # tick 取整
    for i, tp in enumerate(trigger_prices):
        if tp is not None:
            rounded, degraded = round_to_tick(tp, inp.market)
            trigger_prices[i] = rounded
            if degraded:
                tick_degraded = True

    # 限价 = 触发价 ± buffer
    limit_prices = _calc_limit_prices(trigger_prices, inp.side, inp.market)

    # ── 组装 tranches ──
    tranches = []
    for i in range(n):
        tp = trigger_prices[i] if i < len(trigger_prices) else None
        lp = limit_prices[i] if i < len(limit_prices) else None

        if i == 0 and not inp.user_anchor_prices and n_one_exempt:
            trigger_type = "IMMEDIATE"
        elif tp is None:
            trigger_type = "IMMEDIATE"
        elif is_buy_side:
            trigger_type = "PRICE_BELOW"
        else:
            trigger_type = "PRICE_ABOVE"

        tranches.append(TranchePlan(
            sequence=i + 1,
            quantity=batch_quantities[i] if i < len(batch_quantities) else per_batch,
            trigger_type=trigger_type,
            trigger_price=tp,
            limit_price=lp,
            order_type="LIMIT",
            min_interval_days=min_interval_days,
        ))

    # ── constraints_applied ──
    constraints_applied = {
        "max_position_pct": max_position_pct,
        "max_single_add_pct": max_single_add_pct,
        "min_batches_required": min_batches_required,
        "min_interval_between_adds_days": min_interval_days,
        "soft_stop_review_trigger_pct": soft_stop_pct,
        "n_one_exempt": n_one_exempt,
        "requires_review": requires_review,
        # 引擎参数
        "atr_multiplier": ep_config.ATR_MULTIPLIER,
        "base_step_min_pct": ep_config.BASE_STEP_MIN_PCT,
        "base_step_max_pct": ep_config.BASE_STEP_MAX_PCT,
        "max_total_deviation_pct": ep_config.MAX_TOTAL_DEVIATION_PCT,
        "vol_high_threshold": ep_config.VOL_HIGH_THRESHOLD,
        "max_batches": ep_config.MAX_BATCHES,
        "high_percentile_threshold": ep_config.HIGH_PERCENTILE_THRESHOLD,
    }

    # ── plan_summary_block ──
    plan_summary_block = {
        "symbol": inp.symbol,
        "side": inp.side,
        "total_quantity": total_shares,
        "num_tranches": n,
        "tranches": [
            {
                "sequence": t.sequence,
                "quantity": t.quantity,
                "trigger_type": t.trigger_type,
                "trigger_price": t.trigger_price,
                "limit_price": t.limit_price,
            }
            for t in tranches
        ],
        "target_position_pct": inp.target_position_pct,
        "current_position_pct": inp.current_position_pct,
        "current_price": inp.current_price,
    }

    return PlanResult(
        tranches=tranches,
        plan_summary_block=plan_summary_block,
        constraints_applied=constraints_applied,
        warnings=warnings,
        violations=violations,
        tick_degraded=tick_degraded,
    )


# ── 内部: 触发价生成 ──────────────────────────────────────────


def _generate_trigger_prices(
    inp: PlanInput, n: int, warnings: list[str],
) -> list[Optional[float]]:
    """(A)(B) 优先级阶梯生成触发价列表。"""
    # (A-1) 用户锚点价 — 优先级最高,不依赖 current_price
    if inp.user_anchor_prices:
        prices = sorted(inp.user_anchor_prices)
        if inp.side in ("BUY", "ADD"):
            prices = sorted(prices, reverse=True)  # 买入:从高到低
        return prices[:n]

    P = inp.current_price
    if P <= 0:
        return [None] * n

    # (A-3) 自动生成
    is_buy = inp.side in ("BUY", "ADD")

    # base_step%
    atr_pct = (inp.atr14 / P) if inp.atr14 and P > 0 else 0.05
    base_step = max(
        ep_config.BASE_STEP_MIN_PCT,
        min(ep_config.ATR_MULTIPLIER * atr_pct, ep_config.BASE_STEP_MAX_PCT),
    )

    # 分位保护: 高分位时首档延后
    skip_first = False
    if is_buy and inp.price_percentile is not None:
        if inp.price_percentile > ep_config.HIGH_PERCENTILE_THRESHOLD:
            skip_first = True
            warnings.append(
                f"价格分位 {inp.price_percentile:.2f} > {ep_config.HIGH_PERCENTILE_THRESHOLD}，"
                f"首档延后避免高位密集挂单"
            )

    prices: list[Optional[float]] = []
    for i in range(n):
        step_idx = i + (1 if skip_first else 0)

        if i == 0 and not skip_first:
            # 首档贴近现价
            offset = base_step * 0.3  # 首档只偏 30% 的 base_step
        else:
            offset = base_step * step_idx

        # 最大偏离约束
        offset = min(offset, ep_config.MAX_TOTAL_DEVIATION_PCT)

        if is_buy:
            tp = P * (1 - offset)
        else:
            tp = P * (1 + offset)

        prices.append(round(tp, 4))

    # 最小价差约束
    min_tick, _ = get_tick_size(P, inp.market)
    min_spread = max(min_tick, P * ep_config.MIN_SPREAD_PCT)
    for i in range(1, len(prices)):
        if prices[i] is not None and prices[i - 1] is not None:
            gap = abs(prices[i] - prices[i - 1])
            if gap < min_spread:
                # 扩大间距
                if is_buy:
                    prices[i] = round(prices[i - 1] - min_spread, 4)
                else:
                    prices[i] = round(prices[i - 1] + min_spread, 4)

    return prices


# ── 内部: 限价计算 ─────────────────────────────────────────────


def _calc_limit_prices(
    trigger_prices: list[Optional[float]], side: str, market: str,
) -> list[Optional[float]]:
    """限价 = 触发价 ± buffer，按 tick 取整。"""
    is_buy = side in ("BUY", "ADD")
    result = []
    for tp in trigger_prices:
        if tp is None:
            result.append(None)
            continue
        buf = tp * ep_config.LIMIT_PRICE_BUFFER_PCT
        lp = tp + buf if is_buy else tp - buf  # 买入限价略高于触发价
        lp_rounded, _ = round_to_tick(lp, market)
        result.append(lp_rounded)
    return result


# ── 内部: 数量计算 ─────────────────────────────────────────────


def _calc_total_shares(inp: PlanInput) -> int:
    """从仓位百分比+总资产+现价算出总股数。"""
    # 参考价格: 优先用 current_price，降级时用第一个锚点价
    ref_price = inp.current_price
    if (not ref_price or ref_price <= 0) and inp.user_anchor_prices:
        ref_price = inp.user_anchor_prices[0]
    if not ref_price or ref_price <= 0 or inp.total_assets <= 0:
        return 0
    increment_pct = abs(inp.target_position_pct - inp.current_position_pct)
    value = inp.total_assets * increment_pct
    shares = int(value / ref_price)
    return max(shares, 1)


def _pct_to_shares(pct: float, inp: PlanInput) -> int:
    if inp.current_price <= 0 or inp.total_assets <= 0:
        return 0
    return int(inp.total_assets * pct / inp.current_price)
