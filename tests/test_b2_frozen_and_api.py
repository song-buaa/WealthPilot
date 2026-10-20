"""History reads use original frozen input/API/PEER, not a second asset snapshot."""
import asyncio
import json
import pytest
from sqlalchemy.orm import Session
from app.models import Position
from tests.test_b2_history import persist,closed
from tests.test_analysis_skills import peer  # noqa: F401

@pytest.fixture
def context(tmp_path,monkeypatch):
    from tests.test_u1_asset_views import context as original
    yield from original.__wrapped__(tmp_path,monkeypatch)

def seed_owner(c):
    with c.sessions() as db:
        db.add(Position(portfolio_id=7,name='Synthetic closed IB01',ticker='IB01',symbol='IB01:LSE',
            broker='snowball',broker_account_id='fixture',sync_source='api',platform='IBKR',asset_class='固收',
            quantity=0,currency='USD',original_currency='USD',classification_evidence_json='{"con_id":354802220}'))
        db.commit()

def test_frozen_history_never_gets_later_fee_enrichment_and_original_assets_unchanged(context,peer):
    from tests.test_u1_asset_views import ready
    from app.asset_views import load_view
    from pocs.chatgpt_skills.reader import read_history
    from pocs.chatgpt_skills.analysis import analyze
    c=context;seed_owner(c)
    facts=closed();facts[0]['commission']=None;persist(c,facts)
    view=ready(c,'b2-freeze');saved=json.dumps(load_view(c.db,7,view['snapshot_id']),sort_keys=True)
    persist(c)
    live=read_history(c.db,7,{'symbol':'IB01'},synthetic=True)
    frozen=read_history(c.db,7,{'symbol':'IB01','snapshot_id':view['snapshot_id']},synthetic=True)
    assert live['attribution'][0]['realized_pnl']=='198'
    assert frozen['attribution'][0]['realized_pnl'] is None and frozen['snapshot_id']==view['snapshot_id']
    result=asyncio.run(analyze(c.db,7,'analyze_performance',dict(question='Historical facts and current holdings',
        snapshot_id=view['snapshot_id']),synthetic=True))
    history=result['evidence']['historical_attribution']
    assert history['snapshot_id']==view['snapshot_id'] and history['attribution'][0]['realized_pnl'] is None
    assert result['evidence']['asset_view']['wealth_summary']==view['views']['wealth_summary']
    assert json.dumps(load_view(c.db,7,view['snapshot_id']),sort_keys=True)==saved

def test_web_mcp_projection_same_read_contract_without_full_backend(context,monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api import portfolio
    from pocs.chatgpt_skills.reader import read_history
    from app import database,state
    c=context;seed_owner(c);persist(c)
    monkeypatch.setattr(database,'DB_PATH',str(c.db));monkeypatch.setattr(state,'portfolio_id',7)
    app=FastAPI();app.include_router(portfolio.router,prefix='/api/portfolio')
    response=TestClient(app).get('/api/portfolio/trade-history?symbol=IB01')
    assert response.status_code==200,response.text
    web=response.json();mcp=read_history(c.db,7,{'symbol':'IB01'},synthetic=False)
    assert web['items']==mcp['items'] and web['attribution']==mcp['attribution']
    assert TestClient(app).get('/api/portfolio/trade-history?limit=201').status_code==422
    assert TestClient(app).get('/api/portfolio/trade-history?account_ref=raw-account').status_code==422
