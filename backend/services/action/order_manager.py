"""
WealthPilot v3.2/v3.4 投资行动模块 — OrderManager。

职责：
- 草稿 CRUD + 确认/丢弃
- 策略创建/暂停/恢复/作废
- 订单创建/提交/状态同步

关键约束：
- AllocationIntent 不能调用 place_order（在 API 层校验拒绝）
- 超额下单禁止：创建 Order 前校验 cumulative_filled_quantity + 本次 quantity ≤ target_quantity
- 所有关键操作写 AuditLog

v3.4 M3 改造：
- place_order 前增加 symbol 中文名保护（InvalidSymbolError）
- audit_log 币种补全（fx_rate_to_cny / amount_cny_equivalent，护栏 §4.2）
- BrokerAdapter 通过依赖注入（工厂函数在 api/action.py 和 lifespan 中调用）
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from backend.services.action.models import (
    ActionDraft,
    AllocationIntent,
    SymbolStrategy,
    OrderRecord,
    AuditLog,
)
from backend.services.action.state_machine import (
    ActionDraftStatus,
    StrategyStatus,
    OrderStatus,
    validate_draft_transition,
    validate_strategy_transition,
    validate_order_transition,
)
from backend.services.action.brokers.base import (
    BrokerAdapter,
    OrderRequest,
    OrderStatusUpdate,
)

logger = logging.getLogger(__name__)


class OrderManager:
    """投资行动模块核心管理器。"""

    def __init__(self, session: Session, broker_adapter: Optional[BrokerAdapter] = None):
        self.session = session
        self.broker_adapter = broker_adapter

    # ─── AuditLog 辅助 ────────────────────────────────────────────

    def _audit(
        self,
        event_type: str,
        payload: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """写审计日志（append-only）。"""
        log = AuditLog(
            event_type=event_type,
            payload=json.dumps(payload, ensure_ascii=False, default=str) if payload else None,
            ip_address=ip_address,
            user_agent=user_agent,
            timestamp=datetime.now(timezone.utc),
        )
        self.session.add(log)

    # ─── ActionDraft CRUD ─────────────────────────────────────────

    def create_draft(
        self,
        conversation_id: str,
        payload: dict,
        decision_summary: str = "",
    ) -> ActionDraft:
        """创建行动清单草稿。"""
        draft = ActionDraft(
            conversation_id=conversation_id,
            decision_summary=decision_summary,
            payload=json.dumps(payload, ensure_ascii=False, default=str),
            status=ActionDraftStatus.DRAFT,
        )
        self.session.add(draft)
        self._audit("draft_created", {"draft_id": draft.id})
        self.session.flush()
        return draft

    def get_draft(self, draft_id: str) -> Optional[ActionDraft]:
        """查询草稿。"""
        return self.session.query(ActionDraft).filter_by(id=draft_id).first()

    def update_draft(self, draft_id: str, updates: dict) -> ActionDraft:
        """编辑草稿（仅 draft 状态可编辑）。"""
        draft = self.get_draft(draft_id)
        if draft is None:
            raise ValueError(f"草稿不存在: {draft_id}")
        if draft.status != ActionDraftStatus.DRAFT:
            raise ValueError(f"草稿已{draft.status}，不可编辑")

        if "decision_summary" in updates:
            draft.decision_summary = updates["decision_summary"]
        if "payload" in updates:
            draft.payload = json.dumps(updates["payload"], ensure_ascii=False, default=str)

        draft.updated_at = datetime.now(timezone.utc)
        self._audit("draft_updated", {"draft_id": draft_id})
        self.session.flush()
        return draft

    def confirm_draft(self, draft_id: str) -> list[SymbolStrategy | AllocationIntent]:
        """
        确认草稿 → 拆解为 SymbolStrategy / AllocationIntent。

        payload 结构（v3.2 ActionListDraft 对齐）：
        {
            "symbol_strategies": [...],
            "allocation_intents": [...],
            "risk_notes": [...],
            "missing_fields": [...]
        }
        """
        draft = self.get_draft(draft_id)
        if draft is None:
            raise ValueError(f"草稿不存在: {draft_id}")

        validate_draft_transition(draft.status, ActionDraftStatus.CONFIRMED)

        # 解析 payload
        payload = json.loads(draft.payload) if draft.payload else {}

        # missing_fields 非空时拒绝确认（支持结构化和字符串两种格式）
        missing = payload.get("missing_fields", [])
        if missing:
            descriptions = []
            for mf in missing:
                if isinstance(mf, dict):
                    descriptions.append(mf.get("description", str(mf)))
                else:
                    descriptions.append(str(mf))
            raise ValueError(
                f"行动清单有 {len(missing)} 项缺失信息需补充: {', '.join(descriptions)}"
            )

        draft.status = ActionDraftStatus.CONFIRMED
        draft.confirmed_at = datetime.now(timezone.utc)
        draft.updated_at = draft.confirmed_at

        # 创建子实体
        created_entities: list[SymbolStrategy | AllocationIntent] = []

        for s in payload.get("symbol_strategies", []):
            strategy = SymbolStrategy(
                source_draft_id=draft.id,
                symbol=s.get("symbol", ""),
                side=s.get("side", "BUY"),
                target_quantity=s.get("quantity") or s.get("target_quantity"),
                target_quantity_pct=s.get("quantity_pct") or s.get("target_quantity_pct"),
                order_type=s.get("order_type", "LIMIT"),
                trigger_price=s.get("trigger_price"),
                limit_price=s.get("limit_price"),
                related_conversation_id=draft.conversation_id,
                decision_basis=draft.decision_summary or "",
                status=StrategyStatus.ACTIVE,
            )
            self.session.add(strategy)
            created_entities.append(strategy)

        for a in payload.get("allocation_intents", []):
            intent = AllocationIntent(
                source_draft_id=draft.id,
                title=a.get("title", ""),
                target_allocation=json.dumps(
                    a.get("target_allocation", {}),
                    ensure_ascii=False,
                ),
                related_conversation_id=draft.conversation_id,
                decision_basis=draft.decision_summary or "",
                status=StrategyStatus.ACTIVE,
            )
            self.session.add(intent)
            created_entities.append(intent)

        self._audit("draft_confirmed", {
            "draft_id": draft_id,
            "entities_count": len(created_entities),
        })
        self.session.flush()
        return created_entities

    def discard_draft(self, draft_id: str) -> ActionDraft:
        """丢弃草稿。"""
        draft = self.get_draft(draft_id)
        if draft is None:
            raise ValueError(f"草稿不存在: {draft_id}")

        validate_draft_transition(draft.status, ActionDraftStatus.DISCARDED)

        draft.status = ActionDraftStatus.DISCARDED
        draft.discarded_at = datetime.now(timezone.utc)
        draft.updated_at = draft.discarded_at

        self._audit("draft_discarded", {"draft_id": draft_id})
        self.session.flush()
        return draft

    # ─── ActionDraft 列表 ────────────────────────────────────────

    def list_drafts(self, status: str | None = None) -> list[ActionDraft]:
        q = self.session.query(ActionDraft)
        if status:
            q = q.filter_by(status=status)
        return q.order_by(ActionDraft.created_at.desc()).all()

    # ─── AllocationIntent 操作 ────────────────────────────────────

    def get_intent(self, intent_id: str) -> Optional[AllocationIntent]:
        return self.session.query(AllocationIntent).filter_by(id=intent_id).first()

    def list_intents(self, status: str | None = None) -> list[AllocationIntent]:
        q = self.session.query(AllocationIntent)
        if status:
            q = q.filter_by(status=status)
        return q.order_by(AllocationIntent.created_at.desc()).all()

    def update_intent(self, intent_id: str, updates: dict) -> AllocationIntent:
        """编辑配置意图（仅 active/paused 状态可编辑）。"""
        intent = self.get_intent(intent_id)
        if intent is None:
            raise ValueError(f"配置意图不存在: {intent_id}")
        if intent.status not in (StrategyStatus.ACTIVE, StrategyStatus.PAUSED):
            raise ValueError(f"配置意图状态为 {intent.status}，不可编辑")
        if "title" in updates:
            intent.title = updates["title"]
        if "target_allocation" in updates:
            intent.target_allocation = json.dumps(
                updates["target_allocation"], ensure_ascii=False,
            )
        if "decision_basis" in updates:
            intent.decision_basis = updates["decision_basis"]
        intent.updated_at = datetime.now(timezone.utc)
        self._audit("intent_updated", {"intent_id": intent_id})
        self.session.flush()
        return intent

    def discard_intent(self, intent_id: str) -> AllocationIntent:
        intent = self.get_intent(intent_id)
        if intent is None:
            raise ValueError(f"配置意图不存在: {intent_id}")
        validate_strategy_transition(intent.status, StrategyStatus.DISCARDED)
        intent.status = StrategyStatus.DISCARDED
        intent.updated_at = datetime.now(timezone.utc)
        self._audit("intent_discarded", {"intent_id": intent_id})
        self.session.flush()
        return intent

    def count_strategies_for_intent(self, intent_id: str) -> int:
        return self.session.query(SymbolStrategy).filter_by(
            parent_intent_id=intent_id,
        ).count()

    # ─── SymbolStrategy 操作 ──────────────────────────────────────

    def get_strategy(self, strategy_id: str) -> Optional[SymbolStrategy]:
        return self.session.query(SymbolStrategy).filter_by(id=strategy_id).first()

    def list_strategies(
        self, status: str | None = None, parent_intent_id: str | None = None,
    ) -> list[SymbolStrategy]:
        q = self.session.query(SymbolStrategy)
        if status:
            q = q.filter_by(status=status)
        if parent_intent_id:
            q = q.filter_by(parent_intent_id=parent_intent_id)
        return q.order_by(SymbolStrategy.created_at.desc()).all()

    def pause_strategy(self, strategy_id: str) -> SymbolStrategy:
        strategy = self.get_strategy(strategy_id)
        if strategy is None:
            raise ValueError(f"策略不存在: {strategy_id}")
        validate_strategy_transition(strategy.status, StrategyStatus.PAUSED)
        strategy.status = StrategyStatus.PAUSED
        strategy.updated_at = datetime.now(timezone.utc)
        self._audit("strategy_paused", {"strategy_id": strategy_id})
        self.session.flush()
        return strategy

    def resume_strategy(self, strategy_id: str) -> SymbolStrategy:
        strategy = self.get_strategy(strategy_id)
        if strategy is None:
            raise ValueError(f"策略不存在: {strategy_id}")
        validate_strategy_transition(strategy.status, StrategyStatus.ACTIVE)
        strategy.status = StrategyStatus.ACTIVE
        strategy.updated_at = datetime.now(timezone.utc)
        self._audit("strategy_resumed", {"strategy_id": strategy_id})
        self.session.flush()
        return strategy

    def discard_strategy(self, strategy_id: str) -> SymbolStrategy:
        strategy = self.get_strategy(strategy_id)
        if strategy is None:
            raise ValueError(f"策略不存在: {strategy_id}")
        validate_strategy_transition(strategy.status, StrategyStatus.DISCARDED)
        strategy.status = StrategyStatus.DISCARDED
        strategy.updated_at = datetime.now(timezone.utc)
        self._audit("strategy_discarded", {"strategy_id": strategy_id})
        self.session.flush()
        return strategy

    # ─── OrderRecord 操作 ─────────────────────────────────────────

    def get_order(self, order_id: str) -> Optional[OrderRecord]:
        return self.session.query(OrderRecord).filter_by(id=order_id).first()

    def list_orders(
        self, strategy_id: str | None = None, status: str | None = None,
    ) -> list[OrderRecord]:
        q = self.session.query(OrderRecord)
        if strategy_id:
            q = q.filter_by(strategy_id=strategy_id)
        if status:
            q = q.filter_by(status=status)
        return q.order_by(OrderRecord.created_at.desc()).all()

    def cancel_order(self, order_id: str) -> OrderRecord:
        """本地取消订单。"""
        order = self.get_order(order_id)
        if order is None:
            raise ValueError(f"订单不存在: {order_id}")
        if order.status in OrderStatus.TERMINAL:
            raise ValueError(f"订单已终态（{order.status}），不可取消")
        validate_order_transition(order.status, OrderStatus.CANCELLED)
        order.status = OrderStatus.CANCELLED
        order.cancelled_at = datetime.now(timezone.utc)
        self._audit("order_cancelled", {"order_id": order_id})
        self.session.flush()
        return order

    def place_order(self, strategy_id: str, order_params: dict) -> OrderRecord:
        """
        创建订单并提交到券商。

        流程：
        1. 校验策略 status=active
        2. 超额校验：cumulative_filled + quantity ≤ target_quantity
        3. 创建 OrderRecord（status=created）
        4. 调用 BrokerAdapter.place_order
           - 成功 → 更新 status/broker_order_id/raw_broker_response
           - 拒单 → status=rejected
           - 网络异常 → status=unknown
        5. 写 audit_log
        """
        # 显式校验：AllocationIntent 不能直接下单
        intent = self.session.query(AllocationIntent).filter_by(id=strategy_id).first()
        if intent is not None:
            raise ValueError(
                "AllocationIntent cannot place orders directly. "
                "It must be decomposed into SymbolStrategy first."
            )

        strategy = self.get_strategy(strategy_id)
        if strategy is None:
            raise ValueError(f"策略不存在: {strategy_id}")
        if strategy.status != StrategyStatus.ACTIVE:
            raise ValueError(f"策略状态为 {strategy.status}，不可下单")

        # v3.4 M3: symbol 中文名保护
        if strategy.symbol and strategy.symbol[0] >= "\u4e00" and strategy.symbol[0] <= "\u9fff":
            raise InvalidSymbolError(
                f"symbol 包含中文,不是标的代码: {strategy.symbol}"
            )

        quantity = order_params.get("quantity", 0)
        if strategy.target_quantity is not None and quantity > 0:
            remaining = strategy.target_quantity - strategy.cumulative_filled_quantity
            if quantity > remaining:
                raise ValueError(
                    f"超额下单禁止: 目标数量={strategy.target_quantity}, "
                    f"已成交={strategy.cumulative_filled_quantity}, "
                    f"本次={quantity}, 超出={quantity - remaining}"
                )

        broker_name = "mock"
        if self.broker_adapter:
            broker_name = self.broker_adapter.broker_name

        order = OrderRecord(
            strategy_id=strategy_id,
            broker_name=broker_name,
            symbol=strategy.symbol,
            side=strategy.side,
            quantity=quantity,
            order_type=order_params.get("order_type", strategy.order_type),
            limit_price=order_params.get("limit_price", strategy.limit_price),
            stop_price=order_params.get("stop_price", strategy.trigger_price),
            status=OrderStatus.CREATED,
        )
        self.session.add(order)
        self.session.flush()

        self._audit("order_created", {
            "order_id": order.id,
            "strategy_id": strategy_id,
            "quantity": quantity,
        })

        # 调用 BrokerAdapter 提交到券商
        if self.broker_adapter:
            request = OrderRequest(
                symbol=strategy.symbol,
                side=strategy.side,
                quantity=quantity,
                order_type=order_params.get("order_type", strategy.order_type),
                limit_price=(
                    Decimal(str(order_params["limit_price"]))
                    if order_params.get("limit_price") is not None
                    else (Decimal(str(strategy.limit_price)) if strategy.limit_price else None)
                ),
                trigger_price=(
                    Decimal(str(order_params["stop_price"]))
                    if order_params.get("stop_price") is not None
                    else (Decimal(str(strategy.trigger_price)) if strategy.trigger_price else None)
                ),
                local_order_id=order.id,
            )

            try:
                update = self.broker_adapter.place_order(request)
                order.broker_order_id = update.broker_order_id
                order.raw_broker_response = json.dumps(
                    update.raw_response, ensure_ascii=False, default=str,
                )

                if update.status == "rejected":
                    order.status = OrderStatus.REJECTED
                    self._audit("order_rejected", self._enrich_audit_payload({
                        "order_id": order.id,
                        "reason": update.raw_response.get("reason", ""),
                    }, update.raw_response, order))
                else:
                    order.status = OrderStatus.SUBMITTED_TO_BROKER
                    order.submitted_at = datetime.now(timezone.utc)
                    self._audit("order_submitted", self._enrich_audit_payload({
                        "order_id": order.id,
                        "broker_order_id": update.broker_order_id,
                    }, update.raw_response, order))

            except (ConnectionError, TimeoutError) as e:
                order.status = OrderStatus.UNKNOWN
                self._audit("order_network_error", {
                    "order_id": order.id,
                    "error": str(e),
                })
                logger.warning(f"[OrderManager] place_order 网络异常: {e}")

        self.session.flush()
        return order

    def sync_order_status(self, order_id: str) -> OrderRecord:
        """
        同步订单状态。

        流程：
        1. 调用 BrokerAdapter.get_order_status
        2. 校验状态流转合法性
        3. 更新 OrderRecord
        4. 如果 filled/partially_filled → 回写 strategy.cumulative_filled_quantity
        5. 如果 cumulative >= target → strategy.status = completed
        """
        order = self.get_order(order_id)
        if order is None:
            raise ValueError(f"订单不存在: {order_id}")

        if not self.broker_adapter or not order.broker_order_id:
            logger.info(f"[OrderManager] sync_order_status: 无 adapter 或无 broker_order_id")
            return order

        try:
            update = self.broker_adapter.get_order_status(order.broker_order_id)
        except (ConnectionError, TimeoutError) as e:
            # 网络异常 → 标记 unknown（如果当前不是终态）
            if order.status not in OrderStatus.TERMINAL:
                try:
                    validate_order_transition(order.status, OrderStatus.UNKNOWN)
                    order.status = OrderStatus.UNKNOWN
                except ValueError:
                    pass  # 当前状态不允许转 unknown，保持原状
            self._audit("sync_network_error", {"order_id": order_id, "error": str(e)})
            logger.warning(f"[OrderManager] sync 网络异常: {e}")
            self.session.flush()
            return order

        new_status = update.status
        now = datetime.now(timezone.utc)

        # 校验状态流转（允许券商跳过中间状态，如 submitted → filled）
        if new_status != order.status:
            try:
                validate_order_transition(order.status, new_status)
            except ValueError:
                # 券商可能跳过中间状态（如 submitted_to_broker → filled），
                # 这在真实场景下是合法的。记录日志但接受更新。
                logger.info(
                    f"[OrderManager] 状态跳跃: {order.status} → {new_status} "
                    f"(非标准流转，已接受)"
                )

            order.status = new_status

        order.filled_quantity = update.filled_quantity
        order.avg_filled_price = update.avg_filled_price
        order.last_synced_at = now
        order.raw_broker_response = json.dumps(
            update.raw_response, ensure_ascii=False, default=str,
        )

        if new_status == "filled":
            order.filled_at = now
        elif new_status == "cancelled":
            order.cancelled_at = now

        # 回写 strategy.cumulative_filled_quantity
        if update.filled_quantity > 0:
            strategy = self.get_strategy(order.strategy_id)
            if strategy:
                # 重新计算 cumulative：查该 strategy 所有 order 的 filled_quantity 之和
                all_orders = self.list_orders(strategy_id=strategy.id)
                total_filled = sum(o.filled_quantity for o in all_orders)
                strategy.cumulative_filled_quantity = total_filled
                strategy.updated_at = now

                # 如果已达 target → completed
                if (strategy.target_quantity is not None
                        and total_filled >= strategy.target_quantity
                        and strategy.status == StrategyStatus.ACTIVE):
                    strategy.status = StrategyStatus.COMPLETED
                    strategy.completed_at = now
                    self._audit("strategy_auto_completed", {
                        "strategy_id": strategy.id,
                        "total_filled": total_filled,
                        "target": strategy.target_quantity,
                    })

        self._audit("order_synced", {
            "order_id": order_id,
            "status": new_status,
            "filled_quantity": update.filled_quantity,
        })

        self.session.flush()
        return order

    # ─── v3.4 M3: 审计 payload 币种补全 ──────────────────────────

    @staticmethod
    def _enrich_audit_payload(
        base_payload: dict,
        raw_response: dict,
        order=None,
    ) -> dict:
        """护栏 §4.2: 业务层补充 fx_rate_to_cny / amount_cny_equivalent。

        Adapter 只产出原币种事实(currency + limit_price),
        业务层根据 currency 查汇率并补充到审计 payload。
        """
        try:
            from app.fx_service import FALLBACK_RATES
        except ImportError:
            FALLBACK_RATES = {"USD": 6.90, "HKD": 0.92}

        currency = raw_response.get("currency", "USD")
        fx_rate = FALLBACK_RATES.get(currency, 1.0)

        payload = {**base_payload}
        payload["fx_rate_to_cny"] = fx_rate

        limit_price = raw_response.get("limit_price")
        quantity = raw_response.get("quantity")
        if limit_price is not None and quantity is not None:
            payload["amount_cny_equivalent"] = round(
                float(limit_price) * int(quantity) * fx_rate, 2,
            )

        return payload


class InvalidSymbolError(ValueError):
    """Symbol 格式异常（如包含中文名而非标的代码）。"""
