"""Historical PDD 7.6 metadata, genuine durable IO, synthetic report bytes only.
No model, Provider, Worker or production Job creation. Production artifact is not local.
"""
import json
import os
import threading
import time
from hashlib import sha256
import pytest
from backend.services.research_evaluation import SampleStore, DIMENSIONS
from pocs.chatgpt_skills.position_store import PositionStore, encode, StoreError
from pocs.chatgpt_skills.position_jobs import PositionJobs
# The pinned MCP runtime also installs a third-party 'scripts' package.
# Load the exact repository CLI, not an ambient package with the same name.
import importlib.util
from pathlib import Path
_spec=importlib.util.spec_from_file_location('wp_history_import_contract',Path(__file__).resolve().parents[1]/'scripts/import_research_history.py')
_importer=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_importer)
import_reference=_importer.import_reference
from tests.test_position_jobs import original_result

JOB='isolated_pdd_76_job_'+'j'*24
RUN='7'*32
PRODUCTION_SHA='e'*64  # Actual production pin is exercised only in the private replay receipt.


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('NO_NETWORK'))


def durable(tmp_path):
    root=tmp_path/'jobs';root.mkdir(mode=0o700)
    store=PositionStore(root,64*1024*1024)
    row=dict(job_id=JOB,analysis_run_id=RUN,fingerprint=sha256(b'ISOLATED_DURABLE_RECEIPT_NOT_PRODUCTION_REQUEST').hexdigest(),
        state='running',issue=None,created=time.time()-120,finished=None,business_status='UNKNOWN',
        request_key=sha256(b'ISOLATED_ONLY').hexdigest(),owner_scope='isolation',owner_pid=os.getpid(),source_revision='a'*40)
    result=original_result().model_copy(deep=True);result.evidence['position_evidence']['symbol']='PDD:US'
    with store.locked():
        receipts=store.receipts()
        envelope=store.save_result(row,result,time.time()-60)
        row.update(state='completed',finished=envelope['finished_at'],business_status='partial',artifact_sha256=envelope['result_sha256'],artifact_bytes=envelope['result_bytes'])
        receipts['jobs'][JOB]=row;receipts['requests'][row['request_key']]=JOB;store.save_receipts(receipts)
    store.close()
    record=dict(symbol='PDD:US',job_id=JOB,analysis_run_id=RUN,artifact_sha256=envelope['result_sha256'],
        source_record_sha256=sha256(b'PDD_7.6_USER_METADATA_WITH_ISOLATED_REPORT_HASH').hexdigest(),
        assessment_reference=dict(reported_total='7.6',dimensions=dict(zip(DIMENSIONS,['1.8','1.4','1.0','1.8','1.6'])),
                                  source='User historical 7.6 metadata; report bytes are explicit synthetic fixture'))
    def forbidden(*a):pytest.fail('RECOVERY_MUST_NOT_EXECUTE_RESEARCH')
    jobs=PositionJobs(forbidden,threading.Lock(),storage_root=root,quota_bytes=64*1024*1024,retention_seconds=7200)
    return root,row,envelope,record,jobs


@pytest.mark.parametrize('order',['import_first','recover_first'])
def test_bidirectional_historical_job_linkage_without_rewriting_request(tmp_path,order):
    root,row,envelope,record,jobs=durable(tmp_path);samples=SampleStore(root)
    sid=samples.sample_id(JOB)
    try:
        if order=='import_first':
            assert import_reference(samples,record)==sid
            before=(samples.root/f'sample-{sid}.json').read_bytes()
            assert jobs.get(JOB).state=='completed'
        else:
            assert jobs.get(JOB).state=='completed'
            before=(samples.root/f'sample-{sid}.json').read_bytes()
            assert import_reference(samples,record)==sid
        for _ in range(2):
            assert jobs.get(JOB).state=='completed'
            assert import_reference(samples,record)==sid
        page=samples.query(job_id=JOB,export=True);assert page['total_samples']==1
        item=page['items'][0]
        assert item['state']['reports']['reader']['available']
        assert item['state']['artifact']['sha256']==envelope['result_sha256']
        assert item['sample']['question'] is None and item['sample']['request'] is None
        if order=='import_first':assert item['sample']['request_fingerprint'] is None
        assert (samples.root/f'sample-{sid}.json').read_bytes()==before
        assert page['historical_reference_distribution']=={'FAIL':1}
        assert page['distribution']=={} and item['evaluation_status']=='UNEVALUATED'
        jobs.retention_seconds=1
        assert jobs.get(JOB).issue=='RESULT_EXPIRED'
    finally:samples.close()
    restarted=SampleStore(root)
    try:
        item=restarted.query(job_id=JOB,export=True)['items'][0]
        assert item['state']['reports']['reader']['available']
        assert len(list(restarted.root.glob('sample-*.json')))==1
        assert item['evaluation_status']=='UNEVALUATED'
    finally:restarted.close()


