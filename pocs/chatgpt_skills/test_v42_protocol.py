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


@pytest.mark.parametrize('status', ['ok', 'partial', 'unavailable'])
def test_normal_analysis_business_status_is_not_protocol_success(monkeypatch, tmp_path, status):
    from pocs.chatgpt_skills.diagnostics import request_run
    from app.llm_observation import AnalysisObservation
    path = tmp_path / 'safe-events.jsonl'
    seen = []
    def answer(self, operation, arguments):
        seen.append(request_run.get())
        return dict(synthetic=True, scenario=operation, status=status, issues=[],
            queried_at='2026-10-27T00:00:00Z', conclusion='synthetic',
            llm_observation=AnalysisObservation(request_run.get()).snapshot('completed'))
    monkeypatch.setattr(BusinessClient, 'call', answer)
    async def run():
        server = create_server(SimpleNamespace(real_config='not-opened', events=str(path)))
        async with Client(server, mode='legacy') as client:
            result = await client.call_tool('analyze_position', {'request':{'question':'PRIVATE_PROMPT'}})
            assert not result.is_error
            assert result.structured_content['status'] == status
    asyncio.run(run())
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert {row['analysis_run_id'] for row in records} == set(seen)
    final = records[-1]
    assert not final['protocol_is_error'] and final['business_status'] == status
    assert final['provider_request_count'] == 'UNKNOWN'
    assert final['model_call_count'] == 0  # observed empty adapter trace, not an inferred zero
    assert 'PRIVATE_PROMPT' not in path.read_text()
    assert path.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('mode,category,stage', [
    ('start', 'worker_start_failed', 'worker_start'),
    ('timeout', 'ipc_timeout', 'ipc_wait'),
    ('exit', 'worker_exited', 'ipc_wait'),
    ('json', 'invalid_json', 'ipc_receive'),
    ('shape', 'invalid_envelope', 'ipc_receive'),
    ('refused', 'worker_start_refused', 'worker_start'),
    ('validation', 'validation_failed', 'pydantic_validation'),
])
def test_normal_mcp_failure_safe_classification_no_replay(monkeypatch, tmp_path, mode, category, stage):
    import io
    from pocs.chatgpt_skills import server as module
    path = tmp_path / 'safe-events.jsonl'
    calls = []
    payload = {'ok':True, 'result':{'unexpected':'PRIVATE_RESULT'}}
    wire = ('PRIVATE_INVALID_JSON\n' if mode == 'json' else
            json.dumps([]) + '\n' if mode == 'shape' else
            '' if mode == 'exit' else
            json.dumps({'ok':False,'error':'WORKER_START_REFUSED'})+'\n' if mode == 'refused' else
            json.dumps(payload)+'\n')
    child = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(wire),
        poll=lambda: 9 if mode == 'exit' else None, wait=lambda **_:0)
    def spawn(*args, **kwargs):
        calls.append(kwargs)
        if mode == 'start':
            raise OSError('PRIVATE_KEY_AND_ACCOUNT')
        return child
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    monkeypatch.setattr(module.select, 'select', lambda *args:([],[],[]) if mode == 'timeout' else ([child.stdout],[],[]))
    async def run():
        server = create_server(SimpleNamespace(real_config='not-opened', events=str(path),
            business_python='not-executed', portfolio_id=7, mock_database=None))
        async with Client(server, mode='legacy') as client:
            result = await client.call_tool('analyze_position', {'request':{'question':'PRIVATE_PROMPT'}})
            assert result.is_error
            assert 'PRIVATE' not in str(result.content)
    asyncio.run(run())
    records = [json.loads(line) for line in path.read_text().splitlines()]
    final = records[-1]
    assert final['protocol_is_error'] and final['business_status'] == 'UNKNOWN'
    assert final['failure_stage'] == stage and final['safe_error_category'] == category
    assert final['model_call_count'] == 'UNKNOWN'
    assert final['provider_request_count'] == 'UNKNOWN'
    assert ('worker_exit_code' in final) == (mode == 'exit')
    assert len({row['analysis_run_id'] for row in records}) == 1
    assert len(calls) == 1 and 'PRIVATE' not in path.read_text()


