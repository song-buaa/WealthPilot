"""Read original History and Trade/Sync, keeping plan references separate from proof."""
from datetime import timezone

from .snapshot_delta import decimal, instant, symbol_key, ZERO


def _db_instant(value):
    # Original OrderRecord SQL datetimes use naive UTC in SQLite.
    if hasattr(value, "isoformat"):
        value = value.replace(tzinfo=timezone.utc).isoformat() if value.tzinfo is None else value.isoformat()
    return instant(value)


def read_execution_evidence(database, portfolio_id, start, end, orders, contract=None):
    from sqlalchemy import inspect
    from sqlalchemy.orm import Session

    from app.fill_sync import decode
    from app.models import Position
    from app.trade_status import query as trade_status
    from backend.services.action.models import OrderRecord
    from backend.services.action.ownership import stored_account_ref
    from backend.services.portfolio_history.query import history_account_ref, query as history
    from pocs.poc02_wealth_mcp.reader import readonly_snapshot

    result = {"history": [], "orders": {}, "corporate_accounts": [], "issues": []}
    if end <= start:
        return {**result, "issues": ["EXECUTION_WINDOW_EMPTY"]}
    with readonly_snapshot(database, contract) as connection, Session(
            bind=connection, autoflush=False, join_transaction_mode="rollback_only") as db:
        tables = set(inspect(connection).get_table_names())
        if "portfolios" not in tables:
            return {**result, "issues": ["EXECUTION_HISTORY_UNAVAILABLE"]}
        if {"portfolio_history_events", "portfolio_history_imports"} <= tables:
            raw = history(db, portfolio_id, start=start.isoformat(), end=end.isoformat(),
                          limit=200, synthetic=True, at=end)
            result["issues"] = raw["issues"]
            if raw["next_offset"] is not None:
                result["issues"] = sorted(set(result["issues"]) | {"EXECUTION_HISTORY_WINDOW_TRUNCATED"})
            else:
                result["history"] = raw["items"]
            affected = {row["account_ref"] for row in raw["coverage"]
                        if "CORPORATE_ACTIONS_UNSUPPORTED" in row.get("issues", [])
                        and (instant(row.get("coverage_end")) or end) >= start
                        and (instant(row.get("coverage_start")) or start) <= end}
        else:
            result["issues"] = ["EXECUTION_HISTORY_UNAVAILABLE"]
            affected = set()

        owner_positions = db.query(Position).filter_by(portfolio_id=portfolio_id).all() if "positions" in tables else []
        refs = {}
        for row in owner_positions:
            if not row.broker or not row.broker_account_id:
                continue
            broker = "ibkr" if row.broker == "snowball" else row.broker
            owner = history_account_ref(portfolio_id, broker, row.broker_account_id)
            refs.setdefault(owner, set()).add(stored_account_ref(portfolio_id, row))
        result["corporate_accounts"] = sorted({ref for owner in affected for ref in refs.get(owner, ())})
        if not orders or "order_records" not in tables:
            return result
        rows = db.query(OrderRecord).filter(OrderRecord.portfolio_id == portfolio_id,
                                           OrderRecord.id.in_(set(orders))).all()
        for row in rows:
            if not row.broker_account_id or not row.broker_order_id:
                continue
            owner = history_account_ref(portfolio_id, row.broker_name, row.broker_account_id)
            scope = refs.get(owner, set())
            # Same broker and account can have multiple platform groups. An
            # ambiguous alias cannot bind execution to a checkpoint account.
            if len(scope) != 1:
                continue
            observed = _db_instant(row.last_synced_at)
            status = row.status if observed and start <= observed <= end else None
            sync = None
            disputed = bool(decode(row.fill_sync_json).get("evidence_conflict"))
            if {"position_snapshot_runs", "execution_batches", "execution_batch_legs"} <= tables:
                projection = trade_status(db, portfolio_id, order_id=row.id, limit=1, synthetic=True)
                if projection["items"]:
                    current = projection["items"][0]
                    # Sync is assessed separately and cannot imply a fill.
                    sync = current["asset_sync"]["status"]
                    disputed |= "EXECUTION_EVIDENCE_DISPUTED" in current["asset_sync"]["issues"]
            result["orders"][row.id] = {
                "account_ref": next(iter(scope)), "history_account_ref": owner,
                "broker": row.broker_name, "symbol": row.symbol, "side": row.side,
                "status": status, "filled_qty": row.filled_quantity if status is not None else None,
                "observed_at": observed.isoformat() if status is not None else None, "sync_status": sync, "disputed": disputed,
            }
    return result


