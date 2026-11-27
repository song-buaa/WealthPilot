"""Isolated Binance Spot signatures, source failures and original U0/B2/U1 consumers."""
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import hmac
import json
import ssl
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qsl, urlencode
import pytest

from backend.services.broker_sync.binance.client import Configuration, BinanceReadonlyClient
from backend.services.broker_sync.binance.sync_service import BinanceSyncService, trade_events

CONFIG = Configuration("12345", "synthetickey123456", "syntheticsecret123456")


@pytest.mark.parametrize("value,expected", [(None, None), ("7892", 7892)])
def test_binance_connect_port_is_explicit(monkeypatch, value, expected):
    from backend.services.broker_sync.binance.client import configuration

    monkeypatch.setenv("BINANCE_UID", CONFIG.account_id)
    monkeypatch.setenv("BINANCE_API_KEY", CONFIG.api_key)
    monkeypatch.setenv("BINANCE_API_SECRET", CONFIG.api_secret)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9876")
    if value is None:
        monkeypatch.delenv("BINANCE_HTTPS_CONNECT_PORT", raising=False)
    else:
        monkeypatch.setenv("BINANCE_HTTPS_CONNECT_PORT", value)
    assert configuration().https_connect_port == expected


@pytest.mark.parametrize("value", ["0", "65536", "proxy.invalid:7892", "http://127.0.0.1:7892"])
def test_binance_rejects_invalid_connect_port(monkeypatch, value):
    from backend.services.broker_sync.binance.client import configuration

    monkeypatch.setenv("BINANCE_UID", CONFIG.account_id)
    monkeypatch.setenv("BINANCE_API_KEY", CONFIG.api_key)
    monkeypatch.setenv("BINANCE_API_SECRET", CONFIG.api_secret)
    monkeypatch.setenv("BINANCE_HTTPS_CONNECT_PORT", value)
    with pytest.raises(ValueError, match="^BINANCE_CONNECT_PROXY_INVALID$"):
        configuration()


def test_binance_connect_uses_official_tls_and_never_follows_redirect(monkeypatch):
    from backend.services.broker_sync.binance import client as module

    response = Mock(status=302)
    response.getheaders.return_value = [("Location", "https://untrusted.invalid")]
    response.read.return_value = b"{}"
    connection = Mock()
    connection.getresponse.return_value = nullcontext(response)
    factory = Mock(return_value=connection)
    monkeypatch.setattr(module.http.client, "HTTPSConnection", factory)
    session = BinanceReadonlyClient(replace(CONFIG, https_connect_port=7892), clock=lambda: 1000)
    session.offset_ms = 0

    with pytest.raises(ValueError, match="^BINANCE_SOURCE_UNAVAILABLE$"):
        session.get("/api/v3/account")

    assert factory.call_args.args == ("127.0.0.1", 7892)
    tls = factory.call_args.kwargs["context"]
    assert tls.check_hostname and tls.verify_mode == ssl.CERT_REQUIRED
    connection.set_tunnel.assert_called_once_with("api.binance.com", 443)
    connection.request.assert_called_once()
    method, path = connection.request.call_args.args
    assert method == "GET" and path.startswith("/api/v3/account?")
    assert connection.request.call_args.kwargs["headers"]["X-MBX-APIKEY"] == CONFIG.api_key
    assert CONFIG.api_secret not in path
    connection.close.assert_called_once()


def test_binance_connect_tls_error_preserves_last_path_and_redacts(monkeypatch):
    from backend.services.broker_sync.binance import client as module

    connection = Mock()
    connection.request.side_effect = ssl.SSLCertVerificationError("synthetic sensitive diagnostic")
    monkeypatch.setattr(module.http.client, "HTTPSConnection", Mock(return_value=connection))
    direct = Mock(side_effect=AssertionError("must not fall back to direct"))
    monkeypatch.setattr(module.request, "build_opener", direct)
    session = BinanceReadonlyClient(replace(CONFIG, https_connect_port=7892), sleep=lambda _: None)

    with pytest.raises(ValueError, match="^BINANCE_CONNECTION_UNAVAILABLE$"):
        session.server_time()

    direct.assert_not_called()
    assert connection.close.call_count == 2


def fill(identifier=0, *, buyer=True, asset="USDT", fee="0.1", qty="0.001"):
    return dict(id=identifier, symbol="BTCUSDT", price="100000", qty=qty,
        quoteQty=str(Decimal(qty) * 100000), commission=fee, commissionAsset=asset,
        time=int(datetime.now(timezone.utc).timestamp() * 1000), isBuyer=buyer)


class Source:
    def __init__(self):
        self.calls = []
        self.config = CONFIG
        self.uid = "12345"
        self.account_type = "SPOT"
        self.permissions = ["SPOT"]
        self.other_prices = {}
        self.balances = [
            dict(asset="BTC", free="0.00123456", locked="0.00000001"),
            dict(asset="USDT", free="100.00", locked="20.00"),
        ]
        self.fills = [fill()]
        self.error = None
        self.peg = "1"
        self.price_age = 0

    def transport(self, path, query, headers):
        pairs = dict(parse_qsl(query))
        self.calls.append((path, pairs))
        if path in BinanceReadonlyClient.SIGNED:
            signature = pairs.pop("signature")
            assert headers["X-MBX-APIKEY"] == self.config.api_key
            assert signature == hmac.new(self.config.api_secret.encode(),
                urlencode(sorted(pairs.items())).encode(), hashlib.sha256).hexdigest()
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        if self.error and path == "/api/v3/account":
            status, data = self.error
            return status, {"Retry-After": "60"}, json.dumps(data).encode()
        if path == "/api/v3/time":
            data = dict(serverTime=now)
        elif path == "/api/v3/account":
            data = dict(uid=self.uid, accountType=self.account_type, permissions=self.permissions,
                        updateTime=1, balances=self.balances)
        elif path == "/api/v3/ticker/price":
            data = dict(symbol="USDCUSDT", price=self.peg)
        elif path == "/api/v3/aggTrades":
            pair = pairs["symbol"]
            price = "100000" if pair == "BTCUSDT" else self.other_prices.get(pair)
            if price is None:
                return 400, {}, b'{"code":-1121}'
            data = [dict(a=1, p=price, q="0.001", T=now-self.price_age*1000)]
        elif path == "/api/v3/myTrades":
            data = [r for r in self.fills if r["id"] >= int(pairs["fromId"])][:1000]
        else:
            raise AssertionError(path)
        return 200, {}, json.dumps(data).encode()

    def client(self):
        return BinanceReadonlyClient(self.config, transport=self.transport, sleep=lambda _: None)


