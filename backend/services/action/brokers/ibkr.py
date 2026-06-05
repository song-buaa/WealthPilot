"""
WealthPilot v3.10 IBKRBrokerAdapter — Interactive Brokers BrokerAdapter 实现。

将 ib_async 2.x 封装为 v3.2 BrokerAdapter 接口契约。
OrderManager 通过依赖注入使用本适配器，不感知 IB 细节。

M1: 骨架 + Gateway 连接 + 四闸门 + orderRef + 基础状态映射
M2: Inactive 二义性分流 + permId 收口 + orderRef 幂等反查
     + 异常透传契约补全 + not_found 重试

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
# Inactive 不在此表，走 _map_inactive 二义性分流（同 tiger 的 _map_expired）
IB_TO_V32_STATUS = {
    "ApiPending": "submitted_to_broker",
    "PendingSubmit": "submitted_to_broker",
    "PreSubmitted": "broker_pending",
    "Submitted": "broker_pending",
    "PendingCancel": "broker_pending",  # 不提前判 cancelled
    "Filled": "filled",
    "Cancelled": "cancelled",
    "ApiCancelled": "cancelled",
}

# ── Inactive 二义性: 拒单类 errorCode（IBKR 常见）──
# TradeLogEntry.errorCode 命中这些 → 判 rejected
# 参考: https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/#error-codes
REJECTED_ERROR_CODES = {
    201,  # Order rejected - reason given in error text
    203,  # Security is not available for trading
    110,  # Price does not conform to minimum price variation
    104,  # Can't modify a filled order
    105,  # Order being modified does not match original order
    106,  # Transmit order failed: can't transmit
    2110, # Connectivity between IB and exchange lost
}

# 拒单类关键词（errorCode=0 时 fallback 看 message）
REJECTED_KEYWORDS = ["rejected", "insufficient", "buying power", "margin",
                     "not available", "not permissioned", "invalid"]

# permId 回填等待配置
PERM_ID_WAIT_SECONDS = 2.0
PERM_ID_POLL_INTERVAL = 0.1

# not_found 重试配置
NOT_FOUND_MAX_RETRIES = 2
NOT_FOUND_RETRY_DELAYS = [1, 2]  # 指数退避秒数


# ── 自定义异常 ────────────────────────────────────────────────

class OrphanOrderError(ConnectionError):
    """订单在 IB 端 not_found，本地可能有脏数据。

    继承自 ConnectionError，使 OrderManager 既有的
    ``except (ConnectionError, TimeoutError)`` 能兜住此异常。
    """


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

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ib = None
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

        if not self._account_id:
            accounts = self._ib.managedAccounts()
            if accounts:
                self._account_id = accounts[0]
                logger.info("[IBKR] 自动获取账户: %s", self._account_id)

        logger.info(
            "[IBKR] 连接成功 %s:%d client=%d account=%s",
            self._host, self._port, self._client_id, self._account_id,
        )

    # ── BrokerAdapter ABC ─────────────────────────────────────

    @property
    def broker_name(self) -> str:
        return "ibkr"

    def authenticate(self, credentials: dict) -> bool:
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

        # ── permId 收口: 等待 Gateway 回填 permId ──
        perm_id = self._wait_for_perm_id(trade)
        broker_order_id = str(perm_id) if perm_id else str(trade.order.orderId)

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
                    "con_id": getattr(contract, "conId", None),
                },
            ),
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        """取消订单。

        按 permId 在 trades() 反查 Trade 对象再 cancelOrder。
        查不到/已终态返回 False，受理成功 True。
        """
        self._ensure_connected()

        trade = self._find_trade(broker_order_id)
        if trade is None:
            return False

        status = trade.orderStatus.status
        if status in ("Filled", "Cancelled", "ApiCancelled"):
            return False
        # Inactive 可能是临时状态，仍尝试撤单
        try:
            self._ib.cancelOrder(trade.order)
            return True
        except Exception as e:
            logger.warning("[IBKR] cancelOrder 异常: %s", e)
            return False

    def get_order_status(self, broker_order_id: str) -> OrderStatusUpdate:
        """查询订单最新状态。

        not_found 走指数退避重试，耗尽抛 OrphanOrderError。
        """
        self._ensure_connected()

        trade = self._find_trade_with_retry(broker_order_id)

        ib_status = trade.orderStatus.status
        mapped, extras = self._map_status(trade)

        raw = self._build_raw(action="get_order_status", trade=trade)
        if extras:
            raw.update(extras)

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
            raw_response=raw,
        )

    def list_open_orders(self) -> list[OrderStatusUpdate]:
        self._ensure_connected()
        try:
            trades = self._ib.openTrades()
        except Exception as e:
            logger.warning("[IBKR] openTrades 异常: %s", e)
            return []

        result = []
        for t in trades:
            mapped, _ = self._map_status(t)
            result.append(OrderStatusUpdate(
                broker_order_id=self._get_broker_order_id(t),
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

    # ── orderRef 幂等反查（PRD §3.6.1）──────────────────────

    def find_order_by_ref(self, order_ref: str) -> Optional[OrderStatusUpdate]:
        """按 orderRef 在当前 session 的 trades 中反查订单。

        用于超时后判断"订单是否其实已提交成功"，避免盲目重下。
        M2 交付能力 + 单测，M3 端到端接入 OrderManager。

        Returns:
            OrderStatusUpdate（命中）/ None（未命中）
        """
        self._ensure_connected()

        for trade in self._ib.trades():
            if trade.order.orderRef == order_ref:
                mapped, extras = self._map_status(trade)
                raw = self._build_raw(
                    action="find_order_by_ref", trade=trade,
                )
                if extras:
                    raw.update(extras)
                return OrderStatusUpdate(
                    broker_order_id=self._get_broker_order_id(trade),
                    local_order_id=order_ref,
                    status=mapped,
                    filled_quantity=int(trade.orderStatus.filled),
                    avg_filled_price=(
                        Decimal(str(trade.orderStatus.avgFillPrice))
                        if trade.orderStatus.avgFillPrice
                        else None
                    ),
                    timestamp=int(time.time() * 1000),
                    raw_response=raw,
                )

        return None

    # ── 内部: 状态映射 ───────────────────────────────────────

    @staticmethod
    def _map_status(trade) -> tuple[str, dict]:
        """IB status → (v3.2 状态, raw_response 额外字段)。

        Inactive 走 _map_inactive 做二义性分类（同 tiger 的 _map_expired）。
        """
        ib_status = trade.orderStatus.status
        if ib_status == "Inactive":
            return IBKRBrokerAdapter._map_inactive(trade)
        return IB_TO_V32_STATUS.get(ib_status, "unknown"), {}

    @staticmethod
    def _map_inactive(trade) -> tuple[str, dict]:
        """Inactive 二义性分流（同 tiger _map_expired 思路）。

        IBKR 的 Inactive ∈ DoneStates，但语义模糊:
        - 下单被拒 → errorCode + message 在 trade.log
        - 盘前临时 inactive → 无 error
        - 其他不明情况

        分流逻辑:
        1. trade.log 中有 errorCode 命中 REJECTED_ERROR_CODES → rejected
        2. trade.log message 命中 REJECTED_KEYWORDS → rejected
        3. 无 error log 且 whyHeld 非空 → broker_pending（可能是临时）
        4. 都不是 → unknown（标注原因待人工确认）

        errorCode 获取方式: TradeLogEntry.errorCode (int)，每个 TradeLogEntry
        记录一次状态变更或错误事件。Inactive 时通常伴随 errorCode != 0 的 log。
        """
        error_code = 0
        error_message = ""

        # 从 trade.log 最后几条里找 errorCode 或 error message
        for entry in reversed(trade.log):
            ec = getattr(entry, "errorCode", 0)
            msg = getattr(entry, "message", "")
            if ec != 0:
                error_code = ec
                error_message = msg
                break
            if msg and not error_message:
                # errorCode=0 但有 message（如 Inactive 的文字描述）
                error_message = msg

        extras = {
            "inactive_error_code": error_code,
            "inactive_error_message": error_message,
        }

        # 分支 1: 命中拒单类 errorCode
        if error_code in REJECTED_ERROR_CODES:
            extras["inactive_resolved_as"] = "rejected"
            return "rejected", extras

        # 分支 2: errorCode=0 但 message 命中拒单关键词
        if error_code == 0 and error_message:
            msg_lower = error_message.lower()
            if any(kw in msg_lower for kw in REJECTED_KEYWORDS):
                extras["inactive_resolved_as"] = "rejected"
                return "rejected", extras

        # 分支 3: 无 error + whyHeld 非空 → 临时 inactive，保持 broker_pending
        why_held = getattr(trade.orderStatus, "whyHeld", "")
        if error_code == 0 and not error_message and why_held:
            extras["inactive_resolved_as"] = "broker_pending"
            extras["why_held"] = why_held
            return "broker_pending", extras

        # 分支 4: 都不是 → unknown
        extras["inactive_resolved_as"] = "unknown"
        return "unknown", extras

    # ── 内部: 订单查找 ───────────────────────────────────────

    def _find_trade(self, broker_order_id: str):
        """按 permId（主键）在 trades() 中反查 Trade 对象。

        M2: broker_order_id 是 permId（由 place_order 时等待回填后写入）。
        兼容: 如果是纯数字且 permId 匹配不到，回退查 orderId。
        """
        try:
            perm_id_int = int(broker_order_id)
        except (ValueError, TypeError):
            return None

        # 优先按 permId 查
        for trade in self._ib.trades():
            if trade.order.permId == perm_id_int:
                return trade

        # 兼容回退: 按 orderId 查（M1 存的旧数据可能是 orderId）
        for trade in self._ib.trades():
            if trade.order.orderId == perm_id_int:
                return trade

        return None

    def _find_trade_with_retry(self, broker_order_id: str):
        """带指数退避重试的订单查找。

        耗尽重试后抛 OrphanOrderError（继承 ConnectionError）。
        """
        for attempt in range(NOT_FOUND_MAX_RETRIES + 1):
            trade = self._find_trade(broker_order_id)
            if trade is not None:
                return trade

            if attempt < NOT_FOUND_MAX_RETRIES:
                wait = NOT_FOUND_RETRY_DELAYS[attempt]
                logger.warning(
                    "[IBKR] 订单 %s not_found, retry %d/%d after %ds",
                    broker_order_id, attempt + 1, NOT_FOUND_MAX_RETRIES, wait,
                )
                time.sleep(wait)
                continue

            raise OrphanOrderError(
                f"IB 端订单 {broker_order_id} not_found，"
                f"重试 {NOT_FOUND_MAX_RETRIES} 次后仍失败，可能为本地脏数据"
            )

    # ── 内部: permId 工具 ────────────────────────────────────

    @staticmethod
    def _wait_for_perm_id(trade, timeout: float = PERM_ID_WAIT_SECONDS) -> int:
        """等待 Gateway 回填 permId。

        permId 获取时机: placeOrder 返回的 Trade 对象的 order.permId 可能
        仍为 0（Gateway 异步回填）。通过短暂轮询 trade.order.permId 和
        trade.orderStatus.permId 等待回填。

        Returns:
            permId (>0) 或 0（超时未回填，fallback 用 orderId）
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # permId 可能出现在 order 或 orderStatus 上
            perm = trade.order.permId or trade.orderStatus.permId
            if perm and perm > 0:
                return perm
            time.sleep(PERM_ID_POLL_INTERVAL)
        logger.warning(
            "[IBKR] permId 回填超时 (orderId=%s)，fallback 用 orderId",
            trade.order.orderId,
        )
        return 0

    @staticmethod
    def _get_broker_order_id(trade) -> str:
        """从 Trade 对象获取 broker_order_id（优先 permId）。"""
        perm = trade.order.permId or trade.orderStatus.permId
        if perm and perm > 0:
            return str(perm)
        return str(trade.order.orderId)

    # ── 内部: 通用工具 ───────────────────────────────────────

    @staticmethod
    def _parse_symbol(symbol: str) -> tuple[str, str]:
        """解析 symbol -> (market, pure_symbol)。与 tiger.py 一致。"""
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
        """构造 raw_response（对齐 tiger _build_raw + 多 ID 字段）。"""
        r: dict = {
            "broker": "ibkr",
            "account_id": self._account_id,
            "client_id": self._client_id,
            "action": action,
            "outside_rth": False,
        }
        if trade is not None:
            r["order_id"] = trade.order.orderId
            r["perm_id"] = trade.order.permId
            r["broker_order_id"] = self._get_broker_order_id(trade)
            r["ib_status"] = trade.orderStatus.status
            r["order_ref"] = trade.order.orderRef
            mapped, _ = self._map_status(trade)
            r["mapped_status"] = mapped
            # 合约基本信息
            if trade.contract:
                r["con_id"] = getattr(trade.contract, "conId", None)
                r["contract_symbol"] = getattr(trade.contract, "symbol", None)
                r["contract_exchange"] = getattr(trade.contract, "exchange", None)
                r["contract_currency"] = getattr(trade.contract, "currency", None)
        if extra:
            r.update(extra)
        return r
