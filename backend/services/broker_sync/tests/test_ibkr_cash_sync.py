"""IBKR cash persists through the existing scoped snapshot/upsert path."""
from unittest.mock import MagicMock, patch
from decimal import Decimal
import pytest
from core.config import settings
from services.broker_sync.snowball.sync_service import SnowballSyncService
from app.models import Position, Portfolio
from backend.services.broker_sync.tests.test_sync_service_persistence import db_session  # noqa: F401


def client():
    c=MagicMock(broker_name='ibkr')
    c.get_account_info.return_value={'account_id':'U1234567','cash_balances':[
        {'currency':'USD','amount':120},{'currency':'CNH','amount':30},{'currency':'BASE','amount':125}]}
    c.get_positions.return_value=[]
    return c


def configure(monkeypatch):
    monkeypatch.setenv('BROKER_MODE','tiger.live')
    monkeypatch.setattr(settings,'ibkr_account','U1234567')
    monkeypatch.setattr(settings,'snowball_secret_key',None)


def test_tiger_primary_mode_uses_existing_dedicated_ibkr(monkeypatch):
    configure(monkeypatch); c=client()
    with patch('backend.api.action._get_ibkr_adapter',return_value=c) as get:
        service=SnowballSyncService()
    get.assert_called_once()
    assert service._source_channel=='ibkr' and service._client is c


def test_mismatched_account_blocks_before_positions(monkeypatch):
    configure(monkeypatch); c=client();c.get_account_info.return_value['account_id']='U9999999'
    with pytest.raises(RuntimeError,match='ACCOUNT_MISMATCH'):SnowballSyncService(c).fetch_positions()
    c.get_positions.assert_not_called()


def test_explicit_snowball_configuration_is_preserved(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(settings,'snowball_account','snowball-test')
    monkeypatch.setattr(settings,'snowball_secret_key','synthetic-secret')
    with patch.object(SnowballSyncService,'_build_client',return_value=MagicMock()):
        assert SnowballSyncService()._source_channel=='snowball'


def test_cash_upsert_is_idempotent_and_scoped(db_session,monkeypatch):
    configure(monkeypatch); c=client(); service=SnowballSyncService(c)
    db_session.add(Portfolio(id=1,name='Synthetic'))
    other=Position(portfolio_id=1,name='Other cash',ticker='USD',platform='老虎证券',asset_class='现金',
                   quantity=50,market_value_cny=350,original_value=50,original_currency='USD',
                   broker='tiger',broker_account_id='other',sync_source='api')
    db_session.add(other);db_session.commit()
    with patch('services.broker_sync.position_upsert_service.fx_service.convert',
               side_effect=lambda amount,*args:(amount*7,7,'2026-10-16')):
        service.sync_and_persist(db_session)
        service.sync_and_persist(db_session)
        rows=db_session.query(Position).filter_by(broker='snowball',broker_account_id='U1234567').all()
        assert len(rows)==2
        assert sorted(float(r.original_value) for r in rows)==[30,120]
        assert sum(Decimal(str(r.market_value_cny)) for r in rows)==1050
        assert db_session.get(Position,other.id).original_value==50
        c.get_account_info.return_value['cash_balances']=[{'currency':'USD','amount':0}]
        service.sync_and_persist(db_session)
    assert db_session.query(Position).filter_by(broker='snowball',broker_account_id='U1234567').count()==0
    assert db_session.get(Position,other.id).original_value==50
