"""个人原生factor转换；不改公司行动事实。"""
from decimal import Decimal as D

def factor_rows(bars: list[dict], actions: list[dict]) -> list[str]:
    dates=sorted({bars[0]['session_date']}|{max(b['session_date'] for b in bars if b['session_date']<e['date']) for e in actions})
    result=[]
    for day in dates:
        price,split=D(1),D(1)
        for event in actions:
            if event['date']>day:
                if event['type']=='SPLIT':split/=D(event['new_per_old'])
                elif event['type']=='DIVIDEND':
                    previous=max((b for b in bars if b['session_date']<event['date']),key=lambda b:b['session_date'])
                    price*=1-D(event['amount'])/D(previous['close'])
                else:raise ValueError('UNSUPPORTED_ACTION')
        reference=next(b['close'] for b in bars if b['session_date']==day)
        result.append(','.join((day.replace('-',''),str(price),str(split),reference)))
    return result+['20501231,1,1,0']
