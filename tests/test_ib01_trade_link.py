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
    from app.models import Portfolio
    with Session(engine, info={"portfolio_id": 1}) as db:
        db.add(Portfolio(id=1, name="Isolated owner")); db.commit()
    adapter = FakeIBKRExecutionAdapter()
    monkeypatch.setattr(execution_batch, 'get_session', lambda: Session(engine, info={"portfolio_id": 1}))
    monkeypatch.setattr(execution_batch, '_adapter', lambda: adapter)
    monkeypatch.setattr(link_handoff, 'ExecutionBatchService',
        lambda s, a: ExecutionBatchService(s, a, clock=lambda: NOW, allow_mutation=False))
    monkeypatch.setattr(execution_batch, '_service', lambda s: ExecutionBatchService(s,adapter,clock=lambda:NOW,allow_mutation=False))
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
    with Session(engine, info={"portfolio_id": 1}) as s:
        assert s.query(ConversationMessage).count()==s.query(ExecutionBatch).count()==s.query(OrderRecord).count()==0
    assert not adapter.place_calls and not adapter.what_if_calls


def test_confirm_intent_reuses_batch_and_cannot_authorize_an_order(api):
    client,engine,adapter=api
    payload=budget(request_id=str(uuid4()),confirmed=True)
    quote=client.post('/api/execution-batches/trade-link/quote',json={k:v for k,v in payload.items() if k!='confirmed'}).json()
    payload.update(preview_hash=quote['preview_hash'],quote_as_of=quote['batch']['legs'][0]['quote_as_of'])
    result=client.post('/api/execution-batches/trade-link/confirm-intent',json=payload)
    assert result.status_code==200,result.text
    batch=result.json();leg=batch['legs'][0]
    assert batch['broker']=='ibkr' and batch['budget_mode']=='ALL_IN_TOTAL'
    assert batch['status']=='READY' and batch['confirmation_version']==0
    assert leg['resolved_con_id']==354802220 and leg['exchange']=='SMART'
    assert leg['listing_exchange']=='LSEETF' and leg['currency']=='USD'
    assert leg['final_quantity']==31 and leg['estimated_notional']<=4000
    duplicate=client.post('/api/execution-batches/trade-link/confirm-intent',json=payload)
    assert duplicate.json()['id']==batch['id']
    changed=client.post('/api/execution-batches/trade-link/confirm-intent',json=payload|{'target_amount':'5000'})
    assert changed.status_code==422
    with Session(engine, info={"portfolio_id": 1}) as s:
        assert s.query(ExecutionBatch).count()==1
        assert s.query(SymbolStrategy).count()==s.query(OrderRecord).count()==0
        service=ExecutionBatchService(s,adapter,clock=lambda:NOW,allow_mutation=False)
        with pytest.raises((PermissionError,ExecutionSafetyError)):
            service.submit_next_leg(batch['id'],leg_id=leg['id'],confirmation_version=0)
    assert not adapter.place_calls


@pytest.mark.parametrize('terminal_status', ['COMPLETED', 'CANCELLED'])
def test_terminal_link_replay_does_not_create_another_batch(api, terminal_status):
    client,engine,adapter=api
    payload=budget(request_id=str(uuid4()),confirmed=True)
    quote=client.post('/api/execution-batches/trade-link/quote',json={k:v for k,v in payload.items() if k!='confirmed'}).json()
    payload.update(preview_hash=quote['preview_hash'],quote_as_of=quote['batch']['legs'][0]['quote_as_of'])
    first=client.post('/api/execution-batches/trade-link/confirm-intent',json=payload).json()
    with Session(engine, info={"portfolio_id": 1}) as s:
        s.get(ExecutionBatch,first['id']).status=terminal_status;s.commit()
    second=client.post('/api/execution-batches/trade-link/confirm-intent',json=payload).json()
    assert second['id']==first['id'] and second['status']==terminal_status
    assert not adapter.place_calls


