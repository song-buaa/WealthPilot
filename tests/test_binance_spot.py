"""Isolated Binance Spot signatures, source failures and original U0/B2/U1 consumers."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import hmac
import json
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode
import pytest

from backend.services.broker_sync.binance.client import Configuration, BinanceReadonlyClient
from backend.services.broker_sync.binance.sync_service import BinanceSyncService, trade_events

CONFIG = Configuration(7, "12345", "synthetickey123456", "syntheticsecret123456")


def fill(identifier=0, *, buyer=True, asset="USDT", fee="0.1", qty="0.001"):
    return dict(id=identifier, symbol="BTCUSDT", price="100000", qty=qty,
        quoteQty=str(Decimal(qty) * 100000), commission=fee, commissionAsset=asset,
        time=int(datetime.now(timezone.utc).timestamp() * 1000), isBuyer=buyer)


class Source:
    def __init__(self):
        self.calls = []
        self.config = CONFIG
        self.uid = "12345"
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
            data = dict(uid=self.uid, accountType="SPOT", permissions=["SPOT"],
                        updateTime=1, balances=self.balances)
        elif path == "/api/v3/ticker/price":
            data = dict(symbol="USDCUSDT", price=self.peg)
        elif path == "/api/v3/aggTrades":
            data = [dict(a=1, p="100000", q="0.001", T=now-self.price_age*1000)]
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


@pytest.mark.parametrize("problem", ["empty", "partial", "account", "duplicate", "unknown", "permission", "rate", "malformed", "price"])
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
    elif problem == "unknown":
        source.balances.append(dict(asset="DOGE", free="2.5", locked="0"))
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
        if problem == "unknown":
            assert json.loads(runs[-1].context_json)["unsupported_assets"][0]["quantity"] == "2.5"


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
    rotated = Configuration(7, CONFIG.account_id, "rotatedkey12345678", "rotatedsecret123456")
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


def test_disabled_and_private_configuration(tmp_path, monkeypatch):
    from backend.services.broker_sync.binance.client import configuration
    monkeypatch.delenv("BINANCE_CONFIG_FILE", raising=False)
    with pytest.raises(ValueError, match="NOT_CONFIGURED"):
        configuration()
    path = tmp_path / "binance.json"
    path.write_text(json.dumps(dict(portfolio_id=7, uid=CONFIG.account_id,
                                   api_key=CONFIG.api_key, api_secret=CONFIG.api_secret)))
    path.chmod(0o600)
    monkeypatch.setenv("BINANCE_CONFIG_FILE", str(path))
    assert configuration() == CONFIG
    path.chmod(0o644)
    with pytest.raises(ValueError, match="CONFIGURATION_INVALID"):
        configuration()


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
