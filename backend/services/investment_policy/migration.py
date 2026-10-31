"""Pure migration rehearsal; never reads or writes legacy runtime configuration."""
from copy import deepcopy


# Paths describe legacy schema only, not effective personal values. No defaults.
MIGRATIONS = (
    ("D01", "single_asset_limits", "REPLACE_SEMANTICS", "issuer.warning.default / issuer-specific warning / issuer.review"),
    ("D02", "rebalancing_rules", "REPLACE_SEMANTICS", "allocation.deviation; manual review, no forced trade"),
    ("D03", "portfolio_circuit_breaker", "RETIRE_FROM_NEW_POLICY", "no portfolio drawdown circuit breaker"),
    ("D04", "single_asset_limits.core_holding_floor_pct", "RETIRE_FROM_NEW_POLICY", "no permanent holding floor"),
    ("D05", None, "QUALITATIVE_ONLY", "policy 7.1 event / counterevidence review; no unconditional averaging down"),
    ("D06", "liquidity_limits", "REPLACE_SEMANTICS", "qualified investable denominator; cash within stable pool; debt cash separately"),
    ("D07", "leverage_limits", "REPLACE_SEMANTICS", "explicit prohibitions and independent product admission; no numerical leverage tiers"),
    ("D08", "stop_loss_rules", "REPLACE_SEMANTICS", "fundamental review, no cost-based automatic stop"),
    ("D09", "position_sizing", "RETIRE_FROM_NEW_POLICY", "no universal add/batch/interval numbers"),
    ("D09", "cooldown_rules", "RETIRE_FROM_NEW_POLICY", "no forced numerical cooling-off window"),
    ("ALLOCATION", "asset_allocation_ranges", "REPLACE_SEMANTICS", "source-derived neutral and conditional budgets"),
)


def migration_preview(legacy_config, *, bundle_reference):
    """Given explicit isolated input, inventory differences without merging rules."""
    rows = []
    for decision, path, action, target in MIGRATIONS:
        value = legacy_config
        for part in (path or "").split("."):
            value = value.get(part) if isinstance(value, dict) else None
        rows.append(dict(decision=decision, legacy_path=path, legacy_value=deepcopy(value),
                         actual_runtime_status="UNKNOWN", action=action, new_semantics=target))
    return dict(status="REHEARSAL_ONLY_NOT_APPLIED", bundle_reference=deepcopy(bundle_reference), rows=rows,
        production_private_config="UNKNOWN", independent_execution_safety="PRESERVE_ALL_EXISTING_GATES",
        production_activation_blockers=["AUTHORIZED_PRIVATE_RUNTIME_SNAPSHOT", "CONSISTENT_BACKUP_AND_REHEARSAL",
            "ALL_CONSUMERS_SAME_APPROVED_BUNDLE", "EXPLICIT_RELEASE_AND_ACTIVATION_AUTHORIZATION"],
        writes_performed=False)


def check_consumer_versions(contexts):
    """P2 integration probe; cannot make partially wired old/new consumers valid."""
    required = {"web", "peer", "mcp", "allocation", "discipline"}
    if set(contexts) != required:
        return {"status": "UNAVAILABLE", "reason": "MISSING_CONSUMER_RECEIPT"}
    identities = set()
    for value in contexts.values():
        ref = value.get("policy_reference")
        if value.get("status") != "ACTIVE" or not ref:
            return {"status": "UNAVAILABLE", "reason": "CONSUMER_NOT_USING_ACTIVE_POLICY"}
        keys = ("scope", "bundle_id", "bundle_version", "bundle_sha256", "structured_sha256")
        if any(not ref.get(k) for k in keys):
            return {"status": "UNAVAILABLE", "reason": "INCOMPLETE_POLICY_REFERENCE"}
        identities.add(tuple(ref[k] for k in keys))
    if len(identities) != 1:
        return {"status": "UNAVAILABLE", "reason": "MIXED_POLICY_VERSIONS"}
    return {"status": "CONSISTENT_REFERENCES_ONLY", "execution_authorized": False}
