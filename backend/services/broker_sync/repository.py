"""券商同步 Repository:封装持仓快照的读写操作。"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from services.broker_sync.models import PositionSnapshot, PositionSnapshotRun
from services.broker_sync.schema import Position


class PositionSnapshotRepository:
    """持仓快照仓储层。"""

    def __init__(self, session: Session):
        self.session = session

    def create_run(
        self,
        broker: str,
        account_id: str,
        sync_source: str = "api",
        triggered_by: str = "manual",
    ) -> PositionSnapshotRun:
        """创建一条 running 状态的 run 记录,提交后返回。"""
        run = PositionSnapshotRun(
            broker=broker,
            account_id=account_id,
            started_at=datetime.now(timezone.utc),
            status="running",
            sync_source=sync_source,
            triggered_by=triggered_by,
            retry_count=0,
        )
        self.session.add(run)
        self.session.commit()
        self.session.refresh(run)
        return run

    def persist_positions(
        self,
        run_id: int,
        positions: list[Position],
        total_market_value_cnh: Optional[Decimal] = None,
        finalize: bool = True,
    ) -> None:
        """
        批量写入持仓快照,并把 run 标记为 success。
        整个操作是一个事务:任何一条失败,run 状态不会变成 success。
        调用方负责处理事务回滚(出现异常时 session.rollback)。
        """
        run = self.session.get(PositionSnapshotRun, run_id)
        if run is None:
            raise ValueError(f"Run id={run_id} 不存在")

        for pos in positions:
            snapshot = self._position_to_snapshot(run, pos)
            self.session.add(snapshot)

        if finalize:
            self.mark_run_succeeded(
                run_id=run_id,
                position_count=len(positions),
                total_market_value_cnh=total_market_value_cnh,
            )
        else:
            self.session.flush()

    def mark_run_succeeded(
        self,
        run_id: int,
        position_count: int,
        total_market_value_cnh: Optional[Decimal] = None,
    ) -> None:
        """在 snapshot 与业务 Position 都已成功处理后，原子提交成功状态。"""
        run = self.session.get(PositionSnapshotRun, run_id)
        if run is None:
            raise ValueError(f"Run id={run_id} 不存在")
        run.finished_at = datetime.now(timezone.utc)
        run.status = "success"
        run.position_count = position_count
        run.total_market_value_cnh = total_market_value_cnh
        self.session.commit()

    def mark_run_failed(
        self,
        run_id: int,
        error_message: str,
        retry_count: int = 0,
    ) -> None:
        """把 run 标记为 failed,记录错误信息和重试次数。"""
        run = self.session.get(PositionSnapshotRun, run_id)
        if run is None:
            return

        run.finished_at = datetime.now(timezone.utc)
        run.status = "failed"
        run.error_message = error_message[:5000]
        run.retry_count = retry_count
        self.session.commit()

    def get_latest_successful_run(
        self, broker: str, account_id: str
    ) -> Optional[PositionSnapshotRun]:
        """查询最近一次成功的 run。"""
        return (
            self.session.query(PositionSnapshotRun)
            .filter_by(broker=broker, account_id=account_id, status="success")
            .order_by(PositionSnapshotRun.started_at.desc())
            .first()
        )

    @staticmethod
    def _position_to_snapshot(
        run: PositionSnapshotRun, pos: Position
    ) -> PositionSnapshot:
        """Pydantic Position → SQLAlchemy PositionSnapshot。"""
        return PositionSnapshot(
            run_id=run.id,
            broker=pos.broker,
            account_id=pos.account_id,
            snapshot_time=pos.snapshot_time,
            symbol=pos.symbol,
            raw_symbol=pos.raw_symbol,
            name=pos.name,
            name_en=pos.name_en,
            asset_class=pos.asset_class,
            broker_security_type=pos.broker_security_type or None,
            vehicle_type=pos.vehicle_type,
            economic_asset_class=pos.economic_asset_class,
            economic_asset_subclass=pos.economic_asset_subclass,
            classification_source=pos.classification_source,
            classification_confidence=pos.classification_confidence,
            classification_verification_status=pos.classification_verification_status,
            classification_version=pos.classification_version,
            classification_evidence_json=json.dumps(
                pos.classification_evidence,
                ensure_ascii=False,
                default=str,
            ),
            market=pos.market,
            quantity=pos.quantity,
            available_quantity=pos.available_quantity,
            avg_cost=pos.avg_cost,
            cost_method=pos.cost_method,
            cost_basis=pos.cost_basis,
            current_price=pos.current_price,
            market_value=pos.market_value,
            currency=pos.currency,
            unrealized_pnl=pos.unrealized_pnl,
            unrealized_pnl_pct=pos.unrealized_pnl_pct,
            realized_pnl=pos.realized_pnl,
            day_pnl=pos.day_pnl,
            option_meta_json=(
                json.dumps(pos.option_meta.model_dump(mode="json"))
                if pos.option_meta else None
            ),
            raw_data_json=json.dumps(pos.raw_data, ensure_ascii=False, default=str),
        )
