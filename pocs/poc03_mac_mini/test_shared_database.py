"""Batch 1: real services, synthetic data, no device or external-provider access."""
from contextlib import closing
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "poc02_wealth_mcp"))
import common
import deploy
import runner
import shared_database as cutover
import snapshot
from app import database
from app import shared_sqlite
from app.models import Base, Portfolio, Position, WealthItem
from backend.services import portfolio_service, wealth_service
from reader import read_overview, readonly_snapshot


@pytest.fixture
def fixed(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("PUBLIC_DEMO_MODE", "true")
    monkeypatch.setattr(portfolio_service, "_get_tiger_account_cash", lambda: (0.0, []))
    root = deploy.layout(tmp_path.resolve() / "fixture prod")
    db = root / "data/wealthpilot.db"
    engine = create_engine("sqlite:///" + str(db))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Portfolio(id=7, name="synthetic"))
        session.add(Position(portfolio_id=7, name="synthetic", platform="fixture",
                             segment="投资", asset_class="货币", market_value_cny=1000))
        session.add(WealthItem(portfolio_id=7, kind="LIABILITY", name="synthetic",
                              item_type="credit_card", category="credit_card", current_value=100,
                              included_in_net_worth=True, value_as_of=date(2026, 10, 14)))
        session.commit()
    engine.dispose()
    db.chmod(0o400)
    reference = {"database_sha256": common.digest(db), "portfolio_id": 7,
                 "cash_reference": {"configured": False}}
    common.write_json(root / "data/migration-reference.json", reference)
    common.write_json(root / "config/wealth.json", {"database_path": str(db), "portfolio_id": 7})
    common.write_json(root / "config/runtime.json", {"revision": "a" * 40, "mode": "real",
                      "user": "fixture", "user_home": str(root), "expected_hostname": "fixture"})
    common.write_json(root / "config/access-evidence.json", {k: True for k in runner.PROOF_FIELDS})
    release = root / "releases" / ("a" * 40)
    release.mkdir(mode=0o700)
    common.write_json(release / "installed.json", {})
    deploy.select_release(root, "a" * 40)
    monkeypatch.setattr(runner, "host_guard", lambda _: None)
    monkeypatch.setattr(runner, "check_revision", lambda *a: None)
    return root, db


@pytest.fixture
def shared(fixed, monkeypatch):
    root, db = fixed
    cutover.prepare_shared(root, services_stopped=True, apply=True)
    contract = root / "config/database-contract.json"
    monkeypatch.setenv("WEALTHPILOT_SQLITE_CONTRACT", str(contract))
    monkeypatch.setattr(database, "DB_PATH", str(db))
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    yield root, db, contract
    if database._engine is not None:
        database._engine.dispose()


def overview(shared, **kwargs):
    _, db, contract = shared
    return read_overview(db, 7, database_contract=contract, **kwargs)


def test_dry_run_changes_no_file_or_mode(fixed):
    root, db = fixed
    before = {p: (p.read_bytes(), p.stat().st_mode) for p in root.rglob("*") if p.is_file()}
    result = cutover.prepare_shared(root)
    assert not result["apply"]
    assert before == {p: (p.read_bytes(), p.stat().st_mode) for p in root.rglob("*") if p.is_file()}
    assert db.stat().st_mode & 0o777 == 0o400


def test_cutover_requires_stopped_services(fixed):
    root, db = fixed
    with pytest.raises(ValueError, match="STOP_SERVICES"):
        cutover.prepare_shared(root, apply=True)
    assert db.stat().st_mode & 0o777 == 0o400
    assert not list((root / "backups").iterdir())