@pytest.fixture
def context(tmp_path, monkeypatch):
    import backend.services.action.models
    import backend.services.portfolio_history.models
    from tests.test_u1_asset_views import context as original
    yield from original.__wrapped__(tmp_path, monkeypatch)


def publish(c, source):
    service = BinanceSyncService(CONFIG, client=source.client())
    with c.sessions() as db:
        return service.sync_and_persist(db)


def test_balances_history_unknown_cost_and_fixed_consumers(context):
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun
    from backend.services.portfolio_history.models import HistoryEvent
    from backend.services.portfolio_history.query import query
    from pocs.chatgpt_skills.asset_view import capture
    c = context
    source = Source()
    run_id = publish(c, source)
    with c.sessions() as db:
        rows = {r.symbol: r for r in db.query(Position).all()}
        assert rows["BTC:CRYPTO"].quantity == pytest.approx(.00123457)
        assert rows["BTC:CRYPTO"].cost_price is None
        assert rows["BTC:CRYPTO"].profit_loss_value is None
        assert rows["BTC:CRYPTO"].economic_asset_class == "ALTERNATIVE"
        assert rows["USDT:CRYPTO"].quantity == 120
        assert rows["USDT:CRYPTO"].currency == "USDT"
        assert rows["USDT:CRYPTO"].economic_asset_class == "CASH"
        assert db.query(HistoryEvent).count() == 1
        run = db.get(PositionSnapshotRun, run_id)
        meta = json.loads(run.context_json)
        assert meta["complete"] and meta["positions_as_of"] is None and meta["cash_as_of"] is None
        assert meta["account_update_time"] == 1
        assert meta["fx"]["USDT"]["convention"] == "USDT_USD_PARITY_APPROVED"
        result = query(db, 7)
        assert result["items"][0]["quote_asset"] == "USDT"
        assert result["attribution"][0]["cost_basis"] is None
        assert result["attribution"][0]["realized_pnl"] is None
    view = capture(c.db, 7, {}, synthetic=True)
    item = next(r for r in view["data_quality"]["security_inputs"] if r["symbol"] == "BTC:CRYPTO")
    assert item["broker"] == "binance"
    detail = next(r for r in view["views"]["get_investment_details"]["items"] if r["symbol"] == "BTC:CRYPTO")
    assert detail["cost_price"] is None and detail["profit_loss_value"] is None
    assert view["totals"]["investment_profit_loss"] is None
    assert not view["data_quality"]["analysis_eligibility"]["C"]["allowed"]
    second = publish(c, source)
    assert second != run_id
    with c.sessions() as db:
        assert db.query(Position).count() == 2
        assert db.query(HistoryEvent).count() == 1


@pytest.mark.parametrize("problem", ["empty", "partial", "account", "duplicate", "permission", "rate", "malformed", "price"])
def test_failure_preserves_last_good_and_reports_unsupported(context, problem):
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun
    c = context
    source = Source()
    good = publish(c, source)
    with c.sessions() as db:
        previous = [(p.id, p.quantity, p.market_value_cny) for p in db.query(Position).all()]
    if problem == "empty":
        source.balances = []
    elif problem == "partial":
        source.balances.pop()
    elif problem == "account":
        source.uid = "99999"
    elif problem == "duplicate":
        source.balances.append(source.balances[0])
    elif problem == "permission":
        source.error = (403, dict(code=-2015, msg="PRIVATE_SECRET"))
    elif problem == "rate":
        source.error = (429, dict(code=-1003))
    elif problem == "malformed":
        source.balances[0]["free"] = "NaN"
    elif problem == "price":
        source.price_age = 301
    with pytest.raises(ValueError):
        publish(c, source)
    with c.sessions() as db:
        assert [(p.id, p.quantity, p.market_value_cny) for p in db.query(Position).all()] == previous
        runs = db.query(PositionSnapshotRun).order_by(PositionSnapshotRun.id).all()
        assert runs[0].id == good and runs[0].status == "success" and runs[-1].status == "failed"


def test_complete_zero_balance_and_incremental_cursor(context):
    from app.models import Position
    from backend.services.portfolio_history.models import HistoryEvent
    c = context
    source = Source()
    publish(c, source)
    source.fills.append(fill(1, buyer=False))
    source.balances = [dict(asset="BTC", free="0.00000000", locked="0.00000000"),
                       dict(asset="USDT", free="0.00000000", locked="0.00000000")]
    publish(c, source)
    with c.sessions() as db:
        assert all(p.quantity == 0 for p in db.query(Position).all())
        assert db.query(HistoryEvent).count() == 2
    assert source.calls[-1][1]["fromId"] == "1"


@pytest.mark.parametrize("endpoint", ["/api/v3/order", "/api/v3/order/test", "/sapi/v1/capital/withdraw/apply",
                                    "/sapi/v1/asset/transfer", "https://other.example/api/v3/account"])
def test_mutation_unreachable(endpoint):
    source = Source()
    with pytest.raises(ValueError, match="READONLY_ENDPOINT"):
        source.client().get(endpoint)
    assert not source.calls


@pytest.mark.parametrize("code,expected", [(-1022, "SIGNATURE_INVALID"), (-2015, "PERMISSION_DENIED")])
def test_auth_errors_are_safe(code, expected):
    source = Source()
    source.error = (400, dict(code=code, msg=CONFIG.api_key + CONFIG.api_secret))
    with pytest.raises(ValueError, match=expected) as error:
        source.client().get("/api/v3/account")
    assert CONFIG.api_key not in str(error.value) and CONFIG.api_secret not in str(error.value)


def test_rate_limit_does_not_retry_or_sleep():
    source = Source()
    source.error = (429, dict(code=-1003))
    client = source.client()
    with pytest.raises(ValueError, match="RATE_LIMITED"):
        client.get("/api/v3/account")
    assert client.retry_after == 60
    assert len([p for p, _ in source.calls if p == "/api/v3/account"]) == 1


