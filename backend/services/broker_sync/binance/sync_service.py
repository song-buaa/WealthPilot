"""Spot balances and external fills, through the original fenced U0/B2 stores."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from .client import BinanceReadonlyClient, configuration
from backend.services.broker_sync.schema import Position
from backend.services.instruments.classification import (
    AssetClassificationEvidence, classify_instrument, broker_position_classification_fields,
)


def amount(value):
    if not isinstance(value, str):
        raise ValueError("BINANCE_RESPONSE_INVALID")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("BINANCE_RESPONSE_INVALID") from None
    if not result.is_finite() or result < 0 or result >= Decimal("1e12"):
        raise ValueError("BINANCE_RESPONSE_INVALID")
    return result


def instant(milliseconds):
    if type(milliseconds) is not int or milliseconds <= 0:
        raise ValueError("BINANCE_RESPONSE_INVALID")
    try:
        return datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
    except (ValueError, OSError, OverflowError):
        raise ValueError("BINANCE_RESPONSE_INVALID") from None


def trade_events(rows):
    events = []
    for row in rows:
        if (row.get("symbol") != "BTCUSDT" or type(row.get("id")) is not int or row["id"] < 0
                or type(row.get("isBuyer")) is not bool
                or not isinstance(row.get("commissionAsset"), str)
                or not re.fullmatch(r"[A-Z0-9]{2,20}", row["commissionAsset"])):
            raise ValueError("BINANCE_RESPONSE_INVALID")
        qty, price = amount(row.get("qty")), amount(row.get("price"))
        if not qty or not price:
            raise ValueError("BINANCE_RESPONSE_INVALID")
        quote = amount(row.get("quoteQty"))
        if abs(qty * price - quote) > Decimal("0.00000001"):
            raise ValueError("BINANCE_TRADE_VALUE_CONFLICT")
        fee = amount(row.get("commission"))
        if row["commissionAsset"] == "BTC" and row["isBuyer"] and fee >= qty:
            raise ValueError("BINANCE_RESPONSE_INVALID")
        stamp = instant(row.get("time")).isoformat()
        events.append(dict(kind="EXECUTION", external_id="BTCUSDT:" + str(row["id"]),
            instrument_key="listing:BTC:CRYPTO:USD:CRYPTO_SPOT", symbol="BTC:CRYPTO", currency="USD",
            sec_type="CRYPTO_SPOT", executed_at=stamp, time_precision="instant",
            source_time_raw=str(row["time"]), source_timezone="UTC", source_local_date=stamp[:10],
            source_date_semantics="execution", side="BUY" if row["isBuyer"] else "SELL",
            quantity=str(qty), price=str(price), multiplier="1", quote_quantity=str(quote),
            commission=str(fee), commission_currency=row["commissionAsset"], taxes=None,
            broker_realized_pnl=None, broker_cost_basis=None, fx=None,
            source_channel="binance_spot", quote_asset="USDT",
            valuation_convention="USDT_USD_PARITY_APPROVED", source_trade_id=row["id"]))
    return events


class BinanceSyncService:
    def __init__(self, config=None, *, client=None):
        self.config = config or configuration()
        self.account_id = self.config.account_id
        self.client = client or BinanceReadonlyClient(self.config)
        self.metadata = {}
        self.events = []

    def _balances(self):
        self.client.server_time()
        response = self.client.get("/api/v3/account", {"omitZeroBalances": "false"})
        received = datetime.now(timezone.utc)
        if (not isinstance(response, dict) or str(response.get("uid")) != self.account_id
                or response.get("accountType") != "SPOT" or "SPOT" not in response.get("permissions", [])):
            raise ValueError("SOURCE_ACCOUNT_UNPROVEN")
        rows = response.get("balances")
        if not isinstance(rows, list) or not rows:
            raise ValueError("INCOMPLETE_SOURCE")
        balances = {}
        for row in rows:
            if not isinstance(row, dict) or not re.fullmatch(r"[A-Z0-9]{2,20}", str(row.get("asset", ""))):
                raise ValueError("BINANCE_RESPONSE_INVALID")
            asset = row["asset"]
            if asset in balances:
                raise ValueError("DUPLICATE_SNAPSHOT_IDENTITY")
            free, locked = amount(row.get("free")), amount(row.get("locked"))
            if max(-free.as_tuple().exponent, -locked.as_tuple().exponent) > 8:
                raise ValueError("BINANCE_PRECISION_UNSUPPORTED")
            balances[asset] = (free, locked)
        if not {"BTC", "USDT"} <= balances.keys():
            raise ValueError("INCOMPLETE_SOURCE")
        self.metadata = dict(source="binance_spot_account", complete=True, empty_confirmed=False,
            coverage=["SPOT_ONLY"], positions_as_of=None, cash_as_of=None, price_as_of=None,
            account_response_at=received.isoformat(), account_update_time=response.get("updateTime"),
            source_completeness="SPOT_RESPONSE_ONLY", outside_scope=["FUNDING", "EARN", "MARGIN", "FUTURES"],
            issues=["BINANCE_SOURCE_EFFECTIVE_TIME_UNKNOWN", "USDT_USD_VALUATION_CONVENTION",
                    "BINANCE_NON_SPOT_WALLETS_NOT_COVERED", "OPENING_COST_AND_TRANSFERS_UNPROVEN"],
            time_evidence=dict(positions_as_of="BINANCE_ACCOUNT_UPDATE_NOT_BALANCE_EFFECTIVE_TIME",
                cash_as_of="BINANCE_ACCOUNT_UPDATE_NOT_BALANCE_EFFECTIVE_TIME"))
        self.metadata["retry_after_seconds"] = self.client.retry_after
        try:
            peg = self.client.get("/api/v3/ticker/price", {"symbol": "USDCUSDT"})
            if peg.get("symbol") != "USDCUSDT":
                raise ValueError("BINANCE_RESPONSE_INVALID")
            reference = amount(peg.get("price"))
            if not reference:
                raise ValueError("BINANCE_RESPONSE_INVALID")
            self.metadata["usdt_relative_reference"] = str(reference)
            if abs(reference - 1) > Decimal("0.01"):
                self.metadata["issues"].append("USDT_RELATIVE_STABLECOIN_DEVIATION")
        except ValueError as exc:
            self.metadata["retry_after_seconds"] = self.client.retry_after
            if str(exc) == "BINANCE_RATE_LIMITED":
                raise
            self.metadata["issues"].append("USDT_PARITY_REFERENCE_UNVERIFIED")
        unknown = [dict(asset=k, quantity=str(f + l), free=str(f), locked=str(l), valuation="UNKNOWN")
                   for k, (f, l) in balances.items() if k not in {"BTC", "USDT"} and f + l > 0]
        if unknown:
            self.metadata["unsupported_assets"] = unknown
            raise ValueError("BINANCE_UNSUPPORTED_ASSETS")
        return balances, received

    def _positions(self, balances, received):
        btc_free, btc_locked = balances.get("BTC", (Decimal(0), Decimal(0)))
        price = Decimal(0)
        price_time = None
        proof = None
        if btc_free + btc_locked:
            trades = self.client.get("/api/v3/aggTrades", {"symbol": "BTCUSDT", "limit": 1})
            if not isinstance(trades, list) or len(trades) != 1:
                raise ValueError("BINANCE_PRICE_UNAVAILABLE")
            proof = trades[0]
            price, price_time = amount(proof.get("p")), instant(proof.get("T"))
            if not price or not 0 <= (datetime.now(timezone.utc) - price_time).total_seconds() <= 300:
                raise ValueError("BINANCE_PRICE_UNAVAILABLE")
            self.metadata.update(price_as_of=price_time.isoformat(), price_time_basis="last_trade",
                                 price_pair="BTCUSDT", price_quote_asset="USDT")
        result = []
        for asset in ("BTC", "USDT"):
            free, locked = balances.get(asset, (Decimal(0), Decimal(0)))
            cash = asset == "USDT"
            unit_price = Decimal(1) if cash else price
            evidence = AssetClassificationEvidence(broker="binance",
                broker_security_type="STABLECOIN" if cash else "CRYPTO_SPOT",
                vehicle_type_hint="CASH" if cash else "CRYPTO",
                currency="USDT", listing_symbol=asset + ":CRYPTO", listing_market="CRYPTO")
            result.append(Position(broker="binance", account_id=self.account_id,
                symbol=asset + ":CRYPTO", raw_symbol=asset, name=asset + ("（美元等值交易现金）" if cash else " 现货"),
                **broker_position_classification_fields(classify_instrument(evidence), evidence=evidence),
                market="CRYPTO", quantity=free + locked, available_quantity=free,
                avg_cost=Decimal(1) if cash else None, cost_method="weighted_average",
                cost_basis=free + locked if cash else None,
                current_price=unit_price, market_value=(free + locked) * unit_price, currency="USDT",
                unrealized_pnl=Decimal(0) if cash else None, unrealized_pnl_pct=Decimal(0) if cash else None, snapshot_time=received, sync_source="api",
                raw_data=dict(asset=asset, free=str(free), locked=str(locked), quantity=str(free + locked),
                    account_response_at=received.isoformat(), price_quote_asset="USDT",
                    valuation_convention="USDT_USD_PARITY_APPROVED", cost_quality="PARITY_CARRYING_VALUE" if cash else "UNKNOWN",
                    price_evidence=proof if not cash else None)))
        return result

    def _history(self, start_id):
        rows, seen = [], {}
        cursor = start_id
        for _ in range(10):
            page = self.client.get("/api/v3/myTrades", {"symbol": "BTCUSDT", "fromId": cursor, "limit": 1000})
            if not isinstance(page, list) or len(page) > 1000:
                raise ValueError("BINANCE_RESPONSE_INVALID")
            for row in page:
                identifier = row.get("id") if isinstance(row, dict) else None
                if type(identifier) is not int or identifier < cursor:
                    raise ValueError("BINANCE_HISTORY_PAGE_INVALID")
                if identifier in seen and row != seen[identifier]:
                    raise ValueError("HISTORY_EVENT_CONFLICT")
                seen[identifier] = row
            rows.extend(page)
            if len(page) < 1000:
                return trade_events(list(seen.values()))
            cursor = max(row["id"] for row in page) + 1
        raise ValueError("BINANCE_HISTORY_LIMIT")

    def sync_and_persist(self, db, triggered_by="manual"):
        from services.broker_sync.repository import PositionSnapshotRepository
        from app.binance_schema import verify
        from backend.services.action.ownership import portfolio_scope
        from backend.services.portfolio_history.models import HistoryEvent
        from backend.services.portfolio_history.imports import import_batch

        verify(db.connection().connection.driver_connection)
        if portfolio_scope(db) != self.config.portfolio_id:
            raise ValueError("SOURCE_ACCOUNT_UNPROVEN")
        previous = db.query(HistoryEvent.external_id).filter_by(portfolio_id=self.config.portfolio_id,
            broker="binance", account_id=self.account_id, kind="EXECUTION").all()
        ids = [int(row[0].split(":")[1]) for row in previous if re.fullmatch(r"BTCUSDT:[0-9]+", row[0])]
        cursor = max(ids) + 1 if ids else 0
        db.rollback()

        def fetch():
            balances, received = self._balances()
            positions = self._positions(balances, received)
            self.events = self._history(cursor)
            self.metadata["history_cursor"] = max([cursor - 1] + [e["source_trade_id"] for e in self.events])
            self.metadata["history_coverage"] = "BTCUSDT_EXECUTIONS_ONLY_NO_TRANSFERS_OR_OPENING"
            return positions

        def reconcile(session, upsert, snapshots):
            report = upsert.upsert_from_snapshots(snapshots, broker="binance", account_id=self.account_id,
                sync_source="api", commit=False, complete_snapshot=True,
                run_id=snapshots[0].run_id)
            if report["errors"]:
                raise ValueError("SYNC_BUSINESS_UPSERT_FAILED")
            if self.events:
                start = min(e["executed_at"] for e in self.events)
                end = (datetime.fromisoformat(max(e["executed_at"] for e in self.events)) + timedelta(milliseconds=1)).isoformat()
                coverage = dict(coverage_start=start, coverage_end=end, source_timezone="UTC",
                    instrument_keys=["listing:BTC:CRYPTO:USD:CRYPTO_SPOT"],
                    evidence_ref="Binance Spot BTCUSDT executions; transfers and opening unproven")
                raw = json.dumps(self.events, sort_keys=True).encode()
                import_batch(session, self.config.portfolio_id, "binance", self.account_id,
                    "binance_spot", hashlib.sha256(raw).hexdigest(), self.events, coverage,
                    source_metadata=dict(account_id=self.account_id, source_timezone="UTC",
                        issues=["OPENING_AND_TRANSFERS_UNPROVEN"]), commit=False)

        def metadata():
            return {**self.metadata, "retry_after_seconds": self.client.retry_after}

        return PositionSnapshotRepository(db).synchronize("binance", self.account_id, fetch,
            triggered_by=triggered_by, metadata=metadata, reconcile=reconcile, retries=0)
