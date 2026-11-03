"""复用既有AV/LEAN适配的多年冻结输入和独立精确账本；不接券商。"""
from __future__ import annotations
from decimal import Decimal as D
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
import json
from .corporate_actions import factor_rows


def scaled_price(value: str) -> str:
    """原生TradeBar StreamParseNoScale.GetDecimal后再/10000，允许小数。

    旧PoC只接受scaled整数，真实多年AV含6位及浮点尾数。保持完整值，
    不改旧lean_price、不四舍五入。超过.NET decimal精度显式拒绝。
    """
    amount=D(value)
    if not amount.is_finite() or amount<=0 or len(amount.normalize().as_tuple().digits)>28:
        raise ValueError('NATIVE_PRICE_NOT_LOSSLESS_DECIMAL')
    encoded=format(amount*10000,'f')
    if D(encoded)>D('79228162514264337593543950335') or D(encoded)/10000 != amount:
        raise ValueError('NATIVE_PRICE_NOT_LOSSLESS_DECIMAL')
    return encoded


def validate_plan(plan: list[dict]) -> None:
    """计划必须按执行日排序，所有决定严格早于成交日。"""
    if plan != sorted(plan, key=lambda p: p['execution_date']):
        raise ValueError('PLAN_NOT_SORTED')
    seen=set()
    for p in plan:
        if p['signal_date'] >= p['execution_date']:
            raise ValueError('CAUSAL_SIGNAL_REQUIRED')
        if D(str(p['quantity'])) != D(str(p['quantity'])).to_integral_value():
            raise ValueError('INTEGER_QUANTITY_REQUIRED')
        key=(p['execution_date'],p['symbol'])
        if not p['quantity'] or key in seen: raise ValueError('ZERO_OR_DUPLICATE_ORDER')
        seen.add(key)


def ledger(rows: dict, dividends: dict, plan: list[dict], capital: D,
           fee: D = D('1')) -> list[dict]:
    """直接使用RAW和独立精确事件；不读引擎输出或因子表。

    除息权益先于该日开盘成交。经济现金为除息数学确认口径；paid_cash
    只认已知支付日，其余保持应收，不能推断购买力。非交易日支付在下一
    估值日体现，但保存原日。此函数不实现T+1/T+2，是独立经济账本。
    """
    validate_plan(plan)
    by = {s: {r['session_date']: r for r in rs} for s, rs in rows.items()}
    dates = sorted(next(iter(by.values())))
    if any(sorted(v) != dates for v in by.values()):
        raise ValueError('NONCOMMON_CALENDAR')
    if any(p['execution_date'] not in dates or p['symbol'] not in by for p in plan):
        raise ValueError('INVALID_EXECUTION_SESSION_OR_SYMBOL')
    holdings = {s: D(0) for s in by}
    economic = paid = capital
    fees = total_div = D(0)
    claims = []; result = []
    for day in dates:
        for s in by:
            r = by[s][day]
            split = D(r['split'])
            holdings[s] *= split
            if holdings[s] != holdings[s].to_integral_value():
                raise ValueError('FRACTIONAL_SPLIT_REQUIRES_CASH_IN_LIEU_PROOF')
            for e in dividends[s]:
                if e['ex_dividend_date'] == day:
                    if split != 1:
                        raise ValueError('SIMULTANEOUS_ACTION_UNIT_UNVERIFIED')
                    amount = holdings[s] * D(e['amount'])
                    economic += amount; total_div += amount
                    if amount:
                        claims.append(dict(amount=amount, pay=e.get('payment_date'), paid=False))
        for claim in claims:
            if claim['pay'] and claim['pay'] <= day and not claim['paid']:
                paid += claim['amount']; claim['paid'] = True
        for p in (p for p in plan if p['execution_date'] == day):
            s=p['symbol']; qty=D(str(p['quantity'])); cost=qty*D(by[s][day]['open'])+fee
            economic -= cost; paid -= cost; fees += fee; holdings[s] += qty
            if min(economic, paid, holdings[s]) < 0:
                raise ValueError('NEGATIVE_CASH_OR_HOLDINGS')
        mv = sum(holdings[s]*D(by[s][day]['close']) for s in by)
        receivable=sum((c['amount'] for c in claims if not c['paid']),D(0))
        if paid+receivable != economic:
            raise ValueError('CASH_RECEIVABLE_IDENTITY')
        result.append(dict(day=day,economic_cash=str(economic),paid_cash=str(paid),
            receivable=str(receivable),market_value=str(mv),nav=str(economic+mv),
            fees_cumulative=str(fees),dividends_cumulative=str(total_div),
            unknown_payment_events=sum(not c['pay'] for c in claims),
            holdings={s:str(q) for s,q in holdings.items()}))
    return result


def native_inputs(destination: Path, rows: dict, dividends: dict,
                  sessions: list[dict]) -> None:
    """RAW及公司行动复用旧converter；价格无法无损表示就拒绝。"""
    if destination.exists():
        raise ValueError('OUTPUT_EXISTS')
    # 完整验证后才创建输出，防止半成品被误消费。
    encoded={s:[','.join([r['session_date'].replace('-','')+' 00:00',
        *[scaled_price(r[k]) for k in ('open','high','low','close')],r['volume']])
        for r in rs] for s,rs in rows.items()}
    destination.mkdir(parents=True)
    for s,rs in rows.items():
        ticker=s.lower();first=rs[0]['session_date']
        actions=[dict(date=e['ex_dividend_date'],type='DIVIDEND',amount=e['amount'])
                 for e in dividends[s] if first<e['ex_dividend_date']<=rs[-1]['session_date']]
        actions += [dict(date=r['session_date'],type='SPLIT',new_per_old=r['split'])
                    for r in rs if first<r['session_date'] and D(r['split'])!=1]
        daily=destination/'equity/usa/daily'/f'{ticker}.zip'
        daily.parent.mkdir(parents=True,exist_ok=True)
        with ZipFile(daily,'x') as z:
            info=ZipInfo(ticker+'.csv',(1980,1,1,0,0,0));info.compress_type=ZIP_DEFLATED
            z.writestr(info,'\n'.join(encoded[s])+'\n')
        for folder,content in [('factor_files','\n'.join(factor_rows(rs,actions))+'\n'),
             ('map_files',first.replace('-','')+f',{ticker},N\n20501231,{ticker},N\n')]:
            p=destination/'equity/usa'/folder/f'{ticker}.csv';p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(content)
    by={s:{r['session_date']:r for r in rs} for s,rs in rows.items()}
    p=destination/'custom/fixture_open_events.jsonl';p.parent.mkdir(parents=True)
    with p.open('x') as f:
        for session in sessions:
            day=session['session_date']
            f.write(json.dumps(dict(session_date=day,available_at=session['open'].replace('+00:00','Z'),
                opens=[dict(symbol=s,open=by[s][day]['open']) for s in rows]),sort_keys=True)+'\n')
