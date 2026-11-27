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
from tests.test_policy_publisher import formal_portfolio
from tests.test_policy_colocated import shared,stage


def settings(path,prepare_path):
    return SimpleNamespace(real_config='not-opened-read-contract',mock_database=None,
        business_python=os.environ['WP_BUSINESS_PYTHON'],portfolio_id=7,web_base_url='',
        e1_db_path=str(path),e1_enable_writes=True,prepare_config=str(prepare_path))


def test_existing_tools_plus_policy_read_and_transition_without_writes(active,tmp_path,monkeypatch):
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
            assert len(original)==22 and len(tools)==24
            assert set(tools)-original=={'get_investment_policy_context', 'simulate_portfolio_transition'}
            tool=tools['get_investment_policy_context']
            assert tool.annotations.read_only_hint and not tool.annotations.destructive_hint
            assert 'database_path' not in str(tool.input_schema) and 'portfolio_id' not in str(tool.input_schema)
            for scope in ['portfolio','position','risk','full']:
                r=await c.call_tool('get_investment_policy_context',{'scope':scope,'symbol':'SYN2'})
                assert not r.is_error
                value=r.structured_content or json.loads(r.content[0].text)
                assert value['policy_reference']==context(7)['policy_reference']
                assert value['synthetic'] is True
            with monkeypatch.context() as broken:
                broken.setenv('WEALTHPILOT_POLICY_DB_PATH',str(tmp_path/'PRIVATE_PATH_SENTINEL.sqlite'))
                missing=await c.call_tool('get_investment_policy_context',{'scope':'full'})
                assert missing.is_error
                text=' '.join(block.text for block in missing.content if hasattr(block,'text'))
                assert 'POLICY_CONTEXT_UNAVAILABLE' in text
                assert str(tmp_path) not in text and 'PRIVATE_PATH_SENTINEL' not in text
            invalid=await c.call_tool('get_investment_policy_context',{'scope':'../../any-db'})
            assert invalid.is_error
    asyncio.run(run())
    assert active[0].path.read_bytes()==before_policy and e1.path.read_bytes()==before_e1


def test_formal_publisher_receipt_via_actual_readonly_mcp_protocol(formal_portfolio,monkeypatch):
    cfg=settings(None,None);cfg.e1_db_path=None;cfg.e1_enable_writes=False;cfg.prepare_config=None
    before=formal_portfolio[0].path.read_bytes()
    def forbidden(*a,**k):raise AssertionError('Formal policy query invoked PEER/broker')
    monkeypatch.setattr(BusinessClient,'call',forbidden)
    async def run():
        async with Client(create_server(cfg),mode='legacy') as client:
            result=await client.call_tool('get_investment_policy_context',{'scope':'portfolio'})
            assert not result.is_error
            value=result.structured_content or json.loads(result.content[0].text)
            assert value['policy_reference']==context(7)['policy_reference']
            assert value['release']['environment']=='ISOLATED_REHEARSAL'
            assert value['precision']=='UNKNOWN_REQUIRES_QUALIFIED_ASSET_VIEW'
            assert not value['execution_authorized']
    asyncio.run(run())
    assert formal_portfolio[0].path.read_bytes()==before


