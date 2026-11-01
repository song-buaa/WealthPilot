"""Actual MCP protocol + original isolated Worker; synthetic source-failure boundary."""
import asyncio
import json
import os
from types import SimpleNamespace
from mcp import Client
from pocs.chatgpt_skills.server import create_server


def configuration(tmp_path, *, enabled=True):
    return SimpleNamespace(real_config='must-not-open-business-config', mock_database=None,
        business_python=os.environ['WP_BUSINESS_PYTHON'], portfolio_id=7, web_base_url='',
        s3_archive_root=str(tmp_path/'archive') if enabled else None,
        s3_source_root=str(tmp_path/'sources'))


def value(r):
    assert not r.is_error, r
    return r.structured_content or json.loads(r.content[0].text)


def test_six_cards_async_durable_read_and_no_business_config_access(tmp_path, monkeypatch):
    monkeypatch.delenv('WEALTHPILOT_POLICY_DB_PATH', raising=False)
    sources=tmp_path/'sources'; sources.mkdir()
    symbols=['AAPL','NVDA','GOOG','MSFT','META','TSLA']
    (sources/'manifest.json').write_text(json.dumps({'schema_version':'s3-d.sources.v1', 'requests':[],
        'companies':{s+':US':{'directory':{'status':'FAILED','diagnostics':[{'category':'http_403'}]}} for s in symbols}}))
    async def run():
        async with Client(create_server(configuration(tmp_path, enabled=False)), mode='legacy') as c:
            original={t.name for t in (await c.list_tools()).tools}
        async with Client(create_server(configuration(tmp_path)), mode='legacy') as c:
            tools={t.name:t for t in (await c.list_tools()).tools}
            assert set(tools)-original=={'start_company_pool_research','get_company_pool_research_result','read_company_pool_research'}
            assert not tools['start_company_pool_research'].annotations.read_only_hint
            assert tools['read_company_pool_research'].annotations.read_only_hint
            for key in ['archive_root','decision_at','source_root','financial_facts']:
                assert key not in str(tools['start_company_pool_research'].input_schema)
            args={'request_id':'synthetic-pool-1','request':{'symbols':symbols,'purpose':'Synthetic protocol failure boundary; no real company acceptance'}}
            start=value(await c.call_tool('start_company_pool_research', args))
            duplicate=value(await c.call_tool('start_company_pool_research', args))
            assert start['job_id']==duplicate['job_id'] and start['schema_version']=='wealthpilot.company-pool-job.v1'
            conflict=value(await c.call_tool('start_company_pool_research', {**args,'request':{'symbols':['AAPL'],'purpose':'different'}}))
            assert conflict['issue']=='REQUEST_ID_CONFLICT'
            for _ in range(100):
                result=value(await c.call_tool('get_company_pool_research_result',{'job_id':start['job_id']}))
                if result['state']!='running': break
                await asyncio.sleep(.1)
            assert result['state']=='completed', result
            report=result['result']; assert len(report['professional_result']['cards'])==6
            assert report['data_quality']['comparable_count']==0
            assert not report['scope']['holdings_used'] and not report['order_created']
            assert not report['professional_analysis_complete']
            artifact=report['evidence']['artifact_id']
            invalid=await c.call_tool('start_company_pool_research', {**args,'request':{**args['request'],'decision_at':'2000-01-01'}})
            assert invalid.is_error
        async with Client(create_server(configuration(tmp_path)), mode='legacy') as c:
            unknown=value(await c.call_tool('get_company_pool_research_result',{'job_id':start['job_id']}))
            assert unknown['issue']=='JOB_UNKNOWN_OR_PROCESS_RESTARTED'
            reread=value(await c.call_tool('read_company_pool_research',{'artifact_id':artifact}))
            assert reread==report
            invalid=await c.call_tool('read_company_pool_research',{'artifact_id':'../../outside'})
            assert invalid.is_error
            (tmp_path/'archive'/artifact/'comparison.md').write_text('tampered')
            bad=await c.call_tool('read_company_pool_research',{'artifact_id':artifact})
            assert bad.is_error
            assert str(tmp_path) not in str(bad.content)
    asyncio.run(run())
