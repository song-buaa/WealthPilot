"""All databases here are disposable fixtures, never the personal wealth DB."""
from datetime import date, datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reader import read_overview, readonly_snapshot, service_sessions, ReadError
from app.database import Base
from app.models import Portfolio, Position, WealthItem
from backend.services import wealth_service, portfolio_service


@pytest.fixture
def fixture_db(tmp_path, monkeypatch):
    monkeypatch.setenv('PUBLIC_DEMO_MODE', 'true')
    monkeypatch.setenv('DEMO_ACCESS_PASSWORD', 'isolated-poc02-fixture')
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    # Never call Tiger or any other external source in automated fixture tests.
    monkeypatch.setattr(portfolio_service, '_get_tiger_account_cash', lambda: (0.0, []))
    path = tmp_path / 'fixture.db'
    engine = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Portfolio(id=1, name='fixture'))
        session.add(Position(portfolio_id=1, symbol='FIXTURE:TEST', name='fixture',
            platform='fixture', asset_class='货币', segment='投资', market_value_cny=1000))
        for kind, type_, value, included, linked in (
            ('ASSET', 'housing_fund', 200, True, False),
            ('ASSET', 'personal_pension', 300, False, True),
            ('LIABILITY', 'credit_card', 40, True, False),
        ):
            session.add(WealthItem(portfolio_id=1, kind=kind, name='PRIVATE_FIXTURE_NAME',
                item_type=type_, category='fixture', current_value=value,
                included_in_net_worth=included, already_investment_accounted=linked,
                value_as_of=date(2026, 10, 11)))
        session.commit()
    engine.dispose()
    return path


def test_existing_wealth_scope_and_no_write(fixture_db):
    before = hashlib.sha256(fixture_db.read_bytes()).hexdigest()
    result = read_overview(fixture_db, 1, now=datetime(2026, 10, 11, tzinfo=timezone.utc))
    assert (result['total_assets_minor'], result['total_liabilities_minor'], result['net_worth_minor']) == (90000, 4000, 86000)
    assert result['synthetic'] is False
    assert result['data_updated_date'] is None  # legacy position has no valuation time
    assert 'VALUATION_DATE_UNKNOWN' in result['issues']
    assert result['sources']['tiger_cash'] == 'not_configured'
    assert 'PRIVATE_FIXTURE_NAME' not in str(result)
    assert 'FIXTURE:TEST' not in str(result)
    assert hashlib.sha256(fixture_db.read_bytes()).hexdigest() == before
    with sqlite3.connect(fixture_db) as connection:
        assert connection.execute('SELECT count(*) FROM wealth_snapshots').fetchone()[0] == 0


def test_same_three_totals_as_web_service(fixture_db):
    result = read_overview(fixture_db, 1)
    # Existing GET service writes a snapshot; run it only on the disposable fixture.
    engine = create_engine(f'sqlite:///{fixture_db}')
    with engine.connect() as connection, service_sessions(connection):
        summary = wealth_service.get_summary(1)
    for field in ('total_assets', 'total_liabilities', 'net_worth'):
        assert result[field + '_minor'] == round(summary[field] * 100)
    engine.dispose()


@pytest.mark.parametrize('sql', [
    'UPDATE portfolios SET name="changed"', 'DELETE FROM positions',
    'INSERT INTO portfolios(name) VALUES("changed")', 'CREATE TABLE injected(id int)',
    'PRAGMA query_only=OFF', 'ATTACH DATABASE ":memory:" AS injected',
])
def test_database_enforces_readonly(fixture_db, sql):
    before = fixture_db.read_bytes()
    with readonly_snapshot(fixture_db) as connection:
        with pytest.raises(Exception):
            connection.execute(text(sql))
    assert fixture_db.read_bytes() == before


def test_missing_portfolio_fails_closed(fixture_db):
    with pytest.raises(ReadError, match='PORTFOLIO_NOT_FOUND'):
        read_overview(fixture_db, 98765)


def test_missing_database_never_created(tmp_path):
    path = tmp_path / 'missing.db'
    with pytest.raises(FileNotFoundError):
        read_overview(path, 1)
    assert not path.exists()


