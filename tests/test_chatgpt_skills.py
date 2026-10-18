"""Real business services on disposable SQLite; never real broker/production data."""
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import logging
import sqlite3
import threading
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Portfolio, Position, WealthItem
from backend.services import portfolio_service
from backend.services.action.models import ActionDraft, SymbolStrategy, OrderRecord, AuditLog
from backend.services.action.brokers.mock import MockBrokerAdapter
from backend.services.action.risk_engine import CONFIRMATION_TEXT
from pocs.chatgpt_skills.contracts import ExplicitOrder
from pocs.chatgpt_skills.reader import read_details
from pocs.chatgpt_skills.trades import TradeWorkflow, TradeError


@pytest.fixture
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(portfolio_service, '_get_tiger_account_cash', lambda: (0., []))
    db = tmp_path/'synthetic.db'
    engine = create_engine('sqlite:///'+str(db))
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([Portfolio(id=7, name='synthetic'), Portfolio(id=8, name='other')]);s.flush()
        s.add_all([Position(portfolio_id=7, name='Synthetic A', symbol='AAPL:US', ticker='AAPL', platform='fixture',
                           broker='tiger', broker_account_id='DO_NOT_RETURN_ACCOUNT', sync_source='api',
                           asset_class='股票', currency='USD', segment='投资', quantity=100,
                           market_value_cny=10000, profit_loss_value=350, profit_loss_rate=3.5),
                   Position(portfolio_id=8, name='OTHER_PRIVATE_POSITION', ticker='AAPL', platform='other',
                           asset_class='股票', segment='投资', market_value_cny=99999),
                   WealthItem(portfolio_id=7, kind='ASSET', name='Synthetic house', category='property', item_type='property',
                             current_value=20000, included_in_net_worth=True, value_as_of=date.today(), notes='DO_NOT_RETURN_SECRET_NOTE'),
                   WealthItem(portfolio_id=7, kind='LIABILITY', name='Synthetic credit', category='credit_card', item_type='credit_card',
                             current_value=500, included_in_net_worth=True, value_as_of=date.today()-timedelta(days=40)),
                   WealthItem(portfolio_id=8, kind='ASSET', name='OTHER_PRIVATE_ITEM', category='property', item_type='property',
                             current_value=99999, included_in_net_worth=True, value_as_of=date.today())]);s.commit()
    engine.dispose()
    return db


@pytest.fixture
def workflow(database):
    broker = MockBrokerAdapter(fill_delay_seconds=.02, rejection_probability=0, partial_fill_probability=0, network_failure_probability=0)
    wf = TradeWorkflow(database, 7, broker, synthetic=True, discipline_provider=lambda *a: {'violation':False})
    yield wf
    wf.close()


def order(side='BUY', quantity=1):
    return dict(symbol='AAPL:US', side=side, quantity=quantity, order_type='LIMIT', limit_price='1.00', currency='USD')


def create(wf, request='fixture-request', **kw):
    return wf.create('明确的合成买卖指令', request, order(**kw))


def test_manual_services_scope_units_and_readonly(database):
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    assets = read_details(database, 7, 'get_manual_wealth_items', {'kind':'ASSET'}, synthetic=True)
    liabilities = read_details(database, 7, 'get_manual_wealth_items', {'kind':'LIABILITY'}, synthetic=True)
    assert assets['items'][0]['current_value']==20000
    assert assets['amount_unit']=='major' and assets['synthetic']
    assert liabilities['status']=='partial' and 'STALE_COMPONENTS' in liabilities['issues']
    assert 'DO_NOT_RETURN' not in json.dumps(assets) and 'OTHER_PRIVATE' not in json.dumps(assets)
    assert hashlib.sha256(database.read_bytes()).hexdigest()==before
    with sqlite3.connect(database) as db:assert db.execute('SELECT count(*) FROM wealth_snapshots').fetchone()[0]==0


def test_investment_reuses_existing_values_and_account_filter(database):
    value = read_details(database, 7, 'get_investment_details', {}, synthetic=True)
    assert len(value['items'])==1 and len(value['accounts'])==1
    row=value['items'][0]
    assert row['quantity']==100 and row['market_value_cny']==10000 and row['profit_loss_value']==350
    assert row['symbol_weight_of_investments']==1
    assert row['data_updated_date'] is None and 'VALUATION_DATE_UNKNOWN' in row['issues']
    assert 'DO_NOT_RETURN_ACCOUNT' not in json.dumps(value) and 'OTHER_PRIVATE_POSITION' not in json.dumps(value)
    ref=value['accounts'][0]['account_ref']
    assert read_details(database,7,'get_investment_details',{'account_ref':ref})['items']==value['items']
    assert read_details(database,7,'get_investment_details',{'symbol':'MSFT:US'})['items']==[]


