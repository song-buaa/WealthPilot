"""Local SYNTHETIC-only release rehearsal. No CLI/API or production activation."""
from .contracts import canonical, digest
from .parser import PolicyError
from .store import clock


def publish_synthetic(store, *, scope, bundle_id, expected_sequence, request_id, at, action='RELEASE'):
    """Refuse actual approved sources even when a caller labels them synthetic."""
    clock(at)
    if action not in {'RELEASE','ROLLBACK'} or not request_id:
        raise PolicyError('SYNTHETIC_RELEASE_REQUEST_INVALID')
    with store.connection(write=True) as db:
        bundle = store._bundle(db,scope,bundle_id)
        source = db.execute('SELECT philosophy,policy FROM policy_bundles WHERE scope=? AND bundle_id=?',(scope,bundle_id)).fetchone()
        if not all(bytes(source[k]).startswith(b'# SYNTHETIC ONLY') for k in ('philosophy','policy')):
            raise PolicyError('REAL_POLICY_ACTIVATION_FORBIDDEN')
        events = [store._read(r) for r in db.execute('SELECT * FROM policy_events WHERE scope=? AND bundle_id=?',(scope,bundle_id))]
        if not any(e['state']=='APPROVED' and e['approval_ref'] and clock(e['recorded_at'])<=clock(at) for e in events):
            raise PolicyError('STRUCTURED_APPROVAL_REQUIRED')
        releases = [store._read(r) for r in db.execute('SELECT * FROM policy_releases WHERE scope=? ORDER BY sequence',(scope,))]
        payload = dict(scope=scope,bundle_id=bundle_id,bundle_sha256=digest(bundle),request_id=request_id,
            action=action,recorded_at=at,effective_at=at,deployed_at=at,release_approval_ref='SYNTHETIC_LOCAL_TEST_ONLY',
            synthetic=True,predecessor_sequence=expected_sequence)
        prior = next((r for r in releases if r.get('request_id')==request_id),None)
        if prior:
            if prior!=payload:
                raise PolicyError('REQUEST_ID_CONFLICT')
            return prior
        if len(releases)!=expected_sequence:
            raise PolicyError('RELEASE_CAS_CONFLICT')
        if releases and clock(at)<clock(releases[-1]['recorded_at']):
            raise PolicyError('RELEASE_CLOCK_REVERSED')
        db.execute('INSERT INTO policy_releases VALUES(?,?,?,?,?)',
            (scope,expected_sequence+1,bundle_id,canonical(payload),digest(payload)))
        return payload
