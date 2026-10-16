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


def test_real_endpoint_exposes_only_seven_read_only_tools():
    from types import SimpleNamespace
    from pocs.chatgpt_skills.server import create_server
    async def run():
        server=create_server(SimpleNamespace(real_config='not-opened',web_base_url='https://mini.example.ts.net'))
        async with Client(server,mode='legacy') as client:
            tools=(await client.list_tools()).tools
            assert {t.name for t in tools}=={'get_wealth_overview','get_manual_wealth_items','get_investment_details','prepare_trade_confirmation_link','analyze_position','review_portfolio','analyze_performance'}
            assert all(t.annotations.read_only_hint for t in tools)
    asyncio.run(run())


def test_discovery_annotations_and_existing_overview_contract(endpoint):
    async def run():
        async with Client(endpoint,mode='legacy',read_timeout_seconds=50) as client:
            tools=(await client.list_tools()).tools
            by_name={t.name:t for t in tools}
            assert set(by_name)=={'get_wealth_overview','get_manual_wealth_items','get_investment_details','prepare_trade_confirmation_link','create_trade_draft','get_trade_preview','confirm_trade','get_trade_order_status','analyze_position','review_portfolio','analyze_performance'}
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
