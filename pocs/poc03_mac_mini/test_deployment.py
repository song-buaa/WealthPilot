"""Preparation tests: disposable databases, fake launchctl, no remote devices."""
from contextlib import closing
from datetime import date
import json
import os
from pathlib import Path
import plistlib
import signal
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common
import deploy
import runner
import snapshot
import access_check


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "source.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE positions(id INTEGER PRIMARY KEY, value REAL)")
        conn.execute("INSERT INTO positions VALUES(1, 123.45)")
        conn.commit()
    return path


def test_backup_integrity_and_source_unchanged(database, tmp_path):
    before = common.digest(database)
    output = tmp_path / "export.db"
    manifest = snapshot.backup(database, output)
    snapshot.verify(output, manifest)
    assert common.digest(database) == before
    assert manifest["tables"]["positions"]["rows"] == 1
    assert output.stat().st_mode & 0o077 == 0


def test_backup_pins_committed_wal_snapshot(database, tmp_path, monkeypatch):
    writer = sqlite3.connect(database)
    writer.execute("PRAGMA journal_mode=WAL")
    original = snapshot.inventory
    called = False
    def inventory(conn):
        nonlocal called
        result = original(conn)
        if not called:
            called = True
            writer.execute("UPDATE positions SET value=999")
            writer.commit()
        return result
    monkeypatch.setattr(snapshot, "inventory", inventory)
    output = tmp_path / "wal-export.db"
    snapshot.backup(database, output)
    with closing(snapshot.read_connection(output)) as conn:
        assert conn.execute("SELECT value FROM positions").fetchone()[0] == 123.45
    writer.close()


def test_backup_refuses_overwrite(database, tmp_path):
    target = tmp_path / "exists.db"; target.write_bytes(b"keep")
    with pytest.raises(ValueError, match="DESTINATION_EXISTS"):
        snapshot.backup(database, target)
    assert target.read_bytes() == b"keep"


def test_backup_refuses_replacing_existing_manifest(database, tmp_path):
    target = tmp_path / "backup.db"
    manifest = Path(str(target) + ".manifest.json"); manifest.write_bytes(b"keep")
    with pytest.raises(ValueError, match="MANIFEST_ALREADY_EXISTS"):
        snapshot.backup(database, target)
    assert manifest.read_bytes() == b"keep" and not target.exists()


def test_package_manifest_requires_all_deployment_assets(tmp_path):
    package = tmp_path / "package"; package.mkdir()
    (package / "release.json").write_text(json.dumps({"schema": "poc03.release.v1", "revision": "a" * 40, "files": {}}))
    with pytest.raises(ValueError, match="ASSET_MANIFEST_INCOMPLETE"):
        deploy.verify_package(package, "a" * 40)


def test_missing_source_not_created(tmp_path):
    source = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        snapshot.backup(source, tmp_path / "new.db")
    assert not source.exists() and not (tmp_path / "new.db").exists()


def test_tampered_backup_refused(database, tmp_path):
    target = tmp_path / "backup.db"; manifest = snapshot.backup(database, target)
    with target.open("ab") as f: f.write(b"tamper")
    with pytest.raises(ValueError, match="DIGEST_MISMATCH"):
        snapshot.verify(target, manifest)


def test_restore_requires_stopped_services(database, tmp_path):
    backup = tmp_path / "backup.db"; manifest = snapshot.backup(database, backup)
    with pytest.raises(ValueError, match="STOP_SERVICES"):
        snapshot.restore(backup, tmp_path / "live.db", manifest, False)


def test_restore_retains_previous_data_and_exact_backup_bytes(database, tmp_path):
    saved = tmp_path / "backup.db"; manifest = snapshot.backup(database, saved)
    target = tmp_path / "live.db"
    snapshot.backup(database, target)
    with closing(sqlite3.connect(target)) as conn:
        conn.execute("UPDATE positions SET value=777"); conn.commit()
    snapshot.restore(saved, target, manifest, True)
    assert common.digest(target) == manifest["sha256"]
    retained = list(tmp_path.glob("pre-restore-*.db"))
    assert len(retained) == 1
    with closing(snapshot.read_connection(retained[0])) as conn:
        assert conn.execute("SELECT value FROM positions").fetchone()[0] == 777
    assert target.stat().st_mode & 0o777 == 0o400


