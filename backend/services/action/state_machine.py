"""
WealthPilot v3.2 投资行动模块 — 三层状态机。

用 Python 常量 + 校验函数实现，不依赖第三方状态机库。

三套独立的状态流转：
1. ActionDraftStatus:  draft → confirmed | discarded
2. StrategyStatus:     active ↔ paused, active → completed | discarded
                       （AllocationIntent 复用此状态值，但不能触发 place_order）
3. OrderStatus:        created → submitted_to_broker → broker_pending
                       → partially_filled | filled | cancelled | rejected | expired | unknown

关键设计决策：
- StrategyStatus 和 OrderStatus 是两套完全独立的状态，不能混用
- 非法流转抛 ValueError
- 超额下单校验由 OrderManager 在创建 Order 时执行，不在状态机层
"""


# ═══════════════════════════════════════════════════════════════════
# 1. ActionDraft 状态
# ═══════════════════════════════════════════════════════════════════

class ActionDraftStatus:
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"

    ALL = {DRAFT, CONFIRMED, DISCARDED}


# draft → confirmed | discarded（终态不可再流转）
_DRAFT_TRANSITIONS: dict[str, set[str]] = {
    ActionDraftStatus.DRAFT: {ActionDraftStatus.CONFIRMED, ActionDraftStatus.DISCARDED},
    ActionDraftStatus.CONFIRMED: set(),
    ActionDraftStatus.DISCARDED: set(),
}


def validate_draft_transition(from_status: str, to_status: str) -> None:
    """校验 ActionDraft 状态流转，非法流转抛 ValueError。"""
    if from_status not in ActionDraftStatus.ALL:
        raise ValueError(f"无效的 ActionDraft 状态: {from_status}")
    if to_status not in ActionDraftStatus.ALL:
        raise ValueError(f"无效的 ActionDraft 目标状态: {to_status}")
    allowed = _DRAFT_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"ActionDraft 状态流转非法: {from_status} → {to_status}，"
            f"允许的目标状态: {allowed or '（终态，不可流转）'}"
        )


# ═══════════════════════════════════════════════════════════════════
# 2. Strategy 状态（SymbolStrategy + AllocationIntent 共用）
# ═══════════════════════════════════════════════════════════════════

class StrategyStatus:
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    DISCARDED = "discarded"

    ALL = {ACTIVE, PAUSED, COMPLETED, DISCARDED}


# active ↔ paused, active → completed | discarded, paused → discarded
_STRATEGY_TRANSITIONS: dict[str, set[str]] = {
    StrategyStatus.ACTIVE: {
        StrategyStatus.PAUSED,
        StrategyStatus.COMPLETED,
        StrategyStatus.DISCARDED,
    },
    StrategyStatus.PAUSED: {
        StrategyStatus.ACTIVE,
        StrategyStatus.DISCARDED,
    },
    StrategyStatus.COMPLETED: set(),
    StrategyStatus.DISCARDED: set(),
}


def validate_strategy_transition(from_status: str, to_status: str) -> None:
    """校验 Strategy 状态流转，非法流转抛 ValueError。"""
    if from_status not in StrategyStatus.ALL:
        raise ValueError(f"无效的 Strategy 状态: {from_status}")
    if to_status not in StrategyStatus.ALL:
        raise ValueError(f"无效的 Strategy 目标状态: {to_status}")
    allowed = _STRATEGY_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"Strategy 状态流转非法: {from_status} → {to_status}，"
            f"允许的目标状态: {allowed or '（终态，不可流转）'}"
        )


# ═══════════════════════════════════════════════════════════════════
# 3. Order 状态（独立于 Strategy 状态）
# ═══════════════════════════════════════════════════════════════════

class OrderStatus:
    CREATED = "created"
    SUBMITTED_TO_BROKER = "submitted_to_broker"
    BROKER_PENDING = "broker_pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    UNKNOWN = "unknown"

    ALL = {
        CREATED, SUBMITTED_TO_BROKER, BROKER_PENDING,
        PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED, EXPIRED, UNKNOWN,
    }

    # 终态集合（不可再流转）
    TERMINAL = {FILLED, CANCELLED, REJECTED, EXPIRED}


_ORDER_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.CREATED: {OrderStatus.SUBMITTED_TO_BROKER, OrderStatus.CANCELLED},
    OrderStatus.SUBMITTED_TO_BROKER: {
        OrderStatus.BROKER_PENDING,
        OrderStatus.REJECTED,
        OrderStatus.CANCELLED,
    },
    OrderStatus.BROKER_PENDING: {
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.EXPIRED,
        OrderStatus.UNKNOWN,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.UNKNOWN,
    },
    # 终态
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
    # unknown 可以被修正为任何非 unknown 状态
    OrderStatus.UNKNOWN: {
        OrderStatus.BROKER_PENDING,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    },
}


def validate_order_transition(from_status: str, to_status: str) -> None:
    """校验 Order 状态流转，非法流转抛 ValueError。"""
    if from_status not in OrderStatus.ALL:
        raise ValueError(f"无效的 Order 状态: {from_status}")
    if to_status not in OrderStatus.ALL:
        raise ValueError(f"无效的 Order 目标状态: {to_status}")
    allowed = _ORDER_TRANSITIONS.get(from_status, set())
    if to_status not in allowed:
        raise ValueError(
            f"Order 状态流转非法: {from_status} → {to_status}，"
            f"允许的目标状态: {allowed or '（终态，不可流转）'}"
        )