@pytest.mark.parametrize('fail', [False, True, 'serialization'])
def test_real_worker_progress_and_observed_model_failure_are_correlated(monkeypatch, tmp_path, fail):
    """Real Worker.main + pipes + Pydantic + MCP; only analyze is replaced, all network denied."""
    import subprocess
    import sys
    from pocs.chatgpt_skills import server as module
    database = tmp_path / 'frozen-synthetic.db'
    database.touch()
    path = tmp_path / 'safe-events.jsonl'
    original = subprocess.Popen
    probe = '''import sys,socket
from types import SimpleNamespace as NS
from pocs.chatgpt_skills import worker,analysis
from app.llm_observation import observed_result,current
socket.socket.connect=lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'))
@observed_result
async def frozen(*args,**kwargs):
 print('PRIVATE_BUSINESS_STDOUT')
 print('PRIVATE_BUSINESS_STDERR',file=sys.stderr)
 trace=current()
 call=trace.begin({'model':'gpt-6.1-sol','reasoning_effort':'medium'},'chat')
 trace.complete(call,result=NS(model='gpt-6.1-sol',usage=None,service_tier='default'),standard_text=True)
 if FAIL is True: raise RuntimeError('PRIVATE_MODEL_RESPONSE')
 if FAIL == 'serialization':
  cyclic={};cyclic['cycle']=cyclic;return cyclic
 return dict(synthetic=True,scenario='analyze_position',status='partial',issues=[],queried_at='2026-10-27T00:00:00Z',conclusion='synthetic')
analysis.analyze=frozen
sys.argv=['worker','--frozen-database',DATABASE]
worker.main()
'''.replace('FAIL', repr(fail)).replace('DATABASE', repr(str(database)))
    def spawn(command, **kwargs):
        return original([__import__('os').environ.get('WP_BUSINESS_PYTHON',sys.executable),'-u','-c',probe], **kwargs)
    monkeypatch.setattr(module.subprocess, 'Popen', spawn)
    async def run():
        server = create_server(SimpleNamespace(real_config=None, mock_database='not-opened',
            events=str(path), business_python=sys.executable, portfolio_id=7))
        async with Client(server, mode='legacy') as client:
            result = await client.call_tool('analyze_position', {'request':{'question':'PRIVATE_PROMPT'}})
            if fail == 'serialization':
                assert result.is_error and 'PRIVATE' not in str(result.content)
                return
            assert not result.is_error, result.content
            assert result.structured_content['status'] == ('unavailable' if fail else 'partial')
            assert result.structured_content['llm_observation']['call_count'] == 1
            assert not result.structured_content['order_created']
    asyncio.run(run())
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len({row['analysis_run_id'] for row in records}) == 1
    assert {'mcp_received','worker_start','ipc_wait','request_received','analyze_peer'} <= {r['stage'] for r in records}
    assert records[-1]['model_call_count'] == 1
    assert records[-1]['provider_request_count'] == 'UNKNOWN'
    assert records[-1]['failure_stage'] == ('result_serialization' if fail == 'serialization' else 'analyze_peer' if fail else None)
    assert records[-1]['protocol_is_error'] == (fail == 'serialization')
    assert 'PRIVATE' not in path.read_text()


@pytest.mark.parametrize('final_frame', [False, True])
def test_progress_and_error_frames_cannot_spoof_identity(monkeypatch, final_frame):
    import io
    from pocs.chatgpt_skills import server as module
    from pocs.chatgpt_skills.diagnostics import diagnostic
    from mcp.server.mcpserver.exceptions import ToolError
    wire = json.dumps({'ok':False,'error':'BUSINESS_OPERATION_UNAVAILABLE','analysis_diagnostic':diagnostic('b'*32,'analyze_peer')} if final_frame else
        {'type':'analysis_diagnostic','diagnostic':diagnostic('b'*32,'analyze_peer')})+'\n'
    child = SimpleNamespace(stdin=io.StringIO(),stdout=io.StringIO(wire))
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**kw:child)
    monkeypatch.setattr(module.select,'select',lambda *a:([child.stdout],[],[]))
    client = BusinessClient(SimpleNamespace(business_python='unused',portfolio_id=7,real_config=None,mock_database=None))
    with pytest.raises(ToolError,match='ANALYSIS_RUN_ID_MISMATCH') as caught:
        client.call('analyze_position',{})
    assert caught.value.analysis_diagnostic['safe_error_category'] == 'run_id_mismatch'


