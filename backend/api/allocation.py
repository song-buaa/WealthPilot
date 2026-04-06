"""
资产配置模块 — API 路由

/api/allocation 下的所有端点。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional

from app import state
from app.allocation.types import (
    AllocationResult,
    AllocationSnapshot,
    AssetTarget,
    DeviationSnapshot,
    DisciplineCheckResult,
)
from backend.services.allocation_service import (
    classify_asset,
    compute_increment_plan,
    compute_initial_plan,
    get_deviation,
    get_snapshot,
    get_targets,
    get_unclassified_holdings,
    run_discipline_check,
)

router = APIRouter()


# ── 配置状态接口 ─────────────────────────────────────────

@router.get("/snapshot")
def api_snapshot() -> dict:
    """获取当前五大类配置快照"""
    pid = state.portfolio_id
    snapshot = get_snapshot(pid)
    return snapshot.model_dump()


@router.get("/deviation")
def api_deviation() -> dict:
    """获取偏离度快照"""
    pid = state.portfolio_id
    deviation = get_deviation(pid)
    return deviation.model_dump()


@router.get("/targets")
def api_targets() -> list:
    """获取目标区间配置"""
    targets = get_targets()
    return [t.model_dump() for t in targets]


# ── 增量分配计算 ─────────────────────────────────────────

class IncrementPlanRequest(BaseModel):
    increment_amount: float
    user_requested_deriv: bool = False


@router.post("/increment-plan")
def api_increment_plan(req: IncrementPlanRequest) -> dict:
    """计算增量资金分配方案"""
    if req.increment_amount <= 0:
        raise HTTPException(400, "增量金额必须大于 0")

    pid = state.portfolio_id
    result = compute_increment_plan(
        portfolio_id=pid,
        increment_amount=req.increment_amount,
        user_requested_deriv=req.user_requested_deriv,
    )
    return result.model_dump()


class InitialPlanRequest(BaseModel):
    total_amount: float


@router.post("/initial-plan")
def api_initial_plan(req: InitialPlanRequest) -> dict:
    """计算初始配置方案（从零开始）"""
    if req.total_amount <= 0:
        raise HTTPException(400, "总金额必须大于 0")

    result = compute_initial_plan(total_amount=req.total_amount)
    return result.model_dump()


# ── 纪律校验 ─────────────────────────────────────────────

class DisciplineCheckRequest(BaseModel):
    proposed_allocation: Dict[str, float]


@router.post("/discipline-check")
def api_discipline_check(req: DisciplineCheckRequest) -> dict:
    """对拟议分配方案执行纪律校验"""
    pid = state.portfolio_id
    result = run_discipline_check(pid, req.proposed_allocation)
    return result.model_dump()


# ── 资产归类 ─────────────────────────────────────────────

class ClassifyAssetRequest(BaseModel):
    holding_id: int
    asset_class: str


@router.post("/classify-asset")
def api_classify_asset(req: ClassifyAssetRequest) -> dict:
    """手动归类资产"""
    success = classify_asset(req.holding_id, req.asset_class)
    if not success:
        raise HTTPException(404, "持仓不存在或分类无效")
    return {"success": True}


@router.get("/unclassified-holdings")
def api_unclassified_holdings() -> list:
    """获取未分类持仓列表"""
    pid = state.portfolio_id
    return get_unclassified_holdings(pid)


# ── AI 对话已迁移到投资决策后端（/api/decision/chat）──────
# AssetAllocation 意图现在由 decision_service.py 统一处理
