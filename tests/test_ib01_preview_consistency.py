"""Typed owner facts and moving LIVE quotes; disposable SQLite/Fake broker only."""
from decimal import Decimal
import pytest
from sqlalchemy.orm import Session

from app.models import Position, Portfolio
from backend.services.action.models import OrderRecord, ExecutionBatch
from backend.services.execution_batch.service import ExecutionBatchService
from tests.test_ib01_fxquote_preview import cached_fx
from tests.test_ib01_trade_link import api, quoted_request, confirmation


def seed(engine, portfolio_id=1, value=10000):
    with Session(engine) as db:
        if db.get(Portfolio,portfolio_id) is None:
            db.add(Portfolio(id=portfolio_id,name='Synthetic owner'))
        db.add(Position(portfolio_id=portfolio_id, segment='投资', name='Synthetic IB01',
            ticker='IB01', symbol='IB01:LSE', platform='fixture', currency='USD',
            asset_class='固收', quantity=12, cost_price=100, market_value_cny=value))
        db.commit()


def test_nonempty_owner_preview_and_confirm_share_risk(api, cached_fx):
    client,engine,broker=api
    seed(engine);seed(engine,2,999999)
    params,quote=quoted_request(client)
    risk=quote['batch']['execution_policy']['risk_warnings']['IB01']
    assert risk['portfolio_total_value']==10000
    result=client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,quote))
    assert result.status_code==200,result.text
    assert result.json()['execution_policy']['risk_warnings']['IB01']==risk
    assert not broker.place_calls


@pytest.mark.parametrize('change', ['book_only','fees','portfolio'])
def test_pinned_limit_review_accepts_only_immaterial_book_changes(api,cached_fx,monkeypatch,change):
    client,engine,broker=api;seed(engine)
    params,quote=quoted_request(client)
    limit=quote['batch']['legs'][0]['final_limit']
    original=broker.get_executable_quote
    monkeypatch.setattr(broker,'get_executable_quote',lambda r:original(r)|{'ask':125.99,'bid':125.98})
    if change=='fees':
        original_fee=broker.what_if_limit_order
        monkeypatch.setattr(broker,'what_if_limit_order',lambda *a,**kw:original_fee(*a,**kw)|{'commission':Decimal('15')})
    elif change=='portfolio':
        with Session(engine) as db:
            row=db.query(Position).filter_by(portfolio_id=1).one();row.market_value_cny=50000;db.commit()
    result=client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,quote)|{'limit_price':limit})
    assert result.status_code==(200 if change=='book_only' else 422),result.text
    if change!='book_only':assert 'PREVIEW_CHANGED' in result.text
    with Session(engine) as db:assert db.query(OrderRecord).count()==0
    assert not broker.place_calls


def test_recalculate_after_changed_preview_can_continue(api,cached_fx,monkeypatch):
    client,engine,broker=api;seed(engine)
    params,first=quoted_request(client)
    original=broker.get_executable_quote
    monkeypatch.setattr(broker,'get_executable_quote',lambda r:original(r)|{'ask':127,'bid':126.99})
    old=client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,first))
    assert old.status_code==422 and 'PREVIEW_CHANGED' in old.text
    fresh=client.post('/api/execution-batches/trade-link/quote',json=params).json()
    result=client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,fresh)|{'limit_price':fresh['batch']['legs'][0]['final_limit']})
    assert result.status_code==200,result.text
    assert result.json()['status']=='READY' and not broker.place_calls


def test_book_change_still_invalidates_final_authorization(api,cached_fx):
    client,engine,_=api;seed(engine)
    params,quote=quoted_request(client)
    result=client.post('/api/execution-batches/trade-link/confirm-intent',json=confirmation(params,quote))
    assert result.status_code==200,result.text
    with Session(engine) as db:
        batch=db.get(ExecutionBatch,result.json()['id'])
        review=ExecutionBatchService.review_fingerprint(batch)
        authorization=ExecutionBatchService.review_fingerprint(batch,authorization=True)
        batch.legs[0].quote_bid=Decimal('125.98')
        assert ExecutionBatchService.review_fingerprint(batch)==review
        assert ExecutionBatchService.review_fingerprint(batch,authorization=True)!=authorization
