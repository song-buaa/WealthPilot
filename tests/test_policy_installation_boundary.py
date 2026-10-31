"""Code installation is not policy migration, publication or activation.

Only synthetic existing business files; no app/backend lifespan or providers.
"""
from contextlib import closing
import json
import sqlite3

import pytest

from app.shared_sqlite import schema_digest
from backend.services.investment_policy import runtime
from backend.services.investment_policy.store import PolicyStore
from backend.services.investment_policy.formal_publisher import PolicyPublisher
from backend.services.investment_policy import schema_rehearsal


@pytest.fixture
def legacy_business(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    database = tmp_path / 'synthetic-business.sqlite'
    with closing(sqlite3.connect(database)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript('CREATE TABLE portfolios(id INTEGER PRIMARY KEY); INSERT INTO portfolios VALUES(7); CREATE TABLE orders(id INTEGER PRIMARY KEY,payload TEXT); INSERT INTO orders VALUES(1,\'SYNTHETIC_UNCHANGED\');')
        fingerprint = schema_digest(db)
    database.chmod(0o600)
    info = database.stat()
    receipt = tmp_path / 'database-contract.json'
    receipt.write_text(json.dumps(dict(schema='wealthpilot.shared-sqlite.v1', mode='shared_wal',
        database_path=str(database), file_identity=[info.st_dev, info.st_ino],
        portfolio_id=7, schema_sha256=fingerprint)))
    receipt.chmod(0o600)
    for key in runtime.POLICY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('WEALTHPILOT_DB_PATH', str(database))
    monkeypatch.setenv('WEALTHPILOT_SQLITE_CONTRACT', str(receipt))
    return database, receipt, fingerprint


def test_installed_code_without_policy_configuration_never_opens_or_writes_store(legacy_business, monkeypatch):
    database, _, fingerprint = legacy_business
    def forbidden(*args, **kwargs):
        pytest.fail('Read-only LEGACY context attempted database, migration or Publisher action')
    # A deployed code package may import these modules, but ordinary reads must
    # not initialize schema or approve/release policy as an import side effect.
    with monkeypatch.context() as guard:
        guard.setattr(sqlite3, 'connect', forbidden)
        guard.setattr(PolicyStore, 'init_candidate', forbidden)
        guard.setattr(PolicyPublisher, 'approve', forbidden)
        guard.setattr(PolicyPublisher, 'release', forbidden)
        guard.setattr(schema_rehearsal, 'install_on_isolated_copy', forbidden)
        for consumer in ('web', 'backend', 'allocation', 'discipline', 'peer', 'mcp'):
            context = runtime.context(7, consumer=consumer)
            assert context['mode'] == 'LEGACY' and context['status'] == 'NOT_ACTIVE'
            assert context['policy_reference'] is None
    with closing(sqlite3.connect(database)) as db:
        assert schema_digest(db) == fingerprint
        assert db.execute('SELECT payload FROM orders').fetchall() == [('SYNTHETIC_UNCHANGED',)]


def test_premature_shared_policy_configuration_refuses_missing_schema_without_installing_it(legacy_business, monkeypatch):
    database, receipt, fingerprint = legacy_business
    monkeypatch.setenv('WEALTHPILOT_POLICY_DB_PATH', str(database))
    monkeypatch.setenv('WEALTHPILOT_POLICY_SCOPE', 'portfolio:7')
    monkeypatch.setenv('WEALTHPILOT_POLICY_SHARED_CONTRACT', str(receipt))
    result = runtime.resolve(7)
    assert result['mode'] == 'UNAVAILABLE' and result['policy_reference'] is None
    with closing(sqlite3.connect(database)) as db:
        assert schema_digest(db) == fingerprint
        assert db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'policy_%'").fetchall() == []
        assert db.execute('SELECT payload FROM orders').fetchall() == [('SYNTHETIC_UNCHANGED',)]


def test_missing_policy_file_is_unavailable_and_is_not_created(legacy_business, monkeypatch, tmp_path):
    missing = tmp_path / 'not-installed.sqlite'
    monkeypatch.setenv('WEALTHPILOT_POLICY_DB_PATH', str(missing))
    monkeypatch.setenv('WEALTHPILOT_POLICY_SCOPE', 'portfolio:7')
    assert runtime.resolve(7)['mode'] == 'UNAVAILABLE'
    assert not missing.exists()
