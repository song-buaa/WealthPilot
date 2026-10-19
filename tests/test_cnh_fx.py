"""CNH remains native cash; all FX and IBKR responses are isolated fixtures."""
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
import json

import pytest

from app import fx_service as fx_module
from app.fx_service import FXService, FrankfurterProvider, FRANKFURTER_BASE_URL, FRANKFURTER_CNH_URL
from app.models import Position


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    # Match the dedicated sync suite's backend import root only while running
    # this DB fixture; do not shadow root scripts during pytest collection.
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'backend'))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import Portfolio
    from services.broker_sync import models as _sync_models  # noqa: F401
    engine = create_engine('sqlite:///' + str(tmp_path / 'cnh.db'))
    Base.metadata.create_all(engine)
    with Session(engine, info={'portfolio_id':1}) as session:
        session.add(Portfolio(id=1, name='Synthetic'))
        session.commit()
        yield session
    engine.dispose()


def rows(day=None):
    day = day or date.today().isoformat()
    return [dict(base='USD', quote='CNH', rate=7.2, date=day),
            dict(base='USD', quote='CNY', rate=7.1, date=day)]


@pytest.fixture
def http(monkeypatch):
    state = SimpleNamespace(rows=rows(), calls=[], offline=False)
    def get(url, *, params, timeout):
        state.calls.append((url, params, timeout))
        if state.offline:
            raise OSError('isolated offline fixture')
        if url == FRANKFURTER_CNH_URL:
            data = state.rows
        else:
            data = {'date': date.today().isoformat(), 'rates': {params['to']: 7.1}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: data)
    monkeypatch.setattr(fx_module, '_requests', SimpleNamespace(get=get))
    return state


def test_cnh_forward_reverse_and_native_usd_legs(http):
    provider = FrankfurterProvider()
    forward, actual = provider.fetch_rate('CNH', 'CNY', 'latest')
    reverse, reverse_date = provider.fetch_rate('CNY', 'CNH', 'latest')
    assert forward == pytest.approx(7.1/7.2) and forward != 1
    assert forward * reverse == pytest.approx(1)
    assert actual == reverse_date == date.today().isoformat()
    http.rows = rows()[:1]
    assert provider.fetch_rate('CNH', 'USD', 'latest')[0] == pytest.approx(1/7.2)
    assert provider.fetch_rate('USD', 'CNH', 'latest')[0] == 7.2
    assert http.calls[0] == (FRANKFURTER_CNH_URL, {'base':'USD', 'quotes':'CNH,CNY'}, 5)


def test_cnh_ttl_and_historical_keep_real_source_date(http):
    clock = [0]
    fx = FXService(ttl=300, clock=lambda: clock[0])
    first = fx.get_quote('CNH')
    assert first.source == 'provider_frankfurter_v2_BI_USD_cross'
    assert first.quality == 'current'
    assert fx.get_quote('CNH') == first and len(http.calls) == 1
    clock[0] = 301
    http.rows = rows((date.today()-timedelta(days=1)).isoformat())
    assert fx.get_quote('CNH').rate_date == http.rows[0]['date']
    assert len(http.calls) == 2
    requested = (date.today()-timedelta(days=20)).isoformat()
    http.rows = rows(requested)
    historical = fx.get_quote('CNH', date=requested)
    assert historical.quality == 'historical' and historical.rate_date == requested
    assert http.calls[-1][1]['date'] == requested


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'mixed_dates', 'wrong_base', 'wrong_quote',
                                  'zero', 'negative', 'nan', 'infinity', 'missing_date', 'future', 'offline'])
def test_cnh_invalid_observations_never_become_parity_or_mixed_source(http, fault):
    if fault == 'missing': http.rows.pop()
    elif fault == 'duplicate': http.rows[1] = dict(http.rows[0])
    elif fault == 'mixed_dates': http.rows[1]['date'] = (date.today()-timedelta(days=1)).isoformat()
    elif fault == 'wrong_base': http.rows[0]['base'] = 'CNY'
    elif fault == 'wrong_quote': http.rows[0]['quote'] = 'CNY'
    elif fault in ['zero','negative','nan','infinity']:
        http.rows[0]['rate'] = {'zero':0,'negative':-1,'nan':float('nan'),'infinity':float('inf')}[fault]
    elif fault == 'missing_date': del http.rows[0]['date']
    elif fault == 'future':
        for row in http.rows: row['date'] = (date.today()+timedelta(days=1)).isoformat()
    else: http.offline = True
    quote = FXService().get_quote('CNH')
    assert quote.rate is None and quote.rate_date is None and quote.quality == 'unavailable'
    assert len(http.calls) == 1  # cannot bypass rejected same-date pair via another cross


