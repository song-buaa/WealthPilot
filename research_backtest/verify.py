"""从冻结RAW和固定成交计划的闭式复算，与顺序Decimal账本交叉检查。"""
from pathlib import Path
from decimal import Decimal as D
from datetime import date
import argparse
import csv
import json
import math
import statistics
from .contracts import write_new


def verify(frozen: Path, output: Path) -> dict:
    if output.exists():raise ValueError('OUTPUT_EXISTS')
    output.mkdir(parents=True)
    data=json.loads((frozen/'NORMALISED_INPUT.json').read_text())
    rows={s:{r['session_date']:r for r in rs} for s,rs in data['rows'].items()}
    splits={s:[(d,D(r['split'])) for d,r in rs.items() if D(r['split'])!=1] for s,rs in rows.items()}
    checks=[];metrics=[]
    for name in ('buy_hold','annual_small_rebalance'):
        model=json.loads((frozen/f'{name}_model.json').read_text())
        oracle=json.loads((frozen/f'{name}_oracle_before_run.json').read_text())
        expected_days=[s['session_date'] for s in data['sessions']
                       if model['start']<=s['session_date']<=model['end']]
        if (not expected_days or expected_days!=sorted(set(expected_days)) or
                [r['day'] for r in oracle]!=expected_days or
                len(model['symbols'])!=len(set(model['symbols'])) or
                set(model['symbols'])!=set(rows) or
                any(set(r['holdings'])!=set(model['symbols']) for r in oracle)):
            raise ValueError('ORACLE_DATE_OR_SECURITY_COVERAGE')
        def quantity(s,day,before_open=False):
            total=D(0)
            for p in model['plan']:
                if p['symbol']!=s or p['execution_date']>day or (before_open and p['execution_date']==day):continue
                q=D(p['quantity'])
                for d,factor in splits[s]:
                    if p['execution_date']<d<=day:q*=factor
                total+=q
            return total
        claims=[]
        for s,events in data['dividends'].items():
            for e in events:
                amount=quantity(s,e['ex_dividend_date'],True)*D(e['amount'])
                claims.append(dict(ex=e['ex_dividend_date'],pay=e['payment_date'],amount=amount))
        daily=[]
        for r in oracle:
            day=r['day'];spent=D(0);fees=D(0);turnover=D(0)
            for p in model['plan']:
                if p['execution_date']<=day:
                    value=D(p['quantity'])*D(rows[p['symbol']][p['execution_date']]['open'])
                    spent+=value;fees+=1;turnover+=abs(value)
            dividends=sum((c['amount'] for c in claims if c['ex']<=day),D(0))
            paid_div=sum((c['amount'] for c in claims if c['ex']<=day and c['pay'] and c['pay']<=day),D(0))
            cash=D(model['capital'])-spent-fees+dividends
            paid=D(model['capital'])-spent-fees+paid_div
            mv=sum((quantity(s,day)*D(rs[day]['close']) for s,rs in rows.items()),D(0))
            expected=dict(economic_cash=cash,paid_cash=paid,receivable=dividends-paid_div,
                          market_value=mv,nav=cash+mv,fees_cumulative=fees,dividends_cumulative=dividends)
            for field,value in expected.items():
                delta=value-D(r[field])
                if abs(delta)>D('0.000001'):
                    checks.append(dict(case=name,day=day,field=field,delta=str(delta)))
            for s,q in r['holdings'].items():
                if quantity(s,day)!=D(q):checks.append(dict(case=name,day=day,field='quantity',symbol=s))
            daily.append(dict(day=day,nav=str(cash+mv),cash=str(cash),paid_cash=str(paid),
                              receivable=str(dividends-paid_div),fees=str(fees),turnover=str(turnover)))
        with (output/f'{name}_daily.csv').open('x') as stream:
            w=csv.DictWriter(stream,fieldnames=list(daily[0]));w.writeheader();w.writerows(daily)
        levels=[model['capital']]+[float(d['nav']) for d in daily]
        returns=[b/a-1 for a,b in zip(levels,levels[1:])]
        peak=levels[0];mdd=0
        for nav in levels[1:]:peak=max(peak,nav);mdd=min(mdd,nav/peak-1)
        years=(date.fromisoformat(daily[-1]['day'])-date.fromisoformat(daily[0]['day'])).days/365.25
        vol=statistics.stdev(returns)*math.sqrt(252)
        metrics.append(dict(case=name,evidence='INDEPENDENT_EXPECTED_LEDGER_NOT_LEAN_RUNTIME',
            first=daily[0]['day'],last=daily[-1]['day'],rows=len(daily),
            cagr=(levels[-1]/levels[0])**(1/years)-1,mdd=mdd,volatility=vol,
            sharpe_rf0=statistics.mean(returns)*252/vol if vol else None,
            final_nav=daily[-1]['nav'],fees=daily[-1]['fees'],trades=len(model['plan']),
            annual_oneway_turnover_initial_included=float(daily[-1]['turnover'])/2/statistics.mean(levels)/years))
    report=dict(status='PASS' if not checks else 'FAIL',
        method='Closed_form_split_adjusted_trade_cohorts_vs_sequential_Decimal_cash_receivable_book',
        days_per_case=len(daily),tolerance='0.000001',differences=checks,metrics=metrics,
        strict_payment_cash='UNKNOWN_WHERE_PAY_DATE_MISSING',lean_runtime='NOT_PROVEN_BY_THIS_CHECK')
    write_new(output/'independent_verification.json',report)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('frozen',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();print(json.dumps(verify(a.frozen,a.output),ensure_ascii=False,indent=2))
