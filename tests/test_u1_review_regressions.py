"""PR #24 review probes converted to safety assertions, synthetic sources only."""
import asyncio
from datetime import datetime, timedelta, timezone
import json

import pytest
from app.models import Position, WealthItem
from app.asset_views import load_view, public_view
from tests.test_u1_asset_views import context, ready, peer  # noqa: F401


def assert_old_view(c, view):
    assert load_view(c.db,7,view['snapshot_id'])==view


def test_web_clear_requires_controlled_refresh(context,monkeypatch):
    c=context
    with c.sessions() as session:
        session.add(WealthItem(portfolio_id=7,kind='ASSET',name='Independent bank',item_type='bank_cash',
            category='cash_deposits',current_value=50));session.commit()
    before=ready(c)
    from backend.services import portfolio_service
    from backend.api import portfolio
    from app import state
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setattr(portfolio_service,'get_session',c.sessions)
    monkeypatch.setattr(state,'portfolio_id',7)
    app=FastAPI();app.include_router(portfolio.router,prefix='/portfolio')
    assert TestClient(app).delete('/portfolio/positions').status_code==200
    after=ready(c,'after-web-clear')
    assert c.state.calls==2
    assert after['views']['wealth_summary']['total_assets']==1050
    assert after['data_quality']['accounts'][0]['publication_status']=='matched'
    assert_old_view(c,before)


@pytest.mark.parametrize('field,value',[
    ('quantity',9),('cost_price',7),('current_price',8),('original_value',88),
    ('market_value_cny',2000),('currency','USD'),('symbol','CASH-OTHER:CN'),
    ('broker_account_id','OTHER'),('segment','养老')])
def test_same_count_changed_facts_cannot_be_reused(context,field,value):
    c=context;before=ready(c)
    with c.sessions() as session:
        setattr(session.query(Position).one(),field,value);session.commit()
    after=ready(c,'changed-facts')
    assert c.state.calls==2
    # Identity collisions remain fail-closed under the original U0 rules.
    if after['data_quality']['accounts'][0]['publication_status']!='matched':
        assert after['data_quality']['status']=='partial'
        assert after['data_quality']['can_propose_rebalance'] is False
    assert_old_view(c,before)


@pytest.mark.parametrize('operation',['csv','platform'])
def test_original_import_and_platform_replacement_remain_available(context,monkeypatch,operation):
    c=context;before=ready(c)
    from backend.services import portfolio_service as svc
    monkeypatch.setattr(svc,'get_session',c.sessions)
    if operation=='csv':
        csv='平台,资产名称,代码,大类,市值(人民币)\n老虎证券,Imported cash,CASH-CNY,货币,75\n'
        result=svc.import_from_csv(csv.encode(),7,content_type='positions')
        assert result['imported']==1 and not result['errors']
    else:
        svc._replace_positions_by_platform(7,'老虎证券',[dict(name='Imported cash',ticker='CASH-CNY',
            symbol='CASH-CNY:CN',asset_class='货币',vehicle_type='CASH',currency='CNY',market_value_cny=75)])
    after=ready(c,'after-import')
    assert c.state.calls==2
    # Unknown imported cash may not be silently claimed/deleted by a refresh.
    assert after['data_quality']['status']=='partial'
    assert after['data_quality']['can_propose_rebalance'] is False
    assert 'SYNC_PUBLICATION_DIVERGED' in after['data_quality']['issues']
    with c.sessions() as session:assert session.query(Position).filter_by(broker=None).count()==1
    assert_old_view(c,before)


def test_offline_after_clear_reports_divergence_not_fresh_zero(context,monkeypatch):
    c=context;ready(c)
    from backend.services import portfolio_service
    monkeypatch.setattr(portfolio_service,'get_session',c.sessions)
    portfolio_service.clear_positions(7);c.state.offline=True
    view=ready(c,'offline-after-clear')
    assert c.state.calls==2
    assert 'SYNC_PUBLICATION_DIVERGED' in view['data_quality']['issues']
    assert view['data_quality']['can_propose_rebalance'] is False


def test_second_connection_write_between_reuse_and_backup_is_caught(context,monkeypatch):
    c=context;before=ready(c)
    from backend.services import asset_analysis
    original=asset_analysis.create_frozen_copy
    def race(*args,**kwargs):
        with c.sessions() as other:
            row=other.query(Position).one();row.market_value_cny=9000;other.commit()
        return original(*args,**kwargs)
    monkeypatch.setattr(asset_analysis,'create_frozen_copy',race)
    view=ready(c,'interleaved-write')
    assert c.state.calls==1  # race happened after the reuse transaction
    assert view['views']['wealth_summary']['total_assets']==9000
    assert 'SYNC_PUBLICATION_DIVERGED' in view['data_quality']['issues']
    assert view['data_quality']['can_propose_rebalance'] is False
    assert_old_view(c,before)


