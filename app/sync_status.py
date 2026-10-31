"""Safe stored-sync projections shared by Web and read-only workers.

No HTTP framework, database, broker client or runtime configuration dependency.
The Web API re-exports these names for existing import callers.
"""

SAFE_SYNC_CODES = {"SYNC_LEASE_EXPIRED","SYNC_ACCOUNT_UNCONFIGURED","SYNC_SOURCE_FAILED",
    "EMPTY_SOURCE_REQUIRES_REVIEW","INCOMPLETE_SOURCE","FX_RATE_UNAVAILABLE",
    "DUPLICATE_SNAPSHOT_IDENTITY","MIXED_SNAPSHOT_SCOPE","SYNC_CLAIM_EXPIRED_OR_REPLACED",
    "ACCOUNT_PORTFOLIO_AMBIGUOUS","SYNC_RESERVATION_FAILED",
    "SOURCE_ACCOUNT_UNPROVEN","CASH_IDENTITY_COLLISION_REQUIRES_REVIEW",
    "NATIVE_BATCH_INCOMPLETE","NATIVE_BATCH_INVALID","NATIVE_ACCOUNT_UNPROVEN",
    "NATIVE_RECONCILIATION_FAILED","NATIVE_BATCH_STALE","NATIVE_LEGACY_REVIEW_REQUIRED"}


def safe_sync_reason(run):
    """Shared safe failure projection; never forward raw SDK/connection text."""
    if run is None:
        return None
    if not run.error_message:
        return "SYNC_SOURCE_FAILED" if run.status == "failed" else None
    return run.error_message if run.error_message in SAFE_SYNC_CODES else "SYNC_SOURCE_FAILED"


def public_sync_attempt(run):
    from datetime import timezone
    stamp = run.started_at if run else None
    if stamp is not None and stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return {"last_attempt_id":run.id if run else None,
            "last_attempt_time":stamp.isoformat() if stamp else None,
            "last_attempt_status":run.status if run else "never",
            "last_attempt_reason_code":safe_sync_reason(run)}


def _public_quality(run):
    if run is None:
        return {"quality":"unavailable","issues":["NO_SUCCESSFUL_SYNC"]}
    import json
    meta = json.loads(run.context_json or "{}") or {"quality":"partial","issues":["SOURCE_EVIDENCE_UNKNOWN"]}
    result = {key:meta.get(key) for key in ("quality","issues","source","complete","coverage","markets","empty_confirmed","fetched_at",
        "positions_as_of","cash_as_of","price_as_of","currencies","row_count","fx",
        "queried_at","native_batch_id","account_total_assets_cny","cash_balance_cny","security_count",
        "query_action_at","source_completeness","counter_status","publication_mode","reconciliation",
        "time_evidence","price_time_basis")}
    # Pre-D1 IBKR cash_as_of was a local download clock. Reusing that last-good
    # must not turn it into authoritative source time in a NEW analysis view.
    # Preserve stored Run/history; only the current quality projection degrades.
    if meta.get("source") == "ibkr_account_download" and not (meta.get("time_evidence") or {}).get("cash_as_of"):
        result.update(cash_as_of=None, time_evidence={"cash_as_of":"LEGACY_IBKR_CASH_SOURCE_TIME_UNVERIFIED"},
            quality="partial", issues=sorted(set(meta.get("issues") or []) | {"LEGACY_IBKR_CASH_SOURCE_TIME_UNVERIFIED"}))
    return result
