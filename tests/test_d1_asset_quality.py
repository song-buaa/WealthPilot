"""D1 quality decisions on synthetic data, no broker/provider/production I/O."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app import asset_views
from backend.services.instruments.issuers import exposure
from pocs.chatgpt_skills.analysis import AnalysisRequest


AT = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def context(tmp_path,monkeypatch):
    # U1 service adds backend to sys.path; do so at fixture runtime, not during
    # collection where it would shadow the root Offline M5 scripts namespace.
    from tests.test_u1_asset_views import context as original
    yield from original.__wrapped__(tmp_path,monkeypatch)


def ready(*args,**kwargs):
    from tests.test_u1_asset_views import ready as original
    return original(*args,**kwargs)


def valuation_quality(*args,**kwargs):
    from backend.services.execution_plan.market_hours import valuation_quality as original
    return original(*args,**kwargs)


def quality_view():
    stamp = AT.isoformat()
    stock = dict(id=1, symbol='LI:US', security_symbol='LI:US', quantity=2, current_price=50,
        vehicle_type='ADR', economic_asset_class='EQUITY', account_ref='account_a',
        market_value_cny=100, currency='CNY', original_currency='USD', fx_rate_date=AT.date().isoformat(),fx_rate_to_cny=1,
        positions_as_of=stamp, price_as_of=stamp, cash_as_of=stamp)
    cash = dict(stock,id=2,symbol='CASH-USD:US',security_symbol='CASH-USD:US',vehicle_type='CASH',economic_asset_class='CASH',market_value_cny=10)
    return dict(captured_at=stamp,context_current=True,views={'get_manual_wealth_items':{'items':[]}},
        issuer_exposure={'unmapped_direct_holdings':[]},data_quality=dict(policy_version='d1.1',status='ok',issues=[],
        security_inputs=[stock,cash], accounts=[dict(account_ref='account_a',account_synced_at=stamp,
            complete=True,publication_status='matched',last_attempt_status='success')]))


def qualify(view, **kwargs):
    asset_views.qualify_view(view,at=AT,**kwargs)
    return view['data_quality']['analysis_eligibility']


def codes(level):
    return {b['code'] for b in level['blockers']}


def test_evidenced_three_issuers_preserve_security_members():
    def item(symbol,value,**kwargs):
        return dict(symbol=symbol,market_value_cny=value,quantity=1,cost_price=3,
            economic_asset_class='EQUITY',vehicle_type='COMMON_STOCK',currency='USD' if symbol.endswith(':US') else 'HKD',**kwargs)
    rows=[item('LI:US',100),item('LI:US',200),
          item('2015.HGT:HK',300,broker='guojin',broker_security_type='QNX_NATIVE_POSITION',original_currency='CNY'),
          item('2015.SGT:HK',10,broker='guojin',broker_security_type='QNX_NATIVE_POSITION',original_currency='CNY'),
          item('3690.HGT:HK',150,broker='guojin',broker_security_type='QNX_NATIVE_POSITION',original_currency='CNY'),
          item('PDD:US',80),item('UNKNOWN:US',160)]
    original=deepcopy(rows);result=exposure(rows)
    groups={c['issuer_id']:c for c in result['companies']}
    assert groups['LI_AUTO']['market_value_cny']==610 and len(groups['LI_AUTO']['members'])==4
    assert groups['MEITUAN']['economic_region']=='CN'
    assert groups['PDD_HOLDINGS']['market_value_cny']==80
    assert result['known_china_direct_equity_cny']==840
    assert result['known_china_weight_of_investments']==pytest.approx(.84)
    assert result['unmapped_direct_value_cny']==160
    assert rows==original
    # No source proof => do not guess a native channel identity.
    assert not exposure([item('3690.HGT:HK',1)])['companies']


@pytest.mark.parametrize('value,market,at,basis,expected',[
    ('2026-10-20T13:58:00+00:00','US','2026-10-20T14:00:00+00:00',None,'intraday'),
    ('2026-10-16','US','2026-10-18T14:00:00+00:00','close','recent_close'),
    ('2026-07-02','US','2026-07-03T14:00:00+00:00','close','recent_close'), # Independence Day observed
    ('2026-11-27T18:00:00+00:00','US','2026-11-27T19:00:00+00:00',None,'recent_close'), # half-day
    ('2026-10-16','HK','2026-10-19T06:00:00+00:00','close','recent_close'), # Chung Yeung
    ('2026-10-19','HK','2026-10-20T06:00:00+00:00','close','stale'),
    ('2026-10-16','US','2026-10-20T14:00:00+00:00','close','stale'),
    ('2026-10-20', 'US','2026-10-20T14:00:00+00:00',None,'unknown'),
    (None,'US','2026-10-20T14:00:00+00:00',None,'unknown'),
    ('2026-10-20T14:01:00+00:00','US','2026-10-20T14:00:00+00:00',None,'stale'),
    ('2026-10-20T13:59:00','US','2026-10-20T14:00:00+00:00',None,'unknown'),
    ('2026-10-20T13:59:00+00:00','UNSUPPORTED','2026-10-20T14:00:00+00:00',None,'unknown'),
])
def test_session_valuation(value,market,at,basis,expected):
    result=valuation_quality(value,market,at=datetime.fromisoformat(at),basis=basis)
    assert result['status']==expected


def test_abc_scope_and_reasons():
    view=quality_view();assert all(l['allowed'] for l in qualify(view).values())
    view['data_quality']['security_inputs'][0]['price_as_of']=None
    levels=qualify(view)
    assert levels['A']['allowed'] and not levels['B']['allowed'] and not levels['C']['allowed']
    assert 'PRICE_TIME_UNKNOWN' in codes(levels['C'])
    view=quality_view();view['data_quality']['security_inputs'].append(dict(
        view['data_quality']['security_inputs'][0],id=3,symbol='TINY:US',security_symbol='TINY:US',
        market_value_cny=.01,economic_asset_class='UNKNOWN',price_as_of=None,account_ref='other'))
    levels=qualify(view)
    assert levels['A']['allowed'] and levels['B']['allowed'] and not levels['C']['allowed']
    assert all(l['allowed'] for l in qualify(view,symbol='LI:US').values())
    assert not qualify(view,symbol='ABSENT:US')['A']['allowed']


def test_manual_loan_only_blocks_net_worth_and_pension_link_is_not_invented():
    view=quality_view()
    item=dict(id=9,name='Synthetic loan',kind='liability',current_value=500,currency='CNY',
        value_as_of='2026-09-01',effective_included_in_net_worth=True)
    view['views']['get_manual_wealth_items']['items']=[item]
    assert qualify(view)['C']['allowed']
    levels=qualify(view,analysis_scope='net_worth')
    assert levels['A']['allowed'] and 'MANUAL_VALUE_STALE' in codes(levels['B'])
    assert qualify(view,symbol='LI:US')['C']['allowed']
    tasks=view['data_quality']['manual_review_required']
    assert tasks[0]['current_value']==500 and tasks[0]['value_as_of']=='2026-09-01'
    assert tasks[0]['update_path']=='/#/wealth'
    item.update(kind='asset',already_investment_accounted=True)
    assert 'PENSION_HOLDING_LINK_UNVERIFIED' in codes(qualify(view)['B'])
    assert item['current_value']==500


@pytest.mark.parametrize('case',['publication','sync_failed','fill_pending','fx','position','cash','expired'])
def test_quality_failure_is_scoped_and_never_grants_precision(case):
    view=quality_view();q=view['data_quality'];item=q['security_inputs'][0];account=q['accounts'][0]
    if case=='publication':account['publication_status']='diverged'
    if case=='sync_failed':account['last_attempt_status']='failed'
    if case=='fill_pending':account['fill_sync_pending']=True
    if case=='fx':item['fx_rate_date']='2026-01-01'
    if case=='position':item['positions_as_of']=None
    if case=='cash':q['security_inputs'][1]['cash_as_of']=None
    if case=='expired':view['context_current']=False
    assert not qualify(view)['C']['allowed']
    if case=='publication':assert not qualify(view)['A']['allowed']


def test_requalify_same_frozen_input_at_output_does_not_rejuvenate_or_mutate(monkeypatch):
    view=quality_view();qualify(view);original=json.dumps(view,sort_keys=True)
    monkeypatch.setattr(asset_views,'now',lambda:AT+timedelta(seconds=301))
    result=asset_views.public_view(view)
    assert not result['data_quality']['analysis_eligibility']['C']['allowed']
    assert json.dumps(view,sort_keys=True)==original
    assert result['captured_at']==view['captured_at']
    monkeypatch.setattr(asset_views,'now',lambda:AT+timedelta(seconds=1801))
    result=asset_views.public_view(view)
    assert not result['context_current'] and 'ANALYSIS_CONTEXT_EXPIRED' in codes(result['data_quality']['analysis_eligibility']['B'])


def test_existing_prepare_mcp_web_views_share_quality_and_immutable_totals(context):
    from app.models import WealthItem
    from app.asset_views import frozen_result,load_view
    c=context
    with c.sessions() as db:
        db.add(WealthItem(portfolio_id=7,kind='LIABILITY',name='Synthetic loan',item_type='consumer_loan',
            category='consumer_loan',current_value=100,value_as_of=date(2026,1,1)))
        db.commit()
    view=ready(c);original=json.dumps(view,sort_keys=True)
    for operation in ('get_investment_details','get_manual_wealth_items','get_wealth_overview'):
        result=frozen_result(view,operation,{})
        assert result['snapshot_id']==view['snapshot_id']
        assert result['data_quality']['analysis_eligibility']==asset_views.public_view(view)['data_quality']['analysis_eligibility']
        assert result['data_quality']['manual_review_required'][0]['current_value']==100
    with c.sessions() as db:
        db.query(c.Run).update({'finished_at':datetime.now(timezone.utc)-timedelta(seconds=400)})
        db.commit()
    c.state.offline=True;later=ready(c,'offline')
    assert c.state.calls>=2
    assert later['data_quality']['analysis_eligibility']['A']['allowed']
    assert not later['data_quality']['analysis_eligibility']['C']['allowed']
    assert json.dumps(load_view(c.db,7,view['snapshot_id']),sort_keys=True)==original


def test_no_force_override_and_explicit_scope_contract():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):AnalysisRequest(question='analysis',force=True)
    assert AnalysisRequest(question='loan planning',analysis_scope='net_worth').analysis_scope=='net_worth'


def test_tiger_real_sdk_asset_time_and_qqq_wrapper(monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'backend'))
    from tests.test_u0_review_regressions import service, stock, assets
    summary=assets();summary.update_timestamp=int((datetime.now(timezone.utc)-timedelta(seconds=2)).timestamp()*1000)
    raw=stock();raw.contract.symbol='QQQ'
    sync=service([raw],summary=summary);positions=sync.fetch_positions()
    assert positions[0].vehicle_type=='ETF' and positions[0].economic_asset_class=='EQUITY'
    meta=sync._source_metadata
    assert meta['cash_as_of'] and meta['positions_as_of'] is None and meta['price_as_of'] is None
    assert meta['time_evidence']['cash_as_of']=='TIGER_PORTFOLIO_ACCOUNT_UPDATE_TIMESTAMP'
    summary.update_timestamp=None;sync.fetch_positions()
    assert sync._source_metadata['cash_as_of'] is None
    summary.update_timestamp=999999999999999999;sync.fetch_positions()
    assert sync._source_metadata['cash_as_of'] is None


def test_manual_label_edit_does_not_reset_balance_date(context):
    from app.models import WealthItem
    from backend.services.wealth_service import _item_to_dict
    with context.sessions() as db:
        item=WealthItem(portfolio_id=7,kind='LIABILITY',name='Edited label today',item_type='consumer_loan',
            category='consumer_loan',current_value=123,value_as_of=date(2026,1,1))
        db.add(item);db.commit()
        row=_item_to_dict(item)
        assert row['freshness']!='latest' and 'MANUAL_VALUE_STALE' in row['quality_issues']
        assert row['current_value']==123


def test_ibkr_download_time_does_not_pretend_to_be_source_cash_time():
    from backend.services.action.tests.test_ibkr_native_cash import adapter, row
    a=adapter([row('CashBalance',10),row('CashBalance',5,'CNH'),row('CashBalance',99,'BASE')])
    info=a.get_account_info()
    assert info['cash_as_of'] is None and datetime.fromisoformat(info['account_queried_at']).tzinfo is not None
    assert info['cash_balances']==[{'currency':'CNH','amount':5},{'currency':'USD','amount':10}]


def test_reused_pre_d1_ibkr_run_cannot_reintroduce_local_cash_clock(context):
    from backend.api.broker_sync import _public_quality
    raw=json.dumps(dict(source='ibkr_account_download',cash_as_of=AT.isoformat(),quality='current',issues=[]))
    run=SimpleNamespace(context_json=raw)
    projected=_public_quality(run)
    assert projected['cash_as_of'] is None and projected['quality']=='partial'
    assert 'LEGACY_IBKR_CASH_SOURCE_TIME_UNVERIFIED' in projected['issues']
    assert run.context_json==raw


def test_native_cny_valuation_cannot_be_used_as_hkd_limit_quote():
    view=quality_view();view['data_quality']['security_inputs'][0]['price_basis']='BROKER_CNY_VALUE_PER_UNIT'
    assert 'VALUATION_UNIT_VALUE_NOT_EXECUTABLE_QUOTE' in codes(qualify(view)['C'])


def test_listing_identity_conflict_and_unproven_fund_fail_closed():
    from backend.services.instruments.classification import AssetClassificationEvidence, classify_instrument
    result=classify_instrument(AssetClassificationEvidence(broker='tiger',listing_symbol='QQQ:US',
        currency='USD',broker_security_type='STK',stock_type='COMMON',isin='US78462F1030'))
    assert result.economic_asset_class=='UNKNOWN' and result.verification_status=='CONFLICT'
    unknown=classify_instrument(AssetClassificationEvidence(broker='tiger',listing_symbol='UNPROVEN:US',
        currency='USD',vehicle_type_hint='ETF'))
    assert unknown.economic_asset_class=='UNKNOWN'


@pytest.mark.parametrize('bad_rate',[None,0,float('nan')])
def test_current_fx_date_does_not_make_invalid_rate_credible(bad_rate):
    view=quality_view();view['data_quality']['security_inputs'][0]['fx_rate_to_cny']=bad_rate
    assert 'FX_STORED_VALUATION_UNVERIFIED' in codes(qualify(view)['C'])


def test_portfolio_peer_cannot_use_a_single_symbol_to_narrow_qualification(context,monkeypatch):
    import asyncio
    from tests.test_analysis_skills import peer
    from pocs.chatgpt_skills.analysis import analyze
    peer.__wrapped__(monkeypatch)
    c=context;view=ready(c)
    result=asyncio.run(analyze(c.db,7,'review_portfolio',
        {'question':'portfolio risk','symbol':'LI:US','snapshot_id':view['snapshot_id']},synthetic=True))
    final=result['data_quality']['asset_view']
    assert final['qualification_scope']['symbol'] is None
    assert final['analysis_eligibility']['A']['allowed']
    assert result['snapshot_id']==view['snapshot_id'] and c.state.calls==1
