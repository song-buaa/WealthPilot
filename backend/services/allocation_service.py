"""
资产配置模块 — 服务层

编排数据库查询 → 计算引擎 → 返回结果，不含 AI 逻辑。
"""

from __future__ import annotations

from typing import List, Optional

from app.allocation.calculator import (
    allocate_increment,
    build_allocation_snapshot,
    build_deviation_snapshot,
    calc_initial_allocation,
)
from app.allocation.classifier import classify_position
from app.allocation.defaults import get_default_targets
from app.allocation.discipline import auto_correct_violations, check_discipline
from app.allocation.types import (
    AllocAssetClass,
    AllocationResult,
    AllocationSnapshot,
    AssetTarget,
    CN_TO_ALLOC,
    DeviationSnapshot,
    DisciplineCheckResult,
)
from app.database import get_session
from app.models import Position


def get_snapshot(portfolio_id: int) -> AllocationSnapshot:
    """获取当前配置快照"""
    with get_session() as session:
        positions = (
            session.query(Position)
            .filter(Position.portfolio_id == portfolio_id)
            .all()
        )
        return build_allocation_snapshot(positions, segment="投资")


def get_targets() -> List[AssetTarget]:
    """
    获取目标区间。
    V1: 返回固定的默认目标（稳健偏进取）。
    后续迭代时可根据用户画像动态生成。
    """
    return get_default_targets()


def get_deviation(portfolio_id: int) -> DeviationSnapshot:
    """获取偏离度快照"""
    snapshot = get_snapshot(portfolio_id)
    targets = get_targets()
    return build_deviation_snapshot(snapshot, targets)


def compute_increment_plan(
    portfolio_id: int,
    increment_amount: float,
    user_requested_deriv: bool = False,
) -> AllocationResult:
    """
    计算增量分配方案。
    含纪律校验 + block 级自动修正。
    """
    snapshot = get_snapshot(portfolio_id)
    targets = get_targets()

    result = allocate_increment(
        new_money=increment_amount,
        current=snapshot,
        targets=targets,
        user_requested_deriv=user_requested_deriv,
    )

    # 纪律校验
    check = check_discipline(result.allocations, snapshot, targets)

    if not check.passed:
        # block 级违规自动修正
        corrected = auto_correct_violations(result.allocations, snapshot, targets)
        # 用修正后的分配重建 plan_items
        from app.allocation.calculator import round_allocation, _build_result
        result = _build_result(corrected, increment_amount, snapshot, targets)
        # 重新校验
        check = check_discipline(result.allocations, snapshot, targets)

    result.discipline_check = check
    return result


def compute_initial_plan(
    total_amount: float,
) -> AllocationResult:
    """
    计算初始配置方案（从零开始）。
    货币类默认只分配到下限。
    """
    targets = get_targets()
    result = calc_initial_allocation(total_amount, targets)

    # 构建空快照用于纪律校验
    empty_snapshot = AllocationSnapshot(total_investable_assets=0)
    check = check_discipline(result.allocations, empty_snapshot, targets)
    result.discipline_check = check
    return result


def run_discipline_check(
    portfolio_id: int,
    proposed_allocations: dict,
) -> DisciplineCheckResult:
    """对任意拟议分配执行纪律校验"""
    snapshot = get_snapshot(portfolio_id)
    targets = get_targets()
    return check_discipline(proposed_allocations, snapshot, targets)


def classify_asset(
    holding_id: int,
    asset_class: str,
) -> bool:
    """
    手动归类：更新持仓的 asset_class 字段。
    asset_class 传入英文值（如 'equity'），转换为中文存储。
    """
    from app.allocation.types import ALLOC_TO_CN
    cn_class = ALLOC_TO_CN.get(AllocAssetClass(asset_class))
    if not cn_class:
        return False

    with get_session() as session:
        pos = session.query(Position).filter(Position.id == holding_id).first()
        if not pos:
            return False
        pos.asset_class = cn_class
        session.commit()
        return True


def get_unclassified_holdings(portfolio_id: int) -> list:
    """获取未分类持仓列表"""
    with get_session() as session:
        positions = (
            session.query(Position)
            .filter(Position.portfolio_id == portfolio_id)
            .all()
        )
        unclassified = []
        for pos in positions:
            if getattr(pos, "segment", "投资") != "投资":
                continue
            ac = classify_position(pos.asset_class, pos.name)
            if ac == AllocAssetClass.UNCLASSIFIED:
                unclassified.append({
                    "id": pos.id,
                    "name": pos.name,
                    "ticker": pos.ticker,
                    "platform": pos.platform,
                    "asset_class": pos.asset_class,
                    "market_value_cny": pos.market_value_cny,
                })
        return unclassified
