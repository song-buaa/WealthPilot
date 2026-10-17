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
import hashlib
import time
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from backend.services.action.models import (
    ActionDraft,
    AllocationIntent,
    SymbolStrategy,
    OrderRecord,
    AuditLog,
    ExecutionBatch,
    ExecutionLeg,
)
from backend.services.action.state_machine import (
    ActionDraftStatus,
    StrategyStatus,
    OrderStatus,
    validate_draft_transition,
    validate_strategy_transition,
    project_broker_status,
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

    def _sync_execution_batch_linkage(self, order: OrderRecord) -> None:
        """Propagate broker-authoritative OrderRecord state to its Case 1 leg.

        The order poller owns broker reconciliation.  A linked ExecutionLeg
        must never remain visually SUBMITTED after the OrderRecord has moved
        to a terminal or unknown state.
        """
        if not order.batch_id or not order.batch_leg_id:
            return
        leg = self.session.query(ExecutionLeg).filter_by(
            id=order.batch_leg_id, batch_id=order.batch_id,
        ).first()
        batch = self.session.query(ExecutionBatch).filter_by(
            id=order.batch_id,
        ).first()
        if leg is None or batch is None or leg.linked_order_id != order.id:
            return

        leg_status = {
            OrderStatus.CREATED: "SUBMITTING",
            OrderStatus.SUBMITTED_TO_BROKER: "SUBMITTING",
            OrderStatus.BROKER_PENDING: "OPEN",
            OrderStatus.PARTIALLY_FILLED: "PARTIAL_FILLED",
            OrderStatus.FILLED: "FILLED",
            OrderStatus.CANCELLED: "CANCELLED",
            OrderStatus.REJECTED: "REJECTED",
            OrderStatus.EXPIRED: "CANCELLED",
            OrderStatus.UNKNOWN: "UNKNOWN",
        }.get(order.status, "UNKNOWN")
        old_leg_status = leg.status
        old_batch_status = batch.status
        leg.status = leg_status

        statuses = {item.status for item in batch.legs}
        stopped = bool(batch.attention_reason and "REMAINING_STOPPED" in batch.attention_reason)
        attention = {item.status for item in batch.legs if item.linked_order_id} & {"UNKNOWN", "REJECTED", "CANCELLED"}
        if stopped and not attention:
            batch.status = ("SUBMITTED" if any(item.status in {"OPEN", "SUBMITTING", "PARTIAL_FILLED"}
                            for item in batch.legs if item.linked_order_id) else "CANCELLED")
        elif attention:
            batch.status = "ATTENTION_REQUIRED"
            batch.attention_reason = json.dumps([
                f"BROKER_{status}: {item.user_alias}"
                for item in batch.legs
                if (status := item.status) in attention
            ] + (["REMAINING_STOPPED"] if stopped else []), ensure_ascii=False)
        elif statuses and statuses == {"FILLED"}:
            batch.status = "COMPLETED"
        elif "SUBMITTING" in statuses:
            batch.status = "SUBMITTING"
        elif statuses and statuses <= {
            "OPEN", "PARTIAL_FILLED", "FILLED",
        }:
            batch.status = "SUBMITTED"
        elif any(item.linked_order_id for item in batch.legs):
            batch.status = "PARTIALLY_SUBMITTED"

        if old_leg_status != leg.status or old_batch_status != batch.status:
            self._audit("execution_batch_broker_state_synced", {
                "batch_id": batch.id,
                "leg_id": leg.id,
                "order_id": order.id,
                "order_status": order.status,
                "leg_status": leg.status,
                "batch_status": batch.status,
            })

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
        for entity in created_entities:
            if isinstance(entity, SymbolStrategy):
                self._audit("b0_strategy_created", {"strategy_id": entity.id})
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
        """撤销订单 — 先通知券商，再按真实结果更新本地状态。

        分流逻辑（撤单受理 ≠ 已撤销）：
        - 无 ID/历史 owner 不明 → 只读恢复，不推定未发送
        - 已终态 → 拒绝，抛 ValueError
        - 有 broker_order_id 且非终态 → 调 adapter.cancel_order()：
          a) True（受理）→ 立即 sync 确认真实状态
          b) False（券商称已终态）→ sync 拉真实状态回填
          c) 网络异常 → 置 UNKNOWN，交 poller 后续收敛
        """
        order = self.get_order(order_id)
        if order is None:
            raise ValueError(f"订单不存在: {order_id}")

        # 已终态 → 拒绝
        if order.status in OrderStatus.TERMINAL:
            raise ValueError(f"订单已终态（{order.status}），不可取消")

        if not order.broker_order_id or not self.broker_adapter:
            if order.status == OrderStatus.CREATED and not order.submission_key and order.broker_name == "mock":
                order.status = OrderStatus.CANCELLED
                order.cancelled_at = datetime.now(timezone.utc)
                self._audit("order_cancelled", {"order_id": order.id, "cancel_type": "local_unclaimed"})
                self._sync_execution_batch_linkage(order)
                self.session.flush()
                return order
            # A committed claim may have reached the broker. Recovery is read-only.
            result = self.sync_order_status(order.id)
            self._audit("cancel_requires_reconciliation", {"order_id": order.id})
            return result

        self._assert_broker_io_unlocked()
        if self.broker_adapter.broker_name != order.broker_name:
            raise ValueError("BROKER_OWNER_MISMATCH")
        if order.broker_name not in {"mock", "stub"}:
            if not order.portfolio_id or not order.broker_account_id:
                result = self.sync_order_status(order.id)
                self._audit("legacy_cancel_requires_owner_review", {"order_id": order.id})
                return result
            from backend.services.action.ownership import execution_owner
            owner = execution_owner(self.session, self.broker_adapter)
            if owner.portfolio_id != order.portfolio_id or owner.account_id != order.broker_account_id:
                raise ValueError("BROKER_OWNER_MISMATCH")
        try:
            cancel_by_ref = getattr(self.broker_adapter, "cancel_order_by_ref", None)
            accepted = (cancel_by_ref(order.id, broker_order_id=order.broker_order_id)
                        if callable(cancel_by_ref) else self.broker_adapter.cancel_order(order.broker_order_id))
        except (ConnectionError, TimeoutError) as e:
            # 分支 c: 网络异常，无法确认撤单结果
            order = self.apply_broker_observation(order.id, OrderStatusUpdate(
                order.broker_order_id, order.id, OrderStatus.UNKNOWN,
                raw_response={"status_only": True}))
            self._audit("cancel_network_error", {
                "order_id": order_id,
                "error": str(e),
                "note": "撤单结果未知，交 poller 后续确认",
            })
            # 在 raw_broker_response 标记 cancel_requested，供 poller/审计追溯
            self._mark_cancel_requested(order)
            logger.warning(
                "[OrderManager] cancel_order 网络异常: %s, 已置 UNKNOWN", e,
            )
            self.session.flush()
            return order

        # Read the broker status before queuing any audit writes; a cancel
        # acknowledgement is not a terminal observation.
        order = self.sync_order_status(order_id)
        self._audit("cancel_broker_responded", {"order_id": order_id, "broker_accepted": accepted})

        # sync 后标记 cancel_requested（sync 会覆盖 raw_broker_response，所以在之后标记）
        self._mark_cancel_requested(order)
        self.session.flush()

        # 如果 sync 后仍非终态（撤单受理但尚未生效），交 poller 继续轮询
        if order.status not in OrderStatus.TERMINAL:
            logger.info(
                "[OrderManager] cancel 已受理但券商未确认终态 "
                "(status=%s)，交 poller 继续轮询", order.status,
            )

        return order

    @staticmethod
    def _mark_cancel_requested(order: OrderRecord) -> None:
        """在 raw_broker_response JSON 中标记 cancel_requested=true。

        不新增 DB 字段，复用现有 Text 列存储撤单追溯信息。
        """
        existing = {}
        if order.raw_broker_response:
            try:
                existing = json.loads(order.raw_broker_response)
            except (json.JSONDecodeError, TypeError):
                existing = {"_prev_raw": order.raw_broker_response}
        existing["cancel_requested"] = True
        existing["cancel_requested_at"] = datetime.now(timezone.utc).isoformat()
        order.raw_broker_response = json.dumps(
            existing, ensure_ascii=False, default=str,
        )

    @staticmethod
    def _canonical(value):
        def normalize(item):
            if isinstance(item, Decimal):
                return format(item.normalize(), "f")
            if isinstance(item, dict):
                return {k: normalize(v) for k, v in item.items()}
            if isinstance(item, list):
                return [normalize(v) for v in item]
            return item
        return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), default=str)

    def _portfolio_epoch(self, portfolio_id):
        from app.models import Position
        rows = self.session.query(Position).filter_by(portfolio_id=portfolio_id).order_by(Position.id).all()
        facts = [[p.id, p.symbol, p.currency, p.quantity, p.market_value_cny] for p in rows]
        return hashlib.sha256(self._canonical(facts).encode()).hexdigest()

    def _assert_broker_io_unlocked(self):
        if self.session.connection().connection.driver_connection.in_transaction:
            raise ValueError("BROKER_IO_WITH_SQLITE_WRITE_TRANSACTION_FORBIDDEN")

    def _order_epoch(self, owner, *, excluding_batch=None):
        q = self.session.query(OrderRecord).filter_by(broker_name=owner.broker)
        records = q.filter((OrderRecord.broker_account_id == owner.account_id) |
                           OrderRecord.broker_account_id.is_(None)).all()
        return hashlib.sha256(self._canonical(sorted(o.id for o in records
            if not excluding_batch or o.batch_id != excluding_batch)).encode()).hexdigest()

    def _parameters(self, strategy, params):
        if strategy.symbol and "\u4e00" <= strategy.symbol[0] <= "\u9fff":
            raise InvalidSymbolError("symbol 包含中文")
        quantity = Decimal(str(params.get("quantity", strategy.target_quantity or 0)))
        if not quantity.is_finite() or quantity <= 0 or quantity != quantity.to_integral_value():
            raise ValueError("positive whole quantity required")
        tif = params.get("time_in_force", "GTC")
        if tif not in {"GTC", "DAY"}:
            raise ValueError("UNSUPPORTED_TIF")
        return {"symbol": strategy.symbol, "side": strategy.side,
                "quantity": int(quantity), "order_type": params.get("order_type", strategy.order_type),
                "limit_price": Decimal(str(params.get("limit_price", strategy.limit_price or 0))),
                "stop_price": (Decimal(str(params.get("stop_price", strategy.trigger_price)))
                               if params.get("stop_price", strategy.trigger_price) is not None else None),
                "time_in_force": tif, "resolved_contract": params.get("resolved_contract")}

    def create_order_review(self, strategy_id, params, *, risk_snapshot, ttl=30):
        """Persist original Web risk-review nonce, not a permission token.

        Caller explicitly commits this review before returning it to the Web.
        Preview/MCP link generation never calls this method.
        """
        from backend.services.action.ownership import execution_owner
        strategy = self.get_strategy(strategy_id)
        if strategy is None:
            if self.session.get(AllocationIntent, strategy_id):
                raise ValueError("AllocationIntent cannot place orders directly")
            raise ValueError("策略不存在")
        if strategy.batch_leg_id:
            raise ValueError("ExecutionBatch 关联策略只能由批次状态机提交")
        owner = execution_owner(self.session, self.broker_adapter)
        if owner.broker not in {"mock", "stub"}:
            eligible = any(json.loads(log.payload or "{}").get("strategy_id") == strategy_id
                           for log in self.session.query(AuditLog).filter_by(event_type="b0_strategy_created"))
            if not eligible:
                raise ValueError("LEGACY_STRATEGY_RECONCILIATION_REQUIRED")
        facts = {"strategy_id": strategy_id, "portfolio_id": owner.portfolio_id,
                 "broker": owner.broker, "account_id": owner.account_id,
                 "parameters": self._parameters(strategy, params),
                 "epoch": self._order_epoch(owner), "portfolio_epoch": self._portfolio_epoch(owner.portfolio_id), "risk": risk_snapshot,
                 "expires_at": time.time() + ttl}
        audit = AuditLog(event_type="order_reviewed", payload=self._canonical(facts))
        self.session.add(audit)
        self.session.flush()
        return audit.id

    def _submission_key(self, owner, strategy_id, params):
        batch_id, leg_id = params.get("batch_id"), params.get("batch_leg_id")
        if batch_id:
            if not leg_id:
                raise ValueError("BATCH_LEG_REQUIRED")
            facts = ["b0.batch-leg.v1", owner.portfolio_id, owner.broker, owner.account_id, batch_id, leg_id]
        else:
            strategy = self.get_strategy(strategy_id)
            if strategy is None:
                raise ValueError("策略不存在")
            source = self.session.get(ActionDraft, strategy.source_draft_id) if strategy.source_draft_id else None
            if source is not None and source.user_id == "chatgpt-web-link":
                facts = ["b0.web-link.v1", owner.portfolio_id, owner.broker, owner.account_id, source.id, strategy_id]
            else:
                facts = ["b0.strategy-review.v1", owner.portfolio_id, owner.broker, owner.account_id,
                         strategy_id, params.get("review_id")]
        return hashlib.sha256(self._canonical(facts).encode()).hexdigest()

    def existing_submission(self, strategy_id, params, *, owner=None):
        """Read an identical durable claim, never 'any prior strategy order'."""
        from backend.services.action.ownership import execution_owner
        owner = owner or execution_owner(self.session, self.broker_adapter)
        key = self._submission_key(owner, strategy_id, params)
        order = self.session.query(OrderRecord).filter_by(submission_key=key).first()
        if order is None:
            return None
        old = json.loads(order.submission_payload_json)
        if (order.portfolio_id != owner.portfolio_id or order.broker_name != owner.broker
                or order.broker_account_id != owner.account_id
                or old["portfolio_id"] != owner.portfolio_id or old["broker"] != owner.broker
                or old["account_id"] != owner.account_id
                or order.batch_id != params.get("batch_id") or order.batch_leg_id != params.get("batch_leg_id")
                or (not order.batch_id and order.strategy_id != strategy_id)):
            raise ValueError("IDEMPOTENCY_OWNER_CONFLICT")
        strategy = self.get_strategy(order.strategy_id)
        if json.loads(self._canonical(self._parameters(strategy, params))) != old["parameters"]:
            raise ValueError("IDEMPOTENCY_PARAMETER_CONFLICT")
        return order

    def place_order(self, strategy_id: str, order_params: dict) -> OrderRecord:
        """Claim -> commit -> one broker call -> independent observation commit.

        A claim is UNKNOWN, not accepted. A crashed winner is never dispatched
        by another invocation. SQLite writer locks exist only in short Tx A/B.
        The caller must commit its own preparation explicitly before entering.
        """
        from backend.services.action.ownership import execution_owner
        from backend.services.action.risk_engine import CONFIRMATION_TEXT
        owner = execution_owner(self.session, self.broker_adapter)
        if self.session.new or self.session.dirty or self.session.deleted:
            raise ValueError("COMMIT_PREPARATION_BEFORE_DISPATCH")
        connection = self.session.connection().connection.driver_connection
        if connection.in_transaction:
            raise ValueError("WRITE_TRANSACTION_BEFORE_DISPATCH")
        factory = sessionmaker(bind=self.session.get_bind(), expire_on_commit=False)
        self.session.rollback()  # release caller read snapshot; never commit arbitrary caller work
        batch_id = order_params.get("batch_id")
        leg_id = order_params.get("batch_leg_id")
        claimed = False
        with factory() as tx:
            tx.execute(text("BEGIN IMMEDIATE"))
            tx.info.update(self.session.info)
            manager = OrderManager(tx, self.broker_adapter)
            if execution_owner(tx, self.broker_adapter) != owner:
                raise ValueError("EXECUTION_OWNER_CHANGED")
            key = manager._submission_key(owner, strategy_id, order_params)
            existing = manager.existing_submission(strategy_id, order_params, owner=owner)
            if existing is not None:
                order_id = existing.id
            else:
                unknown = tx.query(OrderRecord).filter(
                    OrderRecord.broker_name == owner.broker,
                    ((OrderRecord.broker_account_id == owner.account_id) | OrderRecord.broker_account_id.is_(None)),
                    OrderRecord.status.in_([OrderStatus.UNKNOWN, OrderStatus.SUBMITTED_TO_BROKER]),
                ).first()
                if unknown is not None:
                    raise ValueError("ACCOUNT_RECONCILIATION_REQUIRED")
                batch = tx.get(ExecutionBatch, batch_id) if batch_id else None
                leg = tx.get(ExecutionLeg, leg_id) if batch else None
                if batch:
                    policy = json.loads(batch.execution_policy)
                    from backend.services.execution_batch.service import ExecutionBatchService
                    if ExecutionBatchService.review_fingerprint(batch, authorization=True) != batch.confirmation_hash:
                        raise ValueError("BATCH_AUTHORIZATION_CHANGED")
                    if (policy.get("portfolio_id") != owner.portfolio_id or batch.account_ref != owner.account_id
                            or batch.confirmation_version != order_params.get("confirmation_version")
                            or batch.confirmation_hash != order_params.get("confirmation_hash")
                            or batch.status not in {"CONFIRMED", "SUBMITTING", "PARTIALLY_SUBMITTED"}
                            or leg is None or leg.batch_id != batch.id or leg.linked_order_id):
                        raise ValueError("BATCH_AUTHORIZATION_CHANGED")
                    if policy.get("portfolio_epoch") != manager._portfolio_epoch(owner.portfolio_id):
                        raise ValueError("PORTFOLIO_RISK_EVIDENCE_CHANGED")
                    if policy.get("order_epoch") != manager._order_epoch(owner, excluding_batch=batch.id):
                        raise ValueError("EXECUTION_EPOCH_CHANGED")
                    if any(item.status not in {"OPEN", "PARTIAL_FILLED", "FILLED"}
                           for item in batch.legs if item.sequence < leg.sequence):
                        raise ValueError("PREVIOUS_LEG_NOT_ADVANCEABLE")
                    strategy = SymbolStrategy(id=strategy_id, symbol=f"{leg.user_alias}:LSE", side="BUY",
                        target_quantity=leg.final_quantity, limit_price=leg.final_limit, order_type="LIMIT",
                        status=StrategyStatus.ACTIVE, batch_leg_id=leg.id,
                        related_conversation_id=batch.source_conversation_id)
                    tx.add(strategy)
                    tx.flush()
                    params = manager._parameters(strategy, order_params)
                    if (params["quantity"] != leg.final_quantity or params["limit_price"] != leg.final_limit
                            or params["time_in_force"] != policy.get("time_in_force", "DAY")):
                        raise ValueError("BATCH_PARAMETERS_CHANGED")
                    auth = {"confirmation_hash": batch.confirmation_hash, "version": batch.confirmation_version,
                            "risk": policy.get("risk_warnings"), "epoch": policy.get("order_epoch")}
                else:
                    strategy = manager.get_strategy(strategy_id)
                    if strategy.batch_leg_id:
                        raise ValueError("ExecutionBatch 关联策略只能由批次状态机提交")
                    params = manager._parameters(strategy, order_params)
                    review = tx.get(AuditLog, order_params.get("review_id"))
                    if review is None or review.event_type != "order_reviewed":
                        raise ValueError("ORDER_REVIEW_REQUIRED")
                    auth = json.loads(review.payload)
                    if (auth["strategy_id"] != strategy_id or auth["portfolio_id"] != owner.portfolio_id
                            or auth["account_id"] != owner.account_id or auth["broker"] != owner.broker
                            or auth["epoch"] != manager._order_epoch(owner)
                            or auth.get("portfolio_epoch") != manager._portfolio_epoch(owner.portfolio_id)
                            or auth["parameters"] != json.loads(manager._canonical(params))):
                        raise ValueError("ORDER_REVIEW_CHANGED")
                    if auth["expires_at"] < time.time():
                        raise ValueError("ORDER_REVIEW_EXPIRED")
                    if auth["risk"].get("requires_confirmation") and order_params.get("confirmation_text") != CONFIRMATION_TEXT:
                        raise ValueError("RISK_CONFIRMATION_REQUIRED")
                if strategy.status != StrategyStatus.ACTIVE:
                    raise ValueError(f"策略状态为 {strategy.status}，不可下单")
                if strategy.symbol and "\u4e00" <= strategy.symbol[0] <= "\u9fff":
                    raise InvalidSymbolError("symbol 包含中文")
                outstanding = sum(max(0, o.quantity - int(o.filled_quantity or 0)) for o in manager.list_orders(strategy_id=strategy.id)
                                  if o.status not in OrderStatus.TERMINAL)
                if strategy.target_quantity is not None and params["quantity"] + outstanding + int(strategy.cumulative_filled_quantity or 0) > strategy.target_quantity:
                    raise ValueError("超额下单禁止")
                deadline = min(float(order_params.get("dispatch_deadline", time.time() + 30)),
                               float(auth.get("expires_at", time.time() + 30)))
                if deadline <= time.time():
                    raise ValueError("ORDER_REVIEW_EXPIRED")
                payload = manager._canonical({"portfolio_id": owner.portfolio_id, "account_id": owner.account_id,
                    "broker": owner.broker, "parameters": params, "authorization": auth, "dispatch_deadline": deadline})
                order = OrderRecord(strategy_id=strategy.id, broker_name=owner.broker,
                    portfolio_id=owner.portfolio_id, broker_account_id=owner.account_id,
                    submission_key=key, submission_payload_json=payload,
                    symbol=params["symbol"], side=params["side"], quantity=params["quantity"],
                    limit_price=params["limit_price"], stop_price=params["stop_price"], order_type=params["order_type"],
                    status=OrderStatus.UNKNOWN, batch_id=batch_id, batch_leg_id=leg_id,
                    confirmation_version=order_params.get("confirmation_version"))
                tx.add(order)
                tx.flush()
                if leg:
                    leg.linked_order_id, leg.linked_strategy_id = order.id, strategy.id
                    leg.submission_attempted_at = datetime.now(timezone.utc)
                manager._sync_execution_batch_linkage(order)
                manager._audit("order_created", {"order_id": order.id})
                manager._audit("dispatch_claimed", {"order_id": order.id, "portfolio_id": owner.portfolio_id})
                order_id = order.id
                try:
                    tx.commit()  # A commit failure never grants dispatch permission.
                except BaseException:
                    # Some DBAPI commit errors leave transaction state ambiguous.
                    # Discard the physical connection; never let its writer lock
                    # leak through the pool or authorize a send.
                    tx.invalidate()
                    raise
                claimed = True
        if claimed:
            facts = json.loads(payload)
            params = facts["parameters"]
            request = OrderRequest(**{k: v for k, v in params.items() if k not in {"stop_price", "limit_price"}},
                limit_price=Decimal(params["limit_price"]),
                trigger_price=Decimal(params["stop_price"]) if params["stop_price"] is not None else None,
                local_order_id=order_id, broker_account_id=owner.account_id, dispatch_deadline=deadline)
            try:
                if time.time() >= deadline:
                    raise TimeoutError("DISPATCH_DEADLINE_EXPIRED")
                update = self.broker_adapter.place_order(request)  # exactly one invocation; no retry
            except Exception as exc:
                # Broker may have accepted even when SDK reports an unexpected error.
                update = OrderStatusUpdate(None, order_id, OrderStatus.UNKNOWN,
                                           raw_response={"dispatch_error_type": type(exc).__name__})
            with factory() as tx:
                tx.execute(text("BEGIN IMMEDIATE"))
                manager = OrderManager(tx, self.broker_adapter)
                result = manager.apply_broker_observation(order_id, update)
                manager._audit("order_submitted" if result.broker_order_id else "order_submit_unknown", {"order_id": order_id})
                try:
                    tx.commit()
                except BaseException:
                    tx.invalidate()
                    raise
        self.session.expire_all()
        return self.get_order(order_id)

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

        if not self.broker_adapter:
            return order
        self._assert_broker_io_unlocked()
        configured = getattr(self.broker_adapter, "_account_id", None)
        if order.broker_account_id and configured and configured != order.broker_account_id:
            raise ValueError("BROKER_ACCOUNT_MISMATCH")
        try:
            recover = getattr(self.broker_adapter, "find_order_by_ref", None)
            fresh_recover = getattr(self.broker_adapter, "recover_order", None)
            if fresh_recover is not None:
                raw = json.loads(order.raw_broker_response or "{}")
                update = fresh_recover(order.id, perm_id=raw.get("perm_id"))
                if update is None:
                    update = OrderStatusUpdate(None, order.id, OrderStatus.UNKNOWN,
                                               filled_quantity=order.filled_quantity or 0,
                                               raw_response={"recovery": "NOT_FOUND_OR_UNAVAILABLE", "status_only": True})
            elif recover is not None:
                update = recover(order.id)
                if update is None:
                    update = OrderStatusUpdate(None, order.id, OrderStatus.UNKNOWN,
                                               filled_quantity=order.filled_quantity or 0,
                                               raw_response={"recovery": "NOT_FOUND_OR_UNAVAILABLE", "status_only": True})
            elif order.broker_order_id:
                update = self.broker_adapter.get_order_status(order.broker_order_id)
            else:
                update = OrderStatusUpdate(None, order.id, OrderStatus.UNKNOWN,
                                           raw_response={"status_only": True})
        except (ConnectionError, TimeoutError) as e:
            order = self.apply_broker_observation(order.id, OrderStatusUpdate(
                order.broker_order_id, order.id, OrderStatus.UNKNOWN,
                raw_response={"status_only": True}))
            self._audit("sync_network_error", {"order_id": order_id, "error": str(e)})
            logger.warning(f"[OrderManager] sync 网络异常: {e}")
            self.session.flush()
            return order

        return self.apply_broker_observation(order.id, update)

    def apply_broker_observation(self, order_id: str, update: OrderStatusUpdate) -> OrderRecord:
        """The sole projection writer for initial ack, poll, recovery and cancellation.

        Cumulative facts never increment twice. Absence, pending and stale lower
        quantities cannot erase known executions. A correction is quarantined,
        never replayed as a new financial event in B0.
        """
        # The caller commits immediately after this database-only operation.
        # Never flush stale caller changes, or trust an identity-map object read
        # before broker I/O. A no-row UPDATE also upgrades an existing deferred
        # transaction (or fails closed on SQLITE_BUSY_SNAPSHOT).
        if self.session.new or self.session.dirty or self.session.deleted:
            raise ValueError("COMMIT_PREPARATION_BEFORE_OBSERVATION")
        if not self.session.connection().connection.driver_connection.in_transaction:
            self.session.rollback()
            self.session.execute(text("BEGIN IMMEDIATE"))
        else:
            self.session.execute(text("UPDATE order_records SET id=id WHERE 0"))
        self.session.expire_all()
        order = self.get_order(order_id)
        if order is None:
            raise ValueError("订单不存在")
        if update.local_order_id and update.local_order_id != order.id:
            raise ValueError("BROKER_ORDER_REF_MISMATCH")
        actual_account = update.raw_response.get("observed_account_id")
        if order.broker_account_id and actual_account and actual_account != order.broker_account_id:
            raise ValueError("BROKER_ACCOUNT_MISMATCH")
        if order.submission_payload_json:
            claimed = json.loads(order.submission_payload_json)
            contract = claimed["parameters"].get("resolved_contract") or {}
            observed_con_id = update.raw_response.get("con_id")
            if contract.get("con_id") and observed_con_id and int(contract["con_id"]) != int(observed_con_id):
                raise ValueError("BROKER_CONTRACT_MISMATCH")
        new_status = update.status if update.status in OrderStatus.ALL else OrderStatus.UNKNOWN
        old_qty = int(order.filled_quantity or 0)
        try:
            quantity = Decimal(str(old_qty if update.raw_response.get("status_only") else update.filled_quantity))
            valid_qty = quantity.is_finite() and quantity == quantity.to_integral_value() and 0 <= quantity <= order.quantity
        except Exception:
            valid_qty = False
        price_conflict = (old_qty > 0 and valid_qty and quantity == old_qty
            and order.avg_filled_price is not None and update.avg_filled_price is not None
            and Decimal(str(update.avg_filled_price)) > 0
            and Decimal(str(update.avg_filled_price)).quantize(Decimal("0.0001"))
                != Decimal(str(order.avg_filled_price)).quantize(Decimal("0.0001")))
        prior_raw = json.loads(order.raw_broker_response or "{}")
        disputed = (bool(update.raw_response.get("execution_correction")) or not valid_qty or price_conflict
                    or bool(prior_raw.get("reconciliation_disputed")))
        now = datetime.now(timezone.utc)
        if disputed:
            order.status = OrderStatus.UNKNOWN
            strategy = self.get_strategy(order.strategy_id)
            if strategy and strategy.status != StrategyStatus.DISCARDED:
                strategy.status = StrategyStatus.PAUSED
            self._audit("broker_observation_disputed", {"order_id": order.id, "reason": "quantity/correction"})
        else:
            qty = int(quantity)
            if qty < old_qty:
                self._audit("broker_stale_observation", {"order_id": order.id, "quantity": qty})
                self.session.flush()
                return order
            order.status = project_broker_status(order.status, new_status,
                previous_filled=old_qty, cumulative_filled=qty, quantity=order.quantity)
            order.filled_quantity = qty
            if update.avg_filled_price is not None and qty > 0:
                price = Decimal(str(update.avg_filled_price))
                if price.is_finite() and price > 0:
                    order.avg_filled_price = price
            if order.status == OrderStatus.FILLED and order.filled_at is None:
                order.filled_at = now
            if order.status == OrderStatus.CANCELLED and order.cancelled_at is None:
                order.cancelled_at = now
        if update.broker_order_id:
            order.broker_order_id = update.broker_order_id
        if order.broker_order_id and order.submitted_at is None:
            order.submitted_at = now
        order.last_synced_at = now
        raw = ({**prior_raw, **update.raw_response} if update.raw_response.get("status_only")
               else {**update.raw_response})
        for name in ("perm_id", "order_ref", "con_id", "observed_account_id"):
            if not raw.get(name) and prior_raw.get(name):
                raw[name] = prior_raw[name]
        if disputed:
            raw["reconciliation_disputed"] = True
        order.raw_broker_response = json.dumps(raw, ensure_ascii=False, default=str)
        self.session.flush()
        strategy = self.get_strategy(order.strategy_id)
        if strategy:
            strategy.cumulative_filled_quantity = sum(int(o.filled_quantity or 0) for o in self.list_orders(strategy_id=strategy.id))
            if (strategy.status == StrategyStatus.ACTIVE and strategy.target_quantity is not None
                    and strategy.cumulative_filled_quantity >= strategy.target_quantity):
                strategy.status = StrategyStatus.COMPLETED
                strategy.completed_at = now
        self._sync_execution_batch_linkage(order)
        self._audit("order_synced", {"order_id": order.id, "status": order.status,
                                    "filled_quantity": order.filled_quantity})
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