def test_colocated_policy_uses_original_contract_in_real_mcp_protocol(shared,monkeypatch):
    from backend.services.investment_policy.colocated import CoLocatedPolicyStore
    from backend.services.investment_policy.formal_publisher import PolicyPublisher
    from backend.services.investment_policy.store import utc_now
    from backend.services.investment_policy.contracts import digest
    from tests.test_policy_publisher import approval,installation
    store=CoLocatedPolicyStore(*shared[:2]);bundle=stage(store,shared[2])
    store.transition(scope=bundle['scope'],bundle_id=bundle['bundle_id'],state='VALIDATED',expected_state='DRAFT',request_id='validate',at=utc_now())
    pub=PolicyPublisher(store,environment='ISOLATED_REHEARSAL')
    a=approval(store,bundle);a['approved_at']=utc_now();pub.approve(authorization=a,request_id='approve')
    i=installation(bundle,installed_at=utc_now());a=approval(store,bundle,'ACTIVATE')
    a.update(approved_at=utc_now(),installation_sha256=digest(i))
    pub.release(authorization=a,installation=i,request_id='release')
    monkeypatch.setenv('WEALTHPILOT_POLICY_DB_PATH',str(shared[0]))
    monkeypatch.setenv('WEALTHPILOT_POLICY_SCOPE',bundle['scope'])
    monkeypatch.setenv('WEALTHPILOT_POLICY_SHARED_CONTRACT',str(shared[1]))
    cfg=settings(None,None);cfg.e1_db_path=None;cfg.e1_enable_writes=False;cfg.prepare_config=None
    def forbidden(*a,**k):raise AssertionError('Policy read called business worker')
    monkeypatch.setattr(BusinessClient,'call',forbidden)
    async def run():
        async with Client(create_server(cfg),mode='legacy') as client:
            result=await client.call_tool('get_investment_policy_context',{'scope':'full'})
            assert not result.is_error
            value=result.structured_content or json.loads(result.content[0].text)
            assert value['policy_reference']['bundle_sha256']==digest(bundle)
            assert not value['execution_authorized']
    asyncio.run(run())


def test_explicit_risk_snapshot_uses_existing_readonly_tool(active, monkeypatch):
    cfg = settings(None, None)
    cfg.e1_db_path = None
    cfg.e1_enable_writes = False
    cfg.prepare_config = None
    calls = []

    def read(self, operation, arguments):
        calls.append((operation, arguments))
        return {"concentration": {"status": "CONDITIONAL_RISK", "execution_authorized": False,
            "etf_observation": {"status": "CONDITIONAL", "basis": "OFFICIAL_DISCLOSURE_ESTIMATE"}}}

    monkeypatch.setattr(BusinessClient, 'call', read)

    async def run():
        async with Client(create_server(cfg), mode='legacy') as client:
            for args in ({'scope':'portfolio', 'snapshot_id':'asset-view:v1:1'},
                         {'scope':'risk', 'snapshot_id':'../invalid'},
                         {'scope':'risk', 'symbol':'AAPL', 'snapshot_id':'asset-view:v1:1'}):
                assert (await client.call_tool('get_investment_policy_context', args)).is_error
            args = {'scope':'risk', 'snapshot_id':'asset-view:v1:1'}
            result = await client.call_tool('get_investment_policy_context', args)
            assert not result.is_error
            assert 'OFFICIAL_DISCLOSURE_ESTIMATE' in str(result.content)
            assert calls == [('get_investment_policy_context', args)]

    asyncio.run(run())


def test_business_child_receives_only_etf_reader_settings(monkeypatch):
    import subprocess

    cfg = settings(None, None)
    cfg.real_config = None
    public = {
        "WEALTHPILOT_ETF_RELEASE_DIRECTORY": "/isolated/not-opened",
        "WEALTHPILOT_ETF_RELEASE_PUBLIC_KEY": "a"*64,
        "WEALTHPILOT_ETF_RELEASE_OWNER_UID": "900",
    }
    for key, value in public.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("WEALTHPILOT_ETF_OWNER_PRIVATE_KEY", "TEST_ONLY")
    spawn = subprocess.Popen

    def inspect(command, **kwargs):
        probe = """
import json, os, sys
json.loads(sys.stdin.readline())
print(json.dumps({"ok": True, "result": {k: v for k, v in os.environ.items()
      if k.startswith("WEALTHPILOT_ETF_")}}), flush=True)
"""
        return spawn([command[0], "-c", probe], **kwargs)

    monkeypatch.setattr(subprocess, "Popen", inspect)
    client = BusinessClient(cfg)
    try:
        received = client.call("get_investment_policy_context", {"scope": "risk"})
        assert all(received[key] == value for key, value in public.items())
        assert "WEALTHPILOT_ETF_OWNER_PRIVATE_KEY" not in received
    finally:
        client.close()


