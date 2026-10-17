"""Original Action APIs on disposable data; no real credentials or broker."""
import json
import time
from urllib.parse import urlsplit, parse_qs
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import pytest

from app.database import Base
from app.models import Portfolio
from backend.api import action
from backend.services.action.models import ActionDraft, SymbolStrategy, OrderRecord
from backend.services.action.order_manager import OrderManager
from backend.services.action.brokers.mock import MockBrokerAdapter
from pocs.chatgpt_skills.trade_link import LinkOrder, prepare_link


def order(**changes):
    return dict(symbol='NVDA:US', side='BUY', quantity=10, order_type='LIMIT',
                limit_price='180', currency='USD') | changes


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///'+str(tmp_path/'fixture.db'), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Portfolio(id=1, name='Synthetic')); s.commit()
    broker = MockBrokerAdapter(fill_delay_seconds=.01, rejection_probability=0,
                              partial_fill_probability=0, network_failure_probability=0)
    def manager():
        session = Session(engine, info={"portfolio_id": 1})
        return OrderManager(session, broker_adapter=broker), session
    monkeypatch.setattr(action, '_get_manager', manager)
    monkeypatch.setattr(action, '_BROKER_MODE', 'mock')
    monkeypatch.setattr(action, '_get_portfolio_total_value', lambda s: 100000)
    monkeypatch.setattr(action, '_get_position_value', lambda s, symbol: 0)
    monkeypatch.setattr(action, '_get_discipline_result', lambda *a: None)
    monkeypatch.setattr(action, '_get_fx_rate_to_cny', lambda *a: 1)
    app = FastAPI(); app.include_router(action.router, prefix='/api/action')
    with TestClient(app) as client:
        yield client, engine
    broker.shutdown(); engine.dispose()


@pytest.mark.parametrize('changes', [{}, {'side':'SELL'}, {'symbol':'700:HK','currency':'HKD'},
                                  {'order_type':'CONDITIONAL_LIMIT','trigger_price':'179'}])
def test_pure_link_roundtrip_no_database(changes):
    request = LinkOrder.model_validate(order(**changes))
    result = prepare_link('https://mini.example.ts.net', request)
    outer = urlsplit(result.url)
    assert outer.query == '' and outer.fragment.startswith('/action?')
    fields = {k:v[0] for k,v in parse_qs(outer.fragment.split('?',1)[1]).items()}
    assert fields['symbol'] == request.symbol
    assert fields['side'] == request.side and fields['quantity'] == '10'
    assert fields['currency'] == request.currency and fields['time_in_force'] == 'GTC'
    assert not result.draft_created and not result.order_submitted


@pytest.mark.parametrize('changes', [{'symbol':'NVDA'}, {'quantity':0}, {'quantity':1.5},
    {'quantity':True}, {'limit_price':'nan'}, {'limit_price':0}, {'side':'HOLD'},
    {'currency':'CNY'}, {'order_type':'MARKET'}, {'order_type':'CONDITIONAL_LIMIT'},
    {'time_in_force':'DAY'}, {'trigger_price':'179'}, {'symbol':'<script>:US'}])
def test_invalid_order_never_approximated(changes):
    with pytest.raises(ValueError): LinkOrder.model_validate(order(**changes))


@pytest.mark.parametrize('url', ['', 'http://public.example', 'https://user:secret@mini.example',
    'https://mini.example/?token=secret', 'javascript:alert(1)', 'https://mini.example/#/action'])
def test_link_destination_is_configured_not_model_supplied(url):
    with pytest.raises(ValueError): prepare_link(url, LinkOrder.model_validate(order()))


def test_preview_no_writes_and_invalid_errors(api):
    client, engine = api
    for values, code in [(order(),200), (order(quantity=0),422),
                         ({k:v for k,v in order().items() if k!='currency'},422)]:
        response=client.post('/api/action/trade-link/preview',json=values|{'request_id':str(uuid4())})
        assert response.status_code==code
    with Session(engine) as s:
        assert s.query(ActionDraft).count()==s.query(SymbolStrategy).count()==s.query(OrderRecord).count()==0


def test_existing_web_draft_confirm_order_status_and_repeat(api):
    client, engine = api
    request_id=str(uuid4())
    preview=client.post('/api/action/trade-link/preview',json=order()|{'request_id':request_id}).json()
    assert preview['execution']['broker']=='mock'
    draft_body={'prefill_request_id':request_id,'payload':preview['draft']['payload'],'decision_summary':'synthetic'}
    created=client.post('/api/action/drafts',json=draft_body)
    assert created.status_code==200, created.text
    draft_id=created.json()['id']
    assert client.post('/api/action/drafts',json=draft_body).json()['id']==draft_id
    assert client.post(f'/api/action/drafts/{draft_id}/confirm').status_code==200
    replay=client.post('/api/action/drafts',json=draft_body).json()
    assert replay['id']==draft_id and replay['status']=='confirmed'
    strategy=client.get('/api/action/strategies').json()['items'][0]
    assert strategy['symbol']=='NVDA:US' and strategy['target_quantity']==10 and strategy['limit_price']==180
    with Session(engine) as s:assert s.query(OrderRecord).count()==0
    path=f"/api/action/strategies/{strategy['id']}/place_order"
    payload={'quantity':10,'limit_price':180,'confirmation_text':'我已知晓风险并坚持下单'}
    reviewed = client.post(f"/api/action/strategies/{strategy['id']}/check_risk", json=payload)
    assert reviewed.status_code == 200, reviewed.text
    payload.update(review_id=reviewed.json()['review_id'], time_in_force='GTC')
    result=client.post(path,json=payload)
    assert result.status_code==200, result.text
    placed=result.json(); assert placed['broker_name']=='mock'
    duplicate=client.post(path,json=payload)
    assert duplicate.status_code==200 and duplicate.json()['id']==placed['id']
    time.sleep(.03)
    status=client.get(f"/api/action/orders/{placed['id']}?refresh=true").json()
    assert status['status']=='filled'
    history=client.get('/api/action/timeline').json()
    assert history['items']
    with Session(engine) as s:
        assert s.query(ActionDraft).count()==1 and s.query(SymbolStrategy).count()==1 and s.query(OrderRecord).count()==1


def test_invalid_edited_prefill_cannot_persist(api):
    client,engine=api
    draft=client.post('/api/action/trade-link/preview',json=order()|{'request_id':str(uuid4())}).json()
    draft['draft']['payload']['symbol_strategies'][0]['quantity']=1.5
    result=client.post('/api/action/drafts',json={'prefill_request_id':draft['request_id'],'payload':draft['draft']['payload']})
    assert result.status_code==400
    with Session(engine) as s:assert s.query(ActionDraft).count()==0