def test_timestamp_skew_refreshes_time_once():
    source = Source()
    count = 0
    def transport(path, query, headers):
        nonlocal count
        if path == "/api/v3/account":
            count += 1
            if count == 1:
                return 400, {}, b'{"code":-1021}'
        return source.transport(path, query, headers)
    client = BinanceReadonlyClient(CONFIG, transport=transport)
    assert client.get("/api/v3/account")["uid"] == CONFIG.account_id
    assert count == 2
    assert sum(p == "/api/v3/time" for p, _ in source.calls) == 2


def test_trade_pagination_and_base_fees():
    from backend.services.portfolio_history.calculation import attribution
    source = Source()
    source.fills = [fill(i, asset="BTC", fee="0.000001") for i in range(1001)]
    service = BinanceSyncService(CONFIG, client=source.client())
    events = service._history(0)
    assert len(events) == 1001
    assert source.calls[-1][1]["fromId"] == "1000"
    coverage = dict(coverage_start="2020-01-01T00:00:00+00:00", coverage_end="2030-01-01T00:00:00+00:00",
        source_timezone="UTC", scope="instruments", instrument_keys=[events[0]["instrument_key"]],
        evidence_ref="synthetic partial", complete_executions=False, complete_cashflows=False,
        corporate_actions_reviewed=False, opening_zero_keys=[])
    # Coverage intentionally incomplete: exact inventory flow can be shown, profit cannot.
    from backend.services.portfolio_history.imports import build_evidence
    _, _, evidence = build_evidence("binance", CONFIG.account_id, "binance_spot", "a"*64, events, coverage)
    result = attribution(events, [evidence], None)
    assert Decimal(result["computed_quantity"]) == Decimal("0.000999") * 1001
    assert result["cost_basis"] is None and result["total_holding_pnl"] is None
    assert result["actual_fees_by_currency"]["known_amounts"]["BTC"] == "0.001001"


def test_crypto_calendar_runs_on_weekends():
    from backend.services.execution_plan.market_hours import valuation_quality
    at = datetime(2026, 11, 29, 12, tzinfo=timezone.utc)
    assert valuation_quality(at.isoformat(), "CRYPTO", at=at)["status"] == "intraday"


def test_identity_costs_and_key_rotation(context):
    from backend.services.broker_sync.binance.client import configuration
    from app.models import Position
    from backend.services.action.ownership import stored_account_ref
    c = context
    source = Source()
    publish(c, source)
    with c.sessions() as db:
        row = db.query(Position).filter_by(symbol="BTC:CRYPTO").one()
        ref, identifier = stored_account_ref(7, row), row.id
    rotated = Configuration(CONFIG.account_id, "rotatedkey12345678", "rotatedsecret123456")
    assert rotated.account_id == CONFIG.account_id
    assert CONFIG.api_key not in repr(CONFIG) and CONFIG.api_secret not in repr(CONFIG)
    source.config = rotated
    with c.sessions() as db:
        BinanceSyncService(rotated, client=source.client()).sync_and_persist(db)
    with c.sessions() as db:
        row = db.query(Position).filter_by(symbol="BTC:CRYPTO").one()
        assert (stored_account_ref(7, row), row.id) == (ref, identifier)


