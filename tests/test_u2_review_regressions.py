"""PR #25's five review probes converted to safety assertions, synthetic WAL only."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from app.fill_sync import decode, MAX_REFRESH_PUBLICATIONS
from app.models import Position
from backend.services.action.models import OrderRecord
from backend.services.action.brokers.base import OrderStatusUpdate
from backend.services.action.order_manager import OrderManager
from tests.test_u2_fill_sync import context  # noqa: F401 - original isolated fixture


def progress(c, order):
    with c.sessions() as session:
        return decode(session.get(OrderRecord, order).fill_sync_json)


def exhaust(c):
    for _ in range(MAX_REFRESH_PUBLICATIONS):
        c.drain()


@pytest.mark.parametrize('baseline', ['missing', 'stale'])
def test_no_anchor_first_cache_response_keeps_refreshing_across_restart(context, baseline):
    c = context
    c.state.source_clock = 'unknown'  # actual IBKR contract lacks positions_as_of
    if baseline == 'stale':
        c.publish()
        with c.sessions() as session:
            run = session.get(c.PositionSnapshotRun, 1)
            run.finished_at = datetime.now(timezone.utc) - timedelta(hours=1)
            session.commit()
    order = c.dispatch(2)
    assert progress(c, order)['anchor'] is None
    c.observe(order, 2)
    assert len(c.drain()) == 1  # first successful response still has old holdings
    assert c.status(order)['asset_sync']['automatic_retry']
    c.state.quantity = 12
    c.state.cash = 979
    assert len(c.drain()) == 1  # drain creates another executor/DB connection
    with c.sessions() as session:
        assert session.query(Position).filter_by(symbol='ABC:US').one().quantity == 12
    exhaust(c)
    result = c.status(order)['asset_sync']
    assert result['status'] == 'SYNC_UNVERIFIED'  # never invent a post-fill anchor
    assert result['retry_stop_reason'] == 'REFRESH_LIMIT_REACHED'
    assert result['refresh_publications'] == MAX_REFRESH_PUBLICATIONS
    calls = c.state.calls
    assert c.drain() == [] and c.state.calls == calls
    # New actual evidence reopens the bounded window, duplicate evidence does not.
    c.observe(order, 2, shares=2, exec_id=order+'-2', actual_time='2026-10-19T00:00:00+00:00')
    assert progress(c, order)['refresh_publications'] == 0
    assert len(c.drain()) == 1
    c.observe(order, 2, shares=2, actual_time='2026-10-19T00:00:00+00:00')
    assert progress(c, order)['refresh_publications'] == 1


def test_tiger_successful_publications_have_finite_retry_budget(context):
    c = context
    c.state.trading = 'tiger'
    c.state.source_clock = 'unknown'
    c.publish()
    order = c.dispatch(3)
    c.observe(order, 2, fees=False)
    c.state.quantity = 12
    c.state.cash = 979
    exhaust(c)
    result = c.status(order)
    assert result['trade_status'] == 'partially_filled'
    assert result['actual_commission']['status'] == 'UNKNOWN'
    assert result['asset_sync']['status'] == 'SYNC_UNVERIFIED'
    assert not result['asset_sync']['automatic_retry']
    for _ in range(3):
        assert c.drain() == []
    # Repeated cumulative observation cannot replenish budget; a new fill can.
    c.observe(order, 2, shares=2, fees=False)
    assert c.drain() == []
    c.observe(order, 3, fees=False)
    c.state.quantity = 13
    c.state.cash = 968
    assert len(c.drain()) == 1
    assert progress(c, order)['refresh_publications'] == 1


def test_already_verified_buy_then_later_sell_preserves_original_proof(context):
    c = context
    c.publish()
    buy = c.dispatch(2)
    c.observe(buy, 2)
    c.state.quantity, c.state.cash = 12, 979
    c.drain()
    original = progress(c, buy)['verified_coverage']
    sell = c.dispatch(2, 'SELL')
    c.observe(sell, 2)
    c.state.quantity, c.state.cash = 10, 998
    c.drain()
    assert c.status(buy)['asset_sync']['status'] == 'SYNCED'
    assert c.status(sell)['asset_sync']['status'] == 'SYNCED'
    assert progress(c, buy)['verified_coverage'] == original
    c.publish()
    assert progress(c, buy)['verified_coverage'] == original
    assert c.status(buy)['asset_sync']['status'] == 'SYNCED'


def test_verified_roundtrip_new_u1_views_clear_completed_fill_pending(context, monkeypatch):
    from backend.services.asset_analysis import AssetAnalysisPreparation
    from app.asset_views import load_view
    c = context
    monkeypatch.setenv('WEALTHPILOT_ANALYSIS_CACHE_DIR', str(c.tmp_path/'inputs'))
    c.publish()
    buy = c.dispatch(2)
    c.observe(buy, 2)
    c.state.quantity, c.state.cash = 12, 979
    c.drain()
    sell = c.dispatch(2, 'SELL')
    c.observe(sell, 2)
    c.state.quantity, c.state.cash = 10, 998
    c.drain()
    views = []
    for key in ['verified-roundtrip', 'verified-roundtrip-again']:
        p = AssetAnalysisPreparation(c.executor(), c.db, synthetic=True)
        _, claim = p.request(key)
        p.run(*claim)
        result = p.status(key)
        assert result['state'] == 'ready', result
        view = load_view(c.db, 7, result['snapshot_id'])
        assert 'FILL_SYNC_PENDING_OR_UNVERIFIED' not in view['data_quality']['issues']
        views.append(view)
    assert load_view(c.db, 7, views[0]['snapshot_id']) == views[0]


def test_real_tiger_adapter_reports_unsupported_evidence_and_parks(context):
    from backend.services.action.brokers.tiger import TigerBrokerAdapter, TigerOrderStatus
    c = context
    c.state.trading = 'tiger'
    c.state.source_clock = 'unknown'
    c.publish()
    order = c.dispatch(2)
    adapter = object.__new__(TigerBrokerAdapter)
    adapter._account_id, adapter._is_paper = 'synthetic-owner', True
    dto = SimpleNamespace(account='synthetic-owner', contract=SimpleNamespace(currency='USD'),
        id=123, status=TigerOrderStatus.FILLED,
        trade_time=int(datetime.now(timezone.utc).timestamp()*1000), commission=1, reason=None)
    raw = adapter._build_raw('get_order_status', dto)
    # SDK aggregate fees do not establish execution IDs/fee currency.
    assert 'execution_evidence' not in raw and 'commission' not in raw
    with c.sessions() as session:
        OrderManager(session, c.Broker()).apply_broker_observation(order, OrderStatusUpdate(
            '123', order, 'filled', filled_quantity=2, avg_filled_price=Decimal(10), raw_response=raw))
        session.commit()
    c.state.quantity, c.state.cash = 12, 979
    exhaust(c)
    result = c.status(order)
    assert result['actual_commission']['status'] == 'UNKNOWN'
    assert result['asset_sync']['evidence_support']['actual_commissions'] == 'UNSUPPORTED_BY_ADAPTER'
    assert result['asset_sync']['retry_stop_reason'] == 'UNSUPPORTED_EVIDENCE'
    assert not result['asset_sync']['automatic_retry']
    assert c.drain() == []


def test_explicit_refresh_and_evidence_policy_change_reopen_parked_window(context, monkeypatch):
    import app.fill_sync as contract
    c = context
    order = c.dispatch(2)
    c.observe(order, 2)
    exhaust(c)
    assert not c.status(order)['asset_sync']['automatic_retry']
    c.publish()  # manual source entrypoint resets exactly once
    assert progress(c, order)['refresh_publications'] == 1
    exhaust(c)
    assert c.drain() == []
    monkeypatch.setattr(contract, 'EVIDENCE_POLICY_VERSION', contract.EVIDENCE_POLICY_VERSION+1)
    assert len(c.drain()) == 1
    assert progress(c, order)['refresh_publications'] == 1


def test_transient_failure_does_not_consume_successful_refresh_budget(context):
    c = context
    order = c.dispatch(2)
    c.observe(order, 2)
    c.state.offline = True
    for _ in range(MAX_REFRESH_PUBLICATIONS+1):
        c.drain()
    assert progress(c, order)['refresh_publications'] == 0
    assert c.status(order)['asset_sync']['status'] == 'SYNC_FAILED'
    c.state.offline = False
    c.state.quantity, c.state.cash = 12, 979
    assert len(c.drain()) == 1


def test_checkpoint_recovers_regression_without_erasing_historical_proof(context):
    c = context
    c.publish()
    order = c.dispatch(2)
    c.observe(order, 2)
    c.state.quantity, c.state.cash = 12, 979
    c.drain()
    proof = progress(c, order)['verified_coverage']
    c.state.quantity, c.state.cash = 10, 1000
    c.publish()
    assert c.status(order)['asset_sync']['status'] == 'SYNC_DELAYED'
    assert progress(c, order)['verified_coverage'] == proof
    c.state.quantity, c.state.cash = 12, 979
    c.drain()
    assert c.status(order)['asset_sync']['status'] == 'SYNCED'
    assert progress(c, order)['verified_coverage'] == proof
    with c.sessions() as session:
        session.query(Position).delete()
        session.commit()
    assert c.status(order)['asset_sync']['status'] == 'SYNC_UNVERIFIED'


def test_partial_fills_after_checkpoint_keep_order_and_later_reversal_verified(context):
    c = context
    c.publish()
    buy = c.dispatch(3)
    c.observe(buy, 1)
    c.state.quantity, c.state.cash = 11, 989
    c.drain()
    assert c.status(buy)['asset_sync']['status'] == 'SYNCED'
    c.observe(buy, 3)
    c.state.quantity, c.state.cash = 13, 968
    c.drain()
    assert c.status(buy)['asset_sync']['covered_quantity'] == 3
    sell = c.dispatch(2, 'SELL')
    c.observe(sell, 1)
    c.state.quantity, c.state.cash = 12, 977
    c.drain()
    c.observe(sell, 2)
    c.state.quantity, c.state.cash = 11, 986
    c.drain()
    assert c.status(buy)['asset_sync']['status'] == 'SYNCED'
    assert c.status(sell)['asset_sync']['status'] == 'SYNCED'


def test_checkpoint_cannot_accept_regressed_native_time_or_conflicting_fact(context):
    c = context
    c.publish()
    buy = c.dispatch(2)
    c.observe(buy, 2)
    c.state.quantity, c.state.cash = 12, 979
    c.drain()
    original = progress(c, buy)['verified_coverage']
    c.state.source_clock = 'cached'
    c.publish()
    assert c.status(buy)['asset_sync']['status'] == 'SYNC_UNVERIFIED'
    assert progress(c, buy)['verified_coverage'] == original
    c.state.source_clock = 'current'
    c.drain()
    assert c.status(buy)['asset_sync']['status'] == 'SYNCED'
    # A fee contradiction cannot borrow the checkpoint to retain SYNCED.
    with c.sessions() as session:
        import json
        row = session.get(OrderRecord, buy)
        raw = json.loads(row.raw_broker_response)
        raw['execution_evidence'][0]['commission'] = 2
        OrderManager(session, c.Broker()).apply_broker_observation(buy, OrderStatusUpdate(
            row.broker_order_id, buy, 'filled', filled_quantity=2,
            avg_filled_price=Decimal(10), raw_response=raw))
        session.commit()
    c.publish()
    assert c.status(buy)['asset_sync']['status'] == 'SYNC_UNVERIFIED'
    assert progress(c, buy)['verified_coverage'] == original


def test_cron_does_not_replenish_paused_refresh_budget(context):
    c = context
    order = c.dispatch(2)
    c.observe(order, 2)
    exhaust(c)
    executor = c.executor()
    jobs, _ = executor.reserve('snowball', 'cron')
    executor.execute(**jobs[0])
    assert not c.status(order)['asset_sync']['automatic_retry']
    assert progress(c, order)['refresh_publications'] == MAX_REFRESH_PUBLICATIONS
    assert c.drain() == []


def test_interleaved_partial_buy_and_sell_start_from_verified_current_state(context):
    c = context
    c.publish()
    buy = c.dispatch(2)
    c.observe(buy, 1)
    c.state.quantity, c.state.cash = 11, 989
    c.drain()
    sell = c.dispatch(2, 'SELL')  # the buy remainder is still open
    assert progress(c, sell)['anchor']['watermarks'][buy]['quantity'] == 1
    c.observe(sell, 1)
    c.state.quantity, c.state.cash = 10, 998
    c.drain()
    assert c.status(buy)['asset_sync']['status'] == 'SYNCED'
    assert c.status(sell)['asset_sync']['status'] == 'SYNCED'
    c.observe(buy, 2)
    c.state.quantity, c.state.cash = 11, 987
    c.drain()
    c.observe(sell, 2)
    c.state.quantity, c.state.cash = 10, 996
    c.drain()
    assert c.status(buy)['asset_sync']['status'] == 'SYNCED'
    assert c.status(sell)['asset_sync']['status'] == 'SYNCED'