def test_fixed_target_still_requires_original_intent_constraints(api):
    _,engine,_=api
    valid=link_handoff.link_intent(IBKRLinkPreviewRequest.model_validate(budget(request_id=str(uuid4()))))
    from backend.services.trade_intent.models import IntentConfirmationStatus
    valid.confirmation_status=IntentConfirmationStatus.CONFIRMED
    assert ExecutionBatchService._validate_case1_intent(valid)=='IB01_ALL_IN_TARGET'
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
    with Session(engine, info={"portfolio_id": 1}) as s:
        for name in adapters:
            strategy=SymbolStrategy(symbol='IB01:LSE' if name=='ibkr' else 'NVDA:US',side='BUY',
                target_quantity=1,order_type='LIMIT',limit_price=Decimal('1'),status='active')
            s.add(strategy);s.flush()
            s.add(OrderRecord(id='synthetic-'+name,strategy_id=strategy.id,broker_name=name,
                broker_order_id='synthetic-'+name,symbol=strategy.symbol,side='BUY',quantity=1,
                order_type='LIMIT',status='broker_pending'))
        s.commit()
    poller=OrderPoller(get_session=lambda:Session(engine, info={"portfolio_id": 1}),get_broker_adapter=lambda:adapters['tiger'],
        get_broker_adapter_for_order=lambda name:adapters[name])
    poller._poll_once()
    assert sorted(calls)==[('ibkr','synthetic-ibkr'),('tiger','synthetic-tiger')]
    # Keep the manager and returned session identical, as in the original API.
    def manager():
        s=Session(engine, info={"portfolio_id": 1});return OrderManager(s,broker_adapter=adapters['tiger']),s
    monkeypatch.setattr(action,'_get_manager',manager)
    monkeypatch.setattr(action,'_get_ibkr_adapter',lambda:adapters['ibkr'])
    app=FastAPI();app.include_router(action.router,prefix='/api/action')
    with TestClient(app) as client:
        assert client.get('/api/action/orders/synthetic-ibkr?refresh=true').status_code==200
    assert calls[-1]==('ibkr','synthetic-ibkr')


def quoted_request(client, **changes):
    params = budget(request_id=str(uuid4()), **changes)
    response = client.post('/api/execution-batches/trade-link/quote', json=params)
    assert response.status_code == 200, response.text
    quote = response.json()
    return params, quote


def confirmation(params, quote):
    return params | dict(confirmed=True, preview_hash=quote['preview_hash'],
                        quote_as_of=quote['batch']['legs'][0]['quote_as_of'])


def test_all_in_quote_reuses_original_engine_and_leaves_production_tables_empty(api, monkeypatch):
    client, engine, adapter = api
    original = adapter.get_executable_quote
    monkeypatch.setattr(adapter, 'get_executable_quote', lambda r: original(r)|{'ask':100,'bid':99.99})
    params, quote = quoted_request(client)
    batch = quote['batch']; leg = batch['legs'][0]
    assert batch['budget_mode'] == 'ALL_IN_TOTAL'
    assert leg['final_quantity'] == 39  # 40 shares would exceed the cap after fees/cushion.
    assert leg['estimated_notional'] + batch['estimated_fees'] + batch['safety_cushion'] <= 4000
    assert leg['what_if']['status'] == 'PASS'
    assert leg['resolved_con_id'] == 354802220 and leg['execution_exchange'] == 'SMART'
    with Session(engine, info={"portfolio_id": 1}) as s:
        assert s.query(ExecutionBatch).count() == s.query(ConversationMessage).count() == s.query(OrderRecord).count() == 0
    assert not quote['persisted'] and adapter.what_if_calls and not adapter.place_calls
    result = client.post('/api/execution-batches/trade-link/confirm-intent', json=confirmation(params,quote))
    assert result.status_code == 200, result.text
    assert result.json()['source_conversation_id'] == params['request_id']


def test_price_edit_rebuilds_quote_fees_and_invalidates_confirmed_version(api):
    client, engine, adapter = api
    params, quote = quoted_request(client)
    batch = client.post('/api/execution-batches/trade-link/confirm-intent', json=confirmation(params,quote)).json()
    route = '/api/execution-batches/'+batch['id']
    confirmed = client.post(route+'/confirm',json={'risk_confirmation_text':'我已知晓风险并坚持下单','confirmation_version':0})
    assert confirmed.status_code == 200, confirmed.text
    old = confirmed.json(); assert old['confirmation_hash']
    before = len(adapter.what_if_calls)
    result = client.post(route+'/manual-limits',json={'limits':{'IB01':100},'confirmation_version':old['confirmation_version']})
    assert result.status_code == 200, result.text
    new = result.json()
    assert new['id'] != old['id']
    assert new['source_conversation_id'] == params['request_id']
    assert new['legs'][0]['final_quantity'] == 39 and new['estimated_total']+new['estimated_fees']+new['safety_cushion']<=4000
    assert len(adapter.what_if_calls)>before and not new['confirmation_hash']
    with Session(engine, info={"portfolio_id": 1}) as s:
        assert s.get(ExecutionBatch,old['id']).status=='CANCELLED'
        assert not s.get(ExecutionBatch,old['id']).confirmation_hash
    assert client.post(route+'/confirm',json={'risk_confirmation_text':'我已知晓风险并坚持下单','confirmation_version':old['confirmation_version']}).status_code==409
    assert client.post('/api/execution-batches/'+new['id']+'/confirm',json={'risk_confirmation_text':'我已知晓风险并坚持下单','confirmation_version':old['confirmation_version']}).status_code==409
    assert not adapter.place_calls


