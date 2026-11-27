"""Only original B/C can admit actual risk; D remains frozen scenario evidence."""
from decimal import Decimal

from backend.services.instruments.issuers import IDENTITIES

from .store import digest

PORTFOLIO_SOURCE_ERRORS = {
    "E1_PORTFOLIO_SOURCE_UNAVAILABLE", "E1_PORTFOLIO_SNAPSHOT_CHANGED", "E1_PORTFOLIO_POLICY_CHANGED",
    "E1_PORTFOLIO_ISOLATION_REQUIRED", "E1_PORTFOLIO_SOURCE_CHANGED", "E1_PORTFOLIO_RISK_RULE_MISMATCH",
    "E1_PORTFOLIO_SOURCE_SCOPE_MISMATCH",
}


def _risk_key(portfolio_id, rule_id, subject):
    return digest({"portfolio_id": portfolio_id, "rule_id": rule_id, "subject": subject})


def collect_policy_risks(portfolio_id, policy, allocation, concentration):
    facts = {}
    rules = {rule.rule_id: rule for rule in policy.rules}
    for subject, row in {**concentration.get("issuers", {}), "BTC_SPOT": concentration.get("btc", {})}.items():
        if concentration.get("status") != "DIAGNOSTIC" or concentration.get("denominator_cny") is None:
            break
        hits = {hit["rule_id"]: hit for hit in row.get("policy_hits", [])}
        ceiling = (row.get("known_lower_bound_pct") if subject == "BTC_SPOT" and not row.get("unknown_coverage", True)
                   else row.get("proven_upper_bound_pct"))
        for rule in rules.values():
            is_btc = subject == "BTC_SPOT"
            if (rule.metric != ("btc_weight" if is_btc else "issuer_weight")
                    or rule.scope != ("ALL_INVESTABLE_ASSETS_ECONOMIC" if is_btc else "ALL_INVESTABLE_ASSETS_LOOKTHROUGH")
                    or rule.operator not in {"gt", "gte"}):
                continue
            hit = hits.get(rule.rule_id)
            # A bounded row can dismiss a prior hit only against a proved
            # applicable rule. Exceptions are resolved by C's own rule logic.
            if rule.subject and IDENTITIES.get((rule.subject + ":US", "USD")) != subject:
                continue
            if not hit and ceiling is None:
                continue
            if hit and (hit["action"] != rule.action or hit["operator"] != rule.operator
                        or Decimal(str(hit["threshold"])) != rule.threshold
                        or hit["source_clause"] != rule.source_clause.model_dump(mode="json")):
                raise ValueError("E1_PORTFOLIO_RISK_RULE_MISMATCH")
            upper_amount = row.get("known_spot_cny" if is_btc else "known_lower_bound_cny")
            boundary = Decimal(str(concentration["denominator_cny"])) * rule.threshold
            exact = Decimal(str(upper_amount)) if ceiling is not None and upper_amount is not None else None
            if not hit and (exact is None or not (exact < boundary or rule.operator == "gt" and exact == boundary)):
                continue
            key = _risk_key(portfolio_id, rule.rule_id, subject)
            facts[key] = {"source": "ACTUAL_BC", "rule": rule.model_dump(mode="json"), "subject": subject,
                          "hit": bool(hit), "known_lower_bound_pct": row.get("known_lower_bound_pct"),
                          "proved_upper_bound_pct": ceiling}
    review = allocation.get("strategic_review", {})
    if review.get("status") in {"HUMAN_REVIEW", "NO_RULE_TRIGGERED"} and review.get("policy_reference"):
        rule = rules[review["rule_id"]]
        key = _risk_key(portfolio_id, rule.rule_id, "STRATEGIC_ALLOCATION")
        facts[key] = {"source": "ACTUAL_BC", "rule": rule.model_dump(mode="json"), "subject": "STRATEGIC_ALLOCATION",
                      "hit": review["status"] == "HUMAN_REVIEW",
                      "triggered_classes": review["triggered_classes"]}
    return facts


