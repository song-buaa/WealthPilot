"""Safe stored-sync projections shared by Web and read-only workers.

No HTTP framework, broker client or runtime configuration dependency.
The optional scoped SELECT projection consumes the original Run receipts only.
The Web API re-exports these names for existing import callers.
"""

SAFE_SYNC_CODES = {"SYNC_LEASE_EXPIRED","SYNC_ACCOUNT_UNCONFIGURED","SYNC_SOURCE_FAILED",
    "EMPTY_SOURCE_REQUIRES_REVIEW","INCOMPLETE_SOURCE","FX_RATE_UNAVAILABLE",
    "DUPLICATE_SNAPSHOT_IDENTITY","MIXED_SNAPSHOT_SCOPE","SYNC_CLAIM_EXPIRED_OR_REPLACED",
    "ACCOUNT_PORTFOLIO_AMBIGUOUS","SYNC_RESERVATION_FAILED",
    "SOURCE_ACCOUNT_UNPROVEN", "BINANCE_ACCOUNT_RESPONSE_INVALID", "BINANCE_UID_MISSING",
                    "BINANCE_UID_MISMATCH", "BINANCE_ACCOUNT_TYPE_NOT_SPOT","CASH_IDENTITY_COLLISION_REQUIRES_REVIEW",
    "NATIVE_BATCH_INCOMPLETE","NATIVE_BATCH_INVALID","NATIVE_ACCOUNT_UNPROVEN",
    "NATIVE_RECONCILIATION_FAILED","NATIVE_BATCH_STALE","NATIVE_LEGACY_REVIEW_REQUIRED", 'BINANCE_UNSUPPORTED_ASSETS', 'BINANCE_PRICE_UNAVAILABLE', 'BINANCE_CONNECTION_UNAVAILABLE', 'BINANCE_PERMISSION_DENIED', 'BINANCE_SIGNATURE_INVALID', 'BINANCE_CLOCK_SKEW', 'BINANCE_RATE_LIMITED', 'BINANCE_SOURCE_UNAVAILABLE', 'BINANCE_RESPONSE_INVALID', 'BINANCE_PRECISION_UNSUPPORTED', 'BINANCE_HISTORY_LIMIT', 'BINANCE_TRADE_VALUE_CONFLICT', 'BINANCE_HISTORY_PAGE_INVALID', 'BINANCE_SCHEMA_REQUIRED'}


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
        "time_evidence","price_time_basis", "account_response_at", "account_update_time", "outside_scope",
        "unsupported_assets", "retry_after_seconds", "price_pair", "price_quote_asset", "history_coverage", "usdt_relative_reference", "unvalued_assets", "asset_price_times",
        "account_permission_groups", "permission_semantics")}
    # Pre-D1 IBKR cash_as_of was a local download clock. Reusing that last-good
    # must not turn it into authoritative source time in a NEW analysis view.
    # Preserve stored Run/history; only the current quality projection degrades.
    if meta.get("source") == "ibkr_account_download" and not (meta.get("time_evidence") or {}).get("cash_as_of"):
        result.update(cash_as_of=None, time_evidence={"cash_as_of":"LEGACY_IBKR_CASH_SOURCE_TIME_UNVERIFIED"},
            quality="partial", issues=sorted(set(meta.get("issues") or []) | {"LEGACY_IBKR_CASH_SOURCE_TIME_UNVERIFIED"}))
    return result


def unvalued_source_assets(connection, portfolio_id):
    """Project latest successful original source receipts, never create holdings.

    The caller supplies its existing RO connection. No account identifiers,
    credentials, new store or second valuation calculation leave this helper.
    """
    from sqlalchemy import text
    if not connection.execute(text("SELECT 1 FROM sqlite_master WHERE type='table' AND name='position_snapshot_runs'")).scalar():
        return []
    rows = connection.execute(text(
        "SELECT account_id, context_json FROM position_snapshot_runs WHERE portfolio_id=:pid "
        "AND broker='binance' AND sync_source='api' AND status='success' ORDER BY id DESC"),
        {"pid": portfolio_id}).mappings()
    import json
    seen, result = set(), []
    for row in rows:
        if row["account_id"] in seen:
            continue
        seen.add(row["account_id"])
        from backend.services.action.ownership import stored_account_ref
        from types import SimpleNamespace
        ref = stored_account_ref(portfolio_id, SimpleNamespace(broker="binance",
            broker_account_id=row["account_id"], platform="Binance Spot"))
        for item in json.loads(row["context_json"] or "{}").get("unvalued_assets", []):
            result.append({"broker": "binance", "account_ref": ref, **{k:item.get(k) for k in (
                "asset", "symbol", "quantity", "free", "locked", "valuation", "market_value_cny",
                "reason_code", "last_good_retained")}})
    return result
