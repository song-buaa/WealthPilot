"""
ContextManager — 上下文管理模块

对应工程PRD §3.2。

职责：
    合并本轮 IntentPayload 与历史上下文，生成完整 ExecutionContext。

字段生命周期规则（PRD §3.2 / 业务文档 §十.1）：
    asset        → PositionDecision 时继承；切换至 PortfolioReview/AssetAllocation 时重置
    capital      → 同一会话主题内继承；用户输入新资金规模时重置
    portfolio_id → 组合未被用户修改时继承；明确修改组合结构时重置
    risk_level   → 长期（全局），用户明确调整风险承受度时重置
    goal         → 长期（全局），用户修改投资目标或时间跨度时重置
    time_horizon → 同 goal

会话历史：保留最近5轮（PRD §3.2）

Session 存储：当前为进程内 dict（Phase 1 MVP）。
生产环境应替换为 Redis 或数据库，此处接口已留好。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .types import (
    ExecutionContext,
    InheritedFields,
    IntentPayload,
    Turn,
    UserProfile,
)

# 最多保留的历史轮数（PRD §3.2）
MAX_HISTORY_TURNS = 5


# ── 会话内部状态（非对外接口）────────────────────────────────────────────────

@dataclass
class _SessionState:
    """进程内会话状态（Phase 1 MVP 内存实现）"""
    turn_index: int = 0
    last_intent: Optional[IntentPayload] = None
    inherited_fields: InheritedFields = field(default_factory=InheritedFields)
    conversation_history: List[Turn] = field(default_factory=list)
    user_profile: UserProfile = field(default_factory=UserProfile)


# 进程内 session 存储（Phase 1）；生产环境替换为 Redis / DB
_SESSIONS: Dict[str, _SessionState] = {}


# ── 公开接口 ──────────────────────────────────────────────────────────────────

def build_context(
    session_id: str,
    intent_payload: IntentPayload,
    portfolio_id: Optional[int] = None,
) -> ExecutionContext:
    """
    核心入口：根据本轮 IntentPayload 和历史状态，生成 ExecutionContext。

    Args:
        session_id:      会话唯一标识
        intent_payload:  本轮 IntentRecognizer 输出
        portfolio_id:    用户当前组合 ID（可为 None）

    Returns:
        ExecutionContext，供 Orchestrator 使用
    """
    session = _get_or_create_session(session_id)
    session.turn_index += 1

    # 计算本轮继承字段
    inherited = _compute_inheritance(session, intent_payload, portfolio_id)
    session.inherited_fields = inherited

    # 更新 last_intent
    session.last_intent = intent_payload

    ctx = ExecutionContext(
        session_id=session_id,
        turn_index=session.turn_index,
        intent_payload=intent_payload,
        inherited_fields=inherited,
        user_profile=session.user_profile,
        conversation_history=list(session.conversation_history),
    )
    return ctx


def save_turn(session_id: str, turn: Turn) -> None:
    """
    每轮结束后，将本轮摘要写入会话历史（PRD §3.2 会话历史维护）。
    由 engine.py 在输出生成完成后调用。
    """
    session = _get_or_create_session(session_id)
    session.conversation_history.append(turn)
    # 只保留最近 MAX_HISTORY_TURNS 轮
    if len(session.conversation_history) > MAX_HISTORY_TURNS:
        session.conversation_history = session.conversation_history[-MAX_HISTORY_TURNS:]


def update_user_profile(session_id: str, profile: UserProfile) -> None:
    """更新长期用户画像（从用户系统读取后调用）。"""
    session = _get_or_create_session(session_id)
    session.user_profile = profile


def clear_session(session_id: str) -> None:
    """清除会话（测试用）。"""
    _SESSIONS.pop(session_id, None)


# ── 内部逻辑 ──────────────────────────────────────────────────────────────────

def _get_or_create_session(session_id: str) -> _SessionState:
    if session_id not in _SESSIONS:
        _SESSIONS[session_id] = _SessionState()
    return _SESSIONS[session_id]


def _compute_inheritance(
    session: _SessionState,
    current: IntentPayload,
    portfolio_id: Optional[int],
) -> InheritedFields:
    """
    实现字段生命周期继承/重置规则（PRD §3.2 字段生命周期规则 + §3.2 Intent 切换逻辑）。

    规则：
    - 本轮 IntentPayload 中已有的字段优先，不被继承值覆盖
    - Intent 相同时：全部字段尝试继承
    - Intent 发生切换时：按字段规则逐一判断
    """
    last = session.last_intent
    prev = session.inherited_fields

    new_fields = InheritedFields(
        risk_level=prev.risk_level,   # 长期字段：默认继承
        goal=prev.goal,               # 长期字段：默认继承
        time_horizon=prev.time_horizon,  # 长期字段：默认继承
    )

    cur_intent = current.primary_intent
    last_intent = last.primary_intent if last else None

    # ── asset 字段继承/重置（PRD §3.2）────────────────────────────────────────
    # 本轮已识别到 asset：直接用本轮值
    if current.entities.asset:
        new_fields.asset = current.entities.asset
        new_fields.asset_normalized = current.entities.asset_normalized
    # 本轮无 asset，但 Intent 为 PositionDecision：继承
    elif cur_intent == "PositionDecision" and prev.asset:
        new_fields.asset = prev.asset
        new_fields.asset_normalized = prev.asset_normalized
    # Intent 切换至 PortfolioReview 或 AssetAllocation：重置 asset
    elif cur_intent in ("PortfolioReview", "AssetAllocation"):
        new_fields.asset = None
        new_fields.asset_normalized = None
    # 其他情况（如 PerformanceAnalysis、Education）：保留
    else:
        new_fields.asset = prev.asset
        new_fields.asset_normalized = prev.asset_normalized

    # ── capital 字段继承/重置（PRD §3.2）─────────────────────────────────────
    # 本轮提到新资金规模：用本轮值
    if current.entities.capital:
        new_fields.capital = current.entities.capital_amount
    # 否则继承（同一会话主题内未出现新金额）
    else:
        new_fields.capital = prev.capital

    # ── portfolio_id 继承/重置（PRD §3.2）────────────────────────────────────
    # 本轮明确了 portfolio_id：用本轮值
    if current.entities.portfolio_id:
        new_fields.portfolio_id = current.entities.portfolio_id
    elif portfolio_id is not None:
        new_fields.portfolio_id = str(portfolio_id)
    else:
        # 组合未被修改（PortfolioReview 是"查看"操作，不触发重置）：继承
        new_fields.portfolio_id = prev.portfolio_id

    # ── time_horizon（与 goal 同生命周期，但本轮识别到时优先用本轮值）────────
    if current.entities.time_horizon:
        new_fields.time_horizon = current.entities.time_horizon
    elif cur_intent == "PositionDecision" and new_fields.asset and prev.asset and new_fields.asset != prev.asset:
        # 标的切换时重置 time_horizon，避免跨标的上下文污染
        # （事件型 time_horizon 如"下周发布会前"是标的专属的，不应继承到新标的）
        new_fields.time_horizon = None
    # 否则使用长期字段默认继承（上面已设置）

    return new_fields