def read_portfolio_sources(database, portfolio_id, request, contract=None):
    """Resolve trusted sources using only explicitly named stored U1 and active owner."""
    from app.asset_views import load_view
    from backend.services.allocation_service import _assembled_policy_assets, project_frozen_transition
    from backend.services.investment_policy.concentration_adapter import _read_etf_evidence
    from backend.services.investment_policy.store import clock, utc_now
    from backend.services.investment_policy.contracts import digest as policy_digest, parse_snapshot
    from backend.services.investment_policy.parser import PolicyError
    from backend.services.investment_policy.runtime import bind, current

    try:
        view = load_view(database, portfolio_id, request.snapshot_id, contract)
        pinned = current(portfolio_id)
        context = pinned.consumer("allocation")
    except (PolicyError, ValueError) as error:
        raise ValueError("E1_PORTFOLIO_SOURCE_UNAVAILABLE") from error
    if policy_digest(view) != request.snapshot_sha256:
        raise ValueError("E1_PORTFOLIO_SNAPSHOT_CHANGED")
    reference = context.get("policy_reference") or {}
    if (context.get("mode") != "POLICY_ACTIVE" or context.get("status") != "ACTIVE"
            or reference.get("bundle_sha256") != request.policy_bundle_sha256):
        raise ValueError("E1_PORTFOLIO_POLICY_CHANGED")
    release = context.get("release") or {}
    if release.get("synthetic") is not True and release.get("environment") != "ISOLATED_REHEARSAL":
        raise ValueError("E1_PORTFOLIO_ISOLATION_REQUIRED")
    policy = parse_snapshot(context["snapshot"])
    at = clock(utc_now())
    evidence = _read_etf_evidence(portfolio_id, at=at)
    with bind(pinned):
        try:
            with _assembled_policy_assets(portfolio_id, request.snapshot_id, frozen_view=view,
                                          evaluation_at=at, etf_inputs=evidence) as assembled:
                if assembled.context["policy_reference"] != reference:
                    raise ValueError("E1_PORTFOLIO_POLICY_CHANGED")
                concentration = assembled.allocation_concentration
                allocation = assembled.allocation_hierarchy
                qualification = {"status": "QUALIFIED", "issues": [],
                                 "source_expires_at": assembled.allocation_source["expires_at"]}
        except PolicyError as error:
            if str(error) not in {"POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED",
                                  "POLICY_BUCKET_CLASSIFICATION_UNVERIFIED"}:
                raise ValueError("E1_PORTFOLIO_SOURCE_CHANGED") from error
            concentration, allocation = {}, {}
            qualification = {"status": "UNKNOWN",
                             "issues": getattr(error, "qualification", {}).get("issues", [str(error)])}
        scenario = (project_frozen_transition(portfolio_id, view, request.scenario, session=pinned, at=at,
                                               freeze_evidence=True, etf_inputs=evidence)
                    if getattr(request, "scenario", None) else None)
        pinned.ensure_current()
    facts = collect_policy_risks(portfolio_id, policy, allocation, concentration)
    result = {"snapshot": view, "policy": {"reference": reference, "structured_bundle": context["snapshot"],
             "effective_at": context["effective_at"]}, "qualification": qualification, "risk_facts": facts,
            "actual_concentration": concentration, "actual_allocation": allocation, "scenario": scenario}
    validate_source_scope(result, request, portfolio_id)
    return result


def binding(source):
    """Ignore run-clock fields only. Official evidence, facts and results remain bound."""
    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items() if k not in {"evaluated_at", "evaluation_sha256"}}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value
    return digest(stable(source))



def validate_source_scope(source, request, portfolio_id):
    snapshot = source["snapshot"]
    reference = source["policy"]["reference"]
    if (snapshot["portfolio_id"] != portfolio_id or snapshot["snapshot_id"] != request.snapshot_id
            or digest(snapshot) != request.snapshot_sha256 or reference["scope"] != f"portfolio:{portfolio_id}"
            or reference["bundle_sha256"] != request.policy_bundle_sha256):
        raise ValueError("E1_PORTFOLIO_SOURCE_SCOPE_MISMATCH")


def clear_absent_risk(source, prior):
    """Absence is zero only for a complete and still comparable C inventory."""
    if not prior:
        return None
    fact = prior["actual_risk"]
    subject = fact["subject"]
    rule = fact["rule"]
    reference = source["policy"]["reference"]
    concentration = source["actual_concentration"]
    if (rule["metric"] != "issuer_weight" or reference["bundle_sha256"] != prior["policy_bundle_sha256"]
            or concentration.get("status") != "DIAGNOSTIC" or concentration.get("denominator_cny") is None
            or concentration.get("unknown_coverage") is not False or subject in concentration.get("issuers", {})):
        return None
    if not any(active_rule == rule for active_rule in source["policy"]["structured_bundle"]["rules"]):
        return None
    if not (Decimal(rule["threshold"]) > 0 or rule["operator"] == "gt"):
        return None
    return {**fact, "hit": False, "known_lower_bound_pct": "0", "proved_upper_bound_pct": "0"}
