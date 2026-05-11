"""
WealthPilot v3.2 MockBrokerAdapter — 模拟券商适配器。

用于 v3.2 阶段的端到端流程验证，覆盖真实券商的所有异常场景：
- 正常提交 → 异步成交（含部分成交）
- 拒单（合规/资金校验失败模拟）
- 网络异常 → unknown 状态
- 订单取消
- 订单过期（expire_after_seconds > 0 时）

状态存内存 dict（重启不保留），threading.Timer 模拟异步成交。
全局单例，FastAPI startup 创建，lifespan shutdown 调用 shutdown()。
"""
from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from decimal import Decimal
from typing import Optional

from backend.services.action.brokers.base import (
    BrokerAdapter,
    OrderRequest,
    OrderStatusUpdate,
)

logger = logging.getLogger(__name__)


class MockBrokerAdapter(BrokerAdapter):
    """
    模拟券商适配器。

    模拟参数（可在构造时或测试中调整）：
    - fill_delay_seconds: 成交延迟（默认 5 秒，测试时设 0.01）
    - partial_fill_probability: 部分成交概率（默认 0.1）
    - rejection_probability: 拒单概率（默认 0.02）
    - network_failure_probability: 网络异常概率（默认 0.01）
    - expire_after_seconds: 订单超时秒数（默认 0 = 不超时）
    """

    def __init__(
        self,
        fill_delay_seconds: float = 5.0,
        partial_fill_probability: float = 0.1,
        rejection_probability: float = 0.02,
        network_failure_probability: float = 0.01,
        expire_after_seconds: float = 0,
    ):
        self.fill_delay_seconds = fill_delay_seconds
        self.partial_fill_probability = partial_fill_probability
        self.rejection_probability = rejection_probability
        self.network_failure_probability = network_failure_probability
        self.expire_after_seconds = expire_after_seconds

        # 内存订单存储：broker_order_id → OrderStatusUpdate
        self._orders: dict[str, OrderStatusUpdate] = {}
        self._lock = threading.Lock()
        self._timers: list[threading.Timer] = []

    # ─── ABC 实现 ─────────────────────────────────────────────

    @property
    def broker_name(self) -> str:
        return "mock"

    def authenticate(self, credentials: dict) -> bool:
        return True

    def place_order(self, request: OrderRequest) -> OrderStatusUpdate:
        # 模拟网络异常
        if random.random() < self.network_failure_probability:
            raise ConnectionError("Mock: 模拟网络异常，无法连接券商")

        broker_order_id = f"MOCK-{uuid.uuid4().hex[:8]}"
        ts = int(time.time() * 1000)

        # 模拟拒单
        if random.random() < self.rejection_probability:
            update = OrderStatusUpdate(
                broker_order_id=broker_order_id,
                local_order_id=request.local_order_id,
                status="rejected",
                filled_quantity=0,
                timestamp=ts,
                raw_response={"reason": "Mock: 合规校验失败（模拟拒单）"},
            )
            with self._lock:
                self._orders[broker_order_id] = update
            return update

        # 正常提交
        update = OrderStatusUpdate(
            broker_order_id=broker_order_id,
            local_order_id=request.local_order_id,
            status="submitted_to_broker",
            filled_quantity=0,
            avg_filled_price=request.limit_price,
            timestamp=ts,
            raw_response={
                "symbol": request.symbol,
                "side": request.side,
                "quantity": request.quantity,
                "order_type": request.order_type,
                "limit_price": str(request.limit_price) if request.limit_price else None,
            },
        )
        with self._lock:
            self._orders[broker_order_id] = update

        # 同步推进到 broker_pending（真实券商也是提交后立即 ack）
        with self._lock:
            update.status = "broker_pending"
            update.timestamp = int(time.time() * 1000)

        # 启动异步成交（从 broker_pending 开始）
        self._schedule_fill_from_pending(broker_order_id, request)

        return update

    def cancel_order(self, broker_order_id: str) -> bool:
        with self._lock:
            order = self._orders.get(broker_order_id)
            if order is None:
                return False
            terminal = {"filled", "cancelled", "rejected", "expired"}
            if order.status in terminal:
                return False
            order.status = "cancelled"
            order.timestamp = int(time.time() * 1000)
            return True

    def get_order_status(self, broker_order_id: str) -> OrderStatusUpdate:
        # 模拟网络异常
        if random.random() < self.network_failure_probability:
            raise ConnectionError("Mock: 模拟网络异常，无法查询订单状态")

        with self._lock:
            order = self._orders.get(broker_order_id)
            if order is None:
                raise ValueError(f"Mock: 订单不存在: {broker_order_id}")
            # 返回快照副本
            return OrderStatusUpdate(
                broker_order_id=order.broker_order_id,
                local_order_id=order.local_order_id,
                status=order.status,
                filled_quantity=order.filled_quantity,
                avg_filled_price=order.avg_filled_price,
                timestamp=order.timestamp,
                raw_response=dict(order.raw_response),
            )

    def list_open_orders(self) -> list[OrderStatusUpdate]:
        terminal = {"filled", "cancelled", "rejected", "expired"}
        with self._lock:
            return [
                OrderStatusUpdate(
                    broker_order_id=o.broker_order_id,
                    local_order_id=o.local_order_id,
                    status=o.status,
                    filled_quantity=o.filled_quantity,
                    avg_filled_price=o.avg_filled_price,
                    timestamp=o.timestamp,
                    raw_response=dict(o.raw_response),
                )
                for o in self._orders.values()
                if o.status not in terminal
            ]

    def get_positions(self) -> list[dict]:
        return []  # Mock 不维护持仓

    def get_account_info(self) -> dict:
        return {
            "broker": "mock",
            "cash_available": 1_000_000.0,
            "currency": "USD",
        }

    def shutdown(self) -> None:
        """取消所有待执行的 Timer。"""
        for t in self._timers:
            t.cancel()
        self._timers.clear()
        logger.info("[MockBrokerAdapter] shutdown: 已取消所有 Timer")

    # ─── 异步状态变迁（threading.Timer）────────────────────────

    def _schedule_fill_from_pending(self, broker_order_id: str, request: OrderRequest) -> None:
        """从 broker_pending 开始模拟异步成交。"""
        quantity = request.quantity

        def _do_fill():
            with self._lock:
                order = self._orders.get(broker_order_id)
                if order is None or order.status != "broker_pending":
                    return

                ts = int(time.time() * 1000)

                if random.random() < self.partial_fill_probability:
                    # 部分成交（30%-70%）
                    ratio = random.uniform(0.3, 0.7)
                    partial_qty = max(1, int(quantity * ratio))
                    order.status = "partially_filled"
                    order.filled_quantity = partial_qty
                    order.timestamp = ts
                else:
                    # 全部成交
                    order.status = "filled"
                    order.filled_quantity = quantity
                    order.timestamp = ts

            # 部分成交后，再过一段时间完成或取消剩余
            with self._lock:
                order = self._orders.get(broker_order_id)
                if order and order.status == "partially_filled":
                    t3 = threading.Timer(
                        self.fill_delay_seconds, _do_complete_partial,
                    )
                    t3.daemon = True
                    self._timers.append(t3)
                    t3.start()

        def _do_complete_partial():
            with self._lock:
                order = self._orders.get(broker_order_id)
                if order is None or order.status != "partially_filled":
                    return
                ts = int(time.time() * 1000)
                if random.random() < 0.8:
                    # 80% 概率最终全部成交
                    order.status = "filled"
                    order.filled_quantity = quantity
                    order.timestamp = ts
                else:
                    # 20% 概率剩余被取消
                    order.status = "cancelled"
                    order.timestamp = ts

        def _do_expire():
            with self._lock:
                order = self._orders.get(broker_order_id)
                if order is None:
                    return
                if order.status in ("broker_pending",):
                    order.status = "expired"
                    order.timestamp = int(time.time() * 1000)

        # 延迟后执行成交
        t_fill = threading.Timer(self.fill_delay_seconds, _do_fill)
        t_fill.daemon = True
        self._timers.append(t_fill)
        t_fill.start()

        # 可选：超时检测
        if self.expire_after_seconds > 0:
            t_expire = threading.Timer(self.expire_after_seconds, _do_expire)
            t_expire.daemon = True
            self._timers.append(t_expire)
            t_expire.start()


# ═══════════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════════

_mock_adapter: Optional[MockBrokerAdapter] = None


def get_mock_adapter() -> MockBrokerAdapter:
    """获取全局 MockBrokerAdapter 单例。FastAPI startup 时调用。"""
    global _mock_adapter
    if _mock_adapter is None:
        _mock_adapter = MockBrokerAdapter()
    return _mock_adapter


def shutdown_mock_adapter() -> None:
    """FastAPI lifespan shutdown 时调用。"""
    global _mock_adapter
    if _mock_adapter is not None:
        _mock_adapter.shutdown()
        _mock_adapter = None
