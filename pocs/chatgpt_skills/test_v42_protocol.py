"""Independent MCP SDK test of the actual request schema and serialized wire."""
import asyncio
import json
from types import SimpleNamespace

import jsonschema
import pytest
from mcp import Client

from pocs.chatgpt_skills.server import BusinessClient, create_server


def test_v42_targets_and_scenarios_roundtrip_actual_mcp_schema(monkeypatch):
    calls = []
    def record(self, operation, arguments):
        calls.append((operation, arguments))
        return dict(synthetic=True, scenario=operation, status='partial', issues=['SYNTHETIC_ONLY'],
                    queried_at='2026-10-22T00:00:00+00:00', conclusion='protocol fixture')
    monkeypatch.setattr(BusinessClient, 'call', record)
    target = dict(version='fixture-v1', stage='transition', confirmed_by_user=True,
                  confirmed_at='2026-10-20T00:00:00Z', valid_until='2026-11-20T00:00:00Z',
                  issuers=[dict(issuer_id='LI_AUTO', target_weight=.25)],
                  funding_policy={'account_fixture':'investment_cash'})
    leg = dict(snapshot_id='asset-view:v1:58', issuer_id='LI_AUTO', position_id=1,
               account_ref='account_fixture', destination='investment_cash', sale_value_cny=100,
               fees_cny=0, tax_cny=0, fx_cost_cny=0, liquidity_floor_cny=0,
               assumptions='Synthetic protocol contract, no broker or provider')
    async def run():
        server = create_server(SimpleNamespace(real_config='not-opened', web_base_url='https://mini.example.ts.net'))
        async with Client(server, mode='legacy') as client:
            tools = {tool.name:tool for tool in (await client.list_tools()).tools}
            for name in ('analyze_position', 'review_portfolio'):
                request = dict(question='测试固定目标与情景', snapshot_id='asset-view:v1:58',
                               symbol='LI:US', target_versions=[target], scenario_legs=[leg])
                jsonschema.validate({'request':request}, tools[name].input_schema)
                result = await client.call_tool(name, {'request':request})
                assert not result.is_error, result.content
                assert calls[-1][0] == name
                sent = calls[-1][1]
                assert sent['target_versions'][0]['version'] == target['version']
                assert sent['target_versions'][0]['issuers'][0]['target_weight'] == .25
                assert sent['target_versions'][0]['funding_policy'] == target['funding_policy']
                assert sent['scenario_legs'][0]['sale_value_cny'] == 100
                assert sent['scenario_legs'][0]['snapshot_id'] == sent['snapshot_id']
                before = len(calls)
                invalid = json.loads(json.dumps(request))
                invalid['scenario_legs'][0]['snapshot_id'] = 'asset-view:v1:61'
                rejected = await client.call_tool(name, {'request':invalid})
                assert rejected.is_error
                assert len(calls) == before
    asyncio.run(run())


def test_server_target_configuration_crosses_business_process_boundary(monkeypatch, tmp_path):
    """The path is server configuration, never a tool argument or confirmation."""
    import subprocess
    import sys
    from pocs.chatgpt_skills import server
    configured = str(tmp_path / 'private-target-store.json')
    monkeypatch.setenv('WEALTHPILOT_STAGE_TARGETS_PATH', configured)
    original = subprocess.Popen
    probe = ('import sys,json,os\n'
        'for line in sys.stdin:\n'
        ' request=json.loads(line)\n'
        ' print(json.dumps({"ok":True,"result":{"configured_path":os.environ.get("WEALTHPILOT_STAGE_TARGETS_PATH"),'
        '"live_trading":os.environ.get("ENABLE_IBKR_LIVE_TRADING")}}),flush=True)\n')
    def spawn(command, **kwargs):
        return original([sys.executable, '-u', '-c', probe], **kwargs)
    monkeypatch.setattr(server.subprocess, 'Popen', spawn)
    client = BusinessClient(SimpleNamespace(business_python=sys.executable, portfolio_id=7,
        real_config=None, mock_database='not-opened'))
    try:
        result = client.call('analyze_position', {'confirmed_by_user':True,
            'WEALTHPILOT_STAGE_TARGETS_PATH':'caller-selected.json'})
        assert result == {'configured_path':configured, 'live_trading':'false'}
    finally:
        client.close()


