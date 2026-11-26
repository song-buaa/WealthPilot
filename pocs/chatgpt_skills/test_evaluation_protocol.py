"""Actual MCP protocol roundtrip: private sample -> AI-labelled score -> feedback/export.
This is an engineering client, never proof that ChatGPT has used deployed tools.
"""
import asyncio
import json
from types import SimpleNamespace
from mcp import Client
from pocs.chatgpt_skills.server import create_server, BusinessClient
from tests.test_position_jobs import factory, finish, request
from tests.test_research_evaluation import evaluation


def test_mcp_evaluation_roundtrip_no_research_or_account_calls(tmp_path,monkeypatch):
    root=tmp_path/'jobs';jobs=factory(root)
    job=jobs.start('protocol-existing-job',request());finish(jobs,job.job_id)
    def forbidden(*a,**k):raise AssertionError('NO_MODEL_PROVIDER_ACCOUNT_OR_E1')
    monkeypatch.setattr(BusinessClient,'call',forbidden)
    cfg=SimpleNamespace(real_config='not-opened',web_base_url='',analysis_results_dir=str(root),
        analysis_results_quota_bytes=64*1024*1024,analysis_results_retention_seconds=1)
    async def run():
        async with Client(create_server(cfg),mode='legacy') as c:
            tools={t.name:t for t in (await c.list_tools()).tools}
            assert not tools['save_research_evaluation'].annotations.read_only_hint
            assert tools['query_research_evaluations'].annotations.read_only_hint
            assert tools['save_research_evaluation'].annotations.idempotent_hint
            async def call(name, args):
                r=await c.call_tool(name,args)
                assert not r.is_error,r.content
                return r.structured_content or json.loads(r.content[0].text)
            export=await call('export_research_evaluations',{'job_id':job.job_id})
            row=export['items'][0];sid=row['sample']['sample_id'];h=row['state']['reports']['native']['sha256']
            review=await call('save_research_evaluation',dict(sample_id=sid,evaluation=evaluation(h).model_dump(mode='json')))
            assert review['total']=='7.60' and review['confirmation']=='UNCONFIRMED_AI'
            again=await call('save_research_evaluation',dict(sample_id=sid,evaluation=evaluation(h).model_dump(mode='json')))
            assert again==review
            await call('save_research_feedback',dict(sample_id=sid,feedback=dict(request_id='user-feedback',comment='Engineering roundtrip only',author_type='human',evaluation_id=review['evaluation_id'])))
            queried=await call('query_research_evaluations',{'job_id':job.job_id})
            assert queried['items'][0]['evaluations'][0]['total']=='7.60'
            assert queried['distribution']=={'native/ai/FAIL':1}
            bad=await c.call_tool('save_research_evaluation',dict(sample_id='a'*64,evaluation=evaluation(h).model_dump(mode='json')))
            assert bad.is_error
            assert str(tmp_path) not in str(bad.content)
    asyncio.run(run())
