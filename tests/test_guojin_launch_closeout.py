"""Launch policy: synthetic broker facts; no real account or GUI fixtures."""
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path

import pytest
from tests.test_guojin_native_sync import native_api, env, facts, publish, refresh_hash
from tests.test_guojin_daily_sync import daily, execute, _source_clock
from tests.guojin_native_fixtures import make_batch, write_json


def test_exact_and_accepted_valuation_difference_not_balancing_cash(env):
    from services.broker_sync.guojin.native_exports import reconcile_assets
    exact = reconcile_assets(Decimal('300000'), Decimal('0'), Decimal('300000'), Decimal('300000'))
    assert exact['status'] == 'PASS'
    # Same 158.27 difference against a synthetic independent total, not a live fixture.
    warning = reconcile_assets(Decimal('300158.27'), Decimal('0'), Decimal('300000'), Decimal('300000'))
    assert warning['status'] == 'WARNING' and warning['difference_cny'] == '158.27'
    assert warning['details_total_cny'] == '300158.27' and warning['account_total_assets_cny'] == '300000'
    assert warning['cash_balance_cny'] == '0' and Decimal(warning['difference_ratio']) < Decimal('.005')


@pytest.mark.parametrize('total,difference,allowed',[
    ('300000','1500',True),('300000','1500.01',False),
    ('1000000','2000',True),('1000000','2000.01',False),
    ('10000','50',True),('10000','50.01',False),
    ('300000','-158.27',True),('0','1',False)])
def test_both_warning_limits_required(env,total,difference,allowed):
    from services.broker_sync.guojin.native_exports import reconcile_assets
    args=(Decimal(total)+Decimal(difference),Decimal(0),Decimal(total),Decimal(total))
    if allowed: assert reconcile_assets(*args)['status'] == 'WARNING'
    else:
        with pytest.raises(ValueError,match='NATIVE_RECONCILIATION_FAILED'): reconcile_assets(*args)


def test_funds_header_contradiction_is_not_valuation_warning(env):
    from services.broker_sync.guojin.native_exports import reconcile_assets
    with pytest.raises(ValueError,match='NATIVE_RECONCILIATION_FAILED'):
        reconcile_assets(Decimal('300158.27'),Decimal('100'),Decimal('300000'),Decimal('300000'))


def valuation_warning(c):
    # Alter only independent broker header totals; keep every exported row/cash fact.
    f=c.directory/'stock.txt'
    data=f.read_bytes().decode('gb18030').replace('股票市值：1460.00','股票市值：1455.00').replace('总 资 产：1490.00','总 资 产：1485.00')
    f.write_bytes(data.encode('gb18030'));refresh_hash(c,'stock')
    c.receipt['account_total_assets_cny']='1485.00';write_json(c.directory/'batch.json',c.receipt)


def test_warning_publication_preserves_original_amounts_and_is_idempotent(env):
    valuation_warning(env)
    first=publish(env);assert first.status=='success',first.error
    assert first.meta['reconciliation']['status']=='WARNING'
    before=facts(env)
    assert publish(env).status=='success' and facts(env)==before
    from app.models import Position
    from backend.api.broker_sync import _public_quality
    with env.sessions() as db:
        assert sum(p.market_value_cny for p in db.query(Position))==1490
        assert db.query(Position).filter_by(symbol='CASH-CNY:CN').one().market_value_cny==30
        assert _public_quality(db.get(env.api.Run,first.id))['reconciliation']['account_total_assets_cny']=='1485.00'


