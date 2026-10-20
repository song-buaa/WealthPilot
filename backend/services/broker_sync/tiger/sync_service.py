"""老虎证券持仓同步服务。"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

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
        for kw in WRITE_METHOD_KEYWORDS:
            if kw in name.lower():
                raise RuntimeError(
                    f"READ_ONLY_MODE 已开启,拒绝调用写操作方法: {name}"
                )
        return getattr(self._inner, name)


class TigerSyncService:
    """老虎持仓同步主服务。"""

    def __init__(self):
        # 不在构造阶段抛配置异常：同步尝试必须先留下 run 记录，才能避免
        # 前端把历史成功状态误认为本次同步成功。
        self.account_id = settings.tiger_account or "__unconfigured__"
        self.adapter = TigerAdapter(account_id=self.account_id)
        self._trade_client: Optional[ReadOnlyTradeClient] = None

    @staticmethod
    def _validate_configuration() -> None:
        if not settings.tiger_id:
            raise RuntimeError("TIGER_ID 未配置")
        if not settings.tiger_account:
            raise RuntimeError("TIGER_ACCOUNT 未配置")
        if not settings.tiger_private_key_path:
            raise RuntimeError("TIGER_PRIVATE_KEY_PATH 未配置")

    def _ensure_trade_client(self) -> ReadOnlyTradeClient:
        if self._trade_client is None:
            self._validate_configuration()
            self._trade_client = self._build_trade_client()
        return self._trade_client

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
        trade_client = self._ensure_trade_client()
        # 老虎 SDK 默认 sec_type=STK,基金需要单独查询(API 不支持 sec_type=ALL)
        stk_positions = trade_client.get_positions(account=self.account_id, sec_type="STK")
        fund_positions = trade_client.get_positions(account=self.account_id, sec_type="FUND")
        if stk_positions is None or fund_positions is None:
            raise ValueError("INCOMPLETE_SOURCE")
        sdk_positions = list(stk_positions) + list(fund_positions)
        for position in sdk_positions:
            source_account = getattr(position, "account", None)
            if source_account is None or not str(source_account).strip():
                raise ValueError("SOURCE_ACCOUNT_UNPROVEN")
            if str(source_account) != self.account_id:
                raise ValueError("MIXED_SNAPSHOT_SCOPE")
        assets = trade_client.get_prime_assets(account=self.account_id)
        observed = getattr(assets, "account", None) or getattr(assets, "account_id", None)
        if observed is None or not str(observed).strip():
            raise ValueError("SOURCE_ACCOUNT_UNPROVEN")
        if str(observed) != self.account_id:
            raise ValueError("MIXED_SNAPSHOT_SCOPE")
        segment = assets.segments.get("S") if assets and getattr(assets, "segments", None) else None
        balances = getattr(segment, "currency_assets", None)
        if not isinstance(balances, dict) or not balances:
            raise ValueError("INCOMPLETE_SOURCE")
        # Empty securities need independent account-summary confirmation. A cash
        # row must not accidentally hide an anomalous empty holdings response.
        if not sdk_positions:
            # tigeropen 3.5.8 Segment exposes gross_position_value, not market_value.
            try:
                gross = Decimal(str(getattr(segment, "gross_position_value", None)))
            except (InvalidOperation, ValueError):
                raise ValueError("EMPTY_SOURCE_REQUIRES_REVIEW") from None
            if not gross.is_finite() or gross != 0:
                raise ValueError("EMPTY_SOURCE_REQUIRES_REVIEW")
        from services.broker_sync.schema import cash_position
        positions = self.adapter.to_positions(sdk_positions, snapshot_time)
        for currency, item in balances.items():
            if currency == "BASE":
                continue
            amount = getattr(item, "cash_balance", None)
            if amount is None:
                raise ValueError("INCOMPLETE_SOURCE")
            positions.append(cash_position("tiger", self.account_id, currency, amount, snapshot_time))
        if not any(p.broker_security_type == "CASH" for p in positions):
            raise ValueError("INCOMPLETE_SOURCE")
        # PortfolioAccount.update_timestamp is the SDK's server asset-update
        # millisecond epoch, NOT a security quote/position timestamp.
        cash_time = None
        try:
            value = getattr(assets, "update_timestamp", None)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 10**12:
                stamp = datetime.fromtimestamp(value/1000, timezone.utc)
                if stamp <= datetime.now(timezone.utc):
                    cash_time = stamp.isoformat()
        except (ValueError, OverflowError, OSError):
            pass
        self._source_metadata = {"complete":True, "source":"tiger_api",
            "coverage":["STK","FUND","CASH"], "positions_as_of":None,
            "cash_as_of":cash_time, "price_as_of":None, "issues":["BROKER_VALUATION_TIME_UNKNOWN"],
            "time_evidence":{"positions_as_of":"TIGER_3_5_8_POSITION_TIME_UNSUPPORTED",
                "price_as_of":"TIGER_3_5_8_PRICE_TIME_UNSUPPORTED",
                "cash_as_of":"TIGER_PORTFOLIO_ACCOUNT_UPDATE_TIMESTAMP" if cash_time else "TIGER_CASH_TIME_UNAVAILABLE"}}
        return positions

    def fetch_account_summary(self) -> dict:
        """拉取账户资产摘要（净值、购买力等）。"""
        assets = self._ensure_trade_client().get_assets(account=self.account_id)
        return {
            "raw": str(assets),
            "snapshot_time": datetime.now(timezone.utc).isoformat(),
        }

    # ── 持久化同步（写库）─────────────────────────────────────────

    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 5

    def sync_and_persist(self, db_session, triggered_by="manual"):
        from services.broker_sync.repository import PositionSnapshotRepository
        return PositionSnapshotRepository(db_session).synchronize(
            "tiger", self.account_id, self.fetch_positions, triggered_by=triggered_by,
            metadata=lambda:getattr(self, "_source_metadata", {}),
            retries=self.MAX_RETRIES, retry_delay=self.RETRY_DELAY_SECONDS)
