"""
WealthPilot v3.11 执行计划数据模型。

两张核心表:
1. execution_plans   — 执行计划主对象（计划状态的唯一权威）
2. execution_tranches — 批次（执行子任务）

设计原则（沿用 action/models.py 风格）:
- PK 用 String(36) 存 UUID（SQLite 兼容）
- 金额用 Numeric（不用 Float）
- JSON 字段用 Text（SQLite 不支持 JSONB）
- 时间统一存 UTC
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
# 1. ExecutionPlan — 执行计划主对象
# ═══════════════════════════════════════════════════════════════════

class ExecutionPlan(Base):
    """
    执行计划主对象 — 计划状态的唯一权威。

    plan_status 流转: draft → active → completed | cancelled | superseded
                      active ↔ paused
    """
    __tablename__ = "execution_plans"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(String(36), nullable=True)

    # 标的与方向
    symbol = Column(String(50), nullable=False)       # TICKER:MARKET (如 LI:US / 0700:HK)
    market = Column(String(10), nullable=False)       # US / HK
    side = Column(String(10), nullable=False)         # BUY / ADD / REDUCE / SELL

    # 计划状态
    plan_status = Column(String(20), nullable=False, default="draft")
    # plan_status: draft / active / paused / completed / cancelled / superseded
    plan_version = Column(Integer, nullable=False, default=1)

    # 来源
    source_decision_ref = Column(String(100), nullable=True)

    # 目标
    target_basis = Column(String(20), nullable=False, default="QUANTITY")
    # target_basis: QUANTITY / POSITION_PCT
    target_value = Column(Numeric(20, 4), nullable=True)

    # 用户锚点价（JSON 数组，优先级最高）
    user_anchor_prices = Column(Text, nullable=True)

    # 复盘基准价（计划生成时刻现价）
    one_shot_baseline_price = Column(Numeric(20, 4), nullable=True)

    # 手动事件锁
    manual_event_lock = Column(Text, nullable=True)
    # JSON: { enabled: bool, reason: str, until_date?: str, scope: "all_remaining"|"after_seq_N" }

    # 因子快照（含 data_source_meta）
    factor_snapshot = Column(Text, nullable=True)
    # JSON: FactorSnapshot dict

    # 实际套用的纪律参数（可审计）
    constraints_applied = Column(Text, nullable=True)
    # JSON: 纪律参数取值快照

    # AI 产出（只写解释，不含数字）
    rationale = Column(Text, nullable=True)
    risk_notes = Column(Text, nullable=True)

    # 时间戳
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    activated_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_execution_plans_symbol", "symbol"),
        Index("ix_execution_plans_status", "plan_status"),
    )


# ═══════════════════════════════════════════════════════════════════
# 2. ExecutionTranche — 批次（执行子任务）
# ═══════════════════════════════════════════════════════════════════

class ExecutionTranche(Base):
    """
    执行计划的单个批次。

    status 流转:
      pending → armed → triggered → submitted → filled
                                     ├→ partial_filled → filled | cancelled
                                     ├→ rejected → armed (重试) → failed
                                     └→ cancelled
      armed/pending → skipped (事件锁/用户跳过)
      任意态 → cancelled (计划取消或用户取消)
    """
    __tablename__ = "execution_tranches"

    id = Column(String(36), primary_key=True, default=_uuid)
    plan_id = Column(
        String(36), ForeignKey("execution_plans.id"), nullable=False,
    )
    sequence = Column(Integer, nullable=False)  # 批次序号（从 1 开始）

    # 数量
    quantity = Column(Numeric(20, 4), nullable=False)

    # 触发
    trigger_type = Column(String(20), nullable=False, default="IMMEDIATE")
    # trigger_type: IMMEDIATE / PRICE_BELOW / PRICE_ABOVE / MANUAL
    trigger_price = Column(Numeric(20, 4), nullable=True)
    limit_price = Column(Numeric(20, 4), nullable=True)

    # 订单类型
    order_type = Column(String(30), nullable=False, default="LIMIT")
    # order_type: MARKET / LIMIT / CONDITIONAL_LIMIT

    # 纪律间隔
    min_interval_days = Column(Integer, nullable=True)

    # 状态
    status = Column(String(20), nullable=False, default="pending")
    # status: pending / armed / triggered / submitted / partial_filled / filled
    #         / rejected / failed / skipped / cancelled

    # 关联现有执行轨道（M5 确认后填充，M0 暂为 null）
    linked_symbol_strategy_id = Column(String(36), nullable=True)
    linked_order_record_id = Column(String(36), nullable=True)

    # 时间戳
    triggered_at = Column(DateTime, nullable=True)
    filled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_tranches_plan_id", "plan_id"),
        Index("ix_tranches_status", "status"),
    )