@pytest.mark.parametrize('fault',['account','security','missing','hash'])
def test_warning_never_downgrades_nonvaluation_source_failure(env,fault):
    assert publish(env).status=='success';before=facts(env)
    valuation_warning(env)
    if fault=='account':env.receipt['files']['hgt']['account_id']='unrelated-owner';write_json(env.directory/'batch.json',env.receipt)
    if fault=='security':
        f=env.directory/'sgt.xls';f.write_bytes(f.read_bytes().replace('深HK'.encode('gb18030'),'沪HK'.encode('gb18030')));refresh_hash(env,'sgt')
    if fault=='missing':(env.directory/'hgt.xls').unlink()
    if fault=='hash':env.receipt['files']['hgt']['sha256']='0'*64;write_json(env.directory/'batch.json',env.receipt)
    assert publish(env).status=='failed' and facts(env)==before


def as_observed(c, *, missing_security=False, zero=False):
    receipt=json.loads((c.directory/'batch.json').read_text())
    receipt['schema']='guojin.native-batch.v2'
    for e in receipt['files'].values():
        action=e['queried_at']
        e.update(complete=False,queried_at=None,query_action_at=action,observed_at=action,exported_at=action,
            source_time_quality='UNKNOWN',counter_status='UNKNOWN',row_count_basis='EXPORTED_ROWS',
            query_evidence=dict(page_verified=True,account_verified=True,readonly_action_verified=True,pid=7,hwnd=10,dpi=192))
    if missing_security or zero:
        f=c.directory/'sgt.xls';lines=f.read_bytes().splitlines(keepends=True)
        if missing_security:
            f.write_bytes(lines[0]);receipt['files']['sgt']['row_count']=0
        else:
            row=lines[1].decode('gb18030').split('\t')
            for n in (3,4,5,8,9,10):row[n]='0.00'
            f.write_bytes(lines[0]+'\t'.join(row).encode('gb18030'))
        receipt['files']['sgt']['sha256']=__import__('hashlib').sha256(f.read_bytes()).hexdigest()
        # Header now reports the observed partial rows, retaining actual cash.
        stock=c.directory/'stock.txt'
        stock.write_bytes(stock.read_bytes().replace(b'1460.00',b'1300.00').replace(b'1490.00',b'1330.00'))
        receipt['files']['stock']['sha256']=__import__('hashlib').sha256(stock.read_bytes()).hexdigest()
        receipt['account_total_assets_cny']='1330.00'
    write_json(c.directory/'batch.json',receipt)


@pytest.mark.parametrize('mode',['missing','zero'])
def test_unproven_completeness_never_deletes_or_zeroes_existing_holdings(env,mode):
    assert publish(env).status=='success';before=facts(env)
    make_batch(env.root.parent,batch='observed',query=env.query+timedelta(seconds=1))
    env.directory=env.root/'observed'
    as_observed(env,missing_security=mode=='missing',zero=mode=='zero')
    result=publish(env);assert result.status=='success',result.error
    assert result.meta['complete'] is False and result.meta['quality']=='partial'
    assert result.meta['queried_at']=={'stock':None,'hgt':None,'sgt':None}
    assert 'SOURCE_COMPLETENESS_UNKNOWN' in result.meta['issues']
    from app.models import Position
    with env.sessions() as db:
        assert db.query(Position).filter_by(symbol='2015.SGT:HK').one().quantity==2
    assert facts(env)==before
    repeat=publish(env);assert repeat.status=='success' and facts(env)==before


def test_old_observed_batch_cannot_regress_current_owner(env):
    assert publish(env).status=='success';before=facts(env)
    make_batch(env.root.parent,batch='old-observed',query=env.query-timedelta(minutes=1))
    env.directory=env.root/'old-observed';as_observed(env)
    assert publish(env).status=='failed' and facts(env)==before


def test_observed_proof_is_required_and_cannot_claim_completeness(env):
    as_observed(env)
    receipt=json.loads((env.directory/'batch.json').read_text())
    receipt['files']['hgt']['query_evidence']['account_verified']=False
    write_json(env.directory/'batch.json',receipt)
    assert publish(env).status=='failed' and facts(env)==[]
    receipt['files']['hgt']['query_evidence']['account_verified']=True
    receipt['files']['sgt']['complete']=True
    write_json(env.directory/'batch.json',receipt)
    assert publish(env).status=='failed' and facts(env)==[]


