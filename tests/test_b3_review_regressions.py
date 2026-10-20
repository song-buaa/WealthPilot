"""B3 review safety regressions. Independent SQLite WAL connections, Fake only."""
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from backend.api import execution_batch
from backend.services.action.models import ExecutionBatch, OrderRecord
from backend.services.execution_batch.service import ExecutionBatchService
from tests.test_ib01_trade_link import api
from tests.test_b3_trade_experience import prepared
from tests.test_execution_batch_service import NOW

ACK='我已知晓风险并坚持下单'

def enable_fake(api, monkeypatch):
    client, engine, adapter = api
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode=WAL").scalar()=="wal"
    monkeypatch.setattr(execution_batch, '_service', lambda s: ExecutionBatchService(s, adapter, clock=lambda:NOW, allow_mutation=True))
    return client, engine, adapter

def confirm(client, b):
    return client.post(f"/api/execution-batches/{b['id']}/confirm",json={'confirmation_version':b['confirmation_version'],'risk_confirmation_text':ACK})

def submit(client,b):
    return client.post(f"/api/execution-batches/{b['id']}/legs/{b['legs'][0]['id']}/submit",json={'confirmation_version':b['confirmation_version'],'live_order_acknowledged':True,'confirmation_text':'确认并提交 1 笔 IBKR 实盘限价单'})

def test_confirm_committed_response_lost_then_restore_and_submit_once(api,monkeypatch):
    c,e,a=enable_fake(api,monkeypatch);params,b=prepared(c)
    reply=confirm(c,b);assert reply.status_code==200,reply.text
    # Simulate browser discarding successful confirm response: restore is the authority.
    restored=c.post('/api/execution-batches/trade-link/restore',json=params).json()['batch']
    assert restored['status']=='CONFIRMED' and not a.place_calls
    first=submit(c,restored);assert first.status_code==200,first.text
    again=submit(c,restored);assert again.status_code==200,again.text
    assert first.json()['order_id']==again.json()['order_id'] and len(a.place_calls)==1

def test_broker_accepted_local_timeout_reload_cannot_resend(api,monkeypatch):
    c,e,a=enable_fake(api,monkeypatch);params,b=prepared(c)
    b=confirm(c,b).json();a.order_statuses=['timeout']
    sent=submit(c,b);assert sent.status_code==200,sent.text
    assert sent.json()['status']=='unknown' and len(a.place_calls)==1
    restored=c.post('/api/execution-batches/trade-link/restore',json=params).json()['batch']
    assert restored['legs'][0]['linked_order_id']==sent.json()['order_id']
    assert restored['legs'][0]['status']=='UNKNOWN'
    assert submit(c,restored).json()['order_id']==sent.json()['order_id']
    assert len(a.place_calls)==1

def test_two_clients_confirm_then_concurrent_submit_one_persisted_send(api,monkeypatch):
    c,e,a=enable_fake(api,monkeypatch);params,b=prepared(c)
    with ThreadPoolExecutor(2) as pool:
        replies=list(pool.map(lambda _:confirm(c,b),range(2)))
    assert sorted(r.status_code for r in replies)==[200,409],[r.text for r in replies]
    current=c.post('/api/execution-batches/trade-link/restore',json=params).json()['batch']
    assert current['status']=='CONFIRMED'
    barrier=threading.Barrier(2);old=a.what_if_limit_order
    def overlapping(*args,**kwargs):
        result=old(*args,**kwargs);barrier.wait(timeout=10);return result
    a.what_if_limit_order=overlapping
    with ThreadPoolExecutor(2) as pool:
        replies=list(pool.map(lambda _:submit(c,current),range(2)))
    assert all(r.status_code==200 for r in replies),[r.text for r in replies]
    assert len({r.json()['order_id'] for r in replies})==1 and len(a.place_calls)==1
    with Session(e) as db: assert db.query(OrderRecord).count()==1

