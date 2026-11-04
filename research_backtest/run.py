"""复用既有隔离Docker runner；只读冻结输入，逐次独立目录。"""
from pathlib import Path
from decimal import Decimal as D
from datetime import datetime
from zoneinfo import ZoneInfo
import argparse
import hashlib
import json
import shutil
import subprocess
from collections import Counter
from .contracts import write_new
from .runner import run, docker_command
from .runner import IMAGE


def verify_frozen_bundle(frozen: Path) -> None:
    """检查完整清单和路径；不能让新增未冻结文件绕过检查。"""
    files=json.loads((frozen/'freeze_manifest.json').read_text())['files']
    actual={p.relative_to(frozen).as_posix() for p in frozen.rglob('*') if p.is_file()}
    if not files or actual!=set(files)|{'freeze_manifest.json'}:
        raise ValueError('FROZEN_BUNDLE_INVENTORY_MISMATCH')
    base=frozen.resolve()
    for rel,sha in files.items():
        path=frozen/rel
        if path.is_symlink() or not path.resolve().is_relative_to(base):
            raise ValueError('FROZEN_BUNDLE_PATH_REJECTED')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
            raise ValueError('FROZEN_BUNDLE_HASH_MISMATCH')


def reconcile(frozen: Path, window: Path, name: str, *, report_name: str | None = 'reconciliation.json') -> dict:
    """标准为精确源账本，不以原生舍入后的现金另造PASS。"""
    model=json.loads((frozen/f'{name}_model.json').read_text())
    expected=json.loads((frozen/f'{name}_oracle_before_run.json').read_text())
    actual=json.loads((window/'results/history-observation.json').read_text())
    normal=json.loads((frozen/'NORMALISED_INPUT.json').read_text())
    sessions={s['session_date']:s for s in normal['sessions']}
    check=[]
    def add(label,good,**values):
        check.append(dict(name=label,status='PASS' if good else 'FAIL',**values))
    fills=[e for e in actual['orders'] if e['status'].upper()=='FILLED']
    add('all_planned_orders_filled',len(fills)==len(model['plan'])==actual['total_orders'])
    add('no_rejections',not any(e['status'].upper() in ('INVALID','CANCELED') for e in actual['orders']))
    add('daily_count',len(actual['daily'])==len(expected))
    add('daily_dates',[a['day'] for a in actual['daily']]==[e['day'] for e in expected])
    by_plan={(p['symbol'],p['execution_date']):p for p in model['plan']}
    source={s:{r['session_date']:r for r in rows} for s,rows in normal['rows'].items()}
    submitted=[e for e in actual['orders'] if e['status'].upper()=='SUBMITTED']
    submitted_ids=[e.get('id') for e in submitted];filled_ids=[e.get('id') for e in fills]
    add('filled_order_id_bijection',None not in submitted_ids and None not in filled_ids and
        len(submitted_ids)==len(set(submitted_ids))==len(model['plan']) and
        len(filled_ids)==len(set(filled_ids))==len(model['plan']) and set(submitted_ids)==set(filled_ids))
    add('submitted_orders_terminal',bool(submitted_ids) and all(
        sum(e.get('id')==i and e['status'].upper()=='FILLED' for e in actual['orders'])==1
        and not any(e.get('id')==i and e['status'].upper() not in ('SUBMITTED','FILLED') for e in actual['orders'])
        for i in submitted_ids))
    fill_keys=[]
    for e in fills:
        t=datetime.fromisoformat(e['utc'].replace('Z','+00:00'))
        if t.tzinfo is None:t=t.replace(tzinfo=ZoneInfo('UTC'))
        fill_keys.append((e['symbol'],t.astimezone(ZoneInfo('America/New_York')).date().isoformat()))
    add('plan_fill_bijection',Counter(fill_keys)==Counter((p['symbol'],p['execution_date']) for p in model['plan']))
    for a in fills:
        utc=datetime.fromisoformat(a['utc'].replace('Z','+00:00'))
        if utc.tzinfo is None:utc=utc.replace(tzinfo=ZoneInfo('UTC'))
        day=utc.astimezone(ZoneInfo('America/New_York')).date().isoformat()
        key=(a['symbol'],day);p=by_plan.get(key)
        add('fill_matches_prior_signal',p is not None and p['signal_date']<day,actual=a)
        if p:
            submitted=[e for e in actual['orders'] if e.get('id')==a.get('id') and e['status'].upper()=='SUBMITTED']
            expected_signal=datetime.fromisoformat(p['signal_date']+'T17:05:00').replace(tzinfo=ZoneInfo('America/New_York'))
            add('submitted_after_prior_close',len(submitted)==1 and
                datetime.fromisoformat(submitted[0]['utc'].replace('Z','+00:00'))==expected_signal)
            add('fill_quantity',D(a['quantity'])==D(p['quantity']))
            add('fill_at_real_session_open',utc==datetime.fromisoformat(sessions[day]['open']),utc=a['utc'])
            add('raw_open_price',abs(D(a['price'])-D(source[a['symbol']][day]['open']))<=D('0.000001'))
        add('fee_per_fill',D(a['fee'])==1)
    for e,a in zip(expected,actual['daily']):
        day=e['day'];cash=sum((D(v.get('USD','0')) for v in a['cash'].values()),D(0))
        add(day+':closed_bar_time',datetime.fromisoformat(a['utc'].replace('Z','+00:00'))==datetime.fromisoformat(sessions[day]['close']))
        add(day+':shares',set(a['holdings'])==set(e['holdings']) and
            all(D(a['holdings'][s])==D(e['holdings'][s]) for s in e['holdings']))
        for s in model['symbols']:
            add(day+':raw_close:'+s,D(a['closes'][s])==D(source[s][day]['close']))
        add(day+':fee',abs(D(a['fees_cumulative'])-D(e['fees_cumulative']))<=D('0.000001'))
        add(day+':nonnegative_cash',all(D(v.get('USD','0'))>=0 for v in a['cash'].values()))
        for label,want,got in [('economic_cash',D(e['economic_cash']),cash),('paid_cash',D(e['paid_cash']),D(a['cash']['settled'].get('USD','0'))),('nav',D(e['nav']),D(a['nav']))]:
            delta=got-want
            add(day+':'+label,abs(delta)<=D('0.000001'),expected=str(want),actual=str(got),delta=str(delta))
        mv=sum(D(a['holdings'][s])*D(a['closes'][s]) for s in model['symbols'])
        add(day+':native_nav_identity',abs(D(a['nav'])-cash-mv)<=D('0.000001'))
    for o in actual['opens']:
        add(o['day']+':open_point',not o['full_bar_seen'] and datetime.fromisoformat(o['utc'].replace('Z','+00:00'))==datetime.fromisoformat(sessions[o['day']]['open']))
    add('all_open_points_seen',len(actual['opens'])==len(expected))
    add('open_point_dates',[o['day'] for o in actual['opens']]==[e['day'] for e in expected])
    distributions=[]
    expected_splits={(s,r['session_date']):1/D(r['split']) for s,rs in normal['rows'].items()
        for r in rs if D(r.get('split','1'))!=1 and model.get('start','0000')<=r['session_date']}
    occurred=[a for a in actual['actions'] if a['type']=='SPLIT' and a.get('split_type') in ('SPLIT_OCCURRED','SplitOccurred')]
    add('native_split_count',len(occurred)==len(expected_splits))
    for a in occurred:
        day=datetime.fromisoformat(a['utc'].replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat()
        add('native_split_ratio',D(a['factor'])==expected_splits.get((a['symbol'],day)),actual=a)
    expected_dividends=sum(model.get('start','0000')<=e['ex_dividend_date']<=model.get('end','9999')
        for es in normal['dividends'].values() for e in es)
    add('native_dividend_count',sum(a['type']=='DIVIDEND' for a in actual['actions'])==expected_dividends)
    for a in actual['actions']:
        if a['type']!='DIVIDEND':continue
        utc=datetime.fromisoformat(a['utc'].replace('Z','+00:00'))
        day=utc.astimezone(ZoneInfo('America/New_York')).date().isoformat()
        event=next((e for e in normal['dividends'][a['symbol']] if e['ex_dividend_date']==day),None)
        add('source_dividend_identity',event is not None)
        if event:
            delta=D(a['distribution'])-D(event['amount'])
            distributions.append(dict(symbol=a['symbol'],ex_date=day,source_amount=event['amount'],native_amount=a['distribution'],per_share_delta=str(delta),quantity=a['quantity'],cash_delta=str(delta*D(a['quantity'])),pay_date=event['payment_date']))
            add('precise_dividend_amount',delta==0,**distributions[-1])
    missing_pay=sum(not e.get('payment_date') for es in normal['dividends'].values() for e in es)
    add('payment_dates_complete',missing_pay==0,missing_count=missing_pay)
    report=dict(status='PASS' if all(c['status']=='PASS' for c in check) else 'PARTIAL',
                passed=sum(c['status']=='PASS' for c in check),failed=sum(c['status']=='FAIL' for c in check),
                checks=check,dividend_differences=distributions,
                pay_date_completeness='FAIL_MISSING_PAY_DATES' if missing_pay else 'COMPLETE_IN_SOURCE',tolerance='USD0.000001; exact quantities/timing')
    if report_name is not None:write_new(window/report_name,report)
    return report


def execute(frozen: Path, output: Path, name: str, *, strict_settlement: bool = False) -> dict:
    """无镜像时立即失败，不自动pull/改版本。"""
    verify_frozen_bundle(frozen)
    if strict_settlement:
        normal=json.loads((frozen/'NORMALISED_INPUT.json').read_text())
        missing=sum(not e.get('payment_date') for es in normal['dividends'].values() for e in es)
        reason='UNKNOWN_PAYMENT_DATES' if missing else 'NATIVE_PAY_DATE_PURCHASING_POWER_NOT_VERIFIED'
        # 此锁定原生模型没有合格的支付日适配；不能把研究模式当严格模式运行。
        raise ValueError('STRICT_SETTLEMENT_NOT_QUALIFIED:'+reason)
    for rel,sha in json.loads((frozen/'native_hashes.json').read_text()).items():
        if hashlib.sha256((frozen/'data'/rel).read_bytes()).hexdigest()!=sha:
            raise ValueError('FROZEN_NATIVE_HASH_MISMATCH')
    ready=subprocess.run(docker_command()+['image','inspect',IMAGE],capture_output=True)
    if ready.returncode:raise ValueError('LOCKED_IMAGE_UNAVAILABLE')
    window=output/name;window.mkdir(parents=True,exist_ok=False)
    shutil.copytree(frozen/'data',window/'data')
    shutil.copyfile(frozen/f'{name}_model.json',window/'data/model.json')
    alg=window/'algorithm';alg.mkdir()
    shutil.copyfile(Path(__file__).with_name('algorithm.py'),alg/'main.py')
    shutil.copyfile(Path(__file__).with_name('open_feed.py'),alg/'open_feed.py')
    report=run(window,algorithm_type='AVHistoryBenchmark',
               observation_file='history-observation.json',timeout=900,simulation_orders=True)
    if report['status']=='COMPLETED_REQUIRES_RECONCILIATION':
        return reconcile(frozen,window,name)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('frozen',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--name',choices=['buy_hold','annual_small_rebalance'],required=True)
    p.add_argument('--strict-settlement',action='store_true')
    a=p.parse_args();result=execute(a.frozen,a.output,a.name,strict_settlement=a.strict_settlement)
    print(json.dumps({k:result[k] for k in ('status','passed','failed','run_id','error_class') if k in result},ensure_ascii=False))
