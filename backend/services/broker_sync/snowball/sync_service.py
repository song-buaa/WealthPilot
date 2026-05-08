"""雪盈证券持仓同步服务（只读）。"""
import time
from datetime import datetime, timezone
from typing import Optional

from pydantic import ValidationError

from core.config import settings
from services.broker_sync.schema import Position
from services.broker_sync.snowball.adapter import SnowballAdapter


WRITE_METHOD_KEYWORDS = ("place_order", "cancel_order")


class ReadOnlySnowballClient:
    """SnbHttpClient 只读包装器。"""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name: str):
        if settings.snowball_read_only_mode:
            for kw in WRITE_METHOD_KEYWORDS:
                if kw in name.lower():
                    raise RuntimeError(
                        f"SNOWBALL_READ_ONLY_MODE 已开启,拒绝调用: {name}"
                    )
        return getattr(self._inner, name)


class SnowballSyncService:
    """雪盈持仓同步主服务。"""

    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 5

    def __init__(self):
        if not settings.snowball_account:
            raise RuntimeError("SNOWBALL_ACCOUNT 未配置")
        if not settings.snowball_secret_key:
            raise RuntimeError("SNOWBALL_SECRET_KEY 未配置")

        self.account_id = settings.snowball_account
        self.adapter = SnowballAdapter(account_id=self.account_id)
        self._client = self._build_client()

    def _build_client(self) -> ReadOnlySnowballClient:
        from snbpy.common.domain.snb_config import SnbConfig
        from snbpy.snb_api_client import SnbHttpClient

        config = SnbConfig()
        config.account = settings.snowball_account
        config.key = settings.snowball_secret_key
        config.sign_type = "None"
        config.snb_server = "openapi.snbsecurities.com"
        config.snb_port = "443"
        config.timeout = 10000
        config.schema = "https"

        client = SnbHttpClient(config)
        client.login()
        return ReadOnlySnowballClient(client)

    def fetch_positions(self) -> list[Position]:
        """拉取持仓 → 转换为统一 Position 列表。"""
        snapshot_time = datetime.now(timezone.utc)
        resp = self._client.get_position_list()

        # SDK 数据通过 _data 私有属性访问
        items = None
        if hasattr(resp, "_data") and resp._data:
            items = resp._data
        elif hasattr(resp, "result_data") and resp.result_data:
            items = resp.result_data

        if not items:
            return []

        if not isinstance(items, list):
            items = [items]

        return self.adapter.items_to_positions(items, snapshot_time)

    def sync_and_persist(self, db_session, triggered_by: str = "manual") -> int:
        """同步持仓并写入数据库。"""
        from services.broker_sync.repository import PositionSnapshotRepository
        from services.broker_sync.models import PositionSnapshot
        from services.broker_sync.position_upsert_service import PositionUpsertService

        repo = PositionSnapshotRepository(db_session)
        run = repo.create_run(
            broker="snowball",
            account_id=self.account_id,
            sync_source="api",
            triggered_by=triggered_by,
        )

        last_exception: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                positions = self.fetch_positions()
                repo.persist_positions(run_id=run.id, positions=positions)

                snapshots = db_session.query(PositionSnapshot).filter_by(run_id=run.id).all()
                upsert_service = PositionUpsertService(db_session)
                upsert_report = upsert_service.upsert_from_snapshots(snapshots)

                if upsert_report["errors"]:
                    raise RuntimeError(f"业务表同步失败: {upsert_report['errors']}")
                return run.id

            except (ConnectionError, TimeoutError, OSError) as e:
                last_exception = e
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY_SECONDS)
                    continue
                db_session.rollback()
                repo.mark_run_failed(
                    run_id=run.id,
                    error_message=f"网络错误,重试后失败: {e}",
                    retry_count=attempt,
                )
                raise

            except (ValidationError, KeyError, AttributeError, ValueError) as e:
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
