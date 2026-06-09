"""
WealthPilot v3.4 TigerBrokerAdapter — Tiger 老虎证券 BrokerAdapter 实现。

将 Tiger SDK (tigeropen 3.5.8) 封装为 v3.2 BrokerAdapter 接口契约。
OrderManager 通过依赖注入使用本适配器,不感知 Tiger SDK 细节。

M1.1: 基础骨架 + 4 个安全闸门 + 基础状态映射
M1.2: 错误兜底完整覆盖(EXPIRED 二义性 / not_found 重试 / ApiException 精细分类)
M1.3: 沙箱端到端验证
M2:   凭证管理(CredentialProvider 集成,私钥不再从文件路径读取)
"""
from __future__ import annotations

import logging
import os
import time
from decimal import Decimal
from typing import Optional

from tigeropen.common.consts import Language
from tigeropen.common.exceptions import ApiException
from tigeropen.common.util.order_utils import limit_order
from tigeropen.tiger_open_config import TigerOpenClientConfig
from tigeropen.trade.domain.contract import Contract
from tigeropen.trade.domain.order import OrderStatus as TigerOrderStatus
from tigeropen.trade.trade_client import TradeClient

from backend.services.action.brokers.base import (
    BrokerAdapter,
    OrderRequest,
    OrderStatusUpdate,
)
from backend.services.action.brokers.credentials import (
    CredentialProvider,
    CredentialNotFoundError,
)

logger = logging.getLogger(__name__)

# ── 安全配置 ─────────────────────────────────────────────────
ENABLE_TIGER_LIVE_TRADING = (
    os.getenv("ENABLE_TIGER_LIVE_TRADING", "false").lower() == "true"
)
TIGER_PAPER_ACCOUNT = "21995161433588262"

# ── 业务常量 ─────────────────────────────────────────────────
SUPPORTED_MARKETS = {"US", "HK"}
MARKET_CURRENCY = {"US": "USD", "HK": "HKD"}

# Tiger OrderStatus → v3.2 状态字符串(EXPIRED 不在此表,走 _map_expired)
TIGER_TO_V32_STATUS = {
    TigerOrderStatus.PENDING_NEW: "submitted_to_broker",
    TigerOrderStatus.NEW: "submitted_to_broker",
    TigerOrderStatus.HELD: "broker_pending",
    TigerOrderStatus.PARTIALLY_FILLED: "partially_filled",
    TigerOrderStatus.FILLED: "filled",
    TigerOrderStatus.CANCELLED: "cancelled",
    TigerOrderStatus.PENDING_CANCEL: "cancelled",
    TigerOrderStatus.REJECTED: "rejected",
}

# ── EXPIRED 二义性关键词(护栏 §3.3) ──────────────────────────
REJECTED_KEYWORDS = ["购买力", "资金不足", "合约", "权限", "禁止", "不正确"]
EXPIRED_KEYWORDS = ["过期", "超时", "expired", "timeout", "day order"]


# ── 自定义异常 ────────────────────────────────────────────────

class UnsupportedMarketError(Exception):
    """v3.4 Tiger Adapter 不支持的市场。"""


class OrphanOrderError(ConnectionError):
    """订单在 Tiger 端 not_found,本地可能有脏数据。

    设计决策: 继承自 ConnectionError,使 OrderManager 既有的
    ``except (ConnectionError, TimeoutError)`` 能兜住此异常,
    符合 v3.2 OrderManager 把网络类异常映射成 unknown 状态的语义。
    M3 OrderManager 改造时可增加显式 catch OrphanOrderError 做专项处理。
    """


