"""Private operator schema/consistent-backup probe; stdlib, no business imports.

On Mac Mini execute under a reviewed OS sandbox denying source file writes and
network, allowing writes ONLY to the private backup destination directory. Never
relax the source guard on failure. The installed snapshot/common hashes must be
independently verified first. This is not a production migration/restore tool.
"""
from contextlib import closing
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import sys


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def private_directory(folder):
    if any(p.is_symlink() for p in (folder, *folder.parents)):
        raise ValueError("SYMLINK_BACKUP_DIRECTORY")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.stat().st_uid != os.getuid() or stat.S_IMODE(folder.stat().st_mode) != 0o700:
        raise ValueError("PRIVATE_BACKUP_DIRECTORY_REQUIRED")


def load_verified(name, path, expected):
    if sha(path) != expected:
        raise ValueError("INSTALLED_BACKUP_CODE_HASH_MISMATCH")
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[name]=module
    spec.loader.exec_module(module)
    return module


def run(args):
    args.phase='DEVICE_AND_PATH_CHECK'
    if socket.gethostname() != args.expected_host:
        raise ValueError("PERSONAL_DEVICE_IDENTITY_MISMATCH")
    source=Path(args.source).resolve(strict=True)
    destination=Path(args.destination).absolute()
    if (".." in destination.parts or source == destination.resolve() or destination.exists()
            or source.is_relative_to(destination.parent.resolve())):
        raise ValueError("EXCLUSIVE_SEPARATE_BACKUP_REQUIRED")
    private_directory(destination.parent)
    # No environment files, provider clients, SQLite initialization or loaders.
    code=Path(args.snapshot_code).resolve(strict=True)
    load_verified('common',code.parent/'common.py',args.common_sha)
    snapshot=load_verified('snapshot',code,args.snapshot_sha)
    closed_path = getattr(args, 'closed_source_evidence', None)
    closed = None
    if closed_path:
        # Explicit operator closure evidence only, never a CANTOPEN fallback.
        if Path(args.source).absolute() != source:
            raise ValueError('CLOSED_SOURCE_CANONICAL_PATH_REQUIRED')
        artifact = Path(closed_path).absolute()
        if (any(p.is_symlink() for p in (artifact, *artifact.parents))
                or artifact.stat().st_uid != os.getuid() or artifact.stat().st_nlink != 1
                or stat.S_IMODE(artifact.stat().st_mode) != 0o600):
            raise ValueError('PRIVATE_CLOSED_SOURCE_EVIDENCE_REQUIRED')
        closed=json.loads(artifact.read_text())
        snapshot.verify_closed_source(source,closed)
    receipt={'observed_at':datetime.now(timezone.utc).isoformat(), 'host':socket.gethostname(),
        'source_identity':{'path':str(source),'inode':source.stat().st_ino,'bytes':source.stat().st_size,
                           'uid':source.stat().st_uid,'mode':oct(stat.S_IMODE(source.stat().st_mode))},
        'source_mode':'mode=ro/query_only', 'backup_mechanism':'EXISTING_POC03_SQLITE_BACKUP_API',
        'production_migration_executed':False, 'business_imports':False}
    if closed is not None:
        receipt['source_mode']='mode=ro/immutable=1/query_only/EXPLICIT_VERIFIED_CLOSED_SOURCE'
        receipt['closed_source_evidence']=closed
    receipt['source_sidecars']={suffix:{'bytes':p.stat().st_size,'uid':p.stat().st_uid,
        'mode':oct(stat.S_IMODE(p.stat().st_mode))} for suffix in ('-wal','-shm')
        if (p:=Path(str(source)+suffix)).exists()}
    # Metadata probe pins one snapshot; backup() independently pins its own.
    # Concurrent legitimate writes may occur; never claim cross-DB atomicity.
    args.phase='OPEN_READONLY_SOURCE'
    reader = snapshot.read_connection(source) if closed is None else snapshot.read_connection(source,closed_source=closed)
    with closing(reader) as db:
        args.phase='SCHEMA_SELECT_IN_READ_TRANSACTION'
        db.execute('BEGIN')
        receipt['schema']=db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        receipt['schema_sha256']=hashlib.sha256(json.dumps(receipt['schema'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
        receipt['user_version']=db.execute('PRAGMA user_version').fetchone()[0]
        receipt['journal_mode']=db.execute('PRAGMA journal_mode').fetchone()[0]
        receipt['schema_probe_inventory']=snapshot.inventory(db)
        db.rollback()
    args.phase='EXISTING_BACKUP_API'
    manifest=snapshot.backup(source,destination) if closed is None else snapshot.backup(source,destination,closed_source=closed)
    args.phase='VERIFY_CLOSED_BACKUP'
    snapshot.verify(destination,manifest)
    with closing(snapshot.read_connection(destination)) as db:
        receipt['backup_foreign_key_check']=db.execute('PRAGMA foreign_key_check').fetchall()
    if closed is not None:
        snapshot.verify_closed_source(source,closed)
        receipt['source_identity_and_content_unchanged']=True
    receipt.update(status='VERIFIED_CONSISTENT_BACKUP',backup_sha256=manifest['sha256'],
        backup_bytes=destination.stat().st_size,backup_manifest=manifest,
        cross_database_atomic_snapshot='NOT_CLAIMED', source_write_guard='REQUIRED_EXTERNAL_OS_SANDBOX')
    output=Path(str(destination)+'.audit.json')
    with output.open('x') as stream:
        os.chmod(output,0o600);json.dump(receipt,stream,ensure_ascii=False,indent=2)
    return {'status':receipt['status'],'backup_sha256':manifest['sha256'],'schema_sha256':receipt['schema_sha256'],
            'tables':len(manifest['tables']),'private_receipt_saved':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('source','destination','snapshot-code','snapshot-sha','common-sha','expected-host'):
        parser.add_argument('--'+key,required=True)
    parser.add_argument('--closed-source-evidence', help='Private explicit closure/identity receipt; never automatic')
    args=parser.parse_args()
    try:print(json.dumps(run(args),ensure_ascii=False))
    except Exception as exc:
        # No SQL, record values, credentials or exception paths on stdout.
        print(json.dumps({'status':'BLOCKED_SAFE_READ_ONLY_BACKUP','error_type':type(exc).__name__,
            'phase':getattr(args,'phase','ARGUMENTS'),
            'sqlite_errorname':getattr(exc,'sqlite_errorname',None),'source_guard_not_relaxed':True}))
        raise SystemExit(1)


if __name__=='__main__':main()
