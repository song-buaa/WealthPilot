"""已审资格派生；不复制旧实验报告布局或采集客户端。"""
from pathlib import Path
from decimal import Decimal as D
import json,hashlib
from .contracts import SYMBOLS, write_new
from .run import reconcile, verify_frozen_bundle
from .verify import verify
from .runner import IMAGE
def research_gates(rec: dict, model: dict, normal: dict) -> dict:
    """以完整检查集合派生子门；缺检查不能通过，金额失败单列。"""
    checks=rec.get('checks',[])
    days=sum(model['start']<=s['session_date']<=model['end'] for s in normal['sessions'])
    orders=len(model['plan'])
    splits=sum(D(r.get('split','1'))!=1 and model['start']<=r['session_date']<=model['end']
               for rs in normal['rows'].values() for r in rs)
    dividends=sum(model['start']<=e['ex_dividend_date']<=model['end']
                  for es in normal['dividends'].values() for e in es)
    def combine(states):
        return 'FAIL' if 'FAIL' in states else 'NOT_VERIFIED' if 'NOT_VERIFIED' in states else 'PASS'
    def exact(name,count=1):
        found=[c for c in checks if c['name']==name]
        if len(found)!=count:return 'NOT_VERIFIED'
        return 'PASS' if all(c.get('status')=='PASS' for c in found) else 'FAIL'
    def daily(field):
        found=[c for c in checks if c['name'].endswith(':'+field)]
        if len(found)!=days:return 'NOT_VERIFIED'
        return 'PASS' if all(c.get('status')=='PASS' for c in found) else 'FAIL'
    order_gate=combine([exact(n) for n in ('all_planned_orders_filled','no_rejections',
        'filled_order_id_bijection','submitted_orders_terminal','plan_fill_bijection')]+[exact('fee_per_fill',orders)])
    timing=combine([exact(n,orders) for n in ('fill_matches_prior_signal','submitted_after_prior_close',
        'fill_at_real_session_open','fill_quantity','raw_open_price')]+[
        exact('all_open_points_seen'),exact('open_point_dates'),daily('open_point')])
    raw=combine([exact('daily_count'),exact('daily_dates')]+[daily('raw_close:'+s) for s in model['symbols']])
    accounting=combine([daily(n) for n in ('closed_bar_time','shares','fee','nonnegative_cash','native_nav_identity')])
    actions=combine([exact('native_dividend_count'),exact('native_split_count'),exact('native_split_ratio',splits),
        exact('source_dividend_identity',dividends)])
    core=combine([order_gate,timing,raw,accounting,actions])
    return dict(research_core=core,orders=order_gate,timing=timing,raw=raw,accounting_identity=accounting,actions=actions,
        dividend_precision=exact('precise_dividend_amount',dividends),payment_dates=exact('payment_dates_complete'),
        economic_cash=daily('economic_cash'),paid_cash=daily('paid_cash'),precise_nav=daily('nav'))

def premium_verified(root: Path) -> bool:
    """仅认证实际成功且hash一致的四个具名端点，不信任汇总布尔标签。"""
    required={'TIME_SERIES_DAILY','TIME_SERIES_DAILY_ADJUSTED','DIVIDENDS','SPLITS'};seen=set()
    for p in (root/'requests').glob('*.json'):
        receipt=json.loads(p.read_text());raw=root/'raw'/p.name
        if receipt.get('symbol')!='SPY' or receipt.get('function') not in required:continue
        if (receipt.get('status')!='DATA' or receipt.get('http_status')!=200 or not raw.is_file() or
            hashlib.sha256(raw.read_bytes()).hexdigest()!=receipt.get('response_sha256')):return False
        value=json.loads(raw.read_text());field='Time Series (Daily)' if receipt['function'].startswith('TIME_SERIES') else 'data'
        if field not in value or (field=='Time Series (Daily)' and not value[field]):return False
        seen.add(receipt['function'])
    return seen==required


