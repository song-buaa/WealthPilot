"""Explicit research consent, frozen inputs, atomic writes and durable deduplication."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sqlite3

import pytest

from backend.services.investment_thesis.bridge import ResearchBridge
from backend.services.investment_thesis.store import ThesisStore, digest

EXAMPLES=Path(__file__).resolve().parents[1]/'docs/v4.3/examples/e1'
def sample(name):return json.loads((EXAMPLES/(name+'.json')).read_text())

@pytest.fixture
def setup(tmp_path):
    store=ThesisStore(tmp_path/'private'/'research.db',7);store.init()
    store.save(sample('meituan-v1'),0,'v1');store.save(sample('meituan-v2'),1,'v2')
    return store,ResearchBridge(store)

def request(**kw):
    raw=sample('meituan-review');raw.pop('thesis_revision');raw['requested_verdict']='SUPPORT'
    return dict(operation='REVIEW',record=raw,analysis=sample('peer-result'),analysis_source='USER_SUPPLIED_ANALYSIS_COPY',**kw)

def commit(bridge,preview,**kw):
    return bridge.commit(preview['preview_token'],preview['preview_fingerprint'],confirmed=True,confirmation='保存这次判断',**kw)

def test_no_consent_cancel_expiry_capacity_and_content_tampering_do_not_write(setup):
    store,bridge=setup;before=store.path.read_bytes();p=bridge.preview(request())
    assert not p['saved'] and p['summary']['judgment']=='UNKNOWN'
    assert p['summary']['evidence_by_relation']['supports'] and p['summary']['evidence_by_relation']['opposes']
    assert p['summary']['source_kinds'] and p['summary']['known_at_qualification']
    assert p['summary']['original_analysis_status']=='partial'
    assert p['summary']['exposure']['status']=='CANNOT_QUANTIFY'
    for kwargs in [{},{'confirmed':True},{'confirmed':True,'confirmation':'研究完成'}]:
        with pytest.raises(ValueError,match='EXPLICIT_CONFIRMATION'):
            bridge.commit(p['preview_token'],p['preview_fingerprint'],**kwargs)
    with pytest.raises(ValueError,match='PREVIEW_CHANGED'):
        bridge.commit(p['preview_token'],'changed',confirmed=True,confirmation='保存这次判断')
    x=bridge.commit(p['preview_token'],p['preview_fingerprint'],confirmation='取消');assert x['cancelled']
    with pytest.raises(ValueError,match='EXPIRED'):commit(bridge,p)
    tick=[0.];short=ResearchBridge(store,ttl_seconds=2,capacity=1,clock=lambda:tick[0]);p=short.preview(request())
    with pytest.raises(ValueError,match='CAPACITY'):short.preview(request())
    tick[0]=3
    with pytest.raises(ValueError,match='EXPIRED'):commit(short,p)
    assert store.path.read_bytes()==before and store.reviews('synthetic-meituan')==[]

def test_duplicate_confirmation_and_new_bridge_repreview_use_same_persisted_result(setup):
    store,bridge=setup;p=bridge.preview(request());first=commit(bridge,p);assert first['saved']
    assert commit(bridge,p)==first
    saved=store.reviews('synthetic-meituan')[0]
    assert saved['analysis']==sample('peer-result')
    assert saved['analysis_provenance']=='USER_SUPPLIED_ANALYSIS_COPY'
    assert saved['effective_verdict']=='UNKNOWN' and not saved['execution_authorized']
    # Same explicit ID with a different body must fail, not create a second review.
    changed=request(request_id=p['request_id']);changed['record']['rationale']+=' changed'
    with pytest.raises(ValueError,match='REQUEST_ID_CONFLICT'):bridge.preview(changed)
    restarted=ResearchBridge(store)
    with pytest.raises(ValueError,match='EXPIRED'):commit(restarted,p)
    assert len(store.reviews('synthetic-meituan'))==1


def test_thesis_idempotency_atomic_cas_and_concurrent_confirmation(setup):
    store,bridge=setup;raw=sample('meituan-v2');raw['user_reflection']='new contrary evidence, keep original memory'
    req=dict(operation='THESIS',record=raw)
    p=bridge.preview(req);other=ResearchBridge(store);q=other.preview(req)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a,b=list(pool.map(lambda pair:commit(*pair),[(bridge,p),(other,q)]))
    assert a['revision']==b['revision']==3 and len(store.history('synthetic-meituan'))==3
    assert store.get('synthetic-meituan',1)['content']['user_original_judgment']==raw['user_original_judgment']
    # A distinct stale draft must be refused after the head advances.
    raw['user_reflection']='next';stale=bridge.preview(dict(operation='THESIS',record=raw))
    alternative=deepcopy(raw);alternative['user_reflection']='different';store.save(alternative,3,'concurrent')
    with pytest.raises(ValueError,match='REVISION_CONFLICT'):commit(bridge,stale)


def test_review_conflicts_on_thesis_or_previous_review_change(setup):
    store,bridge=setup;p=bridge.preview(request());q=bridge.preview(request(request_id='other-review'))
    commit(bridge,p)
    with pytest.raises(ValueError,match='REVIEW_CONTEXT_CHANGED'):commit(bridge,q)
    p=bridge.preview(request(request_id='new-draft'));v3=sample('meituan-v2');v3['user_reflection']='new';store.save(v3,2,'v3')
    with pytest.raises(ValueError,match='REVISION_CONFLICT'):commit(bridge,p)


def test_save_failure_rolls_back_both_review_events_and_readback_failure_is_not_success(setup,monkeypatch):
    store,bridge=setup;p=bridge.preview(request());before=store.path.read_bytes()
    original=store._insert_review
    def fail(db,payload,request_id,fingerprint,index):
        if index==1:raise sqlite3.OperationalError('offline injected failure')
        return original(db,payload,request_id,fingerprint,index)
    monkeypatch.setattr(store,'_insert_review',fail)
    with pytest.raises(sqlite3.OperationalError):commit(bridge,p)
    assert store.path.read_bytes()==before and store.reviews('synthetic-meituan')==[]
    monkeypatch.setattr(store,'_insert_review',original)
    read=store.reviews
    monkeypatch.setattr(store,'reviews',lambda *a,**k:(_ for _ in ()).throw(OSError('readback failed')))
    with pytest.raises(OSError):commit(bridge,p)
    assert len(read('synthetic-meituan'))==1
    monkeypatch.setattr(store,'reviews',read)
    assert commit(bridge,p)['saved'] and len(read('synthetic-meituan'))==1


def test_scope_schema_copy_source_and_unknown_issuer_fail_closed(setup):
    store,bridge=setup
    with pytest.raises(ValueError):bridge.preview(dict(request(),portfolio_scope=8))
    with pytest.raises(ValueError):bridge.preview(dict(request(),database='/tmp/any.db'))
    with pytest.raises(ValueError):bridge.preview(dict(request(),analysis_source='ORIGINAL_WORKER_RESULT'))
    wrong=ResearchBridge(ThesisStore(store.path,8))
    with pytest.raises(ValueError,match='NOT_FOUND'):wrong.preview(request())
    raw=sample('nvda-v1');raw['security']['identity_reference']=None;raw['security']['identity_gap']='unmapped';store.save(raw,0,'nvda')
    req=request();req['record'].update(thesis_id=raw['thesis_id'],evidence_ids=[],price_evidence_ids=[])
    p=bridge.preview(req)
    assert 'SECURITY_IDENTITY_UNRESOLVED' in p['evaluation']['verdict_restrictions']
    assert p['summary']['judgment']=='UNKNOWN'
    unavailable=deepcopy(req);unavailable['analysis']['status']='unavailable'
    p=bridge.preview(unavailable);assert p['summary']['judgment']=='UNKNOWN'
    assert p['summary']['original_analysis_status']=='unavailable'
