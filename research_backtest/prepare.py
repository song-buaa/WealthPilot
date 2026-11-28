"""冻结旧B3输入，派生多年原生文件和独立账本；无网络和凭据读取。"""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
from decimal import Decimal as D
from .contracts import SYMBOLS, write_new
from .quality import FIELDS, validate_bar
from .native_data import ledger, native_inputs


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def seal(output: Path, *, after_runs: bool = False) -> None:
    """追加完整清单；已有清单拒绝覆盖。历史运行另有逐次pre-run input_hashes。"""
    files={p.relative_to(output).as_posix():digest(p) for p in sorted(output.rglob('*')) if p.is_file()}
    write_new(output/'freeze_manifest.json',dict(files=files,
        inventory_created_after_runs=after_runs,
        prior_proof='PER_RUN_INPUT_HASHES_AND_CONVERSION_REPEAT' if after_runs else 'BEFORE_RUN'))


def prepare(source: Path, output: Path) -> dict:
    if output.exists(): raise ValueError('OUTPUT_EXISTS')
    manifest=json.loads((source/'HASH_MANIFEST.json').read_text())
    hashes={r['path']:r['sha256'] for r in manifest['files']}
    selected=[p for sub in ('raw','requests','normalized','events','calendar','coverage')
              for p in sorted((source/sub).rglob('*')) if p.is_file()]
    selected += [source/'RESULT.json',source/'OFFICIAL_EVENT_VERIFICATION.json']
    selected += [source/'lean/SPY/data'/sub for sub in (
        'market-hours/market-hours-database.json','symbol-properties/symbol-properties-database.csv')]
    for p in selected:
        if digest(p)!=hashes[p.relative_to(source).as_posix()]:
            raise ValueError('SOURCE_HASH_MISMATCH')
    output.mkdir(parents=True,mode=0o700)
    for p in selected:
        target=output/'source'/p.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,target)
    write_new(output/'source_hashes.json',{p.relative_to(source).as_posix():digest(p) for p in selected})
    sessions=json.loads((source/'calendar/sessions.json').read_text())['sessions']
    rows={};dividends={};coverage={};anchors=set()
    for s in SYMBOLS:
        rs=json.loads((source/'normalized'/f'{s}.json').read_text())['rows']
        raw=json.loads((source/'raw'/f'{s}_TIME_SERIES_DAILY_ADJUSTED.json').read_text())
        anchor=max(d for d in raw['Time Series (Daily)'] if d<rs[0]['session_date'])
        anchors.add(anchor)
        row=dict(session_date=anchor,**{k:raw['Time Series (Daily)'][anchor][v] for k,v in FIELDS.items()})
        rows[s]=[row]+rs
        if any(validate_bar(r) for r in rows[s]): raise ValueError('BAD_RAW_BAR')
        if [r['session_date'] for r in rs]!=[a['session_date'] for a in sessions]:
            raise ValueError('CALENDAR_COVERAGE')
        e=json.loads((source/'events'/f'{s}.json').read_text())
        if e['status']!='PASS_SOURCE_INTERNAL_CONSISTENCY':raise ValueError('SOURCE_EVENT_CONSISTENCY')
        dividends[s]=e['precise_dividends']
        coverage[s]=dict(rows=len(rs),first=rs[0]['session_date'],last=rs[-1]['session_date'],
            missing_days=0,dividends=e['dividend_count'],missing_pay_dates=e['missing_payment_date_count'],
            missing_declaration_dates=e['missing_declaration_date_count'],splits=e['splits'],
            identity='FIVE_NAMED_ETF_SYMBOL_CONTINUITY_ONLY_NOT_GENERAL_PIT_MASTER',
            price='RAW; VENDOR_ADJUSTED_SEPARATE; NO_DOUBLE_DIVIDEND',
            source=e['total_return'])
    if len(anchors)!=1:raise ValueError('NONCOMMON_ANCHOR')
    anchor=anchors.pop()
    native_inputs(output/'data',rows,dividends,sessions)
    for sub in ('market-hours/market-hours-database.json','symbol-properties/symbol-properties-database.csv'):
        target=output/'data'/sub;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source/'lean/SPY/data'/sub,target)
    write_new(output/'data/model.json',{})
    dates=[anchor]+[s['session_date'] for s in sessions]
    for name in ('buy_hold','annual_small_rebalance'):
        plan=[dict(signal_date=anchor,execution_date=dates[1],symbol=s,quantity=100 if s=='SPY' else 10)
              for s in SYMBOLS]
        if name!='buy_hold':
            for prev,day in zip(dates,dates[1:]):
                if day[:4]!=prev[:4]:
                    sign=1 if int(day[:4])%2 else -1
                    plan += [dict(signal_date=prev,execution_date=day,symbol=s,quantity=q)
                             for s,q in sorted([('QQQ',-sign),('XLK',sign)],key=lambda x:x[1])]
        model=dict(name=name,start=dates[1],end=dates[-1],anchor=anchor,capital=100000,
                   symbols=list(SYMBOLS),plan=plan,fee='USD1/filled_order',slippage='0 research assumption',
                   account='Default Cash native settlement; independent economic ledger not purchasing-power model',
                   scope='FIXED_EXOGENOUS_QUANTITIES_NOT_A_RETURN_STRATEGY')
        write_new(output/f'{name}_model.json',model)
        oracle=ledger(rows,dividends,plan,D(100000))
        write_new(output/f'{name}_oracle_before_run.json',[r for r in oracle if r['day']>=dates[1]])
    write_new(output/'data_coverage.json',coverage)
    write_new(output/'NORMALISED_INPUT.json',dict(rows=rows,dividends=dividends,sessions=sessions))
    native={p.relative_to(output/'data').as_posix():digest(p) for p in sorted((output/'data').rglob('*')) if p.is_file()}
    write_new(output/'native_hashes.json',native)
    seal(output)
    return coverage


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();print(json.dumps(prepare(args.source,args.output),ensure_ascii=False,indent=2))