@pytest.mark.parametrize('field,bad',[('analysis_run_id','b'*32),('artifact_sha256','f'*64),('symbol','LI:US'),('artifact_id','different-artifact')])
@pytest.mark.parametrize('order',['import_first','recover_first'])
def test_known_identity_conflicts_never_link_or_replace_reports(tmp_path,field,bad,order):
    root,row,envelope,record,jobs=durable(tmp_path);s=SampleStore(root)
    try:
        if order=='import_first':
            altered=dict(record,**{field:bad})
            if field=='artifact_id':
                with pytest.raises(ValueError):import_reference(s,altered)
                assert s.query()['total_samples']==0
                return
            import_reference(s,altered)
            before=s.query(export=True)['items'][0]['state']
            with pytest.raises(StoreError):s.capture(row,envelope=envelope)
            assert s.query(export=True)['items'][0]['state']==before
        else:
            jobs.get(JOB)
            before=s.query(export=True)['items'][0]['state']
            with pytest.raises((StoreError,ValueError)):import_reference(s,dict(record,**{field:bad}))
            assert s.query(export=True)['items'][0]['state']==before
            assert s.query()['items'][0]['historical_references']==[]
        assert s.query()['total_samples']==1
    finally:s.close()


def test_real_production_metadata_pin_cannot_be_satisfied_by_synthetic_report(tmp_path):
    root,row,envelope,record,jobs=durable(tmp_path);s=SampleStore(root)
    try:
        assert envelope['result_sha256']!=PRODUCTION_SHA
        import_reference(s,dict(record,artifact_sha256=PRODUCTION_SHA))
        with pytest.raises(StoreError):s.capture(row,envelope=envelope)
        item=s.query(export=True)['items'][0]
        assert item['state']['artifact']['sha256']==PRODUCTION_SHA
        assert item['state']['import_completeness']=='METADATA_ONLY'
        assert not item['state']['reports']
        assert item['sample']['request_fingerprint'] is None
    finally:s.close()


@pytest.mark.parametrize('tamper',['fingerprint','missing_fingerprint','result','envelope_identity','missing'])
def test_unknown_fingerprint_requires_real_durable_artifact_not_supplied_envelope(tmp_path,tamper):
    root,row,envelope,record,jobs=durable(tmp_path);s=SampleStore(root)
    import_reference(s,record)
    supplied=json.loads(json.dumps(envelope));claimed=dict(row)
    if tamper=='fingerprint':
        claimed['fingerprint']='d'*64;supplied['fingerprint']='d'*64
    elif tamper=='missing_fingerprint':
        claimed['fingerprint']=None;supplied['fingerprint']=None
    elif tamper=='result':
        supplied['result']['conclusion']='FORGED'
        raw=encode(supplied['result']);supplied['result_sha256']=sha256(raw).hexdigest();supplied['result_bytes']=len(raw)
    elif tamper=='envelope_identity':supplied['analysis_run_id']='b'*32
    else:(root/(JOB+'.result.json')).unlink()
    try:
        with pytest.raises((StoreError,OSError)):s.capture(claimed,envelope=supplied)
        assert s.query()['items'][0]['state']['import_completeness']=='METADATA_ONLY'
    finally:s.close()


def test_original_legacy_import_without_separate_history_record_recovers_once(tmp_path):
    root,row,envelope,record,jobs=durable(tmp_path);s=SampleStore(root)
    sid=import_reference(s,record)
    for path in s.root.glob('history-*.json'):path.unlink()  # explicit old-format fixture
    original=(s.root/f'sample-{sid}.json').read_bytes()
    try:
        assert jobs.get(JOB).state=='completed'
        assert import_reference(s,record)==sid
        assert jobs.get(JOB).state=='completed'
        item=s.query(export=True)['items'][0]
        assert len(item['historical_references'])==1
        assert item['state']['durable_identity']['fingerprint']==row['fingerprint']
        assert (s.root/f'sample-{sid}.json').read_bytes()==original
        assert item['sample']['request_fingerprint'] is None
        assert item['sample']['question'] is None
        with pytest.raises(ValueError,match='CONFLICT'):
            import_reference(s,dict(record,assessment_reference=dict(record['assessment_reference'],source='changed same source hash')))
    finally:s.close()