class TigerBrokerAdapter(BrokerAdapter):
    """Tiger 老虎证券 BrokerAdapter 实现。"""

    def __init__(
        self,
        credential_provider: CredentialProvider,
        broker_key: str = "tiger.paper",
        language: Language = Language.zh_CN,
    ):
        """初始化 TigerBrokerAdapter。

        M2 改造: 从 CredentialProvider 加载凭证,不再接受 private_key_path。
        凭证加载后仅保留 account_id,不在实例上长期持有完整私钥。

        Args:
            credential_provider: 凭证提供者(KeyringCredentialProvider / InMemoryCredentialProvider)
            broker_key: 凭证标识,如 'tiger.paper' / 'tiger.live'
            language: Tiger SDK 语言设置
        """
        creds = credential_provider.load_or_raise(broker_key)
        tiger_id = creds["tiger_id"]
        account_id = creds["account_id"]
        private_key_pem = creds["private_key_pem"]

        # ── 闸门 1: paper-only ────────────────────────────────
        if not ENABLE_TIGER_LIVE_TRADING:
            assert account_id == TIGER_PAPER_ACCOUNT, (
                f"实盘交易未开启(ENABLE_TIGER_LIVE_TRADING=false),"
                f"拒绝使用账号 {account_id}。"
                f"模拟盘账号: {TIGER_PAPER_ACCOUNT}"
            )

        # 从 PEM 字符串提取 base64 内容(tigeropen SDK 期望无 header 的 base64)
        private_key_raw = (
            private_key_pem
            .replace("-----BEGIN RSA PRIVATE KEY-----", "")
            .replace("-----END RSA PRIVATE KEY-----", "")
            .strip()
        )

        config = TigerOpenClientConfig(sandbox_debug=False)
        config.private_key = private_key_raw
        config.tiger_id = tiger_id
        config.account = account_id
        config.language = language

        self._trade_client = TradeClient(config)
        self._account_id = account_id
        self._is_paper = getattr(config, "is_paper", account_id == TIGER_PAPER_ACCOUNT)

        # 用完即丢: 不保留凭证引用
        del creds, private_key_pem, private_key_raw

        logger.info(
            "[TigerBrokerAdapter] 初始化完成 account=***%s is_paper=%s",
            account_id[-5:],
            self._is_paper,
        )

    # ── BrokerAdapter ABC ─────────────────────────────────────

    @property
    def broker_name(self) -> str:
        return "tiger"

    def authenticate(self, credentials: dict) -> bool:
        try:
            accounts = self._trade_client.get_managed_accounts()
            return any(
                getattr(acc, "account", None) == self._account_id
                for acc in accounts
            )
        except Exception as e:
            logger.error("[TigerBrokerAdapter] authenticate 失败: %s", e)
            return False

    def place_order(self, request: OrderRequest) -> OrderStatusUpdate:
        """提交订单到 Tiger。

        异常透传约定(base.py):
        - ConnectionError / TimeoutError 不 catch,由上层 OrderManager 处理。
        - ApiException 在此方法内分类后返回 rejected。
        """
        # ── 闸门 3: 仅支持 LIMIT ──────────────────────────────
        if request.order_type.upper() not in ("LIMIT", "CONDITIONAL_LIMIT"):
            return self._rejected(
                request,
                reason=f"v3.4 Tiger Adapter 仅支持 LIMIT 单,收到 {request.order_type}",
                action="place_order_rejected_order_type",
            )

        # ── 闸门 2: market 白名单 ─────────────────────────────
        market, pure_symbol = self._parse_symbol(request.symbol)
        if market not in SUPPORTED_MARKETS:
            return self._rejected(
                request,
                reason=(
                    f"v3.4 Tiger Adapter 不支持市场 {market}(symbol={request.symbol})。"
                    f"A 股交易请使用国金 QMT(v3.5+)。"
                ),
                action="place_order_blocked_unsupported_market",
            )

        currency = MARKET_CURRENCY[market]
        # Tiger SDK 期望港股 5 位代码(00700), WP 内部用 4 位(0700)
        tiger_symbol = pure_symbol.zfill(5) if market == "HK" and pure_symbol.isdigit() else pure_symbol
        contract = Contract(
            symbol=tiger_symbol, currency=currency, sec_type="STK", market=market,
        )

        order = limit_order(
            account=self._account_id,
            contract=contract,
            action=request.side.upper(),
            limit_price=float(request.limit_price),
            quantity=int(request.quantity),
        )
        # ── 闸门 4: outside_rth=False ─────────────────────────
        order.outside_rth = False

        try:
            self._trade_client.place_order(order)
        except (ConnectionError, TimeoutError):
            raise  # 透传给上层
        except ApiException as e:
            logger.warning("[TigerBrokerAdapter] place_order ApiException: %s", e)
            extra = {"symbol": request.symbol}
            err_msg = (getattr(e, "msg", "") or str(e)).lower()
            if not any(kw in err_msg for kw in [
                "合约", "bad_request", "权限", "permission", "购买力", "资金不足", "不正确",
            ]):
                extra["unknown_api_error"] = True
            return self._rejected(
                request, reason=str(e), action="place_order_api_error", error=e, extra=extra,
            )

        return OrderStatusUpdate(
            broker_order_id=str(order.id),
            local_order_id=request.local_order_id,
            status="submitted_to_broker",
            filled_quantity=0,
            avg_filled_price=None,
            timestamp=int(time.time() * 1000),
            raw_response=self._build_raw(
                action="place_order",
                tiger_order=order,
                extra={
                    "symbol": request.symbol,
                    "market": market,
                    "currency": currency,
                    "limit_price": float(request.limit_price),
                    "quantity": request.quantity,
                    "side": request.side,
                    "order_type": "LIMIT",
                },
            ),
        )

    def get_order_status(self, broker_order_id: str) -> OrderStatusUpdate:
        """查询订单最新状态。

        注意: 返回的 OrderStatusUpdate.local_order_id 为空字符串,
        因为 Adapter 不持有该信息。调用方(OrderManager)必须使用
        调用前已知的 local_order_id,不要读返回值的 local_order_id 字段。

        异常透传约定:
        - ConnectionError / TimeoutError 不 catch,由上层处理。
        - not_found ApiException 走指数退避重试,耗尽后抛 OrphanOrderError
          (继承 ConnectionError,上层既有 catch 能兜住)。
        """
        try:
            tiger_order = self._get_order_with_retry(broker_order_id)
        except (ConnectionError, TimeoutError):
            raise  # OrphanOrderError 也从这里透传
        except ApiException as e:
            logger.warning("[TigerBrokerAdapter] get_order_status ApiException: %s", e)
            return OrderStatusUpdate(
                broker_order_id=broker_order_id,
                local_order_id="",
                status="unknown",
                raw_response=self._build_raw(action="get_order_status_error", error=e),
            )

        mapped, expired_extras = self._map_status(tiger_order)
        raw = self._build_raw(action="get_order_status", tiger_order=tiger_order)
        if expired_extras:
            raw.update(expired_extras)

        return OrderStatusUpdate(
            broker_order_id=broker_order_id,
            local_order_id="",
            status=mapped,
            filled_quantity=getattr(tiger_order, "filled", 0),
            avg_filled_price=(
                Decimal(str(tiger_order.avg_fill_price))
                if getattr(tiger_order, "avg_fill_price", None)
                else None
            ),
            timestamp=getattr(tiger_order, "trade_time", 0) or 0,
            raw_response=raw,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        """取消订单。已终态返回 False,成功返回 True。"""
        try:
            self._trade_client.cancel_order(id=int(broker_order_id))
            return True
        except ApiException:
            return False

    def list_open_orders(self) -> list[OrderStatusUpdate]:
        """列出所有未终态订单。

        注意: 返回的 OrderStatusUpdate.local_order_id 为空字符串,
        因为 Adapter 不持有该信息。调用方必须自行关联 local_order_id。
        """
        try:
            orders = self._trade_client.get_open_orders(account=self._account_id)
            result = []
            for o in (orders or []):
                mapped, _ = self._map_status(o)
                result.append(OrderStatusUpdate(
                    broker_order_id=str(o.id),
                    local_order_id="",
                    status=mapped,
                    filled_quantity=getattr(o, "filled", 0),
                    avg_filled_price=(
                        Decimal(str(o.avg_fill_price))
                        if getattr(o, "avg_fill_price", None)
                        else None
                    ),
                    timestamp=getattr(o, "trade_time", 0) or 0,
                    raw_response=self._build_raw(action="list_open_orders", tiger_order=o),
                ))
            return result
        except ApiException as e:
            logger.warning("[TigerBrokerAdapter] list_open_orders ApiException: %s", e)
            return []

    def get_positions(self) -> list[dict]:
        try:
            positions = self._trade_client.get_positions(account=self._account_id)
            return [
                {
                    "symbol": getattr(p.contract, "symbol", None) if p.contract else None,
                    "market": getattr(p.contract, "market", None) if p.contract else None,
                    "currency": getattr(p.contract, "currency", None) if p.contract else None,
                    "quantity": getattr(p, "quantity", 0),
                    "average_cost": float(getattr(p, "average_cost", 0) or 0),
                    "market_value": float(getattr(p, "market_value", 0) or 0),
                }
                for p in (positions or [])
            ]
        except ApiException as e:
            logger.warning("[TigerBrokerAdapter] get_positions ApiException: %s", e)
            return []

    def get_account_info(self) -> dict:
        try:
            assets = self._trade_client.get_assets(account=self._account_id)
            if not assets:
                return {"broker": "tiger", "error": "no_account_data"}
            summary = getattr(assets[0], "summary", assets[0])
            # gross_position_value: 优先从 summary 取（与 net_liq/cash 口径一致）
            gpv = float(getattr(summary, "gross_position_value", 0) or 0)
            gpv_source = "summary"
            if gpv in (0.0, float("inf")):
                # fallback: 从 SecuritySegment("S") 取（仅证券段，口径可能窄于 summary）
                seg_s = assets[0].segment("S") if hasattr(assets[0], "segment") else None
                gpv = float(getattr(seg_s, "gross_position_value", 0) or 0) if seg_s else 0.0
                if gpv not in (0.0, float("inf")):
                    gpv_source = "SecuritySegment"
                    logger.warning(
                        "[TigerBrokerAdapter] gross_position_value 从 SecuritySegment 取得"
                        "（summary 无此字段），口径可能窄于 net_liquidation/cash"
                    )
            return {
                "broker": "tiger",
                "account_id_masked": f"***{self._account_id[-5:]}",
                "is_paper": self._is_paper,
                "currency": getattr(summary, "currency", "USD"),
                "cash": float(getattr(summary, "cash", 0) or 0),
                "buying_power": float(getattr(summary, "buying_power", 0) or 0),
                "net_liquidation": float(getattr(summary, "net_liquidation", 0) or 0),
                "gross_position_value": gpv,
                "gross_position_value_source": gpv_source,
            }
        except ApiException as e:
            logger.warning("[TigerBrokerAdapter] get_account_info ApiException: %s", e)
            return {"broker": "tiger", "error": str(e)}

    # ── 内部工具方法 ──────────────────────────────────────────

    @staticmethod
    def _parse_symbol(symbol: str) -> tuple[str, str]:
        """解析 symbol -> (market, pure_symbol)。

        主路径: TICKER:MARKET (v3.4 统一格式), 如 'LI:US' → ('US', 'LI')。
        兼容旧格式:
          - 'US.SPY' (MARKET.TICKER 点分隔)
          - 'SPY' (纯代码, 启发式推断)
        """
        # 主路径: TICKER:MARKET
        if ":" in symbol:
            ticker, market = symbol.split(":", 1)
            return market.upper(), ticker
        # 兼容: MARKET.TICKER
        if "." in symbol:
            market, pure = symbol.split(".", 1)
            return market.upper(), pure
        # 降级: 纯代码启发式
        if symbol.isdigit():
            if len(symbol) in (4, 5):
                return "HK", symbol
            if len(symbol) == 6:
                return "CN", symbol
        return "US", symbol

    def _map_status(self, tiger_order) -> tuple[str, dict]:
        """Tiger OrderStatus -> (v3.2 状态字符串, raw_response 额外字段)。

        EXPIRED 走 _map_expired 做二义性分类;其他状态直接查表。
        """
        status = getattr(tiger_order, "status", None)
        if status == TigerOrderStatus.EXPIRED:
            return self._map_expired(tiger_order)
        return TIGER_TO_V32_STATUS.get(status, "unknown"), {}

    @staticmethod
    def _map_expired(tiger_order) -> tuple[str, dict]:
        """Tiger EXPIRED 二义性映射(护栏 §3.3)。

        Returns:
            (mapped_status, raw_response_extras)
        """
        reason = (getattr(tiger_order, "reason", "") or "").lower()

        if any(kw in reason for kw in REJECTED_KEYWORDS):
            return "rejected", {"expired_resolved_as": "rejected"}

        if any(kw in reason for kw in EXPIRED_KEYWORDS):
            return "expired", {"expired_resolved_as": "expired"}

        return "unknown", {
            "tiger_expired_unknown_reason": True,
            "expired_resolved_as": "unknown",
        }

    def _get_order_with_retry(
        self, broker_order_id: str, max_retries: int = 2,
    ):
        """get_order with 指数退避重试(护栏 §3.8)。

        Raises:
            OrphanOrderError: 重试 max_retries 次后仍 not_found
            ApiException: 非 not_found 错误,不重试,直接抛出
        """
        for attempt in range(max_retries + 1):
            try:
                return self._trade_client.get_order(id=int(broker_order_id))
            except ApiException as e:
                err_msg = (getattr(e, "msg", "") or str(e)).lower()
                if "not_found" not in err_msg:
                    raise

                if attempt < max_retries:
                    wait = 2 ** attempt  # 1s, 2s
                    logger.warning(
                        "[TigerBrokerAdapter] get_order not_found, "
                        "retry %d/%d after %ds: %s",
                        attempt + 1, max_retries, wait, broker_order_id,
                    )
                    time.sleep(wait)
                    continue

                raise OrphanOrderError(
                    f"Tiger 端订单 {broker_order_id} not_found,"
                    f"重试 {max_retries} 次后仍失败,可能为本地脏数据"
                ) from e

    def _rejected(
        self,
        request: OrderRequest,
        reason: str,
        action: str,
        error: Exception | None = None,
        extra: dict | None = None,
    ) -> OrderStatusUpdate:
        """快捷构造 rejected 回报。"""
        raw = self._build_raw(action=action, error=error, extra={"reason": reason, "symbol": request.symbol})
        if extra:
            raw.update(extra)
        return OrderStatusUpdate(
            broker_order_id=None,
            local_order_id=request.local_order_id,
            status="rejected",
            filled_quantity=0,
            avg_filled_price=None,
            timestamp=int(time.time() * 1000),
            raw_response=raw,
        )

    def _build_raw(
        self,
        action: str,
        tiger_order=None,
        error: Exception | None = None,
        extra: dict | None = None,
    ) -> dict:
        """构造 raw_response(护栏 §4.1 字段)。Adapter 不含 fx_rate_to_cny。"""
        r: dict = {
            "broker": "tiger",
            "account_type": "paper" if self._is_paper else "live",
            "account_id_masked": f"***{self._account_id[-5:]}",
            "action": action,
            "outside_rth": False,
        }
        if tiger_order is not None:
            r["broker_order_id"] = str(tiger_order.id) if tiger_order.id else None
            r["tiger_status"] = (
                str(tiger_order.status) if hasattr(tiger_order, "status") else None
            )
            mapped, _ = self._map_status(tiger_order)
            r["mapped_status"] = mapped
            r["reason"] = getattr(tiger_order, "reason", None)
        if error is not None:
            r["raw_error_code"] = getattr(error, "code", None)
            r["raw_error_message"] = str(error)
        if extra:
            r.update(extra)
        return r
