"""Independent real MCP protocol against one synthetic private policy store."""
import asyncio
from types import SimpleNamespace
import os
import json

from mcp import Client

from tests.test_policy_integration import active  # explicit synthetic sources only
from pocs.chatgpt_skills.server import BusinessClient,create_server
from backend.services.investment_policy.runtime import context
from backend.services.investment_thesis.store import ThesisStore
from backend.services.investment_thesis.monitoring import migrate_monitoring


def settings(path,prepare_path):
    return SimpleNamespace(real_config='not-opened-read-contract',mock_database=None,
        business_python=os.environ['WP_BUSINESS_PYTHON'],portfolio_id=7,web_base_url='',
        e1_db_path=str(path),e1_enable_writes=True,prepare_config=str(prepare_path))


def test_original_18_tools_plus_one_policy_read_without_business_or_e1_writes(active,tmp_path,monkeypatch):
    e1=ThesisStore(tmp_path/'e1'/'synthetic.sqlite',7);e1.init();migrate_monitoring(e1)
    token=tmp_path/'synthetic-token';token.write_text('TEST_ONLY_'+'x'*32);token.chmod(0o600)
    config=tmp_path/'synthetic-prepare.json';config.write_text(json.dumps({'base_url':'http://127.0.0.1:9','token_file':str(token)}));config.chmod(0o600)
    before_policy=active[0].path.read_bytes();before_e1=e1.path.read_bytes()
    def forbidden(*a,**k):raise AssertionError('Policy query called a business/PEER/account worker')
    monkeypatch.setattr(BusinessClient,'call',forbidden)
    async def run():
        with monkeypatch.context() as isolated:
            isolated.delenv('WEALTHPILOT_POLICY_DB_PATH')
            async with Client(create_server(settings(e1.path,config)),mode='legacy') as c:
                original={t.name for t in (await c.list_tools()).tools}
        async with Client(create_server(settings(e1.path,config)),mode='legacy') as c:
            tools={t.name:t for t in (await c.list_tools()).tools}
            assert len(original)==18 and len(tools)==19
            assert set(tools)-original=={'get_investment_policy_context'}
            tool=tools['get_investment_policy_context']
            assert tool.annotations.read_only_hint and not tool.annotations.destructive_hint
            assert 'database_path' not in str(tool.input_schema) and 'portfolio_id' not in str(tool.input_schema)
            for scope in ['portfolio','position','risk','full']:
                r=await c.call_tool('get_investment_policy_context',{'scope':scope,'symbol':'SYN2'})
                assert not r.is_error
                value=r.structured_content or json.loads(r.content[0].text)
                assert value['policy_reference']==context(7)['policy_reference']
                assert value['synthetic'] is True
            invalid=await c.call_tool('get_investment_policy_context',{'scope':'../../any-db'})
            assert invalid.is_error
    asyncio.run(run())
    assert active[0].path.read_bytes()==before_policy and e1.path.read_bytes()==before_e1
