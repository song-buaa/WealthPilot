"""
WealthPilot v3.10 IBKRBrokerAdapter — Interactive Brokers BrokerAdapter 实现。

将 ib_async 2.x 封装为 v3.2 BrokerAdapter 接口契约。
OrderManager 通过依赖注入使用本适配器，不感知 IB 细节。

M1: 骨架 + Gateway 连接 + 四闸门 + orderRef + 基础状态映射
     不接 factory.py（M3 才做）。

事件循环隔离方案:
    ib_async 基于 asyncio，与 FastAPI 主事件循环共存会冲突。
    本 adapter 在独立后台线程中创建独立事件循环，所有 IB 调用
    通过 run_coroutine_threadsafe 发到该循环执行。对外接口是同步的，
    与 tiger adapter 一致。
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from decimal import Decimal
from typing import Optional

from backend.services.action.brokers.base import (
    BrokerAdapter,
    OrderRequest,
    OrderStatusUpdate,
)

logger = logging.getLogger(__name__)

# ── 安全配置 ─────────────────────────────────────────────────
ENABLE_IBKR_LIVE_TRADING = (
    os.getenv("ENABLE_IBKR_LIVE_TRADING", "false").lower() == "true"
)

# ── 业务常量 ─────────────────────────────────────────────────
SUPPORTED_MARKETS = {"US", "HK"}
MARKET_TO_EXCHANGE = {"US": "SMART", "HK": "SEHK"}
MARKET_TO_CURRENCY = {"US": "USD", "HK": "HKD"}

# IB OrderStatus → v3.2 状态字符串
# 保守原则: 拿不准一律 unknown，不为界面好看强行判终态
IB_TO_V32_STATUS = {
    "ApiPending": "submitted_to_broker",
    "PendingSubmit": "submitted_to_broker",
    "PreSubmitted": "broker_pending",
    "Submitted": "broker_pending",
    "PendingCancel": "broker_pending",  # 不提前判 cancelled
    "Filled": "filled",
    "Cancelled": "cancelled",
    "ApiCancelled": "cancelled",
    "Inactive": "unknown",    # 可能是拒单也可能是其他，保守映射 unknown
}


class IBKRBrokerAdapter(BrokerAdapter):
    """Interactive Brokers BrokerAdapter 实现。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4002,
        client_id: int = 1,
        account_id: str = "",
        timeout: float = 10.0,
    ):
        """初始化 IBKRBrokerAdapter。

        Args:
            host: IB Gateway/TWS 主机地址
            port: IB Gateway/TWS 端口 (4002=paper, 4001=live)
            client_id: IB API client ID
            account_id: IB 账户 ID (如 DU1234567)
            timeout: 连接超时秒数
        """
        self._host = host
        self._port = port
        self._client_id = client_id
        self._account_id = account_id
        self._timeout = timeout

        # ── 闸门 1: paper-only ────────────────────────────────
        if not ENABLE_IBKR_LIVE_TRADING:
            if account_id and not account_id.startswith("DU"):
                raise AssertionError(
                    f"实盘交易未开启(ENABLE_IBKR_LIVE_TRADING=false)，"
                    f"拒绝使用账号 {account_id}。"
                    f"模拟盘账号以 DU 开头。"
                )

        # 事件循环隔离: 独立线程 + 独立事件循环
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ib = None  # ib_async.IB 实例，延迟创建
        self._connected = False

    def _ensure_connected(self) -> None:
        """确保 IB 连接已建立。首次调用时启动后台线程。"""
        if self._connected and self._ib:
            return

        from ib_async import IB

        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever,
                daemon=True,
                name="ibkr-event-loop",
            )
            self._thread.start()

        self._ib = IB()

        future = asyncio.run_coroutine_threadsafe(
            self._ib.connectAsync(
                host=self._host,
                port=self._port,
                clientId=self._client_id,
                timeout=self._timeout,
            ),
            self._loop,
        )
        try:
            future.result(timeout=self._timeout + 5)
        except Exception as e:
            raise ConnectionError(
                f"IB Gateway 连接失败 ({self._host}:{self._port}): {e}"
            ) from e

        self._connected = True

        # 如果构造时没给 account_id，从 Gateway 获取
        if not self._account_id:
            accounts = self._run_sync(self._ib.managedAccounts)
            if accounts:
                self._account_id = accounts[0]
                logger.info("[IBKR] 自动获取账户: %s", self._account_id)

        logger.info(
            "[IBKR] 连接成功 %s:%d client=%d account=%s",
            self._host, self._port, self._client_id, self._account_id,
        )

    def _run_sync(self, coro_or_func, *args, **kwargs):
        """在 IB 事件循环线程中同步执行协程或同步函数。"""
        if asyncio.iscoroutinefunction(coro_or_func):
            coro = coro_or_func(*args, **kwargs)
        elif asyncio.iscoroutine(coro_or_func):
            coro = coro_or_func
        else:
            # 同步函数（如 ib.openTrades()），直接在循环线程中执行
            future = self._loop.call_soon_threadsafe(
                lambda: None  # no-op to wake loop
            )
            return coro_or_func(*args, **kwargs)

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=self._timeout + 5)

    # ── BrokerAdapter ABC ─────────────────────────────────────

    @property
    def broker_name(self) -> str:
        return "ibkr"

    def authenticate(self, credentials: dict) -> bool:
        """已连 Gateway 且账户在 managedAccounts()。"""
        try:
            self._ensure_connected()
            accounts = self._ib.managedAccounts()
            return self._account_id in accounts
        except Exception as e:
            logger.error("[IBKR] authenticate 失败: %s", e)
            return False

    def place_order(self, request: OrderRequest) -> OrderStatusUpdate:
        """提交订单到 IB。

        异常透传约定(同 tiger):
        - ConnectionError / TimeoutError 不 catch，由上层 OrderManager 处理。
        - 业务拒单在此方法内返回 rejected。
        """
        from ib_async import LimitOrder, Stock

        self._ensure_connected()

        # ── 闸门 3: order_type 白名单 ─────────────────────────
        order_type = request.order_type.upper()
        if order_type == "CONDITIONAL_LIMIT":
            return self._rejected(
                request,
                reason="IBKR v3.10 暂不支持条件限价单，请使用普通限价单",
                action="place_order_rejected_conditional",
            )
        if order_type != "LIMIT":
            return self._rejected(
                request,
                reason=f"IBKR v3.10 仅支持 LIMIT 单，收到 {request.order_type}",
                action="place_order_rejected_order_type",
            )

        # ── 闸门 2: market 白名单 ─────────────────────────────
        market, pure_symbol = self._parse_symbol(request.symbol)
        if market not in SUPPORTED_MARKETS:
            return self._rejected(
                request,
                reason=(
                    f"IBKR v3.10 不支持市场 {market}(symbol={request.symbol})。"
                    f"A 股交易请使用国金 QMT。"
                ),
                action="place_order_blocked_unsupported_market",
            )

        exchange = MARKET_TO_EXCHANGE[market]
        currency = MARKET_TO_CURRENCY[market]

        # 港股 symbol: IB 用不补零的原始代码或 4 位均可
        contract = Stock(symbol=pure_symbol, exchange=exchange, currency=currency)

        order = LimitOrder(
            action=request.side.upper(),
            totalQuantity=int(request.quantity),
            lmtPrice=float(request.limit_price),
        )
        # ── 闸门 4: outsideRth=False ─────────────────────────
        order.outsideRth = False
        # ── orderRef: 写入 WealthPilot 侧 order_record.id ────
        order.orderRef = request.local_order_id

        try:
            trade = self._ib.placeOrder(contract, order)
        except (ConnectionError, TimeoutError):
            raise  # 透传给上层
        except Exception as e:
            logger.warning("[IBKR] placeOrder 异常: %s", e)
            return self._rejected(
                request,
                reason=str(e),
                action="place_order_api_error",
            )

        # placeOrder 返回 Trade 对象，orderId 同步可用，permId 可能异步回填
        broker_order_id = str(trade.order.orderId)

        return OrderStatusUpdate(
            broker_order_id=broker_order_id,
            local_order_id=request.local_order_id,
            status="submitted_to_broker",
            filled_quantity=0,
            avg_filled_price=None,
            timestamp=int(time.time() * 1000),
            raw_response=self._build_raw(
                action="place_order",
                trade=trade,
                extra={
                    "symbol": request.symbol,
                    "market": market,
                    "currency": currency,
                    "limit_price": float(request.limit_price),
                    "quantity": request.quantity,
                    "side": request.side,
                    "order_type": "LIMIT",
                    "order_ref": request.local_order_id,
                },
            ),
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        """取消订单。

        IB 的 cancelOrder 接受 Order 对象，不是 order_id 字符串。
        需要从 openTrades() 反查 Trade 对象。
        """
        self._ensure_connected()

        trade = self._find_trade_by_order_id(broker_order_id)
        if trade is None:
            # 查不到 → 可能已终态，返回 False
            return False

        # 已终态不撤
        status = trade.orderStatus.status
        if status in ("Filled", "Cancelled", "ApiCancelled", "Inactive"):
            return False

        try:
            self._ib.cancelOrder(trade.order)
            return True
        except Exception as e:
            logger.warning("[IBKR] cancelOrder 异常: %s", e)
            return False

    def get_order_status(self, broker_order_id: str) -> OrderStatusUpdate:
        """查询订单最新状态。"""
        self._ensure_connected()

        trade = self._find_trade_by_order_id(broker_order_id)
        if trade is None:
            return OrderStatusUpdate(
                broker_order_id=broker_order_id,
                local_order_id="",
                status="unknown",
                raw_response={"broker": "ibkr", "action": "get_order_status_not_found"},
            )

        ib_status = trade.orderStatus.status
        mapped = IB_TO_V32_STATUS.get(ib_status, "unknown")

        return OrderStatusUpdate(
            broker_order_id=broker_order_id,
            local_order_id="",
            status=mapped,
            filled_quantity=int(trade.orderStatus.filled),
            avg_filled_price=(
                Decimal(str(trade.orderStatus.avgFillPrice))
                if trade.orderStatus.avgFillPrice
                else None
            ),
            timestamp=int(time.time() * 1000),
            raw_response=self._build_raw(action="get_order_status", trade=trade),
        )

    def list_open_orders(self) -> list[OrderStatusUpdate]:
        """列出所有未终态订单。"""
        self._ensure_connected()

        try:
            trades = self._ib.openTrades()
        except Exception as e:
            logger.warning("[IBKR] openTrades 异常: %s", e)
            return []

        result = []
        for t in trades:
            ib_status = t.orderStatus.status
            mapped = IB_TO_V32_STATUS.get(ib_status, "unknown")
            result.append(OrderStatusUpdate(
                broker_order_id=str(t.order.orderId),
                local_order_id="",
                status=mapped,
                filled_quantity=int(t.orderStatus.filled),
                avg_filled_price=(
                    Decimal(str(t.orderStatus.avgFillPrice))
                    if t.orderStatus.avgFillPrice
                    else None
                ),
                timestamp=int(time.time() * 1000),
                raw_response=self._build_raw(action="list_open_orders", trade=t),
            ))
        return result

    def get_positions(self) -> list[dict]:
        """获取当前持仓。"""
        self._ensure_connected()
        try:
            positions = self._ib.positions(account=self._account_id)
            return [
                {
                    "symbol": p.contract.symbol if p.contract else None,
                    "market": p.contract.exchange if p.contract else None,
                    "currency": p.contract.currency if p.contract else None,
                    "quantity": p.position,
                    "average_cost": float(p.avgCost or 0),
                    "market_value": float(p.position * (p.avgCost or 0)),
                }
                for p in positions
            ]
        except Exception as e:
            logger.warning("[IBKR] positions 异常: %s", e)
            return []

    def get_account_info(self) -> dict:
        """获取账户信息。"""
        self._ensure_connected()
        try:
            summary = self._ib.accountSummary(account=self._account_id)
            info = {"broker": "ibkr", "account_id": self._account_id}
            for item in summary:
                if item.tag in ("TotalCashValue", "NetLiquidation", "BuyingPower"):
                    info[item.tag] = float(item.value)
            return info
        except Exception as e:
            logger.warning("[IBKR] accountSummary 异常: %s", e)
            return {"broker": "ibkr", "error": str(e)}

    def shutdown(self) -> None:
        """断连 + 清理线程/事件循环。"""
        if self._ib and self._connected:
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._connected = False

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=5)

        self._ib = None
        self._loop = None
        self._thread = None
        logger.info("[IBKR] shutdown 完成")

    # ── 内部工具方法 ──────────────────────────────────────────

    def _find_trade_by_order_id(self, broker_order_id: str):
        """从 openTrades 中按 orderId 反查 Trade 对象。

        IB 的 cancelOrder 和 get_order_status 需要 Trade/Order 对象，
        不接受 order_id 字符串。
        """
        try:
            order_id_int = int(broker_order_id)
        except (ValueError, TypeError):
            return None

        # openTrades 返回当前 session 的所有 trade（含已完成的）
        for trade in self._ib.trades():
            if trade.order.orderId == order_id_int:
                return trade
        return None

    @staticmethod
    def _parse_symbol(symbol: str) -> tuple[str, str]:
        """解析 symbol -> (market, pure_symbol)。

        与 tiger.py 保持一致的逻辑。
        """
        if ":" in symbol:
            ticker, market = symbol.split(":", 1)
            return market.upper(), ticker
        if "." in symbol:
            market, pure = symbol.split(".", 1)
            return market.upper(), pure
        if symbol.isdigit():
            if len(symbol) in (4, 5):
                return "HK", symbol
            if len(symbol) == 6:
                return "CN", symbol
        return "US", symbol

    def _rejected(
        self,
        request: OrderRequest,
        reason: str,
        action: str,
    ) -> OrderStatusUpdate:
        """快捷构造 rejected 回报。"""
        return OrderStatusUpdate(
            broker_order_id=None,
            local_order_id=request.local_order_id,
            status="rejected",
            filled_quantity=0,
            avg_filled_price=None,
            timestamp=int(time.time() * 1000),
            raw_response=self._build_raw(
                action=action,
                extra={"reason": reason, "symbol": request.symbol},
            ),
        )

    def _build_raw(
        self,
        action: str,
        trade=None,
        extra: dict | None = None,
    ) -> dict:
        """构造 raw_response。"""
        r: dict = {
            "broker": "ibkr",
            "account_id": self._account_id,
            "action": action,
            "outside_rth": False,
        }
        if trade is not None:
            r["broker_order_id"] = str(trade.order.orderId)
            r["perm_id"] = trade.order.permId
            r["ib_status"] = trade.orderStatus.status
            r["order_ref"] = trade.order.orderRef
            mapped = IB_TO_V32_STATUS.get(trade.orderStatus.status, "unknown")
            r["mapped_status"] = mapped
        if extra:
            r.update(extra)
        return r
