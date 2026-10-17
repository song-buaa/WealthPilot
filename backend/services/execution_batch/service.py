"""Authoritative v3.15 Case 1 ExecutionBatch orchestration.

All executable numbers originate from broker facts and deterministic rules.
The service is intentionally limited to IBKR/LSE USD ETF/SMART/BUY/LIMIT.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
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
    commission_reserve = result.get(
        "commission_reserve", result.get("commission"),
    )
    if commission_reserve is None:
        raise ExecutionSafetyError(
            "WhatIf commission 缺失"
            f" (min={result.get('min_commission')},"
            f" max={result.get('max_commission')}, currency={currency})"
        )
    return money(commission_reserve)


def _validate_what_if_result(result: dict, funding_currency: str) -> Decimal:
    """Fail closed unless IBKR returned an unambiguous executable preview."""
    if str(result.get("status") or "").upper() != "PASS":
        raise ExecutionSafetyError("WhatIf 未返回 PASS")
    if result.get("what_if") is not True or result.get("transmit") is not True:
        raise ExecutionSafetyError("WhatIf 协议标志无效")
    warning = str(result.get("warning_text") or "").strip()
    if warning:
        raise ExecutionSafetyError(f"WhatIf warning: {warning}")
    return _commission_in_funding_currency(result, funding_currency)


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

    def _broker(self):
        """All broker reads/WhatIf use this guard; dispatch has its own Tx A/B."""
        if self.session.connection().connection.driver_connection.in_transaction:
            raise ExecutionSafetyError("BROKER_IO_WITH_SQLITE_WRITE_TRANSACTION_FORBIDDEN")
        return self.adapter

    def _audit(self, event_type: str, payload: dict) -> None:
        self.session.add(AuditLog(event_type=event_type, payload=_json(payload)))

    def _resolve_for_smart_execution(self, alias: str) -> tuple[dict, object]:
        """Keep LSE listing identity separate from SMART execution routing."""
        listing = self._broker().resolve_lse_usd_etf(alias)
        trusted = verify_resolved_instrument(alias, listing)
        qualifier = getattr(self.adapter, "qualify_execution_route", None)
        if not callable(qualifier):
            raise ExecutionSafetyError("Adapter 缺少 SMART qualification 能力")
        routed = qualifier(listing, exchange="SMART")
        required = {
            "con_id": listing["con_id"],
            "symbol": listing["symbol"],
            "local_symbol": listing["local_symbol"],
            "sec_type": listing["sec_type"],
            "primary_exchange": listing.get("primary_exchange"),
            "currency": "USD",
            "isin": listing["isin"],
            "listing_exchange": "LSEETF",
            "execution_exchange": "SMART",
            "exchange": "SMART",
        }
        mismatches = {
            key: {"expected": expected, "actual": routed.get(key)}
            for key, expected in required.items()
            if routed.get(key) != expected
        }
        if mismatches:
            raise ExecutionSafetyError(
                f"{alias}: SMART qualification identity mismatch: {mismatches}"
            )
        return routed, trusted

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
            result = self._broker().what_if_limit_order(
                resolved, quantity=quantity, limit_price=limit,
            )
            next_fee = _validate_what_if_result(result, funding_currency)
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
            "funding_currency": "USD",
            "venue": "LSE", "trading_currency": "USD",
            "share_class": "ACC", "side": "BUY", "order_type": "LIMIT",
        }
        for name, expected in required.items():
            actual = str(getattr(intent, name).value or "").upper()
            if actual != expected:
                raise ExecutionSafetyError(f"Case 1 {name}={actual}, expected={expected}")
        legs = ExecutionBatchService._canonical_leg_values(intent)
        budget_mode = str(intent.budget_mode.value or "").upper()
        if budget_mode in {"FIXED_TOTAL", "ALL_IN_TOTAL"}:
            # Entry compatibility for a single IB01 top-up. The existing fixed
            # target calculator, broker resolver, WhatIf, cash ledger and final
            # per-leg submission checks remain the execution authority.
            if (len(legs) == 1 and legs[0][:2] == ("IB01", "APPROX_AMOUNT")
                    and legs[0][2] is not None and legs[0][2] > 0
                    and money(intent.stated_cash.value) == legs[0][2]):
                return "IB01_ALL_IN_TARGET" if budget_mode == "ALL_IN_TOTAL" else "IB01_FIXED_TARGET"
            raise ExecutionSafetyError("IB01 fixed target must match the confirmed USD target amount")
        if budget_mode != "ALL_AVAILABLE_CASH":
            raise ExecutionSafetyError("Case 1 budget_mode unsupported")
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

    def create_batch(
        self,
        *,
        conversation_id: str,
        message_id: int,
        _controlled_retry_of: ExecutionBatch | None = None,
        _retry_attempt: int | None = None,
        _manual_limits: dict | None = None,
    ) -> ExecutionBatch:
        """Resolve and calculate a real or fake plan; never submits an order."""
        intent = self.load_confirmed_intent(
            conversation_id=conversation_id, message_id=message_id,
        )
        intent_variant = self._validate_case1_intent(intent)
        if _controlled_retry_of is None:
            existing = self.session.query(ExecutionBatch).filter_by(
                source_message_id=message_id,
            ).order_by(ExecutionBatch.created_at.desc()).first()
            if existing and existing.status not in {"CANCELLED", "COMPLETED"}:
                return existing

        if not self._broker().authenticate({}):
            raise ConnectionError("IBKR authenticate 失败")
        cash_snapshot = self._broker().get_cash_snapshot("USD")
        authoritative_cash, cash_source = select_authoritative_cash(cash_snapshot)
        open_order_details = self._broker().list_open_order_details()
        competing = [
            item for item in open_order_details
            if str(item.get("side", "")).upper() == "BUY"
            and int(item.get("remaining_quantity") or 0) > 0
        ]
        if competing:
            raise ExecutionSafetyError("检测到外部 BUY open order，Case 1 资金池被锁定")

        cushion = money(os.getenv("BATCH_CASH_SAFETY_CUSHION_USD", "25"))
        if not cushion.is_finite() or cushion < 0:
            raise ExecutionSafetyError("Invalid cash safety cushion")
        if intent_variant == "IB01_ALL_IN_TARGET" and authoritative_cash < money(intent.stated_cash.value):
            raise ExecutionSafetyError("INSUFFICIENT_USD_CASH_FOR_ALL_IN_BUDGET")
        if _manual_limits is not None and (intent_variant != "IB01_ALL_IN_TARGET" or set(_manual_limits) != {"IB01"}):
            raise ExecutionSafetyError("Unsupported manual limit input")
        if _controlled_retry_of is not None and _retry_attempt not in {2, 3}:
            raise ExecutionSafetyError("Controlled Retry attempt 必须明确为 2 或 3")

        batch = ExecutionBatch(
            broker="ibkr",
            account_ref=getattr(self.adapter, "_account_id", "fake-account"),
            funding_currency="USD",
            budget_mode=str(intent.budget_mode.value),
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
                "broker": "IBKR", "venue": "LSE",
                "listing_venue": "LSEETF", "routing": "SMART",
                "currency": "USD",
                "side": "BUY", "order_type": "LIMIT", "quantity": "WHOLE",
                "intent_variant": intent_variant,
                **({"quote_max_age_seconds": int(os.getenv("BATCH_QUOTE_MAX_AGE_SECONDS", "30")),
                    "budget_includes_fees": True, "budget_includes_safety_cushion": True, "time_in_force": "DAY"}
                   if intent_variant == "IB01_ALL_IN_TARGET" else {}),
                "sequence": [item[0] for item in self._canonical_leg_values(intent)],
                "external_open_order_count": len(open_order_details),
                "external_open_buy_order_count": len(competing),
                "remainder": "DYNAMIC_LAST",
                "unknown_timeout": "HARD_STOP",
                **({
                    "retry_attempt": _retry_attempt,
                    "retry_of_batch_id": _controlled_retry_of.id,
                    "max_retry_attempt": _retry_attempt,
                    "automatic_retry": False,
                    "human_ui_submit_only": True,
                } if _controlled_retry_of is not None else {}),
            }),
            status="DRAFT",
        )
        # Prepare broker facts on transient objects, never under a SQLite write lock.
        batch.id = str(uuid.uuid4())

        total_notional = Decimal("0")
        total_fees = Decimal("0")
        attention = []
        fixed_legs: list[ExecutionLeg] = []
        remainder_leg = None

        for sequence, (alias, mode, target) in enumerate(
            self._canonical_leg_values(intent), start=1,
        ):
            resolved, trusted = self._resolve_for_smart_execution(alias)
            expected_rule = EXPECTED_MARKET_RULE_IDS[alias]
            if resolved.get("market_rule_id") != expected_rule:
                raise ExecutionSafetyError(
                    f"{alias}: marketRuleId={resolved.get('market_rule_id')}, "
                    f"expected={expected_rule}"
                )
            quote = self._broker().get_executable_quote(resolved)
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
                market_open=self._broker().is_market_open(resolved, now=self.clock()),
            )
            leg.id = str(uuid.uuid4())
            batch.legs.append(leg)
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
                if _manual_limits is not None:
                    limit = money(_manual_limits[alias])
                    if not limit.is_finite() or limit <= 0 or normalize_buy_limit(limit, resolved["market_rule"]) != limit:
                        raise ExecutionSafetyError("手工 Limit 不符合 MarketRule tick")
                    leg.final_limit = limit
                    leg.limit_source = "USER_MANUAL_CONFIRMED"
                    leg.manual_limit_confirmed_at = self.clock()
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
                if intent_variant == "IB01_ALL_IN_TARGET":
                    quantity, notional, result, _ = self._calculate_remainder_with_what_if(
                        leg, budget_before_own_fee=money(batch.stated_cash) - cushion,
                        funding_currency=batch.funding_currency,
                    )
                    total_notional += notional - money(leg.estimated_notional)
                    leg.final_quantity = leg.estimated_quantity = quantity
                    leg.estimated_notional = notional
                else:
                    result = self._broker().what_if_limit_order(
                        json.loads(leg.resolution_snapshot),
                        quantity=leg.estimated_quantity,
                        limit_price=money(leg.final_limit),
                    )
                leg.what_if_snapshot = _json(result)
                total_fees += _validate_what_if_result(
                    result, batch.funding_currency,
                )
            except (ConnectionError, TimeoutError) as exc:
                leg.what_if_snapshot = _json({
                    "status": "PENDING_LIVE_ENABLE", "reason": str(exc),
                    "what_if": True, "transmit": True,
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
                        "what_if": True, "transmit": True,
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
        if intent_variant == "IB01_ALL_IN_TARGET" and not attention:
            self.assert_all_in_budget(batch)
        if attention:
            batch.status = "DRAFT"
            batch.attention_reason = _json(attention)
        else:
            for leg in batch.legs:
                leg.status = "READY"
            batch.status = "READY"
        from backend.services.action.ownership import execution_owner
        owner = execution_owner(self.session, self.adapter)
        policy = json.loads(batch.execution_policy)
        policy.update(portfolio_id=owner.portfolio_id, time_in_force="DAY")
        batch.execution_policy = _json(policy)
        self._update_risks(batch)
        if self.session.new or self.session.dirty or self.session.deleted:
            raise ExecutionSafetyError("COMMIT_PREPARATION_BEFORE_BROKER_REVIEW")
        self.session.rollback()
        from sqlalchemy import text
        self.session.execute(text("BEGIN IMMEDIATE"))
        candidates = self.session.query(ExecutionBatch).filter_by(source_message_id=message_id).filter(
            ExecutionBatch.status.notin_(["CANCELLED", "COMPLETED"])).order_by(ExecutionBatch.created_at.desc()).all()
        for active in candidates:
            active_policy = json.loads(active.execution_policy or "{}")
            if (_controlled_retry_of is None or
                (active_policy.get("retry_of_batch_id") == _controlled_retry_of.id
                 and active_policy.get("retry_attempt") == _retry_attempt)):
                return active
        self.session.add(batch)
        self._audit(
            "execution_batch_controlled_retry_created"
            if _controlled_retry_of is not None
            else "execution_batch_created",
            {
            "batch_id": batch.id, "source_message_id": message_id,
            "retry_of_batch_id": (
                _controlled_retry_of.id if _controlled_retry_of is not None else None
            ),
            "status": batch.status, "broker_mutation": 0,
            },
        )
        self.session.flush()
        return batch

    @staticmethod
    def _require_broker_cancel_evidence(order):
        try:
            raw = json.loads(order.raw_broker_response or "{}")
        except (TypeError, ValueError):
            raw = {}
        if not order.broker_order_id or raw.get("ib_status") not in {"Cancelled", "ApiCancelled"}:
            raise ExecutionSafetyError("UNPROVEN_LEGACY_CANCELLATION: reconcile with broker; no replacement order")

    def create_controlled_retry(self, batch_id: str) -> ExecutionBatch:
        """Create the one allowed Case 1 Retry #2 without reusing old orders.

        The source batch and both cancelled OrderRecords remain immutable audit
        history. Repeated HTTP/UI calls return the same Retry #2 batch and can
        never create a third attempt.
        """
        source = self.get_batch(batch_id)
        source_policy = json.loads(source.execution_policy or "{}")
        if source_policy.get("retry_attempt"):
            raise ExecutionSafetyError("Retry batch 不可再次创建 Retry")

        existing_retry = None
        for candidate in self.session.query(ExecutionBatch).filter_by(
            source_message_id=source.source_message_id,
        ).order_by(ExecutionBatch.created_at.desc()).all():
            policy = json.loads(candidate.execution_policy or "{}")
            if policy.get("retry_of_batch_id") == source.id:
                existing_retry = candidate
                break
        if existing_retry is not None:
            return existing_retry

        if source.status != "ATTENTION_REQUIRED":
            raise ExecutionSafetyError(
                f"仅 ATTENTION_REQUIRED 批次可创建受控 Retry，当前={source.status}"
            )
        if [leg.user_alias for leg in source.legs] != ["CBU3", "IB01"]:
            raise ExecutionSafetyError("受控 Retry #2 仅限冻结的 CBU3 → IB01 Case 1")

        old_orders: list[OrderRecord] = []
        for leg in source.legs:
            if leg.status != "CANCELLED" or not leg.linked_order_id:
                raise ExecutionSafetyError(
                    f"{leg.user_alias}: 旧 attempt 非明确 CANCELLED，HARD STOP"
                )
            order = self.session.query(OrderRecord).filter_by(
                id=leg.linked_order_id,
                batch_id=source.id,
                batch_leg_id=leg.id,
            ).first()
            if order is None or order.status != "cancelled" or int(order.filled_quantity or 0):
                raise ExecutionSafetyError(
                    f"{leg.user_alias}: 旧 OrderRecord 非 cancelled/filled=0，HARD STOP"
                )
            self._require_broker_cancel_evidence(order)
            old_orders.append(order)

        if not self._broker().authenticate({}):
            raise ConnectionError("IBKR authenticate 失败")
        if getattr(self.adapter, "_account_id", None) != source.account_ref:
            raise ExecutionSafetyError("当前 Gateway account 与原 Case 1 不匹配")

        open_orders = self._broker().list_open_order_details()
        if open_orders:
            raise ExecutionSafetyError("Broker 当前存在 open order，HARD STOP")

        execution_reader = getattr(self.adapter, "list_execution_details", None)
        if not callable(execution_reader):
            raise ExecutionSafetyError("Adapter 缺少 execution reconciliation 能力")
        executions = execution_reader()
        old_order_refs = {order.id for order in old_orders}
        old_broker_ids = {
            str(order.broker_order_id) for order in old_orders if order.broker_order_id
        }
        conflicting = [
            item for item in executions
            if str(item.get("order_ref") or "") in old_order_refs
            or str(item.get("perm_id") or "") in old_broker_ids
        ]
        if conflicting:
            raise ExecutionSafetyError("旧 attempt 出现 execution，HARD STOP")

        retry = self.create_batch(
            conversation_id=source.source_conversation_id,
            message_id=source.source_message_id,
            _controlled_retry_of=source,
            _retry_attempt=2,
        )
        if retry.account_ref != source.account_ref:
            raise ExecutionSafetyError("Retry account 与原 Case 1 不一致")
        return retry

    def create_smart_routing_retry(self, batch_id: str) -> ExecutionBatch:
        """Create the one owner-authorized SMART-routed Case 1 Retry #3."""
        source = self.get_batch(batch_id)
        source_policy = json.loads(source.execution_policy or "{}")
        if source_policy.get("retry_attempt") != 2:
            raise ExecutionSafetyError("SMART Retry #3 仅可从 Retry #2 创建")

        for candidate in self.session.query(ExecutionBatch).filter_by(
            source_message_id=source.source_message_id,
        ).order_by(ExecutionBatch.created_at.desc()).all():
            policy = json.loads(candidate.execution_policy or "{}")
            if (
                policy.get("retry_attempt") == 3
                and policy.get("retry_of_batch_id") == source.id
            ):
                return candidate

        if source.status != "ATTENTION_REQUIRED":
            raise ExecutionSafetyError(
                f"Retry #2 status={source.status}，不可创建 Retry #3"
            )
        if [leg.user_alias for leg in source.legs] != ["CBU3", "IB01"]:
            raise ExecutionSafetyError("SMART Retry #3 仅限 CBU3 → IB01")
        first, second = source.legs
        if first.status != "CANCELLED" or not first.linked_order_id:
            raise ExecutionSafetyError("CBU3 Retry #2 非明确 CANCELLED，HARD STOP")
        first_order = self.session.query(OrderRecord).filter_by(
            id=first.linked_order_id,
            batch_id=source.id,
            batch_leg_id=first.id,
        ).first()
        if (
            first_order is None
            or first_order.status != "cancelled"
            or int(first_order.filled_quantity or 0)
        ):
            raise ExecutionSafetyError(
                "CBU3 Retry #2 OrderRecord 非 cancelled/filled=0，HARD STOP"
            )
        try:
            broker_evidence = json.loads(first_order.raw_broker_response or "{}")
        except json.JSONDecodeError as exc:
            raise ExecutionSafetyError(
                "CBU3 Retry #2 缺少可解析 Broker evidence，HARD STOP"
            ) from exc
        error_codes = {
            int(item.get("errorCode"))
            for item in broker_evidence.get("broker_error_history", [])
            if item.get("errorCode") is not None
        }
        if broker_evidence.get("broker_error_code") is not None:
            error_codes.add(int(broker_evidence["broker_error_code"]))
        if not {10311, 201}.issubset(error_codes):
            raise ExecutionSafetyError(
                "Retry #2 root cause 非 10311→201，禁止 SMART Retry #3"
            )
        self._require_broker_cancel_evidence(first_order)
        if (
            second.status not in {"READY", "NOT_SUBMITTED"}
            or second.linked_order_id
            or second.submission_attempted_at
        ):
            raise ExecutionSafetyError("IB01 Retry #2 已发生提交尝试，HARD STOP")

        if not self._broker().authenticate({}):
            raise ConnectionError("IBKR authenticate 失败")
        if getattr(self.adapter, "_account_id", None) != source.account_ref:
            raise ExecutionSafetyError("当前 Gateway account 与 Retry #2 不匹配")
        if self._broker().list_open_order_details():
            raise ExecutionSafetyError("Broker 当前存在 open order，HARD STOP")
        execution_reader = getattr(self.adapter, "list_execution_details", None)
        if not callable(execution_reader):
            raise ExecutionSafetyError("Adapter 缺少 execution reconciliation 能力")
        executions = execution_reader()

        prior_batches = self.session.query(ExecutionBatch).filter_by(
            source_message_id=source.source_message_id,
        ).all()
        prior_orders = self.session.query(OrderRecord).filter(
            OrderRecord.batch_id.in_([item.id for item in prior_batches]),
        ).all()
        prior_refs = {order.id for order in prior_orders}
        prior_broker_ids = {
            str(order.broker_order_id)
            for order in prior_orders if order.broker_order_id
        }
        conflicting = [
            item for item in executions
            if str(item.get("order_ref") or "") in prior_refs
            or str(item.get("perm_id") or "") in prior_broker_ids
        ]
        if conflicting:
            raise ExecutionSafetyError("旧 attempt 出现 execution，HARD STOP")

        retry = self.create_batch(
            conversation_id=source.source_conversation_id,
            message_id=source.source_message_id,
            _controlled_retry_of=source,
            _retry_attempt=3,
        )
        if retry.account_ref != source.account_ref:
            raise ExecutionSafetyError("Retry #3 account 与 Retry #2 不一致")
        return retry

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
            "usable_cash": str(batch.usable_cash),
            "estimated_fees": str(batch.estimated_fees),
            "estimated_total": str(batch.estimated_total),
            "estimated_residual": str(batch.estimated_residual),
            "legs": [
                {
                    "id": leg.id, "alias": leg.user_alias,
                    "con_id": leg.resolved_con_id, "isin": leg.isin,
                    "exchange": leg.exchange, "currency": leg.currency,
                    "allocation_mode": leg.allocation_mode,
                    "target_amount": str(leg.target_amount) if leg.target_amount else None,
                    "share_class_verification": leg.share_class_verification,
                    "quote_as_of": leg.quote_as_of.isoformat() if leg.quote_as_of else None,
                    "final_limit": str(leg.final_limit),
                    "final_quantity": leg.final_quantity,
                    "what_if": json.loads(leg.what_if_snapshot or "null"),
                }
                for leg in batch.legs
            ],
        }

    @staticmethod
    def is_all_in(batch: ExecutionBatch) -> bool:
        return json.loads(batch.execution_policy or "{}").get("intent_variant") == "IB01_ALL_IN_TARGET"

    def assert_all_in_budget(self, batch: ExecutionBatch, *, fee=None) -> None:
        total = money(batch.estimated_total) + money(batch.estimated_fees if fee is None else fee) + money(batch.safety_cushion)
        fee_value = money(batch.estimated_fees if fee is None else fee)
        if not fee_value.is_finite() or fee_value < 0:
            raise ExecutionSafetyError("Invalid WhatIf fee reserve")
        if total > money(batch.stated_cash) or total > money(batch.usable_cash):
            raise ExecutionSafetyError("ALL_IN_BUDGET_EXCEEDED: notional + fees + safety cushion")

    def assert_review_fresh(self, batch: ExecutionBatch) -> None:
        for leg in batch.legs:
            if leg.linked_order_id:
                continue
            if leg.quote_quality != "LIVE" or str(leg.market_data_type) != "1" or not leg.market_open:
                raise ExecutionSafetyError("Fresh LIVE quote and open market required")
            quote_guard({"quote_quality": leg.quote_quality, "ask": leg.quote_ask,
                         "bid": leg.quote_bid, "quote_timestamp": leg.quote_as_of}, now=self.clock())
            _validate_what_if_result(json.loads(leg.what_if_snapshot or "null") or {}, batch.funding_currency)
        self.assert_all_in_budget(batch)

    @staticmethod
    def review_fingerprint(batch: ExecutionBatch, *, authorization=False) -> str:
        # Bind economic facts, not transient IDs or the broker's new timestamp.
        facts = ExecutionBatchService._confirmation_payload(batch)
        facts.pop("batch_id")
        if not authorization:
            facts["execution_policy"].pop("order_epoch", None)
            facts["execution_policy"].pop("portfolio_epoch", None)
            facts["execution_policy"].pop("risk_acknowledged", None)
        facts["request_id"] = batch.source_conversation_id
        for name in ("safety_cushion", "usable_cash", "estimated_fees", "estimated_total", "estimated_residual"):
            facts[name] = format(money(facts[name]).normalize(), "f")
        for leg in facts["legs"]:
            leg.pop("id")
            leg.pop("quote_as_of")
            for name in ("target_amount", "final_limit"):
                leg[name] = format(money(leg[name]).normalize(), "f")
            what_if = leg["what_if"] or {}
            leg["what_if"] = {"status": what_if.get("status"),
                              "fee": format(_commission_in_funding_currency(what_if, batch.funding_currency).normalize(), "f") if what_if.get("status") == "PASS" else None}
        facts["legs"] = [{**item, "quote_ask": format(money(leg.quote_ask).normalize(), "f"), "quote_bid": format(money(leg.quote_bid).normalize(), "f")}
                         for item, leg in zip(facts["legs"], batch.legs)]
        return hashlib.sha256(_json(facts).encode()).hexdigest()

    def _lock_all_in_review(self, batch: ExecutionBatch) -> None:
        # Use the existing SQLite write-serialization contract for version changes
        # and submission. A simultaneous price edit cannot submit an old review.
        if self.is_all_in(batch) and self.session.get_bind().dialect.name == "sqlite":
            from sqlalchemy import text
            connection = self.session.connection().connection.driver_connection
            if not connection.in_transaction:
                self.session.execute(text("BEGIN IMMEDIATE"))
            self.session.refresh(batch)

    def reprice_all_in(self, batch_id: str, limits: dict | None, version: int) -> ExecutionBatch:
        batch = self.get_batch(batch_id)
        self._lock_all_in_review(batch)
        if not self.is_all_in(batch) or version != batch.confirmation_version:
            raise ExecutionSafetyError("confirmation version 无效")
        if batch.status in {"CANCELLED", "COMPLETED"} or any(leg.linked_order_id for leg in batch.legs):
            raise ExecutionSafetyError("Terminal/submitted batch cannot be repriced")
        # Invalidate first, even if fresh quotes/WhatIf subsequently fail.
        batch.status = "CANCELLED"
        batch.confirmation_hash = None
        batch.confirmation_version += 1
        policy = json.loads(batch.execution_policy or "{}")
        policy["review_refresh_pending"] = True
        batch.execution_policy = _json(policy)
        self._audit("execution_batch_price_change_invalidated", {"batch_id": batch.id,
                    "confirmation_version": batch.confirmation_version, "broker_mutation": 0})
        self.session.commit()
        # Invalidation must survive a failed refresh, but reacquire the existing
        # writer lock before checking/creating its replacement. Concurrent link
        # retries then observe and reuse one replacement rather than create two.
        next_batch = self.create_batch(conversation_id=batch.source_conversation_id,
                                       message_id=batch.source_message_id, _manual_limits=limits)
        next_batch.confirmation_version = max(next_batch.confirmation_version, batch.confirmation_version)
        return next_batch

    def _update_risks(self, batch):
        from backend.services.action.risk_engine import portfolio_order_risk
        policy = json.loads(batch.execution_policy)
        risks = {leg.user_alias: portfolio_order_risk(self.session,
                    portfolio_id=policy["portfolio_id"], symbol=f"{leg.user_alias}:LSE",
                    side="BUY", quantity=leg.final_quantity or 0,
                    limit_price=money(leg.final_limit), currency=leg.currency)
                 for leg in batch.legs if not leg.linked_order_id}
        policy["risk_warnings"] = risks
        batch.execution_policy = _json(policy)

    def confirm_batch(self, batch_id: str, *, risk_confirmation_text="") -> ExecutionBatch:
        from backend.services.action.ownership import execution_owner
        from backend.services.action.risk_engine import CONFIRMATION_TEXT
        batch = self.get_batch(batch_id)
        if batch.status != "READY":
            raise ExecutionSafetyError(f"Batch status={batch.status}，不可确认")
        self.assert_review_fresh(batch)
        owner = execution_owner(self.session, self.adapter)
        policy = json.loads(batch.execution_policy)
        if policy.get("portfolio_id") != owner.portfolio_id or batch.account_ref != owner.account_id:
            raise ExecutionSafetyError("BATCH_OWNER_MISMATCH")
        if any(item.get("requires_confirmation") for item in policy.get("risk_warnings", {}).values()):
            if risk_confirmation_text != CONFIRMATION_TEXT:
                raise ExecutionSafetyError("RISK_CONFIRMATION_REQUIRED")
        policy["risk_acknowledged"] = risk_confirmation_text == CONFIRMATION_TEXT
        manager = OrderManager(self.session, self.adapter)
        policy["order_epoch"] = manager._order_epoch(owner, excluding_batch=batch.id)
        policy["portfolio_epoch"] = manager._portfolio_epoch(owner.portfolio_id)
        batch.execution_policy = _json(policy)
        batch.confirmation_version += 1
        batch.confirmation_hash = self.review_fingerprint(batch, authorization=True)
        batch.status = "CONFIRMED"
        batch.confirmed_at = self.clock()
        self._audit("execution_batch_confirmed", {"batch_id": batch.id,
            "confirmation_version": batch.confirmation_version, "confirmation_hash": batch.confirmation_hash})
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
        policy = json.loads(batch.execution_policy or "{}")
        retry_source = None
        retry_attempt = policy.get("retry_attempt")
        if retry_attempt in {2, 3}:
            retry_source_id = str(policy.get("retry_of_batch_id") or "")
            retry_source = self.get_batch(retry_source_id)
        batch.status = "CANCELLED"
        self._audit("execution_batch_superseded", {
            "batch_id": batch.id,
            "retry_of_batch_id": retry_source.id if retry_source else None,
            "broker_mutation": 0,
        })
        self.session.commit()  # retire old review before any fresh broker I/O
        return self.create_batch(
            conversation_id=batch.source_conversation_id,
            message_id=batch.source_message_id,
            _controlled_retry_of=retry_source,
            _retry_attempt=retry_attempt,
        )

    def review_remaining(self, batch_id):
        """Refresh unclaimed legs in place. Linked broker facts stay immutable."""
        batch = self.get_batch(batch_id)
        if not any(leg.linked_order_id for leg in batch.legs):
            return self.refresh_batch(batch_id)
        if any(leg.status in {"UNKNOWN", "REJECTED", "SUBMITTING"} for leg in batch.legs if leg.linked_order_id):
            raise ExecutionSafetyError("RECONCILIATION_REQUIRED")
        if batch.attention_reason and "REMAINING_STOPPED" in batch.attention_reason:
            raise ExecutionSafetyError("REMAINING_STOPPED")
        changes = []
        with self.session.no_autoflush:
            ledger = self._build_ledger(batch)
            cash, _ = select_authoritative_cash(self._broker().get_cash_snapshot("USD"))
            ledger.consistency_guard(cash)
            for leg in batch.legs:
                if leg.linked_order_id:
                    continue
                resolved, _ = self._resolve_for_smart_execution(leg.user_alias)
                if resolved["con_id"] != leg.resolved_con_id:
                    raise ExecutionSafetyError("CONTRACT_CHANGED")
                quote = self._broker().get_executable_quote(resolved)
                ask, _ = quote_guard(quote, now=self.clock())
                if quote.get("quote_quality") != "LIVE" or str(quote.get("market_data_type")) != "1":
                    raise ExecutionSafetyError("LIVE_QUOTE_REQUIRED")
                old = {"quantity": leg.final_quantity, "limit": str(leg.final_limit)}
                leg.final_limit = (money(leg.final_limit) if leg.limit_source == "USER_MANUAL_CONFIRMED"
                                   else normalize_buy_limit(ask, resolved["market_rule"]))
                budget = (ledger.remaining + _validate_what_if_result(json.loads(leg.what_if_snapshot), batch.funding_currency)
                          if leg.allocation_mode == "REMAINDER" else money(leg.target_amount))
                if leg.allocation_mode == "REMAINDER":
                    quantity, notional, result, fee = self._calculate_remainder_with_what_if(
                        leg, budget_before_own_fee=budget, funding_currency=batch.funding_currency)
                else:
                    quantity, notional = calculate_fixed_quantity(budget, leg.final_limit)
                    result = self._broker().what_if_limit_order(resolved, quantity=quantity, limit_price=leg.final_limit)
                    fee = _validate_what_if_result(result, batch.funding_currency)
                leg.final_quantity = leg.estimated_quantity = quantity
                leg.estimated_notional, leg.what_if_snapshot = notional, _json(result)
                leg.quote_bid, leg.quote_ask, leg.quote_last = quote.get("bid"), quote.get("ask"), quote.get("last")
                leg.quote_as_of = datetime.fromisoformat(quote["quote_timestamp"].replace("Z", "+00:00"))
                leg.status = "READY"
                changes.append({"leg_id": leg.id, "before": old,
                                "after": {"quantity": quantity, "limit": str(leg.final_limit)}})
            self._update_risks(batch)
        batch.confirmation_hash = None
        batch.confirmation_version += 1
        batch.status = "READY"
        batch.attention_reason = _json(["REVIEW_REQUIRED", changes])
        self._audit("execution_remaining_reviewed", {"batch_id": batch.id, "changes": changes})
        self.session.flush()
        return batch

    def apply_manual_limits(
        self, batch_id: str, limits: dict[str, object],
    ) -> ExecutionBatch:
        """Apply user-entered limits with exact MarketRule validation and WhatIf."""
        batch = self.get_batch(batch_id)
        policy = json.loads(batch.execution_policy or "{}")
        if policy.get("retry_attempt") in {2, 3}:
            raise ExecutionSafetyError("Controlled Retry 必须使用 Fresh Live Quote")
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
            leg.market_open = self._broker().is_market_open(resolved, now=self.clock())
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
                result = self._broker().what_if_limit_order(
                    json.loads(leg.resolution_snapshot),
                    quantity=leg.final_quantity,
                    limit_price=money(leg.final_limit),
                )
                leg.what_if_snapshot = _json(result)
                total_fees += _validate_what_if_result(
                    result, batch.funding_currency,
                )
            except (ConnectionError, TimeoutError) as exc:
                leg.what_if_snapshot = _json({
                    "status": "PENDING_LIVE_ENABLE", "reason": str(exc),
                    "what_if": True, "transmit": True,
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
                    "what_if": True, "transmit": True,
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
        self._update_risks(batch)
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
        requested = next((item for item in batch.legs if item.id == leg_id), None) if leg_id else None
        if requested and requested.linked_order_id:
            return self.session.get(OrderRecord, requested.linked_order_id)
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
        resolved, _ = self._resolve_for_smart_execution(leg.user_alias)
        if resolved["con_id"] != leg.resolved_con_id:
            raise ExecutionSafetyError("conId changed; confirmation invalid")
        if not self._broker().is_market_open(resolved, now=self.clock()):
            raise ExecutionSafetyError("MARKET_CLOSED：仅允许当前可交易时段即时提交")
        local_order_refs = {
            item.linked_order_id for item in batch.legs if item.linked_order_id
        }
        external = [
            item for item in self._broker().list_open_order_details()
            if str(item.get("side", "")).upper() == "BUY"
            and str(item.get("order_ref", "")) not in local_order_refs
        ]
        if external:
            raise ExecutionSafetyError("存在竞争性外部 BUY open order")

        fresh_cash, _ = select_authoritative_cash(self._broker().get_cash_snapshot("USD"))
        ledger = self._build_ledger(batch)
        ledger.consistency_guard(fresh_cash)
        quote = self._broker().get_executable_quote(resolved)
        if str(quote.get("market_data_type")) != "1":
            raise ExecutionSafetyError("提交前 marketDataType 非 LIVE(1)")
        if str(quote.get("quote_quality") or "").upper() != "LIVE":
            raise ExecutionSafetyError("提交前 quote quality 非 LIVE")
        ask, _ = quote_guard(quote, now=self.clock())
        if leg.allocation_mode == "REMAINDER":
            if money(leg.released_intent_amount) > 0:
                raise ExecutionSafetyError("intent-level released budget requires reconfirmation")
            # Never silently change a human-confirmed limit/quantity at dispatch.
            limit = (money(leg.final_limit) if leg.limit_source == "USER_MANUAL_CONFIRMED"
                     else normalize_buy_limit(ask, resolved["market_rule"]))
            quantity, _ = calculate_fixed_quantity(ledger.remaining, limit)
            if quantity != leg.final_quantity or limit != money(leg.final_limit):
                raise ExecutionSafetyError("REVIEW_REQUIRED: refresh unclaimed remaining legs and reconfirm")
        quantity = int(leg.final_quantity or 0)
        limit = money(leg.final_limit)
        if quantity <= 0 or limit <= 0:
            raise ExecutionSafetyError("Leg 无有效 quantity/limit")
        self.assert_review_fresh(batch)
        expected = self.review_fingerprint(batch, authorization=True)
        if expected != batch.confirmation_hash:
            raise ExecutionSafetyError("confirmation payload changed")
        if leg.target_amount and money(quantity) * limit > money(leg.target_amount):
            raise ExecutionSafetyError("Fixed Target ceiling violated")
        what_if = self._broker().what_if_limit_order(
            resolved, quantity=quantity, limit_price=limit,
        )
        fee = _validate_what_if_result(what_if, batch.funding_currency)
        if self.is_all_in(batch):
            self.assert_all_in_budget(batch, fee=fee)
            if fee != money(batch.estimated_fees):
                raise ExecutionSafetyError("WhatIf fee changed; refresh and reconfirm")
        old_fee = _validate_what_if_result(json.loads(leg.what_if_snapshot), batch.funding_currency)
        if fee != old_fee:
            raise ExecutionSafetyError("REVIEW_REQUIRED: WhatIf fee changed")
        policy = json.loads(batch.execution_policy)
        from backend.services.action.risk_engine import portfolio_order_risk
        current_risk = portfolio_order_risk(self.session, portfolio_id=policy["portfolio_id"],
            symbol=f"{leg.user_alias}:LSE", side="BUY", quantity=quantity, limit_price=limit, currency=leg.currency)
        if current_risk != policy.get("risk_warnings", {}).get(leg.user_alias):
            raise ExecutionSafetyError("REVIEW_REQUIRED: risk evidence changed")
        # Preparation is read-only; Tx A creates strategy+claim+leg link together.
        quote_age = (self.clock() - (leg.quote_as_of.replace(tzinfo=timezone.utc)
                    if leg.quote_as_of.tzinfo is None else leg.quote_as_of)).total_seconds()
        deadline = time.time() + max(0, int(policy.get("quote_max_age_seconds", 30)) - quote_age)
        order = OrderManager(self.session, self.adapter).place_order(str(uuid.uuid4()), {
            "quantity": quantity, "limit_price": limit, "order_type": "LIMIT",
            "time_in_force": policy.get("time_in_force", "DAY"), "resolved_contract": resolved,
            "batch_id": batch.id, "batch_leg_id": leg.id, "confirmation_version": confirmation_version,
            "confirmation_hash": batch.confirmation_hash, "dispatch_deadline": deadline,
        })
        self.session.expire_all()
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
        OrderManager(self.session, self.adapter).sync_order_status(order.id)
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
        batch.attention_reason = _json(["REMAINING_STOPPED"])
        linked = [leg for leg in batch.legs if leg.linked_order_id]
        batch.status = ("ATTENTION_REQUIRED" if any(leg.status in {"UNKNOWN", "REJECTED"} for leg in linked)
                        else "SUBMITTED" if any(leg.status in {"OPEN", "SUBMITTING", "PARTIAL_FILLED"} for leg in linked)
                        else "CANCELLED")
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
