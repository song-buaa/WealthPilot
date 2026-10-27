"""No-network admission, identity, retention and no-replay contracts."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pocs.chatgpt_skills.position_jobs import PositionJobs
from pocs.chatgpt_skills.analysis import AnalysisRequest, AnalysisResult


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    def reject(*args, **kwargs):
        raise AssertionError('NETWORK_FORBIDDEN')
    monkeypatch.setattr(socket.socket, 'connect', reject)


def original_result(status='partial'):
    return AnalysisResult(synthetic=True, scenario='analyze_position', status=status,
        queried_at='2026-10-27T00:00:00Z', issues=['SYNTHETIC_ONLY'], conclusion='Original PEER report',
        professional_result={'professional_explanation':[{'topic':'risk','text':'Synthetic explanation'}]},
        evidence={'records':[{'ref':'frozen-fact'}]}, data_quality={'L3':False},
        limitations=['No trades; synthetic evidence only'], scope={'readonly':True})


def request():
    return AnalysisRequest(question='PRIVATE_SYNTHETIC_QUESTION',symbol='LI:US')


def identifier(jobs, suffix='test_request_000001'):
    return jobs.scope + ':' + suffix


def finish(jobs, job_id):
    for _ in range(200):
        value = jobs.get(job_id)
        if value.state != 'running':
            return value
        time.sleep(.005)
    raise AssertionError('Synthetic job did not finish')


@pytest.mark.parametrize('status',['ok','partial','unavailable'])
def test_start_is_fast_and_get_preserves_original_result_without_execution(status):
    gate, entered = threading.Event(), threading.Event()
    calls=[]; original=original_result(status)
    def execute(req, run):
        calls.append((req,run));entered.set()
        assert gate.wait(2)
        return original
    jobs=PositionJobs(execute,threading.Lock())
    start=time.monotonic(); first=jobs.start(identifier(jobs),request())
    try:
        assert time.monotonic()-start < .5
        assert first.state=='running' and len(first.job_id)>=32 and entered.wait(1)
        for _ in range(10):
            assert jobs.get(first.job_id).state=='running'
        assert len(calls)==1 and calls[0][1]==first.analysis_run_id
    finally:gate.set()
    done=finish(jobs,first.job_id)
    assert done.state=='completed' and done.business_status==status
    assert done.result.model_dump()==original.model_dump()
    assert jobs.get(first.job_id).result.model_dump()==original.model_dump()
    assert jobs.start(identifier(jobs),request()).job_id==first.job_id
    assert len(calls)==1
    # A consumer cannot mutate the private cached result.
    done.result.professional_result.clear()
    assert jobs.get(first.job_id).result.professional_result==original.professional_result


def test_concurrent_duplicate_registration_is_exactly_one_and_conflict_refuses():
    gate=threading.Event();calls=[]
    def execute(*args):
        calls.append(1);assert gate.wait(2);return original_result()
    jobs=PositionJobs(execute,threading.Lock())
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            values=list(pool.map(lambda _:jobs.start(identifier(jobs),request()),range(16)))
        assert len({v.job_id for v in values})==1
        other=request().model_copy(update={'symbol':'PDD:US'})
        assert jobs.start(identifier(jobs),other).issue=='REQUEST_ID_CONFLICT'
        assert jobs.start(identifier(jobs,'second_request_00002'),request()).issue=='ANALYSIS_BUSY'
    finally:gate.set()
    finish(jobs,values[0].job_id)
    assert len(calls)==1


def test_old_scope_and_unknown_job_after_restart_never_replay():
    calls=[]
    def execute(*args):calls.append(1);return original_result()
    before=PositionJobs(execute,threading.Lock())
    first=before.start(identifier(before),request());finish(before,first.job_id)
    after=PositionJobs(execute,threading.Lock())
    assert before.scope!=after.scope
    assert after.get(first.job_id).issue=='JOB_UNKNOWN_OR_PROCESS_RESTARTED'
    assert after.start(identifier(before),request()).issue=='REQUEST_SCOPE_UNAVAILABLE'
    assert after.start('unscoped_request_001',request()).issue=='REQUEST_SCOPE_UNAVAILABLE'
    assert after.get('unpredictable_unknown_job_00000000000').result is None
    assert len(calls)==1


def test_expired_results_keep_idempotent_tombstone_and_do_not_restart(monkeypatch):
    from pocs.chatgpt_skills import position_jobs as module
    clock=[100.0];monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    calls=[]
    def execute(*args):calls.append(1);return original_result()
    jobs=PositionJobs(execute,threading.Lock(),retention_seconds=7200)
    first=jobs.start(identifier(jobs),request());finish(jobs,first.job_id)
    clock[0]+=7199
    assert jobs.get(first.job_id).result is not None
    clock[0]+=1
    expired=jobs.get(first.job_id)
    assert expired.state=='unavailable' and expired.issue=='RESULT_EXPIRED' and expired.result is None
    assert jobs.start(identifier(jobs),request()).job_id==first.job_id
    assert jobs.start(identifier(jobs),request()).issue=='RESULT_EXPIRED'
    assert len(calls)==1


def test_receipts_and_unexpired_results_have_separate_hard_bounds():
    calls=[]
    def execute(*args):calls.append(1);return original_result()
    jobs=PositionJobs(execute,threading.Lock(),max_receipts=1,max_results=1)
    first=jobs.start(identifier(jobs),request());finish(jobs,first.job_id)
    assert jobs.start(identifier(jobs,'new_request_0000002'),request()).issue=='JOB_CAPACITY_REACHED'
    assert jobs.start(identifier(jobs),request()).job_id==first.job_id
    assert len(calls)==1 and len(jobs.requests)==1


@pytest.mark.parametrize('failure',['ipc','invalid_result','storage'])
def test_failed_jobs_cannot_fake_success_or_replay(failure):
    calls=[];slot=threading.Lock()
    def execute(*args):
        calls.append(1)
        if failure=='ipc':raise TimeoutError('PRIVATE_ERROR_CONTEXT')
        if failure=='invalid_result':return {'private':'PRIVATE_RESULT'}
        return original_result()
    jobs=PositionJobs(execute,slot,max_result_bytes=1 if failure=='storage' else 2*1024*1024)
    first=jobs.start(identifier(jobs),request());done=finish(jobs,first.job_id)
    assert done.state=='unavailable' and done.result is None and done.business_status=='UNKNOWN'
    assert 'PRIVATE' not in done.model_dump_json()
    assert jobs.start(identifier(jobs),request()).job_id==first.job_id
    assert len(calls)==1 and not slot.locked()


def test_thread_start_failure_keeps_a_nonreplayable_receipt(monkeypatch):
    from pocs.chatgpt_skills import position_jobs as module
    slot=threading.Lock();calls=[]
    jobs=PositionJobs(lambda *a:calls.append(1),slot)
    def fail(*args):raise RuntimeError('PRIVATE_THREAD_DETAIL')
    monkeypatch.setattr(module.threading.Thread,'start',fail)
    first=jobs.start(identifier(jobs),request())
    assert first.issue=='JOB_START_FAILED' and first.state=='unavailable'
    assert jobs.start(identifier(jobs),request()).job_id==first.job_id
    assert not calls and not slot.locked() and 'PRIVATE' not in first.model_dump_json()


def test_original_sync_slot_rejects_new_job_without_queueing():
    slot=threading.Lock();slot.acquire();calls=[]
    jobs=PositionJobs(lambda *a:calls.append(1),slot)
    assert jobs.start(identifier(jobs),request()).issue=='ANALYSIS_BUSY'
    assert not calls and not jobs.requests
    slot.release()