def test_legacy_success_is_not_retroactively_sealed(context):
    c=context;ready(c)
    with c.sessions() as session:
        row=session.query(c.Run).one();meta=json.loads(row.context_json);meta.pop('published_positions')
        row.context_json=json.dumps(meta);session.commit()
    c.state.offline=True
    view=ready(c,'legacy')
    assert c.state.calls==2
    assert 'SYNC_PUBLICATION_UNVERIFIED' in view['data_quality']['issues']
    assert view['data_quality']['can_propose_rebalance'] is False


def test_legacy_view_remains_readable_but_cannot_claim_unproven_qualification(context):
    c=context;view=ready(c)
    legacy=json.loads(json.dumps(view))
    for account in legacy['data_quality']['accounts']:account.pop('publication_status')
    response=public_view(legacy)
    assert response['views']==view['views'] and response['snapshot_id']==view['snapshot_id']
    assert response['data_quality']['can_propose_rebalance'] is False
    assert 'SYNC_PUBLICATION_UNVERIFIED' in response['data_quality']['issues']
    assert_old_view(c,view)


def test_confirmed_empty_publication_is_reusable(context):
    c=context;ready(c)
    with c.sessions() as session:
        row=session.query(c.Run).one();row.finished_at=datetime.now(timezone.utc)-timedelta(seconds=400);session.commit()
    c.state.empty=True
    first=ready(c,'confirmed-empty');second=ready(c,'reuse-confirmed-empty')
    assert c.state.calls==2
    assert first['views']['get_investment_details']['items']==[]
    assert second['data_quality']['accounts'][0]['publication_status']=='matched'
    assert 'SYNC_PUBLICATION_DIVERGED' not in second['data_quality']['issues']


@pytest.mark.parametrize('start,end,expired',[(250,350,False),(1790,1810,True)])
def test_peer_requalifies_at_output_without_replacing_frozen_input(context,peer,monkeypatch,start,end,expired):
    c=context;view=ready(c);captured=datetime.fromisoformat(view['captured_at'])
    from app import asset_views
    from pocs.chatgpt_skills.analysis import analyze
    from backend.services import decision_service_v3
    monkeypatch.setattr(asset_views,'now',lambda:captured+timedelta(seconds=start))
    original=decision_service_v3.run_chat_stream_v3
    async def elapsed(*args,**kwargs):
        async for event in original(*args,**kwargs):
            monkeypatch.setattr(asset_views,'now',lambda:captured+timedelta(seconds=end))
            yield event
    monkeypatch.setattr(decision_service_v3,'run_chat_stream_v3',elapsed)
    result=asyncio.run(analyze(c.db,7,'review_portfolio',{'question':'review','snapshot_id':view['snapshot_id']},synthetic=True))
    assert result['snapshot_id']==view['snapshot_id'] and c.state.calls==1
    assert result['data_quality']['asset_view']['can_propose_rebalance'] is False
    assert 'STALE_ANALYSIS_VIEW' in result['issues']
    assert result['data_quality']['context_current'] is (not expired)
    if expired:
        assert result['status']=='unavailable' and result['professional_result']=={}
        assert 'ANALYSIS_CONTEXT_EXPIRED' in result['issues'] and '上下文已到期' in result['conclusion']
    else:
        assert result['status']=='partial' and '仅作一般分析' in result['conclusion']
    assert result['evidence']['asset_view']['wealth_summary']['total_assets']==1000
    assert_old_view(c,view)


@pytest.mark.parametrize('reason',['SYNC_SOURCE_FAILED','FX_RATE_UNAVAILABLE','SOURCE_ACCOUNT_UNPROVEN','sensitive account=DO_NOT_EXPOSE'])
def test_latest_attempt_reason_and_last_good_are_separate(context,reason):
    c=context;before=ready(c)
    with c.sessions() as session:
        row=session.query(c.Run).one();row.finished_at=datetime.now(timezone.utc)-timedelta(seconds=400);session.commit()
    c.state.error_code=reason
    view=ready(c,'failed-attempt')
    if 'DO_NOT_EXPOSE' in reason:
        with c.sessions() as session:
            latest=session.query(c.Run).order_by(c.Run.id.desc()).first()
            latest.error_message=reason;session.commit()
        view=ready(c,'legacy-raw-error')
    account=view['data_quality']['accounts'][0]
    expected=reason if reason in {'FX_RATE_UNAVAILABLE','SOURCE_ACCOUNT_UNPROVEN'} else 'SYNC_SOURCE_FAILED'
    assert account['last_attempt_status']=='failed' and account['last_attempt_time']
    assert account['last_attempt_reason_code']==expected
    assert account['last_good_run_id']==before['data_quality']['accounts'][0]['last_good_run_id']
    assert account['last_good_quality']=='current' and account['publication_status']=='matched'
    assert view['views']['wealth_summary']['total_assets']==1000
    assert view['data_quality']['status']=='partial' and not view['data_quality']['can_propose_rebalance']
    assert 'DO_NOT_EXPOSE' not in json.dumps(public_view(view))
    assert 'published_positions' not in json.dumps(public_view(view))
