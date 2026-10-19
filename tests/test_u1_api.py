"""Minimal app only: no backend lifespan, Gateway or trading clients."""
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from backend.api import asset_analysis as api
from tests.test_u1_asset_views import context, ready  # noqa: F401


def endpoint(c,monkeypatch,tmp_path):
    from app import state
    token=tmp_path/'token';token.write_text('A'*48);token.chmod(0o600)
    monkeypatch.setenv('WEALTHPILOT_ANALYSIS_PREPARE_TOKEN_FILE',str(token))
    monkeypatch.setattr(api,'_preparer',c.service)
    monkeypatch.setattr(api,'DB_PATH',str(c.db));monkeypatch.setattr(state,'portfolio_id',7)
    app=FastAPI();app.include_router(api.router,prefix='/api/asset-analysis')
    return TestClient(app),{'Authorization':'Bearer '+'A'*48}


def test_protected_prepare_cannot_expand_scope_or_submit_orders(context,monkeypatch,tmp_path):
    c=context;client,headers=endpoint(c,monkeypatch,tmp_path)
    assert client.post('/api/asset-analysis/prepare',json={'analysis_request_id':'x'}).status_code==403
    assert c.state.calls==0
    for extra in [{'portfolio_id':8},{'broker':'other'},{'place_order':True},{'database':'/private.db'}]:
        assert client.post('/api/asset-analysis/prepare',headers=headers,json={'analysis_request_id':'x',**extra}).status_code==422
    result=client.post('/api/asset-analysis/prepare',headers=headers,json={'analysis_request_id':'x'})
    assert result.status_code==200 and result.json()['state']=='preparing'
    ready_response=client.get('/api/asset-analysis/requests/x',headers=headers).json()
    assert ready_response['state']=='ready' and c.state.calls==1
    same=client.post('/api/asset-analysis/prepare',headers=headers,json={'analysis_request_id':'x'}).json()
    assert same['snapshot_id']==ready_response['snapshot_id'] and c.state.calls==1
    view=client.get('/api/asset-analysis/views/'+same['snapshot_id']).json()
    assert 'internal' not in view and view['views']['wealth_summary']['total_assets']==1000
    assert client.get('/api/asset-analysis/views/asset-view:v1:99999').status_code==404


def test_existing_web_wealth_and_portfolio_accept_same_snapshot(context,monkeypatch,tmp_path):
    c=context;view=ready(c);client,_=endpoint(c,monkeypatch,tmp_path)
    from backend.api import wealth,portfolio
    from app import state
    app=FastAPI();app.include_router(wealth.router,prefix='/wealth');app.include_router(portfolio.router,prefix='/portfolio')
    reader=TestClient(app);snapshot=view['snapshot_id']
    for path,key in [('/wealth/summary','wealth_summary'),('/portfolio/summary','portfolio_summary'),('/portfolio/positions','positions')]:
        result=reader.get(path,params={'snapshot_id':snapshot}).json()
        assert result.pop('snapshot_id')==snapshot
        assert result.pop('data_quality')==view['data_quality']
        assert result==view['views'][key]
    monkeypatch.setattr(state,'portfolio_id',8)
    assert reader.get('/wealth/summary',params={'snapshot_id':snapshot}).status_code==404


def test_token_file_and_prepare_bridge_fail_closed(tmp_path):
    from pocs.chatgpt_skills.prepare import private_token,PrepareClient
    token=tmp_path/'token';token.write_text('B'*48);token.chmod(0o644)
    with pytest.raises(ValueError):private_token(token)
    token.chmod(0o600);assert private_token(token)=='B'*48
    config=tmp_path/'prepare.json'
    for base in ['http://example.com:8000','http://127.0.0.1:8000/?x=1','http://user@127.0.0.1:8000','https://127.0.0.1:8000','http://127.0.0.1:8000/path']:
        config.write_text(json.dumps({'base_url':base,'token_file':str(token)}));config.chmod(0o600)
        with pytest.raises(ValueError):PrepareClient(config)
