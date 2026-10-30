"""Policy SQL capability, fixed identity, indirect paths and pool isolation."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from app.shared_sqlite import schema_digest
from backend.services.investment_policy.colocated import CoLocatedPolicyStore
from backend.services.investment_policy.parser import PolicyError
from backend.services.investment_policy.store import SCHEMA,PolicyStore,utc_now
from backend.services.investment_policy.formal_publisher import PolicyPublisher
from backend.services.investment_policy.service import PolicyService
from tests.test_policy_publisher import approval,installation
from tests.test_investment_policy import PHILOSOPHY,POLICY


def bind_contract(path,receipt):
    with closing(sqlite3.connect(path)) as db:sha=schema_digest(db)
    s=path.stat()
    value=dict(schema='wealthpilot.shared-sqlite.v1',mode='shared_wal',database_path=str(path),
        file_identity=[s.st_dev,s.st_ino],portfolio_id=7,schema_sha256=sha)
    receipt.write_text(json.dumps(value));receipt.chmod(0o600)
    return value


@pytest.fixture
def shared(tmp_path,monkeypatch):
    tmp_path.chmod(0o700)
    path=tmp_path/'business.db';receipt=tmp_path/'database-contract.json'
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript("CREATE TABLE portfolios(id INTEGER PRIMARY KEY); INSERT INTO portfolios VALUES(7); CREATE TABLE orders(id INTEGER PRIMARY KEY,payload TEXT); INSERT INTO orders VALUES(1,'PRIVATE_BUSINESS_SENTINEL'); CREATE TABLE thesis_versions(payload TEXT); INSERT INTO thesis_versions VALUES('PRIVATE_E1_SENTINEL');")
        db.executescript(SCHEMA)
    path.chmod(0o600);bind_contract(path,receipt)
    monkeypatch.setenv('WEALTHPILOT_DB_PATH',str(path))
    return path,receipt,tmp_path


def stage(store,folder):
    paths={}
    for role,raw in [('philosophy',PHILOSOPHY),('policy',POLICY)]:
        p=folder/(role+'.md');p.write_bytes(raw.replace(b'2030-12-01',b'2020-01-01'));paths[role]=p
    return store.stage(scope='portfolio:7',version=1,philosophy_path=paths['philosophy'],policy_path=paths['policy'],
        expected_hashes={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in paths.items()},request_id='stage',expected_head_version=0,created_at=utc_now())


def test_actual_store_and_publisher_reuse_without_business_e1_reads(shared,monkeypatch):
    path,receipt,folder=shared
    with pytest.raises(PolicyError):PolicyStore(path)
    store=CoLocatedPolicyStore(path,receipt);b=stage(store,folder)
    store.transition(scope=b['scope'],bundle_id=b['bundle_id'],state='VALIDATED',expected_state='DRAFT',request_id='validate',at=utc_now())
    pub=PolicyPublisher(store,environment='ISOLATED_REHEARSAL')
    auth=approval(store,b);auth['approved_at']=utc_now();pub.approve(authorization=auth,request_id='approve')
    install=installation(b,installed_at=utc_now())
    from backend.services.investment_policy.contracts import digest
    auth=approval(store,b,'ACTIVATE');auth.update(approved_at=utc_now(),installation_sha256=digest(install))
    pub.release(authorization=auth,installation=install,request_id='release')
    monkeypatch.setenv('WEALTHPILOT_POLICY_DB_PATH',str(path));monkeypatch.setenv('WEALTHPILOT_POLICY_SCOPE',b['scope'])
    monkeypatch.setenv('WEALTHPILOT_POLICY_SHARED_CONTRACT',str(receipt))
    from backend.services.investment_policy.runtime import resolve
    assert resolve(7)['mode']=='POLICY_ACTIVE'
    assert PolicyService(store).get_active_policy_context('portfolio:8')['status']=='UNAVAILABLE'
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT payload FROM orders').fetchone()[0]=='PRIVATE_BUSINESS_SENTINEL'
        assert db.execute('SELECT payload FROM thesis_versions').fetchone()[0]=='PRIVATE_E1_SENTINEL'


@pytest.mark.parametrize('sql',[
    'SELECT * FROM orders','SELECT * FROM thesis_versions','SELECT count(*) FROM orders',
    'UPDATE orders SET payload=\'bad\'','DELETE FROM policy_events','UPDATE policy_bundles SET payload=\'{}\'',
    'CREATE TABLE evil(x)','DROP TABLE orders','ATTACH DATABASE ":memory:" AS other','PRAGMA writable_schema=ON',
    'SAVEPOINT sneaky','COMMIT','SELECT load_extension(?)',
    'INSERT OR REPLACE INTO policy_bundles VALUES(?,?,?,?,?,?,?,?,?)',
    'SELECT * FROM policy_events WHERE scope=? UNION SELECT * FROM orders',
])
def test_direct_business_e1_ddl_and_arbitrary_sql_denied(shared,sql):
    store=CoLocatedPolicyStore(*shared[:2])
    with pytest.raises(PolicyError):
        with store.connection(write=True) as db:db.execute(sql,('portfolio:7',))


def test_scope_raw_handle_transaction_control_and_closed_handle_are_rejected(shared):
    store=CoLocatedPolicyStore(*shared[:2])
    with pytest.raises(PolicyError):
        with store.connection() as db:db.execute('SELECT * FROM policy_events WHERE scope=?',('portfolio:8',))
    with pytest.raises(PolicyError):
        with store.connection() as db:db.set_authorizer(None)
    with store.connection() as db:assert db.execute('SELECT * FROM policy_events WHERE scope=?',('portfolio:7',)).fetchall()==[]
    with pytest.raises(PolicyError):db.execute('SELECT * FROM policy_events WHERE scope=?',('portfolio:7',))
    with pytest.raises(PolicyError):store.init_candidate()


@pytest.mark.parametrize('indirect',[
    "CREATE TRIGGER sneak AFTER INSERT ON policy_bundles BEGIN UPDATE orders SET payload='bad'; END",
    "CREATE TRIGGER sneak AFTER INSERT ON policy_bundles BEGIN SELECT payload FROM orders; END",
    "CREATE TRIGGER sneak AFTER INSERT ON policy_bundles BEGIN INSERT INTO policy_events SELECT * FROM stolen; END",
])
def test_authorizer_denies_indirect_trigger_access_even_with_trusted_schema_digest(shared,indirect):
    path,receipt,folder=shared
    with closing(sqlite3.connect(path)) as db:db.execute(indirect)
    bind_contract(path,receipt)
    store=CoLocatedPolicyStore(path,receipt)
    with pytest.raises(PolicyError,match='REJECTED'):stage(store,folder)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT count(*) FROM policy_bundles').fetchone()[0]==0
        assert db.execute('SELECT payload FROM orders').fetchone()[0]=='PRIVATE_BUSINESS_SENTINEL'


def test_schema_identity_and_contract_changes_fail_closed(shared):
    path,receipt,folder=shared;store=CoLocatedPolicyStore(path,receipt)
    with closing(sqlite3.connect(path)) as db:db.execute('DROP TRIGGER policy_events_no_replace')
    with pytest.raises(PolicyError):
        with store.connection():pass
    bind_contract(path,receipt)
    with pytest.raises(PolicyError,match='CONTRACT_CHANGED'):
        with store.connection():pass
    # New contract cannot turn a weakened trigger into an approved policy schema.
    with pytest.raises(PolicyError):
        with CoLocatedPolicyStore(path,receipt).connection():pass


def test_no_connection_pool_or_privilege_leak_from_business_writer(shared):
    path,receipt,folder=shared;store=CoLocatedPolicyStore(path,receipt)
    business=sqlite3.connect(path)
    try:
        assert business.execute('SELECT * FROM orders').fetchone()
        for _ in range(2):
            with pytest.raises(PolicyError):
                with store.connection() as db:db.execute('SELECT * FROM orders',('portfolio:7',))
        with store.connection() as db:assert db.execute('SELECT * FROM policy_events WHERE scope=?',('portfolio:7',)).fetchall()==[]
    finally:business.close()


def test_replaced_file_and_e1_identity_are_rejected(shared,monkeypatch):
    path,receipt,folder=shared;store=CoLocatedPolicyStore(path,receipt)
    from backend.services.investment_policy.formal_publisher import store_identity
    prior_identity=store_identity(store)
    replacement=folder/'different.db';replacement.write_bytes(path.read_bytes());replacement.chmod(0o600)
    os.replace(replacement,path)
    with pytest.raises(ValueError,match='IDENTITY'):
        with store.connection():pass
    bind_contract(path,receipt)
    assert store_identity(CoLocatedPolicyStore(path,receipt))!=prior_identity
    monkeypatch.setenv('WEALTHPILOT_E1_DB_PATH',str(path))
    with pytest.raises(PolicyError,match='E1_DATABASE'):CoLocatedPolicyStore(path,receipt)


def test_caught_authorization_failure_still_rolls_back_prior_append(shared):
    store=CoLocatedPolicyStore(*shared[:2]);b=stage(store,shared[2])
    with pytest.raises(PolicyError,match='TAINTED'):
        with store.connection(write=True) as db:
            db.execute('INSERT INTO policy_events VALUES(?,?,?,?,?,?,?)',('portfolio:7',2,b['bundle_id'],'VALIDATED','extra','{}','hash'))
            with pytest.raises(PolicyError):db.execute('SELECT * FROM orders')
    assert len(store.history('portfolio:7'))==1
    for name in ['_db','cursor','backup','set_authorizer','executescript']:
        with pytest.raises(PolicyError):
            with store.connection() as db:getattr(db,name)


def test_partial_shared_configuration_does_not_fall_back_to_legacy(shared,monkeypatch):
    monkeypatch.delenv('WEALTHPILOT_POLICY_DB_PATH',raising=False)
    monkeypatch.setenv('WEALTHPILOT_POLICY_SHARED_CONTRACT',str(shared[1]))
    from backend.services.investment_policy.runtime import resolve
    assert resolve(7)['mode']=='UNAVAILABLE'


def test_caught_identity_recheck_failure_cannot_commit_after_identity_restored(shared,monkeypatch):
    store=CoLocatedPolicyStore(*shared[:2]);b=stage(store,shared[2])
    original=store._check;flags={'fail':False}
    def check():
        if flags['fail']:raise PolicyError('INJECTED_PERMISSION_RECHECK_FAILURE')
        original()
    monkeypatch.setattr(store,'_check',check)
    with pytest.raises(PolicyError,match='TAINTED'):
        with store.connection(write=True) as db:
            db.execute('INSERT INTO policy_events VALUES(?,?,?,?,?,?,?)',('portfolio:7',2,b['bundle_id'],'VALIDATED','extra','{}','hash'))
            flags['fail']=True
            with pytest.raises(PolicyError,match='RECHECK_FAILURE'):
                db.execute('SELECT * FROM policy_events WHERE scope=?',('portfolio:7',))
            flags['fail']=False
    assert len(store.history('portfolio:7'))==1


def test_frozen_analysis_view_does_not_rebind_original_policy_owner(shared,monkeypatch):
    store=CoLocatedPolicyStore(*shared[:2]);b=stage(store,shared[2])
    monkeypatch.setenv('WEALTHPILOT_DB_PATH',str(shared[2]/'frozen-analysis.sqlite'))
    assert store.read_bundle(b['scope'],b['bundle_id'])==b
