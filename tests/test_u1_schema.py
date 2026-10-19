"""Explicit U0->U1 digest, clone, receipt and no-loss rollback rehearsal."""
from contextlib import closing
import importlib.util
from pathlib import Path
import sqlite3
import pytest
from sqlalchemy import create_engine
from app.database import Base
from app.u1_schema import plan, verify as verify_u1
from app.shared_sqlite import schema_digest, verify
import app.models
import backend.services.action.models


@pytest.fixture
def deployed_u0(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'backend'))
    import services.broker_sync.models  # noqa: F401
    spec=importlib.util.spec_from_file_location('u1_migration_fixture',Path(__file__).resolve().parents[1]/'scripts/migrate_b0_safety.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli);cli.select_stage('u1')
    root=tmp_path/'private';root.mkdir(mode=0o700);db=root/'synthetic.db'
    engine=create_engine('sqlite:///'+str(db));Base.metadata.create_all(engine);engine.dispose()
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('DROP INDEX uq_asset_analysis_request')
        conn.execute('ALTER TABLE wealth_snapshots DROP COLUMN payload_json')
        conn.execute('ALTER TABLE wealth_snapshots DROP COLUMN analysis_request_id')
        conn.execute("INSERT INTO portfolios(id,name) VALUES(7,'synthetic')");conn.commit()
        digest=schema_digest(conn)
    db.chmod(0o600);info=db.stat();contract=root/'contract.json'
    cli.write_private(contract,dict(schema='wealthpilot.shared-sqlite.v1',mode='shared_wal',database_path=str(db),
        file_identity=[info.st_dev,info.st_ino],portfolio_id=7,schema_sha256=digest,
        b0_migration_receipt='b0-history',u0_migration_receipt='u0-history'))
    return cli,root,db,contract


def test_u1_exact_two_nullable_columns_one_index_no_tables(deployed_u0):
    cli,root,db,contract=deployed_u0;inode=db.stat().st_ino
    before=cli.make_plan(db,contract)
    assert len(before['ddl'])==3 and sum('ADD COLUMN' in d for d in before['ddl'])==2
    assert not any('CREATE TABLE' in d for d in before['ddl']) and before['corrections']==[]
    cli.apply_plan(db,contract,before,root/'backup.db',root/'receipt.json',writers_stopped=True)
    current=verify(db,contract)
    assert current['b0_migration_receipt']=='b0-history' and current['u0_migration_receipt']=='u0-history'
    assert db.stat().st_ino==inode and cli.make_plan(db,contract)['ddl']==[]
    with closing(sqlite3.connect(db)) as conn:verify_u1(conn)
    cli.restore_prewrite(db,contract,root/'receipt.json',root/'preserved.db',writers_stopped=True)
    assert db.stat().st_ino==inode and len(cli.make_plan(db,contract)['ddl'])==3


def test_u1_rejects_data_corrections_partial_ddl_and_wrong_index(deployed_u0):
    cli,root,db,contract=deployed_u0
    with pytest.raises(ValueError,match='NO_DATA_CORRECTIONS'):cli.make_plan(db,contract,[{'kind':'cash_owner'}])
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('ALTER TABLE wealth_snapshots ADD COLUMN payload_json TEXT');conn.commit()
    # exact shared digest refuses ad hoc DDL before any migration apply
    with pytest.raises(ValueError,match='SCHEMA_MISMATCH'):cli.make_plan(db,contract)


def test_u1_interrupted_contract_and_new_fact_restore_forbidden(deployed_u0,monkeypatch):
    cli,root,db,contract=deployed_u0;p=cli.make_plan(db,contract);original=cli.write_private
    def crash(path,value):
        if Path(path)==contract:raise OSError('synthetic crash')
        return original(path,value)
    monkeypatch.setattr(cli,'write_private',crash)
    with pytest.raises(OSError):cli.apply_plan(db,contract,p,root/'backup.db',root/'receipt.json',writers_stopped=True)
    monkeypatch.setattr(cli,'write_private',original)
    cli.resume_contract(db,contract,root/'receipt.json',writers_stopped=True)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE portfolios SET name='new fact' WHERE id=7");conn.commit()
    with pytest.raises(ValueError,match='BUSINESS_WRITES'):cli.restore_prewrite(db,contract,root/'receipt.json',root/'never.db',writers_stopped=True)
    assert verify(db,contract)
