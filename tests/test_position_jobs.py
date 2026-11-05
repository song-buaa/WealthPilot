"""Offline durable task contract. No model/Provider calls."""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from pocs.chatgpt_skills.position_jobs import PositionJobs, report_projection
from pocs.chatgpt_skills.position_store import PositionStore, StoreError, encode
from pocs.chatgpt_skills.analysis import AnalysisRequest, AnalysisResult


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:(_ for _ in ()).throw(AssertionError('NETWORK_FORBIDDEN')))


def original_result(status='partial',bulk=0):
    return AnalysisResult(synthetic=True,scenario='analyze_position',status=status,queried_at='2026-10-27T00:00:00Z',
        issues=['SYNTHETIC_ONLY'],conclusion='完整专业结论',professional_analysis_complete=status!='unavailable',
        professional_result={'analysis_result':{'professional_explanation':[{'text':'完整专业解释','sources':[{'evidence_ref':'real-qualified-ref'}]}],
            'conditionalValuation':{'scenarios':{'base':{'return':'.12'}},'assumptions':{'growth':'.03'},'sensitivity':[1,2]},'policy_reference':{'version':1}}},
        evidence={'position_evidence':{'records':[{'bulk':'x'*bulk}],'qualification':{'L3':False}}},
        limitations=['UNKNOWN: no valuation certainty'],scope={'readonly':True})


def request():return AnalysisRequest(question='PRIVATE_QUESTION',symbol='LI:US')


def factory(root,execute=lambda *a:original_result(),**kw):
    root.mkdir(mode=0o700,exist_ok=True)
    return PositionJobs(execute,threading.Lock(),storage_root=root,quota_bytes=kw.pop('quota_bytes',64*1024*1024),**kw)


def finish(jobs,job):
    for _ in range(400):
        r=jobs.get(job)
        if r.state!='running':return r
        time.sleep(.005)
    raise AssertionError('job did not finish')


@pytest.mark.parametrize('status',['ok','partial','unavailable'])
def test_fast_start_full_artifact_report_and_restart(tmp_path,status):
    calls=[];gate=threading.Event();root=tmp_path/'store';full=original_result(status,3*1024*1024)
    def execute(*a):calls.append(1);assert gate.wait(2);return full
    jobs=factory(root,execute)
    started=time.monotonic();first=jobs.start('client-id',request())
    assert first.state=='running' and time.monotonic()-started<.5
    assert jobs.get(first.job_id).state=='running'
    gate.set();done=finish(jobs,first.job_id)
    assert done.state=='completed' and done.business_status==status
    assert done.schema_version.endswith('.v2') and done.result.schema_version=='wealthpilot.analysis-report.v1'
    assert done.result.conclusion==full.conclusion and done.result.professional_result==full.professional_result
    assert done.artifact.bytes==len(encode(full.model_dump(mode='json')))
    assert len(encode(done.model_dump(mode='json')))<10000
    stored=json.loads((root/(first.job_id+'.result.json')).read_text())
    assert stored['result']==full.model_dump(mode='json')
    after=factory(root,lambda *a:pytest.fail('must not replay'))
    assert after.get(first.job_id).result==done.result
    assert after.start('client-id',request()).job_id==first.job_id
    assert after.start('client-id',request().model_copy(update={'symbol':'PDD:US'})).issue=='REQUEST_ID_CONFLICT'
    assert len(calls)==1 and all((p.stat().st_mode&0o777)==0o600 for p in root.iterdir())