@pytest.mark.parametrize('problem',['MISSING','DELAYED','STALE','CASH','FEES','WRONG_CURRENCY'])
def test_bad_quote_cash_or_fees_never_reaches_ready(api,monkeypatch,problem):
    from datetime import timedelta
    client,engine,adapter=api
    original=adapter.get_executable_quote
    if problem in {'MISSING','DELAYED','STALE'}:
        changes={'MISSING':{'ask':None},'DELAYED':{'quote_quality':'DELAYED','market_data_type':3},
                 'STALE':{'quote_timestamp':(NOW-timedelta(seconds=31)).isoformat()}}[problem]
        monkeypatch.setattr(adapter,'get_executable_quote',lambda r: original(r)|changes)
    elif problem=='CASH':
        monkeypatch.setattr(adapter,'get_cash_snapshot',lambda c:{'CashBalance':3999})
    elif problem=='FEES': adapter.what_if_error=True
    else: adapter.commission_currency='GBP'
    result=client.post('/api/execution-batches/trade-link/quote',json=budget(request_id=str(uuid4())))
    assert result.status_code!=200 or result.json()['batch']['status']!='READY'
    assert not adapter.place_calls
    with Session(engine, info={"portfolio_id": 1}) as s: assert s.query(OrderRecord).count()==s.query(ExecutionBatch).count()==0


def test_changed_or_expired_preview_cannot_be_confirmed(api,monkeypatch):
    from datetime import timedelta
    client,_,adapter=api
    params,quote=quoted_request(client)
    expired=confirmation(params,quote)|{'quote_as_of':(NOW-timedelta(seconds=31)).isoformat()}
    assert client.post('/api/execution-batches/trade-link/confirm-intent',json=expired).status_code==422
    original=adapter.get_executable_quote
    monkeypatch.setattr(adapter,'get_executable_quote',lambda r:original(r)|{'ask':127,'bid':126.99})
    changed=client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,quote))
    assert changed.status_code==422 and 'PREVIEW_CHANGED' in changed.text
    assert not adapter.place_calls


def test_failed_reprice_leaves_old_confirmation_invalid(api,monkeypatch):
    client,engine,adapter=api
    params,quote=quoted_request(client)
    old=client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,quote)).json()
    route='/api/execution-batches/'+old['id']
    old=client.post(route+'/confirm',json={'risk_confirmation_text':'我已知晓风险并坚持下单','confirmation_version':old['confirmation_version']}).json()
    monkeypatch.setattr(adapter,'get_cash_snapshot',lambda c:{'CashBalance':0})
    result=client.post(route+'/manual-limits',json={'limits':{'IB01':130},'confirmation_version':old['confirmation_version']})
    assert result.status_code==422
    with Session(engine, info={"portfolio_id": 1}) as s:
        batch=s.get(ExecutionBatch,old['id'])
        assert batch.status=='CANCELLED' and not batch.confirmation_hash
    assert not adapter.place_calls


def test_all_in_submission_rechecks_fee_and_confirmed_payload_without_real_orders(api,monkeypatch):
    client,engine,adapter=api
    params,quote=quoted_request(client)
    batch=client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,quote)).json()
    batch=client.post('/api/execution-batches/'+batch['id']+'/confirm',json={'risk_confirmation_text':'我已知晓风险并坚持下单','confirmation_version':0}).json()
    with Session(engine, info={"portfolio_id": 1}) as s:
        service=ExecutionBatchService(s,adapter,clock=lambda:NOW,allow_mutation=True) # Fake adapter only
        loaded=service.get_batch(batch['id'])
        assert service.review_fingerprint(loaded, authorization=True)==loaded.confirmation_hash
        original=adapter.what_if_limit_order
        monkeypatch.setattr(adapter,'what_if_limit_order',lambda r,**kw:original(r,**kw)|{'commission':100})
        with pytest.raises(ExecutionSafetyError,match='ALL_IN_BUDGET_EXCEEDED'):
            service.submit_next_leg(batch['id'],confirmation_version=batch['confirmation_version'])
        assert not adapter.place_calls and s.query(OrderRecord).count()==0
        monkeypatch.setattr(adapter,'what_if_limit_order',original)
        loaded.legs[0].final_limit=125
        s.commit()
        with pytest.raises(ExecutionSafetyError,match='payload changed'):
            service.submit_next_leg(batch['id'],confirmation_version=batch['confirmation_version'])
        assert not adapter.place_calls