def test_cnh_refresh_failure_and_old_observation_remain_explicit(http):
    clock = [0]
    fx = FXService(ttl=1, failure_ttl=15, clock=lambda: clock[0])
    first = fx.get_quote('CNH')
    clock[0] = 2
    http.offline = True
    stale = fx.get_quote('CNH')
    assert stale.rate == first.rate and stale.rate_date == first.rate_date
    assert stale.fetched_at == first.fetched_at and stale.source == first.source
    assert stale.quality == 'stale' and 'FX_REFRESH_FAILED' in stale.issues
    clock[0] = 3
    assert fx.get_quote('CNH') == stale and len(http.calls) == 2
    http.offline = False
    http.rows = rows((date.today()-timedelta(days=5)).isoformat())
    old = FXService().get_quote('CNH')
    assert old.quality == 'stale' and old.rate_date == http.rows[0]['date']


@pytest.mark.parametrize('currency', ['USD','HKD','EUR','GBP'])
def test_existing_currency_provider_and_same_currency_are_unchanged(http, currency):
    quote = FXService().get_quote(currency)
    assert quote.rate == 7.1 and quote.source == 'provider'
    assert http.calls == [(FRANKFURTER_BASE_URL+'/latest', {'from':currency,'to':'CNY'},5)]
    assert FXService().get_quote('CNY').rate == 1
    assert len(http.calls) == 1


def test_ibkr_cnh_repeated_sync_preserves_native_identity_and_last_good(db_session, monkeypatch, http):
    from core.config import settings
    from services.broker_sync.models import PositionSnapshotRun
    from services.broker_sync.snowball.sync_service import SnowballSyncService
    monkeypatch.setattr(settings, 'ibkr_account', 'synthetic-ibkr')
    monkeypatch.setattr(settings, 'snowball_secret_key', None)
    client = MagicMock(broker_name='ibkr')
    client.get_account_info.return_value = dict(account_id='synthetic-ibkr', account_snapshot_complete=True,
        cash_balances=[{'currency':'CNH','amount':30.25}, {'currency':'BASE','amount':999}])
    client.get_positions.return_value = []
    service = SnowballSyncService(client)
    fx = FXService()
    monkeypatch.setattr(fx_module.fx_service, 'get_quote', fx.get_quote)
    first = service.sync_and_persist(db_session)
    service.sync_and_persist(db_session)
    pos = db_session.query(Position).filter_by(broker='snowball').one()
    original_id = pos.id
    assert pos.original_currency == 'CNH' and pos.original_value == 30.25
    assert pos.market_value_cny == pytest.approx(30.25 * 7.1/7.2)
    assert pos.fx_rate_to_cny != 1 and pos.broker_account_id == 'synthetic-ibkr'
    meta = json.loads(db_session.get(PositionSnapshotRun, first).context_json)
    assert meta['fx']['CNH']['source'] == 'provider_frankfurter_v2_BI_USD_cross'
    assert meta['fx']['CNH']['rate_date'] == date.today().isoformat()
    assert len(http.calls) == 1
    # Fresh process, no in-memory last-good: unavailable FX must preserve DB facts.
    http.offline = True
    monkeypatch.setattr(fx_module.fx_service, 'get_quote', FXService().get_quote)
    client.get_account_info.return_value['cash_balances'][0]['amount'] = 100
    with pytest.raises(ValueError, match='FX_RATE_UNAVAILABLE'):
        service.sync_and_persist(db_session)
    db_session.expire_all()
    pos = db_session.query(Position).filter_by(broker='snowball').one()
    assert pos.id == original_id and pos.original_value == 30.25
    assert db_session.query(PositionSnapshotRun).order_by(PositionSnapshotRun.id.desc()).first().status == 'failed'