def test_cutover_retains_history_and_verified_backup(shared):
    root, db, contract = shared
    reference = common.private_json(root / "data/migration-reference.json")
    saved = next((root / "backups").glob("pre-shared-*"))
    manifest = common.private_json(saved / "wealthpilot.db.manifest.json")
    snapshot.verify(saved / "wealthpilot.db", manifest)
    assert common.digest(saved / "fixed-snapshot.db") == reference["database_sha256"]
    snapshot.verify(saved / "fixed-snapshot.db", common.private_json(saved / "fixed-snapshot.db.manifest.json"))
    assert common.private_json(saved / "runtime.json").get("database_mode") is None
    assert db.stat().st_mode & 0o777 == 0o600
    assert shared_sqlite.verify(db, contract, integrity=True)["portfolio_id"] == 7
    assert common.private_json(root / "config/runtime.json")["database_mode"] == "shared_wal"


def test_commits_visible_but_query_has_one_snapshot(shared):
    _, db, contract = shared
    with readonly_snapshot(db, contract) as conn:
        before = conn.execute(text("SELECT market_value_cny FROM positions")).scalar()
        with database.get_engine().begin() as writer:
            writer.execute(text("UPDATE positions SET market_value_cny=2000"))
        assert conn.execute(text("SELECT market_value_cny FROM positions")).scalar() == before == 1000
    assert overview(shared)["total_assets_minor"] == 200000


def test_entire_service_calculation_uses_single_snapshot(shared, monkeypatch):
    original = wealth_service._current_totals
    def concurrent_update(pid):
        # read_overview has already read freshness/positions in the snapshot.
        with database.get_engine().begin() as conn:
            conn.execute(text("UPDATE positions SET market_value_cny=3333"))
        return original(pid)
    monkeypatch.setattr(wealth_service, "_current_totals", concurrent_update)
    assert overview(shared)["total_assets_minor"] == 100000
    monkeypatch.setattr(wealth_service, "_current_totals", original)
    assert overview(shared)["total_assets_minor"] == 333300


def test_web_summary_snapshot_is_committed_and_mcp_remains_readonly(shared):
    result = wealth_service.get_summary(7)
    with database.get_engine().connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM wealth_snapshots")).scalar()
    assert count == 1
    data = overview(shared)
    for field in ("total_assets", "total_liabilities", "net_worth"):
        assert data[field + "_minor"] == round(result[field] * 100)
    with database.get_engine().connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM wealth_snapshots")).scalar() == count


@pytest.mark.parametrize("sql", [
    "UPDATE portfolios SET name='forbidden'", "DELETE FROM positions",
    "INSERT INTO portfolios(id,name) VALUES(8,'forbidden')", "CREATE TABLE forbidden(id)",
    "PRAGMA query_only=OFF", "PRAGMA journal_mode=DELETE", "PRAGMA wal_checkpoint(TRUNCATE)",
    "ATTACH DATABASE ':memory:' AS forbidden",
])
def test_mcp_denies_writes_on_owner_writable_database(shared, sql):
    _, db, contract = shared
    with readonly_snapshot(db, contract) as conn:
        with pytest.raises(Exception):
            conn.execute(text(sql))
    assert overview(shared)["net_worth_minor"] == 90000


def test_uncommitted_and_rolled_back_changes_invisible(shared):
    with database.get_engine().connect() as writer:
        writer.execute(text("UPDATE positions SET market_value_cny=9000"))
        assert overview(shared)["total_assets_minor"] == 100000
        writer.rollback()
    assert overview(shared)["total_assets_minor"] == 100000


def test_writer_busy_timeout_serializes_without_lost_commit(shared):
    engine = database.get_engine()
    with engine.connect() as first:
        first.execute(text("UPDATE positions SET market_value_cny=market_value_cny+1"))
        started, acquired = threading.Event(), threading.Event()
        errors = []
        def second_writer():
            try:
                with engine.begin() as second:
                    assert second.execute(text("PRAGMA busy_timeout")).scalar() == 5000
                    started.set()
                    second.execute(text("UPDATE positions SET market_value_cny=market_value_cny+2"))
                    acquired.set()
            except Exception as error:
                errors.append(error)
                started.set()
        thread = threading.Thread(target=second_writer)
        thread.start()
        assert started.wait(3)
        assert not acquired.wait(0.1)
        first.commit()
        thread.join(7)
        assert not thread.is_alive() and errors == [] and acquired.is_set()
    assert overview(shared)["total_assets_minor"] == 100300


