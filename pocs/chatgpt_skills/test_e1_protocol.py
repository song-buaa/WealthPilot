"""4.3 E1 real CLI processes + MCP protocol in independent mcp==2.3 runtime."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

from mcp import Client
import pytest
from pocs.chatgpt_skills.server import create_server, BusinessClient

ROOT=Path(__file__).resolve().parents[2]
EXAMPLES=ROOT/'docs/v4.3/examples/e1'
BUSINESS=os.environ['WP_BUSINESS_PYTHON']


def settings(path=None):
    return SimpleNamespace(real_config='not-opened-no-business-reads',mock_database=None,
        business_python=BUSINESS,portfolio_id=7,web_base_url='',e1_db_path=str(path) if path else None)


def test_cli_to_readonly_mcp_history_review_and_restart(tmp_path,monkeypatch):
    monkeypatch.delenv('WEALTHPILOT_E1_DB_PATH',raising=False)
    monkeypatch.delenv('WEALTHPILOT_STAGE_TARGETS_PATH',raising=False)
    attempts=[]
    def forbidden(*args,**kwargs):
        attempts.append('attempt')
        raise AssertionError('E1 read attempted network/business execution')
    monkeypatch.setattr(BusinessClient,'call',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    path=tmp_path/'private'/'research.db'
    guard=tmp_path/'guard';guard.mkdir()
    audit=tmp_path/'socket-attempts.txt'
    (guard/'sitecustomize.py').write_text('''import socket,os
from pathlib import Path
def deny(*a,**k):
    with open(os.environ["E1_SOCKET_AUDIT"],"a") as f:f.write("denied\\n")
    raise OSError("E1_OFFLINE_SOCKET_GUARD")
socket.socket.connect=deny
socket.socket.connect_ex=deny
socket.create_connection=deny
''')
    env={k:os.environ[k] for k in ('HOME','PATH','LANG','TMPDIR') if k in os.environ}
    env.update(PYTHON_DOTENV_DISABLED='1',PUBLIC_DEMO_MODE='true',DEMO_ACCESS_PASSWORD='e1-synthetic',
        DEMO_ALLOW_MARKET_DATA='false',BROKER_MODE='mock',AV_DEV_MOCK='1',
        WEALTHPILOT_DB_PATH=str(tmp_path/'never-opened-business.db'),
        PYTHONPATH=str(guard)+os.pathsep+str(ROOT),E1_SOCKET_AUDIT=str(audit))
    def cli(*arguments):
        run=subprocess.run([BUSINESS,'-m','backend.services.investment_thesis.cli','--store',str(path),
            '--portfolio-id','7',*arguments],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
        assert run.returncode==0,run.stdout+'\n'+run.stderr
        return json.loads(run.stdout)
    events=[]
    events.append(cli('init'))
    v1=cli('save','--input',str(EXAMPLES/'meituan-v1.json'),'--expected-revision','0','--request-id','flow-v1')
    v2=cli('save','--input',str(EXAMPLES/'meituan-v2.json'),'--expected-revision','1','--request-id','flow-v2')
    assert v1['content']['user_original_judgment']==v2['content']['user_original_judgment']
    events.extend([{'revision':1,'sha256':v1['sha256']},{'revision':2,'sha256':v2['sha256']}])
    async def first_read():
        async with Client(create_server(settings(path)),mode='legacy') as client:
            tools=(await client.list_tools()).tools
            own=[t for t in tools if t.name in (
                'get_investment_thesis','get_investment_reviews','get_due_investment_reviews')]
            assert len(own)==3 and all(t.annotations.read_only_hint and t.annotations.idempotent_hint for t in own)
            before=hashlib.sha256(path.read_bytes()).hexdigest()
            result=await client.call_tool('get_investment_thesis',{'thesis_id':'synthetic-meituan','revision':2})
            assert not result.is_error,result.content
            assert result.structured_content['items'][0]==v2
            assert hashlib.sha256(path.read_bytes()).hexdigest()==before
            bad=await client.call_tool('get_investment_thesis',{'thesis_id':'synthetic-meituan','symbol':'3690:HK'})
            assert bad.is_error
            return sorted(t.name for t in tools)
    tools=asyncio.run(first_read())
    review=cli('review','--input',str(EXAMPLES/'meituan-review.json'),
        '--analysis-file',str(EXAMPLES/'peer-result.json'),'--request-id','flow-review')
    assert review['analysis']==json.loads((EXAMPLES/'peer-result.json').read_text())
    assert review['effective_verdict']=='UNKNOWN'
    assert review['analysis_provenance']=='USER_SUPPLIED_ANALYSIS_COPY' and review['synthetic']
    assert cli('review','--input',str(EXAMPLES/'meituan-review.json'),
        '--analysis-file',str(EXAMPLES/'peer-result.json'),'--request-id','flow-review')==review
    # New server object and separate CLI process prove persistence beyond job memory.
    async def restarted_read():
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        async with Client(create_server(settings(path)),mode='legacy') as client:
            result=await client.call_tool('get_investment_reviews',{'thesis_id':'synthetic-meituan'})
            assert not result.is_error and result.structured_content['items']==[review]
            historic=await client.call_tool('get_investment_thesis',{'thesis_id':'synthetic-meituan'})
            assert [i['revision'] for i in historic.structured_content['items']]==[1,2]
            assert historic.structured_content['items'][1]['diff']['evidence']['added'][0]['evidence_id']=='counter'
        assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    asyncio.run(restarted_read())
    assert cli('show','--thesis-id','synthetic-meituan','--revision','1')==v1
    assert cli('diff','--thesis-id','synthetic-meituan','--from-revision','1','--to-revision','2')==v2['diff']
    assert not (tmp_path/'never-opened-business.db').exists()
    assert not audit.exists() and not attempts
    output=os.environ.get('E1_ACCEPTANCE_OUTPUT_DIR')
    if output:
        out=Path(output);out.mkdir(parents=True,mode=0o700,exist_ok=True)
        evidence=dict(project='WealthPilot 4.3',stage='E1-B1',synthetic=True,
            flow='CLI v1 -> v2 -> MCP read -> injected result copy -> CLI review -> MCP review/diff -> restart read',
            result='PASS',events=events,review=review,tools=tools,public_socket_attempts=0,
            business_worker_calls=0,real_account_reads=0,trades=0)
        file=out/'E1_B1_FLOW.json';file.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n');file.chmod(0o600)


def test_no_configuration_keeps_original_tools_and_bad_store_fails_readonly(tmp_path,monkeypatch):
    monkeypatch.delenv('WEALTHPILOT_E1_DB_PATH',raising=False)
    async def run():
        async with Client(create_server(settings()),mode='legacy') as client:
            original=(await client.list_tools()).tools
        async with Client(create_server(settings(tmp_path/'absent'/'new.db')),mode='legacy') as client:
            configured=(await client.list_tools()).tools
            assert {t.name for t in configured}-{t.name for t in original}=={
                'get_investment_thesis','get_investment_reviews','get_due_investment_reviews'}
            for t in original:
                unchanged=next(x for x in configured if x.name==t.name)
                assert unchanged.input_schema==t.input_schema and unchanged.annotations==t.annotations
            response=await client.call_tool('get_investment_thesis',{'symbol':'3690:HK'})
            assert response.is_error
        assert not (tmp_path/'absent').exists()
    asyncio.run(run())