def test_restore_refuses_unreviewed_sidecars(database, tmp_path):
    saved = tmp_path / "backup.db"; manifest = snapshot.backup(database, saved)
    target = tmp_path / "live.db"; Path(str(target) + "-wal").write_bytes(b"keep")
    with pytest.raises(ValueError, match="SIDECARS_REQUIRE_REVIEW"):
        snapshot.restore(saved, target, manifest, True)
    assert not target.exists()


def test_private_configuration_refuses_world_readable(tmp_path):
    p = tmp_path / "config.json"; p.write_text('{}'); p.chmod(0o644)
    with pytest.raises(ValueError, match="MODE_0600"):
        common.private_json(p)


@pytest.fixture
def runtime(tmp_path):
    root = deploy.layout(tmp_path / "prod with spaces")
    state = {"revision": "a" * 40, "mode": "synthetic", "user": "fixture-user",
             "user_home": str(tmp_path), "expected_hostname": "fixture-mini"}
    common.write_json(root / "config/runtime.json", state)
    release = root / "releases" / state["revision"]; release.mkdir(mode=0o700)
    common.write_json(release / "installed.json", {"revision": state["revision"]})
    deploy.select_release(root, state["revision"])
    return root, state


def test_plists_system_daemon_nonroot_and_space_paths(runtime):
    root, state = runtime
    for role, contents in deploy.render_plists(root, state).items():
        loaded = plistlib.loads(plistlib.dumps(contents))
        assert loaded["UserName"] == "fixture-user"
        assert loaded["RunAtLoad"] and loaded["KeepAlive"]
        assert loaded["Umask"] == 0o077
        assert loaded["ProgramArguments"][-1] == role
        assert str(root) in loaded["ProgramArguments"]
        assert loaded["StandardOutPath"] == loaded["StandardErrorPath"] == "/dev/null"


def test_service_dry_run_never_executes_launchctl(runtime):
    root, _ = runtime
    def forbidden(*args, **kwargs): raise AssertionError("must not execute")
    plan = deploy.service_action(root, "install", False, execute=forbidden)
    assert plan["apply"] is False
    assert plan["planned_commands"][0][-1].endswith("secure-tunnel")
    assert plan["planned_commands"][-2][-1].endswith("readonly-mcp.plist")


def test_existing_other_deployment_refused_before_stop(runtime, tmp_path, monkeypatch):
    root, _ = runtime
    daemon_dir = tmp_path / "fake-daemons"; daemon_dir.mkdir()
    (daemon_dir / (deploy.LABELS["mcp"] + ".plist")).write_bytes(plistlib.dumps({"WorkingDirectory": "/other"}))
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    calls = []
    with pytest.raises(ValueError, match="OTHER_DEPLOYMENT"):
        deploy.service_action(root, "install", True, daemon_dir, lambda c: calls.append(c))
    assert calls == []


def test_bootstrap_failure_stops_partial_ingress(runtime, tmp_path, monkeypatch):
    root, state = runtime
    daemon_dir = tmp_path / "fake-daemons"; daemon_dir.mkdir()
    for role, config in deploy.render_plists(root, state).items():
        (daemon_dir / (deploy.LABELS[role] + ".plist")).write_bytes(plistlib.dumps(config))
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    calls = []
    def fake(command):
        calls.append(command)
        if command[1] == "bootstrap" and command[-1].endswith("secure-tunnel.plist"):
            raise subprocess.CalledProcessError(1, command)
    with pytest.raises(subprocess.CalledProcessError):
        deploy.service_action(root, "restart", True, daemon_dir, fake)
    assert [c[-1] for c in calls[-2:]] == ["system/" + deploy.LABELS["tunnel"], "system/" + deploy.LABELS["mcp"]]


def test_real_gate_refuses_old_poc_attestation(runtime, monkeypatch):
    root, state = runtime; state["mode"] = "real"
    common.write_json(root / "config/runtime.json", state)
    common.write_json(root / "config/access-evidence.json", {k: True for k in runner.PROOF_FIELDS[:4]})
    monkeypatch.setattr(runner, "host_guard", lambda _: None)
    monkeypatch.setattr(runner, "check_revision", lambda *a: None)
    with pytest.raises(ValueError, match="REAL_ACCESS_NOT_VERIFIED"):
        runner.child_command(root, "mcp")


