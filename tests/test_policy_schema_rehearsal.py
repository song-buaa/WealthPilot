"""Offline-copy DDL/backup safety with fabricated histories, never a user DB."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import subprocess
import socket
import json

import pytest

from backend.services.investment_policy import schema_rehearsal as rehearsal
from backend.services.investment_policy.parser import PolicyError


@pytest.fixture
def exported(tmp_path):
    # Reuse the tested original Backup API implementation, stdlib only.
    folder=Path(__file__).resolve().parents[1]/'pocs/poc03_mac_mini'
    sys.path.insert(0,str(folder))
    import snapshot
    private=tmp_path/'backup';private.mkdir(mode=0o700)
    copies=tmp_path/'isolated';copies.mkdir(mode=0o700)
    source=tmp_path/'synthetic-source.db'
    with closing(sqlite3.connect(source)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript("CREATE TABLE theses(id TEXT PRIMARY KEY, payload TEXT); INSERT INTO theses VALUES('TEST_ONLY','frozen'); CREATE TABLE orders(id INTEGER PRIMARY KEY, payload TEXT); INSERT INTO orders VALUES(1,'SYNTHETIC_NO_BROKER');")
        db.commit()
        before=rehearsal.inventory(db)
        output=private/'consistent.db'
        manifest=snapshot.backup(source,output)
        assert rehearsal.inventory(db)==before
    target=copies/'copy.db';shutil.copy2(output,target);target.chmod(0o600)
    return dict(backup=output,manifest=manifest,target=target,rehearsal_root=copies)


def test_consistent_wal_backup_copy_ddl_twice_and_restoration(exported):
    source=exported['backup'];target=exported['target'];raw=source.read_bytes()
    first=rehearsal.install_on_isolated_copy(**exported)
    assert first['preexisting_history_unchanged'] and len(first['added_objects'])==12
    assert source.read_bytes()==raw
    second=rehearsal.install_on_isolated_copy(**exported)
    assert second['added_objects']==[] and second['before']==first['after']
    # Only the isolated target may be restored. Preserve its migrated state first.
    archived=target.parent/'post-migration.db';shutil.copy2(target,archived)
    shutil.copyfile(source,target)
    assert hashlib.sha256(target.read_bytes()).hexdigest()==exported['manifest']['sha256']
    with closing(sqlite3.connect(target)) as db:assert rehearsal.inventory(db)==exported['manifest']['tables']


def test_mid_migration_failure_rolls_back_all_schema_and_data(exported,monkeypatch):
    target=exported['target'];raw=target.read_bytes();original=rehearsal._execute;counter=[]
    def fail(db,sql):
        counter.append(sql)
        if len(counter)==4:raise RuntimeError('INJECTED_ISOLATED_FAILURE')
        original(db,sql)
    monkeypatch.setattr(rehearsal,'_execute',fail)
    with pytest.raises(RuntimeError,match='INJECTED'):rehearsal.install_on_isolated_copy(**exported)
    assert target.read_bytes()==raw
    with closing(sqlite3.connect(target)) as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'policy_%'").fetchall()


def test_copy_must_match_backup_and_never_source_or_hardlink(exported):
    with pytest.raises(PolicyError):rehearsal.install_on_isolated_copy(**{**exported,'target':exported['backup']})
    target=exported['target'];target.unlink();os.link(exported['backup'],target)
    with pytest.raises(PolicyError):rehearsal.install_on_isolated_copy(**exported)
    target.unlink();shutil.copy2(exported['backup'],target)
    with closing(sqlite3.connect(target)) as db:db.execute("UPDATE theses SET payload='wrong'");db.commit()
    with pytest.raises(PolicyError,match='DIFFERS'):rehearsal.install_on_isolated_copy(**exported)


def test_unknown_conflicting_policy_schema_rejected_not_replaced(exported):
    with closing(sqlite3.connect(exported['target'])) as db:db.execute('CREATE TABLE policy_bundles(wrong TEXT)')
    with pytest.raises(PolicyError,match='INCOMPATIBLE'):rehearsal.install_on_isolated_copy(**exported)


def test_migrated_schema_retains_nine_immutable_guards(exported):
    rehearsal.install_on_isolated_copy(**exported)
    from backend.services.investment_policy.store import PolicyStore
    store=PolicyStore(exported['target'])
    with store.connection(write=True) as db:
        assert len(db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'policy_%'").fetchall())==9
        db.execute("INSERT INTO policy_bundles VALUES('SYNTHETIC',1,'id','req','hash','{}','hash',X'01',X'01')")
        with pytest.raises(sqlite3.IntegrityError,match='IMMUTABLE'):db.execute('DELETE FROM policy_bundles')


def test_copy_forged_manifest_or_changed_backup_is_rejected(exported):
    with pytest.raises(PolicyError,match='BACKUP_DIGEST'):
        rehearsal.install_on_isolated_copy(**{**exported,'manifest':{**exported['manifest'],'sha256':'0'*64}})
    with pytest.raises(PolicyError,match='VERIFIED_SQLITE_BACKUP'):
        rehearsal.install_on_isolated_copy(**{**exported,'manifest':{**exported['manifest'],'schema':'RAW_FILE_COPY'}})


def test_parent_traversal_cannot_escape_rehearsal_root(exported):
    nested=exported['rehearsal_root']/'nested';nested.mkdir(mode=0o700)
    outside=exported['rehearsal_root'].parent/'outside.db'
    shutil.copy2(exported['backup'],outside)
    target=nested/'..'/'..'/'outside.db'
    before=outside.read_bytes()
    with pytest.raises(PolicyError):rehearsal.install_on_isolated_copy(**{**exported,'target':target})
    assert outside.read_bytes()==before


@pytest.mark.parametrize('ddl',["CREATE INDEX wrong ON theses(payload)",
    "CREATE TRIGGER wrong AFTER INSERT ON theses BEGIN SELECT 1; END", "CREATE VIEW wrong AS SELECT id FROM theses"])
def test_equal_rows_but_different_business_schema_is_not_verified_copy(exported,ddl):
    with closing(sqlite3.connect(exported['target'])) as db:db.execute(ddl)
    with pytest.raises(PolicyError,match='SCHEMA_DIFFERS'):rehearsal.install_on_isolated_copy(**exported)


def test_private_backup_probe_uses_existing_verified_implementation(exported):
    root=Path(__file__).resolve().parents[1]
    source=exported['backup'];before=source.read_bytes()
    destination=source.parent/'exports'/'second-consistent.db'
    code=root/'pocs/poc03_mac_mini/snapshot.py'
    command=[sys.executable,'-I','-B',str(root/'scripts/policy_backup_readonly.py'),
        '--source',str(source),'--destination',str(destination),'--snapshot-code',str(code),
        '--snapshot-sha',hashlib.sha256(code.read_bytes()).hexdigest(),
        '--common-sha',hashlib.sha256((code.parent/'common.py').read_bytes()).hexdigest(),
        '--expected-host',socket.gethostname()]
    # Same strict profile planned for Mini; all source writes remain denied.
    if sys.platform=='darwin':
        profile='(version 1)(allow default)(deny file-write*)(deny network*)(allow file-write* (subpath '+json.dumps(str(destination.parent))+'))'
        command=['/usr/bin/sandbox-exec','-p',profile,*command]
    result=subprocess.run(command,text=True,capture_output=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    assert json.loads(result.stdout)['status']=='VERIFIED_CONSISTENT_BACKUP'
    assert source.read_bytes()==before
    audit=json.loads(Path(str(destination)+'.audit.json').read_text())
    assert audit['source_mode']=='mode=ro/query_only' and not audit['business_imports']


def test_incompatible_backup_code_is_not_imported(exported):
    import importlib.util
    spec=importlib.util.spec_from_file_location('policy_backup_probe',Path(__file__).resolve().parents[1]/'scripts/policy_backup_readonly.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pytest.raises(ValueError,match='CODE_HASH_MISMATCH'):
        module.load_verified('not_imported',exported['backup'],'0'*64)


def test_existing_source_fk_violation_refuses_before_any_target_ddl(exported):
    # A consistent backup can faithfully preserve an already-invalid legacy FK.
    source=exported['backup']
    with closing(sqlite3.connect(source)) as db:
        db.executescript('CREATE TABLE parent(id INTEGER PRIMARY KEY); CREATE TABLE child(parent_id INTEGER REFERENCES parent(id)); INSERT INTO child VALUES(9);')
        tables=rehearsal.inventory(db)
    exported['manifest'].update(sha256=hashlib.sha256(source.read_bytes()).hexdigest(),tables=tables)
    shutil.copyfile(source,exported['target']);before=exported['target'].read_bytes()
    with pytest.raises(PolicyError,match='SOURCE_BACKUP_FOREIGN_KEY_VIOLATIONS'):
        rehearsal.install_on_isolated_copy(**exported)
    assert exported['target'].read_bytes()==before
