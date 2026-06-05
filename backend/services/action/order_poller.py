"""
WealthPilot v3.4 M3 — 订单状态轮询 Worker。

后台轮询 submitted_to_broker / broker_pending 状态的订单,
从 BrokerAdapter 拉取最新状态并回写 DB。

设计决策:
- 批量查询: 用 DB 查非终态订单列表,逐单调 sync_order_status(因为 Adapter
  的 list_open_orders 没有 local_order_id 对应关系,不适合批量状态回写)
- 轮询间隔: 可配置,默认 5 秒(Tiger API 限制 120 次/分钟,5 秒间隔安全)
- 异常处理: 单笔订单同步失败不影响其他订单
- 孤儿订单: 启动时一次性扫描 submitted_to_broker 但无 broker_order_id 的订单
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlalchemy.orm import Session

from backend.services.action.models import OrderRecord
from backend.services.action.state_machine import OrderStatus

logger = logging.getLogger(__name__)

# 需要轮询的非终态状态
# UNKNOWN 纳入轮询：撤单时网络异常会导致 status=unknown，poller 需要
# 后续确认券商端真实状态以收敛到终态。
POLLABLE_STATUSES = {
    OrderStatus.SUBMITTED_TO_BROKER,
    OrderStatus.BROKER_PENDING,
    OrderStatus.PARTIALLY_FILLED,
    OrderStatus.UNKNOWN,
}


class OrderPoller:
    """后台订单状态轮询 Worker。"""

    def __init__(
        self,
        get_session,
        get_broker_adapter,
        poll_interval: float = 5.0,
    ):
        """
        Args:
            get_session: 返回 SQLAlchemy Session 的可调用对象
            get_broker_adapter: 返回 BrokerAdapter 的可调用对象
            poll_interval: 轮询间隔秒数(默认 5 秒)
        """
        self._get_session = get_session
        self._get_broker_adapter = get_broker_adapter
        self._poll_interval = poll_interval
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """启动轮询 worker(作为 asyncio task)。"""
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[OrderPoller] 启动,间隔 %.1fs", self._poll_interval)

    async def stop(self):
        """停止轮询 worker。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[OrderPoller] 已停止")

    async def _poll_loop(self):
        while self._running:
            try:
                await asyncio.to_thread(self._poll_once)
            except Exception as e:
                logger.error("[OrderPoller] 轮询异常: %s", e)
            await asyncio.sleep(self._poll_interval)

    def _poll_once(self):
        """单次轮询: 查非终态订单,逐单同步状态。"""
        session = self._get_session()
        try:
            orders = (
                session.query(OrderRecord)
                .filter(OrderRecord.status.in_(POLLABLE_STATUSES))
                .filter(OrderRecord.broker_order_id.isnot(None))
                .all()
            )
            if not orders:
                return

            logger.debug("[OrderPoller] 发现 %d 笔待同步订单", len(orders))

            from backend.services.action.order_manager import OrderManager
            adapter = self._get_broker_adapter()
            manager = OrderManager(session, broker_adapter=adapter)

            for order in orders:
                try:
                    manager.sync_order_status(order.id)
                except Exception as e:
                    logger.warning(
                        "[OrderPoller] 同步订单 %s 失败: %s", order.id, e,
                    )

            session.commit()
        except Exception as e:
            session.rollback()
            logger.error("[OrderPoller] 事务异常: %s", e)
        finally:
            session.close()


def scan_orphan_orders(get_session, get_broker_adapter) -> int:
    """启动时扫描孤儿订单: submitted_to_broker 但无 broker_order_id。

    这些订单是网络中断时 place_order 请求已发出但未收到回执的情况。
    处理: 转为 unknown 状态 + 审计日志记录。

    Returns:
        发现的孤儿订单数量
    """
    session = get_session()
    try:
        orphans = (
            session.query(OrderRecord)
            .filter(
                OrderRecord.status == OrderStatus.SUBMITTED_TO_BROKER,
                OrderRecord.broker_order_id.is_(None),
            )
            .all()
        )
        if not orphans:
            return 0

        logger.warning("[scan_orphan_orders] 发现 %d 笔孤儿订单", len(orphans))

        from backend.services.action.order_manager import OrderManager
        adapter = get_broker_adapter()
        manager = OrderManager(session, broker_adapter=adapter)

        for order in orphans:
            order.status = OrderStatus.UNKNOWN
            manager._audit("orphan_order_detected", {
                "order_id": order.id,
                "strategy_id": order.strategy_id,
                "symbol": order.symbol,
            })
            logger.warning(
                "[scan_orphan_orders] 孤儿订单 %s 已标记 unknown", order.id,
            )

        session.commit()
        return len(orphans)
    except Exception as e:
        session.rollback()
        logger.error("[scan_orphan_orders] 扫描异常: %s", e)
        return 0
    finally:
        session.close()
