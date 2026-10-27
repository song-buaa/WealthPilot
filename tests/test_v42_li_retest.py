"""Frozen synthetic seven-attempt ledger; zero Provider or paid network IO."""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import httpx
import openai
import pytest

from app.llm_acceptance import Budget, AdmissionDenied, PRICE_VERSION, guarded_transport
from app.llm_observation import analysis_run
from tests.test_v42_acceptance_budget import request

OLD = '1' * 32
NEW = '2' * 32
AUTH = 'a' * 32


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def history(tmp_path):
    path = tmp_path / 'original-ledger'
    attempts = [dict(analysis_run_id=OLD, model='gpt-6.1-sol', endpoint='chat',
        input_token_bound=10000, output_token_bound=3000, reserved_nanos=80000000) for _ in range(6)]
    attempts.append(dict(analysis_run_id=OLD, model='gpt-6-luna', endpoint='responses',
        input_token_bound=100000, output_token_bound=102917, reserved_nanos=76458500))
    data = dict(version=PRICE_VERSION, max_attempts=24, max_nanos=3000000000,
        attempts=attempts, analyses={'LI:US': OLD}, reserved_nanos=556458500, denials={})
    path.write_text(json.dumps(data, indent=2)); path.chmod(0o600)
    return path, data


def admit(budget, path, identifier=NEW, symbol='LI:US', **changes):
    options = dict(authorization_id=AUTH, expected_ledger_sha256=digest(path)); options.update(changes)
    budget.claim_retest(symbol, identifier, **options)


def test_old_ledger_bytes_history_and_budget_are_preserved(history):
    path, original = history; before = path.read_bytes()
    budget = Budget(path, existing_only=True)
    assert budget.snapshot()['attempt_count'] == 7
    assert budget.snapshot()['reserved_usd'] == '0.5564585'
    assert path.read_bytes() == before  # opening/snapshot is not migration
    inode = path.stat().st_ino
    admit(budget, path)
    migrated = json.loads(path.read_text())
    assert path.stat().st_ino == inode
    assert all(migrated[key] == value for key, value in original.items())
    assert migrated['li_retest']['original_run_id'] == OLD
    assert migrated['li_retest']['analysis_run_id'] == NEW
    assert migrated['li_retest']['authorized_against_sha256'] == hashlib.sha256(before).hexdigest()
    backup = Path(str(path) + '.pre-li-retest')
    assert json.loads(backup.read_text())['original_ledger_utf8'].encode() == before
    assert backup.stat().st_mode & 0o777 == 0o600
    with pytest.raises(AdmissionDenied, match='LEDGER_INVALID'):
        Budget(backup)  # recovery evidence cannot serve as another budget
    restarted = Budget(path, existing_only=True)
    assert restarted.snapshot() == budget.snapshot()
    with pytest.raises(AdmissionDenied):
        admit(restarted, path, '3' * 32, authorization_id='f' * 32)


@pytest.mark.parametrize('changes', [dict(symbol='PDD:US'), dict(symbol='3690:HK'),
    dict(identifier=OLD), dict(authorization_id=None), dict(authorization_id=''),
    dict(expected_ledger_sha256='0' * 64), dict(identifier='bad')])
def test_unauthorized_stale_cross_symbol_or_borrowed_run_is_rejected(history, changes):
    path, _ = history; before = path.read_bytes(); budget = Budget(path, existing_only=True)
    with pytest.raises(AdmissionDenied):
        admit(budget, path, **changes)
    assert path.read_bytes() == before
    assert not Path(str(path) + '.pre-li-retest').exists()
    assert budget.runs == set()


def test_duplicate_and_other_symbols_original_rules_remain(history):
    path, _ = history; budget = Budget(path); admit(budget, path)
    with pytest.raises(AdmissionDenied, match='ALREADY_CLAIMED'):
        admit(budget, path)
    for symbol, identifier in [('LI:US', '4' * 32), ('PDD:US', NEW)]:
        with pytest.raises(AdmissionDenied, match='ALREADY_CLAIMED'):
            budget.claim(symbol, identifier)
    budget.claim('PDD:US', '5' * 32)
    with pytest.raises(AdmissionDenied, match='ALREADY_CLAIMED'):
        budget.claim('PDD:US', '6' * 32)
    assert budget.snapshot()['analyses'] == {'LI:US': OLD, 'PDD:US': '5' * 32}