def observed_profile(daily):
    path=Path(daily.config['automation']['profile']);p=json.loads(path.read_text())
    p.update(schema='guojin.gui-profile.v2',dpi=192,account_proof='shareholder_allowlist',ocr_identity=True,
        stock_save_menu={'id':'Item 32793','class':'','name':'保存','control_type':'MenuItem'})
    for m in p['markets'].values():
        m.update(source_time_quality='UNKNOWN',complete=False,counter_status='UNKNOWN',query=None,rows=None,success=None,total=None)
    write_json(path,p)


def observed_exporter(daily):
    def export(*args):
        source=daily.exporter(*args);path=source/'observations.json';obs=json.loads(path.read_text())
        obs.update(schema='guojin.gui-observations.v2',account_total_assets_cny=None)
        for e in obs['markets'].values():
            action=e.pop('queried_at');e.pop('query_before')
            e.update(complete=False,queried_at=None,query_action_at=action,observed_at=action,
                source_time_quality='UNKNOWN',counter_status='UNKNOWN',row_count=None,
                query_evidence=dict(page_verified=True,account_verified=True,readonly_action_verified=True,pid=7,hwnd=10,dpi=192))
        write_json(path,obs);return source
    return export


def test_observed_action_windows_remain_independent_and_source_time_unknown(daily,monkeypatch):
    _source_clock(daily,monkeypatch,11,45);observed_profile(daily)
    exporter=observed_exporter(daily)
    midday=execute(daily,trigger='cron',exporter=exporter)
    assert midday['status']=='SUCCESS',midday
    receipt=midday['completed_windows']['midday']
    assert receipt['complete'] is False and all(v is None for v in receipt['queried_at'].values())
    assert receipt['query_action_at'] and receipt['source_time_quality']=='UNKNOWN'
    daily.now+=timedelta(minutes=1)
    assert execute(daily,trigger='cron',exporter=exporter)['status']=='SKIPPED'
    daily.now+=timedelta(hours=5,minutes=44)
    afternoon=execute(daily,trigger='cron',exporter=exporter)
    assert afternoon['status']=='SUCCESS' and afternoon['batch']!=midday['batch'],afternoon
    assert afternoon['completed_windows']['midday']==receipt


@pytest.mark.parametrize('timeout',[False,True])
def test_private_shared_worker_is_hash_bound_and_cleaned(daily,monkeypatch,timeout):
    import base64, gzip, hashlib, stat
    seen=[]
    def fake(argv,timeout):
        if 'status' in argv: return b'VM synthetic running'
        transports=list(Path(daily.config['automation']['shared_root']).glob('wp-guojin-worker-*.gz'))
        assert len(transports)==1
        path=transports[0];seen.append(path)
        assert stat.S_IMODE(path.stat().st_mode)==0o600
        payload=path.read_bytes()
        assert b'function Click-Observed' in gzip.decompress(payload)
        launcher=base64.b64decode(argv[-1]).decode('utf-16le')
        assert hashlib.sha256(payload).hexdigest() in launcher and path.name in launcher
        assert 'ReadAllBytes' in launcher and 'AUTO_EVIDENCE_INVALID' in launcher
        if failure: raise ValueError('AUTO_TIMEOUT')
        return b'{"status":"EXPORTED"}'
    failure=timeout
    monkeypatch.setattr(daily.module,'bounded',fake)
    auto,profile=daily.module.automation_config(daily.config)
    if failure:
        with pytest.raises(ValueError,match='AUTO_TIMEOUT'):
            daily.module.export_windows(daily.config,auto,profile,'synthetic',daily.now)
    else: daily.module.export_windows(daily.config,auto,profile,'synthetic',daily.now)
    assert len(seen)==1 and not seen[0].exists()
