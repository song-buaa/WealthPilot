from decimal import Decimal as D
import pytest
from research_backtest.native_data import ledger, native_inputs, validate_plan, scaled_price


def fixture():
    rows = [dict(session_date=f'2024-01-0{i}', open=p, high=p, low=p, close=p,
                 volume='100', split=s) for i,p,s in [(2,'10','1'),(3,'9','1'),(4,'4.5','2')]]
    events = [dict(ex_dividend_date='2024-01-03', amount='1.001234',payment_date='2024-01-04')]
    return {'SPY': rows}, {'SPY': events}


def test_precise_cash_pay_date_and_split_are_separate():
    rows, events = fixture()
    plan=[dict(signal_date='2024-01-01',execution_date='2024-01-02',symbol='SPY',quantity=10)]
    result=ledger(rows,events,plan,D('1000'))
    assert D(result[1]['economic_cash']) == D('909.012340')
    assert D(result[1]['paid_cash']) == D('899')
    assert D(result[1]['receivable']) == D('10.012340')
    assert D(result[2]['paid_cash']) == D('909.012340')
    assert result[2]['holdings']['SPY'] == '20'


def test_unknown_payment_is_not_invented():
    rows, events=fixture();events['SPY'][0]['payment_date']=None
    result=ledger(rows,events,[dict(signal_date='2024-01-01',execution_date='2024-01-02',symbol='SPY',quantity=10)],D('1000'))
    assert D(result[-1]['receivable']) == D('10.012340')
    assert result[-1]['unknown_payment_events'] == 1


def test_same_day_or_unsorted_plan_rejected():
    with pytest.raises(ValueError, match='CAUSAL'):
        validate_plan([dict(signal_date='2024-01-02',execution_date='2024-01-02',symbol='SPY',quantity=1)])


def test_duplicate_pending_instruction_rejected():
    p=dict(signal_date='2024-01-01',execution_date='2024-01-02',symbol='SPY',quantity=1)
    with pytest.raises(ValueError,match='DUPLICATE'):
        validate_plan([p,p])


def test_bundle_tamper_rejected_before_any_docker_call(tmp_path):
    from research_backtest.prepare import seal
    from research_backtest.run import execute
    frozen=tmp_path/'frozen';frozen.mkdir();(frozen/'model.json').write_text('{}')
    seal(frozen);(frozen/'model.json').write_text('{"modified":true}')
    with pytest.raises(ValueError,match='FROZEN_BUNDLE_HASH_MISMATCH'):
        execute(frozen,tmp_path/'run','buy_hold')


def test_unfunded_buy_rejected():
    rows,events=fixture()
    with pytest.raises(ValueError,match='NEGATIVE_CASH'):
        ledger(rows,events,[dict(signal_date='2024-01-01',execution_date='2024-01-02',symbol='SPY',quantity=1000)],D('1'))


def test_native_conversion_rejects_lossy_price(tmp_path):
    rows,events=fixture();rows['SPY'][0]['open']='10.12345678901234567890123456789012345'
    with pytest.raises(ValueError,match='LOSSLESS'):
        native_inputs(tmp_path/'data',rows,events,[])


def test_scaled_native_decimal_preserves_vendor_precision():
    assert D(scaled_price('204.740005'))/10000 == D('204.740005')
    assert D(scaled_price('121.17200000000001'))/10000 == D('121.17200000000001')


def test_dividend_entitlement_before_ex_open_purchase():
    rows,events=fixture()
    plan=[dict(signal_date='2024-01-02',execution_date='2024-01-03',symbol='SPY',quantity=10)]
    result=ledger(rows,events,plan,D('1000'))
    assert D(result[-1]['dividends_cumulative']) == 0




@pytest.mark.parametrize('status',['FILLED','Filled','INVALID'])
def test_reconciliation_accepts_native_enum_case_but_rejects_invalid(tmp_path,status):
    import json
    from research_backtest.run import reconcile
    frozen=tmp_path/'frozen';frozen.mkdir();window=tmp_path/'run';(window/'results').mkdir(parents=True)
    day='2024-01-02'
    def save(p,v):p.write_text(json.dumps(v))
    model=dict(symbols=['SPY'],plan=[dict(signal_date='2024-01-01',execution_date=day,symbol='SPY',quantity=1)])
    expected=dict(day=day,holdings={'SPY':'1'},economic_cash='89',paid_cash='89',nav='99',fees_cumulative='1')
    save(frozen/'buy_hold_model.json',model);save(frozen/'buy_hold_oracle_before_run.json',[expected])
    save(frozen/'NORMALISED_INPUT.json',dict(rows={'SPY':[dict(session_date=day,open='10',close='10')]},dividends={'SPY':[]},sessions=[dict(session_date=day,open=day+'T14:30:00+00:00',close=day+'T21:00:00+00:00')]))
    actual=dict(total_orders=1,daily=[dict(day=day,utc=day+'T21:00:00Z',nav='99',holdings={'SPY':'1.0'},closes={'SPY':'10'},fees_cumulative='1',cash={'settled':{'USD':'89'},'unsettled':{'USD':'0'}})],
        orders=[dict(id=1,symbol='SPY',utc='2024-01-01T22:05:00Z',status='SUBMITTED',quantity='0',price='0',fee='0'),dict(id=1,symbol='SPY',utc=day+'T14:30:00Z',status=status,quantity='1',price='10',fee='1')],
        actions=[],opens=[dict(day=day,utc=day+'T14:30:00Z',full_bar_seen=False)])
    save(window/'results/history-observation.json',actual)
    result=reconcile(frozen,window,'buy_hold')
    assert result['status']==('PARTIAL' if status=='INVALID' else 'PASS')
