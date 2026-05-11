"""
状态机单元测试 — 覆盖三层状态流转的合法/非法路径。

运行: cd backend && python -m pytest services/action/tests/test_state_machine.py -v
"""
import pytest

from backend.services.action.state_machine import (
    ActionDraftStatus,
    StrategyStatus,
    OrderStatus,
    validate_draft_transition,
    validate_strategy_transition,
    validate_order_transition,
)


# ═══════════════════════════════════════════════════════════════════
# ActionDraft 状态流转
# ═══════════════════════════════════════════════════════════════════

class TestActionDraftTransitions:
    """ActionDraft: draft → confirmed | discarded"""

    def test_draft_to_confirmed(self):
        validate_draft_transition("draft", "confirmed")  # 不抛异常

    def test_draft_to_discarded(self):
        validate_draft_transition("draft", "discarded")

    def test_confirmed_is_terminal(self):
        with pytest.raises(ValueError, match="终态"):
            validate_draft_transition("confirmed", "draft")

    def test_discarded_is_terminal(self):
        with pytest.raises(ValueError, match="终态"):
            validate_draft_transition("discarded", "draft")

    def test_confirmed_to_discarded_illegal(self):
        with pytest.raises(ValueError):
            validate_draft_transition("confirmed", "discarded")

    def test_invalid_from_status(self):
        with pytest.raises(ValueError, match="无效"):
            validate_draft_transition("unknown_status", "confirmed")

    def test_invalid_to_status(self):
        with pytest.raises(ValueError, match="无效"):
            validate_draft_transition("draft", "unknown_status")


# ═══════════════════════════════════════════════════════════════════
# Strategy 状态流转（SymbolStrategy + AllocationIntent 共用）
# ═══════════════════════════════════════════════════════════════════

class TestStrategyTransitions:
    """Strategy: active ↔ paused, active → completed | discarded"""

    def test_active_to_paused(self):
        validate_strategy_transition("active", "paused")

    def test_paused_to_active(self):
        validate_strategy_transition("paused", "active")

    def test_active_to_completed(self):
        validate_strategy_transition("active", "completed")

    def test_active_to_discarded(self):
        validate_strategy_transition("active", "discarded")

    def test_paused_to_discarded(self):
        validate_strategy_transition("paused", "discarded")

    def test_completed_is_terminal(self):
        with pytest.raises(ValueError, match="终态"):
            validate_strategy_transition("completed", "active")

    def test_discarded_is_terminal(self):
        with pytest.raises(ValueError, match="终态"):
            validate_strategy_transition("discarded", "active")

    def test_paused_to_completed_illegal(self):
        """paused 不能直接完成，必须先恢复到 active"""
        with pytest.raises(ValueError):
            validate_strategy_transition("paused", "completed")

    def test_invalid_from_status(self):
        with pytest.raises(ValueError, match="无效"):
            validate_strategy_transition("bogus", "active")


# ═══════════════════════════════════════════════════════════════════
# Order 状态流转
# ═══════════════════════════════════════════════════════════════════

class TestOrderTransitions:
    """Order: created → submitted → pending → filled/cancelled/..."""

    def test_created_to_submitted(self):
        validate_order_transition("created", "submitted_to_broker")

    def test_created_to_cancelled(self):
        """创建后立即取消（用户反悔）"""
        validate_order_transition("created", "cancelled")

    def test_submitted_to_pending(self):
        validate_order_transition("submitted_to_broker", "broker_pending")

    def test_submitted_to_rejected(self):
        validate_order_transition("submitted_to_broker", "rejected")

    def test_pending_to_partially_filled(self):
        validate_order_transition("broker_pending", "partially_filled")

    def test_pending_to_filled(self):
        validate_order_transition("broker_pending", "filled")

    def test_pending_to_cancelled(self):
        validate_order_transition("broker_pending", "cancelled")

    def test_pending_to_expired(self):
        validate_order_transition("broker_pending", "expired")

    def test_partially_to_filled(self):
        validate_order_transition("partially_filled", "filled")

    def test_partially_to_cancelled(self):
        """部分成交后取消剩余"""
        validate_order_transition("partially_filled", "cancelled")

    def test_filled_is_terminal(self):
        with pytest.raises(ValueError, match="终态"):
            validate_order_transition("filled", "cancelled")

    def test_cancelled_is_terminal(self):
        with pytest.raises(ValueError, match="终态"):
            validate_order_transition("cancelled", "created")

    def test_rejected_is_terminal(self):
        with pytest.raises(ValueError, match="终态"):
            validate_order_transition("rejected", "created")

    def test_expired_is_terminal(self):
        with pytest.raises(ValueError, match="终态"):
            validate_order_transition("expired", "created")

    def test_unknown_can_recover(self):
        """unknown 状态可以被修正为正常状态"""
        validate_order_transition("unknown", "filled")
        validate_order_transition("unknown", "cancelled")
        validate_order_transition("unknown", "broker_pending")

    def test_created_to_filled_illegal(self):
        """不能跳过 submitted/pending 直接 filled"""
        with pytest.raises(ValueError):
            validate_order_transition("created", "filled")

    def test_created_to_partially_filled_illegal(self):
        with pytest.raises(ValueError):
            validate_order_transition("created", "partially_filled")

    def test_invalid_status(self):
        with pytest.raises(ValueError, match="无效"):
            validate_order_transition("nonexistent", "filled")


# ═══════════════════════════════════════════════════════════════════
# 跨层独立性验证
# ═══════════════════════════════════════════════════════════════════

class TestCrossLayerIndependence:
    """确保三套状态互不干扰"""

    def test_strategy_status_not_in_order_status(self):
        """Strategy 的 'active' 不是 Order 的合法状态"""
        assert "active" not in OrderStatus.ALL

    def test_order_status_not_in_strategy_status(self):
        """Order 的 'submitted_to_broker' 不是 Strategy 的合法状态"""
        assert "submitted_to_broker" not in StrategyStatus.ALL

    def test_draft_status_not_in_strategy_status(self):
        """Draft 的 'draft' 不是 Strategy 的合法状态"""
        assert "draft" not in StrategyStatus.ALL