def test_all_in_rejects_stale_confirmed_quote_even_with_fresh_broker_quote(api,monkeypatch):
    from datetime import timedelta
    client,engine,adapter=api
    params,quote=quoted_request(client)
    batch=client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,quote)).json()
    batch=client.post('/api/execution-batches/'+batch['id']+'/confirm',json={'risk_confirmation_text':'我已知晓风险并坚持下单','confirmation_version':0}).json()
    original=adapter.get_executable_quote
    monkeypatch.setattr(adapter,'get_executable_quote',lambda r:original(r)|{'quote_timestamp':(NOW+timedelta(seconds=31)).isoformat()})
    with Session(engine, info={"portfolio_id": 1}) as s:
        service=ExecutionBatchService(s,adapter,clock=lambda:NOW+timedelta(seconds=31),allow_mutation=True)
        with pytest.raises(ExecutionSafetyError,match='quote stale'):
            service.submit_next_leg(batch['id'],confirmation_version=batch['confirmation_version'])
        assert not adapter.place_calls


def test_all_in_quantity_dependent_fees_converge_inside_budget(api,monkeypatch):
    client,_,adapter=api
    original=adapter.what_if_limit_order
    monkeypatch.setattr(adapter,'what_if_limit_order',lambda r,**kw:original(r,**kw)|{'commission':kw['quantity']*.5})
    _,quote=quoted_request(client)
    batch=quote['batch'];leg=batch['legs'][0]
    assert batch['estimated_fees']==leg['final_quantity']*.5
    assert leg['estimated_notional']+batch['estimated_fees']+batch['safety_cushion']<=4000
    assert not adapter.place_calls


def test_ibkr_tif_matches_original_day_builder_and_rejects_old_mislabelled_gtc():
    assert IBKRLinkOrder.model_validate(budget()).time_in_force=='DAY'
    with pytest.raises(ValueError):IBKRLinkOrder.model_validate(budget(time_in_force='GTC'))


def test_preview_fingerprint_is_bound_to_request_id(api):
    client,_,adapter=api
    params,quote=quoted_request(client)
    other=params|{'request_id':str(uuid4())}
    response=client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(other,quote))
    assert response.status_code==422 and 'PREVIEW_CHANGED' in response.text
    assert not adapter.place_calls


def test_cached_old_review_cannot_survive_another_session_price_edit(api):
    client,engine,adapter=api
    params,quote=quoted_request(client)
    old=client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,quote)).json()
    route='/api/execution-batches/'+old['id']
    old=client.post(route+'/confirm',json={'risk_confirmation_text':'我已知晓风险并坚持下单','confirmation_version':0}).json()
    with Session(engine, info={"portfolio_id": 1}) as stale_session:
        service=ExecutionBatchService(stale_session,adapter,clock=lambda:NOW,allow_mutation=True)
        cached=service.get_batch(old['id']);assert cached.status=='CONFIRMED'
        response=client.post(route+'/manual-limits',json={'limits':{'IB01':130},'confirmation_version':old['confirmation_version']})
        assert response.status_code==200,response.text
        with pytest.raises((ExecutionSafetyError, ValueError)):
            service.submit_next_leg(old['id'],confirmation_version=old['confirmation_version'])
        assert not adapter.place_calls