def test_transition_mcp_uses_readonly_frozen_worker(tmp_path, monkeypatch):
    import sqlite3

    from app.asset_views import VERSION, PIPELINE
    from backend.services.investment_policy.contracts import digest
    from tests import test_policy_integration as policy_fixtures

    monkeypatch.setattr(policy_fixtures, "POLICY", policy_fixtures.POLICY.replace(b"SYN1", b"AAPL"))
    active = policy_fixtures.active.__wrapped__(tmp_path, monkeypatch)
    db = tmp_path / "mock-frozen.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE wealth_snapshots(id INTEGER, portfolio_id INTEGER, analysis_request_id TEXT, payload_json TEXT)")
        frozen = dict(schema_version=VERSION, pipeline=PIPELINE, state="ready", snapshot_id="asset-view:v1:1",
            captured_at="2001-01-01T15:00:00+00:00", portfolio_id=7, data_quality={},
            views={"get_manual_wealth_items": {"items": []}})
        conn.execute("INSERT INTO wealth_snapshots VALUES(1,7,'TEST_ONLY',?)", (json.dumps(frozen),))
    db.chmod(0o400)
    config = tmp_path / "read-only.json"
    config.write_text(json.dumps({"database_path": str(db), "portfolio_id": 7}))
    config.chmod(0o600)
    cfg = settings(None, None)
    cfg.e1_db_path = cfg.prepare_config = None
    cfg.e1_enable_writes = False
    cfg.real_config = str(config)
    policy_before = active[0].path.read_bytes()
    db_before = db.read_bytes()
    payload = dict(snapshot_id=frozen["snapshot_id"], snapshot_sha256=digest(frozen),
        policy_bundle_sha256=context(7)["policy_reference"]["bundle_sha256"],
        paths={"A": [{"label": "Blocked transfer", "actions": [{
            "kind": "TRANSFER_CASH", "action_id": "test", "cash_ref": "position:1", "amount": "5",
            "fee": "0", "outcome": "FAILED", "assumption": "TEST_ONLY fixture",
            "destination_account": "account_test", "destination_currency": "CNY", "destination_per_source": "1",
        }]}]})

    async def run():
        async with Client(create_server(cfg), mode="legacy") as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            transition = tools["simulate_portfolio_transition"]
            assert transition.annotations.read_only_hint and not transition.annotations.open_world_hint
            result = await client.call_tool(transition.name, {"request": payload})
            assert not result.is_error, result.content
            value = result.structured_content or json.loads(result.content[0].text)
            assert value["schema_version"] == "wealthpilot.portfolio-transition.v1"
            assert value["paths"]["A"][0]["concentration"]["status"] == "CONDITIONAL_RISK"
            assert value["paths"]["A"][1]["actions"][0]["blocked_reason"] == "SCENARIO_ACTION_FAILED"
            assert not value["execution_authorized"]
            rejected = await client.call_tool(transition.name, {"request": {**payload, "snapshot_sha256": "9"*64}})
            assert rejected.is_error and "SCENARIO_SNAPSHOT_BINDING_MISMATCH" in str(rejected.content)

    asyncio.run(run())
    assert db.read_bytes() == db_before and active[0].path.read_bytes() == policy_before


