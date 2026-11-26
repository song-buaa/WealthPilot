"""Quality archive: real durable filesystem, frozen synthetic reports, no network."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
import time
import pytest
from pydantic import ValidationError
from backend.services.research_evaluation import SampleStore, Evaluation, Feedback, DIMENSIONS
from pocs.chatgpt_skills.analysis import AnalysisRequest
from pocs.chatgpt_skills.position_store import PositionStore, StoreError
from tests.test_position_jobs import factory, finish, original_result


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('NETWORK_FORBIDDEN'))


def evaluation(report=None, **overrides):
    return Evaluation.model_validate(dict(request_id='review-one',target='native',report_sha256=report,
        ratings={k:dict(score='1.52',basis='Actual report paragraph',deductions='Missing independent competition evidence',
                       report_references=['section:business']) for k in DIMENSIONS},
        readability='PASS',readability_basis='Readable',fact_checks='Frozen facts match',valuation_checks='Conditional, not fair value',
        suggestions='Improve causal evidence',scorer_type='ai',scorer_model='protocol-test-not-a-quality-judge',
        scored_at='2026-11-26T00:00:00Z',**overrides))


def setup(tmp_path, status='partial'):
    jobs=factory(tmp_path/'jobs',lambda *a:original_result(status))
    req=AnalysisRequest(question='Research PDD without cost basis',symbol='PDD:US',
                        history=[dict(role='user',content='UNRELATED_PRIVATE_HISTORY')])
    job=jobs.start('research-one',req);done=finish(jobs,job.job_id)
    samples=SampleStore(jobs.root)
    return jobs,job,done,samples


@pytest.mark.parametrize('status',['ok','partial','unavailable'])
def test_automatic_capture_original_request_artifact_and_ttl_restart(tmp_path, status, monkeypatch):
    jobs,job,done,s=setup(tmp_path,status)
    try:
        page=s.query(job_id=job.job_id,export=True);row=page['items'][0]
        assert row['sample']['question']=='Research PDD without cost basis'
        assert 'history' not in row['sample']['request'] and row['sample']['excluded_history_count']==1
        assert 'UNRELATED_PRIVATE_HISTORY' not in json.dumps(page)
        assert row['state']['artifact']['sha256']==done.artifact.sha256
        assert row['state']['reports']['native']['text']==original_result().conclusion
        assert row['evaluation_status']=='UNEVALUATED' and not row['evaluations']
        assert jobs.start('research-one',AnalysisRequest.model_validate({**row['sample']['request'],'history':[dict(role='user',content='UNRELATED_PRIVATE_HISTORY')]})).job_id==job.job_id
        monkeypatch.setattr('pocs.chatgpt_skills.position_jobs.time.time',lambda:time.time_ns()/1e9+10000)
        assert jobs.get(job.job_id).issue=='RESULT_EXPIRED'
    finally:s.close()
    s=SampleStore(jobs.root)
    try:assert len(s.query(job_id=job.job_id,export=True)['items'])==1
    finally:s.close()


def test_append_reviews_server_total_idempotency_feedback_revision_and_export(tmp_path):
    jobs,job,done,s=setup(tmp_path)
    before=(jobs.root/(job.job_id+'.result.json')).read_bytes()
    try:
        sid=s.sample_id(job.job_id);h=s.query(export=True)['items'][0]['state']['reports']['native']['sha256']
        first=s.evaluate(sid,evaluation(h));assert first['total']=='7.60' and first['quality_status']=='FAIL'
        assert first['confirmation']=='UNCONFIRMED_AI'
        assert s.evaluate(sid,evaluation(h))==first
        changed=evaluation(h).model_copy(update={'suggestions':'changed'})
        with pytest.raises(ValueError,match='CONFLICT'):s.evaluate(sid,changed)
        revised=evaluation(h).model_copy(update={'request_id':'review-human','scorer_type':'human',
            'revision_of':first['evaluation_id'],'revision_reason':'Read original report'})
        revised.ratings={k:v.model_copy(update={'score':__import__('decimal').Decimal('1.6')}) for k,v in revised.ratings.items()}
        second=s.evaluate(sid,revised);assert second['total']=='8.0' and second['score_delta']=='0.40'
        assert second['confirmation']=='HUMAN_REPORTED'
        f=s.feedback(sid,Feedback(request_id='feedback-one',comment='User correction',author_type='human',evaluation_id=second['evaluation_id']))
        assert s.feedback(sid,Feedback.model_validate(f['input']))==f
        row=s.query(export=True)['items'][0];assert len(row['evaluations'])==2 and len(row['feedback'])==1
        assert s.query()['distribution']=={'native/ai/FAIL':1,'native/human/PASS':1}
        assert 'text' not in s.query()['items'][0]['state']['reports']['native']
    finally:s.close()
    assert (jobs.root/(job.job_id+'.result.json')).read_bytes()==before


def test_client_draft_not_final_and_exact_version(tmp_path):
    _,job,_,s=setup(tmp_path)
    try:
        sid=s.sample_id(job.job_id);text='Client proposed report';h=sha256(text.encode()).hexdigest()
        f=s.feedback(sid,Feedback(request_id='draft',comment='Draft only',author_type='ai',report_text=text))
        assert f['delivery_provenance']=='CLIENT_SUBMITTED_DRAFT_UNVERIFIED'
        ev=evaluation(h).model_copy(update={'target':'client_draft'})
        assert s.evaluate(sid,ev)['quality_status']=='FAIL'
        with pytest.raises(ValueError):s.evaluate(sid,ev.model_copy(update={'request_id':'bad','report_sha256':'a'*64}))
        with pytest.raises(ValidationError):Feedback(request_id='false-final',comment='x',report_text=text,report_kind='client_final_human_reported',author_type='ai')
        with pytest.raises(ValueError):s.evaluate(sid,ev.model_copy(update={'request_id':'bad-target','target':'reader'}))
    finally:s.close()


def test_failure_no_report_is_not_rated_not_zero(tmp_path):
    jobs=factory(tmp_path/'jobs',lambda *a:(_ for _ in ()).throw(TimeoutError('PRIVATE')))
    job=jobs.start('failed',AnalysisRequest(question='PDD',symbol='PDD:US'));done=finish(jobs,job.job_id)
    s=SampleStore(jobs.root)
    try:
        sid=s.sample_id(job.job_id);row=s.query()['items'][0]
        assert row['state']['job_state']=='unavailable' and row['evaluation_status']=='UNEVALUATED'
        with pytest.raises(ValueError,match='UNAVAILABLE'):s.evaluate(sid,evaluation())
        ev=evaluation().model_copy(update={'ratings':None,'not_rated_reason':'No real report'})
        r=s.evaluate(sid,ev);assert r['total'] is None and r['quality_status']=='N/R'
    finally:s.close()


def test_invalid_scores_closed_contract(tmp_path):
    good=evaluation('a'*64).model_dump(mode='json')
    for bad in ['2.01','NaN','-1','Infinity']:
        value=json.loads(json.dumps(good));value['ratings'][DIMENSIONS[0]]['score']=bad
        with pytest.raises(ValidationError):Evaluation.model_validate(value)
    value=json.loads(json.dumps(good));value['ratings'].pop(DIMENSIONS[0])
    with pytest.raises(ValidationError):Evaluation.model_validate(value)


def test_eval_failure_does_not_block_job_and_private_store(tmp_path,monkeypatch):
    monkeypatch.setattr(SampleStore,'capture',lambda *a,**k:(_ for _ in ()).throw(OSError('PRIVATE')))
    jobs=factory(tmp_path/'jobs');job=jobs.start('one',AnalysisRequest(question='LI'))
    assert finish(jobs,job.job_id).state=='completed'
    assert all((f.stat().st_mode&0o777)==0o600 for f in jobs.root.iterdir())


def test_concurrent_reviews_one_immutable_record_and_capacity(tmp_path):
    jobs,job,_,s=setup(tmp_path)
    try:
        sid=s.sample_id(job.job_id);h=s.query(export=True)['items'][0]['state']['reports']['native']['sha256']
    finally:s.close()
    def append(_):
        st=SampleStore(jobs.root)
        try:return st.evaluate(sid,evaluation(h))
        finally:st.close()
    with ThreadPoolExecutor(max_workers=5) as pool:rows=list(pool.map(append,range(8)))
    assert len({r['evaluation_id'] for r in rows})==1
    small=SampleStore(jobs.root,quota_bytes=10)
    try:
        with pytest.raises(StoreError):small.feedback(sid,Feedback(request_id='full',comment='No data deletion',author_type='human'))
    finally:small.close()
    s=SampleStore(jobs.root)
    try:assert len(s.query()['items'][0]['evaluations'])==1
    finally:s.close()


def test_multisymbol_pages_and_tamper_fail_closed(tmp_path):
    jobs,job,_,s=setup(tmp_path)
    try:
        second=jobs.start('LI',AnalysisRequest(question='LI',symbol='LI:US'));finish(jobs,second.job_id)
        assert s.query()['total_samples']==2
        assert s.query(symbol='LI:US')['total_samples']==1
        page=s.query(limit=1,export=True);assert page['next_offset']==1
        assert len(s.query(offset=1,limit=1,export=True)['items'])==1
        path=s.root/('sample-'+s.sample_id(job.job_id)+'.json');path.write_text('corrupt')
        with pytest.raises(StoreError):s.query()
    finally:s.close()


def test_recovery_refreshes_lifecycle_but_keeps_artifact_and_report_version(tmp_path):
    jobs,job,_,s=setup(tmp_path)
    try:
        sid=s.sample_id(job.job_id)
        old=s.query(export=True)['items'][0]['state']
        with s.files.locked():s.files.write(f'state-{sid}.json',dict(old,job_state='unavailable',issue='RESULT_STORAGE_UNAVAILABLE'))
        assert jobs.get(job.job_id).state=='completed'
        new=s.query(export=True)['items'][0]['state']
        assert new['job_state']=='completed' and new['issue'] is None
        assert new['reports']==old['reports'] and new['artifact']==old['artifact']
    finally:s.close()


def test_rereview_requires_explicit_basis_and_history_export_is_bounded(tmp_path):
    _,job,_,s=setup(tmp_path)
    try:
        sid=s.sample_id(job.job_id);h=s.query(export=True)['items'][0]['state']['reports']['native']['sha256']
        s.evaluate(sid,evaluation(h))
        with pytest.raises(ValueError,match='REVISION'):s.evaluate(sid,evaluation(h).model_copy(update={'request_id':'unexplained-new-score'}))
        for i in range(15):
            s.feedback(sid,Feedback(request_id=f'feedback-{i}',comment='x'*6000,author_type='human',report_text='字'*180000))
        seen=set();cursor=0
        while True:
            page=s.query(sample_id=sid,history_offset=cursor,export=True)
            assert len(json.dumps(page,ensure_ascii=False).encode())<2*1024*1024
            row=page['items'][0]
            seen.update(r['feedback_id'] for r in row['feedback'])
            if row['history_next_offset'] is None:break
            assert row['history_next_offset']>cursor;cursor=row['history_next_offset']
        assert len(seen)==15
    finally:s.close()


def test_empty_report_accepts_null_hash_not_rated(tmp_path):
    jobs=factory(tmp_path/'jobs',lambda *a:original_result('unavailable').model_copy(update={'conclusion':''}))
    job=jobs.start('empty',AnalysisRequest(question='PDD'));finish(jobs,job.job_id)
    s=SampleStore(jobs.root)
    try:
        result=s.evaluate(s.sample_id(job.job_id),evaluation().model_copy(update={'ratings':None,'not_rated_reason':'No report delivered'}))
        assert result['quality_status']=='N/R' and result['total'] is None
    finally:s.close()


def test_explicit_history_import_unknowns_actual_hash_and_known_score_only(tmp_path):
    from scripts.import_research_history import import_reference
    root=tmp_path/'jobs';root.mkdir(mode=0o700);s=SampleStore(root)
    raw=b'Actual saved historical report';report=tmp_path/'report.md';report.write_bytes(raw)
    record=dict(symbol='PDD:US',source_record_sha256='b'*64,
        native=dict(path=str(report),sha256=sha256(raw).hexdigest()),
        assessment_reference=dict(reported_total='8.1',source='Existing business review',dimensions=dict(zip(DIMENSIONS,['1.8','1.5','1.4','1.8','1.6']))))
    try:
        sid=import_reference(s,record);assert import_reference(s,record)==sid
        row=s.query(export=True)['items'][0]
        assert row['sample']['question'] is None and row['sample']['request_capture']=='UNKNOWN'
        assert row['sample']['historical_assessment_reference']['reported_total']=='8.1'
        assert row['evaluation_status']=='UNEVALUATED' and row['evaluations']==[]
        assert row['state']['reports']['native']['text']==raw.decode()
        assert row['state']['artifact']['sha256'] is None
        report.write_text('tamper')
        with pytest.raises(ValueError,match='HASH'):import_reference(s,record)
    finally:s.close()


def test_code_revision_package_and_linked_worktree_without_processes(tmp_path):
    from backend.services.research_evaluation import code_revision
    root=tmp_path/'release'/'code';root.mkdir(parents=True)
    (root.parent/'installed.json').write_text(json.dumps(dict(revision='b'*40,package_verified=True)))
    assert code_revision(root)=='b'*40
    (root.parent/'installed.json').unlink()
    common=tmp_path/'common';git=common/'worktrees'/'evaluation';git.mkdir(parents=True)
    (root/'.git').write_text('gitdir: '+str(git))
    (git/'commondir').write_text('../..');(git/'HEAD').write_text('ref: refs/heads/codex/evaluation')
    (common/'packed-refs').write_text('a'*40+' refs/heads/codex/evaluation\n')
    assert code_revision(root)=='a'*40


@pytest.mark.parametrize('value',[[],{'revision':None,'package_verified':True},{'revision':3,'package_verified':True}])
def test_malformed_installed_identity_is_unknown_not_research_failure(tmp_path,value):
    from backend.services.research_evaluation import code_revision
    root=tmp_path/'code';root.mkdir()
    (tmp_path/'installed.json').write_text(json.dumps(value))
    assert code_revision(root)=='UNKNOWN'


@pytest.mark.parametrize('target,text',[('native',''),('reader','Reader only')])
def test_import_empty_or_other_target_cannot_create_native_score(tmp_path,target,text):
    from scripts.import_research_history import import_reference
    root=tmp_path/'jobs';root.mkdir(mode=0o700);s=SampleStore(root)
    report=tmp_path/'report.md';report.write_text(text)
    record=dict(symbol='PDD:US',source_record_sha256='c'*64,
        **{target:dict(path=str(report),sha256=sha256(text.encode()).hexdigest())})
    try:
        sid=import_reference(s,record)
        with pytest.raises(ValueError,match='UNAVAILABLE'):
            s.evaluate(sid,evaluation(sha256(text.encode()).hexdigest() if target=='native' else None))
        assert s.evaluate(sid,evaluation().model_copy(update={'ratings':None,'not_rated_reason':'Native report absent'}))['quality_status']=='N/R'
    finally:s.close()


def test_whitespace_client_text_is_not_an_actual_report():
    with pytest.raises(ValidationError,match='CONTENT'):
        Feedback(request_id='empty',comment='No report',author_type='ai',report_text=' \n ')
