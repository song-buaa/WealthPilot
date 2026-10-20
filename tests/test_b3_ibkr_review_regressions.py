"""PR31 review failures become safety assertions; Fake wire, real SDK/loop/WAL."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest
from tests.test_b3_ibkr_runtime import runtime
from tests.test_b0_order_safety import rig


def test_shutdown_rejects_admitted_but_not_yet_enqueued_connection(runtime):
    adapter,sdk=runtime
    admitted=threading.Event();cleanup_finished=threading.Event();resumed=threading.Event()
    original_run=adapter._run_on_loop
    captured={};loop_errors=[]
    def schedule(operation,**kwargs):
        if operation==adapter._connect_single_flight:
            captured['loop']=adapter._loop
            captured['thread']=adapter._thread
            adapter._loop.set_exception_handler(lambda loop,context:loop_errors.append(context))
            admitted.set()
            assert cleanup_finished.wait(2)
            try:return original_run(operation,**kwargs)
            finally:resumed.set()
        result=original_run(operation,**kwargs)
        if operation.__name__=='close':
            cleanup_finished.set()
            assert resumed.wait(2)
        return result
    adapter._run_on_loop=schedule
    with ThreadPoolExecutor(1) as pool:
        waiter=pool.submit(adapter._ensure_connected,deadline=time.monotonic()+1)
        assert admitted.wait(2)
        adapter.shutdown()
        with pytest.raises(ConnectionError,match='IBKR_ADAPTER_SHUTDOWN'):
            waiter.result(timeout=2)
    assert sdk.instances==[]  # No candidate can start after close() finished.
    assert captured['loop'].is_closed() and not captured['thread'].is_alive()
    assert adapter._connection_task is None and not adapter._active_calls and not loop_errors
    with pytest.raises(ConnectionError,match='IBKR_ADAPTER_SHUTDOWN'):adapter._ensure_connected()


def test_shutdown_of_started_initialization_releases_lifecycle_lock_for_cleanup(runtime):
    adapter,sdk=runtime;sdk.connect_delay=30
    with pytest.raises(ConnectionError):adapter._ensure_connected(deadline=time.monotonic()+.02)
    candidate=sdk.instances[0]
    original_disconnect=candidate.disconnect
    def disconnect():
        assert adapter._connection_guard.acquire(blocking=False)
        adapter._connection_guard.release()
        original_disconnect()
    candidate.disconnect=disconnect
    loop,thread=adapter._loop,adapter._thread
    adapter.shutdown()
    assert candidate.connect_cancelled.is_set() and candidate.disconnections==1
    assert loop.is_closed() and not thread.is_alive() and adapter._shutdown_complete.is_set()
    adapter.shutdown()  # terminal + idempotent, no loop recreation
    with pytest.raises(ConnectionError,match='IBKR_ADAPTER_SHUTDOWN'):
        asyncio.run(adapter._connect_single_flight())


def test_sixteen_timeouts_retire_sdk_state_and_late_replies_cannot_poison_cbu3(runtime):
    from ib_async.ib import IB
    from ib_async import Contract,ContractDetails
    adapter,sdk=runtime;adapter._ensure_connected();connection=adapter._ib
    sent=[];futures=[];seventeenth=threading.Event()
    def install():
        ib=IB()
        ib.client.getReqId=lambda:len(sent)+1
        def wire(req_id,contract):
            sent.append(req_id);futures.append(ib.wrapper._futures[req_id])
            if req_id==17:seventeenth.set()
        ib.client.reqContractDetails=wire
        connection.reqContractDetailsAsync=ib.reqContractDetailsAsync
        connection.wrapper=ib.wrapper
        adapter._install_contract_details_guard()
        adapter._install_contract_details_guard()  # No nested callback installation.
        return ib
    ib=adapter._run_on_loop(install)
    def sizes():
        return tuple(len(getattr(ib.wrapper,key)) for key in ('_futures','_results','_reqId2Contract'))
    for _ in range(16):
        with pytest.raises(TimeoutError):
            adapter.instrument_details({'symbol':'IB01:LSE'},deadline=time.monotonic()+.01)
        assert adapter._run_on_loop(sizes)==(0,0,0)
    assert len(sent)==16 and all(f.cancelled() for f in futures)
    with ThreadPoolExecutor(1) as pool:
        normal=pool.submit(adapter.instrument_details,{'symbol':'CBU3:LSE'},deadline=time.monotonic()+2)
        assert seventeenth.wait(1)
        def late_then_current():
            assert sizes()==(1,1,1)
            old=ContractDetails(contract=Contract(conId=354802220))
            for req_id in range(1,17):
                ib.wrapper.contractDetails(req_id,old)
                ib.wrapper.bondContractDetails(req_id,old)
                ib.wrapper.contractDetailsEnd(req_id)
            assert sizes()==(1,1,1) and not futures[-1].done()
            ib.wrapper.contractDetails(17,ContractDetails(contract=Contract(conId=79000224,
                symbol='CSBGU3',localSymbol='CBU3',secType='STK',currency='USD',
                exchange='LSEETF',primaryExchange='LSEETF')))
            ib.wrapper.contractDetailsEnd(17)
        adapter._run_on_loop(late_then_current)
        result=normal.result(timeout=2)
    assert result['instrument']['con_id']==79000224
    assert adapter._run_on_loop(sizes)==(0,0,0)
    assert connection.isConnected() and connection.disconnections==0 and len(sdk.instances)==1


@pytest.mark.parametrize('outcome',['filled','unknown_then_reconcile'])
def test_metadata_timeout_does_not_cancel_or_resend_shared_order(runtime,rig,monkeypatch,outcome):
    from ib_async import Trade,OrderStatus
    from decimal import Decimal
    from backend.services.action.order_manager import OrderManager
    import backend.services.action.brokers.ibkr as module
    adapter,sdk=runtime
    factory,broker,strategy,params=rig
    adapter._account_id=broker._account_id
    sdk.managedAccounts=lambda _:[broker._account_id]
    monkeypatch.setattr(module,'PERM_ID_WAIT_SECONDS',.1)
    monkeypatch.setattr(module,'PERM_ID_POLL_INTERVAL',.005)
    monkeypatch.setattr(module,'BROKER_ACK_WAIT_SECONDS',.1)
    monkeypatch.setattr(module,'BROKER_ACK_POLL_INTERVAL',.005)
    adapter._ensure_connected()
    connection=adapter._ib
    sent=threading.Event();trades=[]
    def accept(contract,order):
        # Original B0 submission identity must already be durable before I/O.
        from backend.services.action.models import OrderRecord
        with factory() as db:
            db.connection().exec_driver_sql('BEGIN IMMEDIATE')
            assert db.get(OrderRecord,order.orderRef).submission_key
            db.rollback()
        order.orderId=17;order.permId=0
        trade=Trade(contract,order,OrderStatus(status='PendingSubmit'))
        trades.append(trade);sent.set()
        return trade
    connection.placeOrder=accept
    connection.trades=lambda:trades
    sdk.hang_query=True
    def submit():
        with factory() as db:
            row=OrderManager(db,adapter).place_order(strategy,params)
            return row.id,row.status
    with ThreadPoolExecutor(1) as pool:
        order=pool.submit(submit)
        assert sent.wait(2)
        with pytest.raises(TimeoutError):
            adapter.instrument_details({'symbol':'IB01:LSE'},deadline=time.monotonic()+.02)
        assert connection.query_cancelled.wait(1)
        if outcome=='filled':
            def fill():
                trade=trades[0];trade.order.permId=101
                trade.orderStatus.status='Filled';trade.orderStatus.filled=10
                trade.orderStatus.avgFillPrice=10;trade.orderStatus.remaining=0
            adapter._run_on_loop(fill)
        order_id,status=order.result(timeout=2)
    assert status==('filled' if outcome=='filled' else 'unknown')
    assert len(trades)==1 and connection.disconnections==0 and connection.isConnected()
    with factory() as db:
        manager=OrderManager(db,adapter)
        assert manager.place_order(strategy,params).id==order_id
        assert len(trades)==1
    if outcome=='unknown_then_reconcile':
        async def no_rows(*args,**kwargs):return []
        async def completed(*args,**kwargs):return trades
        def fill_after_unknown():
            trade=trades[0];trade.order.permId=101
            trade.order.filledQuantity=10
            trade.orderStatus.status='Filled';trade.orderStatus.filled=10
            trade.orderStatus.avgFillPrice=10;trade.orderStatus.remaining=0
        adapter._run_on_loop(fill_after_unknown)
        connection.reqAllOpenOrdersAsync=no_rows
        connection.reqExecutionsAsync=no_rows
        connection.reqCompletedOrdersAsync=completed
        with factory() as db:
            row=OrderManager(db,adapter).sync_order_status(order_id);db.commit()
            assert row.status=='filled' and row.filled_quantity==10
    assert len(trades)==1 and connection.disconnections==0
    print('CONTROL_SHARED_ORDER',outcome,'one send, metadata timeout no disconnect, no replay')



def test_explicit_waiter_cancellation_preserves_other_waiter(runtime):
    adapter,sdk=runtime
    sdk.connect_delay=.15
    with pytest.raises(ConnectionError):
        adapter._ensure_connected(deadline=time.monotonic()+.01)
    async def waiters():
        cancelled=asyncio.create_task(adapter._connect_single_flight())
        survivor=asyncio.create_task(adapter._connect_single_flight())
        await asyncio.sleep(0)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):await cancelled
        await survivor
    adapter._run_on_loop(waiters,timeout=1)
    assert len(sdk.instances)==1 and adapter._ib is sdk.instances[0]
    assert adapter._account_verified and sdk.instances[0].disconnections==0
    print('CONTROL_WAITER_CANCEL one cancelled waiter, survivor connected, one client174')


def test_shutdown_drains_active_metadata_future_before_closing_loop(runtime):
    from concurrent.futures import CancelledError
    from ib_async.ib import IB
    adapter,sdk=runtime;adapter._ensure_connected();connection=adapter._ib
    started=threading.Event();errors=[]
    def install():
        ib=IB();ib.client.getReqId=lambda:1
        ib.client.reqContractDetails=lambda *args:started.set()
        connection.wrapper=ib.wrapper;connection.reqContractDetailsAsync=ib.reqContractDetailsAsync
        adapter._install_contract_details_guard()
        adapter._loop.set_exception_handler(lambda loop,context:errors.append(context))
        return ib
    ib=adapter._run_on_loop(install);loop=adapter._loop;thread=adapter._thread
    with ThreadPoolExecutor(1) as pool:
        waiter=pool.submit(adapter.instrument_details,{'symbol':'IB01:LSE'},deadline=time.monotonic()+2)
        assert started.wait(1)
        adapter.shutdown()
        with pytest.raises(CancelledError):waiter.result(timeout=1)
    assert not adapter._active_calls and not ib.wrapper._futures
    assert not ib.wrapper._results and not ib.wrapper._reqId2Contract
    assert connection.disconnections==1 and loop.is_closed() and not thread.is_alive() and not errors


def test_shutdown_on_owned_loop_fails_fast_without_deadlock_or_closing_admission(runtime):
    adapter,sdk=runtime;adapter._ensure_connected()
    with pytest.raises(RuntimeError,match='IBKR_SHUTDOWN_REQUIRES_EXTERNAL_THREAD'):
        adapter._run_on_loop(adapter.shutdown,timeout=.1)
    assert not adapter._closing and adapter._ib.isConnected()
    assert adapter.instrument_details({'symbol':'CBU3:LSE'})['instrument']['con_id']==79000224