@pytest.mark.parametrize('operation,args', [('get_manual_wealth_items',{'item_id':999}),('get_investment_details',{'account_ref':'invalid'})])
def test_details_unknown_identity_fails(database,operation,args):
    with pytest.raises(Exception):read_details(database,7,operation,args)


def test_cash_failure_keeps_stored_holdings_but_never_claims_complete_zero(database,monkeypatch):
    def failed():
        logging.getLogger('backend.services.portfolio_service').warning('SENSITIVE_FAILURE')
        return 0,[]
    monkeypatch.setattr(portfolio_service,'_get_tiger_account_cash',failed)
    value=read_details(database,7,'get_investment_details',{},cash_configured=True)
    assert value['items'] and value['portfolio_summary'] is None
    assert value['status']=='partial' and 'TIGER_CASH_NOT_SYNCED' in value['issues']
    assert 'SENSITIVE_FAILURE' not in json.dumps(value)


@pytest.mark.parametrize('change', [{'quantity':0},{'quantity':True},{'quantity':1.2},{'limit_price':'nan'},{'limit_price':'-1'},{'side':'HOLD'},{'order_type':'CONDITIONAL_LIMIT'}])
def test_explicit_contract_does_not_guess_missing_or_invalid_order(change):
    with pytest.raises(Exception):ExplicitOrder.model_validate(order()|change)


@pytest.mark.parametrize('side', ['BUY','SELL'])
def test_pending_preview_explicit_confirmation_fill_and_idempotency(workflow,side):
    preview=create(workflow,side=side)
    assert preview['draft_status']=='draft' and not preview['orders']
    assert preview['preview']['side']==side and preview['preview']['quantity']==1
    with pytest.raises(TradeError,match='EXPLICIT_CONFIRMATION_REQUIRED'):
        workflow.confirm(preview['draft_id'],preview['preview_hash'],False)
    placed=workflow.confirm(preview['draft_id'],preview['preview_hash'],True,CONFIRMATION_TEXT)
    assert len(placed['orders'])==1
    replay=workflow.confirm(preview['draft_id'],preview['preview_hash'],True,CONFIRMATION_TEXT)
    assert replay['orders'][0]['order_id']==placed['orders'][0]['order_id']
    deadline=time.monotonic()+2
    while time.monotonic()<deadline:
        status=workflow.status(preview['draft_id'])
        if status['orders'][0]['status']=='filled':break
        time.sleep(.01)
    assert status['orders'][0]['status']=='filled' and status['orders'][0]['filled_quantity']==1
    with Session(workflow.engine) as s:
        assert s.query(OrderRecord).count()==1
        assert s.query(SymbolStrategy).one().cumulative_filled_quantity==1
        assert s.query(AuditLog).filter_by(event_type='mcp_trade_confirmation').count()==1


def test_request_replay_and_changed_payload(workflow):
    a=create(workflow);b=create(workflow)
    assert a['draft_id']==b['draft_id']
    with pytest.raises(TradeError,match='REQUEST_ID_CONFLICT'):create(workflow,quantity=2)


def test_changed_preview_and_original_risk_confirmation(workflow):
    p=create(workflow,quantity=1000)
    assert p['risk']['requires_confirmation'] and p['risk']['confirmation_text_required']==CONFIRMATION_TEXT
    with pytest.raises(TradeError,match='RISK_CONFIRMATION_REQUIRED'):workflow.confirm(p['draft_id'],p['preview_hash'],True)
    with pytest.raises(TradeError,match='PREVIEW_CHANGED_REVIEW_REQUIRED'):workflow.confirm(p['draft_id'],'wrong',True,CONFIRMATION_TEXT)
    assert not workflow.preview(p['draft_id'])['orders']


def test_other_portfolio_cannot_read_or_execute_draft(workflow):
    p=create(workflow)
    other=TradeWorkflow(workflow.path,8,workflow.adapter,synthetic=True,discipline_provider=lambda *a:None)
    with pytest.raises(TradeError,match='DRAFT_NOT_FOUND'):other.preview(p['draft_id'])
    with pytest.raises(TradeError,match='DRAFT_NOT_FOUND'):other.confirm(p['draft_id'],p['preview_hash'],True)
    other.engine.dispose()


