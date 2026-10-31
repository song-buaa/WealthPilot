"""Synthetic identity values only; no real request/production configuration."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from research_v2.sec_identity import SEC_IDENTITY_KEYS,private_identity,child_environment,identity_string
from research_v2.sec_disclosures import request_identity


def config(tmp_path):
    folder=tmp_path/'private';folder.mkdir(mode=0o700)
    path=folder/'sec-identity.json'
    # Syntactic test only; never use this identity for an actual request.
    values=dict(WEALTHPILOT_SEC_ORGANIZATION='Synthetic Unit Checks',WEALTHPILOT_SEC_CONTACT_EMAIL='unit@wealthpilot.local')
    path.write_text(json.dumps(values));path.chmod(0o600)
    return path,values


def test_existing_owner_guard_and_coherent_child_injection(tmp_path,monkeypatch):
    path,values=config(tmp_path)
    for k,v in values.items():monkeypatch.setenv(k,v)
    assert request_identity()==identity_string(values)
    state,loaded=private_identity(path);assert state=='PRESENT' and loaded==values
    parent={'UNCHANGED':'yes',**{k:'stale' for k in SEC_IDENTITY_KEYS}};before=dict(parent)
    child=child_environment(path,parent)
    assert parent==before and child['UNCHANGED']=='yes' and child['PYTHON_DOTENV_DISABLED']=='1'
    assert all(child[k]==values[k] for k in SEC_IDENTITY_KEYS)


@pytest.mark.parametrize('fault',['missing','extra','type','placeholder','malformed','file_mode','dir_mode','symlink'])
def test_invalid_or_missing_config_never_injects_identity(tmp_path,fault):
    path,values=config(tmp_path)
    if fault=='missing':path.unlink()
    elif fault=='extra':values['TOKEN']='never-load';path.write_text(json.dumps(values))
    elif fault=='type':values['WEALTHPILOT_SEC_ORGANIZATION']=True;path.write_text(json.dumps(values))
    elif fault=='placeholder':values['WEALTHPILOT_SEC_CONTACT_EMAIL']='nobody@example.com';path.write_text(json.dumps(values))
    elif fault=='malformed':path.write_text('{')
    elif fault=='file_mode':path.chmod(0o644)
    elif fault=='dir_mode':path.parent.chmod(0o755)
    else:
        alias=path.parent/'alias.json';alias.symlink_to(path);path=alias
    state,_=private_identity(path);assert state==('MISSING' if fault=='missing' else 'INVALID')
    with pytest.raises(ValueError,match='SEC_DEV_IDENTITY_'):child_environment(path,{})


def test_status_cli_never_prints_identity_and_child_gets_only_explicit_identity(tmp_path):
    path,values=config(tmp_path);script=Path(__file__).resolve().parents[1]/'scripts/sec_dev.py'
    result=subprocess.run([sys.executable,str(script),'--config',str(path),'status'],capture_output=True,text=True)
    assert result.returncode==0 and json.loads(result.stdout)['status']=='PRESENT'
    assert all(v not in result.stdout+result.stderr for v in values.values())
    result=subprocess.run([sys.executable,str(script),'--config',str(path),'run','--',sys.executable,'-c',
        'import os,json;print(json.dumps({"loaded":all(os.getenv(k) for k in '+repr(tuple(SEC_IDENTITY_KEYS))+')}))'],capture_output=True,text=True)
    assert result.returncode==0 and json.loads(result.stdout)=={'loaded':True}
    assert all(v not in result.stdout+result.stderr for v in values.values())


def test_existing_worker_opt_in_does_not_load_other_configuration(tmp_path,monkeypatch):
    from pocs.chatgpt_skills.worker import load_analysis_environment
    path,values=config(tmp_path)
    monkeypatch.setenv('WEALTHPILOT_OPENAI_API_KEY','unchanged-test-key')
    for k in SEC_IDENTITY_KEYS:monkeypatch.delenv(k,raising=False)
    load_analysis_environment(path,sec_identity_only=True)
    assert all(os.environ[k]==values[k] for k in SEC_IDENTITY_KEYS)
    assert os.environ['WEALTHPILOT_OPENAI_API_KEY']=='unchanged-test-key'
    with pytest.raises(ValueError,match='LOAD_MODE_CONFLICT'):load_analysis_environment(path,sec_identity_only=True,model_policy_only=True)