def run_qualified(window: Path) -> bool:
    """同一研究观察不能替代引擎完成/锁定版本及输入完整性证据。"""
    try:
        record=json.loads((window/'run-manifest.json').read_text())
        if (record.get('image')!=IMAGE or record.get('network')!='none' or
            record.get('status')!='COMPLETED_REQUIRES_RECONCILIATION' or
            record.get('exit_code')!=0 or record.get('attach_exit_code')!=0 or
            not record.get('has_native_output') or not record.get('input_hashes')):return False
        observation=window/'results/history-observation.json'
        if hashlib.sha256(observation.read_bytes()).hexdigest()!=record.get('observation_sha256'):return False
        for rel,sha in record['input_hashes'].items():
            path=window/rel
            if path.is_symlink() or not path.resolve().is_relative_to(window.resolve()):return False
            if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:return False
        return True
    except (OSError,ValueError,TypeError,KeyError):return False


def independent_repeat(first: Path, second: Path) -> bool:
    """同目录或复制同一运行原件不算重跑。"""
    if first.resolve()==second.resolve() or not run_qualified(first) or not run_qualified(second):return False
    a=json.loads((first/'run-manifest.json').read_text());b=json.loads((second/'run-manifest.json').read_text())
    if not a.get('run_id') or not b.get('run_id') or a['run_id']==b['run_id']:return False
    if not a.get('started_at') or not b.get('started_at') or a['started_at']==b['started_at']:return False
    inputs_a={k:v for k,v in a['input_hashes'].items() if k!='config.json'}
    inputs_b={k:v for k,v in b['input_hashes'].items() if k!='config.json'}
    return inputs_a==inputs_b and (first/'results/history-observation.json').read_bytes()==(second/'results/history-observation.json').read_bytes()


def summarize(frozen: Path, runs: Path, output: Path, repeat_runs: Path | None = None) -> dict:
    """薄汇总仅消费冻结件和实际观察，不取数或改变账务。"""
    verify_frozen_bundle(frozen)
    output.mkdir(parents=True,exist_ok=False)
    normal=json.loads((frozen/'NORMALISED_INPUT.json').read_text())
    independent=verify(frozen,output/'independent')
    gates={};repeated={}
    for name in ('buy_hold','annual_small_rebalance'):
        if not run_qualified(runs/name):
            gates[name]={'research_core':'NOT_VERIFIED_ENGINE_OR_INPUT'};repeated[name]=False
            continue
        model=json.loads((frozen/f'{name}_model.json').read_text())
        rec=reconcile(frozen,runs/name,name,report_name=None)
        write_new(output/f'{name}_reconciliation.json',rec)
        gates[name]=research_gates(rec,model,normal)
        repeated[name]=bool(repeat_runs and independent_repeat(runs/name,repeat_runs/name))
    source=json.loads((frozen/'source/events/summary.json').read_text())
    qualified=(set(normal['rows'])==set(SYMBOLS) and set(source)==set(SYMBOLS)
        and all(source[s]['status']=='PASS_SOURCE_INTERNAL_CONSISTENCY' for s in SYMBOLS)
        and independent['status']=='PASS' and all(g['research_core']=='PASS' for g in gates.values())
        and all(repeated.values()))
    result=dict(AV_LEAN_RESEARCH_INFRA='PASS' if qualified else 'PARTIAL',
        AV_LEAN_STRICT_SETTLEMENT='PARTIAL',SETTLEMENT_ADAPTER_VERIFIED=False,
        scope='FIVE_NAMED_ETF_FROZEN_RESEARCH_NOT_LIVE_OR_GENERAL_PIT',
        gates=gates,independent=independent['status'],reproducibility=repeated,
        input_manifest_sha256=hashlib.sha256((frozen/'freeze_manifest.json').read_bytes()).hexdigest(),
        new_provider_calls=0,real_orders_created=0)
    write_new(output/'RESULT.json',result)
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('frozen',type=Path)
    parser.add_argument('runs',type=Path);parser.add_argument('output',type=Path)
    parser.add_argument('--repeat-runs',type=Path)
    args=parser.parse_args();result=summarize(args.frozen,args.runs,args.output,args.repeat_runs)
    print(json.dumps({k:result[k] for k in ('AV_LEAN_RESEARCH_INFRA','AV_LEAN_STRICT_SETTLEMENT')}))
