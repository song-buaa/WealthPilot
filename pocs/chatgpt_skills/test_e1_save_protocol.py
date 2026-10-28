"""Local MCP preview->fresh consent->save->restart: no business/network access."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import socket
import re
from types import SimpleNamespace

from mcp import Client
import pytest

from backend.services.investment_thesis.store import ThesisStore
from pocs.chatgpt_skills.server import BusinessClient, create_server

EXAMPLES=Path(__file__).resolve().parents[2]/'docs/v4.3/examples/e1'
def sample(name):return json.loads((EXAMPLES/(name+'.json')).read_text())
def settings(path=None,enabled=False):
    return SimpleNamespace(real_config='not-opened',mock_database=None,business_python=os.environ['WP_BUSINESS_PYTHON'],
        portfolio_id=7,web_base_url='',e1_db_path=str(path) if path else None,e1_enable_writes=enabled)


def test_preview_human_confirmation_commit_and_restarted_readback(tmp_path,monkeypatch):
    store=ThesisStore(tmp_path/'private'/'research.db',7);store.init()
    store.save(sample('meituan-v1'),0,'v1');store.save(sample('meituan-v2'),1,'v2')
    attempts=[]
    def forbidden(*a,**kw):attempts.append(True);raise AssertionError('E1 attempted business/network')
    monkeypatch.setattr(BusinessClient,'call',forbidden);monkeypatch.setattr(socket.socket,'connect',forbidden)
    raw=sample('meituan-review');raw.pop('thesis_revision')
    req=dict(operation='REVIEW',record=raw,analysis=sample('peer-result'),analysis_source='USER_SUPPLIED_ANALYSIS_COPY')
    events=[]
    async def run():
        async with Client(create_server(settings(store.path,True)),mode='legacy') as client:
            tools=(await client.list_tools()).tools
            own={t.name:t for t in tools if t.name in ['preview_e1_record','commit_e1_record']}
            assert own['preview_e1_record'].annotations.read_only_hint
            assert own['commit_e1_record'].annotations.read_only_hint is False
            for tool in own.values():
                schema=json.dumps(tool.input_schema)
                assert 'database_path' not in schema and 'portfolio_scope' not in schema
            for name,args in [('get_investment_thesis',{'thesis_id':'synthetic-meituan'}),
                              ('get_investment_reviews',{'thesis_id':'synthetic-meituan'})]:
                assert not (await client.call_tool(name,args)).is_error
            before=hashlib.sha256(store.path.read_bytes()).hexdigest()
            preview=await client.call_tool('preview_e1_record',{'request':req});assert not preview.is_error
            p=preview.structured_content;assert not p['saved'] and p['summary']['judgment']=='UNKNOWN'
            events.append(dict(step='PREVIEW_NOT_SAVED',summary=p['summary']))
            args=dict(preview_token=p['preview_token'],preview_fingerprint=p['preview_fingerprint'])
            assert (await client.call_tool('commit_e1_record',args)).is_error
            assert (await client.call_tool('commit_e1_record',dict(args,confirmed=True))).is_error
            changed=dict(args,preview_fingerprint='modified',confirmed=True,confirmation='保存这次判断')
            assert (await client.call_tool('commit_e1_record',changed)).is_error
            assert hashlib.sha256(store.path.read_bytes()).hexdigest()==before
            cancelled=await client.call_tool('commit_e1_record',dict(args,confirmation='取消'))
            assert not cancelled.is_error and not cancelled.structured_content['saved']
            assert (await client.call_tool('commit_e1_record',dict(args,confirmed=True,confirmation='保存这次判断'))).is_error
            p=(await client.call_tool('preview_e1_record',{'request':req})).structured_content
            # Test actor now explicitly simulates a new human message; not a real investment instruction.
            args=dict(preview_token=p['preview_token'],preview_fingerprint=p['preview_fingerprint'],
                      confirmed=True,confirmation='保存这次判断')
            saved=await client.call_tool('commit_e1_record',args);assert not saved.is_error
            receipt=saved.structured_content;assert receipt['saved'] and receipt['revision']==2
            assert (await client.call_tool('commit_e1_record',args)).structured_content==receipt
            events.append(dict(step='EXPLICIT_SIMULATED_HUMAN_CONFIRMATION',receipt=receipt))
            changed_req=dict(req,request_id=p['request_id'],record=dict(raw,rationale='changed'))
            assert (await client.call_tool('preview_e1_record',{'request':changed_req})).is_error
            read=await client.call_tool('get_investment_reviews',{'thesis_id':'synthetic-meituan'})
            assert len(read.structured_content['items'])==1
            assert read.structured_content['items'][0]['analysis']==sample('peer-result')
        async with Client(create_server(settings(store.path,True)),mode='legacy') as client:
            assert (await client.call_tool('commit_e1_record',args)).is_error
            # Repreview the SAME normalized payload/ID after restart: durable retry, no duplicate.
            retry=dict(req,record=p['payload']['record'],expected_revision=2,request_id=p['request_id'])
            new=await client.call_tool('preview_e1_record',{'request':retry});assert not new.is_error
            q=new.structured_content
            retry_saved=await client.call_tool('commit_e1_record',dict(preview_token=q['preview_token'],
                preview_fingerprint=q['preview_fingerprint'],confirmed=True,confirmation='保存这次判断'))
            assert not retry_saved.is_error and retry_saved.structured_content['review_id']==receipt['review_id']
            read=await client.call_tool('get_investment_reviews',{'thesis_id':'synthetic-meituan'})
            assert len(read.structured_content['items'])==1
            assert read.structured_content['items'][0]['current_qualification']=='HISTORICAL_RECORD_NOT_RECHECKED'
    asyncio.run(run());assert not attempts
    out=os.environ.get('E1_ACCEPTANCE_OUTPUT_DIR')
    if out:
        path=Path(out)/'E1_B31_SYNTHETIC_PROTOCOL_FLOW.json'
        path.write_text(json.dumps(dict(status='PASS',synthetic=True,simulated_human_confirmation=True,
            online_chatgpt_save_available=False,business_calls=0,network_attempts=0,events=events),ensure_ascii=False,indent=2));path.chmod(0o600)


def test_write_switch_is_separate_and_original_contracts_unchanged(tmp_path):
    async def run():
        async with Client(create_server(settings()),mode='legacy') as client:
            baseline=(await client.list_tools()).tools
        async with Client(create_server(settings(tmp_path/'missing.db')),mode='legacy') as client:
            readonly=(await client.list_tools()).tools
        assert {x.name for x in readonly}-{x.name for x in baseline}=={'get_investment_thesis','get_investment_reviews'}
        async with Client(create_server(settings(tmp_path/'missing.db',True)),mode='legacy') as client:
            enabled=(await client.list_tools()).tools
        assert {x.name for x in enabled}-{x.name for x in readonly}=={'preview_e1_record','commit_e1_record'}
        for old in baseline:
            for collection in [readonly,enabled]:
                t=next(x for x in collection if x.name==old.name)
                assert t.input_schema==old.input_schema and t.annotations==old.annotations and re.sub(r'pa_[0-9a-f]+','BOOT_SCOPE',t.description)==re.sub(r'pa_[0-9a-f]+','BOOT_SCOPE',old.description)
        assert not (tmp_path/'missing.db').exists()
    asyncio.run(run())
    with pytest.raises(ValueError,match='EXPLICIT_STORE'):create_server(settings(enabled=True))
