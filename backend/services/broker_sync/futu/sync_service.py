"""富途证券持仓同步服务（只读）。"""
from datetime import datetime, timezone
from typing import Optional


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
        if not str(self.account_id).isdigit():
            raise ValueError("EXPLICIT_FUTU_ACCOUNT_REQUIRED")
        ret, data = self._client.position_list_query(trd_env=TrdEnv.REAL, acc_id=int(self.account_id))
        if ret != 0:
            raise RuntimeError(f"富途 position_list_query 失败: ret={ret}, data={data}")
        if data is None or len(data) == 0:
            return []
        self._source_metadata = {"complete":True,"source":"futu_api","markets":["HK"],
            "positions_as_of":None,"price_as_of":None}
        return self.adapter.dataframe_to_positions(data, snapshot_time)

    def sync_and_persist(self, db_session, triggered_by="manual"):
        from services.broker_sync.repository import PositionSnapshotRepository
        return PositionSnapshotRepository(db_session).synchronize(
            "futu", self.account_id, self.fetch_positions, triggered_by=triggered_by,
            metadata=lambda:getattr(self, "_source_metadata", {}),
            retries=self.MAX_RETRIES, retry_delay=self.RETRY_DELAY_SECONDS)

    def close(self):
        self._client.close()