def test_diagnostic_projection_drops_private_payload_and_optional_sink_failure(monkeypatch, tmp_path):
    from pocs.poc02_wealth_mcp.server import record_analysis_event
    from pocs.chatgpt_skills.diagnostics import diagnostic
    value = diagnostic('a'*32,'analyze_peer')
    value.update(prompt='PRIVATE_PROMPT', provider_request_count=99, exception='PRIVATE_KEY',
        observed_peer_stages=['PRIVATE_PROMPT'],model_error_categories=['PRIVATE_KEY'])
    path = tmp_path / 'events.jsonl'
    record_analysis_event(SimpleNamespace(events=str(path)),value)
    assert 'PRIVATE' not in path.read_text()
    assert json.loads(path.read_text())['provider_request_count'] == 'UNKNOWN'
    assert json.loads(path.read_text())['observed_peer_stages'] == 'UNKNOWN'
    record_analysis_event(SimpleNamespace(events=str(tmp_path/'missing'/'events')),value)


def test_progress_deadline_is_absolute(monkeypatch):
    import io
    from pocs.chatgpt_skills import server as module
    from pocs.chatgpt_skills.diagnostics import request_run, diagnostic
    from mcp.server.mcpserver.exceptions import ToolError
    token = request_run.set('a'*32)
    frame = json.dumps({'type':'analysis_diagnostic','diagnostic':diagnostic('a'*32,'analyze_peer')})+'\n'
    child = SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(frame), poll=lambda:None)
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**kw:child)
    monkeypatch.setattr(module.select,'select',lambda *a:([child.stdout],[],[]))
    # Start, deadline construction, first read, then over the same 270-second deadline.
    clock = iter([0, 0, 1, 1, 271, 271])
    monkeypatch.setattr(module.time,'monotonic',lambda:next(clock,271))
    client = BusinessClient(SimpleNamespace(business_python='unused',portfolio_id=7,real_config=None,mock_database=None))
    try:
        with pytest.raises(ToolError) as caught:
            client.call('analyze_position',{})
        assert caught.value.analysis_diagnostic['safe_error_category'] == 'ipc_timeout'
    finally:
        request_run.reset(token)


def test_untrusted_diagnostic_shapes_fail_closed():
    from pocs.chatgpt_skills.diagnostics import diagnostic, safe_diagnostic
    value = diagnostic('a'*32,'analyze_peer')
    for extra in ({'stage':[]}, {'safe_error_category':{}}, {'business_status':[]}, {'analysis_run_id':'PRIVATE'}):
        assert safe_diagnostic(value | extra,'a'*32) is None


def test_cleanup_failure_never_replaces_business_result(monkeypatch, tmp_path):
    path = tmp_path/'events.jsonl'
    def answer(self, operation, arguments):
        return dict(synthetic=True,scenario=operation,status='partial',issues=[],
            queried_at='2026-10-27T00:00:00Z',conclusion='synthetic')
    def close(self):
        from pocs.chatgpt_skills.diagnostics import request_run
        if request_run.get() is not None:
            raise OSError('PRIVATE_PROCESS_DETAIL')
    monkeypatch.setattr(BusinessClient,'call',answer)
    monkeypatch.setattr(BusinessClient,'close',close)
    async def run():
        server = create_server(SimpleNamespace(real_config='not-opened',events=str(path)))
        async with Client(server,mode='legacy') as client:
            result = await client.call_tool('analyze_position',{'request':{'question':'PRIVATE'}})
            assert not result.is_error and result.structured_content['status']=='partial'
    asyncio.run(run())
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert any(row['safe_error_category']=='cleanup_failed' for row in rows)
    assert rows[-1]['business_status']=='partial' and not rows[-1]['protocol_is_error']
    assert 'PRIVATE' not in path.read_text()


def test_only_existing_safe_model_metadata_supplies_failure_stage_statistics():
    from app.llm_observation import AnalysisObservation
    from pocs.chatgpt_skills.diagnostics import diagnostic, safe_diagnostic
    trace = AnalysisObservation('a'*32)
    call = trace.begin({'model':'gpt-6-luna','reasoning_effort':'none'},'chat')
    trace.complete(call, kind='timeout')
    metadata = trace.snapshot('failed')
    metadata['calls'][0]['peer_stage'] = 'planning'
    value = diagnostic('a'*32,'analyze_peer',category='business_error',observation=metadata)
    projected = safe_diagnostic(value,'a'*32)
    assert projected['model_call_count'] == 1
    assert projected['observed_peer_stages'] == ['planning']
    assert projected['model_error_categories'] == ['timeout']
    assert projected['provider_request_count'] == 'UNKNOWN'
