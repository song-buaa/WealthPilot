"""Native ledger authority; all values synthetic, no live broker calls."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest
from backend.services.action.tests.test_ibkr_adapter import _make_adapter
from backend.services.execution_batch.calculator import select_authoritative_cash


def row(tag, value, currency='USD', account='DU1234567', modelCode=''):
    return SimpleNamespace(tag=tag,value=str(value),currency=currency,account=account,modelCode=modelCode)


def adapter(values):
    a=_make_adapter(); a._ensure_connected=MagicMock()
    a._ib.reqAccountUpdatesAsync=AsyncMock()
    a._ib.accountValues.return_value=values
    a._ib.wrapper.accountValues={('DU1234567','old','USD',''):row('old',999),('other','old','USD',''):row('old',111)}
    return a


@pytest.mark.parametrize('prefix',['','$LEDGER-'])
def test_native_currency_not_converted_aggregate(prefix):
    a=adapter([row(prefix+'CashBalance',120),row(prefix+'CashBalance',30,'CNH'),
               row(prefix+'CashBalance',125,'BASE'),row('TotalCashValue',125),
               row('AvailableFunds',20000),row('BuyingPower',80000),
               row(prefix+'CashBalance',999,account='DU9999999'),
               row(prefix+'CashBalance',888,modelCode='model')])
    cash=a.get_cash_snapshot('USD')
    assert select_authoritative_cash(cash)[0]==120
    assert cash['account_base_TotalCashValue']==125
    assert 'TotalCashValue' not in cash and 'BuyingPower' not in cash
    assert a.get_account_info()['cash_balances']==[{'currency':'CNH','amount':30},{'currency':'USD','amount':120}]
    assert ('DU1234567','old','USD','') not in a._ib.wrapper.accountValues
    assert ('other','old','USD','') in a._ib.wrapper.accountValues
    assert a._ib.reqAccountUpdatesAsync.await_count==2
    assert a._ib.client.reqAccountUpdates.call_count==2
    a._ib.client.reqAccountUpdates.assert_called_with(False,'DU1234567')


@pytest.mark.parametrize('values,reason',[
    ([row('TotalCashValue',5000)],'UNAVAILABLE'),
    ([row('$LEDGER-CashBalance','NaN')],'INVALID'),
    ([row('$LEDGER-CashBalance',100),row('CashBalance',101)],'CONFLICT'),
    ([row('$LEDGER-CashBalance',100,'CNH')],'UNAVAILABLE:USD'),
])
def test_missing_bad_or_wrong_currency_fails_closed(values,reason):
    with pytest.raises(ConnectionError,match=reason):adapter(values).get_cash_snapshot('USD')


def test_zero_and_negative_cash_are_never_replaced_by_buying_power():
    a=adapter([row('$LEDGER-CashBalance',0),row('BuyingPower',10000)])
    assert select_authoritative_cash(a.get_cash_snapshot())[0]==0
    a._ib.accountValues.return_value=[row('$LEDGER-CashBalance',-10),row('BuyingPower',10000)]
    with pytest.raises(Exception):select_authoritative_cash(a.get_cash_snapshot())


def test_refresh_timeout_disconnects_incomplete_cache():
    a=adapter([row('$LEDGER-CashBalance',10000)])
    a._ib.reqAccountUpdatesAsync=AsyncMock(side_effect=TimeoutError('accountDownloadEnd'))
    with pytest.raises(TimeoutError):a.get_cash_snapshot()
    a._ib.disconnect.assert_called_once()
    assert not a._connected


def test_concurrent_account_downloads_are_serialized():
    a=adapter([row('$LEDGER-CashBalance',100)])
    running=0
    async def download(*args):
        nonlocal running
        running+=1
        assert running==1
        await asyncio.sleep(.01)
        running-=1
    a._ib.reqAccountUpdatesAsync=download
    async def run():
        return await asyncio.gather(a._fresh_account_values(),a._fresh_account_values())
    assert len(asyncio.run(run()))==2