def test_restart_after_legitimate_update_keeps_gates(shared):
    root, db, _ = shared
    with database.get_engine().begin() as conn:
        conn.execute(text("UPDATE positions SET market_value_cny=2000"))
    # Flush the change to the main file: a changed .db digest must be accepted.
    with closing(sqlite3.connect(db)) as writer:
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert common.digest(db) != common.private_json(root / "data/migration-reference.json")["database_sha256"]
    command, config = runner.child_command(root, "mcp")
    assert "--real-config" in command and config["database_mode"] == "shared_wal"
    proof = common.private_json(root / "config/access-evidence.json")
    proof["anonymous_denied"] = False
    common.write_json(root / "config/access-evidence.json", proof)
    with pytest.raises(ValueError, match="REAL_ACCESS_NOT_VERIFIED"):
        runner.child_command(root, "mcp")


def test_fixed_mode_still_rejects_updates(fixed):
    root, db = fixed
    runner.child_command(root, "mcp")
    db.chmod(0o600)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE positions SET market_value_cny=2000"); conn.commit()
    with pytest.raises(ValueError, match="MIGRATION_DATABASE_DIGEST_MISMATCH"):
        runner.child_command(root, "mcp")


@pytest.mark.parametrize("change,expected", [
    ("replace", "IDENTITY_MISMATCH"), ("permissions", "PRIVATE_OWNER_REQUIRED"),
    ("symlink", "SYMLINK_REFUSED"), ("schema", "SCHEMA_MISMATCH"),
    ("portfolio", "PORTFOLIO_MISSING"), ("journal", "WAL_REQUIRED"),
    ("corrupt", None),
])
def test_invalid_shared_state_fails_closed(shared, change, expected):
    root, db, contract = shared
    if change == "replace":
        replacement = db.with_name("replacement.db")
        snapshot.backup(db, replacement)
        os.replace(replacement, db)
    elif change == "permissions":
        db.chmod(0o644)
    elif change == "symlink":
        original = db.with_name("preserved.db"); db.rename(original); db.symlink_to(original)
    elif change == "corrupt":
        db.write_bytes(b"synthetic corruption")
    else:
        with closing(sqlite3.connect(db)) as conn:
            conn.execute({"schema": "CREATE TABLE unexpected(id)", "portfolio": "DELETE FROM portfolios",
                          "journal": "PRAGMA journal_mode=DELETE"}[change]); conn.commit()
    with pytest.raises(Exception, match=expected):
        runner.child_command(root, "mcp")
    with pytest.raises(Exception, match=expected):
        overview(shared)


def test_writer_refuses_missing_database_without_creating_it(shared):
    _, db, contract = shared
    db.rename(db.with_suffix(".preserved"))
    with pytest.raises(FileNotFoundError):
        shared_sqlite.writer_connection(db, contract)
    assert not db.exists()


def test_wal_backup_restore_and_explicit_rebind_preserve_runtime_amounts(shared):
    root, db, contract = shared
    reference_before = (root / "data/migration-reference.json").read_bytes()
    writer = database.get_engine().connect()
    try:
        writer.execute(text("UPDATE positions SET market_value_cny=2222")); writer.commit()
        assert Path(str(db) + "-wal").is_file()
        expected = overview(shared)
        saved = root / "backups/runtime.db"
        manifest = snapshot.backup(db, saved)
        snapshot.verify(saved, manifest)
    finally:
        writer.close(); database.get_engine().dispose()
    # All clients stopped; SQLite, rather than unlink(), closes/checkpoints WAL.
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        assert conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete"
    snapshot.restore(saved, db, manifest, True, root / "backups")
    with pytest.raises(ValueError):
        runner.child_command(root, "mcp")
    cutover.prepare_shared(root, services_stopped=True, apply=True,
                           restore_manifest=str(saved) + ".manifest.json")
    assert (root / "data/migration-reference.json").read_bytes() == reference_before
    actual = overview(shared)
    assert all(actual[k] == expected[k] for k in ("total_assets_minor", "total_liabilities_minor", "net_worth_minor"))
    runner.child_command(root, "mcp")


