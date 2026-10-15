"""Automated browser fixture only: existing Action API and disposable Mock DB."""
import os
from pathlib import Path
import sys
import tempfile

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
temporary = tempfile.TemporaryDirectory(prefix='trade-link-browser-')
os.environ.update(WEALTHPILOT_DB_PATH=str(Path(temporary.name)/'fixture.db'),
                  PYTHON_DOTENV_DISABLED='1', PUBLIC_DEMO_MODE='true', BROKER_MODE='mock',
                  DEMO_ACCESS_PASSWORD='isolated-fixture', DEMO_ALLOW_MARKET_DATA='false',
                  IBKR_READ_ONLY_MODE='true', ENABLE_IBKR_LIVE_TRADING='false', ENABLE_TIGER_LIVE_TRADING='false')
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
from app.database import Base, get_engine, get_session
from app.models import Portfolio
from backend.api import action
from backend.services.action.brokers.mock import MockBrokerAdapter

Base.metadata.create_all(get_engine())
with get_session() as s:
    s.add(Portfolio(id=1,name='Synthetic')); s.commit()
broker = MockBrokerAdapter(fill_delay_seconds=.05, rejection_probability=0,
                          partial_fill_probability=0, network_failure_probability=0)
action._get_adapter = lambda: broker
action._get_portfolio_total_value = lambda s: 100000
action._get_position_value = lambda *a: 0
action._get_discipline_result = lambda *a: None
action._get_fx_rate_to_cny = lambda *a: 1
app = FastAPI(); app.include_router(action.router, prefix='/api/action')
@app.get('/api/demo/status')
def demo(): return {'public_demo_mode': False}
@app.get('/api/{path:path}')
def unused(path: str): return {'items':[], 'total':0}
app.mount('/', StaticFiles(directory=root/'frontend/dist',html=True))
if __name__=='__main__': uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]))