def test_reprice_retires_old_confirmation_and_no_authorization_on_new_economics(api,monkeypatch):
    c,e,a=enable_fake(api,monkeypatch);_,b=prepared(c);b=confirm(c,b).json()
    r=c.post(f"/api/execution-batches/{b['id']}/manual-limits",json={'limits':{'IB01':130},'confirmation_version':b['confirmation_version']})
    assert r.status_code==200,r.text
    new=r.json();assert new['id']!=b['id'] and new['status']=='READY' and new['confirmation_hash'] is None
    assert new['legs'][0]['final_limit']==130
    assert submit(c,b).status_code==422
    assert submit(c,new).status_code==422
    assert not a.place_calls

def test_quote_expiry_and_reprice_failure_do_not_dispatch_or_revive_authorization(api,monkeypatch):
    from datetime import timedelta
    c,e,a=enable_fake(api,monkeypatch);_,b=prepared(c);b=confirm(c,b).json()
    with Session(e) as db:
        row=db.get(ExecutionBatch,b['id']);row.legs[0].quote_as_of=NOW-timedelta(seconds=31);db.commit()
    assert submit(c,b).status_code==422
    def unavailable(_): raise ConnectionError('synthetic gateway offline')
    a.get_executable_quote=unavailable
    r=c.post(f"/api/execution-batches/{b['id']}/manual-limits",json={'limits':{'IB01':130},'confirmation_version':b['confirmation_version']})
    assert r.status_code==503
    with Session(e) as db:
        row=db.get(ExecutionBatch,b['id']);assert row.status=='CANCELLED' and row.confirmation_hash is None
        assert db.query(ExecutionBatch).count()==1 and db.query(OrderRecord).count()==0
    assert not a.place_calls

def test_late_confirm_cannot_resurrect_repriced_review(api,monkeypatch):
    c,e,a=enable_fake(api,monkeypatch);_,b=prepared(c)
    loaded=threading.Event();release=threading.Event();target={'thread':None,'paused':False}
    class PausedConfirm(ExecutionBatchService):
        def _lock_review(self,batch):
            if threading.get_ident()==target['thread'] and not target['paused']:
                target['paused']=True;loaded.set();assert release.wait(10)
            return super()._lock_review(batch)
    monkeypatch.setattr(execution_batch,'_service',lambda s:PausedConfirm(s,a,clock=lambda:NOW,allow_mutation=True))
    # Invoke original synchronous route on a dedicated connection/thread to make
    # the exact read-before-reprice/write-after-reprice boundary deterministic.
    def old_confirm():
        target['thread']=threading.get_ident()
        return execution_batch.confirm_batch(b['id'],{'confirmation_version':0,'risk_confirmation_text':ACK})
    with ThreadPoolExecutor(1) as pool:
        pending=pool.submit(old_confirm);assert loaded.wait(10)
        repriced=c.post(f"/api/execution-batches/{b['id']}/manual-limits",json={'limits':{'IB01':130},'confirmation_version':0})
        assert repriced.status_code==200,repriced.text
        replacement=repriced.json()
        with Session(e) as db: assert db.get(ExecutionBatch,b['id']).status=='CANCELLED'
        release.set()
        with pytest.raises(HTTPException) as conflict:
            pending.result(timeout=10)
        assert conflict.value.status_code==409
        assert 'CONFIRMATION_CONFLICT' in conflict.value.detail
    old=c.get(f"/api/execution-batches/{b['id']}").json()
    assert old['status']=='CANCELLED' and old['confirmation_hash'] is None
    assert old['confirmation_version']==1
    assert old['legs'][0]['final_limit']==126
    assert replacement['status']=='READY' and replacement['confirmation_hash'] is None
    assert replacement['legs'][0]['final_limit']==130
    assert submit(c,b).status_code==422 and not a.place_calls
    with Session(e) as db:
        assert db.query(OrderRecord).count()==0
        assert db.get(ExecutionBatch,replacement['id']).status=='READY'


