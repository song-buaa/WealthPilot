"""
资产配置模块 — 纪律校验

每次生成配置方案前必须执行完整校验。
block 级别违规必须修正方案再输出，不得将违规方案直接展示给用户。
"""

from __future__ import annotations

from typing import Dict, List

from app.allocation.types import (
    AllocAssetClass,
    AllocationSnapshot,
    AssetTarget,
    ClassAllocation,
    DisciplineCheckResult,
    DisciplineViolation,
    ViolationSeverity,
)


def check_discipline(
    proposed_allocations: Dict[str, float],
    current_snapshot: AllocationSnapshot,
    targets: List[AssetTarget],
) -> DisciplineCheckResult:
    """
    校验拟议分配是否符合纪律约束。

    proposed_allocations: key = AllocAssetClass.value, value = 新增分配金额
    current_snapshot: 当前配置快照
    targets: 目标区间列表

    返回 DisciplineCheckResult，包含 violations 列表。
    """
    target_map = {t.asset_class.value: t for t in targets}
    violations: List[DisciplineViolation] = []

    new_total_added = sum(proposed_allocations.values())
    new_total = current_snapshot.total_investable_assets + new_total_added

    # 1. 货币下限校验
    cash_target = target_map.get("cash")
    if cash_target and cash_target.cash_min_amount:
        current_cash = current_snapshot.by_class.get("cash", ClassAllocation()).amount
        proposed_cash = proposed_allocations.get("cash", 0)
        final_cash = current_cash + proposed_cash
        if final_cash < cash_target.cash_min_amount:
            violations.append(DisciplineViolation(
                type="cash_below_min",
                message=f"建议后货币金额 {final_cash:.0f} 元 < 下限 {cash_target.cash_min_amount:.0f} 元，建议优先补充货币",
                severity=ViolationSeverity.WARNING,
            ))

    # 2. 另类上限校验（仅当新增分配中有另类时才按 block 级处理）
    alt_target = target_map.get("alt")
    if alt_target and new_total > 0:
        current_alt = current_snapshot.by_class.get("alt", ClassAllocation()).amount
        proposed_alt = proposed_allocations.get("alt", 0)
        final_alt_ratio = (current_alt + proposed_alt) / new_total
        if final_alt_ratio > alt_target.ceiling_ratio:
            # 只有新增分配导致超限才是 block，现有持仓本身超限是 warning
            severity = ViolationSeverity.BLOCK if proposed_alt > 0 else ViolationSeverity.WARNING
            violations.append(DisciplineViolation(
                type="alt_above_ceiling",
                message=f"另类占比 {final_alt_ratio:.1%} 超出上限 {alt_target.ceiling_ratio:.1%}",
                severity=severity,
            ))

    # 3. 衍生上限校验（同理）
    deriv_target = target_map.get("deriv")
    if deriv_target and new_total > 0:
        current_deriv = current_snapshot.by_class.get("deriv", ClassAllocation()).amount
        proposed_deriv = proposed_allocations.get("deriv", 0)
        final_deriv_ratio = (current_deriv + proposed_deriv) / new_total
        if final_deriv_ratio > deriv_target.ceiling_ratio:
            severity = ViolationSeverity.BLOCK if proposed_deriv > 0 else ViolationSeverity.WARNING
            violations.append(DisciplineViolation(
                type="deriv_above_ceiling",
                message=f"衍生占比 {final_deriv_ratio:.1%} 超出上限 {deriv_target.ceiling_ratio:.1%}",
                severity=severity,
            ))

    # 4. 集中度校验（单一标的 ≤ 纪律集中度阈值）
    # V1: 使用 Portfolio 表中的 max_single_stock_pct，默认 15%
    # 此校验在增量分配层面暂不做（需要标的级别数据），由 AI 输出时提示

    # 5. 杠杆校验
    # V1: 由纪律模块独立管理，此处不重复校验

    passed = all(v.severity != ViolationSeverity.BLOCK for v in violations)

    return DisciplineCheckResult(passed=passed, violations=violations)


def auto_correct_violations(
    allocations: Dict[str, float],
    current_snapshot: AllocationSnapshot,
    targets: List[AssetTarget],
) -> Dict[str, float]:
    """
    自动修正 block 级违规。
    - 另类超限：削减另类，多余部分按比例分配到固收/权益
    - 衍生超限：削减衍生，多余部分按比例分配到固收/权益
    """
    target_map = {t.asset_class.value: t for t in targets}
    corrected = dict(allocations)
    new_total_added = sum(corrected.values())
    new_total = current_snapshot.total_investable_assets + new_total_added

    if new_total <= 0:
        return corrected

    for cls_key in ["alt", "deriv"]:
        t = target_map.get(cls_key)
        if not t:
            continue

        current_amount = current_snapshot.by_class.get(cls_key, ClassAllocation()).amount
        proposed = corrected.get(cls_key, 0)
        if proposed <= 0:
            continue

        final_ratio = (current_amount + proposed) / new_total
        if final_ratio > t.ceiling_ratio:
            max_add = max(0, t.ceiling_ratio * new_total - current_amount)
            excess = proposed - max_add
            corrected[cls_key] = max_add

            # 超限部分按比例分配到固收和权益
            fixed_alloc = corrected.get("fixed", 0)
            equity_alloc = corrected.get("equity", 0)
            redist_total = fixed_alloc + equity_alloc
            if redist_total > 0 and excess > 0:
                corrected["fixed"] = fixed_alloc + excess * (fixed_alloc / redist_total)
                corrected["equity"] = equity_alloc + excess * (equity_alloc / redist_total)
            elif excess > 0:
                # 如果固收和权益都没有分配，均分
                corrected["fixed"] = corrected.get("fixed", 0) + excess / 2
                corrected["equity"] = corrected.get("equity", 0) + excess / 2

    return corrected
