"""迁入边界：无公司/生产Owner导入、无采集客户端、无远端Docker默认。"""
import os
from pathlib import Path
import subprocess
import sys
import json
import hashlib
import pytest
import shutil


def test_cold_import_has_no_provider_or_company_runtime():
    code="""
import sys
def audit(event,args):
    if event.startswith('socket.'):raise AssertionError('NETWORK_DURING_IMPORT')
    if event=='open' and isinstance(args[0],str) and (args[0].endswith('.key') or '/.env' in args[0]):
        raise AssertionError('SECRET_DURING_IMPORT')
sys.addaudithook(audit)
import research_backtest.native_data, research_backtest.prepare, research_backtest.run, research_backtest.verify, research_backtest.report
assert not any(m.split('.')[0] in ('redstone','backend','dotenv') for m in sys.modules)
"""
    result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],
        env={'PATH':os.environ.get('PATH',''),'PYTHON_DOTENV_DISABLED':'1'},capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_docker_command_ignores_remote_company_context(monkeypatch,tmp_path):
    from research_backtest import runner
    monkeypatch.setenv('DOCKER_HOST','tcp://company.invalid:2375')
    monkeypatch.setenv('DOCKER_CONTEXT','company-context')
    monkeypatch.setattr(runner.shutil,'which',lambda name:'/usr/bin/docker')
    monkeypatch.setattr(runner.Path,'home',lambda:tmp_path)
    socket=tmp_path/'.docker/run/docker.sock';socket.parent.mkdir(parents=True);socket.touch()
    assert runner.docker_command()==['/usr/bin/docker','--host','unix://'+str(socket)]


def test_base_config_is_local_backtesting_without_credentials():
    from research_backtest.runner import base_config
    config=base_config()
    assert config['environment']=='backtesting' and config['live-mode'] is False
    assert config['api-access-token']=='' and config['job-organization-id']==''


def test_runner_does_not_accept_credential_or_live_templates(tmp_path):
    from research_backtest.runner import run
    with pytest.raises(TypeError):
        run(tmp_path,{'live-mode':True,'api-access-token':'private-fixture'})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('damage',[None,'image','exit','input','observation','missing_manifest'])
def test_report_requires_real_pinned_completed_runtime(tmp_path,damage):
    from research_backtest.report import run_qualified
    from research_backtest.runner import IMAGE
    (tmp_path/'results').mkdir();p=tmp_path/'results/history-observation.json';p.write_text('{}')
    source=tmp_path/'input.json';source.write_text('{}')
    record=dict(image=IMAGE,network='none',status='COMPLETED_REQUIRES_RECONCILIATION',exit_code=0,
        attach_exit_code=0,has_native_output=True,observation_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
        input_hashes={'input.json':hashlib.sha256(source.read_bytes()).hexdigest()})
    if damage=='image':record['image']='unlocked/image:latest'
    if damage=='exit':record['exit_code']=1
    if damage=='input':source.write_text('{"tampered":1}')
    if damage=='observation':p.write_text('{"tampered":1}')
    if damage!='missing_manifest':(tmp_path/'run-manifest.json').write_text(json.dumps(record))
    assert run_qualified(tmp_path) is (damage is None)


def test_same_or_copied_run_cannot_claim_deterministic_repetition(tmp_path):
    from research_backtest.report import independent_repeat
    from research_backtest.runner import IMAGE
    first=tmp_path/'first';(first/'results').mkdir(parents=True)
    p=first/'results/history-observation.json';p.write_text('{}')
    source=first/'input.json';source.write_text('{}')
    record=dict(run_id='test-a',started_at='2026-11-03T01:00:00Z',image=IMAGE,network='none',
        status='COMPLETED_REQUIRES_RECONCILIATION',exit_code=0,attach_exit_code=0,has_native_output=True,
        observation_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),input_hashes={'input.json':hashlib.sha256(source.read_bytes()).hexdigest()})
    (first/'run-manifest.json').write_text(json.dumps(record))
    assert independent_repeat(first,first) is False
    second=tmp_path/'second';shutil.copytree(first,second)
    assert independent_repeat(first,second) is False
    record.update(run_id='test-b',started_at='2026-11-03T01:01:00Z')
    (second/'run-manifest.json').write_text(json.dumps(record))
    assert independent_repeat(first,second) is True