def test_independent_worker_reopens_current_committed_data(shared):
    root, _, _ = shared
    command = [sys.executable, str(Path(__file__).parents[1] / "poc02_wealth_mcp/worker.py"),
               "--config", str(root / "config/wealth.json")]
    environment = {"PATH": os.defpath, "PYTHON_DOTENV_DISABLED": "1"}
    def read():
        completed = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=20, check=True)
        assert completed.stderr == ""
        return json.loads(completed.stdout)
    assert read()["result"]["total_assets_minor"] == 100000
    with database.get_engine().begin() as conn:
        conn.execute(text("UPDATE positions SET market_value_cny=1234"))
    result = read()
    assert result["ok"] and result["result"]["total_assets_minor"] == 123400
    assert result["result"]["schema_version"] == "poc02.wealth-overview.v1"


def test_abnormal_writer_exit_recovers_committed_wal_only(shared):
    root, db, _ = shared
    script = (
        "import os,sys; from app.shared_sqlite import writer_connection; "
        "c=writer_connection(sys.argv[1],sys.argv[2]); "
        "c.execute('UPDATE positions SET market_value_cny=4321'); c.commit(); "
        "c.execute('UPDATE positions SET market_value_cny=9999'); os._exit(23)"
    )
    completed = subprocess.run([sys.executable, "-c", script, str(db),
                                str(root / "config/database-contract.json")],
                               env={"PATH": os.defpath}, capture_output=True, timeout=15)
    assert completed.returncode == 23 and completed.stdout == completed.stderr == b""
    assert overview(shared)["total_assets_minor"] == 432100
    runner.child_command(root, "mcp")
    with database.get_engine().begin() as conn:
        conn.execute(text("UPDATE positions SET market_value_cny=4322"))
    assert overview(shared)["total_assets_minor"] == 432200


