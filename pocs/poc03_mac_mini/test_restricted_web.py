"""Real restricted routes/services over disposable synthetic WAL databases."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_shared_database import fixed, shared  # noqa: F401  isolated fixtures
import common
import deploy
import snapshot
import web_deploy
import web_runner
from app import database
from backend.restricted_web import create_app
from backend.services.restricted_sync import SyncExecutor, ServiceFactory
from services.broker_sync.guojin.sync_service import GuojinSyncService
from services.broker_sync.schema import Position
from services.broker_sync.models import PositionSnapshotRun


def position(value=2500, account="fixture-account"):
    return Position(broker="guojin", account_id=account, symbol="000001:CN", raw_symbol="000001",
        name="合成持仓", asset_class="cash", vehicle_type="CASH", economic_asset_class="CASH",
        classification_source="USER_EXPLICIT", classification_verification_status="EXPLICIT",
        market="CN", quantity=Decimal(1), avg_cost=Decimal(value), cost_method="weighted_average",
        cost_basis=Decimal(value), current_price=Decimal(value), market_value=Decimal(value), currency="CNY",
        unrealized_pnl=Decimal(0), unrealized_pnl_pct=Decimal(0), snapshot_time=datetime.now(timezone.utc), sync_source="api")


def fake_service(broker, value=2500):
    assert broker == "guojin"
    service = GuojinSyncService.__new__(GuojinSyncService)
    service.adapter = type("FixtureAdapter", (), {"account_id": "fixture-account"})()
    service.fetch_positions = lambda: ([position(value)], {})
    return service


@pytest.fixture
def web(shared, monkeypatch):
    import backend.core.demo_mode as demo
    monkeypatch.setattr(demo, "PUBLIC_DEMO_MODE", False)
    root, db, contract = shared
    directory = root / "fixture-dist"; directory.mkdir()
    (directory / "assets").mkdir()
    (directory / "assets/fixture.js").write_text("// synthetic")
    (directory / "index.html").write_text("<html><head></head><body>synthetic</body></html>")
    monkeypatch.setenv("GUOJIN_ACCOUNT", "fixture-account")
    config = {"database_path": str(db), "database_contract_path": str(contract), "portfolio_id": 7,
              "enabled_brokers": ["guojin"], "lock_path": str(root / "run/web.lock"), "revision": "a" * 40}
    return config, directory


def test_explicit_route_catalog_and_static_mode(web):
    config, directory = web
    app = create_app(config, directory, factory=fake_service)
    actual = {(r.path, tuple(sorted(r.methods))) for r in app.routes if hasattr(r, "methods")}
    assert actual == {(p, (method,)) for method, p in [
        ("GET", "/api/demo/status"), ("GET", "/api/web/status"),
        *(('GET', '/api/portfolio/' + p) for p in ('summary','positions','liabilities','alerts')),
        ("GET", "/api/wealth/summary"), ("GET", "/api/wealth/items"),
        ("GET", "/api/allocation/targets"), ("GET", "/api/broker-sync/status"),
        ("POST", "/api/broker-sync/trigger"), ("GET", "/")
    ]}
    with TestClient(app, base_url="http://127.0.0.1:8790") as client:
        assert "__WP_RESTRICTED_WEB__=true" in client.get("/").text
        assert client.get("/assets/fixture.js").status_code == 200
        assert client.get("/api/web/status").json()["scheduler_enabled"] is False
        assert client.get("/api/demo/status").json()["public_demo_mode"] is False


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/action/orders"), ("POST", "/api/action/orders"), ("GET", "/openapi.json"),
    ("POST", "/api/wealth/items"), ("DELETE", "/api/portfolio/positions"),
    ("POST", "/api/portfolio/import/csv"), ("POST", "/api/decision/chat"),
    ("POST", "/api/allocation/increment-plan"), ("GET", "/api/unknown"),
    ("POST", "/api/portfolio/summary"), ("GET", "/api/broker-sync/trigger"),
])
def test_disallowed_methods_and_routes_never_serve_spa(web, method, path):
    app = create_app(*web, factory=fake_service)
    with TestClient(app, base_url="http://127.0.0.1:8790") as client:
        response = client.request(method, path)
        assert response.status_code in (404, 405)
        assert "text/html" not in response.headers.get("content-type", "")


@pytest.mark.parametrize("headers", [{"Host": "attacker.example"}, {"Origin": "https://attacker.example"},
    {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}])
def test_loopback_and_origin_boundary(web, headers):
    app = create_app(*web, factory=fake_service)
    with TestClient(app, base_url="http://127.0.0.1:8790") as client:
        assert client.get("/api/wealth/summary", headers=headers).status_code == 403


def test_real_manual_sync_commits_then_fresh_mcp_reads(shared, web):
    root, db, contract = shared
    history = (root / "data/migration-reference.json").read_bytes()
    app = create_app(*web, factory=fake_service)
    with TestClient(app, base_url="http://127.0.0.1:8790") as client:
        result = client.post("/api/broker-sync/trigger", json={"broker": "guojin"})
        assert result.status_code == 202 and result.json()["brokers_triggered"] == ["guojin"]
        status = client.get("/api/broker-sync/status").json()
        item = next(i for i in status["brokers"] if i["broker"] == "guojin")
        assert item["last_attempt_status"] == "success" and item["last_attempt_id"] == result.json()["jobs"][0]["run_id"]
        assert item["enabled"] and not item["running"]
        from reader import read_overview
        data = read_overview(db, 7, database_contract=contract)
        summary = client.get("/api/wealth/summary").json()
        assert data["total_assets_minor"] == 350000
        assert all(data[k + "_minor"] == round(summary[k] * 100) for k in ("total_assets","total_liabilities","net_worth"))
        assert "VALUATION_DATE_UNKNOWN" in data["issues"]
    with database.get_session() as session:
        assert session.execute(text("SELECT portfolio_id FROM positions WHERE broker='guojin'")).scalar() == 7
        assert session.execute(text("SELECT count(*) FROM position_snapshot_runs")).scalar() == 1
    assert (root / "data/migration-reference.json").read_bytes() == history


def test_constructor_failure_is_redacted_and_last_good_is_retained(shared, web):
    app = create_app(*web, factory=fake_service)
    with TestClient(app, base_url="http://127.0.0.1:8790") as client:
        client.post("/api/broker-sync/trigger", json={"broker":"guojin"})
        good = client.get("/api/wealth/summary").json()
        def broken(_):
            raise RuntimeError("PRIVATE_FIXTURE_SECRET_AND_ACCOUNT")
        app.state.sync_executor.factory = broken
        response = client.post("/api/broker-sync/trigger", json={"broker":"guojin"})
        assert response.status_code == 202
        status = client.get("/api/broker-sync/status")
        assert "PRIVATE_FIXTURE" not in status.text
        item = next(i for i in status.json()["brokers"] if i["broker"] == "guojin")
        assert item["last_attempt_status"] == "failed" and item["last_successful_position_count"] == 1
        assert client.get("/api/wealth/summary").json()["total_assets"] == good["total_assets"]


def test_failed_business_upsert_does_not_commit_snapshots_or_success(shared, web, monkeypatch):
    def broken(*args):
        raise RuntimeError("synthetic mid-transaction failure")
    monkeypatch.setattr(GuojinSyncService, "_remove_qmt_stale_positions", broken)
    with TestClient(create_app(*web, factory=fake_service), base_url="http://127.0.0.1:8790") as client:
        client.post("/api/broker-sync/trigger", json={"broker":"guojin"})
        item = next(i for i in client.get("/api/broker-sync/status").json()["brokers"] if i["broker"]=="guojin")
        assert item["last_attempt_status"] == "failed"
    with database.get_session() as session:
        assert session.execute(text("SELECT count(*) FROM position_snapshots")).scalar() == 0
        assert session.execute(text("SELECT count(*) FROM positions WHERE broker='guojin'")).scalar() == 0


def test_disabled_brokers_and_all_share_one_allowlist(web):
    config, directory = web
    config["enabled_brokers"] = []
    def forbidden(_):
        pytest.fail("Disabled broker invoked")
    with TestClient(create_app(config, directory, factory=forbidden), base_url="http://127.0.0.1:8790") as client:
        assert client.post("/api/broker-sync/trigger", json={"broker":"tiger"}).status_code == 400
        assert client.post("/api/broker-sync/trigger", json={"broker":"all"}).json()["brokers_triggered"] == []
        assert all(not i["enabled"] for i in client.get("/api/broker-sync/status").json()["brokers"])


def test_duplicate_manual_and_future_cron_hold_one_lock(web):
    entered, release = threading.Event(), threading.Event()
    def factory(broker):
        service = fake_service(broker)
        original = service.fetch_positions
        def wait():
            entered.set()
            assert release.wait(5)
            return original()
        service.fetch_positions = wait
        return service
    app = create_app(*web, factory=factory)
    results = []
    with TestClient(app, base_url="http://127.0.0.1:8790") as client:
        thread = threading.Thread(target=lambda: results.append(client.post("/api/broker-sync/trigger",json={"broker":"guojin"})))
        thread.start()
        try:
            assert entered.wait(4)
            response = client.post("/api/broker-sync/trigger",json={"broker":"guojin"})
            assert response.json()["already_running"] == ["guojin"]
            jobs, busy = app.state.sync_executor.reserve("guojin", "cron")
            assert jobs == [] and busy == ["guojin"]
        finally:
            release.set(); thread.join(7)
        assert results[0].status_code == 202


def test_restart_marks_only_our_interrupted_runs_failed(web):
    with database.get_session() as session:
        for source in ("restricted:manual", "manual"):
            session.add(PositionSnapshotRun(portfolio_id=7,broker="guojin",account_id=source,status="running",triggered_by=source,
                context_json=json.dumps({"token":"synthetic","lease_until":"2000-01-01T00:00:00+00:00"}) if source.startswith("restricted:") else None))
        session.commit()
    with TestClient(create_app(*web, factory=fake_service), base_url="http://127.0.0.1:8790"):
        with database.get_session() as session:
            runs = session.query(PositionSnapshotRun).order_by(PositionSnapshotRun.id).all()
            assert [r.status for r in runs] == ["failed", "running"]
            assert runs[0].error_message == "SYNC_LEASE_EXPIRED"


def test_process_lock_rejects_second_web_instance(web):
    with TestClient(create_app(*web, factory=fake_service), base_url="http://127.0.0.1:8790"):
        with pytest.raises(BlockingIOError):
            with TestClient(create_app(*web, factory=fake_service), base_url="http://127.0.0.1:8790"):
                pytest.fail("Duplicate process accepted")


def test_web_plist_and_dry_run_never_touch_mcp(shared, monkeypatch):
    root, _, _ = shared
    common.write_json(root / "config/web.json", {"revision":"a"*40,"enabled_brokers":[]})
    original = deploy.render_plists(root, common.private_json(root / "config/runtime.json"))
    planned = web_deploy.render_plist(root)
    assert planned["Label"] == "com.wealthpilot.restricted-web"
    assert all("current" not in v for v in planned["ProgramArguments"])
    assert planned["UserName"] == "fixture" and planned["Umask"] == 0o077
    def forbidden(_): pytest.fail("launchctl executed")
    assert web_deploy.service_action(root, "install", execute=forbidden)["apply"] is False
    assert original == deploy.render_plists(root, common.private_json(root / "config/runtime.json"))


def test_dist_hash_tampering_refused(shared):
    root, _, _ = shared
    release = root / "releases" / ("a" * 40)
    directory = release / "web-dist"; directory.mkdir()
    (directory / "index.html").write_text("fixture")
    common.write_json(release / "web-assets.json", {"revision":release.name,"files":{"index.html":common.digest(directory/"index.html")}})
    web_deploy.verify_dist(release)
    (directory / "index.html").write_text("changed")
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        web_deploy.verify_dist(release)


def test_upsert_and_stale_removal_stay_in_portfolio_account_and_source(shared, web):
    with database.get_engine().begin() as conn:
        conn.execute(text("INSERT INTO portfolios(id,name) VALUES(8,'other synthetic')"))
        for pid, account, symbol, source in ((8,"other-portfolio-account","000001:CN","api"),
            (7,"other-account","000002:CN","api"), (7,None,"000003:CN",None),
            (7,"fixture-account","000004:CN","api")):
            conn.execute(text("INSERT INTO positions(portfolio_id,name,platform,ticker,symbol,broker,broker_account_id,sync_source,segment,market_value_cny,asset_class) "
                              "VALUES(:pid,'synthetic','国金证券',:ticker,:symbol,:broker,:account,:source,'投资',50,'货币')"),
                         {"pid":pid,"ticker":symbol.split(':')[0],"symbol":symbol,
                          "broker":"guojin" if account else None,"account":account,"source":source})
    with TestClient(create_app(*web,factory=fake_service),base_url="http://127.0.0.1:8790") as client:
        client.post('/api/broker-sync/trigger',json={"broker":"guojin"})
    with database.get_engine().connect() as conn:
        rows=conn.execute(text("SELECT portfolio_id,broker_account_id,symbol,market_value_cny FROM positions WHERE platform='国金证券' ORDER BY portfolio_id,symbol")).all()
    assert rows == [(7,"fixture-account","000001:CN",2500),(7,"other-account","000002:CN",50),
                    (7,None,"000003:CN",50),(8,"other-portfolio-account","000001:CN",50)]


@pytest.mark.parametrize("unknown_account", [False, True])
def test_empty_source_guard_keeps_last_good(shared, web, monkeypatch, unknown_account):
    monkeypatch.setenv("GUOJIN_GATEWAY_URL","http://127.0.0.1:1")
    monkeypatch.setenv("GUOJIN_GATEWAY_SECRET","synthetic-not-a-key")
    monkeypatch.setattr(GuojinSyncService, "fetch_positions", lambda _: ([position(account="unknown")] if unknown_account else [], {}))
    with TestClient(create_app(*web,factory=ServiceFactory()),base_url="http://127.0.0.1:8790") as client:
        client.post('/api/broker-sync/trigger',json={"broker":"guojin"})
        item=next(i for i in client.get('/api/broker-sync/status').json()['brokers'] if i['broker']=='guojin')
        assert item['last_attempt_status']=='failed'
        assert client.get('/api/wealth/summary').json()['total_assets']==1000


def test_web_start_requires_writer_contract_and_expected_portfolio(web, monkeypatch):
    config,directory=web
    config['portfolio_id']=8
    with pytest.raises(ValueError,match='DATABASE_BINDING_MISMATCH'):
        create_app(config,directory)
    config['portfolio_id']=7
    monkeypatch.delenv('WEALTHPILOT_SQLITE_CONTRACT')
    with pytest.raises(ValueError,match='WRITER_CONTRACT_REQUIRED'):
        create_app(config,directory)


def test_web_version_select_and_configuration_are_inert(shared,monkeypatch):
    root,db,_=shared
    revision='a'*40
    release=root/'releases'/revision
    common.write_json(release/'installed.json',{'environments':['mcp','business','web']})
    directory=release/'web-dist';directory.mkdir();(directory/'index.html').write_text('fixture')
    common.write_json(release/'web-assets.json',{'revision':revision,'files':{'index.html':common.digest(directory/'index.html')}})
    monkeypatch.setattr(web_deploy,'check_revision',lambda *a:None)
    original=(root/'config/runtime.json').read_bytes()
    with pytest.raises(ValueError,match='GATEWAY_VERIFICATION_REQUIRED'):
        web_deploy.configure(root,revision,['guojin'])
    web_deploy.configure(root,revision)
    assert common.private_json(root/'config/web.json')['enabled_brokers']==[]
    before=(root/'config/web.json').read_bytes()
    assert web_deploy.select_version(root,revision)['apply'] is False
    assert (root/'config/web.json').read_bytes()==before
    with pytest.raises(ValueError,match='STOP_WEB'):
        web_deploy.select_version(root,revision,apply=True)
    assert (root/'config/runtime.json').read_bytes()==original


def test_web_manifest_hashes_and_optional_arm_web_dependencies():
    import hashlib
    records=json.loads(Path(__file__).with_name('evidence').joinpath('arm-web-wheel-assets.json').read_text())['wheels']
    lock=Path(__file__).with_name('locks').joinpath('web.txt').read_text()
    assert len(records)==9
    assert all('sha256:'+r['sha256'] in lock for r in records)
    assert all(r['filename'].endswith('py3-none-any.whl') for r in records)


def test_real_mcp_http_sees_web_sync_commit(shared,web,tmp_path):
    mcp_python=os.environ.get('MCP_TEST_PYTHON')
    if not mcp_python:
        pytest.skip('MCP_TEST_PYTHON required for independent transport runtime')
    import socket,time
    from urllib.request import urlopen
    from urllib.error import HTTPError,URLError
    root,_,_=shared
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={'PATH':os.defpath,'PYTHON_DOTENV_DISABLED':'1'}
    command=[mcp_python,str(Path(__file__).parents[1]/'poc02_wealth_mcp/server.py'),'--port',str(port),
             '--business-python',sys.executable,'--real-config',str(root/'config/wealth.json'),
             '--access-evidence',str(root/'config/access-evidence.json')]
    process=subprocess.Popen(command,env=env,cwd=tmp_path,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    script=tmp_path/'mcp-client.py'
    script.write_text("import asyncio,sys\nfrom mcp import Client\nasync def main():\n async with Client(sys.argv[1],mode='legacy',read_timeout_seconds=20) as c:\n  result=await c.call_tool('get_wealth_overview',{'currency':'CNY'})\n  assert not result.is_error\n  assert result.structured_content['total_assets_minor']==int(sys.argv[2])\nasyncio.run(main())\n")
    url=f'http://127.0.0.1:{port}/mcp'
    try:
        for _ in range(150):
            assert process.poll() is None
            try: urlopen(url,timeout=.1).close();break
            except HTTPError:break
            except (URLError,TimeoutError):time.sleep(.1)
        for amount in (100000,350000):
            if amount==350000:
                with TestClient(create_app(*web,factory=fake_service),base_url='http://127.0.0.1:8790') as client:
                    assert client.post('/api/broker-sync/trigger',json={'broker':'guojin'}).status_code==202
            result=subprocess.run([mcp_python,str(script),url,str(amount)],env=env,capture_output=True,timeout=30)
            assert result.returncode==0,result.stderr.decode()
    finally:
        process.terminate()
        try:process.wait(5)
        except subprocess.TimeoutExpired:process.kill();process.wait(5)


def test_restricted_import_does_not_load_full_lifespan_or_trading(tmp_path):
    code = '''import importlib.abc,sys,socket
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  forbidden=('backend.main','backend.api.action','backend.agents','backend.skills','decision_engine','apscheduler','backend.services.action.order_manager','backend.services.action.order_poller','backend.services.execution_plan')
  if any(fullname==p or fullname.startswith(p+'.') for p in forbidden): raise RuntimeError('FORBIDDEN_RESTRICTED_IMPORT')
sys.meta_path.insert(0,Guard())
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('NO_EXTERNAL_NETWORK'))
from backend.restricted_web import create_app
from pathlib import Path
assert not Path(sys.argv[1]).exists()
'''
    db=tmp_path/'never-created.db'
    env={'PATH':os.defpath,'PYTHON_DOTENV_DISABLED':'1','PUBLIC_DEMO_MODE':'false','WEALTHPILOT_DB_PATH':str(db)}
    result=subprocess.run([sys.executable,'-c',code,str(db)],env=env,capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr.decode()


def test_runner_excludes_inherited_credentials_and_disabled_broker_env(shared,monkeypatch):
    root,_,_=shared
    release=root/'releases'/('a'*40)
    common.write_json(release/'installed.json',{'environments':['mcp','business','web']})
    directory=release/'web-dist';directory.mkdir();(directory/'index.html').write_text('fixture')
    common.write_json(release/'web-assets.json',{'revision':release.name,'files':{'index.html':common.digest(directory/'index.html')}})
    common.write_json(root/'config/web.json',{'revision':release.name,'enabled_brokers':[]})
    common.write_json(root/'config/web-brokers.json',{'GUOJIN_GATEWAY_SECRET':'synthetic-unused-secret'})
    monkeypatch.setattr(web_runner,'ROOT',release/'code')
    monkeypatch.setattr(web_runner,'host_guard',lambda *a:None)
    monkeypatch.setattr(web_runner,'check_revision',lambda *a:None)
    monkeypatch.setenv('OPENAI_API_KEY','synthetic-inherited-secret')
    _,config,environment=web_runner.settings(root)
    assert config['portfolio_id']==7
    assert 'OPENAI_API_KEY' not in environment and 'GUOJIN_GATEWAY_SECRET' not in environment
    assert environment['IBKR_READ_ONLY_MODE']=='true' and environment['ENABLE_IBKR_LIVE_TRADING']=='false'
    common.write_json(root/'config/web-brokers.json',{'OPENAI_API_KEY':'forbidden'})
    with pytest.raises(ValueError,match='BROKER_ENV_INVALID'):
        web_runner.settings(root)


def test_web_sync_wal_backup_and_web_restart(shared,web):
    root,db,_=shared
    before=(root/'data/migration-reference.json').read_bytes()
    with closing(sqlite3.connect(db)) as keep_wal:
        keep_wal.execute('PRAGMA journal_mode=WAL')
        with TestClient(create_app(*web,factory=fake_service),base_url='http://127.0.0.1:8790') as client:
            client.post('/api/broker-sync/trigger',json={'broker':'guojin'})
            expected=client.get('/api/wealth/summary').json()
            saved=root/'backups/web-runtime.db'
            manifest=snapshot.backup(db,saved)
            snapshot.verify(saved,manifest)
            assert manifest['tables']['position_snapshot_runs']['rows']==1
            assert manifest['tables']['position_snapshots']['rows']==1
    with TestClient(create_app(*web,factory=fake_service),base_url='http://127.0.0.1:8790') as client:
        actual=client.get('/api/wealth/summary').json()
        assert all(actual[k]==expected[k] for k in ('total_assets','total_liabilities','net_worth'))
    assert (root/'data/migration-reference.json').read_bytes()==before


def test_broker_configuration_changes_need_stopped_web_and_keep_mcp_config(shared):
    root,_,_=shared
    common.write_json(root/'config/web.json',{'revision':'a'*40,'enabled_brokers':[]})
    before=(root/'config/runtime.json').read_bytes()
    with pytest.raises(ValueError,match='GATEWAY_VERIFICATION_REQUIRED'):
        web_deploy.set_brokers(root,['guojin'])
    assert web_deploy.set_brokers(root,['guojin'],True)['apply'] is False
    with pytest.raises(ValueError,match='STOP_WEB'):
        web_deploy.set_brokers(root,['guojin'],True,apply=True)
    assert common.private_json(root/'config/web.json')['enabled_brokers']==[]
    assert web_deploy.set_brokers(root,['guojin'],True,True,True)['sync_started'] is False
    assert (root/'config/runtime.json').read_bytes()==before


def test_web_bootstrap_failure_cleans_only_its_own_job(shared,tmp_path,monkeypatch):
    root,_,_=shared
    common.write_json(root/'config/web.json',{'revision':'a'*40,'enabled_brokers':[]})
    daemon=tmp_path/'fake-daemons';daemon.mkdir()
    (daemon/(web_deploy.LABEL+'.plist')).write_bytes(plistlib.dumps(web_deploy.render_plist(root)))
    monkeypatch.setattr(os,'geteuid',lambda:0)
    calls=[]
    def fake(command):
        calls.append(command)
        if command[1]=='bootstrap':raise subprocess.CalledProcessError(1,command)
    with pytest.raises(subprocess.CalledProcessError):
        web_deploy.service_action(root,'start',True,daemon,fake)
    assert calls[-1]==['/bin/launchctl','bootout','system/'+web_deploy.LABEL]
    assert not any('secure-tunnel' in str(call) or 'readonly-mcp' in str(call) for call in calls)


def test_ibkr_sync_factory_has_readonly_client_without_action_router(monkeypatch):
    monkeypatch.setenv('BROKER_MODE','ibkr.readonly')
    monkeypatch.setenv('IBKR_READ_ONLY_MODE','true')
    monkeypatch.setenv('ENABLE_IBKR_LIVE_TRADING','false')
    from core.config import settings
    monkeypatch.setattr(settings,'ibkr_account','DU_SYNTHETIC')
    assert 'backend.api.action' not in sys.modules
    factory=ServiceFactory()
    service=factory('snowball')
    assert service._client.broker_name=='ibkr'
    assert not hasattr(service._client,'place_order') and not hasattr(service._client,'cancel_order')
    assert 'backend.api.action' not in sys.modules
    assert factory.ibkr._loop is None and not factory.ibkr._connected
    factory.close()


def test_concurrent_web_refreshes_do_not_fail_during_wal_cleanup(web,caplog):
    from concurrent.futures import ThreadPoolExecutor
    paths=['/api/broker-sync/status','/api/wealth/summary','/api/portfolio/summary']
    with TestClient(create_app(*web,factory=fake_service),base_url='http://127.0.0.1:8790') as client:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda i:client.get(paths[i%len(paths)]).status_code,range(90)))
    assert all(status==200 for status in results),caplog.text
