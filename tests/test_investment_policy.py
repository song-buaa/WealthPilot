"""Only fictional policy data. No approved private source bytes/hashes in Git."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal as D
import hashlib
import sqlite3

import pytest

from backend.services.investment_policy.contracts import MetricEvidence, canonical, digest
from backend.services.investment_policy.evaluation import budget_preview, evaluate_snapshot
from backend.services.investment_policy.parser import PolicyError, derive
from backend.services.investment_policy.service import PolicyService
from backend.services.investment_policy.store import PolicyStore

AT = "2031-01-01T00:00:00+00:00"
LATER = "2031-01-02T00:00:00+00:00"
PHILOSOPHY = b"# SYNTHETIC ONLY\nAPPROVED_CONTENT SYSTEM_NOT_ACTIVE\nContent fixture\n"
# Deliberately different budgets and thresholds from any user's actual policy.
PHILOSOPHY += "内容批准日期：2030-12-01\n".encode()
POLICY = """# SYNTHETIC ONLY -- NOT INVESTMENT ADVICE
APPROVED_CONTENT SYSTEM_NOT_ACTIVE
内容批准日期：2030-12-01
## 1. 范围
### 1.2 分母
全部合格可投资资产
## 2. 预算
### 2.1 中性战略预算
| 项目 | 目标 | 解释 |
|---|---|---|
| 美国股票ETF子组合 | 25% | synthetic |
| 美国直接个股（主动投资） | 25% | synthetic |
| 中国公司主动精选个股 | 4% | synthetic |
| 黄金 | 7% | synthetic |
| BTC现货 | 4% | synthetic |
| IB01短期美国国债ETF | 25% | synthetic |
| CBU3短期美国国债ETF | 10% | synthetic |
| 未投资交易现金 | 0% | synthetic |
| 合计 | 100% | 65%增长及另类＋35%稳健 |
### 2.2 弹性
0% ≤ C < 12%
0% ≤ A ≤ 15%
C＋A ≤ 20%
U＝65%－C－A，U≥45%
### 2.3 承接
未用预算优先由美国ETF承接
## 3. 细分
### 3.1 ETF
AAA／BBB／CCC按ETF子组合内部20%／60%／20%配置
### 3.2 直接股票
| 发行人 | 目标 |
|---|---|
| SYN1 | 3% |
| SYN2 | 3% |
| SYN3 | 4% |
| SYN4 | 4% |
| SYN5 | 4% |
| SYN6 | 4% |
| 其他自主选股 | 3% |
| 合计 | 25% |
已指定六股合计22%
### 3.3 地区
不直接持有中国股票ETF
## 4. 产品
所有产品仍需单独准入
## 5. 资金
### 5.1 现金
现金总可投资资产2%以内，占用稳健预算，不额外增加总配置
### 5.2 偿债
按原币到期现金流评估，不推断偿债能力
### 5.3 禁止
为股票投资新增消费信用贷；在没有明确交易授权时执行任何真实交易。
不继承旧的杠杆档位。
## 6. 风险
### 6.1 股票
| 对象 | 触发 | 动作 |
|---|---|---|
| 普通股票发行人 | 严格超过10% | WARNING |
| 特别发行人SYN1 | 严格超过12% | WARNING |
| 任何股票发行人 | 达到或超过15% | HUMAN_REVIEW |
### 6.2 BTC
| 条件 | 动作 |
|---|---|
| 目标 | 无动作 |
| 严格超过5% | WARNING |
| 达到或超过6% | HUMAN_REVIEW |
### 6.3 偏离
一级大类绝对偏离严格超过9个百分点；年度做一次全面配置复核。
## 7. 行为
### 7.1 研究
成本回撤不设固定百分比，不因亏损自动交易
### 7.2 执行
不重新引入旧规则，交易独立授权
### 7.3 退出
任何个股均不要求永久保留固定底仓，不设账户熔断
## 8. 过渡
过渡信息不作为长期目标表
## 9. 绩效
目标不是保证收益
## 10. 权限
两份Markdown为源，索引不是政策
""".encode()


def hashes(a=PHILOSOPHY, b=POLICY):
    return {"philosophy": hashlib.sha256(a).hexdigest(), "policy": hashlib.sha256(b).hexdigest()}


def snapshot(data=POLICY, version=1):
    return derive(PHILOSOPHY, data, expected_hashes=hashes(b=data), policy_version=version)


@pytest.fixture
def staged(tmp_path):
    tmp_path.chmod(0o700)
    a, b = tmp_path / 'synthetic_philosophy.md', tmp_path / 'synthetic_policy.md'
    a.write_bytes(PHILOSOPHY)
    b.write_bytes(POLICY)
    store = PolicyStore(tmp_path / 'candidate.sqlite')
    store.init_candidate()
    args = dict(scope='SYNTHETIC', version=1, philosophy_path=a, policy_path=b,
                expected_hashes=hashes(), request_id='stage-1', expected_head_version=0, created_at=AT)
    bundle = store.stage(**args)
    return store, args, bundle


def qualified(number, **changes):
    return MetricEvidence(value=D(number), complete=True, fresh=True, identity_verified=True,
        denominator_qualified=True, as_of=AT).model_copy(update=changes)


def evaluate(s=None, *, weight='0', symbol='SYN2', btc='0', deviation='0', **changes):
    args = dict(metrics={'issuer_weight': qualified(weight), 'btc_weight': qualified(btc),
                         'major_class_absolute_deviation': qualified(deviation)},
                issuer_symbol=symbol, behavior_facts={'new_consumer_credit_for_equities': False, 'unauthorized_real_trade': False},
                as_of=AT, max_age=timedelta(days=1))
    args.update(changes)
    return evaluate_snapshot(s or snapshot(), **args)


def test_derivation_closure_and_full_source_provenance():
    s = snapshot()
    assert sum(s.allocation.neutral.values()) == 1
    assert s.allocation.etf_internal == {'AAA': D('.2'), 'BBB': D('.6'), 'CCC': D('.2')}
    assert s.allocation.specified_equity_total == D('.22')
    assert len(s.rules) == 8
    assert all(r.source_clause.source_sha256 == hashes()['policy'] and r.source_clause.line_start > 0 for r in s.rules)
    assert 'philosophy' in s.contextual_clauses
    assert all(r.auto_trade_allowed is False for r in s.rules)


@pytest.mark.parametrize('old,new', [('25%', '26%'), ('20%／60%／20%', '25%／60%／20%'),
                                     ('合计22%', '合计21%'), ('U≥45%', 'U≥44%'),
                                     ('0% ≤ C < 12%', '0% ≤ C < 3%'), ('占用', '不使用')])
def test_budget_or_grammar_changes_rejected(old, new):
    with pytest.raises(ValueError):
        snapshot(POLICY.decode().replace(old, new).encode())


def test_hash_approval_and_missing_rules_fail_closed():
    with pytest.raises(PolicyError, match='SHA256'):
        derive(PHILOSOPHY, POLICY + b'\n', expected_hashes=hashes(), policy_version=1)
    for old in ['APPROVED_CONTENT', '年度做一次全面配置复核', '| 普通股票发行人 | 严格超过10% | WARNING |']:
        with pytest.raises(ValueError):
            snapshot(POLICY.decode().replace(old, '').encode())


@pytest.mark.parametrize('weight,symbol,expected', [('0.10', 'SYN2', 'NO_RULE_TRIGGERED_NOT_EXECUTION_APPROVAL'),
    ('0.11', 'SYN2', 'WARNING'), ('0.15', 'SYN2', 'HUMAN_REVIEW'),
    ('0.115', 'SYN1', 'NO_RULE_TRIGGERED_NOT_EXECUTION_APPROVAL'), ('0.121', 'SYN1', 'WARNING')])
def test_issuer_exception_and_strict_boundaries(weight, symbol, expected):
    result = evaluate(weight=weight, symbol=symbol)
    assert result['status'] == expected
    assert result['auto_trade_allowed'] is False
    if symbol == 'SYN1':
        assert all(r['rule_id'] != 'issuer.warning.default' for r in result['triggers'])


@pytest.mark.parametrize('btc,deviation,expected', [('0.05', '0.09', 'NO_RULE_TRIGGERED_NOT_EXECUTION_APPROVAL'),
    ('0.051', '0', 'WARNING'), ('0.06', '0', 'HUMAN_REVIEW'), ('0', '0.0901', 'HUMAN_REVIEW')])
def test_btc_and_major_class_pp_boundaries(btc, deviation, expected):
    assert evaluate(btc=btc, deviation=deviation)['status'] == expected


@pytest.mark.parametrize('change', [{'complete': False}, {'fresh': False}, {'identity_verified': False},
    {'denominator_qualified': False}, {'as_of': '2032-01-01T00:00:00Z'}, {'as_of': '2020-01-01T00:00:00Z'},
    {'as_of': '2031-01-01'}, {'value': None}])
def test_unknowns_never_clear(change):
    assert evaluate(metrics={'issuer_weight': qualified('0', **change)})['status'] == 'UNKNOWN'


def test_partial_lower_bound_can_trigger_but_cannot_clear():
    r = evaluate(metrics={'issuer_weight': qualified('.2', complete=False)})
    assert r['status'] == 'HUMAN_REVIEW' and r['portfolio_compliance'] == 'UNKNOWN'
    assert r['triggers'][0]['coverage'] == 'LOWER_BOUND_ONLY'
    assert evaluate(issuer_symbol=None)['status'] == 'UNKNOWN'
    deviation = evaluate(metrics={'major_class_absolute_deviation': qualified('.5', complete=False)})
    assert deviation['status'] == 'UNKNOWN'
    assert not deviation['triggers']


def test_hard_block_is_only_explicit_prohibition_or_independent_safety():
    r = evaluate(weight='.5', existing_safety_blocks=('PRICE_TTL', 'HUMAN_CONFIRMATION_REQUIRED'))
    assert r['existing_safety_blocks'] == ['PRICE_TTL', 'HUMAN_CONFIRMATION_REQUIRED']
    assert all(t['action'] != 'HARD_BLOCK' for t in r['triggers'])
    r = evaluate(behavior_facts={'new_consumer_credit_for_equities': True, 'unauthorized_real_trade': False})
    assert r['status'] == 'HARD_BLOCK'
    assert r['auto_trade_allowed'] is False


def test_cash_and_unused_active_budget_are_closed():
    s = snapshot()
    r = budget_preview(s, china_used=D('.02'), unallocated_us_used=D('0'), temporary_cash=D('.01'))
    assert sum(map(D, r['budget'].values())) == 1
    assert D(r['budget']['us_etf']) == D('.30')
    assert D(r['budget']['stable_products']) == D('.34')
    assert r['stable_product_split'].startswith('UNKNOWN')
    with pytest.raises(PolicyError):
        budget_preview(s, china_used=D('.2'), unallocated_us_used=D('0'), temporary_cash=D('0'))


def test_stage_idempotency_cas_scope_and_no_active(staged):
    store, args, b = staged
    assert store.stage(**args) == b
    with pytest.raises(PolicyError, match='REQUEST_ID_CONFLICT'):
        store.stage(**{**args, 'created_at': LATER})
    with pytest.raises(PolicyError, match='CAS'):
        store.stage(**{**args, 'request_id': 'new-id'})
    with pytest.raises(PolicyError):
        store.read_bundle('OTHER_SCOPE', b['bundle_id'])
    assert PolicyService(store).get_active_policy_context('SYNTHETIC', as_of=LATER)['status'] == 'NOT_ACTIVE'
    assert PolicyService().get_active_policy_context('SYNTHETIC')['status'] == 'NOT_ACTIVE'


def test_derivative_edit_cannot_stage(staged):
    store, args, b = staged
    tampered = deepcopy(b['snapshot'])
    tampered['rules'][0]['threshold'] = '0.99'
    with pytest.raises(PolicyError, match='DERIVATIVE_MISMATCH'):
        store.stage(**{**args, 'supplied_snapshot': tampered})


def test_immutable_store_and_no_activation_api(staged):
    store, args, b = staged
    for table in ('policy_bundles', 'policy_events'):
        with pytest.raises(sqlite3.IntegrityError, match='IMMUTABLE'):
            with store.connection(write=True) as db:
                db.execute(f'DELETE FROM {table}')
    with pytest.raises(PolicyError, match='ACTIVATION_FORBIDDEN'):
        store.transition(scope=args['scope'], bundle_id=b['bundle_id'], state='ACTIVE', expected_state='DRAFT', request_id='bad', at=LATER)
    with pytest.raises(FileExistsError):
        store.init_candidate()


def validate_approve(store, b):
    common = dict(scope='SYNTHETIC', bundle_id=b['bundle_id'], at=AT)
    v = store.transition(**common, state='VALIDATED', expected_state='DRAFT', request_id='validate-'+str(b['bundle_version']))
    assert store.transition(**common, state='VALIDATED', expected_state='DRAFT', request_id='validate-'+str(b['bundle_version'])) == v
    return store.transition(**common, state='APPROVED', expected_state='VALIDATED', request_id='approve-'+str(b['bundle_version']), approval_ref='SYNTHETIC_REVIEW_ONLY')


def synthetic_release(store, b, sequence, action='RELEASE', effective_at=AT):
    # Test-only deployment fixture, never offered by P1 CLI/service or run on real policy.
    value = dict(scope='SYNTHETIC', bundle_id=b['bundle_id'], bundle_sha256=digest(b),
                 release_approval_ref='SYNTHETIC_TEST_FIXTURE', action=action,
                 recorded_at=AT, effective_at=effective_at, deployed_at=AT)
    with store.connection(write=True) as db:
        db.execute('INSERT INTO policy_releases VALUES(?,?,?,?,?)', ('SYNTHETIC', sequence, b['bundle_id'], canonical(value), digest(value)))


def test_read_only_context_restart_history_and_rollback_fixture(staged):
    store, args, b = staged
    validate_approve(store, b)
    synthetic_release(store, b, 1)
    context = PolicyService(store).get_active_policy_context('SYNTHETIC', as_of=LATER)
    assert context['status'] == 'ACTIVE'
    assert context['policy_reference']['bundle_version'] == 1
    assert PolicyService(store).get_active_policy_context('SYNTHETIC', as_of=LATER, expected_bundle_sha='stale')['status'] == 'UNAVAILABLE'
    before = store.path.read_bytes()
    assert PolicyService(PolicyStore(store.path)).get_active_policy_context('SYNTHETIC', as_of=LATER) == context
    assert store.path.read_bytes() == before
    b2 = store.stage(**{**args, 'version': 2, 'expected_head_version': 1, 'request_id': 'stage-2'})
    validate_approve(store, b2)
    synthetic_release(store, b2, 2)
    assert PolicyService(store).get_active_policy_context('SYNTHETIC', as_of=LATER)['policy_reference']['bundle_version'] == 2
    synthetic_release(store, b, 3, 'ROLLBACK')
    assert PolicyService(store).get_active_policy_context('SYNTHETIC', as_of=LATER)['policy_reference']['bundle_version'] == 1
    assert store.read_bundle('SYNTHETIC', b['bundle_id']) == b
    with pytest.raises(sqlite3.IntegrityError, match='IMMUTABLE'):
        with store.connection(write=True) as db:
            db.execute('DELETE FROM policy_releases')


def test_derivative_and_source_revalidation_not_cached(staged):
    store, _, b = staged
    validate_approve(store, b)
    synthetic_release(store, b, 1)
    with sqlite3.connect(store.path) as db:
        db.execute('DROP TRIGGER policy_bundles_no_update')
        data = deepcopy(b)
        data['snapshot']['rules'][0]['threshold'] = '0.99'
        data['structured_sha256'] = digest(data['snapshot'])
        db.execute('UPDATE policy_bundles SET payload=?,payload_sha=?', (canonical(data), digest(data)))
    assert PolicyService(store).get_active_policy_context('SYNTHETIC', as_of=LATER)['status'] == 'UNAVAILABLE'


def test_sources_remain_frozen_after_editor_changes(staged):
    store, args, b = staged
    args['policy_path'].write_bytes(POLICY + b'\nEDIT')
    assert store.read_bundle(args['scope'], b['bundle_id']) == b


def test_context_missing_store_zero_write(tmp_path):
    path = tmp_path / 'not-created.sqlite'
    result = PolicyService(PolicyStore(path)).get_active_policy_context('SYNTHETIC', as_of=LATER)
    assert result['status'] == 'UNAVAILABLE' and not path.exists()


def test_no_policy_legacy_defaults_import_or_business_wiring():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for path in (root/'backend/services/investment_policy').glob('*.py'):
        text = path.read_text()
        assert 'from app.discipline.config import' not in text
        assert 'from app.database import' not in text
        assert 'from backend.main import' not in text
    # Legacy engine behavior remains its own untouched contract; not repurposed to eight percent.
    from app.discipline.config import _DEFAULT_RULES
    assert _DEFAULT_RULES['single_asset_limits']['max_position_pct'] == .40
    assert all(r.action != 'HARD_BLOCK' for r in snapshot().rules if r.metric == 'issuer_weight')


def test_migration_never_merges_or_mutates_legacy_or_independent_guards():
    from backend.services.investment_policy.migration import migration_preview
    legacy = {'single_asset_limits': {'max_position_pct': .40}, 'portfolio_circuit_breaker': {'drawdown_trigger_pct': .25}}
    before = deepcopy(legacy)
    preview = migration_preview(legacy, bundle_reference={'bundle_id': 'SYNTHETIC_ONLY'})
    assert preview['writes_performed'] is False and legacy == before
    assert preview['independent_execution_safety'] == 'PRESERVE_ALL_EXISTING_GATES'
    assert {r['decision'] for r in preview['rows']} >= {f'D{i:02}' for i in range(1, 10)}
    assert migration_preview(None, bundle_reference={})['production_private_config'] == 'UNKNOWN'


def test_consumer_consistency_rejects_mixed_legacy_and_cached_versions(staged):
    from backend.services.investment_policy.migration import check_consumer_versions
    store, _, b = staged
    validate_approve(store, b)
    synthetic_release(store, b, 1)
    active = PolicyService(store).get_active_policy_context('SYNTHETIC', as_of=LATER)
    receipts = {name: deepcopy(active) for name in ['web', 'peer', 'mcp', 'allocation', 'discipline']}
    assert check_consumer_versions(receipts)['status'] == 'CONSISTENT_REFERENCES_ONLY'
    receipts['peer']['policy_reference']['bundle_version'] = 99
    assert check_consumer_versions(receipts)['reason'] == 'MIXED_POLICY_VERSIONS'
    receipts['peer'] = {'status': 'LEGACY_DEFAULT'}
    assert check_consumer_versions(receipts)['status'] == 'UNAVAILABLE'


def test_lifecycle_conflict_clock_and_unapproved_release(staged):
    store, args, b = staged
    synthetic_release(store, b, 1)
    assert PolicyService(store).get_active_policy_context('SYNTHETIC', as_of=LATER)['status'] == 'UNAVAILABLE'
    with pytest.raises(PolicyError, match='LIFECYCLE_CAS'):
        store.transition(scope=args['scope'], bundle_id=b['bundle_id'], state='APPROVED', expected_state='VALIDATED', request_id='early', at=AT, approval_ref='SYNTHETIC')
    store.transition(scope=args['scope'], bundle_id=b['bundle_id'], state='VALIDATED', expected_state='DRAFT', request_id='validate', at=LATER)
    with pytest.raises(PolicyError, match='CLOCK_REVERSED'):
        store.transition(scope=args['scope'], bundle_id=b['bundle_id'], state='APPROVED', expected_state='VALIDATED', request_id='reversed', at=AT, approval_ref='SYNTHETIC')


def test_future_release_not_current_policy(staged):
    store, _, b = staged
    validate_approve(store, b)
    synthetic_release(store, b, 1, effective_at='2040-01-01T00:00:00Z')
    assert PolicyService(store).get_active_policy_context('SYNTHETIC', as_of=LATER)['status'] == 'NOT_ACTIVE'


def test_content_approval_cannot_be_backdated_into_older_bundle(staged):
    store, args, _ = staged
    with pytest.raises(PolicyError, match='PREDATES_CONTENT_APPROVAL'):
        store.stage(**{**args, 'created_at': '2020-01-01T00:00:00Z', 'request_id': 'backdated'})
