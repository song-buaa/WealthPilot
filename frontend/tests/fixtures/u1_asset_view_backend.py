"""Synthetic U1 HTTP/browser fixture. No production lifespan or real adapters."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import os
import socket
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT));sys.path.append(str(ROOT/'backend'))
keep={k:os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR') if k in os.environ}
os.environ.clear();os.environ.update(keep)
os.environ.update(PYTHON_DOTENV_DISABLED='1',PUBLIC_DEMO_MODE='false',BROKER_MODE='mock',
    IBKR_READ_ONLY_MODE='true',ENABLE_IBKR_LIVE_TRADING='false',ENABLE_TIGER_LIVE_TRADING='false',
    FUTU_READ_ONLY_MODE='true',DEMO_ALLOW_MARKET_DATA='false',AV_DEV_MOCK='1')
original_connect=socket.socket.connect

def connect(sock,address):
    if isinstance(address,tuple) and (address[0] not in ('127.0.0.1','localhost','::1') or address[1] in (4001,4002,7496,7497,11111)):
        raise RuntimeError('U1_FIXTURE_NETWORK_FORBIDDEN')
    return original_connect(sock,address)
socket.socket.connect=connect

with tempfile.TemporaryDirectory(prefix='wealthpilot-u1-http-') as temporary:
    root=Path(temporary);root.chmod(0o700);db=root/'synthetic.db'
    os.environ['WEALTHPILOT_DB_PATH']=str(db)
    os.environ['WEALTHPILOT_ANALYSIS_CACHE_DIR']=str(root/'inputs')
    token=root/'token';token.write_text('u1_synthetic_prepare_token_'+'x'*30);token.chmod(0o600)
    os.environ['WEALTHPILOT_ANALYSIS_PREPARE_TOKEN_FILE']=str(token)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from fastapi import FastAPI,HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    import uvicorn
    from app.models import Base,Portfolio,Position,WealthItem
    from app import state
    from services.broker_sync.models import PositionSnapshotRun  # noqa: F401
    from services.broker_sync.repository import PositionSnapshotRepository
    from services.broker_sync.schema import cash_position
    from backend.services.restricted_sync import SyncExecutor
    from backend.services.asset_analysis import AssetAnalysisPreparation
    from backend.api import asset_analysis as api,wealth,portfolio
    from app.asset_views import load_view,public_view
    engine=create_engine('sqlite:///'+str(db),connect_args={'timeout':10})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Portfolio(id=7,name='Synthetic U1'));session.flush()
        for symbol,currency,qty,cost,value,vehicle in [('LI:US','USD',10,20,300,'ADR'),('2015:HK','HKD',100,8,200,'COMMON_STOCK')]:
            session.add(Position(portfolio_id=7,name='Li Auto fixture',ticker=symbol.split(':')[0],symbol=symbol,
                platform=symbol,currency=currency,original_currency=currency,quantity=qty,cost_price=cost,
                current_price=cost,market_value_cny=value,fx_rate_to_cny=1,fx_rate_date=datetime.now().date().isoformat(),
                asset_class='股票',vehicle_type=vehicle,economic_asset_class='EQUITY',segment='投资'))
        session.add_all([
            WealthItem(portfolio_id=7,kind='ASSET',name='Independent bank',item_type='bank_cash',category='cash_deposits',current_value=50),
            WealthItem(portfolio_id=7,kind='ASSET',name='Pension fixture',item_type='personal_pension',category='retirement_long_term',
                current_value=100,already_investment_accounted=True,included_in_net_worth=False),
            WealthItem(portfolio_id=7,kind='LIABILITY',name='Loan fixture',item_type='consumer_loan',category='consumer_loan',current_value=100)])
        session.commit()
    calls=[0]
    def sessions():return Session(engine,info={'portfolio_id':7})
    def factory(broker):
        def persist(session,triggered_by='analysis'):
            def fetch():
                calls[0]+=1
                return [cash_position(broker,'synthetic-owner','CNY',1000,datetime.now(timezone.utc))]
            return PositionSnapshotRepository(session).synchronize(broker,'synthetic-owner',fetch,retries=0,
                triggered_by=triggered_by,metadata=lambda:{'source':'fixture','complete':True,'cash_as_of':datetime.now(timezone.utc).isoformat()})
        return SimpleNamespace(sync_and_persist=persist)
    def preparer():
        executor=SyncExecutor(['tiger'],7,factory,session_factory=sessions,account_resolver=lambda *_:'synthetic-owner')
        return AssetAnalysisPreparation(executor,db,synthetic=True)
    service=preparer();_,claim=service.request('initial-view');service.run(*claim)
    initial=service.status('initial-view')
    if initial['state']!='ready':raise RuntimeError(str(initial))
    state.portfolio_id=7;api.DB_PATH=str(db);api._preparer=preparer
    config=root/'reader.json';config.write_text(json.dumps({'database_path':str(db),'portfolio_id':7}));config.chmod(0o600)
    prepare_config=root/'prepare.json';prepare_config.write_text(json.dumps({'base_url':'http://127.0.0.1:'+sys.argv[1],'token_file':str(token)}));prepare_config.chmod(0o600)
    app=FastAPI();app.include_router(api.router,prefix='/api/asset-analysis')
    app.include_router(wealth.router,prefix='/api/wealth');app.include_router(portfolio.router,prefix='/api/portfolio')
    @app.get('/api/demo/status')
    def demo_status():return {'public_demo_mode':False}
    @app.get('/__test/context')
    def context():
        return dict(snapshot_id=initial['snapshot_id'],source_calls=calls[0],config=str(config),prepare_config=str(prepare_config),token=str(token))
    @app.post('/__test/change-live')
    def change():
        with sessions() as session:
            for row in session.query(Position):row.market_value_cny=99999
            session.commit()
        return {'changed':True}
    # Browser tests exercise the fixed-ID UI boundary without paid model calls.
    # Real PEER/Skills execution is separately covered by test_u1_asset_views.
    @app.post('/__test/review')
    def no_orders():return {'order_count':0}
    original_worker=api.worker_call
    def browser_peer(database,pid,operation,arguments,**kwargs):
        fixed=public_view(load_view(database,pid,arguments['snapshot_id']))
        return {'snapshot_id':fixed['snapshot_id'],'conclusion':'Fixture PEER: fixed CNY '+str(fixed['views']['wealth_summary']['total_assets']),
            'data_quality':{'asset_view':fixed['data_quality'],'context_current':fixed['context_current']}}
    api.worker_call=browser_peer
    dist=ROOT/'frontend/dist'
    if (dist/'assets').exists():app.mount('/assets',StaticFiles(directory=str(dist/'assets')),name='assets')
    @app.get('/{path:path}')
    def spa(path:str):return FileResponse(dist/'index.html')
    uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),access_log=False,log_level='error')
