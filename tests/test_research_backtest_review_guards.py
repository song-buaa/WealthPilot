"""独立Review发现的三种误PASS反例；固定合成输入不接引擎。"""
import copy
import json
from decimal import Decimal as D
import pytest
from research_backtest.native_data import ledger, scaled_price
from research_backtest.run import reconcile
from research_backtest.verify import verify


def fixed_case(tmp_path):
    frozen=tmp_path/'frozen';frozen.mkdir();window=tmp_path/'run';(window/'results').mkdir(parents=True)
    days=['2024-01-02','2024-01-03'];symbols=['SPY','QQQ']
    rows={s:[dict(session_date=d,open='10',high='10',low='10',close='10',volume='100',split='1') for d in days] for s in symbols}
    events={s:[] for s in symbols}
    plan=[dict(signal_date='2024-01-01',execution_date=days[0],symbol=s,quantity=1) for s in symbols]
    model=dict(symbols=symbols,plan=plan,capital=1000,start=days[0],end=days[-1])
    oracle=ledger(rows,events,plan,D(1000))
    normal=dict(rows=rows,dividends=events,sessions=[dict(session_date=d,open=d+'T14:30:00+00:00',close=d+'T21:00:00+00:00') for d in days])
    actual=dict(total_orders=2,daily=[dict(day=d,utc=d+'T21:00:00Z',nav='998',holdings={s:'1.0' for s in symbols},closes={s:'10' for s in symbols},fees_cumulative='2',cash={'settled':{'USD':'978'},'unsettled':{'USD':'0'}}) for d in days],
        orders=[e for i,s in enumerate(symbols,1) for e in [dict(id=i,symbol=s,utc='2024-01-01T22:05:00Z',status='SUBMITTED',quantity='0',price='0',fee='0'),dict(id=i,symbol=s,utc=days[0]+'T14:30:00Z',status='FILLED',quantity='1',price='10',fee='1')]],
        actions=[],opens=[dict(day=d,utc=d+'T14:30:00Z',full_bar_seen=False) for d in days])
    def save(p,v):p.write_text(json.dumps(v))
    for n in ('buy_hold','annual_small_rebalance'):
        save(frozen/f'{n}_model.json',model);save(frozen/f'{n}_oracle_before_run.json',oracle)
    save(frozen/'NORMALISED_INPUT.json',normal);save(window/'results/history-observation.json',actual)
    return frozen,window,model,oracle,normal,actual,save


@pytest.mark.parametrize('damage',['missing_day','missing_symbol','duplicate_day'])
def test_independent_verification_rejects_incomplete_oracle(tmp_path,damage):
    f,w,m,o,n,a,save=fixed_case(tmp_path)
    if damage=='missing_day':o.pop()
    elif damage=='missing_symbol':del o[0]['holdings']['SPY']
    else:o[1]=copy.deepcopy(o[0])
    save(f/'buy_hold_oracle_before_run.json',o)
    with pytest.raises(ValueError,match='COVERAGE'):
        verify(f,tmp_path/'verification')


def test_same_fill_count_cannot_hide_unfilled_submitted_order(tmp_path):
    f,w,m,o,n,a,save=fixed_case(tmp_path)
    a['orders'][3]=copy.deepcopy(a['orders'][1])
    save(w/'results/history-observation.json',a)
    r=reconcile(f,w,'buy_hold')
    order=[c for c in r['checks'] if c['name'] in ('filled_order_id_bijection','plan_fill_bijection','submitted_orders_terminal')]
    assert len(order)==3 and all(c['status']=='FAIL' for c in order)


@pytest.mark.parametrize('damage',['price','time','rejection','missing_check','missing_opens','duplicate_open','full_bar_at_open'])
def test_gate_status_is_derived_and_fails_closed(tmp_path,damage):
    from research_backtest.report import research_gates
    f,w,m,o,n,a,save=fixed_case(tmp_path)
    if damage=='price':a['daily'][0]['closes']['SPY']='11'
    elif damage=='time':a['orders'][1]['utc']='2024-01-02T21:00:00Z'
    elif damage=='rejection':a['orders'][1]['status']='INVALID'
    elif damage=='missing_opens':a['opens']=[]
    elif damage=='duplicate_open':a['opens'][1]=copy.deepcopy(a['opens'][0])
    elif damage=='full_bar_at_open':a['opens'][0]['full_bar_seen']=True
    save(w/'results/history-observation.json',a)
    r=reconcile(f,w,'buy_hold')
    if damage=='missing_check':r['checks']=[c for c in r['checks'] if c['name']!='no_rejections']
    assert research_gates(r,m,n)['research_core']!='PASS'


def test_scaled_decimal_cannot_overflow_dotnet():
    with pytest.raises(ValueError,match='LOSSLESS'):
        scaled_price('1e30')


@pytest.mark.parametrize('pay',[None,'2024-01-31'])
def test_strict_request_stops_before_engine_instead_of_using_ex_cash(tmp_path,pay):
    from research_backtest.prepare import seal
    from research_backtest.run import execute
    f,w,m,o,n,a,save=fixed_case(tmp_path)
    n['dividends']['SPY']=[dict(ex_dividend_date='2024-01-03',amount='1.965548',payment_date=pay)]
    save(f/'NORMALISED_INPUT.json',n);seal(f)
    with pytest.raises(ValueError,match='STRICT_SETTLEMENT_NOT_QUALIFIED'):
        execute(f,tmp_path/'unused_run','buy_hold',strict_settlement=True)


def test_premium_receipt_missing_cannot_become_true(tmp_path):
    from research_backtest.report import premium_verified
    (tmp_path/'requests').mkdir()
    assert premium_verified(tmp_path) is False
