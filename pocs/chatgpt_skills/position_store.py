"""Private file IO for PositionJobs. No execution, database or provider ownership."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import math
import time
import stat


SAFE_ISSUES = frozenset({
    'JOB_START_FAILED', 'ANALYSIS_INTERRUPTED', 'ANALYSIS_JOB_FAILED', 'ANALYSIS_WORKER_FAILED',
    'ANALYSIS_RESULT_INVALID', 'ANALYSIS_IPC_TIMEOUT', 'ANALYSIS_WORKER_EXITED',
    'ANALYSIS_WORKER_START_FAILED', 'ANALYSIS_JSON_INVALID', 'RESULT_SERIALIZATION_FAILED', 'RESULT_STORAGE_UNCONFIGURED',
    'RESULT_STORAGE_UNAVAILABLE', 'RESULT_STORAGE_UNSAFE', 'RESULT_STORAGE_QUOTA',
    'RESULT_ARTIFACT_CORRUPT', 'RESULT_ARTIFACT_MISSING', 'RESULT_RECEIPTS_CORRUPT',
})


class StoreError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')


class PositionStore:
    def __init__(self, root, quota_bytes):
        if root is None or quota_bytes is None or quota_bytes <= 0:
            raise StoreError('RESULT_STORAGE_UNCONFIGURED')
        path = Path(root)
        if not path.is_absolute() or '..' in path.parts:
            raise StoreError('RESULT_STORAGE_UNSAFE')
        for parent in (path, *path.parents):
            if (parent / '.git').exists() or parent.is_symlink():
                raise StoreError('RESULT_STORAGE_UNSAFE')
        # The operator provisions the private root. Never recreate a deleted
        # store: loss of its receipts must not silently authorize another run.
        parent_fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in path.parts[1:]:
                child_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = child_fd
            self.fd = parent_fd
        except Exception:
            os.close(parent_fd)
            raise
        self.quota = quota_bytes
        try:
            self._check(os.fstat(self.fd), directory=True)
        except Exception:
            os.close(self.fd)
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    @staticmethod
    def _check(info, directory=False):
        if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
                or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
                or (not directory and info.st_nlink != 1)):
            raise StoreError('RESULT_STORAGE_UNSAFE')

    def _open(self, name, flags):
        if not re.fullmatch(r'(?:receipts\.lock|active\.lock|receipts\.json|initialized\.json|[A-Za-z0-9_-]{32,64}\.result\.json|\.pending-[a-f0-9]{32})', name):
            raise StoreError('RESULT_STORAGE_UNSAFE')
        for attempt in range(3):
            try:
                fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self.fd)
                break
            except FileNotFoundError:
                # Concurrent first creation on macOS may transiently report
                # ENOENT for O_CREAT. Never retry reads or any model execution.
                if not flags & os.O_CREAT or attempt == 2:raise
                self._check(os.fstat(self.fd), directory=True)
                time.sleep(.001)
        try:
            self._check(os.fstat(fd))
            return fd
        except Exception:
            os.close(fd)
            raise

    @contextmanager
    def locked(self):
        fd = self._open('receipts.lock', os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def admission(self):
        fd = self._open('active.lock', os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            os.close(fd)
            return None

    def read(self, name):
        try:
            fd = self._open(name, os.O_RDONLY)
        except FileNotFoundError:
            return None
        try:
            with os.fdopen(fd, 'rb') as stream:
                raw = stream.read(self.quota + 1)
            if len(raw) > self.quota:
                raise StoreError('RESULT_STORAGE_QUOTA')
            return json.loads(raw)
        except (ValueError, UnicodeError):
            raise StoreError('RESULT_ARTIFACT_CORRUPT') from None

    def write(self, name, value, *, immutable=False):
        raw = encode(value)
        used = 0
        for entry in os.listdir(self.fd):
            info = os.stat(entry, dir_fd=self.fd, follow_symlinks=False)
            self._check(info)
            used += info.st_size
        # Reserve peak space for the atomic replacement, not only final bytes.
        if used + len(raw) > self.quota:
            raise StoreError('RESULT_STORAGE_QUOTA')
        temporary = '.pending-' + secrets.token_hex(16)
        fd = self._open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if immutable:
                # link creates without overwriting an existing immutable result.
                os.link(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd, follow_symlinks=False)
                os.unlink(temporary, dir_fd=self.fd)
            else:
                os.replace(temporary, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def receipts(self):
        value = self.read('receipts.json')
        if value is None:
            if self.read('initialized.json') is not None or any(n.endswith('.result.json') for n in os.listdir(self.fd)):
                raise StoreError('RESULT_RECEIPTS_CORRUPT')
            value = dict(schema='wealthpilot.position-receipts.v1', jobs={}, requests={})
            self.write('initialized.json', {'schema':'wealthpilot.position-store.v1'}, immutable=True)
            self.write('receipts.json', value)
        if (not isinstance(value, dict) or value.get('schema') != 'wealthpilot.position-receipts.v1'
                or not isinstance(value.get('jobs'), dict) or not isinstance(value.get('requests'), dict)):
            raise StoreError('RESULT_RECEIPTS_CORRUPT')
        for key, row in value['jobs'].items():
            if (not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,64}',key) or not isinstance(row, dict) or row.get('job_id') != key or
                    not isinstance(row.get('fingerprint'), str) or
                    not isinstance(row.get('analysis_run_id'), str) or not re.fullmatch(r'[a-f0-9]{32}',row['analysis_run_id']) or
                    not re.fullmatch(r'[a-f0-9]{64}',row['fingerprint']) or
                    not isinstance(row.get('created'),(int,float)) or not math.isfinite(row['created']) or
                    (row.get('finished') is not None and (not isinstance(row['finished'],(int,float)) or not math.isfinite(row['finished']))) or
                    (row.get('state')=='completed' and (row.get('finished') is None or
                        not isinstance(row.get('artifact_sha256'),str) or not re.fullmatch(r'[a-f0-9]{64}',row['artifact_sha256']) or
                        type(row.get('artifact_bytes')) is not int or row['artifact_bytes']<=0)) or
                    not isinstance(row.get('state'),str) or row['state'] not in {'running','completed','unavailable'} or
                    not isinstance(row.get('business_status'),str) or row['business_status'] not in {'ok','partial','unavailable','UNKNOWN'} or
                    (row.get('issue') is not None and (not isinstance(row['issue'],str) or row['issue'] not in SAFE_ISSUES))):
                raise StoreError('RESULT_RECEIPTS_CORRUPT')
        if len(value['requests']) != len(value['jobs']):
            raise StoreError('RESULT_RECEIPTS_CORRUPT')
        if any(not isinstance(key,str) or not re.fullmatch(r'[a-f0-9]{64}',key) or not isinstance(job,str) or job not in value['jobs'] or value['jobs'][job].get('request_key') != key for key,job in value['requests'].items()):
            raise StoreError('RESULT_RECEIPTS_CORRUPT')
        return value

    def save_receipts(self, value):
        self.write('receipts.json', value)

    def save_result(self, row, result, finished_at):
        body = result.model_dump(mode='json')
        raw = encode(body)
        envelope = dict(schema='wealthpilot.position-artifact.v1', job_id=row['job_id'],
            analysis_run_id=row['analysis_run_id'], fingerprint=row['fingerprint'],
            result_schema=body['schema_version'], result_bytes=len(raw),
            result_sha256=hashlib.sha256(raw).hexdigest(), finished_at=finished_at, result=body)
        self.write(row['job_id']+'.result.json', envelope, immutable=True)
        return self.load_result(row)

    def load_result(self, row):
        envelope = self.read(row['job_id']+'.result.json')
        if envelope is None:
            return None
        if not isinstance(envelope, dict):
            raise StoreError('RESULT_ARTIFACT_CORRUPT')
        raw = encode(envelope.get('result'))
        if (envelope.get('schema') != 'wealthpilot.position-artifact.v1'
                or envelope.get('job_id') != row['job_id']
                or envelope.get('analysis_run_id') != row['analysis_run_id']
                or envelope.get('fingerprint') != row['fingerprint']
                or envelope.get('result_schema') != 'wealthpilot.analysis.v1'
                or envelope.get('result_bytes') != len(raw)
                or envelope.get('result_sha256') != hashlib.sha256(raw).hexdigest()
                or (row.get('artifact_sha256') is not None and row['artifact_sha256']!=envelope.get('result_sha256'))
                or (row.get('artifact_bytes') is not None and row['artifact_bytes']!=len(raw))
                or (row.get('state')=='completed' and (row['finished']!=envelope.get('finished_at') or
                    row['business_status']!=envelope.get('result',{}).get('status')))
                or not isinstance(envelope.get('finished_at'),(int,float)) or not math.isfinite(envelope['finished_at'])):
            raise StoreError('RESULT_ARTIFACT_CORRUPT')
        return envelope