def test_late_confirm_cannot_downgrade_completed_batch(api,monkeypatch):
    c,e,a=enable_fake(api,monkeypatch);_,b=prepared(c)
    loaded=threading.Event();release=threading.Event();target={'thread':None,'paused':False}
    class PausedConfirm(ExecutionBatchService):
        def _lock_review(self,batch):
            if threading.get_ident()==target['thread'] and not target['paused']:
                target['paused']=True;loaded.set();assert release.wait(10)
            return super()._lock_review(batch)
    monkeypatch.setattr(execution_batch,'_service',lambda s:PausedConfirm(s,a,clock=lambda:NOW,allow_mutation=True))
    original_send=a.place_order
    def filled(request):
        result=original_send(request);result.status='filled';result.filled_quantity=request.quantity;result.avg_filled_price=request.limit_price;return result
    a.place_order=filled
    def old_confirm():
        target['thread']=threading.get_ident()
        return execution_batch.confirm_batch(b['id'],{'confirmation_version':0,'risk_confirmation_text':ACK})
    with ThreadPoolExecutor(1) as pool:
        pending=pool.submit(old_confirm);assert loaded.wait(10)
        newer=confirm(c,b);assert newer.status_code==200,newer.text
        sent=submit(c,newer.json());assert sent.status_code==200,sent.text
        with Session(e) as db:assert db.get(ExecutionBatch,b['id']).status=='COMPLETED'
        release.set()
        with pytest.raises(HTTPException) as conflict:
            pending.result(timeout=10)
        assert conflict.value.status_code==409
    with Session(e) as db:
        row=db.get(ExecutionBatch,b['id']);order=db.query(OrderRecord).one()
        evidence={'batch_status':row.status,'leg_status':row.legs[0].status,'order_status':order.status,'fake_send_count':len(a.place_calls)}
    assert evidence=={'batch_status':'COMPLETED','leg_status':'FILLED','order_status':'filled','fake_send_count':1}
    with Session(e) as db: assert db.query(OrderRecord).count()==1

@pytest.mark.parametrize('changed', ['quantity', 'limit', 'fees'])
def test_stale_session_economic_evidence_cannot_be_authorized(api, changed):
    c,e,a=api;_,b=prepared(c)
    with e.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA journal_mode=WAL').scalar()=='wal'
    with Session(e,info={'portfolio_id':1}) as older, Session(e) as newer:
        service=ExecutionBatchService(older,a,clock=lambda:NOW,allow_mutation=True)
        cached=service.get_batch(b['id']);list(cached.legs)
        row=newer.get(ExecutionBatch,b['id'])
        if changed=='quantity':row.legs[0].final_quantity-=1
        elif changed=='limit':row.legs[0].final_limit=130
        else:row.estimated_fees+=1
        newer.commit()
        with pytest.raises(ValueError,match='CONFIRMATION_CONFLICT: review_fingerprint changed'):
            service.confirm_batch(b['id'],expected_version=0,risk_confirmation_text=ACK)
        older.rollback()
    with Session(e) as db:
        row=db.get(ExecutionBatch,b['id'])
        assert row.status=='READY' and row.confirmation_hash is None and row.confirmation_version==0
    assert not a.place_calls


def test_confirm_only_uses_prepared_local_evidence_under_writer_lock(api, monkeypatch):
    c,e,a=api;_,b=prepared(c)
    for name in ('authenticate','get_cash_snapshot','list_open_order_details',
                 'resolve_lse_usd_etf','qualify_execution_route','get_executable_quote',
                 'what_if_limit_order','place_order'):
        monkeypatch.setattr(a,name,lambda *args,**kwargs:pytest.fail('Broker I/O during confirm'))
    reply=confirm(c,b)
    assert reply.status_code==200,reply.text
    assert reply.json()['status']=='CONFIRMED' and reply.json()['confirmation_version']==1


@pytest.mark.parametrize('version',[None,True,'0',-1])
def test_web_confirm_requires_explicit_integer_review_version(api,version):
    c,e,a=api;_,b=prepared(c)
    r=c.post(f"/api/execution-batches/{b['id']}/confirm",json={'confirmation_version':version,'risk_confirmation_text':ACK})
    assert r.status_code==409 and 'CONFIRMATION_CONFLICT' in r.json()['detail']
    with Session(e) as db:
        row=db.get(ExecutionBatch,b['id']);assert row.status=='READY' and row.confirmation_version==0
    assert not a.place_calls
