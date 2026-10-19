"""Explicit U1 -> U2, original receipt/digest and rollback boundaries."""
import sqlite3
from contextlib import closing
import pytest
from tests.test_u1_schema import deployed_u0  # noqa: F401
from app.shared_sqlite import schema_digest,verify


@pytest.fixture
def deployed_u1(deployed_u0):
    cli,root,db,contract=deployed_u0
    # The current ORM includes U2; reconstruct the real pre-U2 shape.
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('ALTER TABLE order_records DROP COLUMN fill_sync_json');conn.commit()
        old=cli.private_read(contract);old['schema_sha256']=schema_digest(conn);cli.write_private(contract,old)
    plan=cli.make_plan(db,contract)
    cli.apply_plan(db,contract,plan,root/'u1-backup.db',root/'u1-receipt.json',writers_stopped=True)
    cli.select_stage('u2')
    return cli,root,db,contract


def test_u2_exact_one_nullable_column_preserves_receipts_and_indexes(deployed_u1):
    cli,root,db,contract=deployed_u1;inode=db.stat().st_ino
    before=cli.make_plan(db,contract)
    assert before['ddl']==['ALTER TABLE order_records ADD COLUMN fill_sync_json TEXT']
    assert before['corrections']==[]
    cli.apply_plan(db,contract,before,root/'u2-backup.db',root/'u2-receipt.json',writers_stopped=True)
    checked=verify(db,contract)
    assert checked['b0_migration_receipt']=='b0-history' and checked['u0_migration_receipt']=='u0-history'
    assert checked['u1_migration_receipt'].endswith('u1-receipt.json')
    assert checked['u2_migration_receipt'].endswith('u2-receipt.json')
    assert db.stat().st_ino==inode and cli.make_plan(db,contract)['ddl']==[]
    cli.restore_prewrite(db,contract,root/'u2-receipt.json',root/'preserved.db',writers_stopped=True)
    assert cli.make_plan(db,contract)['ddl']==before['ddl']


def test_u2_no_corrections_or_restore_after_new_facts(deployed_u1):
    cli,root,db,contract=deployed_u1
    with pytest.raises(ValueError,match='U2_HAS_NO_DATA_CORRECTIONS'):cli.make_plan(db,contract,[{'kind':'cash_owner'}])
    plan=cli.make_plan(db,contract)
    cli.apply_plan(db,contract,plan,root/'u2-backup.db',root/'u2-receipt.json',writers_stopped=True)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE portfolios SET name='new fact' WHERE id=7");conn.commit()
    with pytest.raises(ValueError,match='BUSINESS_WRITES'):
        cli.restore_prewrite(db,contract,root/'u2-receipt.json',root/'denied.db',writers_stopped=True)
