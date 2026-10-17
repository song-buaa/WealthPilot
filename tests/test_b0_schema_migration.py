"""Exact digest and in-place migration rehearsal on private synthetic SQLite."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest
from sqlalchemy import create_engine
from app.database import Base
from app.b0_schema import COLUMNS, plan as ddl_plan, verify as verify_shape
from app.shared_sqlite import schema_digest, verify
import importlib.util
_spec = importlib.util.spec_from_file_location("b0_migration_cli", Path(__file__).resolve().parents[1] / "scripts/migrate_b0_safety.py")
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)
make_plan, apply_plan = migration.make_plan, migration.apply_plan
write_private, resume_contract = migration.write_private, migration.resume_contract

from backend.services.action import models as action_models  # noqa: F401,E402
from app import models as business_models  # noqa: F401,E402


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    from services.broker_sync import models  # noqa: F401
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    db = root / "shared.db"
    engine = create_engine(f"sqlite:///{db}")
    Base.metadata.create_all(engine)
    engine.dispose()
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("DROP INDEX uq_order_records_submission_key")
        for table, columns in COLUMNS.items():
            for col in columns:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        conn.execute("INSERT INTO portfolios (id,name) VALUES (7,'Synthetic')")
        conn.commit()
        old = schema_digest(conn)
    os.chmod(db, 0o600)
    stat = db.stat()
    contract = root / "contract.json"
    write_private(contract, {"schema": "wealthpilot.shared-sqlite.v1", "mode": "shared_wal",
        "database_path": str(db), "file_identity": [stat.st_dev, stat.st_ino], "portfolio_id": 7,
        "schema_sha256": old, "immutable_reference": "retain-original-evidence"})
    return root, db, contract


def test_plan_apply_reverify_preserves_inode_backup_and_old_reference(legacy):
    root, db, contract = legacy
    before = db.stat().st_ino
    planned = make_plan(db, contract)
    assert len(planned["ddl"]) == 6
    assert planned["old_schema_sha256"] != planned["target_schema_sha256"]
    receipt = apply_plan(db, contract, planned, root / "backup.db", root / "receipt.json", writers_stopped=True)
    assert receipt["status"] == "VERIFIED" and not receipt["business_writes_reenabled"]
    assert db.stat().st_ino == before
    assert verify(db, contract, integrity=True)["immutable_reference"] == "retain-original-evidence"
    with closing(sqlite3.connect(db)) as conn:
        verify_shape(conn)
        assert ddl_plan(conn) == []
    assert (root / "backup.db.manifest.json").exists()
    assert make_plan(db, contract)["ddl"] == []


def test_refuses_unstopped_changed_digest_or_partial_schema(legacy):
    root, db, contract = legacy
    planned = make_plan(db, contract)
    with pytest.raises(ValueError, match="STOP_ALL"):
        apply_plan(db, contract, planned, root / "backup.db", root / "receipt.json")
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("ALTER TABLE order_records ADD COLUMN portfolio_id INTEGER")
        conn.commit()
    with pytest.raises(ValueError, match="SHARED_SCHEMA_MISMATCH"):
        make_plan(db, contract)
    # Even a manually changed contract cannot turn a partial migration into a new plan.
    data = json.loads(contract.read_text())
    with closing(sqlite3.connect(db)) as conn:
        data["schema_sha256"] = schema_digest(conn)
    write_private(contract, data)
    with pytest.raises(ValueError, match="PARTIAL_B0"):
        make_plan(db, contract)


def test_contract_recovery_only_accepts_exact_rehearsed_target(legacy, monkeypatch):
    root, db, contract = legacy
    planned = make_plan(db, contract)
    original = migration.write_private
    def crash_after_ddl(path, value):
        if Path(path) == contract:
            raise OSError("simulated crash after DDL before contract replace")
        original(path, value)
    monkeypatch.setattr(migration, "write_private", crash_after_ddl)
    with pytest.raises(OSError):
        apply_plan(db, contract, planned, root / "backup.db", root / "receipt.json", writers_stopped=True)
    with pytest.raises(ValueError, match="SHARED_SCHEMA_MISMATCH"):
        verify(db, contract)
    monkeypatch.setattr(migration, "write_private", original)
    resume_contract(db, contract, root / "receipt.json", writers_stopped=True)
    verify(db, contract, integrity=True)
    receipt = json.loads((root / "receipt.json").read_text())
    receipt["business_writes_reenabled"] = True
    original(root / "receipt.json", receipt)
    with pytest.raises(ValueError, match="STOPPED_UNREOPENED"):
        resume_contract(db, contract, root / "receipt.json", writers_stopped=True)


def test_existing_index_wrong_definition_is_not_silently_accepted(legacy):
    _, db, _ = legacy
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("DROP INDEX uq_order_records_batch_submission")
        conn.execute("CREATE INDEX uq_order_records_batch_submission ON order_records(batch_id)")
        with pytest.raises(ValueError, match="INDEX_CONFLICT"):
            ddl_plan(conn)


def test_lse_cas_correction_preserves_id_quantity_cost_and_historical_evidence(legacy):
    root, db, contract = legacy
    from sqlalchemy.orm import Session
    from app.models import Position
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        row = Position(portfolio_id=7, broker="snowball", broker_account_id="synthetic-account", sync_source="api",
            asset_class="固收", name="User protected name", segment="投资", platform="雪盈证券", ticker="CSBGU3", symbol="CSBGU3:US",
            currency="USD", quantity=17, cost_price=125.4, current_price=126,
            classification_evidence_json=json.dumps({"con_id": 79000224, "isin": "IE00B3VWN179"}))
        session.add(row); session.commit(); row_id = row.id
    engine.dispose()
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        original = dict(conn.execute("SELECT * FROM positions WHERE id=?", (row_id,)).fetchone())
    correction = {"position_id": row_id, "expected_fingerprint": migration.row_fingerprint(original)}
    planned = make_plan(db, contract, [correction])
    apply_plan(db, contract, planned, root / "backup.db", root / "receipt.json", writers_stopped=True)
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        after = dict(conn.execute("SELECT * FROM positions WHERE id=?", (row_id,)).fetchone())
    assert after == {**original, "symbol": "CBU3:LSE", "ticker": "CBU3"}


def test_lse_correction_rejects_unproven_owner_or_collision(legacy):
    _, db, contract = legacy
    from sqlalchemy.orm import Session
    from app.models import Position
    engine = create_engine(f"sqlite:///{db}")
    with Session(engine) as session:
        row = Position(portfolio_id=7, broker="snowball", broker_account_id="synthetic-account", sync_source="api",
            asset_class="固收", name="Unproven", platform="雪盈证券", ticker="IB01", symbol="IB01:US", currency="USD")
        session.add(row); session.commit(); row_id = row.id
    engine.dispose()
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM positions WHERE id=?", (row_id,)).fetchone()
        fingerprint = migration.row_fingerprint(row)
    with pytest.raises(ValueError, match="LISTING_UNPROVEN"):
        make_plan(db, contract, [{"position_id": row_id, "expected_fingerprint": fingerprint}])
    with Session(engine) as session:
        row = session.get(Position, row_id)
        row.classification_evidence_json = json.dumps({"con_id": 354802220})
        session.add(Position(portfolio_id=7, broker="snowball", broker_account_id="synthetic-account", sync_source="api",
            name="Collision", asset_class="固收", platform="雪盈证券", ticker="IB01", symbol="IB01:LSE", currency="USD"))
        session.commit()
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        fingerprint = migration.row_fingerprint(conn.execute("SELECT * FROM positions WHERE id=?", (row_id,)).fetchone())
    with pytest.raises(ValueError, match="COLLISION"):
        make_plan(db, contract, [{"position_id": row_id, "expected_fingerprint": fingerprint}])


def test_prewrite_restore_preserves_inode_and_refuses_after_write_gate(legacy):
    root, db, contract = legacy
    old_inode = db.stat().st_ino
    planned = make_plan(db, contract)
    apply_plan(db, contract, planned, root / "old.db", root / "receipt.json", writers_stopped=True)
    migration.restore_prewrite(db, contract, root / "receipt.json", root / "preserved.db", writers_stopped=True)
    assert db.stat().st_ino == old_inode
    assert verify(db, contract)["schema_sha256"] == planned["old_schema_sha256"]
    receipt = json.loads((root / "receipt.json").read_text())
    receipt["business_writes_reenabled"] = True
    write_private(root / "receipt.json", receipt)
    with pytest.raises(ValueError, match="FORBIDDEN_AFTER_WRITER"):
        migration.restore_prewrite(db, contract, root / "receipt.json", root / "never.db", writers_stopped=True)