def test_multiple_instances_concurrently_start_one_task(tmp_path):
    root=tmp_path/'store';gate=threading.Event();calls=[]
    def execute(*a):calls.append(1);assert gate.wait(2);return original_result()
    instances=[factory(root,execute) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        starts=list(pool.map(lambda j:j.start('same-id',request()),instances))
    assert len({s.job_id for s in starts})==1, [(s.issue,s.state) for s in starts]
    assert instances[1].start('other-id',request()).issue=='ANALYSIS_BUSY'
    gate.set();finish(instances[0],starts[0].job_id);assert calls==[1]


def test_expiry_retains_artifact_and_idempotency(tmp_path,monkeypatch):
    jobs=factory(tmp_path/'store',retention_seconds=10)
    first=jobs.start('same',request());finish(jobs,first.job_id)
    monkeypatch.setattr('pocs.chatgpt_skills.position_jobs.time.time',lambda:time.time_ns()/1e9+100)
    expired=jobs.get(first.job_id)
    assert expired.issue=='RESULT_EXPIRED' and expired.result is None and expired.artifact
    assert jobs.start('same',request()).job_id==first.job_id


@pytest.mark.parametrize('failure',['worker','invalid','serialization','disk'])
def test_failures_never_replay_or_fake_completion(tmp_path,failure,monkeypatch):
    def execute(*a):
        if failure=='worker':raise TimeoutError('PRIVATE')
        if failure=='invalid':return {'private':'PRIVATE'}
        value=original_result()
        if failure=='serialization':value.evidence={'bad':float('nan')}
        return value
    if failure=='disk':monkeypatch.setattr(PositionStore,'save_result',lambda *a:(_ for _ in ()).throw(OSError('PRIVATE')))
    jobs=factory(tmp_path/'store',execute);first=jobs.start('id',request());done=finish(jobs,first.job_id)
    expected={'worker':'ANALYSIS_WORKER_FAILED','invalid':'ANALYSIS_RESULT_INVALID','serialization':'RESULT_SERIALIZATION_FAILED','disk':'RESULT_STORAGE_UNAVAILABLE'}
    assert done.issue==expected[failure] and done.result is None
    assert jobs.start('id',request()).job_id==first.job_id
    assert 'PRIVATE' not in done.model_dump_json()


def test_quota_and_unconfigured_store_refuse_without_execute(tmp_path):
    calls=[];jobs=factory(tmp_path/'store',lambda *a:calls.append(1),quota_bytes=1)
    assert jobs.start('id',request()).issue=='RESULT_STORAGE_QUOTA' and not calls
    assert PositionJobs(lambda *a:calls.append(1),threading.Lock()).start('id',request()).issue=='RESULT_STORAGE_UNCONFIGURED'


@pytest.mark.parametrize('attack',['root_mode','symlink','file_mode','hash','missing_index'])
def test_storage_attacks_fail_closed(tmp_path,attack):
    root=tmp_path/'store';jobs=factory(root);first=jobs.start('id',request());finish(jobs,first.job_id)
    artifact=root/(first.job_id+'.result.json')
    if attack=='root_mode':root.chmod(0o755)
    if attack=='symlink':artifact.unlink();artifact.symlink_to(tmp_path/'outside')
    if attack=='file_mode':artifact.chmod(0o644)
    if attack=='hash':
        data=json.loads(artifact.read_text());data['result']['conclusion']='tampered';artifact.write_text(json.dumps(data))
    if attack=='missing_index':(root/'receipts.json').unlink()
    r=jobs.get(first.job_id)
    assert r.state=='unavailable' and r.result is None
    assert jobs.start('id',request()).state=='unavailable'


def test_shared_sync_slot_refuses_no_claim(tmp_path):
    slot=threading.Lock();slot.acquire()
    (tmp_path/'store').mkdir(mode=0o700)
    jobs=PositionJobs(lambda *a:pytest.fail('called'),slot,storage_root=tmp_path/'store',quota_bytes=100000)
    assert jobs.start('id',request()).issue=='ANALYSIS_BUSY'
    slot.release()


@pytest.mark.parametrize('identifier',['','x'*129,'with space','非ASCII',None])
def test_invalid_request_ids_do_not_execute(tmp_path,identifier):
    jobs=factory(tmp_path/'store',lambda *a:pytest.fail('called'))
    assert jobs.start(identifier,request()).issue=='REQUEST_ID_INVALID'


def test_closed_storage_event_is_private_and_persistent(tmp_path):
    import mcp.server
    if not hasattr(mcp.server,"MCPServer"):pytest.skip("requires separate MCP 2.3 protocol environment")
    from pocs.poc02_wealth_mcp.server import record_position_storage_event
    events=tmp_path/'events.jsonl'
    jobs=factory(tmp_path/'store',event=lambda value:record_position_storage_event(SimpleNamespace(events=events),value|{'prompt':'PRIVATE'}))
    first=jobs.start('id',request());finish(jobs,first.job_id)
    rows=[json.loads(s) for s in events.read_text().splitlines()]
    assert {r['storage_status'] for r in rows}=={'serialization_completed','committed'}
    assert all(type(r['result_bytes']) is int and r['job_id']==first.job_id for r in rows)
    assert 'PRIVATE' not in events.read_text()


def test_private_frozen_pdd_has_exact_full_closure(tmp_path):
    path=os.getenv('WEALTHPILOT_TEST_FROZEN_PDD')
    if not path:pytest.skip('opt-in existing private development artifact only')
    original=json.loads(open(path).read());full=AnalysisResult.model_validate(original)
    assert len(encode(full.model_dump(mode='json')))==2207475
    jobs=factory(tmp_path/'store',lambda *a:full);first=jobs.start('frozen-pdd',request());done=finish(jobs,first.job_id)
    assert done.state=='completed' and done.artifact.bytes==2207475
    stored=json.loads((tmp_path/'store'/(first.job_id+'.result.json')).read_text())
    assert stored['result']==full.model_dump(mode='json')
    assert done.result.professional_result['analysis_result']['professional_explanation']==full.professional_result['analysis_result']['professional_explanation']
    assert done.result.professional_result['analysis_result']['conditionalValuation']==full.professional_result['analysis_result']['conditionalValuation']
    assert done.result.conclusion==full.conclusion
    assert len(encode(done.model_dump(mode='json')))<2207475


def _child(root, mode):
    root.mkdir(mode=0o700,exist_ok=True)
    import subprocess,sys
    script='''
import os,time,threading
from pathlib import Path
from pocs.chatgpt_skills.position_jobs import PositionJobs
from pocs.chatgpt_skills.position_store import PositionStore
from tests.test_position_jobs import original_result,request
root=Path(os.environ['TEST_STORE'])
mode=os.environ['TEST_CRASH']
if mode=='artifact':
    original=PositionStore.save_result
    def save(*args,**kwargs):
        original(*args,**kwargs)
        os._exit(23)
    PositionStore.save_result=save
if mode=='claimed':
    original=threading.Thread.start
    threading.Thread.start=lambda *args,**kwargs:os._exit(24)
def execute(*args):
    fd=os.open(str(root.parent/'execute-count'),os.O_CREAT|os.O_APPEND|os.O_WRONLY,0o600)
    os.write(fd,b'call\\n');os.close(fd)
    time.sleep(.05)
    return original_result()
j=PositionJobs(execute,threading.Lock(),storage_root=root,quota_bytes=10000000)
r=j.start('durable-request',request())
for _ in range(500):
    value=j.get(r.job_id)
    if value.state!='running':break
    time.sleep(.01)
'''
    return subprocess.Popen([sys.executable,'-c',script],env={**os.environ,'TEST_STORE':str(root),'TEST_CRASH':mode,'PYTHON_DOTENV_DISABLED':'1'},stdout=subprocess.PIPE,stderr=subprocess.PIPE)


@pytest.mark.parametrize('crash',['claimed','artifact'])
def test_real_process_crash_windows_never_reexecute(tmp_path,crash):
    root=tmp_path/'store';child=_child(root,crash)
    child.communicate(timeout=15)
    assert child.returncode in (23,24)
    receipt=json.loads((root/'receipts.json').read_text());row=next(iter(receipt['jobs'].values()))
    after=factory(root,lambda *a:pytest.fail('replayed after crash'))
    result=after.get(row['job_id'])
    assert result.state==('completed' if crash=='artifact' else 'unavailable')
    assert (result.issue is None) if crash=='artifact' else result.issue=='ANALYSIS_INTERRUPTED'
    assert after.start('durable-request',request()).job_id==row['job_id']
    count=tmp_path/'execute-count'
    assert (count.read_text().count('call') if count.exists() else 0)==(1 if crash=='artifact' else 0)


def test_real_process_concurrent_requests_and_completed_restart(tmp_path):
    root=tmp_path/'store'
    children=[_child(root,'normal') for _ in range(3)]
    for child in children:
        out,err=child.communicate(timeout=20)
        assert child.returncode==0,err.decode()[-1000:]
    assert (tmp_path/'execute-count').read_text().splitlines()==['call']
    receipt=json.loads((root/'receipts.json').read_text());assert len(receipt['jobs'])==1
    job=next(iter(receipt['jobs']))
    after=factory(root,lambda *a:pytest.fail('replayed'))
    assert after.get(job).state=='completed'
    assert after.start('durable-request',request()).job_id==job


def test_result_quota_exhaustion_retains_claim_and_actual_bytes(tmp_path):
    calls=[];events=[]
    def execute(*a):calls.append(1);return original_result(bulk=100000)
    jobs=factory(tmp_path/'store',execute,quota_bytes=20000,event=events.append)
    first=jobs.start('id',request());done=finish(jobs,first.job_id)
    assert done.issue=='RESULT_STORAGE_QUOTA' and done.job_id==first.job_id
    assert done.business_status=='partial' and done.result is None
    assert events[-1]['storage_status']=='RESULT_STORAGE_QUOTA'
    assert events[-1]['result_bytes']>100000 and len(events[-1]['artifact_sha256'])==64
    assert jobs.start('id',request()).job_id==first.job_id and calls==[1]


@pytest.mark.parametrize('category,expected',[
    ('ipc_timeout','ANALYSIS_IPC_TIMEOUT'),('worker_exited','ANALYSIS_WORKER_EXITED'),
    ('worker_start_failed','ANALYSIS_WORKER_START_FAILED'),('invalid_json','ANALYSIS_JSON_INVALID'),
    ('validation_failed','ANALYSIS_RESULT_INVALID')])
def test_original_worker_diagnostics_remain_distinct(tmp_path,category,expected):
    from pocs.chatgpt_skills.diagnostics import diagnostic
    def execute(req,identifier):
        error=RuntimeError('PRIVATE')
        error.analysis_diagnostic=diagnostic(identifier,'ipc_wait',category=category,protocol=True)
        raise error
    jobs=factory(tmp_path/'store',execute);first=jobs.start('id',request())
    assert finish(jobs,first.job_id).issue==expected


def test_corrupt_receipt_cannot_inject_response_or_restart(tmp_path):
    root=tmp_path/'store';jobs=factory(root);first=jobs.start('id',request());finish(jobs,first.job_id)
    data=json.loads((root/'receipts.json').read_text());data['jobs'][first.job_id]['issue']='PRIVATE'
    (root/'receipts.json').write_text(json.dumps(data))
    assert jobs.get(first.job_id).issue=='RESULT_RECEIPTS_CORRUPT'
    assert jobs.start('different',request()).issue=='RESULT_RECEIPTS_CORRUPT'


def test_private_root_cannot_be_inside_git_checkout(tmp_path):
    root=tmp_path/'checkout';root.mkdir();(root/'.git').mkdir()
    assert factory(root/'private').start('id',request()).issue=='RESULT_STORAGE_UNSAFE'


def test_missing_request_mapping_is_corruption_not_permission_to_reexecute(tmp_path):
    root=tmp_path/'store';calls=[]
    jobs=factory(root,lambda *a:(calls.append(1),original_result())[1])
    first=jobs.start('same',request());finish(jobs,first.job_id)
    data=json.loads((root/'receipts.json').read_text());data['requests'].clear()
    (root/'receipts.json').write_text(json.dumps(data))
    assert jobs.start('same',request()).issue=='RESULT_RECEIPTS_CORRUPT' and calls==[1]


def test_deleted_root_is_not_recreated_by_query_or_start(tmp_path):
    import shutil
    root=tmp_path/'store';jobs=factory(root);first=jobs.start('id',request());finish(jobs,first.job_id)
    shutil.rmtree(root)
    assert jobs.get(first.job_id).issue=='RESULT_STORAGE_UNAVAILABLE'
    assert jobs.start('id',request()).issue=='RESULT_STORAGE_UNAVAILABLE' and not root.exists()


def test_valid_non_json_python_types_keep_original_pydantic_encoder(tmp_path):
    from datetime import datetime,timezone
    full=original_result();full.evidence={'captured_at':datetime(2026,1,1,tzinfo=timezone.utc)}
    jobs=factory(tmp_path/'store',lambda *a:full);first=jobs.start('id',request())
    done=finish(jobs,first.job_id)
    assert done.state=='completed'
    stored=json.loads((tmp_path/'store'/(first.job_id+'.result.json')).read_text())
    assert stored['result']==full.model_dump(mode='json')


@pytest.mark.parametrize('attack',['missing_hash','wrong_size','wrong_finish'])
def test_completed_receipt_must_bind_original_artifact(tmp_path,attack):
    root=tmp_path/'store';jobs=factory(root);first=jobs.start('id',request());finish(jobs,first.job_id)
    data=json.loads((root/'receipts.json').read_text());row=data['jobs'][first.job_id]
    if attack=='missing_hash':row.pop('artifact_sha256')
    if attack=='wrong_size':row['artifact_bytes']+=1
    if attack=='wrong_finish':row['finished']+=1
    (root/'receipts.json').write_text(json.dumps(data))
    assert jobs.get(first.job_id).state=='unavailable'
    assert jobs.start('id',request()).state=='unavailable'


def test_symlink_parent_and_file_root_are_rejected(tmp_path):
    real=tmp_path/'real';real.mkdir(mode=0o700);(real/'store').mkdir(mode=0o700)
    link=tmp_path/'link';link.symlink_to(real,target_is_directory=True)
    calls=[]
    jobs=PositionJobs(lambda *a:calls.append(1),threading.Lock(),storage_root=link/'store',quota_bytes=100000)
    assert jobs.start('id',request()).issue=='RESULT_STORAGE_UNSAFE' and not calls
    regular=tmp_path/'file';regular.write_text('not a directory')
    jobs=PositionJobs(lambda *a:calls.append(1),threading.Lock(),storage_root=regular,quota_bytes=100000)
    assert jobs.start('id',request()).state=='unavailable' and not calls
