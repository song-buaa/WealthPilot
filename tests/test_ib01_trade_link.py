"""Isolated entry/contract/authorization regression tests. No live broker calls."""
import json
from uuid import uuid4
from urllib.parse import urlsplit, parse_qs

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.models import ConversationMessage
from backend.api import action, execution_batch
from backend.services.action.models import ExecutionBatch, OrderRecord, SymbolStrategy
from backend.services.execution_batch import link_handoff
from backend.services.execution_batch.service import ExecutionBatchService
from backend.services.execution_batch.calculator import ExecutionSafetyError
from pocs.chatgpt_skills.trade_link import IBKRLinkOrder, IBKRLinkPreviewRequest, LinkOrder, prepare_link
from tests.test_execution_batch_service import FakeIBKRExecutionAdapter, NOW


def budget(**changes):
    return dict(broker='IBKR', symbol='IB01', side='BUY', order_type='LIMIT', currency='USD', target_amount='4000') | changes


@pytest.mark.parametrize('symbol', ['IB01', 'IB01:UK', 'IB01:LSE', 'IB01:LSEETF', 'ib01:lse'])
def test_ib01_uses_registered_identity_not_us_currency_guess(symbol):
    result = prepare_link('https://mini.example.ts.net', IBKRLinkOrder.model_validate(budget(symbol=symbol)))
    assert result.execution_path == 'ibkr_batch'
    assert result.order.symbol == 'IB01:LSE'
    assert result.instrument == dict(con_id=354802220, symbol='IB01', local_symbol='IB01',
        isin='IE00BGSF1X88', listing_exchange='LSEETF', execution_exchange='SMART',
        primary_exchange='LSEETF', currency='USD')
    params = parse_qs(urlsplit(result.url).fragment.split('?', 1)[1])
    assert params['trade'] == ['ibkr'] and params['target_amount'] == ['4000']
    assert 'quantity' not in params and 'limit_price' not in params
    assert not result.draft_created and not result.order_submitted


@pytest.mark.parametrize('changes', [{'symbol':'IB01:US'}, {'symbol':'IB01:HK'}, {'symbol':'VOD:UK'},
    {'symbol':'IB01:UK:US'}, {'broker':'TIGER'}, {'currency':'GBP'}, {'side':'SELL'},
    {'target_amount':'NaN'}, {'target_amount':0}, {'order_type':'MARKET'},
    {'con_id':123}, {'quantity':10}, {'limit_price':'100'}])
def test_unsupported_or_tampered_budget_requests_rejected(changes):
    with pytest.raises(ValueError): IBKRLinkOrder.model_validate(budget(**changes))


@pytest.mark.parametrize("symbol", ["IB01:US", " IB01:US ", "US.IB01", "IB01.US"])
def test_lse_ticker_cannot_be_disguised_as_us_single_order(symbol):
    with pytest.raises(ValueError, match='IB01_REQUIRES_IBKR_BUDGET_LINK'):
        LinkOrder(symbol=symbol, side='BUY', quantity=1, order_type='LIMIT', limit_price='100', currency='USD')


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///'+str(tmp_path/'fixture.db'), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    adapter = FakeIBKRExecutionAdapter()
    monkeypatch.setattr(execution_batch, 'get_session', lambda: Session(engine))
    monkeypatch.setattr(execution_batch, '_adapter', lambda: adapter)
    monkeypatch.setattr(link_handoff, 'ExecutionBatchService',
        lambda s, a: ExecutionBatchService(s, a, clock=lambda: NOW, allow_mutation=False))
    app=FastAPI();app.include_router(execution_batch.router,prefix='/api/execution-batches')
    with TestClient(app) as client:yield client,engine,adapter
    engine.dispose()


def test_web_preview_is_pure_and_request_requires_intent_confirmation(api):
    client,engine,adapter=api
    params=budget(request_id=str(uuid4()))
    response=client.post('/api/execution-batches/trade-link/preview',json=params)
    assert response.status_code==200
    assert response.json()['instrument']['con_id']==354802220
    assert not response.json()['draft_created'] and not response.json()['order_submitted']
    for confirm in [None,False]:
        payload=params if confirm is None else params|{'confirmed':confirm}
        assert client.post('/api/execution-batches/trade-link/confirm-intent',json=payload).status_code==422
    with Session(engine) as s:
        assert s.query(ConversationMessage).count()==s.query(ExecutionBatch).count()==s.query(OrderRecord).count()==0
    assert not adapter.place_calls and not adapter.what_if_calls