@pytest.mark.parametrize('failure,expected',[('reject','rejected'),('network','unknown')])
def test_broker_failure_is_not_retried(workflow,failure,expected):
    setattr(workflow.adapter,'rejection_probability' if failure=='reject' else 'network_failure_probability',1)
    p=create(workflow)
    first=workflow.confirm(p['draft_id'],p['preview_hash'],True,CONFIRMATION_TEXT)
    assert first['orders'][0]['status']==expected
    second=workflow.confirm(p['draft_id'],p['preview_hash'],True,CONFIRMATION_TEXT)
    assert second['orders'][0]['order_id']==first['orders'][0]['order_id']
    with Session(workflow.engine) as s:assert s.query(OrderRecord).count()==1


def test_read_preview_cannot_mutate_database(workflow):
    p=create(workflow)
    before=workflow.path.read_bytes()
    preview=workflow.preview(p['draft_id'])
    assert preview['draft_status']=='draft' and not preview['orders']
    assert workflow.path.read_bytes()==before


def test_confirmation_durable_before_uncertain_submission(workflow,monkeypatch):
    p=create(workflow)
    def crash(*a,**kw):raise RuntimeError('synthetic crash before persisted broker response')
    monkeypatch.setattr(workflow.adapter,'place_order',crash)
    result = workflow.confirm(p['draft_id'],p['preview_hash'],True,CONFIRMATION_TEXT)
    assert result['orders'][0]['status'] == 'unknown'
    again = workflow.confirm(p['draft_id'],p['preview_hash'],True,CONFIRMATION_TEXT)
    assert again['orders'][0]['order_id'] == result['orders'][0]['order_id']


def test_simultaneous_confirmation_creates_only_one_order(workflow):
    p=create(workflow)
    results=[]
    def submit():
        try:results.append(workflow.confirm(p['draft_id'],p['preview_hash'],True,CONFIRMATION_TEXT))
        except TradeError as e:results.append(str(e))
    threads=[threading.Thread(target=submit) for _ in range(2)]
    for t in threads:t.start()
    for t in threads:t.join(timeout=5)
    assert all(not t.is_alive() for t in threads)
    with Session(workflow.engine) as s:assert s.query(OrderRecord).count()==1


def test_default_discipline_stable_readonly_and_no_context_loading(database, monkeypatch):
    from decision_engine import data_loader
    def forbidden(*a, **kw):
        raise AssertionError('Broad context/LLM loading must never run for explicit orders')
    monkeypatch.setattr(data_loader, 'load', forbidden)
    wf = TradeWorkflow(database, 7, MockBrokerAdapter(), synthetic=True)
    try:
        created = create(wf)
        before = database.read_bytes()
        preview = wf.preview(created['draft_id'])
        assert preview['preview_hash'] == created['preview_hash']
        assert preview['risk']['discipline_available']
        assert any(w['rule'] == 'discipline' for w in preview['risk']['warnings'])
        assert database.read_bytes() == before
    finally:
        wf.close()


def test_currency_cannot_mislabel_preview(workflow):
    with pytest.raises(TradeError, match='ORDER_CURRENCY_MARKET_MISMATCH'):
        workflow.create('fixture', 'bad-currency', order() | {'currency':'CNY'})
    with Session(workflow.engine) as session:
        assert session.query(ActionDraft).count() == 0


def test_confirmation_replay_after_holdings_change_never_resubmits(workflow):
    p = create(workflow)
    placed = workflow.confirm(p['draft_id'], p['preview_hash'], True, CONFIRMATION_TEXT)
    with Session(workflow.engine) as session:
        session.query(Position).filter_by(portfolio_id=7).update({'market_value_cny': 1})
        session.commit()
    replay = workflow.confirm(p['draft_id'], p['preview_hash'], True, CONFIRMATION_TEXT)
    assert replay['orders'][0]['order_id'] == placed['orders'][0]['order_id']
    with Session(workflow.engine) as session:
        assert session.query(OrderRecord).count() == 1


def test_partial_fill_status_uses_original_manager(workflow, monkeypatch):
    # Freeze the existing Mock final-fill choice; cancellation is covered separately.
    monkeypatch.setattr("backend.services.action.brokers.mock.random.random", lambda: .5)
    workflow.adapter.partial_fill_probability = 1
    p = create(workflow, quantity=10)
    workflow.confirm(p['draft_id'], p['preview_hash'], True, CONFIRMATION_TEXT)
    time.sleep(.03)
    value = workflow.status(p['draft_id'])
    assert value['orders'][0]['status'] in ('partially_filled', 'filled')
    assert 0 < value['orders'][0]['filled_quantity'] <= 10


