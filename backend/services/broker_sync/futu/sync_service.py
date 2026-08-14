"""富途证券持仓同步服务（只读）。"""
import time
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError

from core.config import settings
from services.broker_sync.schema import Position
from services.broker_sync.futu.adapter import FutuAdapter


# 只读护栏
WRITE_METHOD_KEYWORDS = (
    "place_order", "cancel_order", "modify_order",
    "place_deal", "modify_deal",
)


class ReadOnlyFutuClient:
    """OpenSecTradeContext 只读包装器。"""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name: str):
        if settings.futu_read_only_mode:
            for kw in WRITE_METHOD_KEYWORDS:
                if kw in name.lower():
                    raise RuntimeError(
                        f"FUTU_READ_ONLY_MODE 已开启,拒绝调用写操作方法: {name}"
                    )
        return getattr(self._inner, name)

    def close(self):
        self._inner.close()


class FutuSyncService:
    """富途持仓同步主服务。"""

    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 5

    def __init__(self):
        if not settings.futu_account:
            raise RuntimeError("FUTU_ACCOUNT 未配置")
        self.account_id = settings.futu_account
        self.adapter = FutuAdapter(account_id=self.account_id)
        self._client = self._build_client()

    def _build_client(self) -> ReadOnlyFutuClient:
        from futu import OpenSecTradeContext, TrdMarket, SecurityFirm
        ctx = OpenSecTradeContext(
            filter_trdmarket=TrdMarket.HK,
            host=settings.futu_opend_host,
            port=settings.futu_opend_port,
            security_firm=SecurityFirm.FUTUSECURITIES,
        )
        return ReadOnlyFutuClient(ctx)

    def fetch_positions(self) -> list[Position]:
        """拉取持仓 → 转换为统一 Position 列表。"""
        from futu import TrdEnv
        snapshot_time = datetime.now(timezone.utc)
        ret, data = self._client.position_list_query(trd_env=TrdEnv.REAL)
        if ret != 0:
            raise RuntimeError(f"富途 position_list_query 失败: ret={ret}, data={data}")
        if data is None or len(data) == 0:
            return []
        return self.adapter.dataframe_to_positions(data, snapshot_time)

    def sync_and_persist(self, db_session, triggered_by: str = "manual") -> int:
        """同步持仓并写入数据库。"""
        from services.broker_sync.repository import PositionSnapshotRepository
        from services.broker_sync.models import PositionSnapshot
        from services.broker_sync.position_upsert_service import PositionUpsertService

        repo = PositionSnapshotRepository(db_session)
        run = repo.create_run(
            broker="futu",
            account_id=self.account_id,
            sync_source="api",
            triggered_by=triggered_by,
        )

        last_exception: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                positions = self.fetch_positions()
                repo.persist_positions(run_id=run.id, positions=positions, finalize=False)

                snapshots = db_session.query(PositionSnapshot).filter_by(run_id=run.id).all()
                upsert_service = PositionUpsertService(db_session)
                upsert_report = upsert_service.upsert_from_snapshots(
                    snapshots,
                    broker="futu",
                    account_id=self.account_id,
                    sync_source="api",
                    commit=False,
                )

                if upsert_report["errors"]:
                    raise RuntimeError(f"业务表同步失败: {upsert_report['errors']}")

                repo.mark_run_succeeded(run.id, position_count=len(positions))
                return run.id

            except (ConnectionError, TimeoutError, OSError) as e:
                last_exception = e
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY_SECONDS)
                    continue
                db_session.rollback()
                repo.mark_run_failed(
                    run_id=run.id,
                    error_message=f"网络错误,重试 {self.MAX_RETRIES} 次后仍失败: {e}",
                    retry_count=attempt,
                )
                raise

            except (ValidationError, KeyError, AttributeError, ValueError, RuntimeError) as e:
                db_session.rollback()
                repo.mark_run_failed(
                    run_id=run.id,
                    error_message=f"数据格式错误(不重试): {type(e).__name__}: {e}",
                    retry_count=attempt,
                )
                raise

        if last_exception:
            raise last_exception
        raise RuntimeError("sync_and_persist 内部逻辑错误")

    def close(self):
        self._client.close()
