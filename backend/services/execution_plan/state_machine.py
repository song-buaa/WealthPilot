"""
执行计划状态机 — PRD §6.2 批次状态合法流转校验。

三层状态，主从清晰：
  ExecutionPlan(主) > ExecutionTranche(批次) > OrderRecord(成交)

本模块只做批次(Tranche)状态校验。Plan 状态由上层管理。
"""
from __future__ import annotations


class TrancheStatus:
    PENDING = "pending"
    ARMED = "armed"
    TRIGGERED = "triggered"
    SUBMITTED = "submitted"
    PARTIAL_FILLED = "partial_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"

    ALL = {
        PENDING, ARMED, TRIGGERED, SUBMITTED,
        PARTIAL_FILLED, FILLED,
        REJECTED, FAILED, SKIPPED, CANCELLED,
    }

    # 终态集合
    TERMINAL = {FILLED, FAILED, SKIPPED, CANCELLED}


# 合法流转表 (PRD §6.2)
_TRANCHE_TRANSITIONS: dict[str, set[str]] = {
    TrancheStatus.PENDING: {
        TrancheStatus.ARMED,
        TrancheStatus.SKIPPED,     # 事件锁/用户跳过
        TrancheStatus.CANCELLED,   # 计划取消
    },
    TrancheStatus.ARMED: {
        TrancheStatus.TRIGGERED,
        TrancheStatus.SKIPPED,
        TrancheStatus.CANCELLED,
    },
    TrancheStatus.TRIGGERED: {
        TrancheStatus.SUBMITTED,
        TrancheStatus.CANCELLED,
    },
    TrancheStatus.SUBMITTED: {
        TrancheStatus.PARTIAL_FILLED,
        TrancheStatus.FILLED,
        TrancheStatus.REJECTED,
        TrancheStatus.CANCELLED,
    },
    TrancheStatus.PARTIAL_FILLED: {
        TrancheStatus.FILLED,
        TrancheStatus.CANCELLED,
    },
    TrancheStatus.REJECTED: {
        TrancheStatus.ARMED,       # 重试（有上限，上层控制）
        TrancheStatus.FAILED,      # 重试耗尽
        TrancheStatus.CANCELLED,
    },
    # 终态
    TrancheStatus.FILLED: set(),
    TrancheStatus.FAILED: set(),
    TrancheStatus.SKIPPED: set(),
    TrancheStatus.CANCELLED: set(),
}

# rejected → armed 重试上限
MAX_REJECTED_RETRIES = 3


def validate_tranche_transition(from_status: str, to_status: str) -> None:
    """校验批次状态流转，非法流转抛 ValueError。"""
    if from_status not in TrancheStatus.ALL:
        raise ValueError(f"无效的 Tranche 状态: {from_status}")
    if to_status not in TrancheStatus.ALL:
        raise ValueError(f"无效的 Tranche 目标状态: {to_status}")
    allowed = _TRANCHE_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"Tranche 状态流转非法: {from_status} → {to_status}，"
            f"允许的目标状态: {allowed or '（终态，不可流转）'}"
        )


class PlanStatus:
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"

    ALL = {DRAFT, ACTIVE, PAUSED, COMPLETED, CANCELLED, SUPERSEDED}
    TERMINAL = {COMPLETED, CANCELLED, SUPERSEDED}


_PLAN_TRANSITIONS: dict[str, set[str]] = {
    PlanStatus.DRAFT: {
        PlanStatus.ACTIVE,
        PlanStatus.CANCELLED,
    },
    PlanStatus.ACTIVE: {
        PlanStatus.PAUSED,
        PlanStatus.COMPLETED,
        PlanStatus.CANCELLED,
        PlanStatus.SUPERSEDED,
    },
    PlanStatus.PAUSED: {
        PlanStatus.ACTIVE,
        PlanStatus.CANCELLED,
    },
    # 终态
    PlanStatus.COMPLETED: set(),
    PlanStatus.CANCELLED: set(),
    PlanStatus.SUPERSEDED: set(),
}


def validate_plan_transition(from_status: str, to_status: str) -> None:
    """校验计划状态流转，非法流转抛 ValueError。"""
    if from_status not in PlanStatus.ALL:
        raise ValueError(f"无效的 Plan 状态: {from_status}")
    if to_status not in PlanStatus.ALL:
        raise ValueError(f"无效的 Plan 目标状态: {to_status}")
    allowed = _PLAN_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"Plan 状态流转非法: {from_status} → {to_status}，"
            f"允许的目标状态: {allowed or '（终态，不可流转）'}"
        )
