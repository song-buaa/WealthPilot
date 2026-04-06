"""
资产配置模块 — 核心计算逻辑

纯函数，无 DB 依赖。包含：
- 配置快照构建
- 偏离度计算
- 增量分配算法
- 初始配置分配
- 金额取整
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, List, Optional

from app.allocation.types import (
    AllocAssetClass,
    AllocationPlanItem,
    AllocationResult,
    AllocationSnapshot,
    AssetTarget,
    CashDeviation,
    CashStatus,
    ClassAllocation,
    ClassDeviation,
    DeviationLevel,
    DeviationSnapshot,
    OverallStatus,
    PriorityAction,
    ALLOC_LABEL,
    FIVE_CLASSES,
    FOUR_NON_CASH,
)
from app.allocation.classifier import classify_position


# ── 配置快照构建 ──────────────────────────────────────────

def build_allocation_snapshot(
    positions: list,
    segment: str = "投资",
) -> AllocationSnapshot:
    """
    从持仓列表构建配置快照。

    positions: 数据库 Position 对象列表（需有 asset_class, market_value_cny, segment, name 属性）
    segment: 只统计此 segment 的持仓
    """
    class_amounts: Dict[str, float] = {cls.value: 0.0 for cls in FIVE_CLASSES}
    unclassified_amount = 0.0

    for pos in positions:
        if segment and getattr(pos, "segment", "投资") != segment:
            continue

        value = getattr(pos, "market_value_cny", 0) or 0
        if value <= 0:
            continue

        ac = classify_position(
            getattr(pos, "asset_class", ""),
            getattr(pos, "name", ""),
        )

        if ac == AllocAssetClass.UNCLASSIFIED:
            unclassified_amount += value
        else:
            class_amounts[ac.value] += value

    total = sum(class_amounts.values())

    by_class = {}
    for cls in FIVE_CLASSES:
        amt = class_amounts[cls.value]
        by_class[cls.value] = ClassAllocation(
            amount=amt,
            ratio=amt / total if total > 0 else 0.0,
        )

    return AllocationSnapshot(
        snapshot_at=datetime.now(),
        total_investable_assets=total,
        by_class=by_class,
        unclassified_amount=unclassified_amount,
        has_unclassified=unclassified_amount > 0,
    )


# ── 货币类状态判断 ────────────────────────────────────────

def calc_cash_status(current_amount: float, target: AssetTarget) -> CashStatus:
    """
    V1 说明：cashMaxAmount 仅用于展示目标区间上限，不参与状态计算和补配优先级判断。
    状态计算只基于 cashMinAmount。
    """
    min_amount = target.cash_min_amount or 0
    if current_amount >= min_amount:
        return CashStatus.SUFFICIENT
    if current_amount >= min_amount * 0.8:
        return CashStatus.LOW
    return CashStatus.INSUFFICIENT


# ── 偏离度计算 ────────────────────────────────────────────

def calc_deviation_level(
    deviation: float,
    is_above_floor: bool,
    is_below_ceiling: bool,
    range_width: float,
) -> DeviationLevel:
    """
    deviation = currentRatio - targetMid
    range_width = ceiling - floor
    """
    if not is_below_ceiling:
        return DeviationLevel.ALERT       # 超上限
    if not is_above_floor:
        return DeviationLevel.ALERT       # 低于下限

    abs_dev = abs(deviation)
    if range_width > 0 and abs_dev > range_width / 2 * 0.6:
        return DeviationLevel.SIGNIFICANT
    if abs_dev > 0.001:  # 浮点精度
        return DeviationLevel.MILD
    return DeviationLevel.NORMAL


def build_deviation_snapshot(
    snapshot: AllocationSnapshot,
    targets: List[AssetTarget],
) -> DeviationSnapshot:
    """从配置快照和目标区间构建偏离度快照"""
    target_map = {t.asset_class.value: t for t in targets}
    by_class: Dict[str, ClassDeviation] = {}

    # 非货币四类的偏离计算
    for cls in FOUR_NON_CASH:
        t = target_map.get(cls.value)
        if not t:
            continue

        current_ratio = snapshot.by_class.get(cls.value, ClassAllocation()).ratio
        mid = t.mid_ratio if t.mid_ratio is not None else (
            ((t.floor_ratio or 0) + t.ceiling_ratio) / 2
        )
        deviation = current_ratio - mid
        floor = t.floor_ratio if t.floor_ratio is not None else 0.0
        ceiling = t.ceiling_ratio
        is_above_floor = current_ratio >= floor
        is_below_ceiling = current_ratio <= ceiling
        range_width = ceiling - floor

        by_class[cls.value] = ClassDeviation(
            current_ratio=current_ratio,
            target_mid=mid,
            deviation=deviation,
            is_above_floor=is_above_floor,
            is_below_ceiling=is_below_ceiling,
            is_in_range=is_above_floor and is_below_ceiling,
            deviation_level=calc_deviation_level(deviation, is_above_floor, is_below_ceiling, range_width),
        )

    # 货币类状态
    cash_target = target_map.get(AllocAssetClass.CASH.value)
    cash_amount = snapshot.by_class.get(AllocAssetClass.CASH.value, ClassAllocation()).amount
    cash_status = calc_cash_status(cash_amount, cash_target) if cash_target else CashStatus.SUFFICIENT

    cash_dev = CashDeviation(
        current_amount=cash_amount,
        min_amount=cash_target.cash_min_amount if cash_target else 0,
        max_amount=cash_target.cash_max_amount if cash_target else 0,
        status=cash_status,
    )

    # 整体状态
    overall = calc_overall_status(by_class, cash_status)
    priority = calc_priority_action(overall, by_class)

    return DeviationSnapshot(
        by_class=by_class,
        cash=cash_dev,
        overall_status=overall,
        priority_action=priority,
    )


def calc_overall_status(
    deviations: Dict[str, ClassDeviation],
    cash_status: CashStatus,
) -> OverallStatus:
    any_alert = any(d.deviation_level == DeviationLevel.ALERT for d in deviations.values())
    any_significant = any(d.deviation_level == DeviationLevel.SIGNIFICANT for d in deviations.values())
    any_mild = any(d.deviation_level == DeviationLevel.MILD for d in deviations.values())

    if any_alert or cash_status == CashStatus.INSUFFICIENT:
        return OverallStatus.ALERT
    if any_significant or cash_status == CashStatus.LOW:
        return OverallStatus.SIGNIFICANT_DEVIATION
    if any_mild:
        return OverallStatus.MILD_DEVIATION
    return OverallStatus.ON_TARGET


def calc_priority_action(
    overall_status: OverallStatus,
    deviations: Dict[str, ClassDeviation],
) -> PriorityAction:
    if overall_status == OverallStatus.ALERT:
        return PriorityAction.URGENT_ATTENTION

    if overall_status == OverallStatus.SIGNIFICANT_DEVIATION:
        # 判断是否所有偏离都能靠补仓修复（不需要卖出）
        has_above_ceiling = any(
            not d.is_below_ceiling for d in deviations.values()
        )
        if has_above_ceiling:
            return PriorityAction.URGENT_ATTENTION
        return PriorityAction.CORRECT_WITH_INFLOW

    if overall_status == OverallStatus.MILD_DEVIATION:
        return PriorityAction.CORRECT_WITH_INFLOW

    return PriorityAction.NO_ACTION


# ── 增量资金分配算法 ──────────────────────────────────────

def allocate_increment(
    new_money: float,
    current: AllocationSnapshot,
    targets: List[AssetTarget],
    user_requested_deriv: bool = False,
) -> AllocationResult:
    """
    增量资金分配，按优先级：
    1. 货币类低于下限 → 优先补足
    2. 另类低于下限 → 主动补配
    3. 固收/权益低于下限 → 按缺口比例分配
    4. 按偏离中值缺口比例分配剩余（衍生类默认不参与）
    """
    target_map = {t.asset_class.value: t for t in targets}
    allocation: Dict[str, float] = {}
    remaining = new_money
    total = current.total_investable_assets

    # 第一优先级：货币类低于下限时，优先补足
    cash_t = target_map.get("cash")
    cash_amount = current.by_class.get("cash", ClassAllocation()).amount
    if cash_t and cash_t.cash_min_amount and cash_amount < cash_t.cash_min_amount:
        shortfall = cash_t.cash_min_amount - cash_amount
        alloc = min(remaining, shortfall)
        allocation["cash"] = alloc
        remaining -= alloc
    if remaining <= 0:
        return _build_result(allocation, new_money, current, targets)

    # 第二优先级：另类低于下限
    alt_t = target_map.get("alt")
    if alt_t and alt_t.floor_ratio:
        alt_ratio = current.by_class.get("alt", ClassAllocation()).ratio
        if alt_ratio < alt_t.floor_ratio:
            gap = (alt_t.floor_ratio - alt_ratio) * (total + new_money)
            alloc = min(remaining, gap)
            allocation["alt"] = alloc
            remaining -= alloc
    if remaining <= 0:
        return _build_result(allocation, new_money, current, targets)

    # 第三优先级：固收/权益低于下限
    below_floor = []
    for cls in ["fixed", "equity"]:
        t = target_map.get(cls)
        if t and t.floor_ratio:
            ratio = current.by_class.get(cls, ClassAllocation()).ratio
            if ratio < t.floor_ratio:
                gap = (t.floor_ratio - ratio) * (total + new_money)
                below_floor.append((cls, gap))

    if below_floor:
        total_floor_gap = sum(g for _, g in below_floor)
        for cls, gap in below_floor:
            if total_floor_gap > 0:
                portion = remaining * (gap / total_floor_gap)
                alloc = min(portion, gap)
                allocation[cls] = allocation.get(cls, 0) + alloc
                remaining -= alloc
    if remaining <= 0:
        return _build_result(allocation, new_money, current, targets)

    # 第四优先级：按偏离中值的缺口比例分配剩余
    eligible = []
    for cls in ["fixed", "equity", "alt"]:
        t = target_map.get(cls)
        if t:
            ratio = current.by_class.get(cls, ClassAllocation()).ratio
            if ratio < t.ceiling_ratio:
                eligible.append(cls)

    if user_requested_deriv:
        deriv_t = target_map.get("deriv")
        if deriv_t:
            deriv_ratio = current.by_class.get("deriv", ClassAllocation()).ratio
            if deriv_ratio < deriv_t.ceiling_ratio:
                eligible.append("deriv")

    mid_gaps = []
    for cls in eligible:
        t = target_map.get(cls)
        if t and t.mid_ratio is not None:
            ratio = current.by_class.get(cls, ClassAllocation()).ratio
            gap = max(0, (t.mid_ratio - ratio) * (total + new_money))
            mid_gaps.append((cls, gap))
        elif cls == "deriv" and t:
            # 衍生类无中值，用 ceiling/2 作为参考
            ratio = current.by_class.get(cls, ClassAllocation()).ratio
            gap = max(0, (t.ceiling_ratio / 2 - ratio) * (total + new_money))
            mid_gaps.append((cls, gap))

    total_mid_gap = sum(g for _, g in mid_gaps)
    if total_mid_gap > 0 and remaining > 0:
        for cls, gap in mid_gaps:
            portion = remaining * (gap / total_mid_gap)
            allocation[cls] = allocation.get(cls, 0) + portion

    # 纪律校验：衍生品上限
    deriv_t = target_map.get("deriv")
    if deriv_t and allocation.get("deriv", 0) > 0:
        deriv_current = current.by_class.get("deriv", ClassAllocation()).amount
        proposed_deriv_ratio = (deriv_current + allocation.get("deriv", 0)) / (total + new_money)
        if proposed_deriv_ratio > deriv_t.ceiling_ratio:
            max_add = deriv_t.ceiling_ratio * (total + new_money) - deriv_current
            max_add = max(0, max_add)
            excess = allocation.get("deriv", 0) - max_add
            allocation["deriv"] = max_add
            # 超限部分按比例重新分配到固收和权益
            fixed_alloc = allocation.get("fixed", 0)
            equity_alloc = allocation.get("equity", 0)
            redist_total = fixed_alloc + equity_alloc
            if redist_total > 0 and excess > 0:
                allocation["fixed"] = fixed_alloc + excess * (fixed_alloc / redist_total)
                allocation["equity"] = equity_alloc + excess * (equity_alloc / redist_total)

    return _build_result(allocation, new_money, current, targets)


def calc_initial_allocation(
    total_money: float,
    targets: List[AssetTarget],
) -> AllocationResult:
    """
    初始配置：货币类默认只分配到下限，不追求中值。
    剩余按固收/权益/另类的目标中值比例分配，衍生类不纳入。
    """
    target_map = {t.asset_class.value: t for t in targets}
    allocation: Dict[str, float] = {}

    # 货币类分配到下限
    cash_t = target_map.get("cash")
    cash_alloc = min(total_money, cash_t.cash_min_amount or 0) if cash_t else 0
    allocation["cash"] = cash_alloc
    remaining = total_money - cash_alloc

    if remaining <= 0:
        # 构建一个空快照用于 _build_result
        empty_snapshot = AllocationSnapshot(total_investable_assets=0)
        return _build_result(allocation, total_money, empty_snapshot, targets)

    # 按三类（固收/权益/另类）的 midRatio 分配
    distributable = []
    for cls in ["fixed", "equity", "alt"]:
        t = target_map.get(cls)
        if t and t.mid_ratio:
            distributable.append((cls, t.mid_ratio))

    total_mid = sum(r for _, r in distributable)
    if total_mid > 0:
        for cls, mid in distributable:
            allocation[cls] = remaining * (mid / total_mid)

    empty_snapshot = AllocationSnapshot(total_investable_assets=0)
    return _build_result(allocation, total_money, empty_snapshot, targets)


# ── 金额取整 ─────────────────────────────────────────────

def round_allocation(
    allocations: Dict[str, float],
    granularity: int = 1000,
) -> Dict[str, float]:
    """
    所有建议金额按 granularity 粒度向下取整。
    取整误差统一加到优先级最高的资产类别。

    优先级顺序：cash > alt > fixed > equity > deriv
    """
    priority_order = ["cash", "alt", "fixed", "equity", "deriv"]

    total_error = 0.0
    rounded = {}
    highest_priority_class = None

    for cls in priority_order:
        if cls in allocations:
            amount = allocations[cls]
            rounded_amount = math.floor(amount / granularity) * granularity
            rounded[cls] = rounded_amount
            total_error += amount - rounded_amount
            if highest_priority_class is None:
                highest_priority_class = cls

    # 其他未在优先级列表中的类别
    for cls, amount in allocations.items():
        if cls not in rounded:
            rounded_amount = math.floor(amount / granularity) * granularity
            rounded[cls] = rounded_amount
            total_error += amount - rounded_amount

    # 误差归并
    if highest_priority_class and total_error >= granularity:
        rounded[highest_priority_class] += math.floor(total_error / granularity) * granularity

    return rounded


# ── 内部辅助 ─────────────────────────────────────────────

def _build_result(
    allocation: Dict[str, float],
    total_amount: float,
    current: AllocationSnapshot,
    targets: List[AssetTarget],
) -> AllocationResult:
    """构建 AllocationResult，包含 plan_items"""
    target_map = {t.asset_class.value: t for t in targets}

    # 先取整
    rounded = round_allocation(allocation)

    plan_items = []
    for cls in ["cash", "fixed", "equity", "alt", "deriv"]:
        if cls not in rounded or rounded[cls] <= 0:
            continue

        t = target_map.get(cls)
        current_ratio = current.by_class.get(cls, ClassAllocation()).ratio
        mid = t.mid_ratio if t and t.mid_ratio else 0

        new_total = current.total_investable_assets + total_amount
        suggested_ratio = (
            (current.by_class.get(cls, ClassAllocation()).amount + rounded[cls]) / new_total
            if new_total > 0 else 0
        )

        plan_items.append(AllocationPlanItem(
            asset_class=cls,
            label=ALLOC_LABEL.get(AllocAssetClass(cls), cls),
            current_ratio=current_ratio,
            target_mid=mid,
            deviation=current_ratio - mid if mid else 0,
            suggested_amount=rounded[cls],
            suggested_ratio=suggested_ratio,
        ))

    return AllocationResult(
        total_amount=total_amount,
        allocations=rounded,
        plan_items=plan_items,
    )
