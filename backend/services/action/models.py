"""
WealthPilot v3.2 投资行动模块 ORM 模型。

5 张核心表：
1. action_draft      — 行动清单草稿（AI 对话 → 可执行行动的桥梁）
2. allocation_intent — 资产配置调整意图（组合级策略容器）
3. symbol_strategy   — 标的策略（单标的买卖执行计划）
4. order_record      — 券商订单（实际提交到券商的订单记录）
5. audit_log         — 审计日志（append-only，所有关键操作留痕）

设计原则：
- 与 app/models.py 的业务表分离，避免互相污染
- PK 用 String(36) 存 UUID 字符串（SQLite 兼容）
- 金额用 Numeric（不用 Float）
- JSON 字段用 Text（SQLite 不支持 JSONB）
- 时间统一存 UTC

未来迁 PostgreSQL 时：
- Text → JSONB（payload / target_allocation 等字段）
- String(36) → UUID 原生类型
- DateTime → TIMESTAMPTZ
- audit_log.id Integer → BIGSERIAL
"""
from datetime import datetime, timezone
import uuid

from sqlalchemy import (
    Column, Integer, String, DateTime, Numeric, Text,
    ForeignKey, Index,
)

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════
# 1. ActionDraft — 行动清单草稿
# ═══════════════════════════════════════════════════════════════════

class ActionDraft(Base):
    """
    AI 对话产生的行动清单草稿。

    用户在投资决策对话中确认操作意图后，系统生成草稿；
    用户审阅并确认后，草稿拆解为 SymbolStrategy / AllocationIntent。

    status 流转：draft → confirmed | discarded
    """
    __tablename__ = "action_drafts"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=True)  # 单用户版暂为 NULL
    conversation_id = Column(String(36), nullable=True)  # 关联的对话 conversation_id

    decision_summary = Column(Text, nullable=True)  # AI 生成的决策摘要
    payload = Column(Text, nullable=True)  # JSON: 完整的行动清单结构化数据

    status = Column(String(20), nullable=False, default="draft")
    # status: draft / confirmed / discarded

    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    confirmed_at = Column(DateTime, nullable=True)
    discarded_at = Column(DateTime, nullable=True)


# ═══════════════════════════════════════════════════════════════════
# 2. AllocationIntent — 资产配置调整意图
# ═══════════════════════════════════════════════════════════════════

class AllocationIntent(Base):
    """
    资产配置调整意图（组合级策略容器）。

    从 ActionDraft 确认后生成，描述用户希望达到的目标配置。
    不直接产生 OrderRecord（在 API 层校验拒绝 place_order 调用）。

    status 流转：复用 StrategyStatus（active ↔ paused, active → completed | discarded）
    """
    __tablename__ = "allocation_intents"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=True)
    source_draft_id = Column(
        String(36), ForeignKey("action_drafts.id"), nullable=True,
    )

    title = Column(Text, nullable=True)  # 配置意图标题
    target_allocation = Column(Text, nullable=True)  # JSON: 目标配置比例
    current_allocation_snapshot = Column(Text, nullable=True)  # JSON: 创建时的配置快照

    status = Column(String(20), nullable=False, default="active")
    # status: active / paused / completed / discarded

    related_conversation_id = Column(String(36), nullable=True)
    decision_basis = Column(Text, nullable=True)  # 决策依据文本

    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    completed_at = Column(DateTime, nullable=True)


# ═══════════════════════════════════════════════════════════════════
# 3. SymbolStrategy — 标的策略
# ═══════════════════════════════════════════════════════════════════

