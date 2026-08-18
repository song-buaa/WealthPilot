"""
WealthPilot 券商同步 SQLAlchemy ORM 模型。

设计原则:
- 跟 app/models.py 的业务表(Portfolio/Position)分离,避免互相污染
- 金额一律 Numeric,不用 Float
- snapshot 通过 run_id 关联,run 删除时 CASCADE 删除关联 snapshots
"""
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, DateTime, Numeric, Text,
    ForeignKey, Index
)
from sqlalchemy.orm import relationship

from app.database import Base


class PositionSnapshotRun(Base):
    """同步运行记录:每次同步任务一条。"""
    __tablename__ = "position_snapshot_runs"

    id = Column(Integer, primary_key=True)
    broker = Column(String(20), nullable=False)
    account_id = Column(String(50), nullable=False)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(20), nullable=False, default="running")
    # status: running / success / failed
    sync_source = Column(String(20), nullable=False, default="api")
    position_count = Column(Integer, nullable=True)
    total_market_value_cnh = Column(Numeric(20, 2), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    triggered_by = Column(String(50), nullable=False, default="manual")

    snapshots = relationship(
        "PositionSnapshot",
        back_populates="run",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_runs_broker_account_time", "broker", "account_id", "started_at"),
    )

    def __repr__(self):
        return (
            f"<PositionSnapshotRun id={self.id} broker={self.broker} "
            f"account={self.account_id} status={self.status} "
            f"started={self.started_at}>"
        )


class PositionSnapshot(Base):
    """持仓时序记录:每次同步,每条持仓一行。"""
    __tablename__ = "position_snapshots"

    id = Column(Integer, primary_key=True)
    run_id = Column(
        Integer,
        ForeignKey("position_snapshot_runs.id", ondelete="CASCADE"),
        nullable=False
    )

    # 冗余字段(加查询索引)
    broker = Column(String(20), nullable=False)
    account_id = Column(String(50), nullable=False)
    snapshot_time = Column(DateTime, nullable=False)

    # 标识
    symbol = Column(String(30), nullable=False)
    raw_symbol = Column(String(30), nullable=False)
    name = Column(String(100), nullable=False)
    name_en = Column(String(100), nullable=True)

    # 资产分类
    asset_class = Column(String(20), nullable=False)
    broker_security_type = Column(String(30), nullable=True)
    vehicle_type = Column(String(30), nullable=True)
    economic_asset_class = Column(String(30), nullable=True)
    economic_asset_subclass = Column(String(50), nullable=True)
    classification_source = Column(String(80), nullable=True)
    classification_confidence = Column(String(20), nullable=True)
    classification_verification_status = Column(String(30), nullable=True)
    classification_version = Column(String(30), nullable=True)
    classification_evidence_json = Column(Text, nullable=True)
    market = Column(String(10), nullable=False)

    # 数量与成本
    quantity = Column(Numeric(20, 8), nullable=False)
    available_quantity = Column(Numeric(20, 8), nullable=True)
    avg_cost = Column(Numeric(20, 8), nullable=False)
    cost_method = Column(String(20), nullable=False)
    cost_basis = Column(Numeric(20, 8), nullable=False)

    # 行情
    current_price = Column(Numeric(20, 8), nullable=False)
    market_value = Column(Numeric(20, 8), nullable=False)
    currency = Column(String(10), nullable=False)

    # 盈亏
    unrealized_pnl = Column(Numeric(20, 8), nullable=False)
    unrealized_pnl_pct = Column(Numeric(10, 6), nullable=False)
    realized_pnl = Column(Numeric(20, 8), nullable=True)
    day_pnl = Column(Numeric(20, 8), nullable=True)

    # 期权 + 原始数据
    option_meta_json = Column(Text, nullable=True)
    raw_data_json = Column(Text, nullable=False)

    run = relationship("PositionSnapshotRun", back_populates="snapshots")

    __table_args__ = (
        Index("ix_snapshots_symbol_time", "symbol", "snapshot_time"),
        Index("ix_snapshots_run", "run_id"),
    )

    def __repr__(self):
        return (
            f"<PositionSnapshot id={self.id} run={self.run_id} "
            f"symbol={self.symbol} qty={self.quantity}>"
        )
