"""
Orchestrator — 执行计划生成模块

对应工程PRD §3.3。

职责：
    根据 ExecutionContext 生成 ExecutionPlan，
    决定 Subtask 的执行顺序、深度与并行策略。

Phase 1 限制（PRD §7 Phase 1）：
    - 只处理 primary_flow，secondary_flow 始终为空
    - PositionDecision 全链路：thesis_review → position_fit_check → action_evaluation

TODO Phase 2: Secondary Intent 执行深度判断与注入
TODO Phase 2: PortfolioReview 并行执行（Promise.all 等价）
TODO Phase 2: AssetAllocation 三选一互斥逻辑

执行顺序说明（PRD §3.3 Subtask 执行顺序）：
    PositionDecision: sequential（thesis_review → position_fit_check → action_evaluation）
    PortfolioReview:  (review + risk_check + concentration_check) 并行 → rebalance_check
    AssetAllocation:  三选一互斥
"""
from __future__ import annotations

from typing import List

from .types import (
    DataRequirement,
    ExecutionContext,
    ExecutionPlan,
    SubtaskExecution,
    TRADE_ACTIONS,
)


# ── 各 Intent 的 Subtask 执行计划模板（Phase 1 仅实现 PositionDecision）─────

def generate_plan(ctx: ExecutionContext) -> ExecutionPlan:
    """
    根据 ExecutionContext 生成 ExecutionPlan（PRD §3.3）。

    Phase 1 只支持 PositionDecision 完整链路，其余 Intent 生成占位计划。
    TODO Phase 2: 实现其余 Intent 的完整链路
    """
    intent = ctx.intent_payload.primary_intent
    asset = ctx.inherited_fields.asset or ctx.intent_payload.entities.asset

    if intent == "PositionDecision":
        primary_flow = _position_decision_flow(asset)
    elif intent == "PortfolioReview":
        # TODO Phase 2: 实现并行执行计划
        primary_flow = _stub_flow(["review", "risk_check", "concentration_check", "rebalance_check"])
    elif intent == "AssetAllocation":
        # TODO Phase 2: 三选一互斥逻辑（new_cash / rebalance / goal_based）
        primary_flow = _stub_flow(["new_cash_allocation"])
    elif intent == "PerformanceAnalysis":
        # TODO Phase 3: 实现收益分析链路
        primary_flow = _stub_flow(["pnl_breakdown", "loss_reason", "attribution"])
    else:
        # Education / fallback
        primary_flow = _stub_flow(["concept_explain"])

    # Phase 1: secondary_flow 始终为空（TODO Phase 2: Secondary Intent 支持）
    secondary_flow: List[SubtaskExecution] = []

    return ExecutionPlan(
        primary_flow=primary_flow,
        secondary_flow=secondary_flow,
        execution_mode="sequential",
    )


# ── PositionDecision 执行计划（PRD §3.3）─────────────────────────────────────

def _position_decision_flow(asset: str | None) -> List[SubtaskExecution]:
    """
    PositionDecision 完整 Subtask 链路（PRD §3.3）：
        thesis_review → position_fit_check → action_evaluation

    依赖关系：
        thesis_review:      无前置依赖，需要市场数据 + 新闻
        position_fit_check: 无前置依赖，需要持仓数据
        action_evaluation:  依赖 thesis_review + position_fit_check
    """
    params = {"asset": asset or ""} if asset else {}

    thesis_review = SubtaskExecution(
        subtask="thesis_review",
        intent_source="primary",
        execution_depth="full",
        depends_on=[],
        data_requirements=[
            DataRequirement(type="market_data", params=params),
            DataRequirement(type="news", params=params),
        ],
    )

    position_fit_check = SubtaskExecution(
        subtask="position_fit_check",
        intent_source="primary",
        execution_depth="full",
        depends_on=[],
        data_requirements=[
            DataRequirement(type="portfolio_data", params={}),
            DataRequirement(type="user_profile", params={}),
        ],
    )

    action_evaluation = SubtaskExecution(
        subtask="action_evaluation",
        intent_source="primary",
        execution_depth="full",
        depends_on=["thesis_review", "position_fit_check"],
        data_requirements=[],  # 从前置 Subtask 结果中获取数据
    )

    return [thesis_review, position_fit_check, action_evaluation]


# ── 占位计划（Phase 1 未实现的 Intent）───────────────────────────────────────

def _stub_flow(subtasks: List[str]) -> List[SubtaskExecution]:
    """
    为 Phase 1 未完整实现的 Intent 生成顺序执行占位计划。
    SubtaskRunner 会执行这些 Subtask，但数据和 prompt 使用基础模板。
    """
    return [
        SubtaskExecution(
            subtask=st,
            intent_source="primary",
            execution_depth="full",
            depends_on=[subtasks[i - 1]] if i > 0 else [],
            data_requirements=[],
        )
        for i, st in enumerate(subtasks)
    ]