class SymbolStrategy(Base):
    """
    标的策略（单标的买卖执行计划）。

    从 ActionDraft 确认后生成，描述对某个标的的具体操作意图。
    可关联到 AllocationIntent（组合级策略下的子策略）。
    strategy : order = 1 : N（一个策略可以分批产生多笔订单）。

    status 流转：active ↔ paused, active → completed | discarded

    关键约束：
    - 创建 Order 前校验 cumulative_filled_quantity + 本次 quantity ≤ target_quantity
    - 超额下单禁止
    """
    __tablename__ = "symbol_strategies"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=True)
    source_draft_id = Column(
        String(36), ForeignKey("action_drafts.id"), nullable=True,
    )
    parent_intent_id = Column(
        String(36), ForeignKey("allocation_intents.id"), nullable=True,
    )
    # v3.11: 执行计划关联
    plan_id = Column(String(36), nullable=True)  # 关联 execution_plans.id
    tranche_sequence = Column(Integer, nullable=True)  # 批次序号

    symbol = Column(String(50), nullable=False)  # 标的代码，如 MSFT / 02015
    side = Column(String(10), nullable=False)  # BUY / SELL

    target_quantity = Column(Integer, nullable=True)  # 目标数量（股数）
    target_quantity_pct = Column(Numeric(10, 4), nullable=True)  # 目标仓位百分比
    cumulative_filled_quantity = Column(Integer, nullable=False, default=0)

    order_type = Column(String(30), nullable=False, default="LIMIT")
    # order_type: LIMIT / CONDITIONAL_LIMIT
    trigger_price = Column(Numeric(20, 4), nullable=True)  # 条件单触发价
    limit_price = Column(Numeric(20, 4), nullable=True)  # 限价

    status = Column(String(20), nullable=False, default="active")
    # status: active / paused / completed / discarded

    related_conversation_id = Column(String(36), nullable=True)
    decision_basis = Column(Text, nullable=True)

    # v3.11 M6: 到价触发标记
    armed_at = Column(DateTime, nullable=True)        # 非 null = 已到价待确认
    interval_blocked = Column(String(100), nullable=True)  # 非 null = 纪律暂缓原因

    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_symbol_strategies_symbol", "symbol"),
        Index("ix_symbol_strategies_status", "status"),
    )


# ═══════════════════════════════════════════════════════════════════
# 4. OrderRecord — 券商订单
# ═══════════════════════════════════════════════════════════════════

class OrderRecord(Base):
    """
    券商订单记录。

    每笔订单必须关联到一个 SymbolStrategy（strategy_id NOT NULL）。
    状态由券商回报驱动，WealthPilot 只做记录和同步。

    status 流转：
    created → submitted_to_broker → broker_pending
    → partially_filled / filled / cancelled / rejected / expired / unknown
    """
    __tablename__ = "order_records"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=True)
    strategy_id = Column(
        String(36), ForeignKey("symbol_strategies.id"), nullable=False,
    )

    broker_name = Column(String(20), nullable=False, default="mock")
    # broker_name: mock / tiger / futu / gjzq
    broker_order_id = Column(String(100), nullable=True)  # 券商侧订单 ID

    symbol = Column(String(50), nullable=False)
    side = Column(String(10), nullable=False)  # BUY / SELL
    quantity = Column(Integer, nullable=False)
    filled_quantity = Column(Integer, nullable=False, default=0)

    order_type = Column(String(30), nullable=False, default="LIMIT")
    limit_price = Column(Numeric(20, 4), nullable=True)
    stop_price = Column(Numeric(20, 4), nullable=True)  # 条件单触发价
    avg_filled_price = Column(Numeric(20, 4), nullable=True)

    status = Column(String(30), nullable=False, default="created")
    # status: created / submitted_to_broker / broker_pending /
    #         partially_filled / filled / cancelled / rejected / expired / unknown

    submitted_at = Column(DateTime, nullable=True)
    filled_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)

    raw_broker_response = Column(Text, nullable=True)  # JSON: 券商原始回报

    created_at = Column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_order_records_strategy_id", "strategy_id"),
        Index("ix_order_records_status", "status"),
    )


# ═══════════════════════════════════════════════════════════════════
# 5. AuditLog — 审计日志（append-only）
# ═══════════════════════════════════════════════════════════════════

class AuditLog(Base):
    """
    审计日志，记录所有关键操作。

    Append-only：只插入，不更新，不删除。
    payload 存脱敏后的操作上下文。

    未来迁 PostgreSQL 时 id 改为 BIGSERIAL。
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), nullable=True)

    event_type = Column(String(50), nullable=False)
    # event_type 示例: draft_created / draft_confirmed / draft_discarded /
    #   strategy_created / strategy_paused / strategy_resumed / strategy_discarded /
    #   order_created / order_submitted / order_filled / order_cancelled

    payload = Column(Text, nullable=True)  # JSON: 脱敏后的操作上下文

    ip_address = Column(String(45), nullable=True)  # IPv4/IPv6
    user_agent = Column(String(256), nullable=True)  # HTTP User-Agent
    timestamp = Column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_audit_logs_event_type", "event_type"),
        Index("ix_audit_logs_timestamp", "timestamp"),
    )