def test_e_candidate_uses_actual_mcp_worker_and_private_e1(tmp_path, monkeypatch):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from app.asset_views import PIPELINE, VERSION
    from backend.services.investment_thesis.portfolio_store import migrate_portfolio_governance
    from backend.services.investment_policy.contracts import digest
    from tests import test_policy_integration as policy_fixtures

    monkeypatch.setattr(policy_fixtures, "POLICY", policy_fixtures.POLICY.replace(b"SYN1", b"AAPL"))
    policy = policy_fixtures.active.__wrapped__(tmp_path, monkeypatch)[0]
    db = tmp_path/"frozen-e.sqlite"
    source = {"schema_version": VERSION, "pipeline": PIPELINE, "state": "ready", "snapshot_id": "asset-view:v1:1",
              "captured_at":"2001-01-01T15:00:00+00:00", "portfolio_id":7, "data_quality":{},
              "views":{"get_manual_wealth_items":{"items":[]}}}
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE wealth_snapshots(id INTEGER, portfolio_id INTEGER, analysis_request_id TEXT, payload_json TEXT)")
        conn.execute("INSERT INTO wealth_snapshots VALUES(1,7,'TEST_ONLY',?)", (json.dumps(source),))
    db.chmod(0o400)
    config = tmp_path/"e-read.json"
    config.write_text(json.dumps({"database_path":str(db), "portfolio_id":7})); config.chmod(0o600)
    e1 = ThesisStore(tmp_path/"e-private"/"research.sqlite", 7)
    e1.init()
    migrate_portfolio_governance(e1)
    cfg = settings(e1.path, None)
    cfg.prepare_config = None
    cfg.e1_enable_writes = False
    cfg.real_config = str(config)
    before = (db.read_bytes(), policy.path.read_bytes())
    bound = {"snapshot_id":source["snapshot_id"], "snapshot_sha256":digest(source),
             "policy_bundle_sha256":context(7)["policy_reference"]["bundle_sha256"]}
    at = datetime.now(timezone.utc)
    scenario = {**bound,"paths":{"A":[{"label":"S1","actions":[{
        "kind":"TRANSFER_CASH","action_id":"stalled","cash_ref":"position:1","amount":"100","fee":"0",
        "outcome":"FAILED","assumption":"TEST_ONLY",
        "destination_account":"account_fixture","destination_currency":"CNY","destination_per_source":"1",
    }]}]}}
    proposal = {**bound, "bundle_id":"e-mcp-demo", "request_id":"e-mcp-once","expected_revision":0, "scenario":scenario,
                "selected_path":"A","items":[{"item_id":"mvp","kind":"PLANNED_TRANSITION_HOLD","reason":"Test research",
                 "stage_index":1,"review_due_at":(at+timedelta(days=1)).isoformat(),
                 "expires_at":(at+timedelta(days=2)).isoformat(),"revoke_when":"If facts change"}]}

    async def run():
        async with Client(create_server(cfg), mode="legacy") as client:
            assert "commit_portfolio_review_decision" not in {tool.name for tool in (await client.list_tools()).tools}
        monkeypatch.setenv("WEALTHPILOT_E1_PORTFOLIO_MODE", "ISOLATED")
        async with Client(create_server(cfg), mode="legacy") as client:
            tools = {tool.name:tool for tool in (await client.list_tools()).tools}
            assert tools["preview_portfolio_review_decision"].annotations.read_only_hint is True
            assert tools["commit_portfolio_review_decision"].annotations.read_only_hint is False
            assert "commit_e1_record" not in tools

            async def call(name, arguments):
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.content
                return result.structured_content or json.loads(result.content[0].text)

            p = await call("preview_portfolio_review_decision", {"request":proposal})
            saved = await call("commit_portfolio_review_decision", {"request":{
                **{key:p[key] for key in ("preview_token", "preview_fingerprint", "request_id")},
                "selected_item_ids":["mvp"],"confirmed":True,"confirmation":"保存这次组合决定"}})
            assert saved["record"]["decisions"][0]["disposition_state"] == "RESEARCH_DECISION_WITH_ASSUMPTIONS"
            readback = await call("get_portfolio_review_followups", {"request":{"bundle_id":"e-mcp-demo","revision":1}})
            archive = readback["records"][0]["evidence_archive"]
            archived_input = archive["scenario"]["evidence_archive"]["normalized_input"]
            assert archived_input["paths"] == scenario["paths"]
            assert all(archived_input[key] == value for key, value in bound.items())
            assert not saved["execution_authorized"] and not readback["risk_events"]

        config.write_text(json.dumps({"database_path":str(db),"portfolio_id":8}))
        async with Client(create_server(cfg), mode="legacy") as client:
            wrong = await client.call_tool("get_portfolio_review_followups", {"source":bound})
            assert wrong.is_error and "E1_PORTFOLIO_SOURCE_SCOPE_MISMATCH" in str(wrong.content)

    asyncio.run(run())
    assert (db.read_bytes(), policy.path.read_bytes()) == before
