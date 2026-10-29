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


@pytest.mark.parametrize('outcome', ['partial', 'unavailable', 'exit'])
def test_async_position_survives_real_http_disconnect_and_original_worker(monkeypatch, tmp_path, outcome):
    """Real streamable HTTP + Worker.main + IPC/Pydantic; only analyze is synthetic."""
    import os
    import re
    import socket
    import subprocess
    import sys
    import threading
    import time
    from contextlib import contextmanager
    import uvicorn
    from pocs.chatgpt_skills import server as module
    from pocs.chatgpt_skills.analysis import AnalysisResult

    connect = socket.socket.connect
    def loopback_only(sock, address):
        if not isinstance(address, tuple) or address[0] not in ('127.0.0.1', 'localhost', '::1'):
            raise AssertionError('EXTERNAL_NETWORK_FORBIDDEN')
        return connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', loopback_only)
    entered, release, expected, database = [tmp_path/name for name in ('entered','release','expected.json','frozen.db')]
    database.touch()
    original_spawn = subprocess.Popen
    children=[]
    probe='''import asyncio,json,os,socket,sys
from pathlib import Path
from pocs.chatgpt_skills import worker,analysis
from app.llm_observation import observed_result
socket.socket.connect=lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'))
@observed_result
async def frozen(*args,**kwargs):
 assert not args[3].get('snapshot_id')
 assert os.getenv('ENABLE_IBKR_LIVE_TRADING')=='false'
 Path(ENTERED).write_text('entered')
 print('PRIVATE_WORKER_OUTPUT')
 while not Path(RELEASE).exists(): await asyncio.sleep(.01)
 if OUTCOME=='exit': os._exit(7)
 return dict(synthetic=True,scenario='analyze_position',status=OUTCOME,issues=['SYNTHETIC_ONLY'],
  queried_at='2026-10-27T00:00:00Z',conclusion='Original frozen professional report',
  professional_result={'professional_explanation':[{'topic':'risk','text':'Original explanation'}]},
  evidence={'records':[{'ref':'frozen-fact'}]},data_quality={'L3':False},limitations=['No trades'],scope={'readonly':True})
async def save(*args,**kwargs):
 result=await frozen(*args,**kwargs)
 Path(EXPECTED).write_text(json.dumps(result))
 return result
analysis.analyze=save
sys.argv=['worker','--frozen-database',DATABASE]
worker.main()
'''.replace('ENTERED',repr(str(entered))).replace('RELEASE',repr(str(release))).replace('OUTCOME',repr(outcome)).replace('EXPECTED',repr(str(expected))).replace('DATABASE',repr(str(database)))
    def spawn(command, **kwargs):
        child=original_spawn([os.environ.get('WP_BUSINESS_PYTHON',sys.executable),'-u','-c',probe],**kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(module.subprocess,'Popen',spawn)

    @contextmanager
    def http_server():
        server=create_server(SimpleNamespace(real_config='not-opened',mock_database=None,
            business_python='replaced-by-isolated-probe',portfolio_id=7,events=str(tmp_path/'events.jsonl')))
        app=server.streamable_http_app(json_response=True,stateless_http=True)
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(16)
        config=uvicorn.Config(app,log_level='critical',access_log=False)
        http=uvicorn.Server(config)
        thread=threading.Thread(target=http.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
        for _ in range(200):
            if http.started:break
            time.sleep(.01)
        assert http.started
        try:yield f'http://127.0.0.1:{listener.getsockname()[1]}/mcp'
        finally:
            http.should_exit=True;thread.join(timeout=5);listener.close()
            assert not thread.is_alive()

    async def run(url):
        async with Client(url,mode='legacy',read_timeout_seconds=10) as client:
            tools={tool.name:tool for tool in (await client.list_tools()).tools}
            scope=re.search(r'pa_[0-9a-f]{32}',tools['start_position_analysis'].description)[0]
            assert len(tools)==12 and 'Synchronous compatibility' in tools['analyze_position'].description
            request_id=scope+':li_request_00000001'
            arguments={'request_id':request_id,'request':{'symbol':'LI:US','question':'PRIVATE_SYNTHETIC_QUESTION'}}
            before=time.monotonic()
            start=await client.call_tool('start_position_analysis',arguments)
            assert not start.is_error,start.content
            first=start.structured_content
            assert time.monotonic()-before<2 and first['state']=='running' and first['job_id']
        # The initiating HTTP connection/session has ended. Its lifespan must not kill the job.
        for _ in range(300):
            if entered.exists():break
            await asyncio.sleep(.01)
        assert entered.exists() and len(children)==1 and children[0].poll() is None
        async with Client(url,mode='legacy',read_timeout_seconds=10) as client:
            for _ in range(3):
                before=time.monotonic()
                pending=await client.call_tool('get_position_analysis_result',{'job_id':first['job_id']})
                assert time.monotonic()-before<1 and pending.structured_content['state']=='running'
                again=await client.call_tool('start_position_analysis',arguments)
                assert again.structured_content['job_id']==first['job_id']
            conflict=await client.call_tool('start_position_analysis',arguments|{'request':{'question':'different'}})
            assert conflict.structured_content['issue']=='REQUEST_ID_CONFLICT'
            busy=await client.call_tool('start_position_analysis',arguments|{'request_id':scope+':other_request_00002'})
            assert busy.structured_content['issue']=='ANALYSIS_BUSY'
            sync=await client.call_tool('analyze_position',{'request':{'question':'No duplicate sync call'}})
            assert sync.is_error and 'ANALYSIS_BUSY' in str(sync.content)
            assert len(children)==1
            release.write_text('continue')
            for _ in range(300):
                final=await client.call_tool('get_position_analysis_result',{'job_id':first['job_id']})
                if final.structured_content['state']!='running':break
                await asyncio.sleep(.01)
            final=final.structured_content
            assert final['analysis_run_id']==first['analysis_run_id']
            if outcome=='exit':
                assert final['state']=='unavailable' and final['result'] is None and final['business_status']=='UNKNOWN'
            else:
                assert final['state']=='completed' and final['business_status']==outcome
                assert final['result']==AnalysisResult.model_validate_json(expected.read_text()).model_dump(mode='json')
                assert final['result']['llm_observation']['analysis_run_id']==first['analysis_run_id']
                assert not final['result']['order_created']
            again=await client.call_tool('start_position_analysis',arguments)
            assert again.structured_content['job_id']==first['job_id'] and len(children)==1
            return first,request_id
    try:
        with http_server() as url:first,request_id=asyncio.run(run(url))
        # A fresh server instance models its process-local receipt loss. Old scope cannot start paid work.
        with http_server() as url:
            async def restarted():
                async with Client(url,mode='legacy') as client:
                    missing=await client.call_tool('get_position_analysis_result',{'job_id':first['job_id']})
                    assert missing.structured_content['issue']=='JOB_UNKNOWN_OR_PROCESS_RESTARTED'
                    retry=await client.call_tool('start_position_analysis',{'request_id':request_id,'request':{'question':'old request'}})
                    assert retry.structured_content['issue']=='REQUEST_SCOPE_UNAVAILABLE'
            asyncio.run(restarted())
        assert len(children)==1
        events=(tmp_path/'events.jsonl').read_text()
        assert 'PRIVATE' not in events
        rows=[json.loads(line) for line in events.splitlines()]
        assert {row['analysis_run_id'] for row in rows}=={first['analysis_run_id']}
    finally:
        release.touch()
        for child in children:
            if child.poll() is None:child.terminate()
            child.wait(timeout=5)


@pytest.mark.parametrize('late',[False,True])
def test_single_use_worker_flushes_before_thread_join_and_is_reclaimed(monkeypatch,tmp_path,late):
    """Real analyze + SDK MockTransport + to_thread + Worker.main + parent cleanup."""
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path
    from pocs.chatgpt_skills import server as module
    python=os.environ.get('WP_BUSINESS_PYTHON',sys.executable)
    db=tmp_path/'readonly.db';sent=tmp_path/'sends';ro=tmp_path/'readonly-check'
    subprocess.run([python,'-c',
        'from sqlalchemy import create_engine;from sqlalchemy.orm import Session;'
        'from app.database import Base;from app.models import Portfolio;'
        'import backend.services.action.models;import backend.services.portfolio_history.models;'
        'import services.broker_sync.models;'
        'e=create_engine("sqlite:///"+__import__("sys").argv[1]);Base.metadata.create_all(e);'
        's=Session(e);s.add(Portfolio(id=7,name="synthetic"));s.commit();s.close();e.dispose()',str(db)],check=True,
        env={**os.environ,'PYTHONPATH':str(Path.cwd())+':'+str(Path.cwd()/'backend'),'PYTHON_DOTENV_DISABLED':'1'})
    probe='''import asyncio,json,os,socket,sys,time
from pathlib import Path
from datetime import datetime
import httpx,openai
from pocs.chatgpt_skills import worker,analysis
from backend.services import decision_service_v3 as svc
from tests.test_l1_reliability import facts,AT
from app.llm_config import chat_completion,SOL
from app.database import get_session
from sqlalchemy import text
from types import SimpleNamespace as NS
socket.socket.connect=lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('NETWORK_FORBIDDEN'))
class Clock(datetime):
 @classmethod
 def now(cls,tz=None):return AT
analysis.datetime=Clock
receipt=facts()
def receive(request):
 with open(SENT,'a') as f:f.write(str(time.monotonic())+'\\n')
 time.sleep(.35 if LATE else 3)
 denied=False
 try:
  with get_session() as db:db.execute(text("UPDATE portfolios SET name='BAD'"));db.commit()
 except Exception:denied=True
 Path(RO).write_text(json.dumps({'denied':denied,'readonly_flag':os.environ.get('WEALTHPILOT_EPHEMERAL_ANALYSIS')}))
 raise httpx.ReadTimeout('PRIVATE_BODY',request=request)
client=openai.OpenAI(api_key='synthetic-not-a-key',http_client=httpx.Client(transport=httpx.MockTransport(receive)))
async def stream(*args,**kw):
 kw['stage_callback'](NS(route='position_single'),NS(position_evidence=receipt))
 if LATE:
  import functools
  task=asyncio.get_running_loop().run_in_executor(None,functools.partial(chat_completion,client,model=SOL,messages=[{'role':'user','content':'PRIVATE_PROMPT'}]))
 else:
  task=asyncio.create_task(asyncio.to_thread(chat_completion,client,model=SOL,messages=[{'role':'user','content':'PRIVATE_PROMPT'}]))
 # Start the injected timeout only after the cold SDK actually enters transport.
 while not Path(SENT).exists():await asyncio.sleep(.005)
 async with asyncio.timeout(.08):await task
 yield svc._sse('done',{})
svc.run_chat_stream_v3=stream
sys.argv=['worker','--frozen-database',DATABASE,'--synthetic-input','--portfolio-id','7','--single-analysis']
worker.main()
'''.replace('DATABASE',repr(str(db))).replace('SENT',repr(str(sent))).replace('LATE',repr(late)).replace('RO)',repr(str(ro))+')')
    original=subprocess.Popen;children=[]
    def spawn(command,**kw):
        child=original([python,'-u','-c',probe],**kw);children.append(child);return child
    monkeypatch.setattr(module.subprocess,'Popen',spawn)
    monkeypatch.setenv('WEALTHPILOT_L1_ANALYSIS_TIMEOUT_SECONDS','20')
    unrelated=original([python,'-c','import time;time.sleep(20)'])
    client=BusinessClient(SimpleNamespace(business_python=python,portfolio_id=7,real_config=None,mock_database=None,async_l1=True))
    before=__import__('hashlib').sha256(db.read_bytes()).hexdigest()
    try:
        result=client.call('analyze_position',{'question':'frozen','symbol':'PDD:US'})
        received=time.monotonic()
        assert sent.exists(), json.dumps(result.get('llm_observation'))
        assert result['llm_observation']['call_count']==1
        assert result['llm_observation']['calls'][0]['transport_attempt_count']==1
        assert result['status']=='partial' and not result['professional_analysis_complete']
        assert result['professional_result']['analysis_result']['mode']=='facts_only'
        assert not result['execution_authorized']
        assert received-float(sent.read_text().splitlines()[0]) < 1.5
        frozen=json.dumps(result,sort_keys=True)
        if late:
            children[0].wait(timeout=5)
            assert json.loads(ro.read_text())=={'denied':True,'readonly_flag':'true'}
            assert sent.read_text().count('\n')==1  # SDK did not retry after terminal receipt
            assert json.dumps(result,sort_keys=True)==frozen
        started=time.monotonic();client.close()
        assert time.monotonic()-started<3 and children[0].poll() is not None
        assert unrelated.poll() is None
        assert sent.read_text().count('\n')==1
        assert __import__('hashlib').sha256(db.read_bytes()).hexdigest()==before
        assert not any(word in json.dumps(result) for word in ['PRIVATE_PROMPT','PRIVATE_BODY'])
    finally:
        client.close();unrelated.terminate();unrelated.wait(timeout=3)


def test_single_analysis_serialization_failure_keeps_protocol_stage_and_exits(monkeypatch,tmp_path):
    import os,subprocess,sys
    from pocs.chatgpt_skills import server as module
    python=os.environ.get('WP_BUSINESS_PYTHON',sys.executable)
    db=tmp_path/'fixture.db';db.touch()
    probe='''import sys
from pocs.chatgpt_skills import worker,analysis
async def broken(*a,**kw):
 result={};result['cycle']=result;return result
analysis.analyze=broken
sys.argv=['worker','--frozen-database',DATABASE,'--single-analysis']
worker.main()
'''.replace('DATABASE',repr(str(db)))
    original=subprocess.Popen
    monkeypatch.setattr(module.subprocess,'Popen',lambda *a,**kw:original([python,'-u','-c',probe],**kw))
    client=BusinessClient(SimpleNamespace(business_python=python,portfolio_id=7,real_config=None,mock_database=None,async_l1=True))
    try:
        with pytest.raises(Exception) as error:client.call('analyze_position',{'question':'synthetic'})
        detail=error.value.analysis_diagnostic
        assert detail['failure_stage']=='result_serialization' and detail['safe_error_category']=='serialization_failed'
        assert detail['protocol_is_error']
        client.process.wait(timeout=3)  # error path is single-use too
    finally:client.close()
