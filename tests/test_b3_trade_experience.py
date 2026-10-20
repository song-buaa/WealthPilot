"""B3 uses original request/owner/adapter paths on disposable SQLite only."""
import json
from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.database import Base
from app.models import Portfolio, Position
from backend.services.action.models import ExecutionBatch, OrderRecord
from backend.services.instruments.tradability import TradabilityQuery, query
from pocs.chatgpt_skills.reader import account_ref
from tests.test_ib01_trade_link import api, budget  # original isolated fixture


def prepared(client):
    params=budget(request_id=str(uuid4()))
    quote=client.post('/api/execution-batches/trade-link/quote',json=params).json()
    body=params|dict(confirmed=True,preview_hash=quote['preview_hash'],quote_as_of=quote['batch']['legs'][0]['quote_as_of'])
    batch=client.post('/api/execution-batches/trade-link/confirm-intent',json=body).json()
    return params,batch


def test_restore_matches_full_request_no_new_quote_batch_or_order(api):
    client,engine,adapter=api
    params,batch=prepared(client)
    before=len(adapter.what_if_calls)
    for _ in range(3):
        restored=client.post('/api/execution-batches/trade-link/restore',json=params)
        assert restored.status_code==200 and restored.json()['batch']['id']==batch['id']
    assert len(adapter.what_if_calls)==before and not adapter.place_calls
    for changes in ({'target_amount':'5000'},{'time_in_force':'GTC'},{'symbol':'IB01:US'}):
        assert client.post('/api/execution-batches/trade-link/restore',json=params|changes).status_code==422
    with Session(engine) as db:
        assert db.query(ExecutionBatch).count()==1 and db.query(OrderRecord).count()==0


def test_restore_terminal_results_and_portfolio_boundary(api):
    client,engine,_=api;params,batch=prepared(client)
    with Session(engine) as db:
        row=db.get(ExecutionBatch,batch['id']);row.status='COMPLETED';row.legs[0].status='FILLED';db.commit()
    assert client.post('/api/execution-batches/trade-link/restore',json=params).json()['batch']['status']=='COMPLETED'
    with Session(engine) as db:
        row=db.get(ExecutionBatch,batch['id']);policy=json.loads(row.execution_policy);policy['portfolio_id']=9;row.execution_policy=json.dumps(policy);db.commit()
    assert client.post('/api/execution-batches/trade-link/restore',json=params).json()=={'batch':None}


