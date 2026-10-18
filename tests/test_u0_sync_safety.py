"""U0 synthetic WAL races and source failures; no broker/network credentials."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, date
from decimal import Decimal
import json
from threading import Barrier
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Portfolio, Position as BusinessPosition
from app.fx_service import FXService, FXRateUnavailable
from services.broker_sync.models import PositionSnapshotRun, PositionSnapshot
from services.broker_sync.repository import PositionSnapshotRepository, SyncBusy
from services.broker_sync.schema import Position, cash_position


@pytest.fixture
def db(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'isolated.db'),connect_args={'timeout':10})
    with engine.connect() as c:c.exec_driver_sql('PRAGMA journal_mode=WAL')
    Base.metadata.create_all(engine)
    with Session(engine) as s:s.add_all([Portfolio(id=7,name='test'),Portfolio(id=8,name='other')]);s.commit()
    yield engine
    engine.dispose()


def source(qty=10,account='fixture'):
    return Position(broker='tiger',account_id=account,symbol='FIX:US',raw_symbol='FIX',name='Synthetic',
        asset_class='equity',vehicle_type='COMMON_STOCK',economic_asset_class='EQUITY',market='US',
        quantity=Decimal(qty),avg_cost=Decimal(1),cost_method='fifo',cost_basis=Decimal(qty),
        current_price=Decimal(1),market_value=Decimal(qty),currency='CNY',unrealized_pnl=Decimal(0),
        unrealized_pnl_pct=Decimal(0),snapshot_time=datetime.now(timezone.utc),sync_source='api')


def scoped(engine,pid=7):
    s=Session(engine);s.info['portfolio_id']=pid;return s


def sync(s,fetch,account='fixture',**kw):
    return PositionSnapshotRepository(s).synchronize('tiger',account,fetch,retries=0,**kw)


def test_p04_two_connections_race_one_active_claim(db):
    barrier=Barrier(2)
    def claim():
        with scoped(db) as s:
            barrier.wait()
            try:return PositionSnapshotRepository(s).create_run('tiger','fixture').id
            except SyncBusy:return 'busy'
    with ThreadPoolExecutor(2) as pool:result=list(pool.map(lambda _:claim(),range(2)))
    assert result.count('busy')==1
    with scoped(db) as s:assert s.query(PositionSnapshotRun).filter_by(status='running').count()==1


def test_p05_expired_late_worker_cannot_publish_over_new_owner_claim(db):
    old=scoped(db);repo=PositionSnapshotRepository(old);r=repo.create_run('tiger','fixture');old_id=r.id
    with scoped(db) as other:
        row=other.get(PositionSnapshotRun,old_id);meta=json.loads(row.context_json);meta['lease_until']='2000-01-01T00:00:00+00:00';row.context_json=json.dumps(meta);other.commit()
        sync(other,lambda:[source(20)])
    with pytest.raises(ValueError,match='EXPIRED_OR_REPLACED'):repo.persist_positions(old_id,[source(10)])
    old.rollback();repo.mark_run_failed(old_id,'late error');old.close()
    with scoped(db) as s:
        assert s.query(BusinessPosition).one().quantity==20
        assert s.query(PositionSnapshotRun).filter_by(status='success').count()==1


@pytest.mark.parametrize('failure',['empty','none','partial','offline','mixed','duplicate'])
def test_p06_p07_failed_source_keeps_last_good(db,failure):
    with scoped(db) as s:
        sync(s,lambda:[source(20)])
        def fetch():
            if failure=='offline':raise ConnectionError('never log fixture credentials')
            return {'empty':[],'none':None,'partial':[source(10)],'mixed':[source(10,'other')],
                    'duplicate':[source(10),source(20)]}[failure]
        with pytest.raises(ValueError):sync(s,fetch,metadata=(lambda:{'complete':False}) if failure=='partial' else None)
        s.expire_all()
        assert s.query(BusinessPosition).one().quantity==20
        assert s.query(PositionSnapshot).count()==1
        assert s.query(PositionSnapshotRun).order_by(PositionSnapshotRun.id.desc()).first().status=='failed'


def test_source_and_fx_io_allow_another_sqlite_writer(db,monkeypatch):
    from app.fx_service import fx_service
    original=fx_service.get_quote
    def assert_writer():
        with db.connect() as writer:writer.exec_driver_sql('BEGIN IMMEDIATE');writer.rollback()
    def quote(*args):assert_writer();return original(*args)
    monkeypatch.setattr(fx_service,'get_quote',quote)
    with scoped(db) as s:
        sync(s,lambda:(assert_writer() or [source()]))


def test_confirmed_empty_and_scope_do_not_clear_other_account_or_portfolio(db):
    with scoped(db) as s:
        sync(s,lambda:[source(20)])
        sync(s,lambda:[source(30,'second')],account='second')
        sync(s,lambda:[],metadata=lambda:{'complete':True,'empty_confirmed':True,'source':'confirmed_fixture'})
        assert [(p.broker_account_id,p.quantity) for p in s.query(BusinessPosition)]==[('second',30)]
    with scoped(db,8) as s:
        with pytest.raises(ValueError,match='AMBIGUOUS'):sync(s,lambda:[source(50,'second')],account='second')
        s.rollback()
        assert s.query(BusinessPosition).one().portfolio_id==7


def test_p03_fx_cache_expires_and_unknown_never_converts_one_to_one():
    clock=[0]
    class Provider:
        calls=0
        def fetch_rate(self,*_):self.calls+=1;return 6+self.calls,date.today().isoformat()
    provider=Provider();fx=FXService(provider,ttl=10,clock=lambda:clock[0])
    assert fx.get_rate('USD')==fx.get_rate('USD')==7
    clock[0]=11;assert fx.get_rate('USD')==8 and provider.calls==2
    class Offline:
        def fetch_rate(self,*_):raise OSError('offline')
    fx=FXService(Offline(),failure_ttl=1,clock=lambda:clock[0])
    assert fx.get_quote('CNH').rate is None
    with pytest.raises(FXRateUnavailable):fx.convert(100,'CNH')
    assert fx.get_quote('CNY').rate==1


def test_fx_stale_refresh_keeps_actual_source_date_not_now():
    clock=[0]
    class Provider:
        calls=0
        def fetch_rate(self,*_):
            self.calls+=1
            if self.calls>1:raise OSError('offline')
            return 7,date.today().isoformat()
    fx=FXService(Provider(),ttl=1,clock=lambda:clock[0]);first=fx.get_quote('USD');clock[0]=2
    stale=fx.get_quote('USD')
    assert stale.rate==first.rate and stale.fetched_at==first.fetched_at
    assert stale.rate_date==first.rate_date and stale.quality=='stale'
    assert fx.convert(100,'USD')[2]=='stale'


def test_p09_tiger_cash_persists_and_reads_never_call_broker(db,monkeypatch):
    from backend.services import portfolio_service
    from app import analyzer
    from pocs.chatgpt_skills.reader import read_details
    cash=cash_position('tiger','fixture','CNY',Decimal('100'),datetime.now(timezone.utc))
    with scoped(db) as s:sync(s,lambda:[source(20),cash])
    def forbidden():raise AssertionError('cash provider must never run during read')
    monkeypatch.setattr(portfolio_service,'_get_tiger_account_cash',forbidden)
    monkeypatch.setattr(portfolio_service,'get_session',lambda:scoped(db))
    monkeypatch.setattr(analyzer,'get_session',lambda:scoped(db))
    assert portfolio_service.get_summary(7)['total_assets']==120
    assert portfolio_service.get_summary(7)['total_assets']==120
    result=read_details(db.url.database,7,'get_investment_details',{},synthetic=True,cash_configured=True)
    assert result['portfolio_summary']['total_assets']==120
    assert sum(i['market_value_cny'] for i in result['items'])==120
    assert 'TIGER_CASH_NOT_SYNCED' not in result['issues']
    assert result['sources']['tiger_cash']=='stored_native_cash'
    filtered=read_details(db.url.database,7,'get_investment_details',{'symbol':'FIX:US'},synthetic=True,cash_configured=True)
    assert filtered['sources']['tiger_cash']=='stored_native_cash'
    assert filtered['portfolio_summary']['total_assets']==120


def test_cash_zero_negative_native_currency_and_base_are_distinct():
    stamp=datetime.now(timezone.utc)
    for value in [0,-5,15]:
        pos=cash_position('snowball','fixture','USD',value,stamp)
        assert pos.quantity==Decimal(value) and pos.market_value==Decimal(value)
    with pytest.raises(ValueError):cash_position('tiger','fixture','BASE',10,stamp)


def test_full_and_restricted_executors_share_database_claim(db):
    from backend.services.restricted_sync import SyncExecutor
    factory=lambda broker:None
    resolver=lambda db,broker:'fixture'
    first=SyncExecutor(['tiger'],7,factory,session_factory=lambda:scoped(db),account_resolver=resolver)
    second=SyncExecutor(['tiger'],7,factory,session_factory=lambda:scoped(db),account_resolver=resolver)
    jobs,busy=first.reserve('tiger','manual')
    assert jobs and not busy
    assert second.reserve('tiger','cron')==([],['tiger'])
    first._fail(jobs[0]['run_id'],'SYNC_SOURCE_FAILED')
    first.locks['tiger'].release()
    jobs,_=second.reserve('tiger','cron')
    assert jobs
    second._fail(jobs[0]['run_id'],'SYNC_SOURCE_FAILED');second.locks['tiger'].release()


def test_tiger_sdk_none_or_missing_cash_fails_without_publication(monkeypatch):
    from services.broker_sync.tiger.sync_service import TigerSyncService
    service=TigerSyncService.__new__(TigerSyncService);service.account_id='fixture'
    service.adapter=SimpleNamespace(to_positions=lambda rows,stamp:[source()])
    client=SimpleNamespace(get_positions=lambda **kw:None)
    service._ensure_trade_client=lambda:client
    with pytest.raises(ValueError,match='INCOMPLETE'):service.fetch_positions()
    client.get_positions=lambda **kw:[SimpleNamespace(account='fixture')] if kw['sec_type']=='STK' else []
    client.get_prime_assets=lambda **kw:SimpleNamespace(account='fixture',segments={'S':SimpleNamespace(currency_assets={})})
    with pytest.raises(ValueError,match='INCOMPLETE'):service.fetch_positions()
    client.get_prime_assets=lambda **kw:SimpleNamespace(account='wrong',segments={})
    with pytest.raises(ValueError,match='MIXED'):service.fetch_positions()


def test_tiger_sdk_cash_rows_are_native_and_readonly_guard_is_intrinsic(monkeypatch):
    from services.broker_sync.tiger.sync_service import TigerSyncService,ReadOnlyTradeClient
    service=TigerSyncService.__new__(TigerSyncService);service.account_id='fixture'
    service.adapter=SimpleNamespace(to_positions=lambda rows,stamp:[source()])
    calls=[]
    client=SimpleNamespace(get_positions=lambda **kw:([SimpleNamespace(account='fixture')] if kw['sec_type']=='STK' else []),
        get_prime_assets=lambda **kw:(calls.append(kw) or SimpleNamespace(account='fixture',segments={
            'S':SimpleNamespace(currency_assets={'USD':SimpleNamespace(cash_balance=Decimal('12.5')),
                'CNY':SimpleNamespace(cash_balance=0),'BASE':SimpleNamespace(cash_balance=9999)})})))
    service._ensure_trade_client=lambda:client
    rows=service.fetch_positions()
    assert calls==[{'account':'fixture'}]
    assert sorted((p.currency,p.quantity) for p in rows if p.broker_security_type=='CASH')==[('CNY',0),('USD',Decimal('12.5'))]
    from core.config import settings
    monkeypatch.setattr(settings,'tiger_read_only_mode',False)
    with pytest.raises(RuntimeError):getattr(ReadOnlyTradeClient(client),'place_order')


def test_uncovered_security_partition_is_retained_and_labelled(db):
    with scoped(db) as s:
        sync(s,lambda:[source(20)])
        old=s.query(BusinessPosition).one();old.broker_security_type='OPT';s.commit()
        run=sync(s,lambda:[cash_position('tiger','fixture','CNY',0,datetime.now(timezone.utc))],
            metadata=lambda:{'complete':True,'source':'tiger_api','coverage':['STK','FUND','CASH']})
        assert s.query(BusinessPosition).filter_by(symbol='FIX:US').one().quantity==20
        assert 'UNCOVERED_POSITIONS_RETAINED' in json.loads(s.get(PositionSnapshotRun,run).context_json)['issues']


def test_unknown_fx_keeps_last_good_and_stale_quote_is_explicit(db,monkeypatch):
    from app.fx_service import fx_service,FXQuote
    with scoped(db) as s:
        sync(s,lambda:[source(20)])
        monkeypatch.setattr(fx_service,'get_quote',lambda *_:FXQuote(None,None,'now','unavailable','unavailable',('FX_UNKNOWN',)))
        with pytest.raises(ValueError,match='FX_RATE_UNAVAILABLE'):sync(s,lambda:[source(5)])
        assert s.query(BusinessPosition).one().quantity==20
        monkeypatch.setattr(fx_service,'get_quote',lambda *_:FXQuote(7,'2020-01-01','2020-01-02','provider','stale',('FX_STALE',)))
        run=sync(s,lambda:[source(5).model_copy(update={'currency':'USD'})])
        p=s.query(BusinessPosition).one()
        assert p.quantity==5 and p.fx_rate_date=='stale'
        meta=json.loads(s.get(PositionSnapshotRun,run).context_json)
        assert meta['quality']=='partial' and meta['fx']['USD']['rate_date']=='2020-01-01'


def test_reserved_guojin_owner_is_used_without_env_or_response_rebinding(db,monkeypatch):
    from backend.services.restricted_sync import SyncExecutor
    from services.broker_sync.guojin.sync_service import GuojinSyncService
    monkeypatch.delenv('GUOJIN_ACCOUNT',raising=False)
    service=GuojinSyncService.__new__(GuojinSyncService);service.adapter=None
    service.fetch_positions=lambda:([source().model_copy(update={'broker':'guojin'})],{})
    executor=SyncExecutor(['guojin'],7,lambda _:service,session_factory=lambda:scoped(db),account_resolver=lambda *_:'fixture')
    jobs,_=executor.reserve('guojin');executor.execute('guojin',jobs[0]['run_id'])
    with scoped(db) as s:
        assert s.get(PositionSnapshotRun,jobs[0]['run_id']).status=='success'
        assert s.query(BusinessPosition).one().broker_account_id=='fixture'


def test_native_cash_duplicate_currency_cannot_publish(db):
    cash=cash_position('tiger','fixture','CNY',100,datetime.now(timezone.utc))
    with scoped(db) as s:
        sync(s,lambda:[cash])
        with pytest.raises(ValueError,match='DUPLICATE_SNAPSHOT_IDENTITY'):sync(s,lambda:[cash,cash])
        assert s.query(BusinessPosition).one().market_value_cny==100


def test_name_backfill_preserves_behavior_only_for_current_owner(db):
    with scoped(db) as s:
        s.add(BusinessPosition(portfolio_id=7,name='Donor',ticker='FIX',symbol='FIX:US',platform='manual',asset_class='权益',market_value_cny=1))
        s.add(BusinessPosition(portfolio_id=8,name='Other',ticker='FIX',symbol='FIX:US',platform='manual',asset_class='权益',market_value_cny=1))
        s.commit()
        sync(s,lambda:[source().model_copy(update={'name':'FIX'})])
        assert s.query(BusinessPosition).filter_by(broker='tiger').one().name=='Donor'
        assert s.query(BusinessPosition).filter_by(portfolio_id=8).one().name=='Other'


def test_unknown_fx_zero_cash_is_zero_without_invented_rate(db,monkeypatch):
    from app.fx_service import fx_service,FXQuote
    monkeypatch.setattr(fx_service,'get_quote',lambda *_:FXQuote(None,None,'now','unavailable','unavailable',('FX_UNKNOWN',)))
    with scoped(db) as s:
        run=sync(s,lambda:[cash_position('tiger','fixture','CNH',0,datetime.now(timezone.utc))])
        pos=s.query(BusinessPosition).one()
        assert pos.original_value==0 and pos.market_value_cny==0 and pos.fx_rate_to_cny is None
        assert json.loads(s.get(PositionSnapshotRun,run).context_json)['quality']=='partial'


def test_manual_all_records_unconfigured_source_without_blocking_other_sources(db):
    from backend.services.restricted_sync import SyncExecutor
    class Service:
        def sync_and_persist(self,s,triggered_by):
            return sync(s,lambda:[source()],triggered_by=triggered_by)
    executor=SyncExecutor(['futu','tiger'],7,lambda _:Service(),session_factory=lambda:scoped(db),
        account_resolver=lambda _,broker:'fixture' if broker=='tiger' else None)
    jobs,busy=executor.reserve('all')
    assert [j['broker'] for j in jobs]==['tiger'] and not busy
    executor.execute('tiger',jobs[0]['run_id'])
    with scoped(db) as s:
        assert s.query(PositionSnapshotRun).filter_by(broker='futu').one().status=='failed'
        assert s.query(PositionSnapshotRun).filter_by(broker='tiger').one().status=='success'
