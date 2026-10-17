"""Allowlisted details from existing services; one enforced read-only snapshot."""
from datetime import date, datetime, timezone
import hashlib
import logging

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
from pocs.poc02_wealth_mcp.reader import readonly_snapshot, service_sessions, ReadError, SourceLogProbe, observe_cash

MANUAL_FIELDS = ("id", "kind", "name", "item_type", "category", "source_type", "sync_mode",
                 "current_value", "currency", "original_value", "fx_rate_to_cny", "fx_rate_date",
                 "included_in_net_worth", "already_investment_accounted", "effective_included_in_net_worth",
                 "value_as_of", "last_verified_at", "updated_at", "freshness")
POSITION_FIELDS = ("id", "name", "ticker", "platform", "asset_class", "currency", "quantity",
                   "cost_price", "current_price", "market_value_cny", "original_currency", "original_value",
                   "fx_rate_to_cny", "fx_rate_date", "profit_loss_value", "profit_loss_rate", "segment",
                   "vehicle_type", "economic_asset_class", "economic_asset_subclass")


def account_ref(portfolio_id, row):
    owner = [portfolio_id, row.broker, row.broker_account_id, row.platform]
    return "account_" + hashlib.sha256(repr(owner).encode()).hexdigest()[:20]


def source_quality(connection, row):
    issues = []
    updated = None
    if row.broker and row.broker_account_id and row.sync_source and "position_snapshot_runs" in inspect(connection).get_table_names():
        runs = connection.execute(text("SELECT status, finished_at FROM position_snapshot_runs WHERE portfolio_id=:p AND broker=:b AND account_id=:a AND sync_source=:s ORDER BY started_at DESC"),
                                  {"p": row.portfolio_id, "b": row.broker, "a": row.broker_account_id, "s": row.sync_source}).mappings().all()
        if runs and runs[0]["status"] == "failed":
            issues.append("LATEST_POSITION_SYNC_FAILED")
        success = next((r for r in runs if r["status"] == "success" and r["finished_at"]), None)
        if success:
            updated = datetime.fromisoformat(str(success["finished_at"])).date().isoformat()
    if updated is None:
        issues.append("VALUATION_DATE_UNKNOWN")
    elif (date.today() - date.fromisoformat(updated)).days > 1:
        issues.append("STALE_COMPONENTS")
    return updated, issues