@pytest.fixture
def holdings(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'backend'))
    import services.broker_sync.models  # register the original U0 tables in this isolated fixture
    import backend.services.portfolio_history.models  # register B2 without any production access
    engine=create_engine('sqlite:///'+str(tmp_path/'b3.db'),connect_args={'check_same_thread':False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([Portfolio(id=1,name='one'),Portfolio(id=2,name='two')]);db.flush()
        for pid,broker,acct in [(1,'snowball','synthetic-ibkr'),(1,'tiger','synthetic-tiger'),(2,'snowball','other-owner')]:
            db.add(Position(portfolio_id=pid,name='Synthetic',platform='fixture',asset_class='股票',symbol='IB01:LSE' if broker=='snowball' else 'AAPL:US',broker=broker,broker_account_id=acct,sync_source='api'))
        db.commit()
    yield engine
    engine.dispose()


def ask(engine,broker='ibkr',adapter=None,**changes):
    with Session(engine) as db:
        return query(db,1,{'broker':broker,'symbol':'IB01:LSE' if broker=='ibkr' else 'AAPL:US'}|changes,adapter=adapter,synthetic=True)


def evidence(**changes):
    return dict(instrument=dict(con_id=354802220,isin='IE00BGSF1X88',symbol='IB01',local_symbol='IB01',currency='USD',sec_type='STK',exchange='LSEETF'),source='SYNTHETIC_CONTRACT_READ',market='LSEETF')|changes


def test_catalog_whatif_or_trade_flag_does_not_prove_account_permission(holdings):
    assert ask(holdings)['tradability']=='UNKNOWN'
    adapter=SimpleNamespace(broker_name='ibkr',_account_id='synthetic-ibkr',instrument_details=lambda _:evidence(trade=True,what_if='PASS',buying_power=100000))
    result=ask(holdings,adapter=adapter)
    assert result['tradability']=='UNKNOWN' and result['fees']['status']=='UNKNOWN'
    assert result['instrument']['currency']=='USD'
    assert 'synthetic-ibkr' not in json.dumps(result)


def test_explicit_product_restriction_no_and_unsupported_fund_unknown(holdings):
    adapter=SimpleNamespace(broker_name='ibkr',_account_id='synthetic-ibkr',instrument_details=lambda _:evidence(buy_restricted=True))
    assert ask(holdings,adapter=adapter)['tradability']=='NO'
    adapter.instrument_details=lambda _:evidence(instrument=dict(sec_type='FUND',symbol='IB01'))
    assert ask(holdings,adapter=adapter)['tradability']=='UNKNOWN'


@pytest.mark.parametrize('identity',[{'con_id':999},{'isin':'IE00B3VWN179'},{'symbol':'IB01:US'}])
def test_all_listing_identity_components_must_match(holdings,identity):
    adapter=SimpleNamespace(broker_name='ibkr',_account_id='synthetic-ibkr',instrument_details=lambda _:evidence(buy_restricted=True))
    result=ask(holdings,adapter=adapter,**identity)
    assert result['tradability']=='UNKNOWN' and result['instrument'] is None
    assert 'INSTRUMENT_IDENTITY_CONFLICT' in result['issues']


def test_wrong_account_and_cross_portfolio_refs_never_contact_broker(holdings):
    with Session(holdings) as db:ref=account_ref(2,db.query(Position).filter_by(portfolio_id=2).first())
    with pytest.raises(ValueError,match='ACCOUNT_NOT_FOUND'):ask(holdings,account_ref=ref)
    adapter=SimpleNamespace(broker_name='ibkr',_account_id='other-owner',instrument_details=lambda _:pytest.fail('wrong owner reached broker'))
    with pytest.raises(ValueError,match='ACCOUNT_NOT_FOUND'):ask(holdings,adapter=adapter)


def test_read_io_has_no_sqlite_transaction_and_errors_sanitized(holdings):
    with Session(holdings) as db:
        def read(_):
            assert not db.in_transaction()
            raise ConnectionError('DO_NOT_EXPOSE_ACCOUNT_OR_CREDENTIAL')
        adapter=SimpleNamespace(broker_name='ibkr',_account_id='synthetic-ibkr',instrument_details=read)
        result=query(db,1,{'broker':'ibkr','symbol':'IB01:LSE'},adapter=adapter)
        assert 'BROKER_QUERY_UNAVAILABLE' in result['issues']
        assert 'DO_NOT_EXPOSE' not in json.dumps(result)


@pytest.mark.parametrize('extra',[{'account_id':'other'},{'portfolio_id':2},{'refresh':True},{'force':True},{'side':'SELL'},{'quantity':1}])
def test_no_mutation_or_owner_override_parameters(extra):
    with pytest.raises(ValueError):TradabilityQuery.model_validate({'broker':'ibkr','symbol':'IB01:LSE'}|extra)


def test_protected_endpoint_authentication_and_fixed_owner(holdings,monkeypatch,tmp_path):
    from backend.api import action
    token=tmp_path/'token';token.write_text('s'*48);token.chmod(0o600)
    monkeypatch.setenv('WEALTHPILOT_ANALYSIS_PREPARE_TOKEN_FILE',str(token))
    monkeypatch.setattr(action,'get_session',lambda:Session(holdings,info={'portfolio_id':1}))
    adapter=SimpleNamespace(broker_name='ibkr',_account_id='synthetic-ibkr',instrument_details=lambda _,**kwargs:evidence())
    monkeypatch.setattr(action,'_get_ibkr_adapter',lambda:adapter)
    app=FastAPI();app.include_router(action.router,prefix='/api/action')
    with TestClient(app) as client:
        body={'broker':'ibkr','symbol':'IB01:LSE'}
        assert client.post('/api/action/instrument-tradability',json=body).status_code==403
        headers={'Authorization':'Bearer '+'s'*48}
        result=client.post('/api/action/instrument-tradability',json=body,headers=headers)
        assert result.status_code==200 and result.json()['tradability']=='UNKNOWN'
        assert client.post('/api/action/instrument-tradability',json=body|{'account_id':'bad'},headers=headers).status_code==422


def test_ibkr_catalog_uses_only_contract_details_not_order_or_whatif():
    import asyncio
    from ib_async import Contract,ContractDetails,TagValue
    from backend.services.action.brokers.ibkr import IBKRBrokerAdapter
    adapter=IBKRBrokerAdapter.__new__(IBKRBrokerAdapter)
    adapter._account_id='synthetic-ibkr';adapter._timeout=1
    adapter._ensure_connected=lambda **kwargs:None
    seen=[]
    detail=ContractDetails(contract=Contract(conId=354802220,symbol='IB01',localSymbol='IB01',secType='STK',exchange='LSEETF',primaryExchange='LSEETF',currency='USD'),secIdList=[TagValue('ISIN','IE00BGSF1X88')],orderTypes='LMT',validExchanges='LSEETF',minSize=1)
    async def request(contract):seen.append(contract);return [detail]
    adapter._ib=SimpleNamespace(reqContractDetailsAsync=request,isConnected=lambda:True)
    adapter._run_on_loop=lambda work,**_:asyncio.run(work())
    facts=adapter.instrument_details({'con_id':354802220,'symbol':'IB01:LSE'})
    assert facts['instrument']['con_id']==354802220 and facts['order_types']==['LMT']
    assert len(seen)==1
    adapter.instrument_details({'isin':'IE00BGSF1X88'})
    assert seen[-1].secIdType=='ISIN'


@pytest.mark.parametrize('trade,close_only,restricted',[(True,False,False),(False,False,True),(True,True,True),(None,None,False)])
def test_tiger_catalog_keeps_product_flags_and_requires_sdk_account(trade,close_only,restricted):
    from backend.services.action.brokers.tiger import TigerBrokerAdapter
    adapter=TigerBrokerAdapter.__new__(TigerBrokerAdapter);adapter._account_id='synthetic-tiger'
    contract=SimpleNamespace(symbol='AAPL',local_symbol='AAPL',currency='USD',sec_type='STK',exchange='NASDAQ',name='Synthetic',market='US',lot_size=1,trade=trade,close_only=close_only)
    adapter._trade_client=SimpleNamespace(_account='synthetic-tiger',get_contract=lambda *a,**k:contract)
    facts=adapter.instrument_details({'symbol':'AAPL:US'})
    assert facts['buy_restricted'] is restricted
    adapter._trade_client._account='wrong'
    with pytest.raises(ValueError,match='ACCOUNT_NOT_FOUND'):adapter.instrument_details({'symbol':'AAPL:US'})
    assert adapter.instrument_details({'isin':'US0378331005'})=={}


def test_tiger_endpoint_reuses_configured_mode_not_default_paper(holdings,monkeypatch,tmp_path):
    from backend.api import action
    token=tmp_path/'token';token.write_text('t'*48);token.chmod(0o600)
    monkeypatch.setenv('WEALTHPILOT_ANALYSIS_PREPARE_TOKEN_FILE',str(token))
    monkeypatch.setattr(action,'get_session',lambda:Session(holdings,info={'portfolio_id':1}))
    monkeypatch.setattr(action,'_BROKER_MODE','tiger.live')
    facts=dict(instrument=dict(symbol='AAPL',local_symbol='AAPL',currency='USD',sec_type='STK',exchange='NASDAQ'),market='US',source='TIGER_GET_CONTRACT',buy_restricted=True)
    adapter=SimpleNamespace(broker_name='tiger',_account_id='synthetic-tiger',instrument_details=lambda _:facts)
    monkeypatch.setattr(action,'_get_adapter',lambda:adapter)
    app=FastAPI();app.include_router(action.router,prefix='/api/action')
    with TestClient(app) as client:
        result=client.post('/api/action/instrument-tradability',json={'broker':'tiger','symbol':'AAPL:US'},headers={'Authorization':'Bearer '+'t'*48})
        assert result.status_code==200 and result.json()['tradability']=='NO'


def test_stored_query_also_rejects_shared_owner_across_portfolios(holdings):
    with Session(holdings) as db:
        db.add(Position(portfolio_id=2,name='Conflict',platform='fixture',asset_class='股票',broker='snowball',broker_account_id='synthetic-ibkr',sync_source='api'));db.commit()
    with pytest.raises(ValueError,match='ACCOUNT_NOT_FOUND'):ask(holdings)


def test_foreign_request_cannot_reenter_link_handoff_or_broker_io(api):
    client,engine,adapter=api;params,batch=prepared(client)
    with Session(engine) as db:
        row=db.get(ExecutionBatch,batch['id']);policy=json.loads(row.execution_policy);policy['portfolio_id']=2;row.execution_policy=json.dumps(policy);db.commit()
    count=len(adapter.what_if_calls)
    body=params|dict(confirmed=True,preview_hash=batch['confirmation_hash'] or 'f'*64,quote_as_of=batch['legs'][0]['quote_as_of'])
    result=client.post('/api/execution-batches/trade-link/confirm-intent',json=body)
    assert result.status_code==422 and result.json()['detail']=='TRADE_LINK_OWNER_MISMATCH'
    assert len(adapter.what_if_calls)==count and not adapter.place_calls


def test_prepare_bridge_targets_only_protected_read_endpoint(holdings,monkeypatch,tmp_path):
    import io
    from pocs.chatgpt_skills.prepare import PrepareClient
    from backend.api import action
    token=tmp_path/'token';token.write_text('b'*48);token.chmod(0o600)
    config=tmp_path/'bridge';config.write_text(json.dumps({'base_url':'http://127.0.0.1:18321','token_file':str(token)}));config.chmod(0o600)
    monkeypatch.setenv('WEALTHPILOT_ANALYSIS_PREPARE_TOKEN_FILE',str(token))
    monkeypatch.setattr(action,'get_session',lambda:Session(holdings,info={'portfolio_id':1}))
    adapter=SimpleNamespace(broker_name='ibkr',_account_id='synthetic-ibkr',instrument_details=lambda _,**kwargs:evidence())
    monkeypatch.setattr(action,'_get_ibkr_adapter',lambda:adapter)
    app=FastAPI();app.include_router(action.router,prefix='/api/action')
    bridge=PrepareClient(config);calls=[]
    with TestClient(app) as api_client:
        def open_request(request,timeout):
            calls.append(request.full_url)
            assert request.full_url=='http://127.0.0.1:18321/api/action/instrument-tradability'
            result=api_client.post('/api/action/instrument-tradability',content=request.data,headers=dict(request.header_items()))
            assert result.status_code==200,result.text
            return io.BytesIO(result.content)
        bridge.opener=SimpleNamespace(open=open_request)
        result=bridge.tradability({'broker':'ibkr','symbol':'IB01:LSE'})
        assert result['evidence_source']=='SYNTHETIC_CONTRACT_READ' and result['tradability']=='UNKNOWN'
        with pytest.raises(ValueError):bridge.tradability({'broker':'ibkr','symbol':'IB01:LSE','base_url':'http://other'})
    assert len(calls)==1  # No prepare, synchronize, WhatIf or order endpoint.


def test_budget_remaining_is_not_account_cash_remaining(api):
    from backend.api.execution_batch import _serialize_batch
    from backend.services.execution_batch.service import ExecutionBatchService
    from decimal import Decimal
    client,engine,_=api;_,batch=prepared(client)
    with Session(engine) as db:
        row=db.get(ExecutionBatch,batch['id'])
        row.stated_cash=Decimal('4000');row.usable_cash=Decimal('10000')
        row.estimated_total=Decimal('3900');row.estimated_fees=Decimal('5')
        row.safety_cushion=Decimal('25');row.estimated_residual=Decimal('6070')
        fingerprint=ExecutionBatchService.review_fingerprint(row)
        result=_serialize_batch(row)
        assert result['all_in_budget_remaining']==70
        assert result['estimated_residual']==6070 and result['usable_cash']==10000
        assert result['estimated_total']==3900 and result['estimated_fees']==5
        assert fingerprint==ExecutionBatchService.review_fingerprint(row)
