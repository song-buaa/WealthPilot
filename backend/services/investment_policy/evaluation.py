"""Pure candidate diagnostics. No order permission or legacy-rule mutation."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from .contracts import MetricEvidence, Snapshot
from .parser import PolicyError
from .store import clock


def evaluate_snapshot(snapshot: Snapshot, *, metrics: dict[str, MetricEvidence], issuer_symbol: str | None,
                      behavior_facts: dict[str, bool | None], as_of: str, max_age: timedelta,
                      existing_safety_blocks: tuple[str, ...] = ()):
    """Diagnostic-only; input data qualification is explicit, never fetched here.

    Incomplete lookthrough may prove a breach via a qualified lower bound, never
    prove safety. Missing identity/denominator/time invalidates the measurement.
    Existing execution safety gates are carried through, never translated away.
    """
    cutoff = clock(as_of)
    if max_age.total_seconds() < 0:
        raise PolicyError("INVALID_MAX_AGE")
    triggers, unknown = [], []
    exceptions = {r.subject for r in snapshot.rules if r.metric == "issuer_weight" and r.subject}
    for rule in snapshot.rules:
        if rule.metric == "issuer_weight":
            if not issuer_symbol:
                unknown.append({"rule_id": rule.rule_id, "reason": "ISSUER_IDENTITY_UNKNOWN"})
                continue
            if rule.subject and rule.subject != issuer_symbol:
                continue
            if rule.rule_id == "issuer.warning.default" and issuer_symbol in exceptions:
                continue
        if rule.operator == "prohibited":
            value = behavior_facts.get(rule.metric)
            if value is True:
                triggers.append({"rule_id": rule.rule_id, "action": rule.action, "source_clause": rule.source_clause.model_dump(), "auto_trade_allowed": False})
            elif value is not False:
                unknown.append({"rule_id": rule.rule_id, "reason": "BEHAVIOR_FACT_UNKNOWN"})
            continue
        evidence = metrics.get(rule.metric)
        try:
            qualified = (evidence is not None and evidence.value is not None and evidence.fresh
                         and evidence.identity_verified and evidence.denominator_qualified
                         and evidence.as_of is not None and timedelta(0) <= cutoff - clock(evidence.as_of) <= max_age)
        except PolicyError:
            qualified = False
        if not qualified:
            unknown.append({"rule_id": rule.rule_id, "reason": "MISSING_STALE_OR_UNQUALIFIED_DATA"})
            continue
        if not evidence.complete and rule.metric not in {"issuer_weight", "btc_weight"}:
            # A partial absolute deviation is not necessarily a lower bound:
            # missing holdings could move an underweight class toward its target.
            unknown.append({"rule_id": rule.rule_id, "reason": "INCOMPLETE_NON_MONOTONIC_METRIC"})
            continue
        hit = evidence.value > rule.threshold if rule.operator == "gt" else evidence.value >= rule.threshold
        if hit:
            triggers.append({"rule_id": rule.rule_id, "action": rule.action,
                             "value": str(evidence.value), "threshold": str(rule.threshold),
                             "source_clause": rule.source_clause.model_dump(),
                             "coverage": "COMPLETE" if evidence.complete else "LOWER_BOUND_ONLY", "auto_trade_allowed": False})
        if not evidence.complete:
            unknown.append({"rule_id": rule.rule_id, "reason": "INCOMPLETE_COVERAGE_CANNOT_CLEAR"})
    actions = {item["action"] for item in triggers}
    if existing_safety_blocks or "HARD_BLOCK" in actions:
        status = "HARD_BLOCK"
    elif "HUMAN_REVIEW" in actions:
        status = "HUMAN_REVIEW"
    elif "WARNING" in actions:
        status = "WARNING"
    else:
        status = "UNKNOWN" if unknown else "NO_RULE_TRIGGERED_NOT_EXECUTION_APPROVAL"
    return dict(status=status, triggers=triggers, unknown=unknown, existing_safety_blocks=list(existing_safety_blocks),
                auto_trade_allowed=False, evaluation_mode="ISOLATED_SNAPSHOT_DIAGNOSTIC",
                policy_version=snapshot.policy_version, portfolio_compliance="UNKNOWN" if unknown else "NOT_AN_EXECUTION_ASSESSMENT")


def budget_preview(snapshot: Snapshot, *, china_used: Decimal, unallocated_us_used: Decimal, temporary_cash: Decimal):
    """Conditional unused-budget routing, not a permanent target change or order.

    No automatic redistribution of remaining IB01/CBU3 when cash is requested;
    that product-level decision remains UNKNOWN instead of inventing a rule.
    """
    a = snapshot.allocation
    for number in (china_used, unallocated_us_used, temporary_cash):
        if not number.is_finite() or number < 0:
            raise PolicyError("INVALID_SCENARIO_NUMBER")
    if (china_used > a.neutral["china"] or unallocated_us_used > a.direct_equity.get("unallocated", Decimal(0))
            or temporary_cash > a.temporary_cash_max):
        raise PolicyError("SCENARIO_REQUIRES_NEW_POLICY_DECISION")
    us_direct = a.specified_equity_total + unallocated_us_used
    alternatives = a.neutral["gold"] + a.neutral["btc"]
    us_total = a.growth_total - china_used - alternatives
    us_etf = us_total - us_direct
    result = dict(us_etf=us_etf, us_direct=us_direct, china=china_used, gold=a.neutral["gold"],
                  btc=a.neutral["btc"], stable_products=a.stable_total-temporary_cash, cash=temporary_cash)
    if sum(result.values()) != 1:
        raise PolicyError("SCENARIO_NOT_CLOSED")
    return {"budget": {k: str(v) for k, v in result.items()}, "etf_products": {k: str(us_etf*v) for k, v in a.etf_internal.items()},
            "stable_product_split": "UNKNOWN_REQUIRES_EXPLICIT_PLAN" if temporary_cash else "UNCHANGED",
            "status": "CONDITIONAL_BUDGET_ONLY", "auto_trade_allowed": False}