def test_stale_manual_values_are_explicit(fixture_db):
    result = read_overview(fixture_db, 1, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert result['status'] == 'partial'
    assert 'STALE_COMPONENTS' in result['issues']


def test_source_failure_does_not_claim_zero_cash_is_complete(fixture_db, monkeypatch):
    def failed_cash():
        logging.getLogger('backend.services.portfolio_service').warning('PRIVATE_ACCOUNT_FIXTURE')
        return 0.0, []
    monkeypatch.setattr(portfolio_service, '_get_tiger_account_cash', failed_cash)
    result = read_overview(fixture_db, 1, cash_configured=True)
    assert result['status'] == 'unavailable'
    assert result['total_assets_minor'] is None
    assert 'TIGER_CASH_SOURCE_FAILED' in result['issues']
    assert 'PRIVATE_ACCOUNT_FIXTURE' not in str(result)


def test_zero_cash_and_missing_cash_are_not_conflated(fixture_db):
    result = read_overview(fixture_db, 1, cash_configured=True)
    assert 'TIGER_CASH_ZERO_OR_SOURCE_EMPTY' in result['issues']


def test_service_bindings_restored(fixture_db):
    original = wealth_service.get_session
    read_overview(fixture_db, 1)
    assert wealth_service.get_session is original


def test_multiple_service_sessions_share_one_snapshot(fixture_db):
    # WAL enables a concurrent fixture writer. The query sees its initial
    # snapshot even after another connection commits between service reads.
    with sqlite3.connect(fixture_db) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
    with readonly_snapshot(fixture_db) as connection, service_sessions(connection) as wealth:
        first = wealth._current_totals(1)
        with sqlite3.connect(fixture_db) as writer:
            writer.execute('UPDATE positions SET market_value_cny=2000')
        second = wealth._current_totals(1)
        assert first['total_assets'] == second['total_assets'] == 900
    assert read_overview(fixture_db, 1)['total_assets_minor'] == 190000


def test_latest_failed_sync_and_old_success_remain_visible(fixture_db):
    with sqlite3.connect(fixture_db) as writer:
        writer.execute('UPDATE positions SET broker="fixture", broker_account_id="PRIVATE_OWNER", sync_source="api"')
        writer.execute('CREATE TABLE IF NOT EXISTS position_snapshot_runs(portfolio_id INTEGER, broker TEXT, account_id TEXT, status TEXT, finished_at TEXT, started_at TEXT, triggered_by TEXT, retry_count INTEGER, sync_source TEXT)')
        writer.executemany("INSERT INTO position_snapshot_runs(portfolio_id,broker,account_id,status,finished_at,started_at,triggered_by,retry_count,sync_source) VALUES(1,?,?,?,?,?,'manual',0,'api')", [
            ('fixture','PRIVATE_OWNER','success','2026-10-08 12:00:00','2026-10-08 11:00:00'),
            ('fixture','PRIVATE_OWNER','failed',None,'2026-10-11 11:00:00'),
        ])
    result = read_overview(fixture_db, 1, now=datetime(2026,10,11,tzinfo=timezone.utc))
    assert 'LATEST_POSITION_SYNC_FAILED' in result['issues']
    assert 'STALE_COMPONENTS' in result['issues']
    assert result['oldest_component_date'] == '2026-10-08'
    assert 'PRIVATE_OWNER' not in str(result)


def test_worker_contract_and_redacted_failure(fixture_db, tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'database_path': str(fixture_db), 'portfolio_id': 1}))
    config.chmod(0o600)
    command = [sys.executable, str(Path(__file__).with_name('worker.py')), '--config', str(config)]
    environment = {k: os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR') if k in os.environ}
    before = fixture_db.read_bytes()
    completed = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15, check=True)
    result = json.loads(completed.stdout)
    assert result['ok'] and result['result']['net_worth_minor'] == 86000
    assert fixture_db.read_bytes() == before
    assert completed.stderr == ''
    config.write_text(json.dumps({'database_path': 'PRIVATE_MISSING_PATH', 'portfolio_id': 1}))
    completed = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=15, check=True)
    assert json.loads(completed.stdout) == {'ok': False, 'error': 'WEALTH_READ_UNAVAILABLE'}
    assert 'PRIVATE_MISSING_PATH' not in completed.stdout + completed.stderr
