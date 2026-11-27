"""Spot balances and external fills, through the original fenced U0/B2 stores."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from .client import BinanceReadonlyClient, configuration
from backend.services.broker_sync.schema import Position, cash_position
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
        if not isinstance(response, dict):
            raise ValueError("BINANCE_ACCOUNT_RESPONSE_INVALID")
        if response.get("uid") is None:
            raise ValueError("BINANCE_UID_MISSING")
        if str(response["uid"]) != self.account_id:
            raise ValueError("BINANCE_UID_MISMATCH")
        if response.get("accountType") != "SPOT":
            raise ValueError("BINANCE_ACCOUNT_TYPE_NOT_SPOT")
        # Account trading groups are not the separately verified API Key scopes.
        groups = response.get("permissions")
        groups = [v for v in groups if isinstance(v, str) and
                  re.fullmatch(r"(?:SPOT|MARGIN|LEVERAGED|TRD_GRP_[0-9]{1,9})", v)] if isinstance(groups, list) else []
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
            if asset in {"BTC", "USDT", "USD"} and max(-free.as_tuple().exponent, -locked.as_tuple().exponent) > 8:
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
        self.metadata.update(account_permission_groups=groups,
            permission_semantics="ACCOUNT_TRADING_GROUPS_NOT_API_KEY_SCOPES",
            balance_inventory=[dict(asset=a, free=str(f), locked=str(l), quantity=str(f+l))
                               for a, (f, l) in sorted(balances.items())],
            unvalued_assets=[], asset_price_times={})
        for asset in sorted(getattr(self, "previous_assets", set()) - balances.keys()):
            self._unvalued(asset, None, None, "PREVIOUS_BALANCE_MISSING")
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
        return balances, received

    def _unvalued(self, asset, free, locked, reason):
        self.metadata["unvalued_assets"].append(dict(asset=asset, symbol="CASH-USD:US" if asset == "USD" else asset + ":CRYPTO",
            free=str(free) if free is not None else None,
            locked=str(locked) if locked is not None else None,
            quantity=str(free + locked) if free is not None else None,
            valuation="UNKNOWN", market_value=None, market_value_cny=None, reason_code=reason,
            last_good_retained=asset in getattr(self, "valued_assets", set())))
        self.metadata.update(complete=False, publication_mode="observed_rows")
        self.metadata["issues"].append("BINANCE_UNVALUED_ASSETS")

    def _price(self, asset):
        trades = self.client.get("/api/v3/aggTrades", {"symbol": asset + "USDT", "limit": 1})
        if not isinstance(trades, list) or len(trades) != 1 or not isinstance(trades[0], dict):
            raise ValueError("BINANCE_PRICE_UNAVAILABLE")
        proof = trades[0]
        price, stamp = amount(proof.get("p")), instant(proof.get("T"))
        if not price or not 0 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 300:
            raise ValueError("BINANCE_PRICE_UNAVAILABLE")
        self.metadata["asset_price_times"][asset + ":CRYPTO"] = dict(
            price_as_of=stamp.isoformat(), price_time_basis="last_trade", pair=asset + "USDT")
        return price, proof

    def _positions(self, balances, received):
        result = []
        extra_quotes = 0
        assets = ["BTC", "USDT"] + sorted(a for a in balances if a not in {"BTC", "USDT"})
        for asset in assets:
            free, locked = balances[asset]
            total = free + locked
            if asset == "USD":
                cash = cash_position("binance", self.account_id, "USD", total, received)
                raw = dict(cash.raw_data, asset="USD", free=str(free), locked=str(locked), quantity=str(total),
                    account_response_at=received.isoformat(), valuation_convention="NATIVE_CURRENCY")
                result.append(cash.model_copy(update={"available_quantity": free, "raw_data": raw}))
                continue
            if asset not in {"BTC", "USDT"} and not total:
                continue
            cash = asset == "USDT"
            price, proof = Decimal(1) if cash else Decimal(0), None
            if not cash and total:
                if asset != "BTC":
                    if max(-free.as_tuple().exponent, -locked.as_tuple().exponent) > 8:
                        self._unvalued(asset, free, locked, "PRECISION_UNSUPPORTED")
                        continue
                    if extra_quotes >= 8:
                        self._unvalued(asset, free, locked, "QUOTE_BUDGET_EXHAUSTED")
                        continue
                    extra_quotes += 1
                try:
                    price, proof = self._price(asset)
                except ValueError as exc:
                    if asset == "BTC" or str(exc) == "BINANCE_RATE_LIMITED":
                        raise
                    self._unvalued(asset, free, locked, "QUOTE_UNAVAILABLE")
                    continue
            evidence = AssetClassificationEvidence(broker="binance",
                broker_security_type="STABLECOIN" if cash else "CRYPTO_SPOT" if asset == "BTC" else "SPOT_ASSET",
                vehicle_type_hint="CASH" if cash else "CRYPTO" if asset == "BTC" else "UNKNOWN", currency="USDT",
                listing_symbol=asset + ":CRYPTO", listing_market="CRYPTO")
            classification = classify_instrument(evidence)
            if classification.economic_asset_class.value == "UNKNOWN":
                self.metadata["issues"].append("BINANCE_ASSET_CLASSIFICATION_UNKNOWN")
            result.append(Position(broker="binance", account_id=self.account_id,
                symbol=asset + ":CRYPTO", raw_symbol=asset,
                name=asset + ("（美元等值交易现金）" if cash else " 现货"),
                **broker_position_classification_fields(classification, evidence=evidence),
                market="CRYPTO", quantity=total, available_quantity=free,
                avg_cost=Decimal(1) if cash else None, cost_method="weighted_average",
                cost_basis=total if cash else None, current_price=price, market_value=total * price,
                currency="USDT", unrealized_pnl=Decimal(0) if cash else None,
                unrealized_pnl_pct=Decimal(0) if cash else None, snapshot_time=received, sync_source="api",
                raw_data=dict(asset=asset, free=str(free), locked=str(locked), quantity=str(total),
                    account_response_at=received.isoformat(), price_quote_asset="USDT",
                    valuation_convention="USDT_USD_PARITY_APPROVED",
                    cost_quality="PARITY_CARRYING_VALUE" if cash else "UNKNOWN", price_evidence=proof)))
        times = self.metadata["asset_price_times"].values()
        if times:
            self.metadata.update(price_as_of=min(t["price_as_of"] for t in times), price_time_basis="last_trade")
        if "BTC:CRYPTO" in self.metadata["asset_price_times"]:
            self.metadata.update(price_pair="BTCUSDT", price_quote_asset="USDT")
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
        portfolio_id = portfolio_scope(db)
        from app.models import Position as StoredPosition
        self.previous_assets = {p.currency if p.broker_security_type == "CASH" else p.symbol.split(":")[0]
            for p in db.query(StoredPosition).filter_by(portfolio_id=portfolio_id, broker="binance",
                broker_account_id=self.account_id, sync_source="api").all()}
        self.valued_assets = set(self.previous_assets)
        from services.broker_sync.models import PositionSnapshotRun
        previous_run = db.query(PositionSnapshotRun).filter_by(portfolio_id=portfolio_id, broker="binance",
            account_id=self.account_id, status="success", sync_source="api").order_by(PositionSnapshotRun.id.desc()).first()
        if previous_run:
            self.previous_assets.update(a["asset"] for a in json.loads(previous_run.context_json or "{}").get("unvalued_assets", []))
        previous = db.query(HistoryEvent.external_id).filter_by(portfolio_id=portfolio_id,
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
            if self.metadata.get("complete") is False:
                # Same observed-row publication as Guojin: no stale deletion or
                # overwrite of a row whose current valuation is unavailable.
                for snap in snapshots:
                    upsert._upsert_single(snap, allow_legacy_claim=False)
            else:
                report = upsert.upsert_from_snapshots(snapshots, broker="binance", account_id=self.account_id,
                    sync_source="api", commit=False, complete_snapshot=True, run_id=snapshots[0].run_id)
                if report["errors"]:
                    raise ValueError("SYNC_BUSINESS_UPSERT_FAILED")
            if self.events:
                start = min(e["executed_at"] for e in self.events)
                end = (datetime.fromisoformat(max(e["executed_at"] for e in self.events)) + timedelta(milliseconds=1)).isoformat()
                coverage = dict(coverage_start=start, coverage_end=end, source_timezone="UTC",
                    instrument_keys=["listing:BTC:CRYPTO:USD:CRYPTO_SPOT"],
                    evidence_ref="Binance Spot BTCUSDT executions; transfers and opening unproven")
                raw = json.dumps(self.events, sort_keys=True).encode()
                import_batch(session, portfolio_id, "binance", self.account_id,
                    "binance_spot", hashlib.sha256(raw).hexdigest(), self.events, coverage,
                    source_metadata=dict(account_id=self.account_id, source_timezone="UTC",
                        issues=["OPENING_AND_TRANSFERS_UNPROVEN"]), commit=False)

        def metadata():
            return {**self.metadata, "retry_after_seconds": self.client.retry_after}

        return PositionSnapshotRepository(db).synchronize("binance", self.account_id, fetch,
            triggered_by=triggered_by, metadata=metadata, reconcile=reconcile, retries=0, observed_rows_only=True)
