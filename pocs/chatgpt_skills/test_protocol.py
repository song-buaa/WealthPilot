"""Independent MCP runtime exercises business subprocesses on a marked synthetic DB."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from urllib.error import HTTPError

import jsonschema
from mcp import Client
import pytest

ROOT = Path(__file__).resolve().parents[2]
BUSINESS = os.environ['WP_BUSINESS_PYTHON']


def test_worker_receives_operator_model_policy_without_broker_or_key_inheritance(monkeypatch):
    import io
    from types import SimpleNamespace
    from pocs.chatgpt_skills import server
    from pocs.chatgpt_skills.worker import MODEL_ENV_KEYS
    seen={}
    policy={key:"fixture-policy" for key in MODEL_ENV_KEYS}
    for key,value in policy.items():monkeypatch.setenv(key,value)
    monkeypatch.setenv("WEALTHPILOT_OPENAI_API_KEY","PRIVATE_SENTINEL")
    monkeypatch.setenv("ENABLE_IBKR_LIVE_TRADING","true")
    child=SimpleNamespace(stdin=io.StringIO(),stdout=io.StringIO('{"ok":true,"result":{"fixture":true}}\n'))
    def spawn(*args,**kwargs):seen.update(kwargs);return child
    monkeypatch.setattr(server.subprocess,"Popen",spawn)
    monkeypatch.setattr(server.select,"select",lambda *args:([child.stdout],[],[]))
    client=server.BusinessClient(SimpleNamespace(business_python=BUSINESS,portfolio_id=7,
        real_config=None,mock_database="synthetic-not-opened",analysis_env_file=None))
    assert client.call("get_wealth_overview",{})=={"fixture":True}
    assert {key:seen["env"][key] for key in MODEL_ENV_KEYS}==policy
    assert "WEALTHPILOT_OPENAI_API_KEY" not in seen["env"]
    assert seen["env"]["ENABLE_IBKR_LIVE_TRADING"]=="false" and seen["env"]["BROKER_MODE"]=="mock"


@pytest.fixture
def endpoint(tmp_path):
    db=tmp_path/'synthetic.db'
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR') if k in os.environ}
    env.update(PYTHON_DOTENV_DISABLED='1',WEALTHPILOT_DB_PATH=str(db),PUBLIC_DEMO_MODE='true',
               DEMO_ACCESS_PASSWORD='synthetic-fixture',BROKER_MODE='mock',DEMO_ALLOW_MARKET_DATA='false')
    subprocess.run([BUSINESS,str(ROOT/'pocs/chatgpt_skills/worker.py'),'--init-mock',str(db),'--portfolio-id','7'],
                   env=env,cwd=ROOT,check=True,capture_output=True)
    seed="""from app.models import Position,get_session
