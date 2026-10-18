"""PR #22 R01/R02/I01: real SDK contracts, isolated publication assertions."""
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from tigeropen.trade.domain.contract import Contract
from tigeropen.trade.domain.position import Position as SDKPosition
from tigeropen.trade.domain.prime_account import PortfolioAccount, Segment, CurrencyAsset

from app.database import Base
from app.models import Portfolio, Position
from app.fx_service import fx_service, FXQuote
from services.broker_sync.models import PositionSnapshotRun, PositionSnapshot
from services.broker_sync.repository import PositionSnapshotRepository
from services.broker_sync.position_upsert_service import PositionUpsertService
from services.broker_sync.schema import cash_position
from services.broker_sync.tiger.adapter import TigerAdapter
from services.broker_sync.tiger.sync_service import TigerSyncService


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(fx_service, 'get_quote', lambda *_:FXQuote(1,date.today().isoformat(),'synthetic','fixture','current'))
    engine=create_engine('sqlite:///'+str(tmp_path/'review.db'))
    Base.metadata.create_all(engine)
    with Session(engine,info={'portfolio_id':7}) as session:
        session.add_all([Portfolio(id=7,name='fixture'),Portfolio(id=8,name='other')]);session.commit()
        yield session
    engine.dispose()


def stock(account='fixture'):
    return SDKPosition(account=account,contract=Contract('FIX','USD',sec_type='STK',market='US'),
        quantity=10,average_cost=1,market_price=1,market_value=10,realized_pnl=0,unrealized_pnl=0,unrealized_pnl_percent=0)


def assets(account='fixture',gross=10):
    result=PortfolioAccount(account);segment=Segment();segment.category='S';segment.gross_position_value=gross
    segment.add_currency_asset(CurrencyAsset.from_dict({'currency':'CNY','cash_balance':100}))
    result.add_segment(segment)
    return result


def service(stk, fund=None, summary=None):
    result=TigerSyncService.__new__(TigerSyncService);result.account_id='fixture';result.adapter=TigerAdapter('fixture')
    result.MAX_RETRIES=0
    result._ensure_trade_client=lambda:SimpleNamespace(
        get_positions=lambda **kw:stk if kw['sec_type']=='STK' else ([] if fund is None else fund),
        get_prime_assets=lambda **_:summary if summary is not None else assets())
    return result


def facts(db):
    return [tuple(getattr(p,c.name) for c in Position.__table__.columns) for p in db.query(Position).order_by(Position.id)]


@pytest.mark.parametrize('case',['wrong','mixed','fund_wrong','missing','blank','cash_wrong','cash_missing'])
def test_r02_source_owner_failures_keep_last_good(db,case):
    service([stock()]).sync_and_persist(db);before=facts(db)
    inputs={
        'wrong':([stock('other')],[],assets()),
        'mixed':([stock(),stock('other')],[],assets()),
        'fund_wrong':([stock()],[stock('other')],assets()),
        'missing':([stock(None)],[],assets()),
        'blank':([stock(' ')],[],assets()),
        'cash_wrong':([stock()],[],assets('other')),
        'cash_missing':([stock()],[],assets(None)),
    }
    with pytest.raises(ValueError,match='MIXED_SNAPSHOT_SCOPE|SOURCE_ACCOUNT_UNPROVEN'):
        service(*inputs[case]).sync_and_persist(db)
    assert facts(db)==before and db.query(PositionSnapshot).count()==2
    assert db.query(PositionSnapshotRun).order_by(PositionSnapshotRun.id.desc()).first().status=='failed'


def test_r02_adapter_does_not_relabel_source_account():
    with pytest.raises(ValueError,match='MIXED_SNAPSHOT_SCOPE'):TigerAdapter('fixture').to_position(stock('other'))
    with pytest.raises(ValueError,match='SOURCE_ACCOUNT_UNPROVEN'):TigerAdapter('fixture').to_position(stock(None))
    assert TigerAdapter('fixture').to_position(stock()).account_id=='fixture'


def test_i01_real_sdk_cash_only_account_and_legitimate_liquidation(db):
    service([stock()]).sync_and_persist(db)
    original_cash_id=db.query(Position).filter_by(vehicle_type='CASH').one().id
    service([],summary=assets(gross=0)).sync_and_persist(db)
    remaining=db.query(Position).all()
    assert len(remaining)==1 and remaining[0].id==original_cash_id
    assert remaining[0].original_value==100 and remaining[0].broker_account_id=='fixture'
    assert db.query(PositionSnapshotRun).order_by(PositionSnapshotRun.id.desc()).first().status=='success'


