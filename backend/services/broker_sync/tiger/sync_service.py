"""老虎证券持仓同步服务。"""
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.common.util.signature_utils import read_private_key
from tigeropen.common.consts import Language
from tigeropen.trade.trade_client import TradeClient

from core.config import settings
from services.broker_sync.schema import Position
from services.broker_sync.tiger.adapter import TigerAdapter


# 只读模式下被禁止的方法关键字
WRITE_METHOD_KEYWORDS = (
    "place_order", "cancel_order", "modify_order",
    "preview_order", "submit_order",
)


class ReadOnlyTradeClient:
    """TradeClient 只读包装器。"""

    def __init__(self, inner: TradeClient):
        self._inner = inner

    def __getattr__(self, name):
        if settings.tiger_read_only_mode:
            for kw in WRITE_METHOD_KEYWORDS:
                if kw in name.lower():
                    raise RuntimeError(
                        f"READ_ONLY_MODE 已开启,拒绝调用写操作方法: {name}"
                    )
        return getattr(self._inner, name)


class TigerSyncService:
    """老虎持仓同步主服务。"""

    def __init__(self):
        if not settings.tiger_id:
            raise RuntimeError("TIGER_ID 未配置")
        if not settings.tiger_account:
            raise RuntimeError("TIGER_ACCOUNT 未配置")

        self.account_id = settings.tiger_account
        self.adapter = TigerAdapter(account_id=self.account_id)
        self._trade_client = self._build_trade_client()

    def _build_trade_client(self) -> ReadOnlyTradeClient:
        project_root = Path(__file__).parent.parent.parent.parent.parent
        pk_path = project_root / settings.tiger_private_key_path

        config = TigerOpenClientConfig()
        config.private_key = read_private_key(str(pk_path))
        config.tiger_id = settings.tiger_id
        config.account = self.account_id
        config.language = Language.zh_CN

        return ReadOnlyTradeClient(TradeClient(config))

    def fetch_positions(self) -> list[Position]:
        """拉取持仓 → 转换为统一 Position 列表。"""
        snapshot_time = datetime.now(timezone.utc)
        sdk_positions = self._trade_client.get_positions(account=self.account_id)

        if not sdk_positions:
            return []

        return self.adapter.to_positions(sdk_positions, snapshot_time)

    def fetch_account_summary(self) -> dict:
        """拉取账户资产摘要（净值、购买力等）。"""
        assets = self._trade_client.get_assets(account=self.account_id)
        return {
            "raw": str(assets),
            "snapshot_time": datetime.now(timezone.utc).isoformat(),
        }

    # ── 持久化同步（写库）─────────────────────────────────────────

    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 5

    def sync_and_persist(
        self,
        db_session,
        triggered_by: str = "manual",
    ) -> int:
        """
        同步持仓并写入数据库。

        重试策略:
        - 网络/API 错误:重试最多 MAX_RETRIES 次
        - 数据格式错误:立即失败,不重试

        返回 run_id。失败时抛出最后一次的异常。
        """
        from tigeropen.common.exceptions import ApiException
        from services.broker_sync.repository import PositionSnapshotRepository

        repo = PositionSnapshotRepository(db_session)
        run = repo.create_run(
            broker="tiger",
            account_id=self.account_id,
            sync_source="api",
            triggered_by=triggered_by,
        )

        last_exception: Optional[Exception] = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                positions = self.fetch_positions()
                repo.persist_positions(
                    run_id=run.id,
                    positions=positions,
                    total_market_value_cnh=None,
                )
                return run.id

            except (ApiException, ConnectionError, TimeoutError, OSError) as e:
                last_exception = e
                if attempt < self.MAX_RETRIES:
                    time.sleep(self.RETRY_DELAY_SECONDS)
                    continue
                db_session.rollback()
                repo.mark_run_failed(
                    run_id=run.id,
                    error_message=f"网络错误,重试 {self.MAX_RETRIES} 次后仍失败: {type(e).__name__}: {e}",
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
