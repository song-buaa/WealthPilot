"""U1 task identity, original sync, frozen services and permissions; synthetic only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Portfolio, Position, WealthItem, WealthSnapshot
from app.asset_views import load_view, public_view, frozen_input, frozen_result
from backend.services.asset_analysis import AssetAnalysisPreparation
from tests.test_analysis_skills import peer  # noqa: F401 - original isolated PEER fixture


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'backend'))
    monkeypatch.setenv('WEALTHPILOT_ANALYSIS_CACHE_DIR',str(tmp_path/'inputs'))
    from services.broker_sync.schema import cash_position
    from services.broker_sync.repository import PositionSnapshotRepository
    from services.broker_sync.models import PositionSnapshotRun
    from backend.services.restricted_sync import SyncExecutor
    from app.fx_service import fx_service, FXQuote
    monkeypatch.setattr(fx_service,'get_quote',lambda *_:FXQuote(1,date.today().isoformat(),'fixture','fixture','current'))
    db=tmp_path/'synthetic.db';engine=create_engine('sqlite:///'+str(db),connect_args={'timeout':10})
    with engine.connect() as conn:conn.exec_driver_sql('PRAGMA journal_mode=WAL')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([Portfolio(id=7,name='Synthetic'),Portfolio(id=8,name='OTHER_PRIVATE')]);session.commit()
    state=SimpleNamespace(calls=0, offline=False, cash=1000, empty=False, error_code=None)
    def sessions():return Session(engine,info={'portfolio_id':7})
    def factory(broker):
        def sync(session,triggered_by='manual'):
            def fetch():
                state.calls+=1
                if state.offline:raise ConnectionError('isolated source unavailable')
                if state.error_code:raise ValueError(state.error_code)
                if state.empty:return []
                return [cash_position(broker,'fixture','CNY',state.cash,datetime.now(timezone.utc))]
            return PositionSnapshotRepository(session).synchronize(broker,'fixture',fetch,retries=0,
                triggered_by=triggered_by,metadata=lambda:dict(source='fixture',complete=True,empty_confirmed=state.empty,
                    cash_as_of=datetime.now(timezone.utc).isoformat(),positions_as_of=datetime.now(timezone.utc).isoformat()))
        return SimpleNamespace(sync_and_persist=sync)
    def service():
        executor=SyncExecutor(['tiger'],7,factory,session_factory=sessions,account_resolver=lambda *_:'fixture')
        return AssetAnalysisPreparation(executor,db,synthetic=True)
    yield SimpleNamespace(db=db,engine=engine,sessions=sessions,state=state,service=service,Run=PositionSnapshotRun)
    engine.dispose()


def ready(c, key='task1'):
    service=c.service();pending,claim=service.request(key)
    assert claim is not None
    service.run(*claim)
    status=service.status(key)
    assert status['state']=='ready',status
    return load_view(c.db,7,status['snapshot_id'])


def test_prepare_idempotency_and_immutable_views(context):
    c=context;service=c.service();pending,claim=service.request('same-task')
    assert pending['state']=='preparing' and c.state.calls==0
    assert service.request('same-task')[1] is None
    service.run(*claim)
    status=service.status('same-task');assert status['state']=='ready',status
    view=load_view(c.db,7,status['snapshot_id']);original=json.dumps(view,sort_keys=True)
    assert view['views']['wealth_summary']['total_assets']==1000
    with c.sessions() as session:
        pos=session.query(Position).one();pos.market_value_cny=2000;session.commit()
    assert json.dumps(load_view(c.db,7,status['snapshot_id']),sort_keys=True)==original
    assert service.request('same-task')[1] is None and c.state.calls==1
    next_view=ready(c,'different-task')
    assert next_view['snapshot_id']!=view['snapshot_id']
    assert c.state.calls==2  # local facts diverged: original sync must restore/verify
    assert next_view['views']['wealth_summary']['total_assets']==1000
    assert all('account_id' not in a and 'account' not in a for a in public_view(view)['data_quality']['accounts'])
    assert 'internal' not in public_view(view)


def test_fixed_scope_does_not_read_unbound_consumption_cashflow(context,monkeypatch):
    c=context
    from backend.services import wealth_service
    from pocs.chatgpt_skills.analysis import readonly_business_database
    def forbidden(*args):
        raise AssertionError('global consumption is not Portfolio-owned')
    monkeypatch.setattr(wealth_service,'_cashflow_for_month',forbidden)
    with readonly_business_database(c.db):
        summary=wealth_service.get_summary(7,record_snapshot=False,include_cashflow=False)
    assert summary['attribution']['cashflow_available'] is False
    assert summary['attribution']['cashflow_income'] is None
    view=ready(c)
    assert view['views']['wealth_summary']['attribution']['cashflow_available'] is False


def test_source_clock_expires_before_new_capture_clock(context,monkeypatch):
    c=context;view=ready(c)
    from app import asset_views
    captured=datetime.fromisoformat(view['captured_at'])
    source=(captured-timedelta(seconds=290)).isoformat()
    view['data_quality'].update(status='ok',issues=[],can_propose_rebalance=True,
        accounts=[{'account_synced_at':source,'cash_as_of':source,'time_quality':{'cash_as_of':'current'}}])
    monkeypatch.setattr(asset_views,'now',lambda:captured+timedelta(seconds=20))
    current=public_view(view)
    assert current['context_current'] is True
    assert current['data_quality']['can_propose_rebalance'] is False
    assert 'STALE_ACCOUNT_SYNC' in current['data_quality']['issues']
    assert 'STALE_SOURCE_TIME' in current['data_quality']['issues']


def test_two_connections_only_one_claim_for_same_task(context):
    barrier=Barrier(2);c=context
    def request():
        service=c.service();barrier.wait();return service.request('racing-task')
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:request(),range(2)))
    assert sum(claim is not None for _,claim in results)==1
    with c.sessions() as session:assert session.query(WealthSnapshot).filter_by(analysis_request_id='racing-task').count()==1
    assert c.state.calls==0


@pytest.mark.parametrize('failure',['offline','fx'])
def test_failed_refresh_keeps_last_good_and_cooldown(context,monkeypatch,failure):
    c=context;first=ready(c)
    with c.sessions() as session:
        row=session.query(c.Run).one();row.finished_at=datetime.now(timezone.utc)-timedelta(seconds=400);session.commit()
    if failure=='offline':c.state.offline=True
    else:
        from app.fx_service import fx_service, FXQuote
        monkeypatch.setattr(fx_service,'get_quote',lambda *_:FXQuote(None,None,'fixture','unavailable','unavailable',('FX_UNKNOWN',)))
    c.state.cash=2000
    view=ready(c,'failed-refresh')
    assert view['views']['wealth_summary']['total_assets']==1000
    assert view['data_quality']['status']=='partial'
    assert view['data_quality']['can_propose_rebalance'] is False
    assert 'LATEST_POSITION_SYNC_FAILED' in view['data_quality']['issues']
    assert view['data_quality']['accounts'][0]['last_attempt_reason_code']==(
        'FX_RATE_UNAVAILABLE' if failure=='fx' else 'SYNC_SOURCE_FAILED')
    assert c.state.calls==2
    ready(c,'cooldown-task')
    assert c.state.calls==2
    with c.sessions() as session:assert session.query(Position).one().original_value==1000


def test_fixed_clone_scope_expiry_tampering_and_readonly(context,monkeypatch):
    c=context;view=ready(c)
    with pytest.raises(ValueError,match='NOT_FOUND'):load_view(c.db,8,view['snapshot_id'])
    with pytest.raises(ValueError,match='NOT_FOUND'):load_view(c.db,7,'../../private')
    from pocs.chatgpt_skills.analysis import readonly_business_database
    from app.database import get_session
    with frozen_input(view) as path, readonly_business_database(path):
        def denied():
            with get_session() as session:
                with pytest.raises(Exception):session.execute(text('UPDATE positions SET quantity=0'));session.commit()
        denied();asyncio.run(asyncio.to_thread(denied))
    expired={**view,'captured_at':(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()}
    with pytest.raises(ValueError,match='EXPIRED'):
        with frozen_input(expired):pass
    with frozen_input(view) as path:
        with path.open('ab') as stream:stream.write(b'corruption')
    with pytest.raises(ValueError,match='INVALID'):
        with frozen_input(view):pass
    assert load_view(c.db,7,view['snapshot_id'])['views']['wealth_summary']['total_assets']==1000


def test_manual_pension_cash_and_trend_use_original_calculation(context):
    c=context
    with c.sessions() as session:
        session.add_all([
            WealthItem(portfolio_id=7,kind='ASSET',name='Pension',item_type='personal_pension',category='retirement_long_term',
                       current_value=100,already_investment_accounted=True,included_in_net_worth=False),
            WealthItem(portfolio_id=7,kind='ASSET',name='Bank',item_type='bank_cash',category='cash_deposits',current_value=20),
            WealthItem(portfolio_id=7,kind='LIABILITY',name='Loan',item_type='consumer_loan',category='consumer_loan',current_value=30),
            Position(portfolio_id=8,name='OTHER_PRIVATE',platform='fixture',asset_class='现金',market_value_cny=999999)])
        session.commit()
    view=ready(c);wealth=view['views']['wealth_summary']
    assert (wealth['total_assets'],wealth['total_liabilities'],wealth['net_worth'],wealth['pension_benefit'])==(920,30,890,100)
    assert 'OTHER_PRIVATE' not in json.dumps(public_view(view))
    assert 'PENSION_HOLDING_LINK_UNVERIFIED' in view['data_quality']['issues']
    assert frozen_result(view,'get_manual_wealth_items',{'kind':'LIABILITY'})['items'][0]['name']=='Loan'
    assert wealth['trend']==[{'date':date.today().isoformat(),'net_worth':890}]


def test_same_issuer_exposure_never_combines_quantities_or_guesses():
    from backend.services.instruments.issuers import exposure
    items=[dict(id=1,symbol='LI:US',account_ref='a',quantity=10,cost_price=20,currency='USD',original_currency='USD',
                vehicle_type='ADR',economic_asset_class='EQUITY',market_value_cny=1500),
           dict(id=2,symbol='2015:HK',account_ref='b',quantity=100,cost_price=100,currency='HKD',original_currency='HKD',
                vehicle_type='COMMON_STOCK',economic_asset_class='EQUITY',market_value_cny=1400),
           dict(id=3,symbol='LI:HK',currency='HKD',vehicle_type='COMMON_STOCK',economic_asset_class='EQUITY',market_value_cny=50),
           dict(id=4,symbol='LI:US',currency='CNY',vehicle_type='COMMON_STOCK',economic_asset_class='EQUITY',market_value_cny=60)]
    result=exposure(items);company=result['companies'][0]
    assert company['market_value_cny']==2900 and company['economic_region']=='CN'
    assert [m['quantity'] for m in company['members']]==[10,100]
    assert [m['cost_price'] for m in company['members']]==[20,100]
    assert len(result['unmapped_direct_holdings'])==2 and 'quantity' not in company


def test_task_context_change_is_not_same_request(context):
    c=context;ready(c)
    service=c.service();service.executor.account_resolver=lambda *_:'changed-owner'
    with pytest.raises(ValueError,match='CONFLICT'):service.request('task1')


def test_expired_claim_cannot_publish_or_retry_under_same_id(context):
    c=context;service=c.service();_,claim=service.request('abandoned')
    with c.sessions() as session:
        row=session.query(WealthSnapshot).one();payload=json.loads(row.payload_json)
        payload['internal']['lease_until']='2000-01-01T00:00:00+00:00';row.payload_json=json.dumps(payload);session.commit()
    service.run(*claim)
    assert service.request('abandoned')[1] is None
    assert service.status('abandoned')['state']=='expired' and c.state.calls==0


def test_peer_uses_frozen_input_after_live_change(context,peer):
    c=context;view=ready(c)
    with c.sessions() as session:
        pos=session.query(Position).one();pos.market_value_cny=9000;session.commit()
    from pocs.chatgpt_skills.analysis import analyze
    result=asyncio.run(analyze(c.db,7,'review_portfolio',{'question':'review','snapshot_id':view['snapshot_id']},synthetic=True))
    assert result['snapshot_id']==view['snapshot_id']
    assert result['scope']['investment_holdings_total_cny']==1000
    assert result['evidence']['asset_view']['wealth_summary']['total_assets']==1000
    assert result['order_created'] is False


def test_expired_u0_claim_is_recovered_without_cooldown_rollback(context):
    c=context;ready(c)
    from services.broker_sync.repository import PositionSnapshotRepository
    with c.sessions() as session:
        run=PositionSnapshotRepository(session).create_run('tiger','fixture')
        old_id=run.id;meta=json.loads(run.context_json);meta['lease_until']='2000-01-01T00:00:00+00:00'
        run.context_json=json.dumps(meta);session.commit()
    ready(c,'recover-claim')
    assert c.state.calls==2
    with c.sessions() as session:
        assert session.get(c.Run,old_id).status=='failed'
        assert session.query(c.Run).filter_by(status='running').count()==0


def test_two_different_tasks_race_fast_source_only_one_refresh(context):
    c=context;barrier=Barrier(2)
    def reserve():
        service=c.service();barrier.wait()
        jobs,_=service.executor.reserve('tiger','analysis',freshness_seconds=300)
        for job in jobs:service.executor.execute(job['broker'],job['run_id'])
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:reserve(),range(2)))
    assert c.state.calls==1
    # Independent executor after a fast completion must reuse its observation.
    jobs,busy=c.service().executor.reserve('tiger','analysis',freshness_seconds=300)
    assert jobs==[] and busy==[] and c.state.calls==1


def test_cache_expiry_cleanup_respects_active_analysis_lock(context):
    import os
    from app.asset_views import cache_root,cleanup
    c=context;view=ready(c)
    with frozen_input(view) as path:
        os.utime(path,(1,1));cleanup(cache_root());assert path.exists()
    cleanup(cache_root());assert not path.exists()
    with pytest.raises(ValueError,match='EXPIRED'):
        with frozen_input(view):pass
    assert load_view(c.db,7,view['snapshot_id'])['views']['wealth_summary']['total_assets']==1000


def test_task_rate_limit_and_expired_ready_do_not_trigger_new_sync(context):
    c=context
    service=c.service()
    for i in range(6):service.request('pending'+str(i))
    with pytest.raises(ValueError,match='RATE_LIMITED'):service.request('seventh')
    assert service.request('pending0')[1] is None and c.state.calls==0


def test_source_times_never_use_capture_or_sync_time_as_price_evidence():
    from pocs.chatgpt_skills.asset_view import time_quality
    assert time_quality(None,300)=='unknown'
    assert time_quality(date.today().isoformat(),300)=='unknown'
    assert time_quality(date.today().isoformat(),86400,daily=True)=='current'
    assert time_quality((datetime.now(timezone.utc)-timedelta(days=5)).isoformat(),86400,daily=True)=='stale'
    assert time_quality((datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),86400,daily=True)=='stale'
    assert time_quality(datetime.now().isoformat(),300)=='unknown'


def test_stale_view_remains_same_facts_but_not_precise_rebalance(context):
    c=context;view=ready(c)
    stale={**view,'captured_at':(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()}
    public=public_view(stale)
    assert public['views']==view['views'] and public['context_current'] is True
    assert public['data_quality']['can_propose_rebalance'] is False
    assert 'STALE_ANALYSIS_VIEW' in public['data_quality']['issues']
    result=frozen_result(stale,'get_investment_details',{})
    assert result['status']=='partial' and 'STALE_ANALYSIS_VIEW' in result['issues']
