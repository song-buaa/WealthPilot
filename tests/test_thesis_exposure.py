"""Synthetic marked-value inputs only. No balance, provider or personal risk data."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import socket

import pytest

from backend.services.investment_thesis.exposure import calculate_exposure

AT=datetime(2026,10,28,6,tzinfo=timezone.utc)


def view():
    from backend.services.instruments.issuers import exposure
    stamp=AT.isoformat()
    def row(i,symbol,value,kind,currency,account):
        return dict(id=i,symbol=symbol,security_symbol=symbol,account_ref=account,
            currency=currency,original_currency=currency,market_value_cny=value,
            vehicle_type=kind,economic_asset_class='CASH' if kind=='CASH' else 'EQUITY',
            quantity=10+i,cost_price=20,current_price=30,fx_rate_to_cny=1 if currency=='CNY' else 7,
            fx_rate_date='2026-10-28',fx_quality='current',
            price_as_of='2026-10-27T20:00:00+00:00' if symbol.endswith(':US') else stamp,
            price_time_basis='quote',positions_as_of=stamp,cash_as_of=stamp)
    rows=[row(1,'3690:HK',60000,'COMMON_STOCK','HKD','account_a'),
          row(2,'3690:HK',40000,'COMMON_STOCK','HKD','account_b'),
          row(3,'SPY:US',500000,'ETF','USD','account_a'),
          row(4,'CASH:CNY',400000,'CASH','CNY','account_b')]
    return dict(snapshot_id='asset-view:v1:1',portfolio_id=7,captured_at=stamp,context_current=True,
        synthetic=True,issuer_exposure=exposure(rows),views={'get_manual_wealth_items':{'items':[]}},
        data_quality=dict(policy_version='d1.1',issues=[],can_propose_rebalance=False,
            analysis_eligibility={'B':{'allowed':True}},security_inputs=rows,
            accounts=[dict(account_ref=a,publication_status='matched',complete=True,
                account_synced_at=stamp,last_attempt_status='success') for a in ('account_a','account_b')]))


def scenario(**kwargs):
    result=dict(snapshot_id='asset-view:v1:1',funding='INTERNAL_CASH',investment_value_added_cny='50000',
                external_cash_added_cny='0',fees_cny='0',assumptions_reference='synthetic static prices and zero fees')
    result.update(kwargs)
    return result


def security(symbol='3690:HK',currency='HKD'):
    return dict(symbol=symbol,currency=currency,identity_reference='fixture://synthetic-security')


def calculate(v=None,s=None,sec=None,**kwargs):
    return calculate_exposure(v if v is not None else view(),sec or security(),s or scenario(),portfolio_id=7,at=AT,**kwargs)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*a,**k):pytest.fail('pure exposure attempted network')
    monkeypatch.setattr(socket.socket,'connect',denied)


def test_internal_external_denominator_unchanged_inputs_and_no_limit():
    v=view();before=deepcopy(v)
    internal=calculate(v)
    assert internal['status']=='QUANTIFIED',internal
    assert internal['direct_weight_before']==pytest.approx(.10)
    assert internal['direct_weight_after']==pytest.approx(.15)
    assert internal['denominator_before_cny']==internal['denominator_after_cny']
    external=calculate(v,scenario(funding='EXTERNAL_CASH',external_cash_added_cny='50000'))
    assert external['direct_weight_after']==pytest.approx(150000/1050000)
    assert internal['confirmed_limit_status']=='UNKNOWN' and internal['etf_indirect_exposure']=='UNKNOWN'
    assert internal['execution_authorized'] is False and internal['order_created'] is False
    assert len(internal['members'])==2 and internal['members'][0]['quantity']!=internal['members'][1]['quantity']
    assert v==before


def test_hypothetical_limit_is_not_personal_authority_and_costs():
    r=calculate(s=scenario(hypothetical_limit_weight='.12',hypothetical_limit_reference='fixture only, not user cap',fees_cny='100'))
    assert r['limit_status']=='SCENARIO_EXCEEDED' and r['confirmed_limit_status']=='UNKNOWN'
    assert r['direct_weight_after']==pytest.approx(150000/999900)
    assert 'suggested purchase' in r['warning']


def test_reuse_server_confirmed_stage_limit_scope_and_expiry():
    target=dict(version='synthetic-stage-v1',portfolio_id=7,basis='server_stage_target_store',
        confirmation_status='confirmed',confirmation_source='local_operator_cli',
        confirmation_reference='synthetic confirmation not actual personal preference',
        confirmed_at='2026-10-27T00:00:00+00:00',effective_from='2026-10-27T00:00:00+00:00',
        valid_until='2026-11-01T00:00:00+00:00',issuers=[dict(issuer_id='MEITUAN',target_weight=.12,tolerance=0)])
    assert calculate(confirmed_targets=target)['confirmed_limit_status']=='EXCEEDED'
    for changes in ({'portfolio_id':8},{'basis':'client_boolean'},{'valid_until':'2026-10-27T01:00:00+00:00'}):
        assert calculate(confirmed_targets={**target,**changes})['confirmed_limit_status']=='UNKNOWN'


@pytest.mark.parametrize('alter,reason',[
    (lambda v:v.update(captured_at=(AT-timedelta(days=1)).isoformat()),'VIEW_EXPIRED'),
    (lambda v:v.update(portfolio_id=8),'CROSS_SCOPE'),
    (lambda v:v['data_quality']['security_inputs'][0].update(current_price=0),'PRICE_OR_QUANTITY'),
    (lambda v:v['data_quality']['security_inputs'][0].pop('fx_rate_to_cny'),'UNQUALIFIED'),
    (lambda v:v['data_quality']['accounts'][0].update(complete=False),'COVERAGE_UNKNOWN'),
    (lambda v:v['data_quality']['security_inputs'].pop(),'DO_NOT_RECONCILE'),
    (lambda v:v['data_quality']['security_inputs'][0].update(price_as_of='2026-01-01'),'UNQUALIFIED'),
    (lambda v:v['data_quality']['security_inputs'][0].update(positions_as_of='2020-01-01T00:00:00+00:00'),'SOURCE_PRICE_OR_FX_GAP'),
    (lambda v:v['data_quality']['analysis_eligibility']['B'].update(allowed=False),'ORIGINAL_WHOLE_PORTFOLIO'),
])
def test_missing_stale_wrong_scope_refuses(alter,reason):
    v=view();alter(v)
    result=calculate(v)
    assert result['status']=='CANNOT_QUANTIFY' and result['direct_weight_after'] is None,result
    assert any(reason in r for r in result['reasons']),result


def test_unmapped_nvda_is_not_added_to_shared_issuer_owner():
    assert calculate(sec=security('NVDA:US','USD'))['status']=='CANNOT_QUANTIFY'
    v=view();v['data_quality']['security_inputs'][0]['symbol']='NVDA:US'
    assert 'ISSUER_MAPPING_INCOMPLETE' in calculate(v)['reasons']


def test_multi_security_same_issuer_does_not_add_adr_share_counts():
    from backend.services.instruments.issuers import exposure
    v=view();rows=v['data_quality']['security_inputs']
    rows[0].update(symbol='LI:US',security_symbol='LI:US',currency='USD',original_currency='USD',vehicle_type='ADR',price_as_of='2026-10-27T20:00:00+00:00')
    rows[1].update(symbol='2015:HK',security_symbol='2015:HK')
    v['issuer_exposure']=exposure(rows)
    r=calculate(v,sec=security('LI:US','USD'))
    assert r['status']=='QUANTIFIED' and float(r['direct_before_cny'])==100000
    assert [i['quantity'] for i in r['members']]==[11,12]
    assert 'quantity' not in r


def test_unknown_fee_funding_and_insufficient_cash_do_not_invent_buying_power():
    for change in ({'fees_cny':None},{'external_cash_added_cny':None},{'investment_value_added_cny':'5000000'},
                   {'funding':'INTERNAL_CASH','external_cash_added_cny':'100'}):
        assert calculate(s=scenario(**change))['status']=='CANNOT_QUANTIFY'
