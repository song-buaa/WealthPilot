"""Opt-in prepare uses protected HTTP; seven existing tools retain RO semantics."""
import asyncio
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.request import urlopen
import pytest
from mcp import Client
from pocs.chatgpt_skills.server import create_server, BusinessClient

ROOT=Path(__file__).resolve().parents[2]
BUSINESS=os.environ['WP_BUSINESS_PYTHON']


@pytest.fixture
def context():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base='http://127.0.0.1:'+str(port)
    process=subprocess.Popen([BUSINESS,str(ROOT/'frontend/tests/fixtures/u1_asset_view_backend.py'),str(port)],
        cwd=ROOT,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        until=time.monotonic()+30
        while time.monotonic()<until:
            if process.poll() is not None:raise AssertionError('isolated U1 backend exited')
            try:
                with urlopen(base+'/__test/context',timeout=1) as r:config=json.load(r)
                break
            except OSError:time.sleep(.1)
        else:raise AssertionError('isolated U1 backend timeout')
        yield base,config
    finally:
        process.terminate();process.wait(timeout=10)


def test_prepare_http_idempotency_and_original_read_tools(context):
    base,config=context
    async def run():
        settings=SimpleNamespace(real_config=config['config'],prepare_config=config['prepare_config'],
            business_python=BUSINESS,portfolio_id=7,mock_database=None,web_base_url=base)
        server=create_server(settings)
        async with Client(server,mode='legacy') as client:
            tools=(await client.list_tools()).tools
            assert len(tools)==11
            assert all(t.annotations.read_only_hint for t in tools if t.name!='prepare_asset_analysis')
            prepare=next(t for t in tools if t.name=='prepare_asset_analysis')
            assert prepare.annotations.read_only_hint is False and prepare.annotations.idempotent_hint is True
            assert 'FIRST call prepare_asset_analysis' in server.instructions
            first=await client.call_tool('prepare_asset_analysis',{'analysis_request_id':'protocol-task'})
            assert not first.is_error,first.content
            result=first.structured_content
            assert result['state']=='ready' and result['snapshot_id']
            second=await client.call_tool('prepare_asset_analysis',{'analysis_request_id':'protocol-task'})
            assert second.structured_content['snapshot_id']==result['snapshot_id']
            assert result['web_view_url'].endswith('/#/asset-view/'+result['snapshot_id'])
            overview=await client.call_tool('get_wealth_overview',{'snapshot_id':result['snapshot_id']})
            details=await client.call_tool('get_investment_details',{'snapshot_id':result['snapshot_id']})
            manual=await client.call_tool('get_manual_wealth_items',{'snapshot_id':result['snapshot_id'],'kind':'LIABILITY'})
            assert not overview.is_error and not details.is_error and not manual.is_error
            assert overview.structured_content['total_assets_minor']==145000
            assert details.structured_content['portfolio_summary']['total_assets']==1500
            assert details.structured_content['issuer_exposure']['companies'][0]['market_value_cny']==500
            assert len(manual.structured_content['items'])==1
            assert all(r.structured_content['snapshot_id']==result['snapshot_id'] for r in (overview,details,manual))
            assert 'internal' not in json.dumps(result) and 'synthetic-owner' not in json.dumps(details.structured_content)
        with urlopen(base+'/__test/context') as r:assert json.load(r)['source_calls']==1
    asyncio.run(run())


def test_business_worker_receives_private_cache_location_only(monkeypatch,tmp_path):
    """Both launchers must find the same context without inheriting broker secrets."""
    import pocs.chatgpt_skills.server as module
    seen={}
    monkeypatch.setenv('WEALTHPILOT_ANALYSIS_CACHE_DIR',str(tmp_path/'inputs'))
    monkeypatch.setenv('TIGER_PRIVATE_KEY','synthetic-do-not-inherit')
    process=SimpleNamespace(stdin=io.StringIO(),stdout=io.StringIO('{"ok":true,"result":{"fixed":true}}\n'),
                            poll=lambda:None)
    def launch(*args,**kwargs):
        seen.update(kwargs['env']);return process
    monkeypatch.setattr(module.subprocess,'Popen',launch)
    monkeypatch.setattr(module.select,'select',lambda *args:([process.stdout],[],[]))
    client=BusinessClient(SimpleNamespace(business_python=BUSINESS,portfolio_id=7,real_config='fixture.json',mock_database=None))
    assert client.call('review_portfolio',{'question':'synthetic'})=={'fixed':True}
    assert seen['WEALTHPILOT_ANALYSIS_CACHE_DIR']==str(tmp_path/'inputs')
    assert 'TIGER_PRIVATE_KEY' not in seen