@pytest.mark.parametrize('manual_limit', [None, '100'])
def test_fresh_link_review_replaces_stale_persisted_batch(api, monkeypatch, manual_limit):
    """Reopening/recalculating the same request must not reuse its old quote."""
    from datetime import timedelta
    client, engine, adapter = api
    params, first_quote = quoted_request(client)
    old = client.post('/api/execution-batches/trade-link/confirm-intent',
                      json=confirmation(params, first_quote)).json()
    old = client.post('/api/execution-batches/'+old['id']+'/confirm',
                      json={'risk_confirmation_text':'我已知晓风险并坚持下单','confirmation_version': old['confirmation_version']}).json()
    later = NOW + timedelta(seconds=40)
    original = adapter.get_executable_quote
    monkeypatch.setattr(adapter, 'get_executable_quote',
                        lambda r: original(r) | {'quote_timestamp': later.isoformat()})
    monkeypatch.setattr(link_handoff, 'ExecutionBatchService',
                        lambda s, a: ExecutionBatchService(s, a, clock=lambda: later, allow_mutation=False))
    monkeypatch.setattr(execution_batch, '_service',
                        lambda s: ExecutionBatchService(s, adapter, clock=lambda: later, allow_mutation=False))
    edited = params | ({'limit_price': manual_limit} if manual_limit else {})
    fresh = client.post('/api/execution-batches/trade-link/quote', json=edited).json()
    result = client.post('/api/execution-batches/trade-link/confirm-intent',
                         json=confirmation(edited, fresh))
    assert result.status_code == 200, result.text
    batch = result.json()
    assert batch['id'] != old['id'] and batch['status'] == 'READY'
    assert batch['confirmation_version'] > old['confirmation_version']
    assert not batch['confirmation_hash'] and not batch['confirmed_at']
    assert batch['legs'][0]['quote_as_of'] == fresh['batch']['legs'][0]['quote_as_of']
    if manual_limit:
        assert batch['legs'][0]['final_limit'] == float(manual_limit)
    duplicate = client.post('/api/execution-batches/trade-link/confirm-intent',
                            json=confirmation(edited, fresh))
    assert duplicate.status_code == 200 and duplicate.json()['id'] == batch['id']
    with Session(engine, info={"portfolio_id": 1}) as s:
        original_batch = s.get(ExecutionBatch, old['id'])
        assert original_batch.status == 'CANCELLED' and not original_batch.confirmation_hash
        assert s.query(ExecutionBatch).count() == 2
        assert s.query(OrderRecord).count() == s.query(SymbolStrategy).count() == 0
    assert not adapter.place_calls
    expired = confirmation(edited, fresh) | {'quote_as_of': (later-timedelta(seconds=31)).isoformat()}
    assert client.post('/api/execution-batches/trade-link/confirm-intent',json=expired).status_code == 422

@pytest.mark.parametrize('offset', ['+08:00', None])
def test_equivalent_review_timestamp_does_not_create_duplicate(api, offset):
    from datetime import datetime, timedelta, timezone
    client, engine, adapter = api
    params, quote = quoted_request(client)
    payload = confirmation(params, quote)
    when = datetime.fromisoformat(payload['quote_as_of'].replace('Z', '+00:00'))
    payload['quote_as_of'] = (when.astimezone(timezone(timedelta(hours=8))).isoformat()
                             if offset else when.replace(tzinfo=None).isoformat())
    first = client.post('/api/execution-batches/trade-link/confirm-intent', json=payload)
    assert first.status_code == 200, first.text
    second = client.post('/api/execution-batches/trade-link/confirm-intent', json=payload)
    assert second.status_code == 200 and second.json()['id'] == first.json()['id']
    with Session(engine, info={"portfolio_id": 1}) as s:
        assert s.query(ExecutionBatch).count() == 1 and s.query(OrderRecord).count() == 0
    assert not adapter.place_calls


def test_quote_expiring_during_whatif_stays_blocked(api, monkeypatch):
    from datetime import timedelta
    client, _, adapter = api
    params, quote = quoted_request(client)
    original = adapter.what_if_limit_order
    def slow_whatif(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(execution_batch, '_service',
                            lambda s: ExecutionBatchService(s, adapter, clock=lambda: NOW+timedelta(seconds=31)))
        return result
    monkeypatch.setattr(adapter, 'what_if_limit_order', slow_whatif)
    result = client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,quote))
    assert result.status_code == 422 and 'quote stale' in result.text
    assert not adapter.place_calls