def test_analysis_run_crosses_real_subprocess_without_broker_permissions(monkeypatch):
    import subprocess
    import sys
    from pocs.chatgpt_skills import server
    original=subprocess.Popen
    probe='''import json,os,sys
from app.llm_observation import AnalysisObservation
for line in sys.stdin:
 request=json.loads(line)
 value=AnalysisObservation(request['analysis_run_id']).snapshot('completed')
 print(json.dumps({'ok':True,'result':{'llm_observation':value,'live':os.getenv('ENABLE_IBKR_LIVE_TRADING'),
  'key_inherited':'WEALTHPILOT_OPENAI_API_KEY' in os.environ}}),flush=True)
'''
    def spawn(command, **kwargs):return original([sys.executable,'-u','-c',probe],**kwargs)
    monkeypatch.setattr(server.subprocess,'Popen',spawn)
    monkeypatch.setenv('WEALTHPILOT_OPENAI_API_KEY','PRIVATE_KEY')
    monkeypatch.setenv('ENABLE_IBKR_LIVE_TRADING','true')
    client=BusinessClient(SimpleNamespace(business_python=sys.executable,portfolio_id=7,real_config=None,mock_database='not-opened'))
    try:
        first=client.call('analyze_position',{'question':'PRIVATE_ASSET'})
        second=client.call('review_portfolio',{'question':'PRIVATE_ASSET'})
        assert first['llm_observation']['analysis_run_id'] != second['llm_observation']['analysis_run_id']
        assert first['live']=='false' and not first['key_inherited']
        assert 'PRIVATE' not in json.dumps(first)
    finally:client.close()


def test_actual_mcp_output_preserves_safe_token_metadata_not_model_content(monkeypatch):
    from app.llm_observation import AnalysisObservation
    from types import SimpleNamespace as NS
    trace=AnalysisObservation()
    call=trace.begin({'model':'gpt-6-luna','reasoning_effort':'low'},'chat')
    trace.complete(call,result=NS(model='gpt-6-luna',usage=NS(prompt_tokens=100,completion_tokens=20,
        completion_tokens_details=NS(reasoning_tokens=10)),service_tier='default'),raw=NS(retries_taken=0),standard_text=True)
    metadata=trace.snapshot('completed');metadata['private_asset']='PRIVATE_ASSET'
    def record(self,operation,arguments):
        return dict(synthetic=True,scenario=operation,status='partial',issues=[],queried_at='2026-10-23T00:00:00Z',
            conclusion='evidence-filtered explanation',llm_observation=metadata)
    monkeypatch.setattr(BusinessClient,'call',record)
    async def run():
        server=create_server(SimpleNamespace(real_config='not-opened',web_base_url='https://mini.example.ts.net'))
        async with Client(server,mode='legacy') as client:
            value=await client.call_tool('analyze_position',{'request':{'question':'分析'}})
            assert not value.is_error
            result=value.structured_content
            row=result['llm_observation']['calls'][0]
            assert row['input_tokens']==100 and row['reasoning_tokens']==10
            assert 'PRIVATE_ASSET' not in json.dumps(result)
            assert not result['order_created']
            rejected=await client.call_tool('analyze_position',{'request':{'question':'分析','analysis_run_id':'a'*32}})
            assert rejected.is_error
    asyncio.run(run())


def test_mcp_error_metadata_rejects_wrong_analysis_run_and_preserves_safe_business_errors(monkeypatch):
    import io
    from mcp.server.mcpserver.exceptions import ToolError
    from app.llm_observation import unavailable
    from pocs.chatgpt_skills import server as module
    stale=unavailable('b'*32,status='timeout')
    child=SimpleNamespace(stdin=io.StringIO(),stdout=io.StringIO(json.dumps(
        {'ok':False,'error':'BUSINESS_OPERATION_UNAVAILABLE','llm_observation':stale})+'\n'))
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**kw:child)
    monkeypatch.setattr(module.select,'select',lambda *a:([child.stdout],[],[]))
    client=BusinessClient(SimpleNamespace(business_python='unused',portfolio_id=7,real_config=None,mock_database='unused'))
    with pytest.raises(ToolError,match='ANALYSIS_RUN_ID_MISMATCH'):
        client.call('analyze_position',{'question':'fixture'})
    def fail(self,operation,arguments):
        error=ToolError('PORTFOLIO_NOT_FOUND');error.llm_observation=stale;raise error
    monkeypatch.setattr(BusinessClient,'call',fail)
    async def run():
        server=create_server(SimpleNamespace(real_config='not-opened',web_base_url='https://mini.example.ts.net'))
        async with Client(server,mode='legacy') as client:
            result=await client.call_tool('analyze_position',{'request':{'question':'fixture'}})
            assert result.is_error and 'PORTFOLIO_NOT_FOUND' in str(result.content)
    asyncio.run(run())
