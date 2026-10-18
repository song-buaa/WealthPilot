"""B0->U0 exact digest, explicit DDL, backup and no-loss rollback rehearsal."""
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import pytest
from sqlalchemy import create_engine
from app.database import Base
from app.shared_sqlite import schema_digest,verify
from app.u0_schema import plan,verify as verify_u0
import app.models
import backend.services.action.models
import services.broker_sync.models

@pytest.fixture
def deployed_b0(tmp_path):
    spec=importlib.util.spec_from_file_location('u0_migration_fixture',Path(__file__).resolve().parents[1]/'scripts/migrate_b0_safety.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli);cli.select_stage('u0')
    root=tmp_path/'private';root.mkdir(mode=0o700);db=root/'synthetic.db'
    engine=create_engine('sqlite:///'+str(db));Base.metadata.create_all(engine);engine.dispose()
    with closing(sqlite3.connect(db)) as c:
        c.execute('PRAGMA journal_mode=WAL');c.execute('DROP INDEX uq_sync_active_owner')
        c.execute('ALTER TABLE position_snapshot_runs DROP COLUMN context_json')
        c.execute("INSERT INTO portfolios(id,name) VALUES(7,'synthetic')");c.commit();digest=schema_digest(c)
    os.chmod(db,0o600);st=db.stat();contract=root/'contract.json'
    cli.write_private(contract,{'schema':'wealthpilot.shared-sqlite.v1','mode':'shared_wal','database_path':str(db),
        'file_identity':[st.st_dev,st.st_ino],'portfolio_id':7,'schema_sha256':digest,
        'b0_migration_receipt':'historical-b0-receipt-unchanged'})
    return cli,root,db,contract


def test_explicit_u0_plan_has_one_column_one_index_and_preserves_b0_receipt(deployed_b0):
    cli,root,db,contract=deployed_b0;inode=db.stat().st_ino
    p=cli.make_plan(db,contract)
    assert p['schema']=='wealthpilot.u0-migration.v1' and len(p['ddl'])==2
    assert all('wealth_snapshots' not in ddl and 'CREATE TABLE' not in ddl for ddl in p['ddl'])
    cli.apply_plan(db,contract,p,root/'before.db',root/'receipt.json',writers_stopped=True)
    assert verify(db,contract)['b0_migration_receipt']=='historical-b0-receipt-unchanged'
    assert db.stat().st_ino==inode and cli.make_plan(db,contract)['ddl']==[]
    with closing(sqlite3.connect(db)) as c:verify_u0(c)
    cli.restore_prewrite(db,contract,root/'receipt.json',root/'preserved.db',writers_stopped=True)
    assert db.stat().st_ino==inode and len(cli.make_plan(db,contract)['ddl'])==2


def test_duplicate_active_run_and_wrong_index_block_without_mutation(deployed_b0):
    cli,root,db,contract=deployed_b0
    with closing(sqlite3.connect(db)) as c:
        for _ in range(2):c.execute("INSERT INTO position_snapshot_runs(portfolio_id,broker,account_id,started_at,status,sync_source,retry_count,triggered_by) VALUES(7,'tiger','fixture','2026-01-01','running','api',0,'manual')")
        c.commit();before=schema_digest(c)
        with pytest.raises(ValueError,match='DUPLICATE_ACTIVE'):plan(c)
        assert schema_digest(c)==before
        c.execute("DELETE FROM position_snapshot_runs")
        c.execute("CREATE INDEX uq_sync_active_owner ON position_snapshot_runs(account_id)");c.commit()
        with pytest.raises(ValueError,match='INDEX_CONFLICT'):plan(c)


def test_u0_interrupted_contract_recovery_and_new_fact_restore_refusal(deployed_b0,monkeypatch):
    cli,root,db,contract=deployed_b0;p=cli.make_plan(db,contract);original=cli.write_private
    def crash(path,value):
        if Path(path)==contract:raise OSError('synthetic interruption')
        return original(path,value)
    monkeypatch.setattr(cli,'write_private',crash)
    with pytest.raises(OSError):cli.apply_plan(db,contract,p,root/'before.db',root/'receipt.json',writers_stopped=True)
    with pytest.raises(ValueError,match='SCHEMA_MISMATCH'):verify(db,contract)
    monkeypatch.setattr(cli,'write_private',original)
    cli.resume_contract(db,contract,root/'receipt.json',writers_stopped=True)
    with closing(sqlite3.connect(db)) as c:c.execute("UPDATE portfolios SET name='new-fact' WHERE id=7");c.commit()
    with pytest.raises(ValueError,match='BUSINESS_WRITES'):cli.restore_prewrite(db,contract,root/'receipt.json',root/'never.db',writers_stopped=True)
    assert verify(db,contract)


def cash_request(cli,db):
    with closing(sqlite3.connect(db)) as c:
        cursor=c.execute("INSERT INTO positions(portfolio_id,name,ticker,symbol,platform,asset_class,vehicle_type,broker_security_type,currency,quantity,cost_price,current_price,market_value_cny,original_value,segment) VALUES(7,'legacy cash','CASH-CNY','CASH-CNY:CN','manual','货币','CASH','CASH','CNY',100,1,1,100,100,'投资')")
        row_id=cursor.lastrowid
        c.commit();c.row_factory=sqlite3.Row;row=c.execute('SELECT * FROM positions WHERE id=?',(row_id,)).fetchone()
        return dict(kind='cash_owner',position_id=row['id'],expected_fingerprint=cli.row_fingerprint(row),
            broker='tiger',account_id='fixture',currency='CNY',confirmed_same_cash=True,evidence_ref='synthetic-account-reconciliation')


def test_explicit_cash_mapping_preserves_facts_and_reuses_row_on_sync(deployed_b0):
    from sqlalchemy.orm import Session
    from services.broker_sync.repository import PositionSnapshotRepository
    from services.broker_sync.schema import cash_position
    from app.models import Position
    from datetime import datetime,timezone
    cli,root,db,contract=deployed_b0
    request=cash_request(cli,db)
    with closing(sqlite3.connect(db)) as c:
        c.row_factory=sqlite3.Row;before=dict(c.execute('SELECT * FROM positions').fetchone())
    p=cli.make_plan(db,contract,[request])
    assert len(p['ddl'])==2 and p['corrections'][0]['kind']=='cash_owner'
    cli.apply_plan(db,contract,p,root/'before.db',root/'receipt.json',writers_stopped=True)
    with closing(sqlite3.connect(db)) as c:
        c.row_factory=sqlite3.Row;after=dict(c.execute('SELECT * FROM positions').fetchone())
    changed={'broker','broker_account_id','sync_source','ticker','symbol'}
    assert {k:v for k,v in before.items() if k not in changed}=={k:v for k,v in after.items() if k not in changed}
    assert after['broker']=='tiger' and after['broker_account_id']=='fixture' and after['sync_source']=='api'
    engine=create_engine('sqlite:///'+str(db))
    with Session(engine,info={'portfolio_id':7}) as s:
        for amount in [100,80,80]:
            PositionSnapshotRepository(s).synchronize('tiger','fixture',lambda:[cash_position('tiger','fixture','CNY',amount,datetime.now(timezone.utc))],retries=0)
        assert s.query(Position).count()==1 and s.query(Position).one().id==request['position_id']
        assert s.query(Position).one().market_value_cny==80
    engine.dispose()


@pytest.mark.parametrize('failure',['attestation','fingerprint','duplicate_target','existing_api','cross_portfolio'])
def test_cash_mapping_requires_proof_and_never_merges(deployed_b0,failure):
    cli,root,db,contract=deployed_b0;request=cash_request(cli,db);requests=[request]
    with closing(sqlite3.connect(db)) as c:
        if failure=='existing_api':
            c.execute("INSERT INTO positions(portfolio_id,name,platform,asset_class,vehicle_type,broker,broker_account_id,sync_source,symbol,currency) VALUES(7,'existing','test','货币','CASH','tiger','fixture','api','CASH-CNY:CN','CNY')")
        elif failure=='cross_portfolio':
            c.execute("INSERT INTO portfolios(id,name) VALUES(8,'other')")
            c.execute("INSERT INTO positions(portfolio_id,name,platform,asset_class,broker,broker_account_id) VALUES(8,'other','test','货币','tiger','fixture')")
        c.commit()
    if failure=='attestation':request['confirmed_same_cash']=False
    if failure=='fingerprint':request['expected_fingerprint']='0'*64
    if failure=='duplicate_target':
        second=cash_request(cli,db);requests.append(second)
    with closing(sqlite3.connect(db)) as c:before=cli.inventory(c)
    with pytest.raises(ValueError):cli.make_plan(db,contract,requests)
    with closing(sqlite3.connect(db)) as c:assert cli.inventory(c)==before
    assert not (root/'receipt.json').exists()


def test_cash_mapping_plan_rejects_changed_row_before_any_apply(deployed_b0):
    cli,root,db,contract=deployed_b0;request=cash_request(cli,db)
    p=cli.make_plan(db,contract,[request])
    with closing(sqlite3.connect(db)) as c:
        c.execute('UPDATE positions SET market_value_cny=101 WHERE id=?',(request['position_id'],));c.commit()
    with pytest.raises(ValueError,match='POSITION_CAS_MISMATCH'):
        cli.apply_plan(db,contract,p,root/'not-created.db',root/'not-created.json',writers_stopped=True)
    assert not (root/'not-created.db').exists() and not (root/'not-created.json').exists()
    with closing(sqlite3.connect(db)) as c:
        assert c.execute('SELECT broker,market_value_cny FROM positions').fetchone()==(None,101)
