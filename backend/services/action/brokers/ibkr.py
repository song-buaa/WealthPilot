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
import concurrent.futures
import inspect
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
IBKR_READ_ONLY_MODE = (
    os.getenv("IBKR_READ_ONLY_MODE", "true").lower() == "true"
)

# 账户前缀校验（闸门 1）
# Paper: "DU" 前缀（实测 DUQ629797）
# Live: 默认 "U"（个人实盘）。FA 子账户 "F"、机构 "I" 等需用户确认后扩展。
# 待用户用真实 live 账户号确认后可调整此元组。
IBKR_PAPER_PREFIX = "DU"
IBKR_LIVE_ACCOUNT_PREFIXES = tuple(
    os.getenv("IBKR_LIVE_ACCOUNT_PREFIXES", "U").split(",")
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

# ── Inactive 二义性: 拒单类 errorCode ──────────────────────
# 纪律: 此表只收录真实观测到的拒单码（探针/真实回报证据）。
#        新增任何码前必须有实测证据，不得照文档或印象添加。
# 来源: M2.5 开市探针实测（docs/v3.10/m2_5_probe_open_result.md）
# 排除: 202 是正常撤单确认，明确不在此表中。
REJECTED_ERROR_CODES = {
    200,  # No security definition found — 无效合约（探针 2a: 直接 Cancelled）
    201,  # Order rejected — 保证金不足等（探针 2b: 进 Inactive，errorCode 经 error callback 异步到达）
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

def _validate_account_prefix(account_id: str, context: str = "") -> None:
    """闸门 1 正向校验：账户类型必须与连接意图一致。

    - paper (ENABLE=false): 必须 DU 开头
    - live  (ENABLE=true):  必须匹配 IBKR_LIVE_ACCOUNT_PREFIXES（默认 U），且不能 DU 开头
    - live read-only (READ_ONLY=true, ENABLE=false): 可连接已验证的 live 前缀；
      下单与撤单会被本地硬拒绝
    - 空/None 一律拒绝

    Raises:
        AssertionError（沿用原有异常类型，OrderManager 不特殊处理）
    """
    if not account_id or not account_id.strip():
        raise AssertionError(
            f"[闸门1 {context}] 账户为空，拒绝连接。"
        )

    masked = f"***{account_id[-4:]}" if len(account_id) > 4 else account_id

    is_live_account = any(
        account_id.startswith(prefix) for prefix in IBKR_LIVE_ACCOUNT_PREFIXES
    )
    if IBKR_READ_ONLY_MODE and not ENABLE_IBKR_LIVE_TRADING and is_live_account:
        return

    if ENABLE_IBKR_LIVE_TRADING:
        # live 模式：必须匹配 live 前缀，且不能是 paper (DU)
        if account_id.startswith(IBKR_PAPER_PREFIX):
            raise AssertionError(
                f"[闸门1 {context}] 实盘模式(ENABLE_IBKR_LIVE_TRADING=true)，"
                f"但账户 {masked} 以 {IBKR_PAPER_PREFIX} 开头（Paper 账户）。"
                f"请配置实盘账户或关闭实盘开关。"
            )
        if not is_live_account:
            raise AssertionError(
                f"[闸门1 {context}] 实盘模式(ENABLE_IBKR_LIVE_TRADING=true)，"
                f"但账户 {masked} 前缀不在允许列表 {IBKR_LIVE_ACCOUNT_PREFIXES} 中。"
                f"如为 FA/机构账户，请设 IBKR_LIVE_ACCOUNT_PREFIXES 环境变量。"
            )
    else:
        # paper 模式：必须 DU 开头
        if not account_id.startswith(IBKR_PAPER_PREFIX):
            raise AssertionError(
                f"[闸门1 {context}] Paper 模式(ENABLE_IBKR_LIVE_TRADING=false)，"
                f"但账户 {masked} 不以 {IBKR_PAPER_PREFIX} 开头。"
                f"模拟盘账号以 DU 开头。"
            )


def _is_live_read_only_account(account_id: str) -> bool:
    """只读验收允许 Live 连接，但绝不允许交易 mutation。"""
    return (
        IBKR_READ_ONLY_MODE
        and any(account_id.startswith(prefix) for prefix in IBKR_LIVE_ACCOUNT_PREFIXES)
    )


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
        self._account_id = account_id  # 可能为空，连接后从 managedAccounts() 取
        self._timeout = timeout
        self._account_verified = False  # 闸门 1 延迟到连接后校验

        # ── 闸门 1 预检: 构造时若已知 account_id → 正向校验前缀
        if account_id:
            _validate_account_prefix(account_id, "构造时预检")

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ib = None
        self._connected = False

        # M2.5: error callback 抓取的 errorCode 映射
        # key: orderId (int), value: {"errorCode": int, "errorString": str}
        # 线程安全: errorEvent 在 ib_async 的事件循环线程中触发，
        # _map_inactive 在调用线程中读取。dict 的单次赋值在 CPython 下是原子的，
        # 且写入发生在读取之前（error callback 先于 status 查询），无需额外锁。
        self._error_codes: dict[int, dict] = {}

    def _run_on_loop(self, operation, *, timeout: float | None = None):
        """在 adapter 专属 event loop 执行一次 IB 调用并等待普通结果。

        ``operation`` 必须在 loop thread 内创建和消费 IB runtime 对象；调用者只能
        得到已脱离 ib_async 生命周期的普通 Python 数据。超时会取消 coroutine，不能
        被静默转换为“空持仓/空订单”。
        """
        if self._loop is None or not self._loop.is_running():
            raise ConnectionError("IBKR event loop 未运行")

        call_timeout = timeout if timeout is not None else self._timeout

        async def invoke():
            result = operation()
            if inspect.isawaitable(result):
                return await asyncio.wait_for(result, timeout=call_timeout)
            return result

        future = asyncio.run_coroutine_threadsafe(invoke(), self._loop)
        try:
            return future.result(timeout=call_timeout + 1)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(
                f"IBKR 调用超时（{call_timeout}s）"
            ) from exc

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

        try:
            async def connect_and_snapshot_accounts():
                self._ib = IB()
                await self._ib.connectAsync(
                    host=self._host,
                    port=self._port,
                    clientId=self._client_id,
                    timeout=self._timeout,
                    readonly=IBKR_READ_ONLY_MODE,
                )
                self._ib.errorEvent += self._on_ib_error
                return list(self._ib.managedAccounts())

            accounts = self._run_on_loop(
                connect_and_snapshot_accounts,
                timeout=self._timeout + 5,
            )
        except Exception as e:
            self._ib = None
            raise ConnectionError(
                f"IB Gateway 连接失败 ({self._host}:{self._port}): {e}"
            ) from e

        self._connected = True

        # ── 闸门 1 真校验: 基于 managedAccounts() 的真实账户 ──
        # 探针实测: order.account 是空字符串，唯一可靠来源是 managedAccounts()
        self._resolve_and_verify_account(accounts)

        logger.info(
            "[IBKR] 连接成功 %s:%d client=%d account=%s",
            self._host, self._port, self._client_id, self._account_id,
        )

    def _resolve_and_verify_account(self, accounts: list[str] | None = None) -> None:
        """从 managedAccounts() 解析真实账户并做 paper-only 断言。

        探针发现: placeOrder 后 order.account 是空字符串，
        唯一可靠的账户来源是 ib.managedAccounts() → list[str]。

        逻辑:
        1. 取 managedAccounts()，空列表 → 报错
        2. 如果 config 指定了 account_id → 在列表中找匹配项，找不到 → 报错
        3. 如果 config 没指定 → 取第一个
        4. paper-only 断言: 非 DU 开头 + 实盘未开启 → 拒绝
        """
        if accounts is None:
            accounts = self._run_on_loop(
                lambda: list(self._ib.managedAccounts()),
            )
        if not accounts:
            raise RuntimeError(
                "IB Gateway 未返回任何账户 (managedAccounts 为空)。"
                "请检查 TWS/Gateway 是否已登录。"
            )

        if self._account_id:
            # config 指定了账户 → 必须在 managedAccounts 中
            if self._account_id not in accounts:
                raise RuntimeError(
                    f"IBKR_ACCOUNT={self._account_id} 不在 Gateway 的"
                    f"managedAccounts 中: {accounts}"
                )
        else:
            # config 未指定 → 取第一个
            self._account_id = accounts[0]
            logger.info("[IBKR] 从 managedAccounts 自动获取账户: %s", self._account_id)

        # ── 闸门 1 真校验 ──
        try:
            _validate_account_prefix(self._account_id, "连接后校验")
        except AssertionError:
            self._run_on_loop(lambda: self._ib.disconnect())
            self._connected = False
            raise

        self._account_verified = True

    # ── BrokerAdapter ABC ─────────────────────────────────────

    @property
    def broker_name(self) -> str:
        return "ibkr"

    def authenticate(self, credentials: dict) -> bool:
        try:
            self._ensure_connected()
            accounts = self._run_on_loop(lambda: list(self._ib.managedAccounts()))
            return self._account_id in accounts
        except (ConnectionError, TimeoutError):
            raise
        except Exception as e:
            logger.error("[IBKR] authenticate 失败: %s", e)
            return False

    def place_order(self, request: OrderRequest) -> OrderStatusUpdate:
        """提交订单到 IB。

        异常透传约定(同 tiger):
        - ConnectionError / TimeoutError 不 catch，由上层 OrderManager 处理。
        - 业务拒单在此方法内返回 rejected。
        """
        if _is_live_read_only_account(self._account_id):
            return self._rejected(
                request,
                reason="IBKR_READ_ONLY_MODE=true：真实账户只读模式禁止下单",
                action="place_order_blocked_read_only",
            )

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

        try:
            async def submit_on_loop() -> dict:
                from ib_async import LimitOrder, Stock

                exchange = MARKET_TO_EXCHANGE[market]
                currency = MARKET_TO_CURRENCY[market]
                contract = Stock(
                    symbol=pure_symbol, exchange=exchange, currency=currency,
                )
                order = LimitOrder(
                    action=request.side.upper(),
                    totalQuantity=int(request.quantity),
                    lmtPrice=float(request.limit_price),
                    tif="DAY",
                )
                # ── 闸门 4: outsideRth=False ─────────────────
                order.outsideRth = False
                order.orderRef = request.local_order_id
                trade = self._ib.placeOrder(contract, order)

                deadline = time.monotonic() + PERM_ID_WAIT_SECONDS
                while time.monotonic() < deadline:
                    if trade.order.permId or trade.orderStatus.permId:
                        break
                    await asyncio.sleep(PERM_ID_POLL_INTERVAL)
                return self._snapshot_trade(trade)

            snapshot = self._run_on_loop(
                submit_on_loop,
                timeout=self._timeout + PERM_ID_WAIT_SECONDS,
            )
        except (ConnectionError, TimeoutError):
            raise  # 透传给上层
        except Exception as e:
            logger.warning("[IBKR] placeOrder 异常: %s", e)
            return self._rejected(
                request,
                reason=str(e),
                action="place_order_api_error",
            )

        broker_order_id = self._snapshot_broker_order_id(snapshot)
        raw = self._build_raw_from_snapshot("place_order", snapshot)
        raw.update({
            "symbol": request.symbol,
            "market": market,
            "currency": MARKET_TO_CURRENCY[market],
            "limit_price": float(request.limit_price),
            "quantity": request.quantity,
            "side": request.side,
            "order_type": "LIMIT",
            "order_ref": request.local_order_id,
        })

        return OrderStatusUpdate(
            broker_order_id=broker_order_id,
            local_order_id=request.local_order_id,
            status="submitted_to_broker",
            filled_quantity=0,
            avg_filled_price=None,
            timestamp=int(time.time() * 1000),
            raw_response=raw,
        )

    def cancel_order(self, broker_order_id: str) -> bool:
        """取消订单。

        按 permId 在 trades() 反查 Trade 对象再 cancelOrder。
        查不到/已终态返回 False，受理成功 True。
        """
        if _is_live_read_only_account(self._account_id):
            logger.warning("[IBKR] 真实账户只读模式禁止撤单")
            return False

        self._ensure_connected()
        try:
            broker_id = int(broker_order_id)
        except (TypeError, ValueError):
            return False

        def cancel_on_loop() -> bool:
            for trade in self._ib.trades():
                if trade.order.permId == broker_id or trade.order.orderId == broker_id:
                    if trade.orderStatus.status in ("Filled", "Cancelled", "ApiCancelled"):
                        return False
                    # Inactive 可能是临时状态，仍尝试撤单
                    self._ib.cancelOrder(trade.order)
                    return True
            return False

        try:
            return self._run_on_loop(cancel_on_loop)
        except Exception as e:
            logger.warning("[IBKR] cancelOrder 异常: %s", e)
            return False

    def get_order_status(self, broker_order_id: str) -> OrderStatusUpdate:
        """查询订单最新状态。

        not_found 走指数退避重试，耗尽抛 OrphanOrderError。
        """
        self._ensure_connected()

        snapshot = self._find_trade_with_retry(broker_order_id)
        return self._status_update_from_snapshot(
            snapshot,
            action="get_order_status",
            broker_order_id=broker_order_id,
        )

    def list_open_orders(self) -> list[OrderStatusUpdate]:
        self._ensure_connected()
        try:
            async def fetch_open_order_snapshots():
                await self._ib.reqOpenOrdersAsync()
                return [
                    self._snapshot_trade(trade)
                    for trade in self._ib.openTrades()
                ]

            snapshots = self._run_on_loop(fetch_open_order_snapshots)
        except (ConnectionError, TimeoutError):
            raise
        except Exception as exc:
            raise ConnectionError(f"IBKR 查询已有订单失败: {exc}") from exc

        return [
            self._status_update_from_snapshot(snapshot, action="list_open_orders")
            for snapshot in snapshots
        ]

    def get_positions(self) -> list[dict]:
        self._ensure_connected()
        try:
            async def fetch_position_snapshots():
                snapshots = []
                for item in list(self._ib.portfolio(account=self._account_id)):
                    contract = item.contract
                    details = await self._ib.reqContractDetailsAsync(contract)
                    detail = details[0] if details else None
                    qualified = detail.contract if detail else contract
                    snapshots.append({
                        "symbol": qualified.symbol,
                        "local_symbol": qualified.localSymbol,
                        "sec_type": qualified.secType,
                        "exchange": qualified.exchange,
                        "primary_exchange": qualified.primaryExchange,
                        "currency": qualified.currency,
                        "con_id": qualified.conId,
                        "long_name": detail.longName if detail else qualified.localSymbol,
                        "category": detail.category if detail else "",
                        "subcategory": detail.subcategory if detail else "",
                        "industry": detail.industry if detail else "",
                        "quantity": float(item.position or 0),
                        "average_cost": float(item.averageCost or 0),
                        "current_price": float(item.marketPrice or 0),
                        "market_value": float(item.marketValue or 0),
                        "unrealized_pnl": float(item.unrealizedPNL or 0),
                        "realized_pnl": float(item.realizedPNL or 0),
                    })
                return snapshots

            return self._run_on_loop(fetch_position_snapshots)
        except (ConnectionError, TimeoutError):
            raise
        except Exception as exc:
            raise ConnectionError(f"IBKR 查询持仓失败: {exc}") from exc

    def get_account_info(self) -> dict:
        self._ensure_connected()
        try:
            async def fetch_account_info():
                summary = await self._ib.accountSummaryAsync(self._account_id)
                info = {"broker": "ibkr", "account_id": self._account_id}
                for item in summary:
                    if item.tag in ("TotalCashValue", "NetLiquidation", "BuyingPower"):
                        info[item.tag] = float(item.value)
                # Dashboard 现金以逐币种 CashBalance 为真值；BASE 是折算汇总，
                # 与各原币行同时使用会重复计算。
                info["cash_balances"] = [
                    {"currency": item.currency, "amount": float(item.value)}
                    for item in self._ib.accountValues(account=self._account_id)
                    if item.tag == "CashBalance" and item.currency != "BASE"
                ]
                return info

            return self._run_on_loop(fetch_account_info)
        except (ConnectionError, TimeoutError):
            raise
        except Exception as exc:
            raise ConnectionError(f"IBKR 查询账户摘要失败: {exc}") from exc

    def shutdown(self) -> None:
        if self._ib and self._connected:
            try:
                self._run_on_loop(lambda: self._ib.disconnect())
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

        for snapshot in self._read_trade_snapshots():
            if snapshot["order_ref"] == order_ref:
                return self._status_update_from_snapshot(
                    snapshot,
                    action="find_order_by_ref",
                    local_order_id=order_ref,
                )

        return None

    # ── 内部: error callback 处理（M2.5）─────────────────────

    def _on_ib_error(self, reqId: int, errorCode: int, errorString: str, contract) -> None:
        """IB error callback。记录 orderId → errorCode 映射。

        探针实测发现: errorCode 201（保证金不足）不在 trade.log 里
        （trade.log 显示 errorCode=0），而是通过此 callback 异步到达。
        _map_inactive 需要同时查 trade.log 和此映射才能正确分流。
        """
        if reqId > 0 and errorCode in REJECTED_ERROR_CODES:
            self._error_codes[reqId] = {
                "errorCode": errorCode,
                "errorString": errorString,
            }
            logger.warning(
                "[IBKR] error callback: orderId=%d errorCode=%d msg=%s",
                reqId, errorCode, errorString[:200],
            )

    # ── 内部: 状态映射 ───────────────────────────────────────

    def _map_status(self, trade) -> tuple[str, dict]:
        """IB status → (v3.2 状态, raw_response 额外字段)。

        Inactive 走 _map_inactive 做二义性分类（同 tiger 的 _map_expired）。
        """
        ib_status = trade.orderStatus.status
        if ib_status == "Inactive":
            return self._map_inactive(trade)
        return IB_TO_V32_STATUS.get(ib_status, "unknown"), {}

    def _map_inactive(self, trade) -> tuple[str, dict]:
        """Inactive 二义性分流（同 tiger _map_expired 思路）。

        IBKR 的 Inactive ∈ DoneStates，但语义模糊:
        - 下单被拒 → errorCode + message
        - 盘前临时 inactive → 无 error
        - 其他不明情况

        errorCode 双来源合并（M2.5 探针校准）:
        - 来源 1: trade.log 中的 TradeLogEntry.errorCode（大部分 error 走这里）
        - 来源 2: error callback 抓取的 _error_codes[orderId]（201 等异步 error 走这里）
        探针实测: 201 保证金不足时 trade.log errorCode=0，但 error callback 带 201。
        两个来源取并集，任一命中拒单码即判 rejected。

        分流逻辑:
        1. errorCode（来源 1 或 2）命中 REJECTED_ERROR_CODES → rejected
        2. message 命中 REJECTED_KEYWORDS → rejected
        3. 无 error + whyHeld 非空 → broker_pending（临时）
        4. 都不是 → unknown（待人工确认）
        """
        # 来源 1: trade.log
        log_error_code = 0
        log_error_message = ""
        for entry in reversed(trade.log):
            ec = getattr(entry, "errorCode", 0)
            msg = getattr(entry, "message", "")
            if ec != 0:
                log_error_code = ec
                log_error_message = msg
                break
            if msg and not log_error_message:
                log_error_message = msg

        # 来源 2: error callback 映射
        cb_info = self._error_codes.get(trade.order.orderId, {})
        cb_error_code = cb_info.get("errorCode", 0)
        cb_error_message = cb_info.get("errorString", "")

        # 合并: 取非零的那个（如果两个都非零，优先 callback 的，因为它更权威）
        error_code = cb_error_code if cb_error_code != 0 else log_error_code
        error_message = cb_error_message if cb_error_code != 0 else log_error_message

        extras = {
            "inactive_error_code": error_code,
            "inactive_error_message": error_message,
            "inactive_log_error_code": log_error_code,
            "inactive_cb_error_code": cb_error_code,
        }

        # 分支 1: 命中拒单类 errorCode（任一来源）
        if error_code in REJECTED_ERROR_CODES:
            extras["inactive_resolved_as"] = "rejected"
            return "rejected", extras
        # 也查另一个来源（防两个 code 不同但都应判拒单的边缘情况）
        if log_error_code in REJECTED_ERROR_CODES or cb_error_code in REJECTED_ERROR_CODES:
            extras["inactive_resolved_as"] = "rejected"
            return "rejected", extras

        # 分支 2: message 命中拒单关键词 → unknown（降级，不判 rejected）
        # 纪律: rejected 是终态，只有 REJECTED_ERROR_CODES 数字码有权判定。
        # keyword 匹配作为信号保留在 raw_response 里，交人工确认。
        combined_msg = (error_message or "") + " " + (cb_error_message or "")
        if combined_msg.strip():
            msg_lower = combined_msg.lower()
            matched_kw = [kw for kw in REJECTED_KEYWORDS if kw in msg_lower]
            if matched_kw:
                extras["inactive_resolved_as"] = "unknown"
                extras["keyword_matched"] = matched_kw
                extras["keyword_note"] = "message 含拒单关键词但无实测 errorCode，降级为 unknown 待人工确认"
                return "unknown", extras

        # 分支 3: 无 error + whyHeld 非空 → 临时 inactive
        why_held = getattr(trade.orderStatus, "whyHeld", "")
        if error_code == 0 and not error_message and why_held:
            extras["inactive_resolved_as"] = "broker_pending"
            extras["why_held"] = why_held
            return "broker_pending", extras

        # 分支 4: 都不是 → unknown
        extras["inactive_resolved_as"] = "unknown"
        return "unknown", extras

    # ── 内部: 订单查找 ───────────────────────────────────────

    def _read_trade_snapshots(self) -> list[dict]:
        """在 IB loop 内将 session trade 缓存转为普通 Python 快照。"""
        try:
            return self._run_on_loop(
                lambda: [self._snapshot_trade(trade) for trade in self._ib.trades()]
            )
        except (ConnectionError, TimeoutError):
            raise
        except Exception as exc:
            raise ConnectionError(f"IBKR 查询订单缓存失败: {exc}") from exc

    def _find_trade(self, broker_order_id: str) -> Optional[dict]:
        """按 permId（主键）在 snapshots 中查订单，不把 Trade 跨线程返回。

        M2: broker_order_id 是 permId（由 place_order 时等待回填后写入）。
        兼容: 如果是纯数字且 permId 匹配不到，回退查 orderId。
        """
        try:
            perm_id_int = int(broker_order_id)
        except (ValueError, TypeError):
            return None

        snapshots = self._read_trade_snapshots()
        # 优先按 permId 查
        for snapshot in snapshots:
            if snapshot["perm_id"] == perm_id_int:
                return snapshot

        # 兼容回退: 按 orderId 查（M1 存的旧数据可能是 orderId）
        for snapshot in snapshots:
            if snapshot["order_id"] == perm_id_int:
                return snapshot

        return None

    def _find_trade_with_retry(self, broker_order_id: str):
        """带指数退避重试的订单查找。

        耗尽重试后抛 OrphanOrderError（继承 ConnectionError）。
        """
        for attempt in range(NOT_FOUND_MAX_RETRIES + 1):
            snapshot = self._find_trade(broker_order_id)
            if snapshot is not None:
                return snapshot

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

    @staticmethod
    def _snapshot_trade(trade) -> dict:
        """从 IB Trade 提取跨线程安全的、无 runtime 引用的值对象。"""
        contract = getattr(trade, "contract", None)
        order = trade.order
        status = trade.orderStatus
        return {
            "order_id": int(order.orderId or 0),
            "perm_id": int(order.permId or status.permId or 0),
            "order_ref": order.orderRef or "",
            "ib_status": status.status or "",
            "filled_quantity": int(status.filled or 0),
            "avg_filled_price": float(status.avgFillPrice or 0),
            "why_held": status.whyHeld or "",
            "con_id": getattr(contract, "conId", None) if contract else None,
            "contract_symbol": getattr(contract, "symbol", None) if contract else None,
            "contract_exchange": getattr(contract, "exchange", None) if contract else None,
            "contract_currency": getattr(contract, "currency", None) if contract else None,
            "log": [
                {
                    "error_code": int(getattr(entry, "errorCode", 0) or 0),
                    "message": str(getattr(entry, "message", "") or ""),
                }
                for entry in (trade.log or [])
            ],
        }

    def _map_snapshot_status(self, snapshot: dict) -> tuple[str, dict]:
        ib_status = snapshot["ib_status"]
        if ib_status != "Inactive":
            return IB_TO_V32_STATUS.get(ib_status, "unknown"), {}

        log_error_code = 0
        log_error_message = ""
        for entry in reversed(snapshot["log"]):
            if entry["error_code"]:
                log_error_code = entry["error_code"]
                log_error_message = entry["message"]
                break
            if entry["message"] and not log_error_message:
                log_error_message = entry["message"]

        cb_info = self._error_codes.get(snapshot["order_id"], {})
        cb_error_code = cb_info.get("errorCode", 0)
        cb_error_message = cb_info.get("errorString", "")
        error_code = cb_error_code or log_error_code
        error_message = cb_error_message if cb_error_code else log_error_message
        extras = {
            "inactive_error_code": error_code,
            "inactive_error_message": error_message,
            "inactive_log_error_code": log_error_code,
            "inactive_cb_error_code": cb_error_code,
        }
        if error_code in REJECTED_ERROR_CODES or (
            log_error_code in REJECTED_ERROR_CODES
            or cb_error_code in REJECTED_ERROR_CODES
        ):
            extras["inactive_resolved_as"] = "rejected"
            return "rejected", extras

        combined_msg = f"{error_message or ''} {cb_error_message or ''}".lower()
        matched_kw = [kw for kw in REJECTED_KEYWORDS if kw in combined_msg]
        if matched_kw:
            extras.update({
                "inactive_resolved_as": "unknown",
                "keyword_matched": matched_kw,
                "keyword_note": "message 含拒单关键词但无实测 errorCode，降级为 unknown 待人工确认",
            })
            return "unknown", extras
        if error_code == 0 and not error_message and snapshot["why_held"]:
            extras.update({
                "inactive_resolved_as": "broker_pending",
                "why_held": snapshot["why_held"],
            })
            return "broker_pending", extras
        extras["inactive_resolved_as"] = "unknown"
        return "unknown", extras

    @staticmethod
    def _snapshot_broker_order_id(snapshot: dict) -> str:
        return str(snapshot["perm_id"] or snapshot["order_id"])

    def _build_raw_from_snapshot(self, action: str, snapshot: dict) -> dict:
        mapped, _ = self._map_snapshot_status(snapshot)
        return {
            "broker": "ibkr",
            "account_id": self._account_id,
            "client_id": self._client_id,
            "action": action,
            "outside_rth": False,
            "order_id": snapshot["order_id"],
            "perm_id": snapshot["perm_id"],
            "broker_order_id": self._snapshot_broker_order_id(snapshot),
            "ib_status": snapshot["ib_status"],
            "order_ref": snapshot["order_ref"],
            "mapped_status": mapped,
            "con_id": snapshot["con_id"],
            "contract_symbol": snapshot["contract_symbol"],
            "contract_exchange": snapshot["contract_exchange"],
            "contract_currency": snapshot["contract_currency"],
        }

    def _status_update_from_snapshot(
        self,
        snapshot: dict,
        *,
        action: str,
        broker_order_id: str | None = None,
        local_order_id: str = "",
    ) -> OrderStatusUpdate:
        mapped, extras = self._map_snapshot_status(snapshot)
        raw = self._build_raw_from_snapshot(action, snapshot)
        raw.update(extras)
        return OrderStatusUpdate(
            broker_order_id=broker_order_id or self._snapshot_broker_order_id(snapshot),
            local_order_id=local_order_id,
            status=mapped,
            filled_quantity=snapshot["filled_quantity"],
            avg_filled_price=(
                Decimal(str(snapshot["avg_filled_price"]))
                if snapshot["avg_filled_price"]
                else None
            ),
            timestamp=int(time.time() * 1000),
            raw_response=raw,
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