@pytest.mark.parametrize('endpoint', ['chat', 'responses'])
def test_shared_24_attempt_cap_and_original_history_with_new_http(history, endpoint):
    path, original = history; budget = Budget(path); admit(budget, path); sends=[]
    with httpx.Client(transport=httpx.MockTransport(lambda req: sends.append(req) or httpx.Response(200))) as client:
        with guarded_transport(budget), analysis_run(NEW):
            for _ in range(25):
                try:
                    req = request() if endpoint=='chat' else request(dict(model='gpt-6-luna',input='synthetic',
                        max_output_tokens=40,store=False,service_tier='default'),url='https://api.openai.com/v1/responses')
                    client.send(req)
                except AdmissionDenied: pass
    snapshot = budget.snapshot()
    assert len(sends) == 17 and snapshot['attempt_count'] == 24
    assert snapshot['attempts'][:7] == original['attempts']
    assert {a['analysis_run_id'] for a in snapshot['attempts'][7:]} == {NEW}
    assert float(snapshot['reserved_usd']) <= 3


def test_cost_cap_also_includes_the_old_reservation(history):
    path, _ = history; budget = Budget(path); admit(budget, path)
    with analysis_run(NEW):
        payload=dict(model='gpt-6.1-sol', messages=[dict(role='user',content='synthetic')],
            max_completion_tokens=128000, store=False, service_tier='default')
        budget.before_send(request(payload))
        with pytest.raises(AdmissionDenied, match='BUDGET_EXHAUSTED'):
            budget.before_send(request(payload))
    assert budget.snapshot()['attempt_count'] == 8
    assert float(budget.snapshot()['reserved_usd']) <= 3


def test_sdk_retry_attempts_remain_separately_reserved(history, monkeypatch):
    path, original = history; budget = Budget(path); admit(budget, path); sends=[]
    monkeypatch.setattr('time.sleep', lambda *_: None)
    def receive(req):
        sends.append(req)
        assert budget.snapshot()['attempt_count'] == 7 + len(sends)  # committed before transport
        return httpx.Response(500, json={'error':{'message':'synthetic'}})
    with guarded_transport(budget), analysis_run(NEW):
        with openai.OpenAI(api_key='synthetic', max_retries=1,
                http_client=httpx.Client(transport=httpx.MockTransport(receive))) as client:
            with pytest.raises(openai.InternalServerError):
                client.chat.completions.create(model='gpt-6-luna', messages=[dict(role='user',content='synthetic')],
                    max_completion_tokens=10, store=False, service_tier='default')
    assert len(sends) == 2
    assert budget.snapshot()['attempts'][:7] == original['attempts']
    assert budget.snapshot()['attempt_count'] == 9


def test_concurrent_registration_across_processes_has_one_winner(history):
    path, original = history; expected = digest(path)
    code = '''
import sys
from app.llm_acceptance import Budget, AdmissionDenied
try:
 b=Budget(sys.argv[1],existing_only=True)
 b.claim_retest('LI:US',sys.argv[2],authorization_id='a'*32,expected_ledger_sha256=sys.argv[3])
 print('CLAIMED')
except AdmissionDenied: print('DENIED')
'''
    children = [subprocess.Popen([sys.executable, '-c', code, str(path), c * 32, expected],
        stdout=subprocess.PIPE, text=True) for c in '2345']
    results = [child.communicate(timeout=30)[0].strip() for child in children]
    assert all(child.returncode == 0 for child in children)
    assert results.count('CLAIMED') == 1 and results.count('DENIED') == 3
    state = Budget(path).snapshot()
    assert state['analyses'] == original['analyses'] and state['attempts'] == original['attempts']


