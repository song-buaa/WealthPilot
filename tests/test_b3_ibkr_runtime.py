"""B3 runtime fault injection: real adapter loop, Fake SDK, no broker socket."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from types import SimpleNamespace

import pytest
from backend.services.action.brokers.ibkr import IBKRBrokerAdapter
from tests.test_b3_trade_experience import holdings  # disposable owner-scoped DB


class Event:
    def __init__(self): self.listeners = []
    def __iadd__(self, callback):
        self.listeners.append(callback)
        return self
    def emit(self):
        for callback in self.listeners: callback()


@pytest.fixture
def runtime(monkeypatch):
    import ib_async
    class FakeIB:
        instances = []
        connect_delay = .04
        fail_next = False
        hang_query = False
        def __init__(self):
            self.connected = False
            self.wrapper = SimpleNamespace(_results={}, contractDetails=lambda *args:None,
                                           bondContractDetails=lambda *args:None)
            self.disconnections = 0
            self.errorEvent = Event()
            self.disconnectedEvent = Event()
            self.connect_started = threading.Event()
            self.query_started = threading.Event()
            self.query_cancelled = threading.Event()
            self.connect_cancelled = threading.Event()
            self.client_ids = []
            self.instances.append(self)
        async def connectAsync(self, **kwargs):
            self.client_ids.append(kwargs['clientId'])
            self.connect_started.set()
            try:
                await asyncio.sleep(self.connect_delay)
            except asyncio.CancelledError:
                self.connect_cancelled.set()
                raise
            if FakeIB.fail_next:
                FakeIB.fail_next = False
                raise TimeoutError('synthetic SDK connection timeout')
            self.connected = True
        def managedAccounts(self): return ['DU_SYNTHETIC']
        def isConnected(self): return self.connected
        def disconnect(self):
            self.disconnections += 1
            self.connected = False
            self.disconnectedEvent.emit()
        async def reqContractDetailsAsync(self, contract):
            self.query_started.set()
            if FakeIB.hang_query:
                try: await asyncio.Future()
                except asyncio.CancelledError:
                    self.query_cancelled.set()
                    raise
            from ib_async import Contract, ContractDetails, TagValue
            cb = contract.conId == 79000224 or contract.symbol == 'CBU3'
            return [ContractDetails(contract=Contract(
                conId=79000224 if cb else 354802220, symbol='CSBGU3' if cb else 'IB01',
                localSymbol='CBU3' if cb else 'IB01', secType='STK', currency='USD',
                exchange='LSEETF', primaryExchange='LSEETF'),
                secIdList=[TagValue('ISIN', 'IE00B3VWN179' if cb else 'IE00BGSF1X88')],
                orderTypes='LMT', minSize=1)]
    monkeypatch.setattr(ib_async, 'IB', FakeIB)
    monkeypatch.setattr(IBKRBrokerAdapter, '_install_open_order_evidence_hook', lambda _:None)
    adapter = IBKRBrokerAdapter(client_id=174, account_id='DU_SYNTHETIC', timeout=.2)
    yield adapter, FakeIB
    adapter.shutdown()


def test_two_first_requests_share_one_connection_and_distinct_identities(runtime):
    adapter, sdk = runtime
    gate = threading.Barrier(2)
    def query(identity):
        gate.wait()
        return adapter.instrument_details(identity)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(query, {'symbol':'IB01:LSE'})
        second = pool.submit(query, {'symbol':'CBU3:LSE'})
        assert first.result()['instrument']['con_id'] == 354802220
        assert second.result()['instrument']['con_id'] == 79000224
    assert len(sdk.instances) == 1
    assert sdk.instances[0].client_ids == [174]
    assert adapter._ib is sdk.instances[0] and adapter._account_verified


def test_shared_failure_cleans_candidate_and_next_request_retries(runtime):
    adapter, sdk = runtime
    sdk.fail_next = True
    gate = threading.Barrier(2)
    def connect():
        gate.wait()
        with pytest.raises(ConnectionError): adapter._ensure_connected()
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _:connect(), range(2)))
    assert len(sdk.instances) == 1 and sdk.instances[0].disconnections == 1
    assert adapter._ib is None and not adapter._account_verified
    adapter._ensure_connected()
    assert len(sdk.instances) == 2 and adapter._ib is sdk.instances[1]


def test_waiter_timeout_does_not_cancel_or_replace_shared_initialization(runtime):
    adapter, sdk = runtime
    sdk.connect_delay = .15
    with pytest.raises(ConnectionError):
        adapter._ensure_connected(deadline=time.monotonic()+.02)
    candidate = sdk.instances[0]
    adapter._ensure_connected(deadline=time.monotonic()+1)
    assert adapter._ib is candidate and len(sdk.instances) == 1
    assert candidate.isConnected() and not candidate.disconnections
    assert not candidate.connect_cancelled.is_set()


def test_shutdown_cancels_pending_candidate_before_stopping_loop(runtime):
    adapter, sdk = runtime
    sdk.connect_delay = 30
    with pytest.raises(ConnectionError):
        adapter._ensure_connected(deadline=time.monotonic()+.02)
    loop, thread = adapter._loop, adapter._thread
    candidate = sdk.instances[0]
    adapter.shutdown()
    assert candidate.connect_cancelled.is_set() and candidate.disconnections == 1
    assert not thread.is_alive() and loop.is_closed()
    assert adapter._ib is adapter._connection_task is None


def test_initialization_own_timeout_cleans_candidate(runtime):
    adapter, sdk = runtime
    sdk.connect_delay = 30
    # Exercise the initialization's own bound, not only a cancelled waiter.
    adapter._timeout = .01
    with pytest.raises(ConnectionError): adapter._ensure_connected()
    # The caller and owned-task timers may fire in either order.
    adapter._run_on_loop(lambda: asyncio.sleep(.03), timeout=.2)
    assert sdk.instances[0].connect_cancelled.is_set()
    assert sdk.instances[0].disconnections == 1
    assert adapter._ib is None
    sdk.connect_delay = 0
    adapter._ensure_connected()
    assert adapter._ib is sdk.instances[1]


def test_disconnect_reconnect_and_late_old_disconnect_cannot_clear_new_session(runtime):
    adapter, sdk = runtime
    adapter._ensure_connected()
    old = adapter._ib
    adapter._run_on_loop(old.disconnect)
    assert not adapter._connected and not adapter._account_verified
    adapter._ensure_connected()
    current = adapter._ib
    adapter._run_on_loop(old.disconnectedEvent.emit)
    assert current is not old and current.isConnected()
    assert adapter._connected and adapter._account_verified
    assert [item.client_ids for item in sdk.instances] == [[174],[174]]


def test_contract_deadline_cancels_query_not_valid_shared_connection(runtime):
    adapter, sdk = runtime
    adapter._ensure_connected()
    connection = adapter._ib
    sdk.hang_query = True
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        adapter.instrument_details({'symbol':'IB01:LSE'}, deadline=start+.04)
    assert time.monotonic()-start < .5
    assert connection.query_cancelled.wait(.5)
    assert connection.isConnected() and not connection.disconnections
    sdk.hang_query = False
    assert adapter.instrument_details({'symbol':'CBU3:LSE'})['instrument']['con_id'] == 79000224
    assert len(sdk.instances) == 1


def test_original_factory_entrypoints_share_single_instance(monkeypatch):
    from backend.api import action
    monkeypatch.setattr(action, '_cached_adapter', None)
    monkeypatch.setattr(action, '_BROKER_MODE', 'ibkr.paper')
    seen = []
    def create(*args, **kwargs):
        time.sleep(.02)
        value = object(); seen.append(value)
        return value
    monkeypatch.setattr(action, 'get_broker_adapter', create)
    with ThreadPoolExecutor(8) as pool:
        values = list(pool.map(lambda i: action._get_adapter() if i%2 else action._get_ibkr_adapter(), range(8)))
    assert len(seen) == 1 and all(value is seen[0] for value in values)


def bridge_config(tmp_path):
    token=tmp_path/'token'; token.write_text('s'*48); token.chmod(0o600)
    config=tmp_path/'bridge.json'
    config.write_text(json.dumps({'base_url':'http://127.0.0.1:18321','token_file':str(token)}))
    config.chmod(0o600)
    return token, config


def test_bridge_budgets_only_change_ibkr_tradability(tmp_path):
    import io
    from pocs.chatgpt_skills.prepare import PrepareClient
    _, config = bridge_config(tmp_path)
    bridge=PrepareClient(config); seen=[]
    def open_request(request, timeout):
        seen.append((request.full_url,timeout))
        return io.BytesIO(b'{"state":"ready"}')
    bridge.opener=SimpleNamespace(open=open_request)
    bridge.tradability({'broker':'ibkr','symbol':'IB01:LSE'})
    bridge.tradability({'broker':'tiger','symbol':'AAPL:US'})
    bridge.prepare('same-original-task')
    assert [timeout for _,timeout in seen] == [18,5,5]
    assert seen[2][0].endswith('/asset-analysis/prepare')


def test_protected_bridge_deadline_converges_backend_and_preserves_owner(runtime,holdings,monkeypatch,tmp_path):
    """Actual protected HTTP handler and adapter loop; never full backend lifespan."""
    import io
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from backend.api import action
    from backend.services.action.brokers import base
    from pocs.chatgpt_skills.prepare import PrepareClient
    adapter, sdk = runtime
    from app.models import Position
    with Session(holdings) as db:
        db.query(Position).filter_by(broker_account_id='synthetic-ibkr').update(
            {'broker_account_id':'DU_SYNTHETIC'})
        db.commit()
    token,config=bridge_config(tmp_path)
    monkeypatch.setenv('WEALTHPILOT_ANALYSIS_PREPARE_TOKEN_FILE',str(token))
    monkeypatch.setattr(action,'get_session',lambda:Session(holdings,info={'portfolio_id':1}))
    monkeypatch.setattr(action,'_get_ibkr_adapter',lambda:adapter)
    monkeypatch.setattr(base,'IBKR_TRADABILITY_QUERY_SECONDS',.12)
    app=FastAPI(); app.include_router(action.router,prefix='/api/action')
    bridge=PrepareClient(config)
    with TestClient(app) as client:
        def open_request(request,timeout):
            assert timeout == 18
            result=client.post('/api/action/instrument-tradability',content=request.data,headers=dict(request.header_items()))
            assert result.status_code == 200
            return io.BytesIO(result.content)
        bridge.opener=SimpleNamespace(open=open_request)
        sdk.hang_query=True
        # Full-suite generation-2 GC can consume this synthetic 120ms
        # deadline before adapter admission; normalize setup, not the bound.
        import gc
        gc.collect()
        started=time.monotonic()
        result=bridge.tradability({'broker':'ibkr','symbol':'IB01:LSE'})
        assert time.monotonic()-started < 1
        assert result['tradability']=='UNKNOWN'
        assert 'BROKER_QUERY_UNAVAILABLE' in result['issues']
        assert 'ACCOUNT_PERMISSION_UNPROVEN' in result['issues']
        assert 'LIVE_ACCOUNT_QUERY_NOT_CONFIGURED' not in result['issues']
        assert sdk.instances[0].query_cancelled.wait(.5)
        sdk.hang_query=False
        result=bridge.tradability({'broker':'ibkr','symbol':'CBU3:LSE'})
        assert result['instrument']['con_id']==79000224 and result['fees']['amount'] is None
        assert len(sdk.instances)==1
        bad=client.post('/api/action/instrument-tradability',json={'broker':'ibkr','symbol':'IB01:LSE'},headers={'Authorization':'Bearer '+'x'*48})
        assert bad.status_code==403
        bad=client.post('/api/action/instrument-tradability',json={'broker':'ibkr','symbol':'IB01:LSE','account_ref':'not-an-owner'},headers={'Authorization':'Bearer '+'s'*48})
        assert bad.status_code==422
        assert len(sdk.instances)==1


def test_real_sdk_late_contract_reply_cannot_complete_next_request(runtime):
    """Real ib_async request IDs/Futures, only the wire send replaced in memory."""
    from ib_async.ib import IB
    from ib_async import Contract, ContractDetails
    adapter, _ = runtime
    adapter._ensure_connected()
    connection=adapter._ib
    sent=[]
    def install():
        sdk=IB()
        sdk.client.getReqId=lambda:len(sent)+1
        sdk.client.reqContractDetails=lambda req_id,contract:sent.append(req_id)
        connection.reqContractDetailsAsync=sdk.reqContractDetailsAsync
        connection.wrapper=sdk.wrapper
        adapter._install_contract_details_guard()
        return sdk
    sdk=adapter._run_on_loop(install)
    with pytest.raises(TimeoutError):
        adapter.instrument_details({'symbol':'IB01:LSE'},deadline=time.monotonic()+.02)
    def late_old_reply():
        assert 1 not in sdk.wrapper._futures
        assert 1 not in sdk.wrapper._results
        assert 1 not in sdk.wrapper._reqId2Contract
        sdk.wrapper.contractDetails(1,ContractDetails(contract=Contract(conId=354802220)))
        sdk.wrapper.contractDetailsEnd(1)
        assert not sdk.wrapper._futures
        def send_new(req_id,contract):
            sent.append(req_id)
            sdk.wrapper.contractDetails(req_id,ContractDetails(contract=Contract(
                conId=79000224,symbol='CSBGU3',localSymbol='CBU3',secType='STK',
                currency='USD',exchange='LSEETF',primaryExchange='LSEETF')))
            sdk.wrapper.contractDetailsEnd(req_id)
        sdk.client.reqContractDetails=send_new
    adapter._run_on_loop(late_old_reply)
    assert adapter.instrument_details({'symbol':'CBU3:LSE'})['instrument']['con_id']==79000224
    assert sent==[1,2] and connection.isConnected() and not connection.disconnections


def test_account_mismatch_candidate_never_becomes_shared_connection(runtime):
    adapter,sdk=runtime
    sdk.managedAccounts=lambda _:['DU_DIFFERENT']
    with pytest.raises(ConnectionError,match='managedAccounts'):
        adapter._ensure_connected()
    assert adapter._ib is None and not adapter._account_verified
    assert sdk.instances[0].disconnections==1


def test_disconnected_http_waiter_leaves_only_bounded_backend_work(runtime,holdings,monkeypatch,tmp_path):
    """Real loopback socket timeout while Backend is still awaiting Fake SDK."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from app.models import Position
    from backend.api import action
    from backend.services.action.brokers import base
    from pocs.chatgpt_skills import prepare
    adapter,sdk=runtime
    adapter._ensure_connected()
    with Session(holdings) as db:
        db.query(Position).filter_by(broker_account_id='synthetic-ibkr').update(
            {'broker_account_id':'DU_SYNTHETIC'})
        db.commit()
    token,config=bridge_config(tmp_path)
    monkeypatch.setenv('WEALTHPILOT_ANALYSIS_PREPARE_TOKEN_FILE',str(token))
    monkeypatch.setattr(action,'get_session',lambda:Session(holdings,info={'portfolio_id':1}))
    monkeypatch.setattr(action,'_get_ibkr_adapter',lambda:adapter)
    monkeypatch.setattr(base,'IBKR_TRADABILITY_QUERY_SECONDS',.5)
    # Inject a transport abort earlier than the normal 18-second HTTP budget.
    monkeypatch.setattr(prepare,'IBKR_TRADABILITY_HTTP_SECONDS',.03)
    app=FastAPI();app.include_router(action.router,prefix='/api/action')
    completed=threading.Event()
    responses=[]
    with TestClient(app) as api:
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body=self.rfile.read(int(self.headers['Content-Length']))
                try:
                    result=api.post(self.path,content=body,headers={'Authorization':self.headers['Authorization'],'Content-Type':self.headers['Content-Type']})
                    responses.append((result.status_code,result.json()))
                    self.send_response(result.status_code);self.end_headers()
                    try:self.wfile.write(result.content)
                    except (BrokenPipeError,ConnectionResetError):pass
                finally:completed.set()
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            config.write_text(json.dumps({'base_url':'http://127.0.0.1:'+str(server.server_port),
                                          'token_file':str(token)}))
            bridge=prepare.PrepareClient(config)
            sdk.hang_query=True
            with pytest.raises(ValueError,match='ANALYSIS_PREPARE_UNAVAILABLE'):
                bridge.tradability({'broker':'ibkr','symbol':'IB01:LSE'})
            assert completed.wait(2),responses
            assert responses[0][0]==200 and 'BROKER_QUERY_UNAVAILABLE' in responses[0][1]['issues'],responses
            assert adapter._ib.query_started.is_set() and adapter._ib.query_cancelled.wait(.5),responses
            assert len(sdk.instances)==1 and adapter._ib.isConnected()
            sdk.hang_query=False
            monkeypatch.setattr(prepare,'IBKR_TRADABILITY_HTTP_SECONDS',1)
            result=bridge.tradability({'broker':'ibkr','symbol':'CBU3:LSE'})
            assert result['instrument']['con_id']==79000224
            assert len(sdk.instances)==1
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)
