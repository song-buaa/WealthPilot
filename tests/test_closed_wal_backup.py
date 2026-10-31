"""Synthetic stopped/live WAL with OS source-write denial on macOS only."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pocs/poc03_mac_mini"))
import snapshot


@pytest.fixture
def wal(tmp_path):
    source_dir = tmp_path/"source"; source_dir.mkdir(mode=0o700)
    out = tmp_path/"exports"; out.mkdir(mode=0o700)
    source = source_dir/"closed.db"
    db = sqlite3.connect(source)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("CREATE TABLE parent(id INTEGER PRIMARY KEY); INSERT INTO parent VALUES(1);"
                     "CREATE TABLE child(id INTEGER REFERENCES parent(id),payload TEXT);"
                     "INSERT INTO child VALUES(1,'SYNTHETIC_NO_PROVIDER');")
    db.close()  # fixture setup normal SQLite last-close, no explicit checkpoint/unlink
    source.chmod(0o600)
    assert source.read_bytes()[18:20] == b"\x02\x02"
    assert not any(Path(str(source)+s).exists() for s in ("-wal", "-shm", "-journal"))
    return source, out


def evidence(source):
    return snapshot.closed_source_evidence(source, closure_evidence_ref="TEST_ONLY_ALL_CLIENTS_CLOSED_NO_RESTART",
        all_clients_stopped=True, automatic_restart_disabled=True, no_unmerged_wal=True)


def test_explicit_closed_backup_preserves_source_and_manifest_and_restores(wal):
    source, out = wal; proof = evidence(source); before = source.read_bytes()
    target = out/"export.db"
    with closing(snapshot.read_connection(source, closed_source=proof)) as db:
        expected = snapshot.inventory(db)
        with pytest.raises(sqlite3.OperationalError): db.execute("DELETE FROM child")
    manifest = snapshot.backup(source, target, closed_source=proof)
    assert manifest["tables"] == expected and source.read_bytes() == before
    assert set(manifest) == {"schema", "sha256", "tables", "integrity", "created_at"}
    snapshot.verify(target, manifest)
    restored = out/"restored.db"
    snapshot.restore(target, restored, manifest, True)
    snapshot.verify(restored, manifest)
    with closing(snapshot.read_connection(restored)) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert snapshot._closed_source_binding(source) == proof["source"]


@pytest.mark.parametrize("field", ["all_clients_stopped", "automatic_restart_disabled", "no_unmerged_wal", "closure_evidence_ref", "host"])
def test_missing_or_uncertain_closure_refuses(wal, field):
    source, out = wal; proof = evidence(source)
    proof.pop(field)
    with pytest.raises(ValueError, match="CLOSURE_EVIDENCE"):
        snapshot.backup(source, out/"refused.db", closed_source=proof)
    assert not (out/"refused.db").exists()
    with pytest.raises(ValueError, match="CLOSURE_EVIDENCE"):
        snapshot.closed_source_evidence(source, closure_evidence_ref="NO_ATTESTATION")


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("kind", ["empty", "dangling_symlink"])
def test_any_sidecar_including_empty_or_dangling_link_refuses(wal, suffix, kind):
    source, out = wal; proof = evidence(source)
    sidecar = Path(str(source)+suffix)
    if kind == "empty": sidecar.write_bytes(b"")
    else: sidecar.symlink_to(source.parent/"not-present")
    with pytest.raises(ValueError, match="SIDECAR_PRESENT"):
        snapshot.backup(source, out/"refused.db", closed_source=proof)
    assert not (out/"refused.db").exists()
    assert sidecar.exists() or sidecar.is_symlink()  # never removed by backup


@pytest.mark.parametrize("change", ["hash", "inode", "owner_identity", "path", "symlink", "hardlink"])
def test_changed_file_or_evidence_identity_refuses(wal, change):
    source, out = wal; proof = evidence(source)
    if change == "hash": proof["source"]["sha256"] = "0"*64
    elif change == "owner_identity": proof["source"]["identity"][2] += 1
    elif change == "path": proof["source"]["path"] += ".wrong"
    elif change == "inode":
        replacement = out/"new"; replacement.write_bytes(source.read_bytes()); replacement.chmod(0o600)
        os.replace(replacement, source)
    elif change == "hardlink": os.link(source, out/"alias")
    else:
        alias = out/"alias"; alias.symlink_to(source); source = alias
    with pytest.raises(ValueError): snapshot.backup(source, out/"refused.db", closed_source=proof)
    assert not (out/"refused.db").exists()


def test_source_change_after_sql_read_blocks_publication(wal, monkeypatch):
    source, out = wal; proof = evidence(source); original = snapshot.inventory; called = False
    def change(db):
        nonlocal called
        result = original(db)
        if not called:
            called = True
            with source.open("ab") as f: f.write(b"UNEXPECTED_CHANGE")
        return result
    monkeypatch.setattr(snapshot, "inventory", change)
    with pytest.raises(ValueError): snapshot.backup(source, out/"refused.db", closed_source=proof)
    assert not (out/"refused.db").exists() and not (out/"refused.db.manifest.json").exists()


def test_live_wal_default_uses_committed_frames_never_immutable(wal, monkeypatch):
    source, out = wal; writer = sqlite3.connect(source)
    try:
        writer.execute("INSERT INTO child VALUES(1,'LIVE_WAL_COMMITTED')"); writer.commit()
        assert Path(str(source)+"-wal").stat().st_size > 0
        with pytest.raises(ValueError, match="SIDECAR"):
            evidence(source)
        calls = []; original = snapshot.sqlite3.connect
        def connect(database, *args, **kwargs):
            calls.append(str(database)); return original(database, *args, **kwargs)
        monkeypatch.setattr(snapshot.sqlite3, "connect", connect)
        manifest = snapshot.backup(source, out/"active.db")
        assert manifest["tables"]["child"]["rows"] == 2
        assert not any("immutable" in uri for uri in calls)
    finally:
        writer.close()


def _probe(source, out, proof=None):
    code = ROOT/"pocs/poc03_mac_mini/snapshot.py"
    command = [sys.executable, "-I", "-B", str(ROOT/"scripts/policy_backup_readonly.py"),
        "--source", str(source), "--destination", str(out/"backup.db"),
        "--snapshot-code", str(code), "--snapshot-sha", hashlib.sha256(code.read_bytes()).hexdigest(),
        "--common-sha", hashlib.sha256((code.parent/"common.py").read_bytes()).hexdigest(),
        "--expected-host", socket.gethostname()]
    if proof is not None:
        artifact = out/"closed.json"; artifact.write_text(json.dumps(proof)); artifact.chmod(0o600)
        command += ["--closed-source-evidence", str(artifact)]
    if sys.platform == "darwin":
        profile = ('(version 1)(allow default)(deny file-write*)(deny network*)'
                   '(allow file-write* (subpath '+json.dumps(str(out))+'))')
        command = ["/usr/bin/sandbox-exec", "-p", profile, *command]
    return subprocess.run(command, capture_output=True, text=True, timeout=30)


def test_closed_wal_probe_and_backup_share_verified_open_path_under_os_guard(wal):
    source, out = wal; proof = evidence(source)
    if sys.platform == "darwin":
        # Reproduce exact original failure; no fallback and no source sidecars.
        result = _probe(source, out)
        assert result.returncode == 1
        error = json.loads(result.stdout)
        assert error["phase"] == "SCHEMA_SELECT_IN_READ_TRANSACTION"
        assert error["sqlite_errorname"] == "SQLITE_CANTOPEN"
        assert not (out/"backup.db").exists()
    result = _probe(source, out, proof)
    assert result.returncode == 0, result.stdout+result.stderr
    audit = json.loads((out/"backup.db.audit.json").read_text())
    assert "immutable=1" in audit["source_mode"]
    assert audit["source_identity_and_content_unchanged"] and audit["backup_foreign_key_check"] == []
    snapshot.verify_closed_source(source, proof)
    snapshot.verify(out/"backup.db", audit["backup_manifest"])
    snapshot.restore(out/"backup.db", out/"restored.db", audit["backup_manifest"], True)


def test_active_wal_probe_default_remains_readonly_under_os_guard(wal):
    source, out = wal; writer = sqlite3.connect(source)
    try:
        writer.execute("INSERT INTO child VALUES(1,'IN_WAL_ONLY')"); writer.commit()
        # Parent retains real WAL/SHM; subprocess cannot create/write source files.
        result = _probe(source, out)
        assert result.returncode == 0, result.stdout+result.stderr
        audit = json.loads((out/"backup.db.audit.json").read_text())
        assert audit["source_mode"] == "mode=ro/query_only"
        assert audit["backup_manifest"]["tables"]["child"]["rows"] == 2
        assert Path(str(source)+"-wal").exists() and Path(str(source)+"-shm").exists()
    finally:
        writer.close()
