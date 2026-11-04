"""S1-B3固定数据质量核对；不填补/重写源价格，无网络或Key读取。"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo
from .contracts import SYMBOLS, write_new

FIELDS={'open':'1. open','high':'2. high','low':'3. low','close':'4. close',
        'adjusted_close':'5. adjusted close','volume':'6. volume','dividend':'7. dividend amount','split':'8. split coefficient'}


def parse_json(raw: bytes):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result: raise ValueError('DUPLICATE_JSON_KEY')
            result[key]=value
        return result
    return json.loads(raw,object_pairs_hook=pairs)


def validate_bar(row: dict) -> list[str]:
    try: values={k:D(row[k]) for k in FIELDS}
    except Exception: return ['MISSING_OR_INVALID_NUMBER']
    if any(not v.is_finite() for v in values.values()): return ['NONFINITE']
    issues=[]
    if any(values[k]<=0 for k in ('open','high','low','close','adjusted_close','split')):issues.append('NONPOSITIVE')
    if values['volume']<0 or values['dividend']<0:issues.append('NEGATIVE_VOLUME_OR_DIVIDEND')
    if values['volume']!=values['volume'].to_integral_value():issues.append('NONINTEGER_VOLUME')
    if not values['low']<=min(values['open'],values['close'])<=max(values['open'],values['close'])<=values['high']:issues.append('OHLC_RELATION')
    return issues


def audit(root: Path, metadata: Path) -> dict:
    raw_calendar=metadata.read_bytes();hours=parse_json(raw_calendar)['entries']['Equity-usa-[*]']
    holidays={datetime.strptime(v,'%m/%d/%Y').date() for v in hours['holidays']}
    early={datetime.strptime(day,'%m/%d/%Y').date():close for day,close in hours['earlyCloses'].items()}
    datasets={s:parse_json((root/'raw'/f'{s}_TIME_SERIES_DAILY_ADJUSTED.json').read_bytes()) for s in SYMBOLS}
    end=min(max(v['Time Series (Daily)']) for v in datasets.values())
    # 当前日尚未结束时，即使源返回今日也不作为完整session。
    today=datetime.now(ZoneInfo('America/New_York')).date()
    end=min(date.fromisoformat(end),today-timedelta(days=1))
    start=date(2012,7,1);sessions=[];day=start
    while day<=end:
        if day.weekday()<5 and day not in holidays:
            close=early.get(day,'16:00:00')
            dt=lambda t:datetime.fromisoformat(day.isoformat()+'T'+t).replace(tzinfo=ZoneInfo('America/New_York')).astimezone(timezone.utc).isoformat()
            sessions.append(dict(session_date=day.isoformat(),open=dt('09:30:00'),close=dt(close),early_close=day in early))
        day+=timedelta(days=1)
    write_new(root/'calendar/sessions.json',dict(timezone='America/New_York',source_sha256=hashlib.sha256(raw_calendar).hexdigest(),sessions=sessions))
    (root/'calendar/source_market_hours.json').write_bytes(raw_calendar)
    expected={v['session_date'] for v in sessions};summary={}
    for symbol,data in datasets.items():
        source=data['Time Series (Daily)'];rows=[];anomalies=[];events=[];adjustment=[]
        if data['Meta Data'].get('2. Symbol')!=symbol:anomalies.append(dict(type='IDENTITY_SYMBOL_MISMATCH'))
        for day,values in sorted(source.items()):
            row=dict(session_date=day,**{name:values.get(key) for name,key in FIELDS.items()})
            if start.isoformat()<=day<=end.isoformat():
                rows.append(row)
                for error in validate_bar(row):anomalies.append(dict(session_date=day,type=error))
                if D(row['dividend']) or D(row['split'])!=1:events.append(row)
        actual={r['session_date'] for r in rows}
        missing=sorted(expected-actual);extra=sorted(actual-expected)
        for previous,current in zip(rows,rows[1:]):
            cp,cc=D(previous['close']),D(current['close']);div=D(current['dividend']);split=D(current['split'])
            # 检验供应商前收盘扣息的向后因子约定，不把它直接等同经济TRI。
            if div and split!=1:
                adjustment.append(dict(session_date=current['session_date'],status='SIMULTANEOUS_ACTION_UNVERIFIED'));continue
            ratio=(cp-div)/(cc*split)
            expected_prev=D(current['adjusted_close'])*ratio
            residual=abs(D(previous['adjusted_close'])-expected_prev)
            up=D(1).scaleb(D(previous['adjusted_close']).as_tuple().exponent)
            uc=D(1).scaleb(D(current['adjusted_close']).as_tuple().exponent)
            tolerance=(up+uc*abs(ratio))/2+D('0.000000001')
            if residual>tolerance:
                adjustment.append(dict(session_date=current['session_date'],status='FACTOR_RECURRENCE_MISMATCH',residual=str(residual),rounding_tolerance=str(tolerance),dividend=str(div),split=str(split)))
        report=dict(symbol=symbol,source_first=min(source),source_last=max(source),source_rows=len(source),
            candidate_first=rows[0]['session_date'],candidate_last=rows[-1]['session_date'],candidate_rows=len(rows),calendar_expected=len(expected),
            missing_sessions=missing,unexpected_sessions=extra,duplicate_dates=0,anomalies=anomalies,
            candidate_dividends=[dict(date=r['session_date'],amount=r['dividend']) for r in events if D(r['dividend'])],
            candidate_splits=[dict(date=r['session_date'],new_per_old=r['split']) for r in events if D(r['split'])!=1],
            adjusted_close_recurrence=dict(checked=len(rows)-1,exceptions=adjustment,method='previous_close_minus_distribution_backward_factor_with_source_rounding'),
            raw_sha256=hashlib.sha256((root/'raw'/f'{symbol}_TIME_SERIES_DAILY_ADJUSTED.json').read_bytes()).hexdigest(),
            source_known_at='UNKNOWN_HISTORICAL',normalization='RAW_OHLCV_SEPARATE_FROM_VENDOR_ADJUSTED_CLOSE',
            status='PASS_BASIC' if not (missing or extra or anomalies) else 'DATA_QUALITY_BLOCKED')
        write_new(root/'normalized'/f'{symbol}.json',dict(symbol=symbol,rows=rows,source_ref=f'raw/{symbol}_TIME_SERIES_DAILY_ADJUSTED.json',as_of=end.isoformat()))
        write_new(root/'coverage'/f'{symbol}.json',report);summary[symbol]=report
    write_new(root/'coverage/summary.json',summary)
    return summary


def verify_events_and_total_return(root: Path) -> dict:
    """独立DIVIDENDS完整精度复核；保留初次前收盘扣息假设的诊断原件。"""
    reports={}
    for symbol in SYMBOLS:
        rows=parse_json((root/'normalized'/f'{symbol}.json').read_bytes())['rows']
        byday={r['session_date']:r for r in rows}
        source=parse_json((root/'raw'/f'{symbol}_DIVIDENDS.json').read_bytes())
        events=[];issues=[];selected={}
        for event in source['data']:
            event={k:(None if v in ('None','N/A','') else v) for k,v in event.items()}
            day=event['ex_dividend_date']
            if rows[0]['session_date']<=day<=rows[-1]['session_date']:
                if day in selected:issues.append(dict(type='DUPLICATE_DIVIDEND',date=day))
                selected[day]=event;events.append(event)
                if day not in byday or abs(D(byday[day]['dividend'])-D(event['amount']))>D('0.00005'):
                    issues.append(dict(type='DIVIDEND_ENDPOINT_MISMATCH',date=day))
        for row in rows:
            if D(row['dividend']) and row['session_date'] not in selected:issues.append(dict(type='EVENT_ENDPOINT_MISSING',date=row['session_date']))
        errors=[]
        for previous,current in zip(rows,rows[1:]):
            amount=D(selected[current['session_date']]['amount']) if current['session_date'] in selected else D(0)
            split=D(current['split'])
            if split!=1 and amount:
                issues.append(dict(type='SIMULTANEOUS_ACTION_REQUIRES_UNIT_PROOF',date=current['session_date']));continue
            gross=(D(current['close'])*split+amount)/D(previous['close'])
            observed=D(current['adjusted_close'])/D(previous['adjusted_close'])
            error=abs(gross-observed)
            errors.append(error)
            if error>D('0.000000000001'):issues.append(dict(type='ECONOMIC_TR_RECONCILIATION',date=current['session_date'],error=str(error)))
        splits=[dict(effective_date=r['session_date'],split_factor=r['split']) for r in rows if D(r['split'])!=1]
        if symbol in ('SMH','IGV'):
            independent=parse_json((root/'raw'/f'{symbol}_SPLITS.json').read_bytes())['data']
            for e in splits:
                if not any(v['effective_date']==e['effective_date'] and D(v['split_factor'])==D(e['split_factor']) for v in independent):
                    issues.append(dict(type='SPLIT_ENDPOINT_MISMATCH',date=e['effective_date']))
        report=dict(symbol=symbol,status='PASS_SOURCE_INTERNAL_CONSISTENCY' if not issues else 'BLOCKED',
            dividend_count=len(events),daily_dividend_count=sum(D(r['dividend'])>0 for r in rows),
            splits=splits,issues=issues,precise_dividends=events,
            missing_payment_date_count=sum(not e['payment_date'] for e in events),
            missing_declaration_date_count=sum(not e['declaration_date'] for e in events),
            known_at='UNVERIFIED_DECLARATION_DATE_NOT_PROOF_OF_AMOUNT_KNOWLEDGE',
            total_return=dict(method='g_t=(raw_close_t*new_per_old_t+full_precision_distribution_t)/raw_close_previous',
                reinvestment='EX_DATE_CLOSE_MATHEMATICAL_INDEX_NOT_CASH_PURCHASE',checked=len(errors),
                max_abs_gross_ratio_error=str(max(errors)),tolerance='0.000000000001',
                source_precision='DIVIDENDS.amount; daily dividend amount rounded to four decimals'),
            previous_diagnostic='coverage/*.json tested a different backward factor hypothesis; exceptions preserved, not accepted as data corruption')
        write_new(root/'events'/f'{symbol}.json',report);reports[symbol]=report
    write_new(root/'events/summary.json',reports)
    return reports