def test_minimal_schema_migration_preserves_history_and_is_idempotent(context):
    from app.binance_schema import plan, verify, FIELDS
    from app.shared_sqlite import schema_digest
    from services.broker_sync.repository import PositionSnapshotRepository
    from services.broker_sync.schema import cash_position
    c = context
    with c.sessions() as db:
        PositionSnapshotRepository(db).synchronize("tiger", "fixture",
            lambda: [cash_position("tiger", "fixture", "CNY", 50, datetime.now(timezone.utc))],
            metadata=lambda: dict(source="synthetic", complete=True), retries=0)
    raw = c.engine.raw_connection()
    try:
        sql = raw.execute("SELECT sql FROM sqlite_master WHERE name='position_snapshots'").fetchone()[0]
        import re
        for field in FIELDS:
            sql = re.sub(r'\b' + field + r'(\s+NUMERIC\s*\(\d+,\s*\d+\))', field + r'\1 NOT NULL', sql, flags=re.I)
        indexes = [r[0] for r in raw.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='position_snapshots'")]
        sql = sql.replace("CREATE TABLE position_snapshots", "CREATE TABLE old_shape")
        raw.execute(sql)
        raw.execute("INSERT INTO old_shape SELECT * FROM position_snapshots")
        raw.execute("DROP TABLE position_snapshots")
        raw.execute("ALTER TABLE old_shape RENAME TO position_snapshots")
        for index in indexes:
            raw.execute(index)
        raw.commit()
        before = raw.execute("SELECT * FROM position_snapshots").fetchall()
        old_digest = schema_digest(raw)
        statements = plan(raw)
        assert statements
        raw.execute("BEGIN IMMEDIATE")
        for statement in statements:
            raw.execute(statement)
        verify(raw)
        raw.commit()
        assert raw.execute("SELECT * FROM position_snapshots").fetchall() == before
        assert raw.execute("PRAGMA foreign_key_check").fetchall() == []
        assert raw.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert schema_digest(raw) != old_digest
        assert plan(raw) == []
    finally:
        raw.close()


def test_binance_btc_policy_identity_requires_original_owner_attestation():
    from backend.services.investment_policy.allocation_hierarchy import _identity
    from backend.services.investment_policy.concentration import _btc_identity
    row = dict(broker="binance", symbol="BTC:CRYPTO", broker_security_type="CRYPTO_SPOT",
        vehicle_type="CRYPTO", economic_asset_class="ALTERNATIVE",
        classification_source="BINANCE_SPOT_NATIVE_IDENTITY", classification_verification_status="DETERMINISTIC",
        original_currency="USDT")
    ref = "position:42"
    coverage = dict(accounts=dict(evidence_refs=[]),
        gold=dict(asset_refs=[], evidence_refs=[]),
        btc=dict(status="PRESENT", asset_refs=[ref],
                 evidence_refs=[ref+":BTC_SPOT_UNLEVERED", ref+":BTC_SPOT_UNLEVERED:source:synthetic-account"]))
    assert _identity(ref, row, "ALTERNATIVE", coverage, set(), {})[0] == "btc"
    quality = dict(investable_scope_evidence=dict(coverage=coverage))
    assert _btc_identity(ref, row, quality)
    coverage["btc"]["evidence_refs"] = []
    assert _identity(ref, row, "ALTERNATIVE", coverage, set(), {}) is None
    assert not _btc_identity(ref, row, quality)
    row["symbol"] = "WBTC:CRYPTO"
    assert not _btc_identity(ref, row, quality)


@pytest.mark.parametrize("missing", ["BINANCE_UID", "BINANCE_API_KEY", "BINANCE_API_SECRET"])
def test_missing_environment_configuration(monkeypatch, missing):
    from backend.services.broker_sync.binance.client import configuration
    for name, value in zip(("BINANCE_UID", "BINANCE_API_KEY", "BINANCE_API_SECRET"),
                           (CONFIG.account_id, CONFIG.api_key, CONFIG.api_secret)):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(missing, "  ")
    with pytest.raises(ValueError, match="^BINANCE_NOT_CONFIGURED$"):
        configuration()


@pytest.mark.parametrize("field,value", [("BINANCE_UID", "not-a-uid"),
    ("BINANCE_UID", "0"), ("BINANCE_API_KEY", "bad-key"), ("BINANCE_API_SECRET", "short")])
def test_invalid_environment_configuration_is_redacted(monkeypatch, field, value):
    from backend.services.broker_sync.binance.client import configuration
    for name, credential in zip(("BINANCE_UID", "BINANCE_API_KEY", "BINANCE_API_SECRET"),
                                (CONFIG.account_id, CONFIG.api_key, CONFIG.api_secret)):
        monkeypatch.setenv(name, credential)
    monkeypatch.setenv(field, value)
    with pytest.raises(ValueError, match="^BINANCE_CONFIGURATION_INVALID$") as caught:
        configuration()
    assert CONFIG.api_key not in str(caught.value) and CONFIG.api_secret not in str(caught.value)


def test_environment_configuration_uses_existing_owner(context, monkeypatch):
    from backend.services.broker_sync.binance.client import configuration
    from backend.api import broker_sync
    for name, value in zip(("BINANCE_UID", "BINANCE_API_KEY", "BINANCE_API_SECRET"),
                           (CONFIG.account_id, CONFIG.api_key, CONFIG.api_secret)):
        monkeypatch.setenv(name, value)
    config = configuration()
    assert config == CONFIG
    assert CONFIG.api_key not in repr(config) and CONFIG.api_secret not in repr(config)
    source = Source()
    with context.sessions() as db:
        assert broker_sync._sync_account(db, "binance") == CONFIG.account_id
        BinanceSyncService(client=source.client()).sync_and_persist(db)
    source.calls.clear()
    with context.sessions() as db:
        db.info["portfolio_id"] = 8
        with pytest.raises(ValueError, match="ACCOUNT_PORTFOLIO_AMBIGUOUS"):
            BinanceSyncService(client=source.client()).sync_and_persist(db)
    assert source.calls == []


def test_rate_and_permission_failure_do_not_import_partial_history(context):
    from backend.services.portfolio_history.models import HistoryEvent
    from app.models import Position
    c = context
    source = Source()
    publish(c, source)
    source.fills.append(fill(1))
    original = source.transport
    def fail_history(path, query, headers):
        if path == "/api/v3/myTrades":
            return 429, {"Retry-After": "100"}, b'{"code":-1003}'
        return original(path, query, headers)
    source.transport = fail_history
    with pytest.raises(ValueError, match="RATE_LIMITED"):
        publish(c, source)
    with c.sessions() as db:
        assert db.query(HistoryEvent).count() == 1
        assert db.query(Position).count() == 2


def test_deviation_is_disclosed_without_claiming_actual_usd(context):
    from services.broker_sync.models import PositionSnapshotRun
    source = Source()
    source.peg = "1.05"
    run = publish(context, source)
    with context.sessions() as db:
        quality = json.loads(db.get(PositionSnapshotRun, run).context_json)
        assert "USDT_RELATIVE_STABLECOIN_DEVIATION" in quality["issues"]
        assert quality["quality"] == "partial"
        assert quality["currencies"] == ["USDT"]


def test_reference_rate_limit_halts_without_followup_or_publish(context):
    from services.broker_sync.models import PositionSnapshotRun
    from app.models import Position
    source = Source()
    original = source.transport
    def limited(path, query, headers):
        if path == '/api/v3/ticker/price':
            source.calls.append((path, {}))
            return 429, {'Retry-After': '90'}, b'{"code":-1003}'
        return original(path, query, headers)
    source.transport = limited
    with pytest.raises(ValueError, match='BINANCE_RATE_LIMITED'):
        publish(context, source)
    assert [p for p, _ in source.calls] == ['/api/v3/time', '/api/v3/account', '/api/v3/ticker/price']
    with context.sessions() as db:
        assert db.query(Position).count() == 0
        run = db.query(PositionSnapshotRun).one()
        assert run.status == 'failed'
        assert json.loads(run.context_json)['retry_after_seconds'] == 90


def test_enabled_status_before_first_run(context, monkeypatch):
    from backend.api import broker_sync
    import backend.core.demo_mode as demo
    monkeypatch.setattr(demo, 'PUBLIC_DEMO_MODE', False)
    monkeypatch.setattr(broker_sync, '_sync_account', lambda db, broker: CONFIG.account_id)
    monkeypatch.setenv('WEALTHPILOT_ENABLED_SYNC_BROKERS', 'tiger, binance')
    with context.sessions() as db:
        row = next(r for r in broker_sync.get_sync_status(db).brokers if r.broker == 'binance')
        assert row.enabled and row.last_sync_status == 'never'
        monkeypatch.delenv('WEALTHPILOT_ENABLED_SYNC_BROKERS')
        assert not next(r for r in broker_sync.get_sync_status(db).brokers if r.broker == 'binance').enabled


def test_unknown_cost_reaches_holdings_tool_and_legacy_consumers(context, monkeypatch):
    import app.utils.position_aggregator as aggregator
    from backend.graph.tools import execute_fetch_holdings
    from backend.services import decision_service, discipline_service
    publish(context, Source())
    monkeypatch.setattr(aggregator, 'get_session', context.sessions)
    positions, total = aggregator.aggregate_investment_positions(7)
    btc = next(p for p in positions if p.symbol == 'BTC:CRYPTO')
    assert btc.pl_rate is None and btc.profit_loss_value is None
    result = execute_fetch_holdings(7)
    assert next(p for p in result.positions if p.ticker == btc.ticker).pl_rate is None
    assert decision_service._get_candidate_positions('盈利最多', [btc])[0] == []
    assert decision_service._get_candidate_positions('亏损最多', [btc])[0] == []
    from backend.services.investment_policy import runtime
    monkeypatch.setattr(runtime, 'current', lambda *_: SimpleNamespace(mode='LEGACY'))
    monkeypatch.setattr(discipline_service, '_parse_trade_intent', lambda *_: {'name': 'BTC', 'unresolved': False})
    evaluation = discipline_service.evaluate_trade('买入BTC', 7)
    assert evaluation['evaluation']['risk_status'] == 'UNKNOWN'
    assert evaluation['evaluation']['blocked']
    assert evaluation['evaluation']['block_reason'] == 'POSITION_COST_UNKNOWN'


@pytest.mark.parametrize('free,accepted', [('0.00000001', True), ('0.000000001', False)])
def test_balance_precision_boundary(context, free, accepted):
    from app.models import Position
    source = Source()
    source.balances[0].update(free=free, locked='0')
    if accepted:
        publish(context, source)
        with context.sessions() as db:
            assert db.query(Position).filter_by(symbol='BTC:CRYPTO').one().quantity == float(Decimal(free))
    else:
        with pytest.raises(ValueError, match='BINANCE_PRECISION_UNSUPPORTED'):
            publish(context, source)


def test_missing_fx_cannot_publish_assets_but_explicit_zero_needs_no_conversion(context, monkeypatch):
    from app.fx_service import fx_service, FXQuote
    from app.models import Position
    source = Source()
    publish(context, source)
    monkeypatch.setattr(fx_service, 'get_quote', lambda *_: FXQuote(None, None, 'none', 'none', 'unavailable'))
    with pytest.raises(ValueError, match='FX_RATE_UNAVAILABLE'):
        publish(context, source)
    with context.sessions() as db:
        assert db.query(Position).filter_by(symbol='USDT:CRYPTO').one().quantity == 120
    source.balances = [dict(asset=asset, free='0', locked='0') for asset in ('BTC', 'USDT')]
    publish(context, source)
    with context.sessions() as db:
        assert all(p.quantity == 0 and p.market_value_cny == 0 for p in db.query(Position).all())
        assert db.query(Position).filter_by(symbol='BTC:CRYPTO').one().profit_loss_value is None


def test_zero_balance_unknown_cost_is_not_economic_exposure():
    from backend.services.discipline_service import _build_portfolio_state
    row = dict(name='BTC', ticker='BTC:CRYPTO', market_value_cny=0, asset_class='另类',
               profit_loss_rate=None, is_leverage_etf=False)
    state = _build_portfolio_state([row], portfolio_drawdown_pct=0)
    assert state.positions == []
    assert row['profit_loss_rate'] is None


@pytest.mark.parametrize('shell_value', [None, '', '   ', 'existing-runtime'])
def test_binance_reuses_original_dotenv_loader(tmp_path, monkeypatch, shell_value):
    from backend.core.env import BINANCE_CREDENTIAL_ENV_KEYS, TIGER_CREDENTIAL_ENV_KEYS, load_project_environment
    from backend.services.broker_sync.binance.client import configuration
    # Only the explicitly created synthetic file is read; never discover real .env.
    monkeypatch.delenv('PYTHON_DOTENV_DISABLED', raising=False)
    for key in TIGER_CREDENTIAL_ENV_KEYS:
        monkeypatch.setenv(key, '')
    expected = ('12345', 'synthetickey123456', 'syntheticsecret123456')
    path = tmp_path / '.env'
    path.write_text(''.join(k + '=' + v + '\n' for k, v in zip(BINANCE_CREDENTIAL_ENV_KEYS, expected)))
    for key, value in zip(BINANCE_CREDENTIAL_ENV_KEYS, expected):
        if shell_value is None:
            monkeypatch.setenv(key, '')  # Track dotenv's later direct os.environ writes for teardown.
            monkeypatch.delenv(key)
        elif shell_value == 'existing-runtime':
            monkeypatch.setenv(key, value + '9')
        else:
            monkeypatch.setenv(key, shell_value)
    load_project_environment(path)
    result = configuration()
    assert (result.account_id, result.api_key, result.api_secret) == (
        tuple(v + '9' for v in expected) if shell_value == 'existing-runtime' else expected)


def cash_source():
    """Production shape only; UID, amounts and trades are synthetic."""
    source = Source()
    source.permissions = ['TRD_GRP_236']
    source.balances = [dict(asset='BTC', free='0', locked='0'),
        dict(asset='USDT', free='23.45678901', locked='0.1'),
        dict(asset='USD', free='31.50', locked='1.50')]
    source.fills = []
    return source


def test_cjk_zero_balances_preserve_identifiers_without_quotes(context):
    from services.broker_sync.models import PositionSnapshotRun

    source = cash_source()
    source.balances[2].update(free="0.00000000", locked="0.00000000")
    source.balances.extend(dict(asset=f"FIX{i}", free="0.00000000", locked="0.00000000")
                           for i in range(881))
    for index, label in ((742, "合成样本"), (838, "样本")):
        source.balances[index]["asset"] = label
    run_id = publish(context, source)
    assert all(path != "/api/v3/aggTrades" for path, _ in source.calls)
    with context.sessions() as db:
        meta = json.loads(db.get(PositionSnapshotRun, run_id).context_json)
        inventory = {row["asset"]: row for row in meta["balance_inventory"]}
        assert meta["complete"] is True and not meta["unvalued_assets"]
        assert len(inventory) == 884
        for label in ("合成样本", "样本"):
            assert inventory[label]["free"] == inventory[label]["locked"] == "0E-8"


def test_nonzero_cjk_balance_preserves_quantity_and_other_holdings(context):
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun

    source = cash_source()
    source.balances.append(dict(asset="ETH", free="2", locked="0"))
    source.other_prices = {"ETHUSDT": "5"}
    publish(context, source)
    source.balances.pop()
    source.balances.append(dict(asset="合成样本", free="2.25", locked="0.75"))
    source.calls.clear()
    run_id = publish(context, source)
    assert all(path != "/api/v3/aggTrades" for path, _ in source.calls)
    with context.sessions() as db:
        meta = json.loads(db.get(PositionSnapshotRun, run_id).context_json)
        assert meta["complete"] is False and meta["publication_mode"] == "observed_rows"
        gap = next(x for x in meta["unvalued_assets"] if x["asset"] == "合成样本")
        assert (gap["free"], gap["locked"], gap["quantity"]) == ("2.25", "0.75", "3.00")
        assert gap["valuation"] == "UNKNOWN"
        assert gap["market_value"] is None and gap["market_value_cny"] is None
        assert {p.symbol for p in db.query(Position)} == {
            "BTC:CRYPTO", "USDT:CRYPTO", "CASH-USD:US", "ETH:CRYPTO"
        }
        assert db.query(Position).filter_by(symbol="ETH:CRYPTO").one().quantity == 2


def test_cjk_identifier_remains_invalid_for_quotes_and_history():
    from backend.services.broker_sync.binance.sync_service import trade_events

    source = cash_source()
    with pytest.raises(ValueError, match="BINANCE_SYMBOL_UNSUPPORTED"):
        source.client().get("/api/v3/aggTrades", {"symbol": "样本USDT"})
    assert not source.calls
    with pytest.raises(ValueError, match="BINANCE_RESPONSE_INVALID"):
        trade_events([fill(asset="样本")])


@pytest.mark.parametrize("row,field,data_type,predicate,code", [
    (None, "row", "NoneType", "ROW_NOT_OBJECT", "BINANCE_RESPONSE_INVALID"),
    ({"free": "0", "locked": "0"}, "asset", "missing", "REQUIRED_FIELD", "BINANCE_RESPONSE_INVALID"),
    ({"asset": "样本", "locked": "0"}, "free", "missing", "REQUIRED_FIELD", "BINANCE_RESPONSE_INVALID"),
    ({"asset": "样本", "free": 0, "locked": "0"}, "free", "int", "AMOUNT_NOT_STRING", "BINANCE_RESPONSE_INVALID"),
    ({"asset": "样本", "free": "0", "locked": "PRIVATE"}, "locked", "str", "DECIMAL_PARSE", "BINANCE_RESPONSE_INVALID"),
    ({"asset": "样本", "free": "NaN", "locked": "0"}, "free", "str", "AMOUNT_NOT_FINITE", "BINANCE_RESPONSE_INVALID"),
    ({"asset": "样本", "free": "0", "locked": "-1"}, "locked", "str", "AMOUNT_OUT_OF_RANGE", "BINANCE_RESPONSE_INVALID"),
    ({"asset": "样本", "free": "1e12", "locked": "0"}, "free", "str", "AMOUNT_OUT_OF_RANGE", "BINANCE_RESPONSE_INVALID"),
    ({"asset": "样本", "free": "0", "locked": "0"}, "asset", "str", "DUPLICATE_IDENTIFIER", "DUPLICATE_SNAPSHOT_IDENTITY"),
])
def test_balance_failure_records_only_safe_coordinates(row, field, data_type, predicate, code):
    source = cash_source()
    prior_asset = "样本" if predicate == "DUPLICATE_IDENTIFIER" else "合成样本"
    source.balances.append(dict(asset=prior_asset, free="0", locked="0"))
    source.balances.append(row)
    service = BinanceSyncService(CONFIG, client=source.client())
    with pytest.raises(ValueError, match=f"^{code}$"):
        service._balances()
    assert service.metadata == {"balance_validation_error": {
        "row_index": 4, "field": field, "data_type": data_type, "predicate": predicate,
    }}


def test_balance_failure_persists_diagnostic_and_keeps_last_good(context):
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun

    source = cash_source()
    good_id = publish(context, source)
    source.balances[2]["locked"] = "0.000000001"
    with pytest.raises(ValueError, match="^BINANCE_PRECISION_UNSUPPORTED$"):
        publish(context, source)
    with context.sessions() as db:
        failed = db.query(PositionSnapshotRun).filter_by(status="failed").one()
        meta = json.loads(failed.context_json)
        assert meta["balance_validation_error"] == {
            "row_index": 2, "field": "locked", "data_type": "str", "predicate": "CORE_ASSET_SCALE",
        }
        assert "balance_inventory" not in meta
        assert db.get(PositionSnapshotRun, good_id).status == "success"
        assert db.query(Position).filter_by(symbol="CASH-USD:US").one().quantity == 33


def test_spot_inventory_accepts_single_character_zero_rows_without_quotes(context):
    from services.broker_sync.models import PositionSnapshotRun
    from backend.services.portfolio_history.models import HistoryEvent

    source = cash_source()
    # Eleven synthetic one-character rows, plus numeric and length boundaries.
    codes = (*"TWGABCDEFX7", "1INCH", "Z" * 20)
    source.balances.extend(dict(asset=code, free="0.00000000", locked="0.00000000")
                           for code in codes)
    source.fills = [fill(asset="T")]
    run_id = publish(context, source)
    assert all(path != "/api/v3/aggTrades" for path, _ in source.calls)
    with context.sessions() as db:
        meta = json.loads(db.get(PositionSnapshotRun, run_id).context_json)
        assert meta["complete"] is True
        assert {row["asset"] for row in meta["balance_inventory"]} == {*codes, "BTC", "USDT", "USD"}
        assert json.loads(db.query(HistoryEvent).one().payload_json)["commission_currency"] == "T"


def test_nonzero_short_spot_codes_reuse_quote_unknown_and_last_good(context):
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun

    source = cash_source()
    source.balances.extend([dict(asset="T", free="2", locked="1"), dict(asset="W", free="1", locked="0")])
    source.other_prices = {"TUSDT": "5"}
    publish(context, source)
    source.other_prices.clear()
    source.balances[-2]["free"] = "4"
    run_id = publish(context, source)
    with context.sessions() as db:
        meta = json.loads(db.get(PositionSnapshotRun, run_id).context_json)
        assert not meta["complete"]
        assets = {row["asset"]: row for row in meta["unvalued_assets"]}
        assert assets["T"]["quantity"] == "5"
        assert assets["T"]["valuation"] == "UNKNOWN"
        assert assets["W"]["market_value_cny"] is None
        assert assets["T"]["last_good_retained"]
        assert db.query(Position).filter_by(symbol="T:CRYPTO").one().quantity == 3


@pytest.mark.parametrize("code", [
    12, None, "", "t", "Ｔ", "T/USDT", "A" * 21, "样/本", "样\\本", "样\n本",
    "样\u202e本", "样本A", "㐀", "样" * 21,
])
def test_spot_asset_codes_reject_invalid_types_and_characters(code):
    source = cash_source()
    source.balances.append(dict(asset=code, free="0", locked="0"))
    service = BinanceSyncService(CONFIG, client=source.client())
    with pytest.raises(ValueError, match="BINANCE_RESPONSE_INVALID"):
        service._balances()


def test_account_group_and_native_usd_are_not_key_permissions(context, monkeypatch):
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun, PositionSnapshot
    from app.fx_service import fx_service, FXQuote
    from backend.services import portfolio_service
    source = cash_source()
    monkeypatch.setattr(fx_service, 'get_quote', lambda a, b: FXQuote(7, '2026-11-27', 'fixture', 'fixture', 'current'))
    run_id = publish(context, source)
    with context.sessions() as db:
        rows = {r.symbol: r for r in db.query(Position).all()}
        assert set(rows) == {'BTC:CRYPTO', 'USDT:CRYPTO', 'CASH-USD:US'}
        assert rows['CASH-USD:US'].quantity == 33
        assert rows['CASH-USD:US'].currency == 'USD'
        assert rows['CASH-USD:US'].economic_asset_class == 'CASH'
        assert rows['USDT:CRYPTO'].currency == 'USDT'
        assert rows['USDT:CRYPTO'].economic_asset_class == 'CASH'
        assert rows['BTC:CRYPTO'].economic_asset_class == 'ALTERNATIVE'
        usd = db.query(PositionSnapshot).filter_by(run_id=run_id, currency='USD').one()
        assert usd.available_quantity == Decimal('31.5')
        assert json.loads(usd.raw_data_json)['locked'] == '1.50'
        meta = json.loads(db.get(PositionSnapshotRun, run_id).context_json)
        assert meta['account_permission_groups'] == ['TRD_GRP_236']
        assert meta['permission_semantics'] == 'ACCOUNT_TRADING_GROUPS_NOT_API_KEY_SCOPES'
        assert meta['fx']['USD']['rate'] == meta['fx']['USDT']['rate'] == 7
        assert 'convention' not in meta['fx']['USD']
        assert meta['fx']['USDT']['convention'] == 'USDT_USD_PARITY_APPROVED'
    monkeypatch.setattr(portfolio_service, 'get_session', context.sessions)
    import app.analyzer
    monkeypatch.setattr(app.analyzer, 'get_session', context.sessions)
    summary = portfolio_service.get_summary(7)
    assert summary['total_assets'] == pytest.approx((33 + 23.55678901) * 7)
    assert 'unvalued_assets' not in summary
    publish(context, source)
    with context.sessions() as db:
        assert db.query(Position).count() == 3
    for balance in source.balances:
        balance.update(free='0', locked='0')
    publish(context, source)
    with context.sessions() as db:
        assert all(r.quantity == 0 and r.market_value_cny == 0 for r in db.query(Position).all())


@pytest.mark.parametrize('field,value,code', [('uid', '99999', 'BINANCE_UID_MISMATCH'),
    ('uid', None, 'BINANCE_UID_MISSING'), ('account_type', 'MARGIN', 'BINANCE_ACCOUNT_TYPE_NOT_SPOT'),
    ('account_type', None, 'BINANCE_ACCOUNT_TYPE_NOT_SPOT')])
def test_identity_failure_names_the_field_without_leaking_values(context, field, value, code):
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun
    source = cash_source()
    publish(context, source)
    setattr(source, field, value)
    with pytest.raises(ValueError, match='^' + code + '$'):
        publish(context, source)
    with context.sessions() as db:
        assert db.query(Position).filter_by(currency='USD').one().quantity == 33
        run = db.query(PositionSnapshotRun).order_by(PositionSnapshotRun.id.desc()).first()
        assert run.status == 'failed' and run.error_message == code
        assert CONFIG.account_id not in run.error_message and CONFIG.api_key not in run.error_message


def test_unquoted_assets_keep_quantity_unknown_and_existing_valuation(context, monkeypatch):
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun
    from backend.services import portfolio_service
    from app.sync_status import _public_quality
    from pocs.chatgpt_skills.asset_view import capture
    source = cash_source()
    source.balances.append(dict(asset='ETH', free='2', locked='0.25'))
    source.other_prices['ETHUSDT'] = '2500'
    publish(context, source)
    with context.sessions() as db:
        old = db.query(Position).filter_by(symbol='ETH:CRYPTO').one()
        old_facts = (old.id, old.quantity, old.current_price, old.market_value_cny)
    source.other_prices.clear()
    source.balances[-1].update(free='1.25', locked='0.5')
    source.balances.append(dict(asset='MYSTERY', free='1.234567891', locked='0'))
    run_id = publish(context, source)
    with context.sessions() as db:
        old = db.query(Position).filter_by(symbol='ETH:CRYPTO').one()
        assert (old.id, old.quantity, old.current_price, old.market_value_cny) == old_facts
        assert db.query(Position).filter_by(symbol='MYSTERY:CRYPTO').count() == 0
        meta = _public_quality(db.get(PositionSnapshotRun, run_id))
        assert meta['complete'] is False and meta['publication_mode'] == 'observed_rows'
        gaps = {g['asset']: g for g in meta['unvalued_assets']}
        assert gaps['ETH']['quantity'] == '1.75' and gaps['ETH']['last_good_retained']
        assert gaps['ETH']['valuation'] == 'UNKNOWN' and gaps['ETH']['market_value_cny'] is None
        assert gaps['MYSTERY']['quantity'] == '1.234567891'
        assert gaps['MYSTERY']['reason_code'] == 'PRECISION_UNSUPPORTED'
    monkeypatch.setattr(portfolio_service, 'get_session', context.sessions)
    import app.analyzer
    monkeypatch.setattr(app.analyzer, 'get_session', context.sessions)
    summary = portfolio_service.get_summary(7)
    assert summary['valuation_complete'] is False and len(summary['unvalued_assets']) == 2
    assert summary['valuation_scope'] == 'STORED_KNOWN_AND_LAST_GOOD_SUBTOTAL'
    view = capture(context.db, 7, {}, synthetic=True)
    accounts = view['data_quality']['accounts']
    assert any(a.get('unvalued_assets') for a in accounts)
    assert not view['data_quality']['analysis_eligibility']['C']['allowed']
    details = view['views']['get_investment_details']
    assert details['valuation_complete'] is False and len(details['unvalued_assets']) == 2
    overview = view['views']['get_wealth_overview']
    assert overview['valuation_complete'] is False
    assert overview['status'] == 'partial'
    eth = next(p for p in view['data_quality']['security_inputs'] if p['symbol'] == 'ETH:CRYPTO')
    assert eth['price_as_of'] is None
    # Restore authoritative quote: source gaps retire without a new asset store.
    source.other_prices['ETHUSDT'] = '2400'
    source.balances.pop()
    source.balances[-1].update(free='1.5', locked='0')
    publish(context, source)
    with context.sessions() as db:
        assert db.query(Position).filter_by(symbol='ETH:CRYPTO').one().quantity == 1.5
        run = db.query(PositionSnapshotRun).order_by(PositionSnapshotRun.id.desc()).first()
        # MYSTERY disappeared without an explicit zero, so coverage remains partial.
        assert _public_quality(run)['unvalued_assets'][0]['asset'] == 'MYSTERY'
    source.balances.append(dict(asset='MYSTERY', free='0', locked='0'))
    publish(context, source)
    assert 'unvalued_assets' not in portfolio_service.get_summary(7)


def test_absent_usd_is_not_zero_during_partial_account_publication(context):
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun
    source = cash_source()
    publish(context, source)
    source.balances.pop()
    publish(context, source)
    with context.sessions() as db:
        assert db.query(Position).filter_by(symbol='CASH-USD:US').one().quantity == 33
        run = db.query(PositionSnapshotRun).order_by(PositionSnapshotRun.id.desc()).first()
        gap = json.loads(run.context_json)['unvalued_assets'][0]
        assert gap['asset'] == 'USD' and gap['quantity'] is None
        assert gap['reason_code'] == 'PREVIOUS_BALANCE_MISSING'


def test_extra_market_quotes_remain_bounded_and_history_is_btc_only(context):
    from services.broker_sync.models import PositionSnapshotRun
    source = cash_source()
    for i in range(10):
        asset = 'COIN' + str(i)
        source.balances.append(dict(asset=asset, free='1', locked='0'))
        source.other_prices[asset + 'USDT'] = '2'
    run_id = publish(context, source)
    assert len([p for p, _ in source.calls if p == '/api/v3/aggTrades']) == 8
    assert [q['symbol'] for p, q in source.calls if p == '/api/v3/myTrades'] == ['BTCUSDT']
    with context.sessions() as db:
        gaps = json.loads(db.get(PositionSnapshotRun, run_id).context_json)['unvalued_assets']
        assert len(gaps) == 2 and all(g['reason_code'] == 'QUOTE_BUDGET_EXHAUSTED' for g in gaps)
    with pytest.raises(ValueError, match='BINANCE_SYMBOL_UNSUPPORTED'):
        source.client().get('/api/v3/myTrades', {'symbol': 'ETHUSDT'})
    with pytest.raises(ValueError, match='BINANCE_SYMBOL_UNSUPPORTED'):
        source.client().get('/api/v3/aggTrades', {'symbol': 'ETH/USDT'})


def test_extra_asset_rate_limit_stops_before_history_and_keeps_last_good(context):
    from app.models import Position
    source = cash_source()
    publish(context, source)
    source.balances.append(dict(asset='ETH', free='1', locked='0'))
    source.calls.clear()
    original = source.transport
    def limited(path, query, headers):
        if path == '/api/v3/aggTrades':
            source.calls.append((path, {}))
            return 429, {'Retry-After': '120'}, b'{"code":-1003}'
        return original(path, query, headers)
    source.transport = limited
    with pytest.raises(ValueError, match='BINANCE_RATE_LIMITED'):
        publish(context, source)
    assert not any(p == '/api/v3/myTrades' for p, _ in source.calls)
    with context.sessions() as db:
        assert db.query(Position).count() == 3
        assert db.query(Position).filter_by(currency='USD').one().quantity == 33


def test_partial_publication_and_b2_failure_are_one_transaction(context, monkeypatch):
    from app.models import Position
    from backend.services.portfolio_history import imports
    from services.broker_sync.models import PositionSnapshotRun
    source = cash_source()
    publish(context, source)
    source.balances[1]['free'] = '1'
    source.balances.append(dict(asset='MYSTERY', free='5', locked='0'))
    source.fills = [fill()]
    def fail(*args, **kwargs):
        raise ValueError('SYNTHETIC_IMPORT_FAILURE')
    monkeypatch.setattr(imports, 'import_batch', fail)
    with pytest.raises(ValueError, match='SYNC_SOURCE_FAILED'):
        publish(context, source)
    with context.sessions() as db:
        assert db.query(Position).filter_by(symbol='USDT:CRYPTO').one().quantity == pytest.approx(23.55678901)
        assert db.query(Position).filter_by(currency='USD').one().quantity == 33
        run = db.query(PositionSnapshotRun).order_by(PositionSnapshotRun.id.desc()).first()
        assert run.status == 'failed'


def test_gap_projection_uses_original_portfolio_and_opaque_account_scope(context):
    from app.sync_status import unvalued_source_assets
    from pocs.chatgpt_skills.reader import read_details
    source = cash_source()
    source.balances.append(dict(asset='MYSTERY', free='7', locked='0'))
    publish(context, source)
    other = cash_source()
    other.uid = '98765'
    other.config = replace(CONFIG, account_id=other.uid)
    other.balances.append(dict(asset='MYSTERY', free='9', locked='0'))
    with context.sessions() as db:
        BinanceSyncService(other.config, client=other.client()).sync_and_persist(db)
        gaps = unvalued_source_assets(db.connection(), 7)
        assert len(gaps) == 2
        selected = next(g for g in gaps if g['quantity'] == '7')
        assert unvalued_source_assets(db.connection(), 8) == []
        assert all(CONFIG.account_id not in json.dumps(g) and other.uid not in json.dumps(g) for g in gaps)
    result = read_details(context.db, 7, 'get_investment_details', {'account_ref': selected['account_ref']}, synthetic=True)
    assert result['unvalued_assets'] == [selected]
    assert result['portfolio_summary']['unvalued_assets'] == [selected]
