"""
执行计划状态机测试 — 合法/非法流转覆盖。

运行: python -m pytest backend/services/execution_plan/tests/test_state_machine.py -v
"""
import pytest
from backend.services.execution_plan.state_machine import (
    validate_tranche_transition,
    validate_plan_transition,
    TrancheStatus,
    PlanStatus,
)


class TestTrancheTransitions:
    """批次状态流转。"""

    # ── 合法流转 ──
    def test_pending_to_armed(self):
        validate_tranche_transition("pending", "armed")

    def test_armed_to_triggered(self):
        validate_tranche_transition("armed", "triggered")

    def test_triggered_to_submitted(self):
        validate_tranche_transition("triggered", "submitted")

    def test_submitted_to_filled(self):
        validate_tranche_transition("submitted", "filled")

    def test_submitted_to_partial(self):
        validate_tranche_transition("submitted", "partial_filled")

    def test_partial_to_filled(self):
        validate_tranche_transition("partial_filled", "filled")

    def test_submitted_to_rejected(self):
        validate_tranche_transition("submitted", "rejected")

    def test_rejected_to_armed_retry(self):
        validate_tranche_transition("rejected", "armed")

    def test_rejected_to_failed(self):
        validate_tranche_transition("rejected", "failed")

    def test_pending_to_skipped(self):
        validate_tranche_transition("pending", "skipped")

    def test_armed_to_cancelled(self):
        validate_tranche_transition("armed", "cancelled")

    # ── 非法流转(终态不可退) ──
    def test_filled_to_pending_illegal(self):
        with pytest.raises(ValueError, match="状态流转非法"):
            validate_tranche_transition("filled", "pending")

    def test_cancelled_to_armed_illegal(self):
        with pytest.raises(ValueError, match="状态流转非法"):
            validate_tranche_transition("cancelled", "armed")

    def test_failed_to_anything_illegal(self):
        with pytest.raises(ValueError, match="终态"):
            validate_tranche_transition("failed", "pending")

    def test_skipped_to_armed_illegal(self):
        with pytest.raises(ValueError, match="终态"):
            validate_tranche_transition("skipped", "armed")

    # ── 非法跳跃 ──
    def test_pending_to_filled_illegal(self):
        with pytest.raises(ValueError, match="状态流转非法"):
            validate_tranche_transition("pending", "filled")

    def test_armed_to_filled_illegal(self):
        with pytest.raises(ValueError, match="状态流转非法"):
            validate_tranche_transition("armed", "filled")

    # ── 无效状态 ──
    def test_invalid_from_status(self):
        with pytest.raises(ValueError, match="无效"):
            validate_tranche_transition("nonexistent", "armed")

    def test_invalid_to_status(self):
        with pytest.raises(ValueError, match="无效"):
            validate_tranche_transition("pending", "nonexistent")


class TestPlanTransitions:
    """计划状态流转。"""

    def test_draft_to_active(self):
        validate_plan_transition("draft", "active")

    def test_active_to_paused(self):
        validate_plan_transition("active", "paused")

    def test_paused_to_active(self):
        validate_plan_transition("paused", "active")

    def test_active_to_completed(self):
        validate_plan_transition("active", "completed")

    def test_active_to_cancelled(self):
        validate_plan_transition("active", "cancelled")

    def test_completed_terminal(self):
        with pytest.raises(ValueError, match="终态"):
            validate_plan_transition("completed", "active")

    def test_draft_to_completed_illegal(self):
        with pytest.raises(ValueError, match="状态流转非法"):
            validate_plan_transition("draft", "completed")