def test_restart_lost_mock_broker_state_is_explicit_not_replayed(workflow):
    p = create(workflow)
    placed = workflow.confirm(p['draft_id'], p['preview_hash'], True, CONFIRMATION_TEXT)
    restarted = TradeWorkflow(workflow.path, 7, MockBrokerAdapter(network_failure_probability=0), synthetic=True,
                              discipline_provider=lambda *a: {'violation':False})
    try:
        with pytest.raises(TradeError, match='BROKER_ORDER_STATE_UNAVAILABLE'):
            restarted.status(p['draft_id'])
        assert restarted.confirm(p['draft_id'], p['preview_hash'], True, CONFIRMATION_TEXT)['orders'][0]['order_id'] == placed['orders'][0]['order_id']
    finally:
        restarted.close()


def test_details_single_wal_snapshot_then_new_request_observes_commit(database, monkeypatch):
    with sqlite3.connect(database) as db:
        db.execute('PRAGMA journal_mode=WAL')
    original = portfolio_service.get_positions
    changed = []
    def concurrent_write(*a, **kw):
        value = original(*a, **kw)
        if not changed:
            with sqlite3.connect(database) as writer:
                writer.execute('UPDATE positions SET market_value_cny=20000 WHERE portfolio_id=7')
            changed.append(True)
        return value
    monkeypatch.setattr(portfolio_service, 'get_positions', concurrent_write)
    first = read_details(database, 7, 'get_investment_details', {})
    assert first['items'][0]['market_value_cny'] == first['portfolio_summary']['total_assets'] == 10000
    second = read_details(database, 7, 'get_investment_details', {})
    assert second['items'][0]['market_value_cny'] == second['portfolio_summary']['total_assets'] == 20000


def test_reused_details_service_cannot_accidentally_write(database, monkeypatch):
    from backend.services import wealth_service
    def unexpected_write(*a, **kw):
        session = wealth_service.get_session()
        try:
            session.execute(text("UPDATE wealth_items SET current_value=0"))
            session.commit()
        finally:
            session.close()
    monkeypatch.setattr(wealth_service, 'list_items', unexpected_write)
    before = database.read_bytes()
    with pytest.raises(Exception):
        read_details(database, 7, 'get_manual_wealth_items', {})
    assert database.read_bytes() == before


def test_failed_latest_sync_preserves_success_date_and_holding(database, monkeypatch):
    from datetime import datetime
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    from services.broker_sync.models import PositionSnapshotRun
    engine = create_engine('sqlite:///'+str(database))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            PositionSnapshotRun(portfolio_id=7, broker='tiger', account_id='DO_NOT_RETURN_ACCOUNT', status='success',
                started_at=datetime(2020,1,1), finished_at=datetime(2020,1,1)),
            PositionSnapshotRun(portfolio_id=7, broker='tiger', account_id='DO_NOT_RETURN_ACCOUNT', status='failed',
                started_at=datetime(2020,1,2), finished_at=datetime(2020,1,2), error_message='PRIVATE_ERROR')])
        session.commit()
    engine.dispose()
    value = read_details(database, 7, 'get_investment_details', {})
    assert value['items'][0]['quantity'] == 100
    assert value['items'][0]['data_updated_date'] == '2020-01-01'
    assert {'LATEST_POSITION_SYNC_FAILED','STALE_COMPONENTS'} <= set(value['issues'])
    assert 'PRIVATE_ERROR' not in json.dumps(value)


def test_legacy_web_edit_cannot_execute_unpreviewed_order(workflow):
    p = create(workflow)
    with Session(workflow.engine) as session:
        draft = session.get(ActionDraft, p['draft_id'])
        payload = json.loads(draft.payload)
        payload['symbol_strategies'][0]['quantity'] = 900
        draft.payload = json.dumps(payload)
        session.commit()
    with pytest.raises(TradeError, match='DRAFT_CHANGED_REVIEW_REQUIRED'):
        workflow.confirm(p['draft_id'], p['preview_hash'], True, CONFIRMATION_TEXT)
    with Session(workflow.engine) as session:
        assert session.query(OrderRecord).count() == 0


def test_conditional_limit_preview_matches_original_broker_request(workflow):
    payload = order() | {'order_type':'CONDITIONAL_LIMIT', 'trigger_price':'1.20'}
    p = workflow.create('fixture conditional', 'conditional', payload)
    result = workflow.confirm(p['draft_id'],p['preview_hash'],True,CONFIRMATION_TEXT)
    with Session(workflow.engine) as session:
        record = session.get(OrderRecord, result['orders'][0]['order_id'])
        assert record.order_type == 'CONDITIONAL_LIMIT' and Decimal(str(record.stop_price)) == Decimal('1.20')


def test_zero_cash_is_not_claimed_as_proven_complete_source(database):
    value = read_details(database,7,'get_investment_details',{},cash_configured=True)
    assert value['status'] == 'partial'
    assert value['sources']['tiger_cash'] == 'not_synced'
    assert 'TIGER_CASH_NOT_SYNCED' in value['issues']