def test_confirm_intent_reuses_batch_and_cannot_authorize_an_order(api):
    client,engine,adapter=api
    payload=budget(request_id=str(uuid4()),confirmed=True)
    result=client.post('/api/execution-batches/trade-link/confirm-intent',json=payload)
    assert result.status_code==200,result.text
    batch=result.json();leg=batch['legs'][0]
    assert batch['broker']=='ibkr' and batch['budget_mode']=='FIXED_TOTAL'
    assert batch['status']=='READY' and batch['confirmation_version']==0
    assert leg['resolved_con_id']==354802220 and leg['exchange']=='SMART'
    assert leg['listing_exchange']=='LSEETF' and leg['currency']=='USD'
    assert leg['final_quantity']==31 and leg['estimated_notional']<=4000
    duplicate=client.post('/api/execution-batches/trade-link/confirm-intent',json=payload)
    assert duplicate.json()['id']==batch['id']
    changed=client.post('/api/execution-batches/trade-link/confirm-intent',json=payload|{'target_amount':'5000'})
    assert changed.status_code==422
    with Session(engine) as s:
        assert s.query(ExecutionBatch).count()==1
        assert s.query(SymbolStrategy).count()==s.query(OrderRecord).count()==0
        service=ExecutionBatchService(s,adapter,clock=lambda:NOW,allow_mutation=False)
        with pytest.raises((PermissionError,ExecutionSafetyError)):
            service.submit_next_leg(batch['id'],leg_id=leg['id'],confirmation_version=0)
    assert not adapter.place_calls


def test_terminal_link_replay_does_not_create_another_batch(api):
    client,engine,adapter=api
    payload=budget(request_id=str(uuid4()),confirmed=True)
    first=client.post('/api/execution-batches/trade-link/confirm-intent',json=payload).json()
    with Session(engine) as s:
        s.get(ExecutionBatch,first['id']).status='COMPLETED';s.commit()
    second=client.post('/api/execution-batches/trade-link/confirm-intent',json=payload).json()
    assert second['id']==first['id'] and second['status']=='COMPLETED'
    assert not adapter.place_calls


def test_fixed_target_still_requires_original_intent_constraints(api):
    _,engine,_=api
    valid=link_handoff.link_intent(IBKRLinkPreviewRequest.model_validate(budget(request_id=str(uuid4()))))
    from backend.services.trade_intent.models import IntentConfirmationStatus
    valid.confirmation_status=IntentConfirmationStatus.CONFIRMED
    assert ExecutionBatchService._validate_case1_intent(valid)=='IB01_FIXED_TARGET'
    for field,value in [('side','SELL'),('broker','TIGER'),('venue','NYSE'),('funding_currency','GBP')]:
        changed=valid.model_copy(deep=True);getattr(changed,field).value=value
        with pytest.raises(ExecutionSafetyError):ExecutionBatchService._validate_case1_intent(changed)
    changed=valid.model_copy(deep=True);changed.legs[0].target_amount.value='5000'
    with pytest.raises(ExecutionSafetyError):ExecutionBatchService._validate_case1_intent(changed)


def test_explicit_ibkr_batch_does_not_use_active_tiger_adapter(monkeypatch):
    sentinel=object();calls=[]
    monkeypatch.setattr(action,'_BROKER_MODE','tiger.live')
    monkeypatch.setattr(action,'_cached_adapter',None)
    def factory(**kwargs):calls.append(kwargs);return sentinel
    monkeypatch.setattr(action,'get_broker_adapter',factory)
    assert execution_batch._adapter() is sentinel
    assert execution_batch._adapter() is sentinel
    assert calls==[{'broker_name':'ibkr','mode':'readonly'}]


def test_existing_poller_and_web_refresh_keep_ibkr_orders_on_ibkr(api, monkeypatch):
    from types import SimpleNamespace
    from decimal import Decimal
    from backend.services.action.brokers.base import OrderStatusUpdate
    from backend.services.action.order_manager import OrderManager
    from backend.services.action.order_poller import OrderPoller
    _,engine,_=api
    calls=[]
    def adapter(name):
        def status(order_id):
            assert order_id=='synthetic-'+name
            calls.append((name,order_id))
            return OrderStatusUpdate(broker_order_id=order_id,local_order_id='',status='broker_pending',raw_response={})
        return SimpleNamespace(broker_name=name,get_order_status=status)
    adapters={name:adapter(name) for name in ['tiger','ibkr']}
    with Session(engine) as s:
        for name in adapters:
            strategy=SymbolStrategy(symbol='IB01:LSE' if name=='ibkr' else 'NVDA:US',side='BUY',
                target_quantity=1,order_type='LIMIT',limit_price=Decimal('1'),status='active')
            s.add(strategy);s.flush()
            s.add(OrderRecord(id='synthetic-'+name,strategy_id=strategy.id,broker_name=name,
                broker_order_id='synthetic-'+name,symbol=strategy.symbol,side='BUY',quantity=1,
                order_type='LIMIT',status='broker_pending'))
        s.commit()
    poller=OrderPoller(get_session=lambda:Session(engine),get_broker_adapter=lambda:adapters['tiger'],
        get_broker_adapter_for_order=lambda name:adapters[name])
    poller._poll_once()
    assert sorted(calls)==[('ibkr','synthetic-ibkr'),('tiger','synthetic-tiger')]
    # Keep the manager and returned session identical, as in the original API.
    def manager():
        s=Session(engine);return OrderManager(s,broker_adapter=adapters['tiger']),s
    monkeypatch.setattr(action,'_get_manager',manager)
    monkeypatch.setattr(action,'_get_ibkr_adapter',lambda:adapters['ibkr'])
    app=FastAPI();app.include_router(action.router,prefix='/api/action')
    with TestClient(app) as client:
        assert client.get('/api/action/orders/synthetic-ibkr?refresh=true').status_code==200
    assert calls[-1]==('ibkr','synthetic-ibkr')