def test_corrupt_original_request_index_never_becomes_verified_identity(tmp_path):
    root,row,envelope,record,jobs=durable(tmp_path);s=SampleStore(root)
    import_reference(s,record)
    path=root/'receipts.json';data=json.loads(path.read_text());data['requests']={}
    path.write_text(json.dumps(data));before=path.read_bytes()
    try:
        with pytest.raises(StoreError,match='RECEIPTS_CORRUPT'):s.capture(row,envelope=envelope)
        assert path.read_bytes()==before
        assert 'durable_identity' not in s.query()['items'][0]['state']
    finally:s.close()


@pytest.mark.parametrize('order',['import_first','recover_first'])
def test_historical_score_and_new_evaluation_remain_distinct_and_immutable(tmp_path,order):
    from tests.test_research_evaluation import evaluation
    root,row,envelope,record,jobs=durable(tmp_path);s=SampleStore(root)
    try:
        sid=s.sample_id(JOB)
        if order=='import_first':import_reference(s,record)
        jobs.get(JOB)
        if order=='recover_first':import_reference(s,record)
        page=s.query(export=True);report=page['items'][0]['state']['reports']['native']
        scored=s.evaluate(sid,evaluation(report['sha256']))
        path=s.root/f"evaluation-{scored['evaluation_id']}.json";original=path.read_bytes()
        assert import_reference(s,record)==sid
        assert jobs.get(JOB).state=='completed'
        page=s.query(export=True)
        assert page['distribution']=={'native/ai/FAIL':1}
        assert page['historical_reference_distribution']=={'FAIL':1}
        assert len(page['items'][0]['historical_references'])==1
        assert page['items'][0]['historical_references'][0]['input']['historical_assessment_reference']['reported_total']=='7.6'
        assert path.read_bytes()==original
    finally:s.close()


@pytest.mark.parametrize('alias',['NASDAQ:PDD','PDD','PDD:US'])
def test_automatic_capture_alias_is_not_subject_to_historical_symbol_gate(tmp_path,alias):
    from pocs.chatgpt_skills.analysis import AnalysisRequest
    root,row,envelope,record,jobs=durable(tmp_path);s=SampleStore(root)
    try:
        sid=s.capture(dict(row,state='running'),request=AnalysisRequest(question='Research',symbol=alias))
        assert s.capture(row,envelope=envelope)==sid
        assert s.query(export=True)['items'][0]['state']['reports']['reader']['available']
    finally:s.close()


def test_committed_original_and_incomplete_sample_still_reject_wrong_import_hash(tmp_path):
    root,row,envelope,record,jobs=durable(tmp_path);s=SampleStore(root)
    try:
        s.capture(dict(row,state='running'))
        with pytest.raises(StoreError):import_reference(s,dict(record,artifact_sha256='f'*64))
        assert not list(s.root.glob('history-*.json'))
        import_reference(s,record)
        assert jobs.get(JOB).state=='completed'
        history=s.query(export=True)['items'][0]['historical_references'][0]
        assert history['input']['artifact_reference']['sha256']==envelope['result_sha256']
    finally:s.close()


@pytest.mark.parametrize('order',['import_first','recover_first'])
def test_missing_historical_run_and_symbol_remain_unknown_not_fabricated(tmp_path,order):
    root,row,envelope,record,jobs=durable(tmp_path);s=SampleStore(root)
    record=dict(record,symbol='UNKNOWN',analysis_run_id='UNKNOWN')
    try:
        if order=='import_first':import_reference(s,record)
        jobs.get(JOB)
        if order=='recover_first':import_reference(s,record)
        item=s.query(export=True)['items'][0]
        assert item['state']['reports']['reader']['available']
        if order=='import_first':
            assert item['sample']['symbol']=='UNKNOWN' and item['sample']['analysis_run_id']=='UNKNOWN'
            assert item['state']['symbol']=='PDD:US'
            assert item['state']['durable_identity']['analysis_run_id']==RUN
    finally:s.close()
