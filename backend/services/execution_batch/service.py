"""Authoritative v3.15 Case 1 ExecutionBatch orchestration.

All executable numbers originate from broker facts and deterministic rules.
The service is intentionally limited to IBKR/LSEETF/USD/ETF/BUY/LIMIT.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from sqlalchemy.orm import Session

from app.models import ConversationMessage
from backend.services.action.models import (
    AuditLog,
    ExecutionBatch,
    ExecutionLeg,
    OrderRecord,
    SymbolStrategy,
)
from backend.services.action.order_manager import OrderManager
from backend.services.trade_intent.models import StructuredTradeIntent
from backend.services.trade_intent.persistence import decode_message_metadata

from .calculator import (
    CashLedger,
    ExecutionSafetyError,
    calculate_fixed_quantity,
    money,
    normalize_buy_limit,
    quote_guard,
    select_authoritative_cash,
)
from .trusted_instruments import (
    EXPECTED_MARKET_RULE_IDS,
    verify_resolved_instrument,
)


BATCH_STATUSES = {
    "DRAFT", "READY", "CONFIRMED", "SUBMITTING", "PARTIALLY_SUBMITTED",
    "SUBMITTED", "ATTENTION_REQUIRED", "COMPLETED", "CANCELLED",
}
LEG_STATUSES = {
    "DRAFT", "READY", "SUBMITTING", "SUBMITTED", "OPEN",
    "PARTIAL_FILLED", "FILLED", "REJECTED", "UNKNOWN",
    "NOT_SUBMITTED", "CANCELLED",
}
ADVANCEABLE_LEG_STATUSES = {"SUBMITTED", "OPEN", "PARTIAL_FILLED", "FILLED"}

CASE1_INTENT_VARIANTS = {
    "ORIGINAL_4_LEG": [
        ("IBTA", "APPROX_AMOUNT", Decimal("11350")),
        ("VDCA", "APPROX_AMOUNT", Decimal("2850")),
        ("CBU0", "APPROX_AMOUNT", Decimal("1400")),
        ("IB01", "REMAINDER", None),
    ],
    "CBU3_IB01_2_LEG": [
        ("CBU3", "APPROX_AMOUNT", Decimal("15600")),
        ("IB01", "REMAINDER", None),
    ],
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _commission_in_funding_currency(result: dict, funding_currency: str) -> Decimal:
    """Return a WhatIf fee only when its broker-reported currency is usable.

    ExecutionBatch has no FX-normalization contract.  A missing or different
    commission currency therefore cannot be added to the USD cash ledger.
    """
    currency = str(result.get("commission_currency") or "").upper()
    expected = str(funding_currency or "").upper()
    if not currency:
        raise ExecutionSafetyError("WhatIf commission currency 缺失，禁止计入资金账本")
    if currency != expected:
        raise ExecutionSafetyError(
            f"WhatIf commission currency={currency}，无法按 {expected} 汇总（未配置 FX normalization）"
        )
    return money(result.get("commission"))


class ExecutionBatchService:
    def __init__(
        self,
        session: Session,
        adapter,
        *,
        clock: Callable[[], datetime] = _utcnow,
        allow_mutation: bool | None = None,
    ):
        self.session = session
        self.adapter = adapter
        self.clock = clock
        self._allow_mutation_override = allow_mutation

    def _audit(self, event_type: str, payload: dict) -> None:
        self.session.add(AuditLog(event_type=event_type, payload=_json(payload)))

    def _calculate_remainder_with_what_if(
        self,
        leg: ExecutionLeg,
        *,
        budget_before_own_fee: Decimal,
        funding_currency: str,
        max_iterations: int = 4,
    ) -> tuple[int, Decimal, dict, Decimal]:
        """Converge whole-share remainder quantity with its own WhatIf fee.

        The fee is broker-authoritative and may depend on quantity.  Each pass
        reserves the latest fee before recalculating quantity; a bounded loop
        fails closed instead of returning an underfunded plan.
        """
        limit = money(leg.final_limit)
        resolved = json.loads(leg.resolution_snapshot)
        fee = Decimal("0")
        for _ in range(max_iterations):
            spendable = max(Decimal("0"), budget_before_own_fee - fee)
            quantity, notional = calculate_fixed_quantity(spendable, limit)
            result = self.adapter.what_if_limit_order(
                resolved, quantity=quantity, limit_price=limit,
            )
            next_fee = _commission_in_funding_currency(result, funding_currency)
            next_spendable = max(
                Decimal("0"), budget_before_own_fee - next_fee,
            )
            next_quantity, _ = calculate_fixed_quantity(next_spendable, limit)
            if next_quantity == quantity:
                return quantity, notional, result, next_fee
            fee = next_fee
        raise ExecutionSafetyError("REMAINDER WhatIf fee/quantity 未在有限迭代内收敛")

    @staticmethod
    def _canonical_leg_values(intent: StructuredTradeIntent) -> list[tuple[str, str, Decimal | None]]:
        result = []
        for leg in sorted(intent.legs, key=lambda item: item.sequence):
            alias = str(leg.alias.value or "").upper()
            mode = str(leg.allocation_mode.value or "").upper()
            target = money(leg.target_amount.value) if leg.target_amount.value is not None else None
            result.append((alias, mode, target))
        return result

    @staticmethod
    def _validate_case1_intent(intent: StructuredTradeIntent) -> str:
        if intent.confirmation_status.value != "CONFIRMED":
            raise ExecutionSafetyError("Trade Intent 尚未人工确认")
        required = {
            "broker": "IBKR", "funding_source": "CASH",
            "funding_currency": "USD", "budget_mode": "ALL_AVAILABLE_CASH",
            "venue": "LSE", "trading_currency": "USD",
            "share_class": "ACC", "side": "BUY", "order_type": "LIMIT",
        }
        for name, expected in required.items():
            actual = str(getattr(intent, name).value or "").upper()
            if actual != expected:
                raise ExecutionSafetyError(f"Case 1 {name}={actual}, expected={expected}")
        legs = ExecutionBatchService._canonical_leg_values(intent)
        for variant, expected in CASE1_INTENT_VARIANTS.items():
            if legs == expected:
                return variant
        raise ExecutionSafetyError(f"仅支持冻结的 Case 1 intent variants，收到 {legs}")

    def load_confirmed_intent(
        self, *, conversation_id: str, message_id: int,
    ) -> StructuredTradeIntent:
        message = self.session.query(ConversationMessage).filter_by(
            id=message_id, conversation_id=conversation_id, role="assistant",
        ).first()
        if message is None:
            raise LookupError("Trade Intent message 不存在")
        metadata = decode_message_metadata(message.metadata_json)
        raw = metadata.get("trade_intent")
        if not isinstance(raw, dict):
            raise LookupError("message 不含 Trade Intent")
        intent = StructuredTradeIntent.model_validate(raw)
        self._validate_case1_intent(intent)
        return intent

    def create_batch(self, *, conversation_id: str, message_id: int) -> ExecutionBatch:
        """Resolve and calculate a real or fake plan; never submits an order."""
        intent = self.load_confirmed_intent(
            conversation_id=conversation_id, message_id=message_id,
        )
        intent_variant = self._validate_case1_intent(intent)
        existing = self.session.query(ExecutionBatch).filter_by(
            source_message_id=message_id,
        ).order_by(ExecutionBatch.created_at.desc()).first()
        if existing and existing.status not in {"CANCELLED", "COMPLETED"}:
            return existing

        if not self.adapter.authenticate({}):
            raise ConnectionError("IBKR authenticate 失败")
        cash_snapshot = self.adapter.get_cash_snapshot("USD")
        authoritative_cash, cash_source = select_authoritative_cash(cash_snapshot)
        open_order_details = self.adapter.list_open_order_details()
        competing = [
            item for item in open_order_details
            if str(item.get("side", "")).upper() == "BUY"
            and int(item.get("remaining_quantity") or 0) > 0
        ]
        if competing:
            raise ExecutionSafetyError("检测到外部 BUY open order，Case 1 资金池被锁定")

        cushion = money(os.getenv("BATCH_CASH_SAFETY_CUSHION_USD", "25"))
        batch = ExecutionBatch(
            broker="ibkr",
            account_ref=getattr(self.adapter, "_account_id", "fake-account"),
            funding_currency="USD",
            budget_mode="ALL_AVAILABLE_CASH",
            source_conversation_id=conversation_id,
            source_message_id=message_id,
            source_trade_intent=_json(intent.model_dump(mode="json")),
            stated_cash=money(intent.stated_cash.value),
            authoritative_cash_snapshot=_json({
                **cash_snapshot,
                "authority": cash_source,
                "authoritative_usd_cash": str(authoritative_cash),
            }),
            usable_cash=authoritative_cash,
            safety_cushion=cushion,
            execution_policy=_json({
                "broker": "IBKR", "venue": "LSEETF", "currency": "USD",
                "side": "BUY", "order_type": "LIMIT", "quantity": "WHOLE",
                "intent_variant": intent_variant,
                "sequence": [item[0] for item in self._canonical_leg_values(intent)],
                "external_open_order_count": len(open_order_details),
                "external_open_buy_order_count": len(competing),
                "remainder": "DYNAMIC_LAST",
                "unknown_timeout": "HARD_STOP",
            }),
            status="DRAFT",
        )
        self.session.add(batch)
        self.session.flush()

        total_notional = Decimal("0")
        total_fees = Decimal("0")
        attention = []
        fixed_legs: list[ExecutionLeg] = []
        remainder_leg = None

        for sequence, (alias, mode, target) in enumerate(
            self._canonical_leg_values(intent), start=1,
        ):
            resolved = self.adapter.resolve_lse_usd_etf(alias)
            trusted = verify_resolved_instrument(alias, resolved)
            expected_rule = EXPECTED_MARKET_RULE_IDS[alias]
            if resolved.get("market_rule_id") != expected_rule:
                raise ExecutionSafetyError(
                    f"{alias}: marketRuleId={resolved.get('market_rule_id')}, "
                    f"expected={expected_rule}"
                )
            quote = self.adapter.get_executable_quote(resolved)
            leg = ExecutionLeg(
                batch_id=batch.id, sequence=sequence, user_alias=alias,
                allocation_mode=mode,
                target_amount=target,
                authorization_class=(
                    "NORMAL_EXECUTION_VARIANCE" if mode == "REMAINDER"
                    else "FIXED_TARGET"
                ),
                resolved_con_id=resolved["con_id"], symbol=resolved["symbol"],
                local_symbol=resolved["local_symbol"], sec_type=resolved["sec_type"],
                stock_type=resolved["stock_type"], exchange=resolved["exchange"],
                primary_exchange=resolved.get("primary_exchange"),
                currency=resolved["currency"], trading_class=resolved.get("trading_class"),
                isin=resolved["isin"], long_name=resolved.get("long_name"),
                resolution_snapshot=_json(resolved),
                share_class_requirement="ACC",
                share_class_verification=trusted.verification_status,
                verification_source=trusted.verification_source,
                verified_at=datetime.fromisoformat(trusted.verified_at),
                quote_bid=quote.get("bid"), quote_ask=quote.get("ask"),
                quote_last=quote.get("last"),
                quote_as_of=(
                    datetime.fromisoformat(quote["quote_timestamp"].replace("Z", "+00:00"))
                    if quote.get("quote_timestamp") else None
                ),
                quote_quality=quote.get("quote_quality"),
                market_data_type=str(quote.get("market_data_type", "")),
                market_rule_id=resolved.get("market_rule_id"),
                min_tick=resolved.get("min_tick"),
                market_rule=_json(resolved.get("market_rule", [])),
                trading_hours=_json({
                    "trading_hours": resolved.get("trading_hours"),
                    "liquid_hours": resolved.get("liquid_hours"),
                    "time_zone_id": resolved.get("time_zone_id"),
                }),
                status="DRAFT",
                market_open=self.adapter.is_market_open(resolved, now=self.clock()),
            )
            self.session.add(leg)
            self.session.flush()
            try:
                if str(quote.get("market_data_type")) != "1":
                    raise ExecutionSafetyError(
                        f"marketDataType={quote.get('market_data_type')}，要求 LIVE(1)"
                    )
                if str(quote.get("quote_quality") or "").upper() != "LIVE":
                    raise ExecutionSafetyError(
                        f"quote quality={quote.get('quote_quality')}，要求 LIVE"
                    )
                if not leg.market_open:
                    raise ExecutionSafetyError("MARKET_CLOSED")
                ask, _ = quote_guard(quote, now=self.clock())
                limit = normalize_buy_limit(ask, resolved.get("market_rule", []))
                leg.reference_price = ask
                leg.suggested_limit = limit
                leg.final_limit = limit
                leg.limit_source = "SUGGESTED_BEST_ASK"
                if mode == "APPROX_AMOUNT":
                    quantity, notional = calculate_fixed_quantity(target, limit)
                    leg.estimated_quantity = leg.final_quantity = quantity
                    leg.estimated_notional = notional
                    fixed_legs.append(leg)
                else:
                    remainder_leg = leg
            except ExecutionSafetyError as exc:
                attention.append(f"{alias}: {exc}")

        # WhatIf fixed legs first; read-only Gateway may intentionally block it.
        for leg in fixed_legs:
            total_notional += money(leg.estimated_notional)
            try:
                result = self.adapter.what_if_limit_order(
                    json.loads(leg.resolution_snapshot),
                    quantity=leg.estimated_quantity,
                    limit_price=money(leg.suggested_limit),
                )
                leg.what_if_snapshot = _json(result)
                total_fees += _commission_in_funding_currency(
                    result, batch.funding_currency,
                )
            except (ConnectionError, TimeoutError) as exc:
                leg.what_if_snapshot = _json({
                    "status": "PENDING_LIVE_ENABLE", "reason": str(exc),
                    "what_if": True, "transmit": False,
                })
                attention.append(f"{leg.user_alias}: WhatIf 待解除 Gateway Read-Only 后重跑")

        if remainder_leg and remainder_leg.suggested_limit:
            remainder_budget_before_own_fee = max(
                Decimal("0"), authoritative_cash - total_notional - total_fees - cushion,
            )
            try:
                quantity, notional = calculate_fixed_quantity(
                    remainder_budget_before_own_fee,
                    money(remainder_leg.suggested_limit),
                )
                remainder_leg.estimated_quantity = remainder_leg.final_quantity = quantity
                remainder_leg.estimated_notional = notional
                try:
                    quantity, notional, result, fee = (
                        self._calculate_remainder_with_what_if(
                            remainder_leg,
                            budget_before_own_fee=remainder_budget_before_own_fee,
                            funding_currency=batch.funding_currency,
                        )
                    )
                    remainder_leg.estimated_quantity = quantity
                    remainder_leg.final_quantity = quantity
                    remainder_leg.estimated_notional = notional
                    remainder_leg.what_if_snapshot = _json(result)
                    total_fees += fee
                except (ConnectionError, TimeoutError) as exc:
                    remainder_leg.what_if_snapshot = _json({
                        "status": "PENDING_LIVE_ENABLE", "reason": str(exc),
                        "what_if": True, "transmit": False,
                    })
                    attention.append(
                        f"{remainder_leg.user_alias}: WhatIf 待解除 Gateway Read-Only 后重跑"
                    )
                total_notional += money(remainder_leg.estimated_notional)
            except ExecutionSafetyError as exc:
                attention.append(f"{remainder_leg.user_alias}: {exc}")

        batch.estimated_total = total_notional
        batch.estimated_fees = total_fees
        batch.estimated_residual = max(
            Decimal("0"), authoritative_cash - total_notional - total_fees - cushion,
        )
        if attention:
            batch.status = "DRAFT"
            batch.attention_reason = _json(attention)
        else:
            for leg in batch.legs:
                leg.status = "READY"
            batch.status = "READY"
        self._audit("execution_batch_created", {
            "batch_id": batch.id, "source_message_id": message_id,
            "status": batch.status, "broker_mutation": 0,
        })
        self.session.flush()
        return batch

    @staticmethod
    def _confirmation_payload(batch: ExecutionBatch) -> dict:
        return {
            "batch_id": batch.id,
            "account_ref": batch.account_ref,
            "currency": batch.funding_currency,
            "budget_mode": batch.budget_mode,
            "safety_cushion": str(batch.safety_cushion),
            "execution_policy": json.loads(batch.execution_policy),
            "cash_accounting_model_version": batch.cash_accounting_model_version,
            "legs": [
                {
                    "id": leg.id, "alias": leg.user_alias,
                    "con_id": leg.resolved_con_id, "isin": leg.isin,
                    "exchange": leg.exchange, "currency": leg.currency,
                    "allocation_mode": leg.allocation_mode,
                    "target_amount": str(leg.target_amount) if leg.target_amount else None,
                    "share_class_verification": leg.share_class_verification,
                }
                for leg in batch.legs
            ],
        }

    def confirm_batch(self, batch_id: str) -> ExecutionBatch:
        batch = self.get_batch(batch_id)
        if batch.status != "READY":
            raise ExecutionSafetyError(f"Batch status={batch.status}，不可确认")
        batch.confirmation_version += 1
        batch.confirmation_hash = hashlib.sha256(
            _json(self._confirmation_payload(batch)).encode("utf-8")
        ).hexdigest()
        batch.status = "CONFIRMED"
        batch.confirmed_at = self.clock()
        self._audit("execution_batch_confirmed", {
            "batch_id": batch.id,
            "confirmation_version": batch.confirmation_version,
            "confirmation_hash": batch.confirmation_hash,
        })
        self.session.flush()
        return batch

    def get_batch(self, batch_id: str) -> ExecutionBatch:
        batch = self.session.query(ExecutionBatch).filter_by(id=batch_id).first()
        if batch is None:
            raise LookupError("ExecutionBatch 不存在")
        return batch

    def list_batches(self) -> list[ExecutionBatch]:
        return self.session.query(ExecutionBatch).order_by(
            ExecutionBatch.created_at.desc()
        ).all()

    def refresh_batch(self, batch_id: str) -> ExecutionBatch:
        """Rebuild all broker facts into a new versioned review object."""
        batch = self.get_batch(batch_id)
        if any(leg.linked_order_id for leg in batch.legs):
            raise ExecutionSafetyError("已有 Broker order 的 Batch 不可整体刷新")
        batch.status = "CANCELLED"
        self._audit("execution_batch_superseded", {
            "batch_id": batch.id, "broker_mutation": 0,
        })
        self.session.flush()
        return self.create_batch(
            conversation_id=batch.source_conversation_id,
            message_id=batch.source_message_id,
        )

    def apply_manual_limits(
        self, batch_id: str, limits: dict[str, object],
    ) -> ExecutionBatch:
        """Apply user-entered limits with exact MarketRule validation and WhatIf."""
        batch = self.get_batch(batch_id)
        if any(leg.linked_order_id for leg in batch.legs):
            raise ExecutionSafetyError("已有 Broker order，不可修改 Limit")
        aliases = {leg.user_alias for leg in batch.legs}
        if set(limits) != aliases:
            raise ExecutionSafetyError(
                f"必须为 {'/'.join(leg.user_alias for leg in batch.legs)} 分别输入 Limit"
            )

        total_notional = Decimal("0")
        total_fees = Decimal("0")
        attention = []
        fixed = []
        remainder = None
        for leg in batch.legs:
            limit = money(limits[leg.user_alias])
            tiers = json.loads(leg.market_rule or "[]")
            if limit <= 0 or normalize_buy_limit(limit, tiers) != limit:
                raise ExecutionSafetyError(
                    f"{leg.user_alias}: 手工 Limit 不符合 MarketRule tick"
                )
            leg.final_limit = limit
            leg.limit_source = "USER_MANUAL_CONFIRMED"
            leg.manual_limit_confirmed_at = self.clock()
            resolved = json.loads(leg.resolution_snapshot)
            leg.market_open = self.adapter.is_market_open(resolved, now=self.clock())
            if leg.allocation_mode == "APPROX_AMOUNT":
                quantity, notional = calculate_fixed_quantity(
                    money(leg.target_amount), limit,
                )
                leg.final_quantity = leg.estimated_quantity = quantity
                leg.estimated_notional = notional
                fixed.append(leg)
            else:
                remainder = leg

        for leg in fixed:
            total_notional += money(leg.estimated_notional)
            try:
                result = self.adapter.what_if_limit_order(
                    json.loads(leg.resolution_snapshot),
                    quantity=leg.final_quantity,
                    limit_price=money(leg.final_limit),
                )
                leg.what_if_snapshot = _json(result)
                total_fees += _commission_in_funding_currency(
                    result, batch.funding_currency,
                )
            except (ConnectionError, TimeoutError) as exc:
                leg.what_if_snapshot = _json({
                    "status": "PENDING_LIVE_ENABLE", "reason": str(exc),
                    "what_if": True, "transmit": False,
                })
                attention.append(f"{leg.user_alias}: WhatIf 待 Live Enable")

        snapshot = json.loads(batch.authoritative_cash_snapshot)
        authoritative_cash = money(snapshot["authoritative_usd_cash"])
        if remainder:
            remainder_budget_before_own_fee = max(
                Decimal("0"),
                authoritative_cash - total_notional - total_fees - money(batch.safety_cushion),
            )
            quantity, notional = calculate_fixed_quantity(
                remainder_budget_before_own_fee, money(remainder.final_limit),
            )
            remainder.final_quantity = remainder.estimated_quantity = quantity
            remainder.estimated_notional = notional
            try:
                quantity, notional, result, fee = (
                    self._calculate_remainder_with_what_if(
                        remainder,
                        budget_before_own_fee=remainder_budget_before_own_fee,
                        funding_currency=batch.funding_currency,
                    )
                )
                remainder.final_quantity = remainder.estimated_quantity = quantity
                remainder.estimated_notional = notional
                remainder.what_if_snapshot = _json(result)
                total_fees += fee
            except (ConnectionError, TimeoutError) as exc:
                remainder.what_if_snapshot = _json({
                    "status": "PENDING_LIVE_ENABLE", "reason": str(exc),
                    "what_if": True, "transmit": False,
                })
                attention.append(f"{remainder.user_alias}: WhatIf 待 Live Enable")
            total_notional += money(remainder.estimated_notional)

        batch.estimated_total = total_notional
        batch.estimated_fees = total_fees
        batch.estimated_residual = max(
            Decimal("0"),
            authoritative_cash - total_notional - total_fees - money(batch.safety_cushion),
        )
        batch.confirmation_hash = None
        batch.status = "DRAFT" if attention else "READY"
        batch.attention_reason = _json(attention) if attention else None
        for leg in batch.legs:
            leg.status = "DRAFT" if attention else "READY"
        self._audit("execution_batch_manual_limits_applied", {
            "batch_id": batch.id, "status": batch.status,
            "broker_mutation": 0,
        })
        self.session.flush()
        return batch

    def _assert_live_mutation_enabled(self) -> None:
        allowed = self._allow_mutation_override
        if allowed is None:
            allowed = (
                os.getenv("ENABLE_IBKR_LIVE_TRADING", "false").lower() == "true"
                and os.getenv("IBKR_READ_ONLY_MODE", "true").lower() == "false"
            )
        if not allowed:
            raise PermissionError(
                "真实提交未启用：要求 ENABLE_IBKR_LIVE_TRADING=true 且 "
                "IBKR_READ_ONLY_MODE=false"
            )

    def _next_leg(self, batch: ExecutionBatch) -> ExecutionLeg | None:
        for index, leg in enumerate(batch.legs):
            if leg.status in {"READY", "DRAFT", "NOT_SUBMITTED"}:
                previous = batch.legs[:index]
                if any(item.status not in ADVANCEABLE_LEG_STATUSES for item in previous):
                    raise ExecutionSafetyError("前序 Leg 尚未达到可继续状态")
                return leg
            if leg.status in {"UNKNOWN", "REJECTED"}:
                raise ExecutionSafetyError(f"{leg.user_alias}={leg.status}，Batch hard stop")
        return None

    def submit_next_leg(
        self, batch_id: str, *, confirmation_version: int, leg_id: str | None = None,
    ) -> OrderRecord:
        """Official mutation path. Must only be called by the authenticated UI flow."""
        self._assert_live_mutation_enabled()
        batch = self.get_batch(batch_id)
        if batch.confirmation_version != confirmation_version or not batch.confirmation_hash:
            raise ExecutionSafetyError("confirmation version/hash 无效")
        if batch.status not in {"CONFIRMED", "SUBMITTING", "PARTIALLY_SUBMITTED"}:
            raise ExecutionSafetyError(f"Batch status={batch.status}，不可提交")
        requested_leg = (
            next((item for item in batch.legs if item.id == leg_id), None)
            if leg_id else None
        )
        if leg_id and requested_leg is None:
            raise LookupError("ExecutionLeg 不存在")
        if requested_leg and requested_leg.linked_order_id:
            return self.session.query(OrderRecord).filter_by(
                id=requested_leg.linked_order_id
            ).one()
        leg = self._next_leg(batch)
        if leg is None:
            raise ExecutionSafetyError("没有待提交 Leg")
        if requested_leg and requested_leg.id != leg.id:
            raise ExecutionSafetyError("只能按已确认顺序提交下一 Leg")

        # Every leg revalidates identity, live cash and external-order conflict.
        resolved = self.adapter.resolve_lse_usd_etf(leg.user_alias)
        verify_resolved_instrument(leg.user_alias, resolved)
        if resolved["con_id"] != leg.resolved_con_id:
            raise ExecutionSafetyError("conId changed; confirmation invalid")
        if not self.adapter.is_market_open(resolved, now=self.clock()):
            raise ExecutionSafetyError("MARKET_CLOSED：仅允许当前可交易时段即时提交")
        local_order_refs = {
            item.linked_order_id for item in batch.legs if item.linked_order_id
        }
        external = [
            item for item in self.adapter.list_open_order_details()
            if str(item.get("side", "")).upper() == "BUY"
            and str(item.get("order_ref", "")) not in local_order_refs
        ]
        if external:
            raise ExecutionSafetyError("存在竞争性外部 BUY open order")

        fresh_cash, _ = select_authoritative_cash(self.adapter.get_cash_snapshot("USD"))
        ledger = self._build_ledger(batch)
        ledger.consistency_guard(fresh_cash)
        if leg.allocation_mode == "REMAINDER":
            if money(leg.released_intent_amount) > 0:
                raise ExecutionSafetyError("intent-level released budget requires reconfirmation")
            quote = self.adapter.get_executable_quote(resolved)
            ask, _ = quote_guard(quote, now=self.clock())
            limit = normalize_buy_limit(ask, resolved["market_rule"])
            quantity, notional = calculate_fixed_quantity(ledger.remaining, limit)
            leg.reference_price, leg.final_limit = ask, limit
            leg.final_quantity, leg.estimated_notional = quantity, notional
        quantity = int(leg.final_quantity or 0)
        limit = money(leg.final_limit)
        if quantity <= 0 or limit <= 0:
            raise ExecutionSafetyError("Leg 无有效 quantity/limit")
        if leg.target_amount and money(quantity) * limit > money(leg.target_amount):
            raise ExecutionSafetyError("Fixed Target ceiling violated")
        what_if = self.adapter.what_if_limit_order(
            resolved, quantity=quantity, limit_price=limit,
        )
        _commission_in_funding_currency(what_if, batch.funding_currency)
        leg.what_if_snapshot = _json(what_if)

        strategy = SymbolStrategy(
            symbol=f"{leg.user_alias}:LSE", side="BUY", target_quantity=quantity,
            order_type="LIMIT", limit_price=limit, status="active",
            related_conversation_id=batch.source_conversation_id,
            decision_basis="v3.15 Case 1 confirmed ExecutionBatch",
            batch_leg_id=leg.id,
        )
        self.session.add(strategy)
        self.session.flush()
        leg.linked_strategy_id = strategy.id
        leg.status = "SUBMITTING"
        batch.status = "SUBMITTING"
        self.session.flush()

        manager = OrderManager(self.session, broker_adapter=self.adapter)
        order = manager.place_order(strategy.id, {
            "quantity": quantity,
            "limit_price": limit,
            "order_type": "LIMIT",
            "resolved_contract": resolved,
            "batch_id": batch.id,
            "batch_leg_id": leg.id,
            "confirmation_version": confirmation_version,
        })
        leg.linked_order_id = order.id
        leg.submission_attempted_at = self.clock()
        leg.status = {
            "submitted_to_broker": "SUBMITTING",
            "broker_pending": "OPEN",
            "partially_filled": "PARTIAL_FILLED",
            "filled": "FILLED",
            "rejected": "REJECTED",
            "unknown": "UNKNOWN",
            "cancelled": "CANCELLED",
        }.get(order.status, "UNKNOWN")
        if leg.status in {"UNKNOWN", "REJECTED", "CANCELLED"}:
            batch.status = "ATTENTION_REQUIRED"
            batch.attention_reason = f"{leg.user_alias}={leg.status}"
        elif leg.status == "SUBMITTING":
            batch.status = "SUBMITTING"
        elif all(item.status in ADVANCEABLE_LEG_STATUSES for item in batch.legs):
            batch.status = "SUBMITTED"
        else:
            batch.status = "PARTIALLY_SUBMITTED"
        self._audit("execution_leg_submitted", {
            "batch_id": batch.id, "leg_id": leg.id, "order_id": order.id,
            "status": leg.status,
        })
        self.session.flush()
        return order

    def _build_ledger(self, batch: ExecutionBatch) -> CashLedger:
        filled = Decimal("0")
        reserved = Decimal("0")
        fees = Decimal("0")
        intent_release = Decimal("0")
        for leg in batch.legs:
            intent_release += money(leg.released_intent_amount)
            if leg.what_if_snapshot:
                fees += _commission_in_funding_currency(
                    json.loads(leg.what_if_snapshot), batch.funding_currency,
                )
            if not leg.linked_order_id:
                continue
            order = self.session.query(OrderRecord).filter_by(id=leg.linked_order_id).first()
            if not order:
                continue
            filled_qty = int(order.filled_quantity or 0)
            filled += money(filled_qty) * money(order.avg_filled_price or order.limit_price)
            if order.status in {"submitted_to_broker", "broker_pending", "partially_filled"}:
                reserved += money(max(0, order.quantity - filled_qty)) * money(order.limit_price)
        snapshot = json.loads(batch.authoritative_cash_snapshot)
        initial = money(snapshot["authoritative_usd_cash"])
        return CashLedger(
            initial_cash=initial, filled_cost=filled,
            active_reservations=reserved, fee_reserve=fees,
            safety_cushion=money(batch.safety_cushion),
            intent_release_blocked=intent_release,
        )

    def reconcile_leg(self, batch_id: str, leg_id: str) -> ExecutionLeg:
        batch = self.get_batch(batch_id)
        leg = next((item for item in batch.legs if item.id == leg_id), None)
        if not leg or not leg.linked_order_id:
            raise LookupError("Leg/Order 不存在")
        order = self.session.query(OrderRecord).filter_by(id=leg.linked_order_id).one()
        update = self.adapter.find_order_by_ref(order.id)
        if update is None:
            raise ExecutionSafetyError("orderRef NOT_FOUND；仅允许用户显式 Retry")
        order.broker_order_id = update.broker_order_id
        order.status = update.status
        order.filled_quantity = update.filled_quantity
        order.avg_filled_price = update.avg_filled_price
        order.raw_broker_response = _json(update.raw_response)
        leg.status = {
            "submitted_to_broker": "SUBMITTING", "broker_pending": "OPEN",
            "partially_filled": "PARTIAL_FILLED", "filled": "FILLED",
            "rejected": "REJECTED", "unknown": "UNKNOWN",
            "cancelled": "CANCELLED",
        }.get(update.status, "UNKNOWN")
        batch.status = (
            "ATTENTION_REQUIRED"
            if leg.status in {"UNKNOWN", "REJECTED", "CANCELLED"}
            else "PARTIALLY_SUBMITTED"
        )
        self._audit("execution_leg_reconciled", {
            "batch_id": batch.id, "leg_id": leg.id, "status": leg.status,
        })
        self.session.flush()
        return leg

    def skip_rejected_leg(self, batch_id: str, leg_id: str) -> ExecutionBatch:
        batch = self.get_batch(batch_id)
        leg = next((item for item in batch.legs if item.id == leg_id), None)
        if not leg or leg.status != "REJECTED":
            raise ExecutionSafetyError("只有 REJECTED Leg 可以 Skip")
        leg.status = "NOT_SUBMITTED"
        leg.released_intent_amount = money(leg.target_amount)
        batch.status = "DRAFT"
        batch.confirmation_hash = None
        batch.attention_reason = "intent-level allocation release requires new confirmation"
        self._audit("execution_leg_skipped_reconfirmation_required", {
            "batch_id": batch.id, "leg_id": leg.id,
            "released_intent_amount": str(leg.released_intent_amount),
        })
        self.session.flush()
        return batch

    def stop_remaining(self, batch_id: str) -> ExecutionBatch:
        batch = self.get_batch(batch_id)
        for leg in batch.legs:
            if not leg.linked_order_id and leg.status not in {"FILLED", "CANCELLED"}:
                leg.status = "CANCELLED"
        batch.status = "CANCELLED"
        self._audit("execution_batch_remaining_stopped", {
            "batch_id": batch.id, "broker_cancel_mutations": 0,
        })
        self.session.flush()
        return batch

    def retire_replaced_intent(self, batch_id: str) -> ExecutionBatch:
        """Retire an unsubmitted batch after an intent-level allocation change."""
        batch = self.get_batch(batch_id)
        if any(leg.linked_order_id for leg in batch.legs):
            raise ExecutionSafetyError("已有 Broker order，不能按未提交意图替换处理")
        for leg in batch.legs:
            if leg.status != "FILLED":
                leg.status = "CANCELLED"
        batch.status = "CANCELLED"
        batch.attention_reason = _json(["INTENT_REPLACED"])
        self._audit("execution_batch_retired_intent_replaced", {
            "batch_id": batch.id,
            "reason": "INTENT_REPLACED",
            "broker_cancel_mutations": 0,
            "broker_order_mutations": 0,
        })
        self.session.flush()
        return batch
