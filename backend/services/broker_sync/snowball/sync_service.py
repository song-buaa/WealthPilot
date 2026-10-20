"""雪盈证券持仓同步服务（只读）。"""
import os
from datetime import datetime, timezone
from typing import Optional


from core.config import settings
from services.broker_sync.schema import Position
from services.broker_sync.snowball.adapter import IBKRPortfolioAdapter, SnowballAdapter


WRITE_METHOD_KEYWORDS = ("place_order", "cancel_order", "modify_order", "submit_order", "replace_order")


class ReadOnlySnowballClient:
    """SnbHttpClient 只读包装器。"""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name: str):
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

    def __init__(self, ibkr_client=None):
        self._source_channel = (
            "ibkr" if (ibkr_client is not None
                       or "ibkr" in os.environ.get("BROKER_MODE", "").lower()
                       or (settings.ibkr_account and not settings.snowball_secret_key))
            else "snowball"
        )
        if self._source_channel == "ibkr":
            if not settings.ibkr_account:
                raise RuntimeError("IBKR_ACCOUNT 未配置")
            self.account_id = settings.ibkr_account
            self.adapter = IBKRPortfolioAdapter(account_id=self.account_id)
            if ibkr_client is None:
                from backend.api.action import _get_ibkr_adapter
                ibkr_client = _get_ibkr_adapter()
            self._client = ibkr_client
            if self._client.broker_name != "ibkr":
                raise RuntimeError("BROKER_MODE=ibkr 但未取得 IBKRBrokerAdapter")
            return

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
        if self._source_channel == "ibkr":
            account_info = self._client.get_account_info()
            if account_info.get("account_id") != self.account_id:
                raise RuntimeError("IBKR_SYNC_ACCOUNT_MISMATCH")
            raw_positions = self._client.get_positions()
            if raw_positions is None:
                raise ValueError("INCOMPLETE_SOURCE")
            if not raw_positions and not account_info.get("account_snapshot_complete"):
                raise ValueError("EMPTY_SOURCE_REQUIRES_REVIEW")
            if not account_info.get("cash_balances"):
                raise ValueError("INCOMPLETE_SOURCE")
            self._source_metadata = {"complete":True, "source":"ibkr_account_download",
                "positions_as_of":None, "cash_as_of":account_info.get("cash_as_of"),
                "queried_at":account_info.get("account_queried_at"),
                "time_evidence":{"positions_as_of":"IBKR_PORTFOLIO_TIME_UNSUPPORTED",
                    "price_as_of":"IBKR_PORTFOLIO_PRICE_TIME_UNSUPPORTED",
                    "cash_as_of":"IBKR_ACCOUNT_VALUE_TIME_UNSUPPORTED"},
                "price_as_of":None, "issues":["BROKER_VALUATION_TIME_UNKNOWN"] if raw_positions else []}
            return self.adapter.to_positions(
                raw_positions,
                account_info.get("cash_balances", []),
                snapshot_time,
            )

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

    def sync_and_persist(self, db_session, triggered_by="manual"):
        from services.broker_sync.repository import PositionSnapshotRepository
        return PositionSnapshotRepository(db_session).synchronize(
            "snowball", self.account_id, self.fetch_positions, triggered_by=triggered_by,
            metadata=lambda:getattr(self, "_source_metadata", {}),
            retries=self.MAX_RETRIES, retry_delay=self.RETRY_DELAY_SECONDS)