def execution_for_trade(links, evidence, action_id, key, side, amount_native, start, end):
    order_id = links.get(action_id)
    if not order_id:
        return {"status": "EVIDENCE_PENDING", "issue": "PLAN_ORDER_REFERENCE_REQUIRED"}
    order = evidence["orders"].get(order_id)
    account, symbol, currency = key
    if (not order or order["account_ref"] != account or order["side"] != side
            or symbol_key(order["symbol"], currency) != symbol):
        return {"status": "COVERAGE_UNKNOWN", "issue": "ORDER_OWNER_LISTING_OR_SIDE_UNVERIFIED"}
    confirmed = []
    seen = set()
    for row in evidence["history"]:
        if row["kind"] != "EXECUTION" or row.get("local_order_id") != order_id:
            continue
        executed_at = instant(row.get("executed_at"))
        qty, price, multiplier = (decimal(row.get(field)) for field in ("quantity", "price", "multiplier"))
        if (row.get("account_ref") != order["history_account_ref"] or row.get("broker") != order["broker"]
                or row.get("currency") != currency or symbol_key(row.get("symbol"), currency) != symbol
                or row.get("side") != side or row.get("time_precision") != "instant"
                or not executed_at or not start <= executed_at < end or not qty or not price or not multiplier
                or not row.get("evidence_sources") or row["external_id"] in seen):
            # Any contradictory relevant fill prevents exact plan proof.
            return {"status": "COVERAGE_UNKNOWN", "issue": "LINKED_EXECUTION_EVIDENCE_CONFLICT"}
        seen.add(row["external_id"])
        confirmed.append({"execution_ref": row["external_id"], "instrument_key": row["instrument_key"],
                          "executed_at": executed_at.isoformat(), "quantity": qty, "gross_native": qty*price*multiplier,
                          "source": row["source"], "evidence_sources": row["evidence_sources"]})
    observed_at = instant(order["observed_at"])
    latest_fill = max((instant(row["executed_at"]) for row in confirmed), default=None)
    confirmed_quantity = sum((row["quantity"] for row in confirmed), ZERO)
    contradictory_terminal = confirmed and order["status"] in {"cancelled", "rejected"} and order["filled_qty"] == 0
    if order.get("disputed") or contradictory_terminal or (latest_fill and observed_at and observed_at >= latest_fill
            and order["filled_qty"] is not None and decimal(order["filled_qty"]) != confirmed_quantity):
        return {"status": "COVERAGE_UNKNOWN", "issue": "LINKED_EXECUTION_OWNER_CONFLICT",
                "linked_order_ref": order_id}
    gross = sum((row["gross_native"] for row in confirmed), ZERO)
    if confirmed and gross == amount_native:
        status = "VERIFIED_EXECUTED"
    elif confirmed:
        status = "PARTIALLY_OBSERVED"
    elif (order["status"] in {"cancelled", "rejected"} and order["filled_qty"] == 0
          and (instant(order["observed_at"]) or end) >= start
          and "EXECUTION_HISTORY_WINDOW_TRUNCATED" not in evidence["issues"]):
        status = "NOT_EXECUTED"
    elif order["filled_qty"] and (instant(order["observed_at"]) or end) >= start:
        status = "PARTIALLY_OBSERVED"
    else:
        status = "EVIDENCE_PENDING"
    return {"status": status, "linked_order_ref": order_id, "stored_order_status": order["status"],
            "sync_status_at_readback": order["sync_status"], "exec_gross_native": gross if confirmed else None,
            "planned_gross_native": amount_native, "currency": currency, "executions": confirmed,
            "issue": None if status == "VERIFIED_EXECUTED" else "EXACT_PLAN_EXECUTION_NOT_PROVED"}
