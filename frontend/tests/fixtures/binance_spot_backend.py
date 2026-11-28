"""Synthetic Spot adapter -> original SQLite/services -> real built React, no lifespan."""
import os
from pathlib import Path
import sys
import tempfile
from datetime import date

root = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(root), str(root / 'backend')]
temporary = tempfile.TemporaryDirectory(prefix='binance-browser-')
os.environ.update(WEALTHPILOT_DB_PATH=str(Path(temporary.name) / 'fixture.db'),
                  PUBLIC_DEMO_MODE='false', PYTHON_DOTENV_DISABLED='1')
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn
from app.database import Base, get_engine, get_session
from app.models import Portfolio
import backend.services.action.models
import backend.services.portfolio_history.models
from services.broker_sync.models import PositionSnapshotRun
from app.fx_service import fx_service, FXQuote
from backend.services import portfolio_service, wealth_service
from tests.test_binance_spot import Source, CONFIG
from backend.services.broker_sync.binance.sync_service import BinanceSyncService

Base.metadata.create_all(get_engine())
with get_session() as db:
    db.add(Portfolio(id=7, name='Synthetic Binance account'))
    db.commit()
    db.info['portfolio_id'] = 7
    fx_service.get_quote = lambda *_: FXQuote(7, date.today().isoformat(), 'synthetic', 'fixture', 'current')
    BinanceSyncService(CONFIG, client=Source().client()).sync_and_persist(db)
app = FastAPI()

@app.get('/api/demo/status')
def demo():
    return {'public_demo_mode': False}

@app.get('/api/portfolio/summary')
def summary():
    return portfolio_service.get_summary(7)

@app.get('/api/portfolio/positions')
def positions():
    return portfolio_service.get_positions(7)

@app.get('/api/wealth/summary')
def wealth():
    return wealth_service.get_summary(7)

@app.get('/api/broker-sync/status')
def status():
    return {'brokers': [dict(broker='binance', platform='Binance Spot', enabled=True,
        last_sync_status='success', last_position_count=2)]}

@app.get('/api/{path:path}')
def unused(path: str):
    return [] if 'targets' in path else {'items': [], 'total': 0}

app.mount('/', StaticFiles(directory=root / 'frontend' / 'dist', html=True))
if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]))
