"""Original B0 observation -> U0 publish -> U1/MCP contract, synthetic WAL only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Portfolio
from app.fill_sync import decode, encode
from app.trade_status import query
from backend.services.action.models import OrderRecord, SymbolStrategy
from backend.services.action.order_manager import OrderManager
from backend.services.action.brokers.base import OrderStatusUpdate


@pytest.fixture
def context(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'backend'))
    from services.broker_sync.repository import PositionSnapshotRepository
    from services.broker_sync.models import PositionSnapshotRun
    from services.broker_sync.schema import Position, cash_position
    from backend.services.restricted_sync import SyncExecutor
    from app.fx_service import fx_service, FXQuote
    monkeypatch.setattr(fx_service,'get_quote',lambda *_:FXQuote(1,date.today().isoformat(),'synthetic','synthetic','current'))
    db=tmp_path/'u2.db';engine=create_engine('sqlite:///'+str(db),connect_args={'timeout':10})
    with engine.connect() as conn:conn.exec_driver_sql('PRAGMA journal_mode=WAL')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([Portfolio(id=7,name='Synthetic'),Portfolio(id=8,name='Other')]);session.commit()
    state=SimpleNamespace(quantity=10,cash=1000,cost=10,calls=0,offline=False,before_publish=None,trading='ibkr',source_clock='current')
    def sessions():return Session(engine,info={'portfolio_id':7})
    def factory(broker):
        def sync(session,triggered_by='manual'):
            def fetch():
                state.calls+=1
                # A second connection can take the write lock DURING source IO.
                with sqlite3.connect(db,timeout=.1) as other:
                    other.execute('BEGIN IMMEDIATE');other.rollback()
                if state.offline:raise ConnectionError('DO_NOT_EXPOSE_ACCOUNT_CONNECTION')
                now=datetime.now(timezone.utc)
                result=[cash_position(broker,'synthetic-owner','USD',state.cash,now)]
                if state.quantity:
                    result.append(Position(broker=broker,account_id='synthetic-owner',symbol='ABC:US',
                        raw_symbol='ABC',name='Synthetic',asset_class='equity',market='US',currency='USD',
                        quantity=Decimal(state.quantity),avg_cost=Decimal(state.cost),cost_basis=Decimal(state.quantity)*Decimal(state.cost),
                        cost_method='weighted_average',current_price=Decimal(11),market_value=Decimal(state.quantity)*11,
                        unrealized_pnl=Decimal(state.quantity)*(11-Decimal(state.cost)),unrealized_pnl_pct=Decimal('.1'),
                        vehicle_type='COMMON_STOCK',economic_asset_class='EQUITY',snapshot_time=now,sync_source='api',raw_data={'con_id':111}))
                if state.before_publish:state.before_publish()
                return result
            return PositionSnapshotRepository(session).synchronize(broker,'synthetic-owner',fetch,retries=0,
                triggered_by=triggered_by,metadata=lambda:dict(source='synthetic',complete=True,
                    positions_as_of=None if state.source_clock=='unknown' else (datetime.now(timezone.utc)-timedelta(seconds=60 if state.source_clock=='cached' else 0)).isoformat(),
                    cash_as_of=None if state.source_clock=='unknown' else (datetime.now(timezone.utc)-timedelta(seconds=60 if state.source_clock=='cached' else 0)).isoformat(),
                    price_as_of=datetime.now(timezone.utc).isoformat()))
        return SimpleNamespace(sync_and_persist=sync)
    def source():return 'snowball' if state.trading=='ibkr' else 'tiger'
    def executor():return SyncExecutor([source()],7,factory,session_factory=sessions,account_resolver=lambda *_:'synthetic-owner')
    def publish():
        e=executor();jobs,_=e.reserve(source());return e.execute(**jobs[0])
    class Broker:
        @property
        def broker_name(self):return state.trading
        _account_id='synthetic-owner'
        def place_order(self,request):
            with sqlite3.connect(db,timeout=.1) as other:
                other.execute('BEGIN IMMEDIATE');other.rollback()
            return OrderStatusUpdate('broker-'+request.local_order_id,request.local_order_id,'broker_pending')
    def dispatch(quantity=10,side='BUY'):
        with sessions() as session:
            manager=OrderManager(session,Broker())
            draft=manager.create_draft('synthetic-conversation',payload={'symbol_strategies':[dict(symbol='ABC:US',side=side,quantity=quantity,limit_price=10)]})
            session.flush()
            strategy=manager.confirm_draft(draft.id)[0]
            session.commit()
            params=dict(quantity=quantity,limit_price=10,resolved_contract={'con_id':111,'currency':'USD'})
            review=manager.create_order_review(strategy.id,params,risk_snapshot={'passed':True,'requires_confirmation':False,'warnings':[]})
            session.commit()
            result=manager.place_order(strategy.id,{**params,'review_id':review})
            return result.id
    def observe(order_id,quantity,*,fees=True,exec_id=None,shares=None,status=None,actual_time='native'):
        with sessions() as session:
            order=session.get(OrderRecord,order_id)
            prior=int(order.filled_quantity or 0)
            raw=dict(observed_account_id='synthetic-owner',con_id=111,contract_currency='USD',perm_id=1)
            if quantity:
                identity=exec_id or order_id+'-'+str(quantity)
                prior_execution=decode(order.fill_sync_json).get('executions',{}).get(identity,{})
                raw['execution_evidence']=[dict(exec_id=exec_id or order_id+'-'+str(quantity),order_ref=order_id,
                    observed_account_id='synthetic-owner',con_id=111,shares=shares if shares is not None else quantity-prior,
                    price=10,commission=1 if fees else None,commission_currency='USD' if fees else None,
                    timestamp=(prior_execution.get('timestamp') or datetime.now(timezone.utc).isoformat()) if actual_time=='native' else actual_time)]
            result=OrderManager(session,Broker()).apply_broker_observation(order_id,OrderStatusUpdate(
                order.broker_order_id,order_id,status or ('filled' if quantity==order.quantity else 'partially_filled'),
                filled_quantity=quantity,avg_filled_price=Decimal(10) if quantity else None,raw_response=raw))
            session.commit()
            return result.id
    def drain():
        with sessions() as session:
            for row in session.query(OrderRecord).all():
                p=decode(row.fill_sync_json)
                if p:p['next_attempt_at']=0;row.fill_sync_json=encode(p)
            session.commit()
        return executor().sync_fills()
    def status(order_id):
        with sessions() as session:return query(session,7,order_id=order_id)['items'][0]
    yield SimpleNamespace(**locals())
    engine.dispose()


def test_first_partial_fill_and_duplicate_observation(context):
    c=context;c.publish();order=c.dispatch()
    c.observe(order,2);assert c.status(order)['asset_sync']['status']=='SYNC_PENDING'
    c.state.quantity=12;c.state.cash=979
    assert len(c.drain())==1
    assert c.status(order)['trade_status']=='partially_filled'
    assert c.status(order)['asset_sync']['status']=='SYNCED'
    calls=c.state.calls
    c.observe(order,2,shares=2)
    assert c.executor().sync_fills()==[] and c.state.calls==calls


def test_partial_progress_and_final_fill_are_cumulative(context):
    c=context;c.publish();order=c.dispatch(5)
    c.observe(order,2);c.state.quantity=12;c.state.cash=979;c.drain()
    c.observe(order,4);c.state.quantity=14;c.state.cash=958;c.drain()
    c.observe(order,5);c.state.quantity=15;c.state.cash=947;c.drain()
    result=c.status(order)
    assert result['filled_quantity']==5 and result['trade_status']=='filled'
    assert result['asset_sync']['covered_quantity']==5
    assert result['actual_commission']['amount']=='3'


def test_source_cache_delay_is_not_coverage(context):
    c=context;c.publish();order=c.dispatch();c.observe(order,3)
    c.drain()
    assert c.status(order)['asset_sync']['status']=='SYNC_DELAYED'
    c.state.quantity=13;c.state.cash=969;c.drain()
    assert c.status(order)['asset_sync']['status']=='SYNCED'


@pytest.mark.parametrize('failure',['offline','timeout'])
def test_failed_sync_does_not_change_broker_fill_and_retries(context,failure):
    c=context;c.publish();order=c.dispatch(3);c.observe(order,3)
    if failure=='offline':c.state.offline=True
    else:c.state.before_publish=lambda:(_ for _ in ()).throw(TimeoutError('DO_NOT_EXPOSE'))
    c.drain();result=c.status(order)
    assert result['trade_status']=='filled' and result['asset_sync']['status']=='SYNC_FAILED'
    assert 'DO_NOT_EXPOSE' not in json.dumps(result)
    c.state.offline=False;c.state.before_publish=None;c.state.quantity=13;c.state.cash=969
    c.drain();assert c.status(order)['asset_sync']['status']=='SYNCED'


def test_same_account_multiple_orders_coalesce(context):
    c=context;c.publish();a=c.dispatch(2);b=c.dispatch(3)
    c.observe(a,2);c.observe(b,3);c.state.quantity=15;c.state.cash=948
    assert len(c.drain())==1
    assert c.status(a)['asset_sync']['status']=='SYNCED'
    assert c.status(b)['asset_sync']['status']=='SYNCED'


def test_cash_and_unknown_fee_are_not_invented(context):
    c=context;c.publish();order=c.dispatch(2);c.observe(order,2,fees=False)
    c.state.quantity=12;c.state.cash=980;c.drain()
    result=c.status(order)
    assert result['asset_sync']['status']=='SYNC_UNVERIFIED'
    assert result['asset_sync']['positions_verified']
    assert result['actual_commission']['status']=='UNKNOWN'
    c.observe(order,2,fees=True,shares=2)
    c.drain();assert c.status(order)['asset_sync']['status']=='SYNC_UNVERIFIED'  # fee not settled in cash
    c.state.cash=979;c.drain();assert c.status(order)['asset_sync']['status']=='SYNCED'


def test_sell_to_zero_and_net_zero_round_trip(context):
    c=context;c.publish();order=c.dispatch(10,'SELL');c.observe(order,10)
    c.state.quantity=0;c.state.cash=1099;c.drain()
    assert c.status(order)['asset_sync']['status']=='SYNCED'
    buy=c.dispatch(2);sell=c.dispatch(2,'SELL');c.observe(buy,2);c.observe(sell,2)
    c.state.quantity=0;c.state.cash=1097;c.drain()
    assert c.status(buy)['asset_sync']['status']=='SYNC_UNVERIFIED'
    assert c.status(sell)['asset_sync']['status']=='SYNC_UNVERIFIED'


def test_commit_crash_pending_restarts_and_expired_claim_fenced(context):
    c=context;c.publish();order=c.dispatch(2);c.observe(order,2)
    with c.sessions() as s:
        row=s.get(OrderRecord,order);p=decode(row.fill_sync_json);p['next_attempt_at']=0;row.fill_sync_json=encode(p);s.commit()
    executor=c.executor();jobs,_=executor.reserve('snowball','fill')
    # Process disappears after durable claim; replacement expires its lease.
    with c.sessions() as s:
        run=s.get(c.PositionSnapshotRun,jobs[0]['run_id']);meta=json.loads(run.context_json)
        meta['lease_until']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();run.context_json=json.dumps(meta);s.commit()
    assert c.status(order)['asset_sync']['status']=='SYNC_PENDING'
    assert c.status(order)['asset_sync']['reason_code']=='SYNC_LEASE_EXPIRED'
    c.state.quantity=12;c.state.cash=979;c.drain()
    assert c.status(order)['asset_sync']['status']=='SYNCED'
    calls=c.state.calls;executor.execute(**jobs[0])
    assert c.state.calls==calls


def test_two_connections_only_one_publisher(context):
    c=context;c.publish();order=c.dispatch(2);c.observe(order,2)
    with c.sessions() as s:
        row=s.get(OrderRecord,order);p=decode(row.fill_sync_json);p['next_attempt_at']=0;row.fill_sync_json=encode(p);s.commit()
    c.state.quantity=12;c.state.cash=979
    barrier=Barrier(2)
    def run():barrier.wait();return c.executor().sync_fills()
    with ThreadPoolExecutor(2) as pool:result=list(pool.map(lambda _:run(),range(2)))
    assert sum(len(r) for r in result)==1 and c.state.calls==2


def test_fill_advances_during_source_io_remains_pending(context):
    c=context;c.publish();order=c.dispatch(3);c.observe(order,2)
    c.state.quantity=12;c.state.cash=979
    c.state.before_publish=lambda:c.observe(order,3)
    c.drain();assert c.status(order)['asset_sync']['status']=='SYNC_PENDING'
    c.state.before_publish=None;c.state.quantity=13;c.state.cash=968;c.drain()
    assert c.status(order)['asset_sync']['status']=='SYNCED'


def test_account_owner_and_portfolio_filters(context):
    c=context;c.publish();order=c.dispatch(2);c.observe(order,2)
    from backend.services.restricted_sync import SyncExecutor
    wrong=SyncExecutor(['snowball'],7,c.factory,session_factory=c.sessions,account_resolver=lambda *_:'other-owner')
    assert wrong.sync_fills()==[]
    with c.sessions() as s:assert query(s,8)['items']==[]
    assert c.status(order)['asset_sync']['status']=='SYNC_PENDING'


def test_legacy_recovered_fill_no_pretrade_proof_stays_unverified(context):
    c=context;c.publish();order=c.dispatch(2)
    with c.sessions() as s:s.get(OrderRecord,order).fill_sync_json=None;s.commit()
    c.observe(order,2);c.state.quantity=12;c.state.cash=979;c.drain()
    assert c.status(order)['asset_sync']['status']=='SYNC_UNVERIFIED'


def test_read_query_never_mutates_or_refreshes(context):
    c=context;c.publish();order=c.dispatch();c.observe(order,2)
    calls=c.state.calls
    with c.sessions() as s:
        before=s.connection().connection.driver_connection.total_changes
        result=query(s,7,symbol='ABC:US')
        assert s.connection().connection.driver_connection.total_changes==before
    assert result['items'][0]['actual_filled_at']
    assert c.state.calls==calls


@pytest.mark.parametrize('trigger',['manual','cron','analysis'])
def test_existing_publishers_can_cover_pending_fills(context,trigger):
    c=context;c.publish();order=c.dispatch(2);c.observe(order,2)
    c.state.quantity=12;c.state.cash=979
    e=c.executor();jobs,_=e.reserve('snowball',trigger,freshness_seconds=300 if trigger=='analysis' else None)
    assert len(jobs)==1
    e.execute(**jobs[0]);assert c.status(order)['asset_sync']['status']=='SYNCED'


def test_later_publication_and_web_edit_cannot_keep_false_synced_claim(context):
    c=context;c.publish();order=c.dispatch(2);c.observe(order,2)
    c.state.quantity=12;c.state.cash=979;c.drain()
    assert c.status(order)['asset_sync']['status']=='SYNCED'
    c.state.quantity=10;c.state.cash=1000;c.publish()
    assert c.status(order)['asset_sync']['status']=='SYNC_DELAYED'
    c.state.quantity=12;c.state.cash=979;c.drain()
    from app.models import Position
    with c.sessions() as s:s.query(Position).delete();s.commit()
    assert c.status(order)['asset_sync']['status']=='SYNC_UNVERIFIED'


def test_new_u1_fixed_view_flags_pending_old_view_is_unchanged(context,monkeypatch):
    from backend.services.asset_analysis import AssetAnalysisPreparation
    from app.asset_views import load_view
    c=context;monkeypatch.setenv('WEALTHPILOT_ANALYSIS_CACHE_DIR',str(c.tmp_path/'inputs'))
    def ready(key):
        p=AssetAnalysisPreparation(c.executor(),c.db,synthetic=True)
        _,claim=p.request(key);p.run(*claim);result=p.status(key)
        assert result['state']=='ready',result
        return load_view(c.db,7,result['snapshot_id'])
    old=ready('before-fill');order=c.dispatch(2);c.observe(order,2)
    new=ready('after-fill')  # broker cache still old, no fabricated coverage
    assert 'FILL_SYNC_PENDING_OR_UNVERIFIED' in new['data_quality']['issues']
    assert not new['data_quality']['can_propose_rebalance']
    assert load_view(c.db,7,old['snapshot_id'])==old


def test_rollback_observation_never_enqueues_committed_work(context):
    c=context;c.publish();order=c.dispatch(2)
    with c.sessions() as s:
        OrderManager(s,c.Broker()).apply_broker_observation(order,OrderStatusUpdate(
            'broker-'+order,order,'filled',filled_quantity=2,avg_filled_price=Decimal(10),
            raw_response={'contract_currency':'USD','observed_account_id':'synthetic-owner'}))
        s.rollback()
    assert c.executor().sync_fills()==[]
    assert c.status(order)['filled_quantity']==0


def test_accept_then_process_crash_is_recovered_by_original_poller(context,monkeypatch):
    c=context;c.publish()
    def accepted_then_crashed(self,request):raise SystemExit('synthetic process loss after broker accept')
    monkeypatch.setattr(c.Broker,'place_order',accepted_then_crashed)
    with pytest.raises(SystemExit):c.dispatch(2)
    with c.sessions() as s:
        row=s.query(OrderRecord).one();order=row.id
        assert row.status=='unknown' and decode(row.fill_sync_json)['anchor']
    adapter=c.Broker()
    def recover(ref,*,perm_id=None):
        return OrderStatusUpdate('accepted-broker-id',ref,'filled',filled_quantity=2,avg_filled_price=Decimal(10),
            raw_response={'observed_account_id':'synthetic-owner','con_id':111,'contract_currency':'USD',
                'execution_evidence':[dict(exec_id='restart-fill',order_ref=ref,observed_account_id='synthetic-owner',
                    shares=2,price=10,con_id=111,commission=1,commission_currency='USD',timestamp=datetime.now(timezone.utc).isoformat())]})
    adapter.recover_order=recover
    from backend.services.action.order_poller import OrderPoller
    c.state.quantity=12;c.state.cash=979
    poller=OrderPoller(c.sessions,lambda:adapter,sync_fills=c.drain)
    assert poller._poll_once()==1
    assert c.status(order)['trade_status']=='filled' and c.status(order)['asset_sync']['status']=='SYNCED'


def test_tiger_maps_only_to_original_tiger_owner_and_unknown_actual_fees(context):
    c=context;c.state.trading='tiger';c.publish();order=c.dispatch(2);c.observe(order,2,fees=False)
    c.state.quantity=12;c.state.cash=980;c.drain()
    result=c.status(order)
    assert result['broker']=='tiger' and result['asset_sync']['positions_verified']
    assert result['asset_sync']['status']=='SYNC_UNVERIFIED'
    with c.sessions() as s:assert {r.broker for r in s.query(c.PositionSnapshotRun)}=={'tiger'}


def test_wrong_account_and_contract_observations_do_not_queue_work(context):
    c=context;c.publish();order=c.dispatch(2)
    for raw,reason in [({'observed_account_id':'other-owner'},'BROKER_ACCOUNT_MISMATCH'),
                       ({'observed_account_id':'synthetic-owner','con_id':222},'BROKER_CONTRACT_MISMATCH')]:
        with c.sessions() as s:
            with pytest.raises(ValueError,match=reason):
                OrderManager(s,c.Broker()).apply_broker_observation(order,OrderStatusUpdate(
                    'broker-id',order,'filled',filled_quantity=2,raw_response=raw))
            s.rollback()
    assert c.status(order)['filled_quantity']==0 and c.executor().sync_fills()==[]


def test_execution_fee_correction_revokes_coverage_without_changing_fill(context):
    c=context;c.publish();order=c.dispatch(2);c.observe(order,2)
    c.state.quantity=12;c.state.cash=979;c.drain()
    assert c.status(order)['asset_sync']['status']=='SYNCED'
    with c.sessions() as s:
        row=s.get(OrderRecord,order);raw=json.loads(row.raw_broker_response)
        raw['execution_evidence'][0]['commission']=2
        OrderManager(s,c.Broker()).apply_broker_observation(order,OrderStatusUpdate(
            row.broker_order_id,order,'filled',filled_quantity=2,avg_filled_price=Decimal(10),raw_response=raw))
        s.commit()
    result=c.status(order)
    assert not result['asset_sync']['cash_verified']
    assert 'EXECUTION_EVIDENCE_DISPUTED' in result['asset_sync']['issues']
    c.drain();result=c.status(order)
    assert result['trade_status']=='filled' and result['asset_sync']['status']=='SYNC_UNVERIFIED'
    assert result['actual_commission']['status']=='UNKNOWN'


def test_cross_currency_commission_cannot_be_used_as_usd_fee(context):
    c=context;c.publish();order=c.dispatch(2)
    with c.sessions() as s:
        row=s.get(OrderRecord,order)
        raw={'contract_currency':'USD','con_id':111,'observed_account_id':'synthetic-owner',
             'execution_evidence':[dict(exec_id='hk-fee',order_ref=order,observed_account_id='synthetic-owner',
                shares=2,price=10,con_id=111,commission=1,commission_currency='HKD')]}
        OrderManager(s,c.Broker()).apply_broker_observation(order,OrderStatusUpdate(
            row.broker_order_id,order,'filled',filled_quantity=2,avg_filled_price=Decimal(10),raw_response=raw));s.commit()
    c.state.quantity=12;c.state.cash=979;c.drain()
    result=c.status(order)
    assert result['actual_commission']['status']=='KNOWN'
    assert result['actual_commission']['currency']=='HKD'
    assert result['actual_commission']['amount']=='1'
    assert result['asset_sync']['status']=='SYNC_UNVERIFIED'


def test_web_status_api_does_not_accept_scope_or_refresh_parameters(context,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import action
    c=context;c.publish();order=c.dispatch(2);c.observe(order,2)
    monkeypatch.setattr(action,'get_session',c.sessions)
    app=FastAPI();app.include_router(action.router,prefix='/api/action')
    client=TestClient(app)
    for extra in ['refresh=true','portfolio_id=8','account_id=other']:
        assert client.get('/api/action/trade-and-sync-status?'+extra).status_code==422
    result=client.get('/api/action/trade-and-sync-status',params={'order_id':order})
    assert result.status_code==200 and result.json()['items'][0]['filled_quantity']==2
    assert c.state.calls==1


def test_reverse_fills_require_actual_cash_and_keep_broker_cost_authority(context):
    c=context;c.publish();buy=c.dispatch(3);sell=c.dispatch(1,'SELL')
    c.observe(buy,3);c.observe(sell,1)
    c.state.quantity=12;c.state.cash=979;c.state.cost='10.5'
    c.drain()  # position net matches, but two actual fees not both in cash yet
    assert c.status(buy)['asset_sync']['status']=='SYNC_UNVERIFIED'
    c.state.cash=978;c.drain()
    assert c.status(buy)['asset_sync']['status']=='SYNCED'
    assert c.status(sell)['asset_sync']['status']=='SYNCED'
    from app.models import Position
    with c.sessions() as s:
        row=s.query(Position).filter_by(symbol='ABC:US').one()
        assert row.quantity==12 and row.cost_price==10.5


def test_unreconciled_zero_quantity_order_blocks_account_delta_proof(context):
    c=context;c.publish();filled=c.dispatch(2);uncertain=c.dispatch(1)
    c.observe(filled,2)
    with c.sessions() as s:s.get(OrderRecord,uncertain).status='unknown';s.commit()
    c.state.quantity=12;c.state.cash=979;c.drain()
    result=c.status(filled)
    assert result['asset_sync']['status']=='SYNC_UNVERIFIED'
    assert 'FILL_IDENTITY_OR_AUTHORITY_UNVERIFIED' in result['asset_sync']['issues']


@pytest.mark.parametrize('clock',['unknown','cached'])
def test_matching_deltas_without_post_fill_broker_watermark_are_not_verified(context,clock):
    c=context;c.publish();order=c.dispatch(2);c.observe(order,2)
    c.state.quantity=12;c.state.cash=979;c.state.source_clock=clock
    c.drain();result=c.status(order)
    assert result['asset_sync']['status']=='SYNC_UNVERIFIED'
    assert 'BROKER_FILL_WATERMARK_UNVERIFIED' in result['asset_sync']['issues']
    calls=c.state.calls
    if clock=='unknown':
        assert not result['asset_sync']['automatic_retry']
        assert c.drain()==[] and c.state.calls==calls
    else:
        assert result['asset_sync']['automatic_retry']
        c.state.source_clock='current';c.drain()
        assert c.status(order)['asset_sync']['status']=='SYNCED'


def test_unproven_pretrade_clock_cannot_be_repaired_with_a_new_posttrade_timestamp(context):
    c=context;c.state.source_clock='unknown';c.publish();order=c.dispatch(2);c.observe(order,2)
    c.state.quantity=12;c.state.cash=979;c.state.source_clock='current';c.drain()
    assert c.status(order)['asset_sync']['status']=='SYNC_UNVERIFIED'


def test_every_relevant_fill_needs_actual_time_not_only_one_of_two_orders(context):
    c=context;c.publish();a=c.dispatch(2);b=c.dispatch(1)
    c.observe(a,2);c.observe(b,1,actual_time=None)
    c.state.quantity=13;c.state.cash=968;c.drain()
    assert c.status(a)['asset_sync']['status']=='SYNC_UNVERIFIED'
    assert c.status(b)['asset_sync']['status']=='SYNC_UNVERIFIED'


def test_closed_unverifiable_history_does_not_poison_new_pretrade_anchor(context):
    c=context;c.publish();old=c.dispatch(2)
    with c.sessions() as s:s.get(OrderRecord,old).fill_sync_json=None;s.commit()
    c.observe(old,2);c.state.quantity=12;c.state.cash=979;c.drain()
    assert c.status(old)['asset_sync']['status']=='SYNC_UNVERIFIED'
    assert not c.status(old)['asset_sync']['automatic_retry']
    new=c.dispatch(2);c.observe(new,2);c.state.quantity=14;c.state.cash=958;c.drain()
    assert c.status(new)['asset_sync']['status']=='SYNCED'
    assert c.status(old)['asset_sync']['status']=='SYNC_UNVERIFIED'
