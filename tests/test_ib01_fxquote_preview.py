"""Cache-type regressions use disposable SQLite and the original fake broker."""
from dataclasses import asdict
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.fx_service import FXQuote, FXService, fx_service
from app.models import Position
from backend.services.action.models import OrderRecord
from backend.services.action.risk_engine import _cached_fx_rate_to_cny, portfolio_order_risk
from tests.test_ib01_trade_link import api, budget, confirmation  # noqa: F401
from tests.test_execution_batch_service import NOW


@pytest.fixture
def cached_fx(monkeypatch):
    quote = FXQuote(7.1, NOW.date().isoformat(), NOW.isoformat(), "provider", "current")
    monkeypatch.setattr(fx_service, "_cache", {("USD", "CNY", "latest"): quote})
    monkeypatch.setattr(fx_service, "_clock", lambda: 100)
    monkeypatch.setattr(fx_service, "_expires", {("USD", "CNY", "latest"): 400})
    def forbidden(*args, **kwargs):
        raise AssertionError("Risk review must not fetch FX or call a provider")
    monkeypatch.setattr(fx_service, "get_quote", forbidden)
    return quote


@pytest.mark.parametrize("kind", ["object", "dict", "legacy_tuple"])
def test_risk_quantifies_cached_fx_without_subscripting_objects(api, cached_fx, kind):
    _, engine, _ = api
    fx_service._cache[("USD", "CNY", "latest")] = {
        "object": cached_fx, "dict": asdict(cached_fx),
        "legacy_tuple": (7.1, NOW.date().isoformat()),
    }[kind]
    with Session(engine) as db:
        db.add(Position(portfolio_id=1, segment="投资", name="Synthetic balance",
            ticker="CNY", symbol="CASH-CNY:SH", platform="fixture", currency="CNY",
            asset_class="货币", quantity=10000, cost_price=1, market_value_cny=10000))
        db.commit()
        result = portfolio_order_risk(db, portfolio_id=1, symbol="IB01:LSE",
            side="BUY", quantity=20, limit_price=Decimal("126"), currency="USD")
    warning = next(w for w in result["warnings"] if w["rule"] == "single_order_pct")
    assert warning["detail"]["order_value"] == 17892
    assert result["requires_confirmation"]


@pytest.mark.parametrize("kind", ["object", "dict"])
def test_ib01_2650_preview_reaches_manual_confirmation_without_order(api, cached_fx, kind):
    client, engine, broker = api
    if kind == "dict":
        fx_service._cache[("USD", "CNY", "latest")] = asdict(cached_fx)
    original_cash = broker.get_cash_snapshot
    broker.get_cash_snapshot = lambda currency: original_cash(currency) | {"CashBalance": 2692.91}
    params = budget(request_id=str(uuid4()), target_amount="2650", time_in_force="DAY")
    response = client.post('/api/execution-batches/trade-link/quote', json=params)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview['batch']['status'] == 'READY'
    leg = preview['batch']['legs'][0]
    assert leg['resolved_con_id'] == 354802220
    assert leg['isin'] == 'IE00BGSF1X88'
    assert leg['final_quantity'] == 20
    assert Decimal(str(leg['final_limit'])) == 126
    batch = preview['batch']
    assert batch['usable_cash'] == 2692.91
    assert batch['estimated_fees'] == 1
    assert Decimal(str(batch['estimated_total'])) + Decimal(str(batch['estimated_fees'])) + Decimal(str(batch['safety_cushion'])) <= 2650
    assert batch['execution_policy']['time_in_force'] == 'DAY'
    assert broker.what_if_calls and not broker.place_calls
    # Save only the reviewed intent in the disposable test DB. No final order submit.
    result = client.post('/api/execution-batches/trade-link/confirm-intent',
                        json=confirmation(params, preview))
    assert result.status_code == 200, result.text
    assert result.json()['status'] == 'READY'
    with Session(engine) as db:
        assert db.query(OrderRecord).count() == 0
    assert not broker.place_calls


@pytest.mark.parametrize("changes", [
    {"quality": "stale"}, {"quality": "fallback"}, {"quality": "unavailable"},
    {"rate": None}, {"rate": 0}, {"rate": -1}, {"rate": "NaN"},
    {"rate": "Infinity"}, {"rate": "bad"}, {"rate_date": None},
    {"rate_date": "fallback"}, {"rate_date": {}},
])
@pytest.mark.parametrize("kind", ["object", "dict"])
def test_untrusted_fx_remains_unquantified_not_zero_or_bypassed(cached_fx, changes, kind):
    data = asdict(cached_fx) | changes
    fx_service._cache[("USD", "CNY", "latest")] = FXQuote(**data) if kind == 'object' else data
    assert _cached_fx_rate_to_cny('USD') is None


@pytest.mark.parametrize("expiry", [99, 100])
def test_expired_or_boundary_fx_is_not_relabelled_fresh(cached_fx, expiry):
    fx_service._expires[("USD", "CNY", "latest")] = expiry
    assert _cached_fx_rate_to_cny('USD') is None


def test_actual_fxservice_provider_cache_object_and_legacy_normalization(monkeypatch):
    class Source:
        def fetch_rate(self, *args):
            return 7.1, date.today().isoformat()
    service = FXService(provider=Source(), clock=lambda: 100)
    quote = service.get_quote('USD', 'CNY')
    assert isinstance(quote, FXQuote)
    import app.fx_service as module
    monkeypatch.setattr(module, 'fx_service', service)
    assert _cached_fx_rate_to_cny('USD') == Decimal('7.1')
    service._cache[("USD", "CNY", "latest")] = (7.1, date.today().isoformat())
    service.get_quote('USD', 'CNY')  # Original FXService upgrades old tuples to FXQuote.
    assert _cached_fx_rate_to_cny('USD') == Decimal('7.1')


@pytest.mark.parametrize('failure', ['stale', 'delayed', 'missing_cash', 'whatif', 'fee_currency', 'fee_unknown'])
def test_type_fix_does_not_relax_original_execution_safety(api, cached_fx, monkeypatch, failure):
    client, engine, broker = api
    original = broker.get_executable_quote
    original_whatif = broker.what_if_limit_order
    if failure == 'stale':
        monkeypatch.setattr(broker, 'get_executable_quote', lambda r: original(r) | {'quote_timestamp': (NOW-timedelta(seconds=31)).isoformat()})
    elif failure == 'delayed':
        monkeypatch.setattr(broker, 'get_executable_quote', lambda r: original(r) | {'quote_quality': 'DELAYED', 'market_data_type': 3})
    elif failure == 'missing_cash':
        monkeypatch.setattr(broker, 'get_cash_snapshot', lambda c: {'currency': c, 'BuyingPower': 100000})
    elif failure == 'whatif':
        broker.what_if_error = True
    elif failure == 'fee_currency':
        broker.commission_currency = 'HKD'
    else:
        monkeypatch.setattr(broker, 'what_if_limit_order', lambda *a, **kw: original_whatif(*a, **kw) | {'commission': None})
    response = client.post('/api/execution-batches/trade-link/quote', json=budget(request_id=str(uuid4()), target_amount='2650'))
    if response.status_code == 200:
        result = response.json()
        assert result['batch']['status'] in {'DRAFT', 'ATTENTION_REQUIRED'}, response.text
        assert result['batch']['attention_reason']
        assert result['preview_hash'] == ''
    assert not broker.place_calls
    with Session(engine) as db:
        assert db.query(OrderRecord).count() == 0