def test_failed_link_refresh_can_retry_without_reviving_explicit_cancellation(api, monkeypatch):
    from datetime import timedelta
    client, engine, adapter = api
    params, quote = quoted_request(client)
    old = client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,quote)).json()
    later = NOW+timedelta(seconds=40)
    original = adapter.get_executable_quote
    monkeypatch.setattr(adapter, 'get_executable_quote',lambda r:original(r)|{'quote_timestamp':later.isoformat()})
    monkeypatch.setattr(link_handoff, 'ExecutionBatchService',
                        lambda s,a:ExecutionBatchService(s,a,clock=lambda:later,allow_mutation=False))
    monkeypatch.setattr(execution_batch, '_service',
                        lambda s:ExecutionBatchService(s,adapter,clock=lambda:later,allow_mutation=False))
    fresh = client.post('/api/execution-batches/trade-link/quote',json=params).json()
    # Facts change during confirm: fail closed, but don't trap the request forever.
    monkeypatch.setattr(adapter, 'get_executable_quote',
                        lambda r:original(r)|{'quote_timestamp':later.isoformat(),'ask':127,'bid':126.99})
    failed = client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,fresh))
    assert failed.status_code==422 and 'PREVIEW_CHANGED' in failed.text
    with Session(engine, info={"portfolio_id": 1}) as s:
        assert s.get(ExecutionBatch,old['id']).status=='CANCELLED'
        assert not s.get(ExecutionBatch,old['id']).confirmation_hash
    fresh = client.post('/api/execution-batches/trade-link/quote',json=params).json()
    retried = client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,fresh))
    assert retried.status_code==200, retried.text
    assert retried.json()['status']=='READY' and retried.json()['id']!=old['id']
    with Session(engine, info={"portfolio_id": 1}) as s:
        assert s.query(ExecutionBatch).count()==2 and s.query(OrderRecord).count()==0
    assert not adapter.place_calls

def test_concurrent_fresh_link_retries_share_one_active_review(api, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import timedelta
    from threading import Event, current_thread
    client, engine, adapter = api
    params, quote = quoted_request(client)
    old = client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,quote)).json()
    later = NOW+timedelta(seconds=40)
    original_quote = adapter.get_executable_quote
    monkeypatch.setattr(adapter, 'get_executable_quote',
                        lambda r:original_quote(r)|{'quote_timestamp':later.isoformat()})
    monkeypatch.setattr(link_handoff, 'ExecutionBatchService',
                        lambda s,a:ExecutionBatchService(s,a,clock=lambda:later,allow_mutation=False))
    monkeypatch.setattr(execution_batch, '_service',
                        lambda s:ExecutionBatchService(s,adapter,clock=lambda:later,allow_mutation=False))
    fresh = client.post('/api/execution-batches/trade-link/quote',json=params).json()
    payload = confirmation(params,fresh)
    entered, release, second_done = Event(), Event(), Event()
    original_cash = adapter.get_cash_snapshot
    first_thread = []
    def cash(currency):
        if not first_thread:
            first_thread.append(current_thread().ident)
            entered.set()
            assert release.wait(4)
        return original_cash(currency)
    monkeypatch.setattr(adapter, 'get_cash_snapshot', cash)
    def second():
        try:return client.post('/api/execution-batches/trade-link/confirm-intent',json=payload)
        finally:second_done.set()
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(client.post,'/api/execution-batches/trade-link/confirm-intent',json=payload)
        assert entered.wait(4)
        other = workers.submit(second)
        second_done.wait(1)  # A serialized retry must wait behind the fresh build.
        release.set()
        one, two = first.result(), other.result()
    assert one.status_code==two.status_code==200, (one.text,two.text)
    assert one.json()['id']==two.json()['id']
    with Session(engine, info={"portfolio_id": 1}) as s:
        assert s.query(ExecutionBatch).count()==2
        assert s.get(ExecutionBatch,old['id']).status=='CANCELLED'
        assert s.query(OrderRecord).count()==0
    assert not adapter.place_calls


def test_all_in_budget_uses_native_usd_not_base_cash_or_margin(api):
    from backend.services.action.tests.test_ibkr_native_cash import adapter as cash_adapter, row
    client, engine, broker = api
    native = cash_adapter([row('$LEDGER-CashBalance',120),row('$LEDGER-CashBalance',50000,'CNH'),
                           row('TotalCashValue',8000),row('AvailableFunds',20000),row('BuyingPower',80000)])
    broker.get_cash_snapshot = native.get_cash_snapshot
    response = client.post('/api/execution-batches/trade-link/quote',json=budget(request_id=str(uuid4())))
    assert response.status_code == 422
    assert 'INSUFFICIENT_USD_CASH_FOR_ALL_IN_BUDGET' in response.text
    assert broker.place_calls == []
    with Session(engine, info={"portfolio_id": 1}) as session:
        assert session.query(OrderRecord).count() == session.query(ExecutionBatch).count() == 0