def _read_details(path, portfolio_id, operation, arguments, *, database_contract=None, synthetic=False):
    from app.models import Portfolio, Position
    from app.utils import position_aggregator
    from backend.services import wealth_service, portfolio_service
    with readonly_snapshot(path, database_contract) as connection, service_sessions(connection):
        with Session(bind=connection, autoflush=False, join_transaction_mode="rollback_only") as session:
            if session.get(Portfolio, portfolio_id) is None:
                raise ReadError("PORTFOLIO_NOT_FOUND")
            if database_contract:
                from app.shared_sqlite import load_contract
                if load_contract(database_contract)["portfolio_id"] != portfolio_id:
                    raise ReadError("PORTFOLIO_NOT_FOUND")
            issues, accounts, summary = [], [], None
            if operation == "get_manual_wealth_items":
                items = wealth_service.list_items(portfolio_id, arguments.get("kind"))
                if arguments.get("item_id") is not None:
                    items = [i for i in items if i["id"] == arguments["item_id"]]
                    if not items:
                        raise ReadError("ITEM_NOT_FOUND")
                items = [{k: i[k] for k in MANUAL_FIELDS} for i in items]
                for item in items:
                    if not item["value_as_of"]:
                        issues.append("VALUATION_DATE_UNKNOWN")
                    elif (date.today()-date.fromisoformat(item["value_as_of"])).days > 30:
                        issues.append("STALE_COMPONENTS")
                scope = "Stored manual WealthItems, including excluded/linked items. current_value is CNY; original_value uses currency. Not an additional net-worth total."
            elif operation == "get_investment_details":
                rows = session.query(Position).filter_by(portfolio_id=portfolio_id, segment="投资").all()
                serialized = portfolio_service.get_positions(portfolio_id, "投资")["items"]
                by_id = {i["id"]: i for i in serialized}
                aggregate, _ = position_aggregator.aggregate_investment_positions(portfolio_id)
                weights = {i.symbol or i.ticker: i.weight for i in aggregate}
                items, owner_map = [], {}
                for row in rows:
                    ref = account_ref(portfolio_id, row)
                    updated, row_issues = source_quality(connection, row)
                    owner_map.setdefault(ref, {"account_ref": ref, "broker": row.broker, "platform": row.platform,
                                               "source": row.sync_source, "identity": "opaque_stored_owner_no_raw_account_id"})
                    if arguments.get("account_ref") and ref != arguments["account_ref"]:
                        continue
                    if arguments.get("symbol") and arguments["symbol"] not in (row.symbol, row.ticker):
                        continue
                    item = {k: by_id[row.id].get(k) for k in POSITION_FIELDS}
                    item.update(symbol=row.symbol, account_ref=ref, source=row.sync_source,
                                data_updated_date=updated, issues=row_issues,
                                symbol_weight_of_investments=weights.get(row.symbol or row.ticker))
                    items.append(item); issues.extend(row_issues)
                accounts = list(owner_map.values())
                if arguments.get("account_ref"):
                    accounts = [a for a in accounts if a["account_ref"] == arguments["account_ref"]]
                    if not accounts:
                        raise ReadError("ACCOUNT_NOT_FOUND")
                # Existing Portfolio summary includes its existing Tiger cash behavior.
                summary = portfolio_service.get_summary(portfolio_id)
                if any(i.get("id") == -1 for i in serialized):
                    issues.append("LIVE_CASH_VALUATION_DATE_UNKNOWN")
                scope = "Stored investment holdings. CNY fields are yuan; prices/original_value use their stated currency. Weight is the existing cross-account symbol weight (0..1), not a new account balance. Accounts are stored ownership groups, not a live broker account inventory."
            else:
                raise ReadError("UNKNOWN_OPERATION")
            return dict(schema_version="wealthpilot.details.v1", synthetic=synthetic,
                        queried_at=datetime.now(timezone.utc).isoformat(), status="partial" if issues else "ok",
                        issues=sorted(set(issues)), amount_unit="major", scope=scope,
                        items=items, accounts=accounts, portfolio_summary=summary)


def read_details(path, portfolio_id, operation, arguments, *, database_contract=None, synthetic=False, cash_configured=False):
    root = logging.getLogger()
    probe, handlers = SourceLogProbe(), root.handlers[:]
    root.handlers = [probe]
    try:
        with observe_cash() as cash:
            result = _read_details(path, portfolio_id, operation, arguments, database_contract=database_contract, synthetic=synthetic)
        result["sources"] = {}
        if operation == "get_investment_details":
            result["sources"]["tiger_cash"] = "not_configured"
            if cash_configured:
                result["issues"].append("LIVE_CASH_VALUATION_DATE_UNKNOWN")
                result["sources"]["tiger_cash"] = "queried" if cash["has_details"] else "queried_zero_or_missing"
                if not cash["has_details"]:
                    result["issues"].append("TIGER_CASH_ZERO_OR_SOURCE_EMPTY")
            if probe.cash_failed:
                result["sources"]["tiger_cash"] = "failed"
                result["issues"].append("TIGER_CASH_SOURCE_FAILED")
                result["portfolio_summary"] = None
            if probe.fx_degraded:
                result["issues"].append("LIVE_CASH_FX_DEGRADED")
        result["issues"] = sorted(set(result["issues"]))
        result["status"] = "partial" if result["issues"] else "ok"
        return result
    finally:
        root.handlers = handlers
