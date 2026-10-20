"""PR27 review probes converted to safety assertions; isolated DB/Mock only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def context(tmp_path, monkeypatch):
    from tests.test_u1_asset_views import context as original
    yield from original.__wrapped__(tmp_path, monkeypatch)


def view():
    from tests.test_d1_asset_quality import quality_view
    return quality_view()


def qualify(value, **scope):
    from tests.test_d1_asset_quality import AT
    from app.asset_views import qualify_view
    qualify_view(value, at=AT, **scope)
    return value['data_quality']['analysis_eligibility']


def codes(level):
    return {b['code'] for b in level['blockers']}


def test_r01_p01_original_prepare_keeps_last_good_but_not_amount_permission(context):
    from app.asset_views import load_view, public_view
    from backend.services.asset_analysis import AssetAnalysisPreparation
    from backend.services.restricted_sync import SyncExecutor
    from services.broker_sync.repository import PositionSnapshotRepository
    from services.broker_sync.schema import cash_position
    c = context
    state = SimpleNamespace(offline=False, calls=0,
        cash_time=(datetime.now(timezone.utc)-timedelta(days=10)).isoformat())
    def factory(broker):
        def sync(db, triggered_by='analysis'):
            def fetch():
                state.calls += 1
                if state.offline:
                    raise ValueError('INCOMPLETE_SOURCE')
                return [cash_position(broker, 'fixture', 'CNY', 1000, datetime.now(timezone.utc))]
            return PositionSnapshotRepository(db).synchronize(broker, 'fixture', fetch, retries=0,
                triggered_by=triggered_by, metadata=lambda:dict(source='fixture', complete=True,
                    cash_as_of=state.cash_time))
        return SimpleNamespace(sync_and_persist=sync)
    executor = SyncExecutor(['tiger'], 7, factory, session_factory=c.sessions, account_resolver=lambda *_:'fixture')
    service = AssetAnalysisPreparation(executor, c.db, synthetic=True)
    def prepare(key):
        _, claim = service.request(key)
        service.run(*claim)
        status = service.status(key)
        assert status['state'] == 'ready', status
        return load_view(c.db, 7, status['snapshot_id'])
    first = prepare('initial')
    stored = json.dumps(first, sort_keys=True)
    q = public_view(first)['data_quality']
    assert q['analysis_eligibility']['A']['allowed']
    assert not q['analysis_eligibility']['B']['allowed']
    assert 'CASH_SOURCE_TIME_UNVERIFIED_OR_STALE_FOR_PLANNING' in codes(q['analysis_eligibility']['B'])
    with c.sessions() as db:
        db.query(c.Run).filter_by(status='success').update({'finished_at':datetime.now(timezone.utc)-timedelta(days=10)})
        db.commit()
    state.offline = True
    failed = prepare('failed-refresh')
    q = public_view(failed)['data_quality']
    assert q['accounts'][0]['last_attempt_status'] == 'failed'
    assert q['analysis_eligibility']['A']['allowed']
    assert not q['analysis_eligibility']['B']['allowed']
    assert 'CASH_REFRESH_NOT_SUCCESSFUL_FOR_PLANNING' in codes(q['analysis_eligibility']['B'])
    assert 'CASH_SUCCESSFUL_SYNC_UNVERIFIED_OR_STALE_FOR_PLANNING' in codes(q['analysis_eligibility']['B'])
    assert failed['totals']['investment_assets'] == 1000
    calls = state.calls
    repeated, claim = service.request('failed-refresh')
    assert claim is None and repeated['snapshot_id'] == failed['snapshot_id'] and state.calls == calls
    next_view = prepare('new-local-capture')
    assert next_view['snapshot_id'] != failed['snapshot_id']
    assert not public_view(next_view)['data_quality']['analysis_eligibility']['B']['allowed']
    assert next_view['totals']['investment_assets'] == 1000
    assert json.dumps(load_view(c.db, 7, first['snapshot_id']), sort_keys=True) == stored
    # A genuine successful read with current source evidence can recover B.
    with c.sessions() as db:
        db.query(c.Run).filter_by(status='failed').update({'finished_at':datetime.now(timezone.utc)-timedelta(minutes=10)})
        db.commit()
    state.offline = False
    state.cash_time = datetime.now(timezone.utc).isoformat()
    recovered = prepare('recovered')
    assert public_view(recovered)['data_quality']['analysis_eligibility']['B']['allowed']


@pytest.mark.parametrize('source_age,read_age,failed,allowed', [
    (7200, 7200, False, True),  # B is not C's 300-second policy.
    (86400, 86400, False, True),
    (86401, 0, False, False),  # New local publication cannot rejuvenate source.
    (86401, 86401, True, False),
    (None, 7200, False, True),  # Natural UNKNOWN remains disclosed.
    (None, 86401, True, False),
    (-1, 0, False, False),  # Future/invalid known evidence is not UNKNOWN support.
])
def test_r01_important_cash_has_bounded_amount_policy(source_age, read_age, failed, allowed):
    from tests.test_d1_asset_quality import AT
    v = view();q = v['data_quality'];cash = q['security_inputs'][1]
    q['security_inputs'] = [cash]
    cash['cash_as_of'] = None if source_age is None else (AT-timedelta(seconds=source_age)).isoformat()
    q['accounts'][0].update(account_synced_at=(AT-timedelta(seconds=read_age)).isoformat(),
        last_attempt_status='failed' if failed else 'success')
    levels = qualify(v)
    assert levels['A']['allowed'] and levels['B']['allowed'] is allowed
    assert v['data_quality']['cash_planning_policy']['max_age_seconds'] == 86400
    if source_age is None:
        assert 'CASH_TIME_UNKNOWN_OR_STALE' in q['issues']


def test_r01_small_or_unrelated_old_cash_does_not_veto_b():
    v = view();cash = v['data_quality']['security_inputs'][1]
    cash.update(market_value_cny=.1, cash_as_of='2026-01-01T00:00:00+00:00')
    assert qualify(v)['B']['allowed']
    cash['market_value_cny'] = 1000
    assert qualify(v, symbol='LI:US')['B']['allowed']
    assert 'CASH_TIME_UNKNOWN_OR_STALE' in v['data_quality']['issues']


def test_r01_cash_amount_must_still_be_finite():
    v = view();v['data_quality']['security_inputs'][1]['market_value_cny'] = float('nan')
    levels = qualify(v)
    assert 'ASSET_AMOUNT_UNVERIFIED' in codes(levels['B'])


def test_i01_p02_other_currency_cannot_grant_generic_usd_precision():
    v = view();cash = v['data_quality']['security_inputs'][1]
    cash.update(symbol='CASH-HKD:HK', security_symbol='CASH-HKD:HK', original_currency='HKD')
    levels = qualify(v, symbol='LI:US')
    assert not levels['C']['allowed'] and not v['data_quality']['can_propose_rebalance']
    assert any(b.get('required_currency') == 'USD' for b in levels['C']['blockers'])
    assert 'SETTLEMENT_CURRENCY_CASH_UNAVAILABLE' in codes(levels['C'])


def test_i01_p03_unrelated_currency_is_disclosed_without_vetoing_usd():
    v = view();extra = deepcopy(v['data_quality']['security_inputs'][1])
    extra.update(id=3, symbol='CASH-HKD:HK', security_symbol='CASH-HKD:HK', original_currency='HKD',
        cash_as_of='2026-01-01T00:00:00+00:00')
    v['data_quality']['security_inputs'].append(extra)
    assert qualify(v, symbol='LI:US')['C']['allowed']
    assert 'CASH_TIME_UNKNOWN_OR_STALE' in v['data_quality']['issues']
    assert not qualify(v)['C']['allowed']  # Whole portfolio still includes HKD.


def test_i01_other_account_cash_cannot_grant_target_account_permission():
    v = view();v['data_quality']['security_inputs'][1]['account_ref'] = 'other'
    assert 'SETTLEMENT_CURRENCY_CASH_UNAVAILABLE' in codes(qualify(v, symbol='LI:US')['C'])


@pytest.mark.parametrize('amount', [0, -10])
def test_i01_known_zero_or_negative_cash_is_not_fabricated_buying_power(amount):
    v = view();v['data_quality']['security_inputs'][1]['market_value_cny'] = amount
    assert qualify(v, symbol='LI:US')['C']['allowed']
    assert 'no FX conversion or margin assumed' in v['data_quality']['qualification_scope']['cash_purpose']


def test_i01_scoped_cash_amount_cannot_be_nan_and_security_currency_wins():
    v = view();q = v['data_quality'];q['security_inputs'][1]['market_value_cny'] = float('nan')
    assert 'CASH_AMOUNT_UNVERIFIED' in codes(qualify(v, symbol='LI:US')['C'])
    v = view();v['data_quality']['security_inputs'][0]['security_currency'] = 'HKD'
    assert not qualify(v, symbol='LI:US')['C']['allowed']


@pytest.mark.parametrize('security_type,hint,expected', [
    ('STK', 'COMMON_STOCK', 'EQUITY'), ('ETF', 'ETF', 'EQUITY'),
    ('OPT', 'OPT', 'DERIVATIVE'), ('FUT', 'FUT', 'DERIVATIVE'),
    ('STK', 'OPTION', 'DERIVATIVE'), ('FUND', 'FUND', 'UNKNOWN'), ('', 'ETF', 'UNKNOWN'),
])
def test_i02_p04_qqq_only_verified_native_wrappers(security_type, hint, expected):
    from backend.services.instruments.classification import AssetClassificationEvidence, classify_instrument
    result = classify_instrument(AssetClassificationEvidence(broker='tiger', currency='USD',
        listing_symbol='QQQ:US', broker_security_type=security_type, vehicle_type_hint=hint))
    assert result.economic_asset_class == expected
    if expected == 'EQUITY':
        assert result.vehicle_type == 'ETF' and result.verification_status == 'VERIFIED'
    else:
        assert result.verification_status != 'VERIFIED'


@pytest.mark.parametrize('security_type', ['STK', 'ETF', 'OPT', 'FUT'])
def test_i02_stable_id_conflict_still_fails_closed(security_type):
    from backend.services.instruments.classification import AssetClassificationEvidence, classify_instrument
    result = classify_instrument(AssetClassificationEvidence(broker='tiger', currency='USD',
        listing_symbol='QQQ:US', broker_security_type=security_type, isin='US78462F1030'))
    assert result.economic_asset_class == 'UNKNOWN' and result.verification_status == 'CONFLICT'


def test_i02_unproven_fund_not_guessed_from_similar_name():
    from backend.services.instruments.classification import AssetClassificationEvidence, classify_instrument
    result = classify_instrument(AssetClassificationEvidence(broker='tiger', currency='USD',
        listing_symbol='QQQ_SIMILAR:US', broker_security_type='ETF', long_name='Unknown fund', vehicle_type_hint='ETF'))
    assert result.economic_asset_class == 'UNKNOWN'


def test_n01_p05_independent_purposes_never_extend_single_scope():
    v = view();stock = v['data_quality']['security_inputs'][0]
    stock['symbol'] = stock['security_symbol'] = 'UNMAPPED:US'
    v['issuer_exposure']['unmapped_direct_holdings'] = [dict(stock)]
    portfolio = qualify(v)
    assert not portfolio['B']['allowed'] and portfolio['C']['allowed']
    single = qualify(v, symbol='UNMAPPED:US')
    assert single['C']['allowed']
    assert v['data_quality']['qualification_scope']['symbol'] == 'UNMAPPED:US'
    assert 'MATERIAL_ISSUER_MAPPING_INCOMPLETE' in codes(portfolio['B'])
