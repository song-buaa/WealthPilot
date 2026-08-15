"""Deterministic quote, tick, cash and amount calculators."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import os


class ExecutionSafetyError(ValueError):
    pass


def money(value) -> Decimal:
    if isinstance(value, dict):
        value = value.get("amount", 0)
    return Decimal(str(value or 0))


def select_authoritative_cash(snapshot: dict) -> tuple[Decimal, str]:
    """Choose the most conservative real USD cash metric; never use leverage."""
    candidates = []
    for key in ("CashBalance", "SettledCash", "TotalCashValue"):
        value = snapshot.get(key)
        if value is not None:
            candidates.append((money(value), key))
    if not candidates:
        raise ExecutionSafetyError("缺少 USD CashBalance/SettledCash/TotalCashValue")
    non_negative = [(value, key) for value, key in candidates if value >= 0]
    if not non_negative:
        raise ExecutionSafetyError("USD cash metrics 均为负值")
    return min(non_negative, key=lambda item: item[0])


def market_rule_increment(price: Decimal, tiers: list[dict]) -> Decimal:
    if price <= 0 or not tiers:
        raise ExecutionSafetyError("无有效 MarketRule tier")
    selected = None
    for tier in sorted(tiers, key=lambda item: money(item["low_edge"])):
        if price >= money(tier["low_edge"]):
            selected = money(tier["increment"])
        else:
            break
    if not selected or selected <= 0:
        raise ExecutionSafetyError("价格不在 MarketRule 可用区间")
    return selected


def normalize_buy_limit(reference: Decimal, tiers: list[dict]) -> Decimal:
    """Round a BUY reference upward so the limit never falls below best ask."""
    increment = market_rule_increment(reference, tiers)
    units = (reference / increment).to_integral_value(rounding=ROUND_CEILING)
    return units * increment


def calculate_fixed_quantity(target: Decimal, limit: Decimal) -> tuple[int, Decimal]:
    if target <= 0 or limit <= 0:
        raise ExecutionSafetyError("target/limit 必须为正")
    quantity = int((target / limit).to_integral_value(rounding=ROUND_FLOOR))
    if quantity <= 0:
        raise ExecutionSafetyError("整股取整后 quantity=0")
    notional = money(quantity) * limit
    if notional > target:
        raise ExecutionSafetyError("Fixed Target notional 超过用户授权")
    return quantity, notional


def quote_guard(quote: dict, *, now: datetime | None = None) -> tuple[Decimal, Decimal]:
    now = now or datetime.now(timezone.utc)
    quality = str(quote.get("quote_quality") or "MISSING").upper()
    if quality not in {"LIVE", "DELAYED", "FROZEN"}:
        raise ExecutionSafetyError(f"quote quality={quality}")
    ask, bid = money(quote.get("ask")), money(quote.get("bid"))
    if ask <= 0:
        raise ExecutionSafetyError("缺少可执行 best ask")
    as_of = quote.get("quote_timestamp")
    if isinstance(as_of, str):
        as_of = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    if not isinstance(as_of, datetime):
        raise ExecutionSafetyError("quote timestamp 缺失")
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    max_age = int(os.getenv("BATCH_QUOTE_MAX_AGE_SECONDS", "30"))
    if (now - as_of.astimezone(timezone.utc)).total_seconds() > max_age:
        raise ExecutionSafetyError("quote stale")
    if bid > 0 and ask >= bid:
        mid = (ask + bid) / 2
        spread = (ask - bid) / mid
        max_spread = money(os.getenv("BATCH_MAX_SPREAD_PCT", "0.01"))
        if spread > max_spread:
            raise ExecutionSafetyError(
                f"spread {spread:.6f} 超过阈值 {max_spread:.6f}"
            )
    return ask, bid


@dataclass(frozen=True)
class CashLedger:
    initial_cash: Decimal
    filled_cost: Decimal = Decimal("0")
    active_reservations: Decimal = Decimal("0")
    fee_reserve: Decimal = Decimal("0")
    safety_cushion: Decimal = Decimal("25")
    intent_release_blocked: Decimal = Decimal("0")

    @property
    def remaining(self) -> Decimal:
        return max(
            Decimal("0"),
            self.initial_cash
            - self.filled_cost
            - self.active_reservations
            - self.fee_reserve
            - self.safety_cushion
            - self.intent_release_blocked,
        )

    def consistency_guard(self, fresh_cash: Decimal) -> None:
        if fresh_cash < self.remaining:
            raise ExecutionSafetyError(
                "Broker fresh cash 低于本地安全账本，停止后续执行"
            )