def test_interrupted_migration_preserves_recovery_and_refuses_restart(history, monkeypatch):
    path, _ = history; before = path.read_bytes(); budget = Budget(path)
    dump = json.dump
    def interrupt(value, stream, **kwargs):
        if isinstance(value, dict) and value.get('ledger_schema') == 2:
            stream.write('{"ledger_schema":'); stream.flush()
            raise OSError('synthetic interrupted write')
        return dump(value, stream, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(json, 'dump', interrupt)
        with pytest.raises(OSError): admit(budget, path)
    assert budget.runs == set()
    assert json.loads(Path(str(path)+'.pre-li-retest').read_text())['original_ledger_utf8'].encode() == before
    with pytest.raises(AdmissionDenied, match='LEDGER_INVALID'): Budget(path)
    assert path.read_bytes() != before  # never auto-restored/replaced after interruption


@pytest.mark.parametrize('change', ['count', 'old_id', 'new_id', 'cross_symbol', 'schema'])
def test_corrupt_extended_ledger_fails_closed(history, change):
    path, _ = history; budget = Budget(path); admit(budget, path)
    state=json.loads(path.read_text())
    if change=='count': state['reserved_nanos']-=1
    elif change=='old_id': state['li_retest']['original_run_id']='f'*32
    elif change=='new_id': state['li_retest']['analysis_run_id']=OLD
    elif change=='cross_symbol': state['li_retest']['symbol']='PDD:US'
    else: state['ledger_schema']=3
    path.write_text(json.dumps(state))
    with pytest.raises(AdmissionDenied, match='LEDGER_INVALID'): Budget(path)


def test_retest_cannot_create_a_new_budget(tmp_path):
    path=tmp_path/'missing'
    with pytest.raises(AdmissionDenied, match='LEDGER_REQUIRED'): Budget(path, existing_only=True)
    assert not path.exists()


def test_retest_entry_uses_original_single_analysis_without_snapshot(history, monkeypatch):
    path, _ = history; seen=[]
    spec=importlib.util.spec_from_file_location('li_retest_entry',Path(__file__).parents[1]/'scripts/v42_peer_acceptance.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    from pocs.chatgpt_skills import analysis
    async def analyze(database, portfolio, name, options, **kwargs):
        seen.append((name, options))
        return dict(status='partial', issues=[], conclusion='synthetic', professional_result={})
    monkeypatch.setattr(analysis, 'analyze', analyze)
    result=asyncio.run(module.run_li_retest('unused', 7, Budget(path), identifier=NEW,
        authorization_id=AUTH, expected_ledger_sha256=digest(path), synthetic=True))
    assert len(seen)==len(result)==1 and seen[0][0]=='analyze_position'
    assert seen[0][1]['symbol']=='LI:US' and 'snapshot_id' not in seen[0][1]
    assert Budget(path).snapshot()['attempt_count']==7
    with pytest.raises(AdmissionDenied):
        asyncio.run(module.run_li_retest('unused', 7, Budget(path), identifier='3'*32,
            authorization_id=AUTH, expected_ledger_sha256=digest(path), synthetic=True))
    assert len(seen)==1


def test_process_local_run_mutation_cannot_register_a_retest(history):
    path, _ = history; budget=Budget(path)
    budget.runs.add(NEW)  # adversarial attempt, not an admission mechanism
    with analysis_run(NEW), pytest.raises(AdmissionDenied, match='RUN_UNASSOCIATED'):
        budget.before_send(request())
    assert budget.snapshot()['attempt_count']==7 and 'li_retest' not in budget.snapshot()


def test_backup_left_before_commit_requires_review_not_silent_retry(history):
    path, _=history; before=path.read_bytes(); budget=Budget(path)
    backup=Path(str(path)+'.pre-li-retest');backup.write_text('interrupted recovery artifact');backup.chmod(0o600)
    with pytest.raises(AdmissionDenied, match='MIGRATION_RECOVERY_REQUIRED'):
        admit(budget,path)
    assert path.read_bytes()==before and budget.runs==set()


@pytest.mark.parametrize('extra', [[], ['--allow-li-retest', '--snapshot-id', 'asset-view:v1:65']])
def test_cli_rejects_missing_explicit_retest_permission_or_any_snapshot(tmp_path, extra):
    path=tmp_path/'must-not-create'
    command=[sys.executable, 'scripts/v42_peer_acceptance.py', '--database', 'unused', '--portfolio-id', '7',
        '--contract', 'unused', '--operator-env', 'unused', '--ledger', str(path), '--output', 'unused',
        '--allow-paid-acceptance', '--li-retest-run-id', NEW, '--li-retest-authorization-id', AUTH,
        '--expected-ledger-sha256', '0'*64] + extra
    result=subprocess.run(command,capture_output=True,text=True,timeout=20)
    assert result.returncode==2 and 'error:' in result.stderr
    assert not path.exists()