def test_shared_mcp_http(shared, tmp_path):
    """An independent MCP environment invokes the real reader on fixture data."""
    mcp_python = os.environ.get("MCP_TEST_PYTHON")
    if not mcp_python:
        pytest.skip("MCP_TEST_PYTHON is required for the separate transport runtime")
    import socket
    import time
    from urllib.error import HTTPError, URLError
    from urllib.request import urlopen
    root, _, _ = shared
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    script = Path(__file__).parents[1] / "poc02_wealth_mcp/server.py"
    command = [mcp_python, str(script), "--port", str(port), "--business-python", sys.executable,
               "--real-config", str(root / "config/wealth.json"), "--access-evidence",
               str(root / "config/access-evidence.json")]
    env = {"PATH": os.defpath, "PYTHON_DOTENV_DISABLED": "1"}
    url = f"http://127.0.0.1:{port}/mcp"
    process = subprocess.Popen(command, env=env, cwd=tmp_path,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    async_script = tmp_path / "http_client.py"
    async_script.write_text(
        "import asyncio,json,sys\nfrom mcp import Client\nimport jsonschema\n"
        "async def main():\n"
        " async with Client(sys.argv[1],mode='legacy',read_timeout_seconds=20) as c:\n"
        "  tools=await c.list_tools()\n"
        "  assert [t.name for t in tools.tools]==['get_wealth_overview']\n"
        "  assert tools.tools[0].annotations.read_only_hint is True\n"
        "  result=await c.call_tool('get_wealth_overview',{'currency':'CNY'})\n"
        "  assert not result.is_error\n"
        "  data=result.structured_content\n"
        "  jsonschema.validate(data,tools.tools[0].output_schema)\n"
        "  assert data['total_assets_minor']==int(sys.argv[2])\n"
        "  assert data['status']=='partial' and data['synthetic'] is False\n"
        "  bad=await c.call_tool('get_wealth_overview',{'currency':'USD'})\n"
        "  assert bad.is_error\n"
        "asyncio.run(main())\n")
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            assert process.poll() is None
            try:
                urlopen(url, timeout=0.2).close(); break
            except HTTPError:
                break
            except (URLError, TimeoutError):
                time.sleep(0.05)
        else:
            pytest.fail("fixture MCP listener did not start")
        for value in (100000, 222200):
            if value == 222200:
                with database.get_engine().begin() as conn:
                    conn.execute(text("UPDATE positions SET market_value_cny=2222"))
            completed = subprocess.run([mcp_python, str(async_script), url, str(value)],
                                       env=env, capture_output=True, timeout=30)
            assert completed.returncode == 0, completed.stderr.decode()
        validator = Path(__file__).with_name("validate.py")
        output = root / "evidence/runtime-validation.json"
        checked = subprocess.run([mcp_python, str(validator), "--port", str(port),
                                  "--expect", "real", "--runtime-data", "--output", str(output)],
                                 env=env, capture_output=True, timeout=30)
        assert checked.returncode == 0, checked.stderr.decode()
        receipt = json.loads(checked.stdout)
        assert receipt["result"] == "PASS"
        assert receipt["comparison_basis"] == "runtime_contract_only"
        assert receipt["amounts_equal_to_migration_reference"] is None
        reference = root / "data/migration-reference.json"
        refused = subprocess.run([mcp_python, str(validator), "--port", str(port),
                                  "--expect", "real", "--runtime-data", "--reference", str(reference),
                                  "--output", str(output)], env=env, capture_output=True, timeout=10)
        assert refused.returncode == 1
        assert json.loads(refused.stdout)["error"] == "RUNTIME_AND_MIGRATION_COMPARISON_EXCLUSIVE"
    finally:
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(5)


def test_unknown_stale_and_source_failure_remain_explicit(shared, monkeypatch):
    data = overview(shared, now=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert data["status"] == "partial"
    assert {"STALE_COMPONENTS", "VALUATION_DATE_UNKNOWN"} <= set(data["issues"])
    import logging
    def failed():
        logging.getLogger("backend.services.portfolio_service").warning("synthetic source failure")
        return 0.0, []
    monkeypatch.setattr(portfolio_service, "_get_tiger_account_cash", failed)
    data = overview(shared, cash_configured=True)
    assert data["status"] == "unavailable" and data["net_worth_minor"] is None
    assert "TIGER_CASH_NOT_SYNCED" in data["issues"]


def test_transient_sidecar_disappearing_during_validation_is_allowed(shared, monkeypatch):
    _, db, contract_path = shared
    sidecar = Path(str(db) + "-journal")
    sidecar.touch(mode=0o600)
    original = shared_sqlite.private_file
    removed = False
    def racing_file(path, mode=None):
        nonlocal removed
        if Path(path) == sidecar and not removed:
            removed = True
            sidecar.unlink()  # simulate SQLite's last-close cleanup, fixture only
        return original(path, mode)
    monkeypatch.setattr(shared_sqlite, "private_file", racing_file)
    shared_sqlite.check_file(db, shared_sqlite.load_contract(contract_path))
    assert removed


@pytest.mark.parametrize("kind", ["public", "dangling_symlink"])
def test_persistent_unsafe_sidecar_still_fails_closed(shared, kind):
    _, db, contract_path = shared
    sidecar = Path(str(db) + "-journal")
    if kind == "public":
        sidecar.touch(); sidecar.chmod(0o644)
    else:
        sidecar.symlink_to(sidecar.with_name("missing-synthetic-journal"))
    with pytest.raises(ValueError):
        shared_sqlite.check_file(db, shared_sqlite.load_contract(contract_path))