s=get_session();s.add(Position(portfolio_id=7,name='Synthetic asset',ticker='AAPL',symbol='AAPL:US',platform='fixture',currency='USD',asset_class='股票',segment='投资',market_value_cny=10000,quantity=100));s.commit();s.close()
"""
    subprocess.run([BUSINESS,'-c',seed],env=env,cwd=ROOT,check=True,capture_output=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    log=tempfile.TemporaryFile()
    process=subprocess.Popen([sys.executable,str(ROOT/'pocs/chatgpt_skills/server.py'),'--port',str(port),
                              '--web-base-url','https://mini.example.ts.net',
                              '--business-python',BUSINESS,'--mock-database',str(db),'--portfolio-id','7'],
                             cwd=ROOT,env=env,stdout=log,stderr=log)
    endpoint=f'http://127.0.0.1:{port}/mcp'
    try:
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            if process.poll() is not None:
                log.seek(0);raise AssertionError(log.read().decode()[-3000:])
            try:urlopen(endpoint,timeout=.2)
            except HTTPError:break
            except OSError:time.sleep(.05)
        else:raise AssertionError('server did not start')
        yield endpoint
    finally:
        process.terminate()
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=3)
        log.close()


def test_link_tool_never_persists_a_draft(endpoint, tmp_path):
    async def run():
        before=hashlib.sha256((tmp_path/'synthetic.db').read_bytes()).hexdigest()
        async with Client(endpoint,mode='legacy') as client:
            result=await client.call_tool('prepare_trade_confirmation_link', {'order': {
                'symbol':'NVDA:US','side':'BUY','quantity':10,'order_type':'LIMIT',
                'limit_price':'180','currency':'USD'}})
            assert not result.is_error, result.content
            value=result.structured_content
            assert value['url'].startswith('https://mini.example.ts.net/#/action?')
            assert value['draft_created'] is False and value['order_submitted'] is False
        assert hashlib.sha256((tmp_path/'synthetic.db').read_bytes()).hexdigest()==before
    asyncio.run(run())


def test_ib01_budget_link_is_pure_and_exposes_verified_identity(endpoint, tmp_path):
    async def run():
        before=hashlib.sha256((tmp_path/'synthetic.db').read_bytes()).hexdigest()
        async with Client(endpoint,mode='legacy') as client:
            result=await client.call_tool('prepare_trade_confirmation_link', {'order': {
                'broker':'IBKR','symbol':'IB01:UK','side':'BUY','order_type':'LIMIT',
                'target_amount':'4000','currency':'USD'}})
            assert not result.is_error,result.content
            value=result.structured_content
            assert value['instrument']['con_id']==354802220
            assert value['execution_path']=='ibkr_batch'
            assert value['order']['symbol']=='IB01:LSE'
            assert value['order']['target_amount']=='4000'
            assert value['draft_created'] is False and value['order_submitted'] is False
        assert hashlib.sha256((tmp_path/'synthetic.db').read_bytes()).hexdigest()==before
    asyncio.run(run())


def test_real_endpoint_preserves_seven_tools_and_adds_readonly_trade_sync_query():
    from types import SimpleNamespace
    from pocs.chatgpt_skills.server import create_server
    async def run():
        server=create_server(SimpleNamespace(real_config='not-opened',web_base_url='https://mini.example.ts.net'))
        async with Client(server,mode='legacy') as client:
            tools=(await client.list_tools()).tools
            assert {t.name for t in tools}=={'get_wealth_overview','get_manual_wealth_items','get_investment_details','prepare_trade_confirmation_link','analyze_position','review_portfolio','analyze_performance','get_trade_and_sync_status','get_trade_history','get_instrument_tradability'}
            assert all(t.annotations.read_only_hint for t in tools)
    asyncio.run(run())


def test_analysis_discovery_and_calls_keep_nonblank_validation_without_regex(monkeypatch):
    """Live tools/list + call over the MCP runtime, no business/provider I/O."""
    from types import SimpleNamespace
    from pocs.chatgpt_skills.server import create_server, BusinessClient
    calls = []
    def original_business_contract(self, operation, arguments):
        calls.append((operation, arguments))
        return dict(synthetic=True,scenario=operation,status='partial',issues=['SYNTHETIC_ONLY'],
            queried_at='2026-10-20T00:00:00+00:00',conclusion=arguments['question'])
    monkeypatch.setattr(BusinessClient, 'call', original_business_contract)
    async def run():
        server = create_server(SimpleNamespace(real_config='not-opened',web_base_url='https://mini.example.ts.net'))
        async with Client(server,mode='legacy') as client:
            tools = {t.name:t for t in (await client.list_tools()).tools}
            assert len(tools) == 10 and all(t.annotations.read_only_hint for t in tools.values())
            for name in ('review_portfolio','analyze_position','analyze_performance'):
                schema = tools[name].input_schema
                # Resolve through JSON Schema, not a handwritten mirror of it.
                for question in ('请重新评估我的投资组合', 'Review my portfolio allocation'):
                    arguments = {'request':{'question':question,'analysis_scope':'net_worth'}}
                    jsonschema.validate(arguments, schema)
                    result = await client.call_tool(name, arguments)
                    assert not result.is_error, result.content
                    assert result.structured_content['conclusion'] == question
                    assert calls[-1][1]['analysis_scope'] == 'net_worth'
                assert '"pattern": "\\\\S"' not in json.dumps(schema)
                assert 'analysis_scope' in json.dumps(schema)
                before = len(calls)
                for request in ({'question':' \t\n\u3000'}, {'question':''}, {'question':'x'*6001},
                        {'question':'Valid question','analysis_scope':'all'},
                        {'question':'Valid question','force':True},
                        {'question':'Valid question','snapshot_id':'invalid'}):
                    result = await client.call_tool(name, {'request':request})
                    assert result.is_error
                assert len(calls) == before  # Reject before reaching business/PEER.
    asyncio.run(run())


def test_trade_and_sync_query_is_readonly_and_does_not_expose_raw_sdk(endpoint,tmp_path):
    import sqlite3
    db=tmp_path/'synthetic.db'
    with sqlite3.connect(db) as s:
        s.execute("INSERT INTO symbol_strategies(id,symbol,side,target_quantity,cumulative_filled_quantity,order_type,status,created_at,updated_at) VALUES('u2-strategy','IB01:LSE','BUY',2,2,'LIMIT','completed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)")
        s.execute("INSERT INTO order_records(id,strategy_id,broker_name,portfolio_id,broker_account_id,symbol,side,quantity,filled_quantity,order_type,status,raw_broker_response,fill_sync_json,created_at) VALUES('u2-order','u2-strategy','ibkr',7,'DO_NOT_EXPOSE','IB01:LSE','BUY',2,2,'LIMIT','filled',?, ?,CURRENT_TIMESTAMP)",
            (json.dumps({'raw_error_message':'DO_NOT_EXPOSE_TOKEN'}),json.dumps({'version':1,'anchor':None,'target_quantity':2})))
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    async def run():
        async with Client(endpoint,mode='legacy') as client:
            result=await client.call_tool('get_trade_and_sync_status',{'request':{'symbol':'IB01:LSE'}})
            assert not result.is_error,result.content
            item=result.structured_content['items'][0]
            assert item['trade_status']=='filled' and item['asset_sync']['status']=='SYNC_PENDING'
            assert item['actual_commission']['status']=='UNKNOWN'
            assert 'DO_NOT_EXPOSE' not in json.dumps(result.structured_content)
            tools={t.name:t for t in (await client.list_tools()).tools}
            assert tools['get_trade_and_sync_status'].annotations.read_only_hint
            rejected=await client.call_tool('get_trade_and_sync_status',{'request':{'order_id':'u2-order','refresh':True}})
            assert rejected.is_error
    asyncio.run(run())
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before


def test_discovery_annotations_and_existing_overview_contract(endpoint):
    async def run():
        async with Client(endpoint,mode='legacy',read_timeout_seconds=50) as client:
            tools=(await client.list_tools()).tools
            by_name={t.name:t for t in tools}
            assert set(by_name)=={'get_wealth_overview','get_manual_wealth_items','get_investment_details','prepare_trade_confirmation_link','create_trade_draft','get_trade_preview','confirm_trade','get_trade_order_status','analyze_position','review_portfolio','analyze_performance','get_trade_and_sync_status','get_trade_history','get_instrument_tradability'}
            assert by_name['get_trade_and_sync_status'].annotations.read_only_hint
            assert by_name['prepare_trade_confirmation_link'].annotations.read_only_hint
            assert all(tool.meta['securitySchemes'] == [{'type':'noauth'}] for tool in tools)
            assert by_name['get_investment_details'].annotations.read_only_hint
            assert not by_name['create_trade_draft'].annotations.read_only_hint
            assert by_name['confirm_trade'].annotations.destructive_hint
            assert not by_name['get_trade_order_status'].annotations.read_only_hint
            for name in ('get_wealth_overview','get_manual_wealth_items','get_investment_details'):
                result=await client.call_tool(name,{})
                assert not result.is_error, result.content
                jsonschema.validate(result.structured_content,by_name[name].output_schema)
                assert result.structured_content['synthetic'] is True
            overview=(await client.call_tool('get_wealth_overview',{})).structured_content
            assert overview['total_assets_minor']==1000000 and overview['schema_version']=='poc02.wealth-overview.v1'
    asyncio.run(run())


def test_mcp_confirm_and_broker_state_end_to_end(endpoint):
    async def run():
        async with Client(endpoint,mode='legacy',read_timeout_seconds=50) as client:
            schemas={tool.name: tool.output_schema for tool in (await client.list_tools()).tools}
            arguments={'instruction':'合成测试：限价1美元买AAPL 1股','request_id':'e2e-request',
                       'order':{'symbol':'AAPL:US','side':'BUY','quantity':1,'limit_price':'1','currency':'USD'}}
            pending=await client.call_tool('create_trade_draft',arguments)
            assert not pending.is_error, pending.content
            jsonschema.validate(pending.structured_content, schemas['create_trade_draft'])
            p=pending.structured_content
            assert p['draft_status']=='draft' and p['orders']==[]
            preview=await client.call_tool('get_trade_preview',{'draft_id':p['draft_id']})
            assert preview.structured_content['preview_hash']==p['preview_hash']
            confirm={'draft_id':p['draft_id'],'preview_hash':p['preview_hash'],'confirmed':False}
            denied=await client.call_tool('confirm_trade',confirm)
            assert denied.is_error and 'EXPLICIT_CONFIRMATION_REQUIRED' in denied.content[0].text
            placed=await client.call_tool('confirm_trade',confirm|{'confirmed':True,'risk_confirmation':'我已知晓风险并坚持下单'})
            assert not placed.is_error, placed.content
            jsonschema.validate(placed.structured_content, schemas['confirm_trade'])
            assert placed.structured_content['synthetic'] and placed.structured_content['broker']=='mock'
            for _ in range(30):
                status=await client.call_tool('get_trade_order_status',{'draft_id':p['draft_id']})
                assert not status.is_error,status.content
                if status.structured_content['orders'][0]['status']=='filled':break
                await asyncio.sleep(.05)
            jsonschema.validate(status.structured_content, schemas['get_trade_order_status'])
            assert status.structured_content['orders'][0]['status']=='filled'
            replay=await client.call_tool('confirm_trade',confirm|{'confirmed':True,'risk_confirmation':'我已知晓风险并坚持下单'})
            assert not replay.is_error
            assert replay.structured_content['orders'][0]['order_id']==placed.structured_content['orders'][0]['order_id']
    asyncio.run(run())


@pytest.mark.parametrize('name,args',[('place_order',{}),('get_wealth_overview',{'currency':'USD'}),
    ('create_trade_draft',{'instruction':'猜价格','request_id':'invalid','order':{'symbol':'AAPL:US','side':'BUY','quantity':1}})])
def test_errors_do_not_break_subsequent_read(endpoint,name,args):
    async def run():
        async with Client(endpoint,mode='legacy',read_timeout_seconds=50) as client:
            result=await client.call_tool(name,args)
            assert result.is_error and 'Traceback' not in result.content[0].text
            result=await client.call_tool('get_investment_details',{})
            assert not result.is_error
    asyncio.run(run())


def test_analysis_failure_contract_and_original_reads_survive(endpoint,tmp_path):
    async def run():
        before=hashlib.sha256((tmp_path/'synthetic.db').read_bytes()).hexdigest()
        async with Client(endpoint,mode='legacy',read_timeout_seconds=60) as client:
            tools={t.name:t for t in (await client.list_tools()).tools}
            for name in ('analyze_position','review_portfolio','analyze_performance'):
                invalid=await client.call_tool(name,{'request':{'question':' '}})
                assert invalid.is_error
            result=await client.call_tool('analyze_performance',{'request':{'question':'fixture current P&L only'}})
            assert not result.is_error,result.content
            jsonschema.validate(result.structured_content,tools['analyze_performance'].output_schema)
            assert result.structured_content['status'] in ('partial','unavailable')
            assert result.structured_content['order_created'] is False
            assert result.structured_content['synthetic'] is True
            original=await client.call_tool('get_investment_details',{})
            assert not original.is_error
        assert hashlib.sha256((tmp_path/'synthetic.db').read_bytes()).hexdigest()==before
    asyncio.run(run())


def test_trade_history_protocol_scoped_readonly_facts_and_no_import_tool(endpoint,tmp_path):
    seed='''from app.database import get_engine
from sqlalchemy.orm import Session
from app.models import Position
from tests.test_b2_history import persist
from types import SimpleNamespace
engine=get_engine()
with Session(engine) as db:
    db.add(Position(portfolio_id=7,name='Closed synthetic IB01',ticker='IB01',symbol='IB01:LSE',platform='IBKR',asset_class='固收',broker='snowball',broker_account_id='fixture',sync_source='api',currency='USD',original_currency='USD',quantity=0,classification_evidence_json='{"con_id":354802220}'));db.commit()
persist(SimpleNamespace(engine=engine))
'''
    db=tmp_path/'synthetic.db'
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR') if k in os.environ}
    env.update(PYTHON_DOTENV_DISABLED='1',WEALTHPILOT_DB_PATH=str(db),PUBLIC_DEMO_MODE='true',
        DEMO_ACCESS_PASSWORD='synthetic-fixture',BROKER_MODE='mock',DEMO_ALLOW_MARKET_DATA='false')
    subprocess.run([BUSINESS,'-c',seed],env=env,cwd=ROOT,check=True,capture_output=True)
    before=hashlib.sha256(db.read_bytes()).hexdigest()
    async def run():
        async with Client(endpoint,mode='legacy') as client:
            tools={t.name:t for t in (await client.list_tools()).tools}
            tool=tools['get_trade_history']
            assert tool.annotations.read_only_hint and not tool.annotations.destructive_hint
            assert 'import_trade_history' not in tools and 'sync_trade_history' not in tools
            args={'request':{'symbol':'IB01','start':'2025-01-01T00:00:00Z'}}
            jsonschema.validate(args,tool.input_schema)
            result=await client.call_tool('get_trade_history',args)
            assert not result.is_error,result.content
            r=result.structured_content
            assert r['synthetic'] and len(r['items'])==1 and r['items'][0]['side']=='SELL'
            assert r['attribution'][0]['realized_pnl']=='198'
            assert 'fixture' not in json.dumps(r) and 'account_id' not in json.dumps(r)
            for invalid in ({'force':True},{'refresh':True},{'portfolio_id':8},{'account_id':'fixture'},
                            {'limit':201},{'account_ref':'raw-account'}):
                rejected=await client.call_tool('get_trade_history',{'request':invalid})
                assert rejected.is_error
    asyncio.run(run())
    assert hashlib.sha256(db.read_bytes()).hexdigest()==before


def test_b3_tradability_tool_is_readonly_unknown_without_account_evidence(endpoint,tmp_path):
    import sqlite3
    with sqlite3.connect(tmp_path/'synthetic.db') as db:
        db.execute("UPDATE positions SET broker='snowball',broker_account_id='synthetic-owner',sync_source='api' WHERE portfolio_id=7")
    async def run():
        before=hashlib.sha256((tmp_path/'synthetic.db').read_bytes()).hexdigest()
        async with Client(endpoint,mode='legacy') as client:
            tools={t.name:t for t in (await client.list_tools()).tools}
            tool=tools['get_instrument_tradability']
            assert tool.annotations.read_only_hint and not tool.annotations.destructive_hint
            result=await client.call_tool(tool.name,{'request':{'broker':'ibkr','symbol':'IB01:LSE'}})
            assert not result.is_error,result.content
            assert result.structured_content['tradability']=='UNKNOWN'
            assert result.structured_content['fees']['status']=='UNKNOWN'
            assert result.structured_content['account_ref'].startswith('account_')
            assert 'synthetic-owner' not in json.dumps(result.structured_content)
            for changes in ({'account_id':'raw-secret'},{'portfolio_id':999},{'force':True},{'refresh':True},{'submit':True}):
                rejected=await client.call_tool(tool.name,{'request':{'broker':'ibkr','symbol':'IB01:LSE'}|changes})
                assert rejected.is_error
            foreign=await client.call_tool(tool.name,{'request':{'broker':'ibkr','symbol':'IB01:LSE','account_ref':'account_'+'0'*20}})
            assert foreign.is_error
        assert hashlib.sha256((tmp_path/'synthetic.db').read_bytes()).hexdigest()==before
    asyncio.run(run())
