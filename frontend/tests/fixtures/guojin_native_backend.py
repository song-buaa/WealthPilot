"""Original native sync/Web/MCP over temporary synthetic WAL. No full lifespan."""
import os
from pathlib import Path
import sys
import tempfile

root=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(root));sys.path.insert(0,str(root/'backend'))
temporary=tempfile.TemporaryDirectory(prefix='guojin-browser-')
base=Path(temporary.name).resolve()
os.environ.update(PYTHON_DOTENV_DISABLED='1',WEALTHPILOT_DB_PATH=str(base/'fixture.db'),
    PUBLIC_DEMO_MODE='true',DEMO_ACCESS_PASSWORD='synthetic-only',BROKER_MODE='mock',DEMO_ALLOW_MARKET_DATA='false',
    IBKR_READ_ONLY_MODE='true',ENABLE_IBKR_LIVE_TRADING='false',ENABLE_TIGER_LIVE_TRADING='false')
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
from app.database import Base, get_engine, get_session
from app.models import Portfolio
from backend.services import portfolio_service, wealth_service
from backend.services.restricted_sync import SyncExecutor
from backend.services.broker_sync.guojin.sync_service import GuojinSyncService
from tests.guojin_native_fixtures import make_batch
from pocs.chatgpt_skills.reader import read_details

fixture=make_batch(base)
os.environ['GUOJIN_NATIVE_CONFIG']=str(fixture.config_path)
os.environ.pop('GUOJIN_ACCOUNT',None)
with get_engine().connect() as db:db.exec_driver_sql('PRAGMA journal_mode=WAL')
Base.metadata.create_all(get_engine())
with get_session() as db:db.add(Portfolio(id=7,name='Synthetic'));db.commit()
# Only a local fixture app is exposed; no complete backend lifespan/poller.
import backend.core.demo_mode as demo
demo.PUBLIC_DEMO_MODE=False
executor=SyncExecutor(['guojin'],7,lambda _:GuojinSyncService(fixture.config_path))

def synchronize():
    jobs,_=executor.reserve('guojin')
    for job in jobs:executor.execute('guojin',job['run_id'])
    return {'brokers_triggered':['guojin'],'message':'fixture sync'}
synchronize()
app=FastAPI()
@app.get('/api/demo/status')
def demo_status():return {'public_demo_mode':False}
@app.get('/api/portfolio/summary')
def summary():return portfolio_service.get_summary(7)
@app.get('/api/portfolio/positions')
def positions():return portfolio_service.get_positions(7)
@app.get('/api/wealth/summary')
def wealth():return wealth_service.get_summary(7)
@app.get('/api/wealth/items')
def items():return {'items':[],'total':0}
@app.get('/api/broker-sync/status')
def status():return executor.status()
@app.post('/api/broker-sync/trigger')
def trigger():return synchronize()
@app.get('/test/mcp')
def mcp():return read_details(base/'fixture.db',7,'get_investment_details',{},synthetic=True)
@app.post('/test/incomplete')
def incomplete():(fixture.directory/'sgt.xls').unlink();return {'fixture_only':True}
@app.get('/api/{path:path}')
def unused(path:str):return [] if 'targets' in path else {'items':[],'total':0}
app.mount('/',StaticFiles(directory=root/'frontend/dist',html=True),name='frontend')
if __name__=='__main__':uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]))
