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
from backend.api import action, execution_batch
from backend.services.action.brokers.mock import MockBrokerAdapter
from tests.test_execution_batch_service import FakeIBKRExecutionAdapter
from backend.services.execution_batch import link_handoff
from backend.services.execution_batch.service import ExecutionBatchService
from datetime import datetime, timezone

ibkr = FakeIBKRExecutionAdapter()
ibkr_state = {'quality':'LIVE', 'cash':10000, 'fee':5}
_original_quote = ibkr.get_executable_quote
_original_cash = ibkr.get_cash_snapshot
ibkr.get_executable_quote = lambda r: _original_quote(r) | {
    'ask':100, 'bid':99.99, 'quote_timestamp':datetime.now(timezone.utc).isoformat(),
    'quote_quality':ibkr_state['quality'], 'market_data_type':1 if ibkr_state['quality']=='LIVE' else 3}
ibkr.get_cash_snapshot = lambda c: _original_cash(c) | {k:ibkr_state['cash'] for k in ['CashBalance','SettledCash','TotalCashValue']}
_original_what_if = ibkr.what_if_limit_order
def what_if(r, **kwargs):
    return _original_what_if(r,**kwargs) | {'commission':ibkr_state['fee']}
ibkr.what_if_limit_order = what_if
execution_batch._adapter = lambda: ibkr
link_handoff.ExecutionBatchService = lambda s,a: ExecutionBatchService(s,a,allow_mutation=False)
execution_batch._service = lambda s: ExecutionBatchService(s,ibkr,allow_mutation=False)

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
app.include_router(execution_batch.router, prefix='/api/execution-batches')
@app.post('/__test/ibkr-state')
def set_ibkr_state(body: dict):
    ibkr_state.update(body)
    return {'isolated_synthetic_fixture':True, 'real_order_submitted':False}

@app.get('/__test/ibkr-counts')
def ibkr_counts():
    import hashlib
    from sqlalchemy import text
    with get_session() as s:
        names = ['action_drafts', 'allocation_intents', 'symbol_strategies',
                 'order_records', 'execution_batches', 'execution_legs',
                 'conversations', 'conversation_messages']
        counts = {name: s.execute(text(f'SELECT count(*) FROM {name}')).scalar() for name in names}
        fingerprints = {name: hashlib.sha256(repr(s.execute(text(f'SELECT * FROM {name} ORDER BY rowid')).fetchall()).encode()).hexdigest() for name in names}
        return {'batches':counts['execution_batches'], 'orders':counts['order_records'],
                'business_counts':counts, 'business_fingerprints':fingerprints,
                'real_order_calls':len(ibkr.place_calls)}


@app.post('/__test/age-ibkr-reviews')
def age_ibkr_reviews():
    """Make only the persisted review stale; fresh quote generation stays real-time."""
    from datetime import timedelta
    from backend.services.action.models import ExecutionLeg
    with get_session() as s:
        for leg in s.query(ExecutionLeg).all():
            leg.quote_as_of = datetime.now(timezone.utc) - timedelta(seconds=40)
        s.commit()
    return {'isolated_synthetic_fixture': True}

@app.get('/api/demo/status')
def demo(): return {'public_demo_mode': False}
@app.get('/api/{path:path}')
def unused(path: str): return {'items':[], 'total':0}
app.mount('/', StaticFiles(directory=root/'frontend/dist',html=True))
if __name__=='__main__': uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]))
