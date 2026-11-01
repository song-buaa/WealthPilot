"""A-min: frozen synthetic original-owner facts, no DB/Provider/Policy writes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import socket
import sqlite3

import pytest

from backend.services.investable_assets import project_investable_assets
from backend.services.investment_policy.contracts import digest

AT = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
STAMP = AT.isoformat()


@pytest.fixture
def view(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('projection must not perform I/O')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    from backend.services.investment_policy import runtime
    monkeypatch.setattr(runtime, 'resolve', forbidden)
    coverage = {k: dict(status='ABSENT', as_of=STAMP, evidence_refs=['fixture:inventory'], asset_refs=[])
                for k in ['accounts', 'manual_assets', 'pensions', 'gold', 'btc', 'deduplication']}
    coverage['accounts'].update(status='PRESENT', asset_refs=['position:1'])
    coverage['deduplication'].update(status='PRESENT', asset_refs=['position:1'])
    return dict(schema_version='wealthpilot.asset-view.v1', pipeline='u1.1', state='ready',
        portfolio_id=7, snapshot_id='asset-view:v1:1', captured_at=STAMP,
        totals=dict(total_assets=1500, total_liabilities=2000, net_worth=-500),
        views={'get_manual_wealth_items': {'items': []}},
        data_quality=dict(accounts=[dict(account_ref='account_fixture', publication_status='matched',
            last_attempt_status='success', account_synced_at=STAMP, quality='current', complete=True)],
            security_inputs=[dict(id=1, symbol='CASH:CNY', vehicle_type='CASH', currency='CNY',
                market_value_cny='1000.00', account_ref='account_fixture', cash_as_of=STAMP)],
            investable_scope_evidence=dict(schema_version='wealthpilot.investable-scope.v1',
                portfolio_id=7, snapshot_id='asset-view:v1:1', as_of=STAMP, coverage=coverage,
                dispositions={'position:1': dict(state='INCLUDED', reason='FREE_INVESTMENT', evidence_refs=['fixture:purpose'])})))


def project(view, **kw):
    return project_investable_assets(view, portfolio_id=7, snapshot_id='asset-view:v1:1',
                                     snapshot_sha256=digest(view), at=AT, **kw)


def test_qualified_decimal_projection_is_pure_and_not_net_worth(view):
    before = deepcopy(view)
    result = project(view)
    assert result.status == 'QUALIFIED'
    assert result.policy_assets.denominator_cny == Decimal('1000.00')
    assert result.total_assets_cny == 1500 and result.liabilities_cny == 2000
    assert result.net_assets_cny == -500  # Negative net worth does not subtract debt from D.
    assert result.snapshot_sha256 == digest(view)
    assert not result.execution_authorized
    assert view == before
    assert result.policy_assets.bucket_weights == {}  # No B2 classification/targets.


@pytest.mark.parametrize('reason', ['LIVING_CASH', 'EMERGENCY_RESERVE', 'DEBT_RESERVE', 'RESTRICTED'])
def test_known_non_investable_cash_is_excluded_once(view, reason):
    q = view['data_quality']; q['security_inputs'].append({**q['security_inputs'][0], 'id': 2, 'market_value_cny': '123.45'})
    e = q['investable_scope_evidence']; e['coverage']['accounts']['asset_refs'].append('position:2')
    e['coverage']['deduplication']['asset_refs'].append('position:2')
    e['dispositions']['position:2'] = dict(state='EXCLUDED', reason=reason, evidence_refs=['fixture:restriction'])
    result = project(view)
    assert result.status == 'QUALIFIED'
    assert result.policy_assets.denominator_cny == Decimal('1000')
    assert result.assets[1].state == 'EXCLUDED'


@pytest.mark.parametrize('domain', ['accounts', 'manual_assets', 'pensions', 'gold', 'btc', 'deduplication'])
def test_missing_external_or_inventory_coverage_never_means_zero(view, domain):
    view['data_quality']['investable_scope_evidence']['coverage'][domain]['status'] = 'UNKNOWN'
    result = project(view)
    assert result.status == 'UNKNOWN' and result.policy_assets.denominator_cny is None
    assert not result.policy_assets.denominator_qualified


@pytest.mark.parametrize('key,value', [('cash_as_of', None), ('cash_as_of', '2020-01-01T00:00:00Z'),
                                     ('currency', None), ('market_value_cny', 'NaN'),
                                     ('market_value_cny', True), ('market_value_cny', '-1')])
def test_source_data_unknown_cannot_be_overridden_by_purpose_evidence(view, key, value):
    view['data_quality']['security_inputs'][0][key] = value
    assert project(view).policy_assets.denominator_cny is None


@pytest.mark.parametrize('change', [dict(last_attempt_status='failed'), dict(publication_status='unverified'),
                                   dict(account_synced_at='2020-01-01T00:00:00Z'), dict(fill_sync_pending=True),
                                   dict(quality='partial'), dict(complete=False)])
def test_account_failure_and_last_good_remain_unknown(view, change):
    view['data_quality']['accounts'][0].update(change)
    assert project(view).status == 'UNKNOWN'


def test_old_u1_or_client_flags_cannot_establish_qualification(view):
    del view['data_quality']['investable_scope_evidence']
    view['data_quality'].update(can_propose_rebalance=True, policy_asset_qualification={'denominator_qualified': True})
    result = project(view)
    assert result.policy_assets.denominator_cny is None
    assert all(row.state == 'UNKNOWN' for row in result.assets)


def test_snapshot_scope_hash_and_clock_bindings(view):
    args = dict(portfolio_id=7, snapshot_id=view['snapshot_id'], snapshot_sha256=digest(view), at=AT)
    for change, error in [(dict(portfolio_id=8), 'SCOPE'), (dict(snapshot_id='asset-view:v1:2'), 'SCOPE'),
                           (dict(snapshot_sha256='0'*64), 'HASH'), (dict(at=AT.replace(tzinfo=None)), 'CLOCK')]:
        with pytest.raises(ValueError, match=error):
            project_investable_assets(view, **{**args, **change})
    view['totals']['total_assets'] = 999
    with pytest.raises(ValueError, match='HASH'):
        project_investable_assets(view, **args)


def test_evidence_scope_and_duplicate_rows_fail_closed(view):
    view['data_quality']['investable_scope_evidence']['portfolio_id'] = 8
    with pytest.raises(ValueError, match='EVIDENCE_SCOPE'):
        project(view)
    view['data_quality']['investable_scope_evidence']['portfolio_id'] = 7
    view['data_quality']['security_inputs'] *= 2
    with pytest.raises(ValueError, match='DUPLICATE'):
        project(view)


def test_source_clock_not_rejuvenated_by_fresh_receipt(view):
    view['captured_at'] = (AT-timedelta(seconds=301)).isoformat()
    assert project(view).status == 'UNKNOWN'


def pension(view, links):
    row = dict(id=10, kind='asset', item_type='personal_pension', current_value=20,
               currency='CNY', value_as_of='2026-11-02', quality_issues=[], already_investment_accounted=True)
    view['views']['get_manual_wealth_items']['items'].append(row)
    e = view['data_quality']['investable_scope_evidence']
    e['coverage']['deduplication']['asset_refs'].append('wealth:10')
    for domain in ['manual_assets', 'pensions']:
        e['coverage'][domain].update(status='PRESENT', asset_refs=['wealth:10'])
    e['dispositions']['wealth:10'] = dict(state='EXCLUDED', reason='DUPLICATE', evidence_refs=['fixture:pension'])
    e['pension_links'] = {'wealth:10': links}


@pytest.mark.parametrize('links', [[], ['position:999'], ['position:1', 'position:1']])
def test_pension_amount_dedup_does_not_prove_holding_identity(view, links):
    pension(view, links)
    result = project(view)
    assert result.status == 'UNKNOWN'
    assert 'PENSION_HOLDING_LINK_UNVERIFIED' in result.policy_assets.issues


def test_verified_pension_mapping_excludes_restricted_holding_and_duplicate_amount(view):
    q = view['data_quality']; q['security_inputs'].append({**q['security_inputs'][0], 'id': 2, 'market_value_cny': 20})
    e = q['investable_scope_evidence']; e['coverage']['accounts']['asset_refs'].append('position:2')
    e['coverage']['deduplication']['asset_refs'].append('position:2')
    e['dispositions']['position:2'] = dict(state='EXCLUDED', reason='PENSION', evidence_refs=['fixture:restricted-pension'])
    pension(view, ['position:2'])
    result = project(view)
    assert result.status == 'QUALIFIED'
    assert result.policy_assets.denominator_cny == 1000


def test_fx_source_quality_and_native_currency_are_not_assumed(view):
    row = view['data_quality']['security_inputs'][0]
    row.update(currency='USD', original_currency='USD', fx_rate_to_cny=7,
               fx_rate_date='2026-11-02', fx_quality='unverified')
    assert project(view).status == 'UNKNOWN'
    row['fx_quality'] = 'current'
    assert project(view).status == 'QUALIFIED'
    row['fx_rate_date'] = '2020-01-01'
    assert project(view).status == 'UNKNOWN'


def test_decimal_addition_preserves_source_precision(view):
    q = view['data_quality']; q['security_inputs'][0]['market_value_cny'] = '0.1'
    q['security_inputs'].append({**q['security_inputs'][0], 'id': 2, 'market_value_cny': '0.2'})
    e = q['investable_scope_evidence']; e['coverage']['accounts']['asset_refs'].append('position:2')
    e['coverage']['deduplication']['asset_refs'].append('position:2')
    e['dispositions']['position:2'] = e['dispositions']['position:1'].copy()
    assert project(view).policy_assets.denominator_cny == Decimal('0.3')


def test_stale_debt_invalidates_net_worth_not_investment_denominator(view):
    view['views']['get_manual_wealth_items']['items'].append(dict(id=20, kind='liability',
        current_value=2000, currency='CNY', value_as_of='2020-01-01', quality_issues=['MANUAL_VALUE_STALE']))
    result = project(view)
    assert result.status == 'QUALIFIED' and result.policy_assets.denominator_cny == 1000
    assert result.net_assets_cny is None and result.wealth_totals_status == 'UNKNOWN'


def test_missing_purpose_or_empty_evidence_stays_unknown(view):
    e = view['data_quality']['investable_scope_evidence']
    e['dispositions']['position:1']['evidence_refs'] = []
    assert project(view).status == 'UNKNOWN'
    del e['dispositions']['position:1']
    assert project(view).status == 'UNKNOWN'


def test_manual_free_asset_and_restricted_pension_use_original_wealth_values(view):
    manual = view['views']['get_manual_wealth_items']['items']
    manual.extend([dict(id=10, kind='asset', item_type='bank_cash', current_value='12.34',
                       currency='CNY', value_as_of='2026-11-02', last_verified_at=STAMP, quality_issues=[]),
                   dict(id=11, kind='asset', item_type='personal_pension', current_value='99',
                       currency='CNY', value_as_of='2026-11-02', last_verified_at=STAMP, quality_issues=[])])
    e = view['data_quality']['investable_scope_evidence']
    e['coverage']['manual_assets'].update(status='PRESENT', asset_refs=['wealth:10', 'wealth:11'])
    e['coverage']['pensions'].update(status='PRESENT', asset_refs=['wealth:11'])
    e['coverage']['deduplication']['asset_refs'].extend(['wealth:10', 'wealth:11'])
    e['dispositions']['wealth:10'] = dict(state='INCLUDED', reason='FREE_INVESTMENT', evidence_refs=['fixture:bank'])
    e['dispositions']['wealth:11'] = dict(state='EXCLUDED', reason='PENSION', evidence_refs=['fixture:pension'])
    assert project(view).policy_assets.denominator_cny == Decimal('1012.34')
    manual[0]['value_as_of'] = '2020-01-01'
    assert project(view).policy_assets.denominator_cny is None
    manual[0]['value_as_of'] = '2026-11-02'
    manual[0]['last_verified_at'] = '2020-01-01'
    assert project(view).policy_assets.denominator_cny is None


def test_invalid_freeform_qualifying_flag_is_rejected(view):
    e = view['data_quality']['investable_scope_evidence']
    e['denominator_qualified'] = True
    assert project(view).status == 'UNKNOWN'


def test_equity_source_price_time_reuses_original_calendar_qualification(view):
    row = view['data_quality']['security_inputs'][0]
    row.update(symbol='TSLA:US', vehicle_type='COMMON_STOCK', positions_as_of=STAMP,
               price_as_of=STAMP)
    assert project(view).status == 'QUALIFIED'
    row['price_as_of'] = '2020-01-01'
    assert project(view).status == 'UNKNOWN'


def test_cash_only_exclusion_cannot_hide_an_equity_holding(view):
    row = view['data_quality']['security_inputs'][0]
    row.update(symbol='TSLA:US', vehicle_type='COMMON_STOCK')
    e = view['data_quality']['investable_scope_evidence']
    e['dispositions']['position:1'].update(state='EXCLUDED', reason='DEBT_RESERVE')
    result = project(view)
    assert result.status == 'UNKNOWN'
    assert 'ASSET_EXCLUSION_NOT_APPLICABLE' in result.assets[0].issues


def test_failed_account_cannot_be_hidden_by_excluding_its_observed_rows(view):
    q = view['data_quality']
    q['accounts'].append(dict(account_ref='account_unknown', publication_status='matched',
        last_attempt_status='failed', account_synced_at=STAMP, quality='current', complete=True))
    q['security_inputs'].append({**q['security_inputs'][0], 'id': 2, 'account_ref': 'account_unknown'})
    e = q['investable_scope_evidence']
    for domain in ['accounts', 'deduplication']:
        e['coverage'][domain]['asset_refs'].append('position:2')
    e['dispositions']['position:2'] = dict(state='EXCLUDED', reason='RESTRICTED', evidence_refs=['fixture:restricted'])
    assert project(view).status == 'UNKNOWN'