def test_synthetic_command_only_verified_mcp_entry(runtime, monkeypatch):
    root, _ = runtime
    monkeypatch.setattr(runner, "host_guard", lambda _: None)
    monkeypatch.setattr(runner, "check_revision", lambda *a: None)
    command, _ = runner.child_command(root, "mcp")
    assert command[1].endswith("pocs/poc02_wealth_mcp/server.py")
    assert "--real-config" not in command
    assert not any("backend.main" in arg or "order" in arg for arg in command)


def test_host_guard_rejects_rosetta(monkeypatch):
    monkeypatch.setattr(common.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(common.platform, "machine", lambda: "x86_64")
    with pytest.raises(ValueError, match="NATIVE_ARM64"):
        common.host_guard("fixture-mini")


def test_runner_discards_raw_child_output(runtime, monkeypatch):
    root, _ = runtime
    fake_secret = "PRIVATE_FIXTURE_SHOULD_NOT_BE_LOGGED"
    monkeypatch.setattr(runner, "child_command", lambda *args: ([sys.executable, "-c", "print('" + fake_secret + "')"], {"user_home": str(root)}))
    # Popen cwd must exist, no production checkout/launchd required.
    (root / "current/code").mkdir()
    monkeypatch.setattr(sys, "argv", ["runner", "--root", str(root), "--role", "mcp"])
    old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        with pytest.raises(SystemExit) as result: runner.main()
        assert result.value.code == 0
    finally:
        for s, handler in old.items(): signal.signal(s, handler)
    log = (root / "logs/mcp.lifecycle.jsonl").read_text()
    assert fake_secret not in log and "child_started" in log and "child_exited" in log


def test_switch_real_refuses_missing_evidence_even_dry_run(runtime):
    root, state = runtime
    with pytest.raises(ValueError, match="PRIVATE_FILE_REQUIRED"):
        deploy.switch_mode_or_revision(root, state["revision"], "real", False)


def test_configure_does_not_reset_existing_runtime(runtime, monkeypatch):
    root, state = runtime
    monkeypatch.setattr(deploy, "check_revision", lambda *a: None)
    with pytest.raises(ValueError, match="EXISTING_RUNTIME"):
        deploy.configure(root, state["revision"], "fixture-mini", "unused", "unused")


def test_private_reference_replays_existing_calculation(tmp_path, monkeypatch):
    sys.path.insert(0, str(common.ROOT))
    os.environ.update(PYTHON_DOTENV_DISABLED="1", PUBLIC_DEMO_MODE="false")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.models import Portfolio, Position, WealthItem
    db = tmp_path / "wealth.db"; engine = create_engine("sqlite:///" + str(db))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Portfolio(id=1, name="fixture"))
        session.add(Position(portfolio_id=1, name="fixture", platform="fixture", segment="投资", asset_class="货币", market_value_cny=1000))
        session.add(WealthItem(portfolio_id=1, kind="LIABILITY", name="fixture", item_type="credit_card", category="fixture", current_value=100, value_as_of=date.today(), included_in_net_worth=True))
        session.commit()
    engine.dispose()
    reference = snapshot.valuation_reference(db, {"portfolio_id": 1})
    result = snapshot.check_reference(db, reference)
    assert all(result["amounts_equal"].values())
    reference["result"]["net_worth_minor"] += 1
    with pytest.raises(ValueError, match="AMOUNTS_MISMATCH"):
        snapshot.check_reference(db, reference)


def test_private_scope_probe_redacts_ids_and_key():
    key = "PRIVATE_FIXTURE_KEY"
    metadata = {"organization_ids": ["PRIVATE_ORG"], "workspace_ids": ["PRIVATE_WORKSPACE"], "creator_email": "PRIVATE_EMAIL"}
    def fake(path, credential):
        return (200, metadata) if credential == key else (401, None)
    result = access_check.probe("fixture", key, {"organization_ids": ["PRIVATE_ORG"], "workspace_ids": ["PRIVATE_WORKSPACE"]}, fake)
    assert result["result"] == "PASS"
    assert all(value not in json.dumps(result) for value in (key, "PRIVATE_ORG", "PRIVATE_WORKSPACE", "PRIVATE_EMAIL"))


def test_scope_probe_does_not_pass_extra_workspace():
    def fake(path, credential):
        return (200, {"organization_ids": ["org"], "workspace_ids": ["one", "other"]}) if credential == "fixture" else (401, None)
    result = access_check.probe("fixture", "fixture", {"organization_ids": ["org"], "workspace_ids": ["one"]}, fake)
    assert result["result"] == "FAIL" and result["expected_private_scope_equal"] is False
