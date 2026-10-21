"""Synthetic source evidence and isolated original Guojin publication only."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import multiprocessing
from pathlib import Path
import subprocess
import sys

import pytest

from tests.guojin_native_fixtures import make_batch, write_json
from tests.test_guojin_native_sync import native_api, env, facts, publish as original_publish


@pytest.fixture
def daily(env, tmp_path):
    from services.broker_sync.guojin import daily_sync as module
    shared = tmp_path/'shared';shared.mkdir()
    profile = tmp_path/'profile.json'
    field=dict(id='synthetic',class_='Static',pattern='(?<value>synthetic)')
    field['class']=field.pop('class_')
    step=dict(x=10,y=10,expect='synthetic read-only page')
    write_json(profile, dict(schema='guojin.gui-profile.v1', verified_readonly=True,
        evidence_ref='synthetic read-only contract', window_pattern='synthetic',account_pattern='(?<account>synthetic)',
        width=100,height=100,save_title='Save',excel_prompt_title='Open Excel',filename=field,
        save_button={**field,'name':'Save'},excel_no={**field,'name':'No'},
        markets={k:dict(page_pattern='synthetic',open=[],refresh=step,export=[step],query=field,rows=field,
            success=field,total=field) for k in ('stock','hgt','sgt')}))
    env.config['automation'] = dict(schema='guojin.automation.v1',enabled=True,vm='Synthetic Windows',
        guest_root='D:\\Synthetic', shared_root=str(shared), profile=str(profile))
    write_json(env.config_path,env.config)
    # Earlier ready is a valid last-good; new source query is independently newer.
    now=env.query+timedelta(seconds=3)
    clock=lambda:now+timedelta(seconds=5)
    def exporter(config,auto,profile,batch,started):
        source = make_batch(shared,batch=batch,query=started+timedelta(seconds=1))
        # Move under the same path that Parallels shared_root/batch would expose.
        destination=shared/batch;source.directory.rename(destination)
        obs=dict(schema='guojin.gui-observations.v1',batch=batch,status='EXPORTED',
            account_total_assets_cny=source.receipt['account_total_assets_cny'],markets={})
        for key,item in source.receipt['files'].items():
            image=destination/(key+'.png');image.write_bytes(b'synthetic private GUI evidence')
            obs['markets'][key]=dict(account_id=item['account_id'],complete=True,
                query_before=(started-timedelta(days=1)).isoformat(),queried_at=item['queried_at'],
                row_count=item['row_count'],exported_at=(started+timedelta(seconds=2)).isoformat(),
                sha256=item['sha256'],evidence_sha256=hashlib.sha256(image.read_bytes()).hexdigest())
        write_json(destination/'observations.json',obs)
        return destination
    calls=[]
    def publisher(*args):
        calls.append(args)
        result=original_publish(env)
        assert result.status=='success',result.error
        return dict(published=True,run_id=result.id)
    env.module,env.exporter,env.publisher,env.calls,env.now,env.clock=module,exporter,publisher,calls,now,clock
    return env


def execute(daily, **kwargs):
    ticks=iter([daily.now]+[daily.now+timedelta(seconds=5)]*10)
    return daily.module.run(daily.config_path,database='synthetic.db',contract='synthetic-contract',
        publish_assets=kwargs.pop('publish_assets',True),
        exporter=kwargs.pop('exporter',daily.exporter),publisher=kwargs.pop('publisher',daily.publisher),
        clock=kwargs.pop('clock',lambda:next(ticks)),**kwargs)


def test_full_flow_original_executor_distinct_channels_cash_and_ready(daily):
    before=json.loads((daily.root/'ready.json').read_text())
    result=execute(daily)
    assert result['status']=='SUCCESS',result
    assert json.loads((daily.root/'ready.json').read_text())!=before
    assert result['security_count']==4 and result['account_total_assets_cny']=='1490.00'
    with daily.sessions() as db:
        from app.models import Position
        assert db.query(Position).count()==5
        assert {p.symbol for p in db.query(Position) if '.HGT:' in p.symbol or '.SGT:' in p.symbol}=={
            '2015.HGT:HK','1234.HGT:HK','2015.SGT:HK'}
    assert len(daily.calls)==1


@pytest.mark.parametrize('case', ['missing','hash','old_query','unchanged','wrong_owner','partial','wrong_count',
    'missing_market','reconcile','image_hash','future_export','mixed_batch','symlink'])
def test_invalid_producer_never_switches_ready_and_preserves_last_good(daily,case):
    assert original_publish(daily).status=='success'
    before=facts(daily);pointer=(daily.root/'ready.json').read_bytes()
    def bad(*args):
        source=daily.exporter(*args)
        obs=json.loads((source/'observations.json').read_text())
        item=obs['markets']['hgt']
        if case=='missing':(source/'sgt.xls').unlink()
        if case=='hash':item['sha256']='0'*64
        if case=='old_query':item['queried_at']=(daily.now-timedelta(days=1)).isoformat()
        if case=='unchanged':item['query_before']=item['queried_at']
        if case=='wrong_owner':item['account_id']='another-owner'
        if case=='partial':item['complete']=False
        if case=='wrong_count':item['row_count']=999
        if case=='missing_market':del obs['markets']['sgt']
        if case=='reconcile':obs['account_total_assets_cny']='1'
        if case=='image_hash':item['evidence_sha256']='0'*64
        if case=='future_export':item['exported_at']=(daily.now+timedelta(days=1)).isoformat()
        if case=='mixed_batch':obs['batch']='old_batch'
        if case=='symlink':
            (source/'hgt.xls').rename(source/'copy.xls');(source/'hgt.xls').symlink_to(source/'copy.xls')
        write_json(source/'observations.json',obs)
        return source
    result=execute(daily,exporter=bad)
    assert result['status']=='FAILED' and not daily.calls
    assert (daily.root/'ready.json').read_bytes()==pointer and facts(daily)==before
    assert 'another-owner' not in json.dumps(result)


@pytest.mark.parametrize('code',['AUTO_SESSION_UNAVAILABLE','AUTO_LOGIN_REQUIRED','AUTO_QUERY_UNCHANGED',
    'AUTO_COUNTER_120022','AUTO_GUI_UNAVAILABLE','AUTO_TIMEOUT'])
def test_windows_failures_bounded_no_retry_or_ready_change(daily,code):
    pointer=(daily.root/'ready.json').read_bytes();calls=[]
    def failed(*args):calls.append(1);raise ValueError(code)
    result=execute(daily,exporter=failed)
    assert result['status']=='FAILED' and result['reason_code']==code
    assert calls==[1] and daily.calls==[] and (daily.root/'ready.json').read_bytes()==pointer


def test_unknown_sdk_errors_redacted(daily):
    def failed(*args):raise RuntimeError('secret raw account password connection data')
    result=execute(daily,exporter=failed)
    assert result['reason_code']=='AUTO_EVIDENCE_INVALID'
    assert 'secret' not in json.dumps(result)


def test_disabled_never_touches_windows(daily):
    daily.config['automation']['enabled']=False;write_json(daily.config_path,daily.config)
    with pytest.raises(ValueError,match='AUTO_DISABLED'):execute(daily)
    assert not daily.calls


def test_unverified_or_incomplete_gui_profile_fails_before_vm_input(daily):
    path=Path(daily.config['automation']['profile']);profile=json.loads(path.read_text())
    profile['markets']['hgt'].pop('rows');write_json(path,profile)
    with pytest.raises(ValueError,match='AUTO_GUI_CONTRACT_UNVERIFIED'):execute(daily)
    assert not daily.calls


def test_default_validation_does_not_switch_ready_or_access_database(daily):
    pointer=(daily.root/'ready.json').read_bytes()
    result=execute(daily,publish_assets=False)
    assert result['status']=='VALIDATED' and not daily.calls
    assert (daily.root/'ready.json').read_bytes()==pointer and facts(daily)==[]
    def forbidden(*args):raise AssertionError('already validated source must be reused')
    result=execute(daily,exporter=forbidden)
    assert result['status']=='SUCCESS' and len(daily.calls)==1


def test_ready_failure_leaves_old_pointer(daily,monkeypatch):
    old=(daily.root/'ready.json').read_bytes()
    replace=daily.module.os.replace
    def fail(a,b):
        if Path(b).name=='ready.json':raise OSError('synthetic interrupted atomic rename')
        replace(a,b)
    monkeypatch.setattr(daily.module.os,'replace',fail)
    assert execute(daily)['status']=='FAILED'
    assert (daily.root/'ready.json').read_bytes()==old and not daily.calls


def test_crash_after_ready_retries_sealed_batch_without_new_gui(daily):
    def unavailable(*args):raise ValueError('AUTO_SYNC_FAILED')
    result=execute(daily,publisher=unavailable)
    assert result['status']=='FAILED' and result['reason_code']=='AUTO_SYNC_FAILED'
    batch=result['batch']
    def forbidden(*args):raise AssertionError('recovery must not re-export')
    result=execute(daily,retry_batch=batch,exporter=forbidden)
    assert result['status']=='SUCCESS' and len(daily.calls)==1
    logs=[json.loads(p.read_text()) for p in (daily.root/batch).glob('run_*.json')]
    assert {r['status'] for r in logs}=={'FAILED','SUCCESS'}


def test_cannot_regress_ready_even_before_original_db_fence(daily):
    first=execute(daily);assert first['status']=='SUCCESS'
    pointer=(daily.root/'ready.json').read_bytes()
    second=execute(daily)  # same observed source time, different batch
    assert second['status']=='FAILED' and second['reason_code']=='NATIVE_BATCH_STALE'
    assert (daily.root/'ready.json').read_bytes()==pointer and len(daily.calls)==1
    # A stale failed candidate must not trap every subsequent invocation into
    # retrying it. A genuinely new query is eligible again.
    daily.now+=timedelta(seconds=3)
    recovered=execute(daily)
    assert recovered['status']=='SUCCESS' and len(daily.calls)==2


def test_schedule_and_manual_share_flow_restart_misfire_and_once_per_day(daily,monkeypatch):
    # A fixed workday around 17:30; frozen synthetic source remains same day.
    now=daily.now.astimezone(daily.module.SHANGHAI).replace(hour=17,minute=30).astimezone(timezone.utc)
    monkeypatch.setattr(daily.module,'scheduled_day',lambda now:True)
    from services.broker_sync.guojin import native_exports
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):return now+timedelta(seconds=10)
    monkeypatch.setattr(native_exports,'datetime',FrozenDateTime)
    result=execute(daily,trigger='cron',clock=lambda:now)
    # exporter is synthetic future+1s, so provide a moving completion clock.
    assert result['status']=='FAILED'
    times=iter([now,now+timedelta(seconds=5)]+[now+timedelta(seconds=6)]*10)
    result=execute(daily,trigger='cron',clock=lambda:next(times))
    assert result['status']=='SUCCESS',result
    assert execute(daily,trigger='cron',clock=lambda:now)['reason_code']=='ALREADY_PUBLISHED_TODAY'
    assert execute(daily,trigger='cron',clock=lambda:now+timedelta(hours=1))['reason_code']=='AUTO_WINDOW_CLOSED'
    # New process sees same private status, not an in-memory scheduling flag.
    assert json.loads((daily.root/'automation-status.json').read_text())['last_success_day']==now.astimezone(daily.module.SHANGHAI).date().isoformat()


def _try_lock(root, queue):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
    from services.broker_sync.guojin.daily_sync import single_run
    try:
        with single_run(Path(root)):queue.put('acquired')
    except ValueError as exc:queue.put(str(exc))


def test_cross_process_mutex_and_process_exit_recovery(daily):
    ctx=multiprocessing.get_context('spawn');queue=ctx.Queue()
    with daily.module.single_run(daily.root):
        child=ctx.Process(target=_try_lock,args=(str(daily.root),queue));child.start()
        assert queue.get(timeout=10)=='AUTO_BUSY'
        child.join(10);assert child.exitcode==0
    child=ctx.Process(target=_try_lock,args=(str(daily.root),queue));child.start()
    assert queue.get(timeout=10)=='acquired'
    child.join(10);assert child.exitcode==0


def test_bounded_process_cleanup(daily):
    with pytest.raises(ValueError,match='AUTO_TIMEOUT'):
        daily.module.bounded([sys.executable,'-c','import time;time.sleep(30)'],timeout=.05)


def test_parallels_contract_uses_current_user_guest_watchdog_and_no_password(daily,monkeypatch):
    calls=[]
    def fake(argv,timeout):
        calls.append((argv,timeout))
        if 'status' in argv:return b'VM synthetic running'
        return b'{"status":"EXPORTED"}'
    monkeypatch.setattr(daily.module,'bounded',fake)
    auto,profile=daily.module.automation_config(daily.config)
    assert daily.module.export_windows(daily.config,auto,profile,'synthetic',daily.now).name=='synthetic'
    argv,budget=calls[-1]
    assert '--current-user' in argv and budget==640 and '--password' not in argv
    assert len(argv[-1]) < 28000  # nested -EncodedCommand exceeded Windows 32767 limit
    import base64
    launcher=base64.b64decode(argv[-1]).decode('utf-16le')
    assert 'WaitForExit(610000)' in launcher and '$child.Kill()' in launcher
    assert 'GzipStream' in launcher and 'Remove-Item -LiteralPath $out,$err,$taskfile' in launcher
    script=daily.module.PS_SCRIPT.read_text()
    assert 'query_before=$before' in script and 'AUTO_COUNTER_120022' in script
    assert 'Get-Process -Name xiadan' in script and 'Local\\WealthPilotGuojinNativeExport' in script


def test_holiday_and_out_of_calendar_range_fail_closed(daily):
    # Reuse installed D1 calendar, not a weekday heuristic.
    assert daily.module.scheduled_day(datetime(2026,10,18,9,30,tzinfo=timezone.utc)) is False
    with pytest.raises(ValueError,match='AUTO_CALENDAR_UNKNOWN'):
        daily.module.scheduled_day(datetime(2050,1,3,9,30,tzinfo=timezone.utc))


def test_original_cli_strict_contract_reuses_committed_batch_after_lost_response(daily,monkeypatch):
    import os
    import sqlite3
    from app.shared_sqlite import schema_digest
    database=Path(daily.engine.url.database)
    database.parent.chmod(0o700);database.chmod(0o600)
    for suffix in ('-wal','-shm'):
        path=Path(str(database)+suffix)
        if path.exists():path.chmod(0o600)
    with sqlite3.connect(database) as db:
        digest=schema_digest(db)
    info=database.stat();contract=database.parent/'db-contract.json'
    write_json(contract,dict(schema='wealthpilot.shared-sqlite.v1',mode='shared_wal',portfolio_id=7,
        database_path=str(database),file_identity=[info.st_dev,info.st_ino],schema_sha256=digest))
    monkeypatch.setenv('WEALTHPILOT_ENABLED_SYNC_BROKERS','guojin')
    assert original_publish(daily).status=='success'
    first=facts(daily)
    def count():
        with daily.sessions() as db:return db.query(daily.api.Run).count()
    before=count()
    response=daily.module.publish(daily.config_path,database,contract)
    assert response['published'] is True and response['reused'] is True
    assert count()==before and facts(daily)==first
    # A different Owner or modified strict contract cannot reuse facts.
    bad=json.loads(contract.read_text());bad['portfolio_id']=8;write_json(contract,bad)
    with pytest.raises(ValueError):daily.module.publish(daily.config_path,database,contract)
    assert count()==before and facts(daily)==first


def test_restart_auto_recovers_only_sealed_same_day_batch(daily):
    result=execute(daily,publisher=lambda *args:(_ for _ in ()).throw(ValueError('AUTO_SYNC_FAILED')))
    assert result['status']=='FAILED'
    def forbidden(*args):raise AssertionError('recover sealed batch rather than duplicate GUI query')
    recovered=execute(daily,exporter=forbidden)
    assert recovered['status']=='SUCCESS' and recovered['batch']==result['batch']


def test_scheduler_template_is_existing_codex_timer_not_new_service(daily):
    doc=(Path(__file__).resolve().parents[1]/'docs/GUOJIN_NATIVE_SYNC.md').read_text()
    assert '--trigger cron' in doc and '--trigger manual' in doc
    assert '17:30' in doc and '17:30–18:00' in doc


@pytest.mark.parametrize('code,expected',[
    ('NATIVE_LEGACY_REVIEW_REQUIRED','NATIVE_LEGACY_REVIEW_REQUIRED'),
    ('raw secret owner or credential','AUTO_SYNC_FAILED')])
def test_publication_failure_preserves_only_safe_original_reason(daily,monkeypatch,code,expected):
    def child(argv,timeout,allow_failure=False):
        assert allow_failure is True
        return json.dumps(dict(status='failed',published=False,reason_code=code)).encode()
    monkeypatch.setattr(daily.module,'bounded',child)
    with pytest.raises(ValueError,match=expected):daily.module.publish('config','database','contract')