@pytest.mark.parametrize('gross',[None,float('inf'),float('nan'),10,-10])
def test_i01_invalid_empty_source_cannot_remove_last_good(db,gross):
    service([stock()]).sync_and_persist(db);before=facts(db)
    with pytest.raises(ValueError,match='EMPTY_SOURCE_REQUIRES_REVIEW'):
        service([],summary=assets(gross=gross)).sync_and_persist(db)
    assert facts(db)==before


def legacy_cash(**kwargs):
    fields=dict(portfolio_id=7,name='Synthetic manual cash',ticker='CASH-CNY',symbol='CASH-CNY:CN',
        platform='老虎证券',asset_class='货币',vehicle_type='CASH',economic_asset_class='CASH',
        broker_security_type='CASH',segment='投资',currency='CNY',original_value=100,market_value_cny=100)
    return Position(**{**fields,**kwargs})


def sync_cash(db,amount=100):
    return PositionSnapshotRepository(db).synchronize('tiger','fixture',
        lambda:[cash_position('tiger','fixture','CNY',amount,datetime.now(timezone.utc))],retries=0)


@pytest.mark.parametrize('source',[None,'csv_upload','statement','manual','api'])
def test_r01_first_cash_collision_and_retries_preserve_original_facts(db,source):
    db.add(legacy_cash(sync_source=source));db.commit();before=facts(db)
    for amount in [100,80,80]:
        with pytest.raises(ValueError,match='CASH_IDENTITY_COLLISION_REQUIRES_REVIEW'):sync_cash(db,amount)
        assert facts(db)==before
    assert db.query(PositionSnapshot).count()==0
    assert db.query(PositionSnapshotRun).filter_by(status='success').count()==0


def test_r01_existing_success_cannot_be_used_to_claim_new_manual_cash(db):
    sync_cash(db,80)
    db.add(legacy_cash());db.commit();before=facts(db)
    for _ in range(2):
        with pytest.raises(ValueError,match='CASH_IDENTITY_COLLISION_REQUIRES_REVIEW'):sync_cash(db,70)
    assert facts(db)==before
    PositionUpsertService(db)._claim_legacy_positions_from_history(broker='tiger',account_id='fixture',sync_source='api')
    db.commit()
    assert facts(db)==before


@pytest.mark.parametrize('source',['api','csv_upload'])
def test_r01_existing_duplicate_or_imported_same_owner_cash_is_not_overwritten(db,source):
    sync_cash(db,80)
    db.add(legacy_cash(broker='tiger',broker_account_id='fixture',sync_source=source));db.commit();before=facts(db)
    with pytest.raises(ValueError,match='CASH_IDENTITY_COLLISION_REQUIRES_REVIEW'):sync_cash(db,70)
    assert facts(db)==before


def test_r01_independent_known_owner_and_money_fund_are_not_claimed(db):
    independent=legacy_cash(broker='tiger',broker_account_id='other-manual-owner',sync_source='manual')
    fund=legacy_cash(name='Money fund',ticker='FUND',symbol='FUND:CN',vehicle_type='FUND',broker_security_type='FUND')
    db.add_all([independent,fund]);db.commit();before={p.id:tuple(getattr(p,c.name) for c in Position.__table__.columns) for p in [independent,fund]}
    for amount in [100,80,80]:sync_cash(db,amount)
    for key,expected in before.items():
        assert tuple(getattr(db.get(Position,key),c.name) for c in Position.__table__.columns)==expected
    assert db.query(Position).filter_by(broker='tiger',broker_account_id='fixture').count()==1


@pytest.mark.parametrize('ticker',[None,'USD'])
@pytest.mark.parametrize('asset_class',['货币','现金','cash'])
def test_r01_untyped_legacy_cash_requires_owner_evidence(db,ticker,asset_class):
    row=legacy_cash(ticker=ticker,symbol=None,vehicle_type=None,broker_security_type=None,
        economic_asset_class=None,currency='USD',asset_class=asset_class)
    db.add(row);db.commit();before=facts(db)
    with pytest.raises(ValueError,match='CASH_IDENTITY_COLLISION_REQUIRES_REVIEW'):
        PositionSnapshotRepository(db).synchronize('tiger','fixture',lambda:[
            cash_position('tiger','fixture','USD',100,datetime.now(timezone.utc))],retries=0)
    assert facts(db)==before
