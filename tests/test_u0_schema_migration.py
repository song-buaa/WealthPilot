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
