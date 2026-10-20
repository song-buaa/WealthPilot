"""Original B0 receipt pipeline: U2 -> B2 plan/clone/apply/recover/rollback."""
from contextlib import closing
import sqlite3
import pytest
from app.shared_sqlite import schema_digest,verify
from tests.test_u2_schema import deployed_u1  # noqa: F401
from tests.test_u1_schema import deployed_u0  # noqa: F401

@pytest.fixture
def deployed_u2(deployed_u1):
    cli,root,db,contract=deployed_u1
    cli.apply_plan(db,contract,cli.make_plan(db,contract),root/'u2-backup.db',root/'u2-receipt.json',writers_stopped=True)
    with closing(sqlite3.connect(db)) as connection:
        connection.execute('DROP TABLE IF EXISTS portfolio_history_events')
        connection.execute('DROP TABLE IF EXISTS portfolio_history_imports');connection.commit()
        value=cli.private_read(contract);value['schema_sha256']=schema_digest(connection);cli.write_private(contract,value)
    cli.select_stage('b2')
    return cli,root,db,contract

def test_exact_two_tables_two_unique_indexes_idempotent_digest_preserved_receipts(deployed_u2):
    cli,root,db,contract=deployed_u2;inode=db.stat().st_ino
    plan=cli.make_plan(db,contract)
    assert len(plan['ddl'])==4 and sum('CREATE TABLE' in s for s in plan['ddl'])==2
    assert sum('CREATE UNIQUE INDEX' in s for s in plan['ddl'])==2
    assert not any('ALTER' in s or 'UPDATE' in s or 'DELETE' in s for s in plan['ddl'])
    assert plan['corrections']==[]
    cli.apply_plan(db,contract,plan,root/'b2-backup.db',root/'b2-receipt.json',writers_stopped=True)
    current=verify(db,contract,integrity=True)
    assert db.stat().st_ino==inode and current['u2_migration_receipt'].endswith('u2-receipt.json')
    assert current['b2_migration_receipt'].endswith('b2-receipt.json')
    assert cli.make_plan(db,contract)['ddl']==[]
    cli.restore_prewrite(db,contract,root/'b2-receipt.json',root/'preserved.db',writers_stopped=True)
    assert len(cli.make_plan(db,contract)['ddl'])==4

def test_b2_receipt_recovery_and_no_restore_after_new_facts(deployed_u2,monkeypatch):
    cli,root,db,contract=deployed_u2;plan=cli.make_plan(db,contract);original=cli.write_private
    def crash(path,value):
        if path==contract:raise OSError('synthetic interrupted contract replace')
        original(path,value)
    monkeypatch.setattr(cli,'write_private',crash)
    with pytest.raises(OSError):cli.apply_plan(db,contract,plan,root/'b2-backup.db',root/'b2-receipt.json',writers_stopped=True)
    monkeypatch.setattr(cli,'write_private',original)
    cli.resume_contract(db,contract,root/'b2-receipt.json',writers_stopped=True)
    with closing(sqlite3.connect(db)) as c:
        c.execute("INSERT INTO portfolio_history_imports VALUES('synthetic',7,'ibkr','fixture','ibkr_flex','abc','now','2024','2025','{}')");c.commit()
    with pytest.raises(ValueError,match='BUSINESS_WRITES'):
        cli.restore_prewrite(db,contract,root/'b2-receipt.json',root/'never.db',writers_stopped=True)

def test_no_corrections_partial_tables_or_wrong_index(deployed_u2):
    cli,root,db,contract=deployed_u2
    with pytest.raises(ValueError,match='NO_DATA_CORRECTIONS'):cli.make_plan(db,contract,[dict(kind='cash_owner')])
    with closing(sqlite3.connect(db)) as c:
        c.execute(cli.make_plan(db,contract)['ddl'][0]);c.commit()
        value=cli.private_read(contract);value['schema_sha256']=schema_digest(c);cli.write_private(contract,value)
    with pytest.raises(ValueError,match='PARTIAL'):cli.make_plan(db,contract)


def test_wrong_unique_index_fails_closed(deployed_u2):
    from app.b2_schema import plan as shape_plan
    cli,root,db,contract=deployed_u2
    cli.apply_plan(db,contract,cli.make_plan(db,contract),root/'b2-backup.db',root/'b2-receipt.json',writers_stopped=True)
    with closing(sqlite3.connect(db)) as c:
        c.execute('DROP INDEX uq_history_event')
        c.execute('CREATE INDEX uq_history_event ON portfolio_history_events(broker,account_id,kind,external_id)');c.commit()
        with pytest.raises(ValueError,match='INDEX_CONFLICT'):shape_plan(c)


def test_standalone_migration_cli_resolves_existing_portfolio_fk(deployed_u2):
    import os,sys,subprocess,json
    from pathlib import Path
    cli,root,db,contract=deployed_u2
    target=root/'standalone-plan.json'
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR','PYTHON_DOTENV_DISABLED') if k in os.environ}
    env['PYTHON_DOTENV_DISABLED']='1'
    command=[sys.executable,str(Path(__file__).resolve().parents[1]/'scripts/migrate_b0_safety.py'),
        'plan','--stage','b2','--database',str(db),'--contract',str(contract),'--plan',str(target)]
    result=subprocess.run(command,env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert len(json.loads(target.read_text())['ddl'])==4
