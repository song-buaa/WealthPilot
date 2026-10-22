"""Independent MCP SDK test of the actual request schema and serialized wire."""
import asyncio
import json
from types import SimpleNamespace

import jsonschema
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
