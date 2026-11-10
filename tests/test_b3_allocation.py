"""B3 original owner → frozen U1 → PolicySession → HTTP, with fictional assets."""

from decimal import Decimal as D
import hashlib

import pytest

from app import asset_views
from backend.services.investment_policy import runtime
from backend.services.investment_policy.contracts import digest
from backend.services.investment_policy.publisher import publish_synthetic
from tests import test_policy_integration as policies
from tests.test_allocation_snapshot_errors import client  # noqa: F401
from tests.test_b2_allocation import clock, owner_file, publish, request, source  # noqa: F401
from tests.test_investable_assets import AT, STAMP

# Intentionally separate from the standard P2 65/35, 9pp fixture.
B3_POLICY = policies.POLICY.decode()
for old, new in (
    ('美国股票ETF子组合 | 25%', '美国股票ETF子组合 | 30%'),
    ('美国直接个股（主动投资） | 25%', '美国直接个股（主动投资） | 30%'),
    ('中国公司主动精选个股 | 4%', '中国公司主动精选个股 | 5%'),
    ('黄金 | 7%', '黄金 | 3%'), ('BTC现货 | 4%', 'BTC现货 | 2%'),
    ('IB01短期美国国债ETF | 25%', 'IB01短期美国国债ETF | 20%'),
    ('65%增长及另类＋35%稳健', '70%增长及另类＋30%稳健'),
    ('U＝65%', 'U＝70%'), ('U≥45%', 'U≥50%'),
    ('AAA／BBB／CCC按ETF子组合内部20%／60%／20%', 'SPY／QQQ／SPMO按ETF子组合内部30%／50%／20%'),
    # Mirror the ACTIVE label forms, with synthetic weights totaling 27% plus 3%.
    ('SYN1 | 3%', 'AAPL（苹果） | 4%'),
    ('SYN2 | 3%', 'Alphabet（GOOG、GOOGL合并） | 5%'),
    ('SYN3 | 4%', 'BRK.B（伯克希尔） | 4%'),
    ('SYN4 | 4%', 'META | 4%'),
    ('SYN5 | 4%', 'MSFT（微软） | 5%'),
    ('SYN6 | 4%', 'TSLA（特斯拉） | 5%'),
    ('已指定六股合计22%', '已指定六股合计27%'),
    ('特别发行人SYN1', '特别发行人AAPL'),
    ('合计 | 25%', '合计 | 30%'),
    ('严格超过9个百分点', '严格超过5个百分点'),
):
    B3_POLICY = B3_POLICY.replace(old, new)
B3_POLICY = B3_POLICY.encode()


@pytest.fixture
def active(monkeypatch, tmp_path):
    monkeypatch.setattr(policies, 'POLICY', B3_POLICY)
    return policies.active.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def b3(source):
    quality = source['data_quality']
    row = quality['security_inputs'][0]
    evidence = quality['investable_scope_evidence']
    positions = [
        ('CASH:CNY', 'CASH', 'CASH', 'CNY', '0'),
        ('SPY:US', 'ETF', 'EQUITY', 'USD', '90'),
        ('QQQ:US', 'ETF', 'EQUITY', 'USD', '150'),
        ('SPMO:US', 'ETF', 'EQUITY', 'USD', '60'),
        ('AAPL:US', 'COMMON_STOCK', 'EQUITY', 'USD', '300'),
        ('PDD:US', 'ADR', 'EQUITY', 'USD', '50'),
        ('IB01:LSE', 'ETF', 'FIXED_INCOME', 'USD', '200'),
        ('CSBGU3:LSE', 'ETF', 'FIXED_INCOME', 'USD', '100'),
    ]
    quality['security_inputs'] = [
        {**row, 'id': i, 'symbol': symbol, 'vehicle_type': vehicle, 'economic_asset_class': economic,
         'currency': currency, 'fx_quality': 'current', 'fx_rate_date': AT.date().isoformat(), 'fx_rate_to_cny': '7',
         'market_value_cny': amount, 'positions_as_of': STAMP, 'price_as_of': STAMP}
        for i, (symbol, vehicle, economic, currency, amount) in enumerate(positions, start=1)
    ]
    refs = [f'position:{i}' for i in range(1, len(positions) + 1)]
    for domain in ('accounts', 'deduplication'):
        evidence['coverage'][domain]['asset_refs'] = refs.copy()
    evidence['dispositions'] = {ref: dict(state='INCLUDED', reason='FREE_INVESTMENT',
                                          evidence_refs=['fixture:purpose']) for ref in refs}
    items = source['views']['get_manual_wealth_items']['items']
    manual_classes = {}
    for ident, key, economic, amount in [(1, 'gold', 'COMMODITY', 30), (2, 'btc', 'ALTERNATIVE', 20)]:
        ref = f'wealth:{ident}'
        items.append(dict(id=ident, kind='asset', item_type='other_asset', current_value=amount,
                          currency='CNY', value_as_of=AT.date().isoformat(), last_verified_at=STAMP, quality_issues=[]))
        fact = 'GOLD_EXPOSURE' if key == 'gold' else 'BTC_SPOT_UNLEVERED'
        evidence['coverage'][key].update(status='PRESENT', asset_refs=[ref],
                                         evidence_refs=[f'{ref}:{fact}:source:fixture:{key}', f'{ref}:{fact}'])
        evidence['coverage']['deduplication']['asset_refs'].append(ref)
        evidence['dispositions'][ref] = dict(state='INCLUDED', reason='FREE_INVESTMENT', evidence_refs=['fixture:purpose'])
        manual_classes[ref] = dict(economic_asset_class=economic, evidence_refs=['fixture:canonical'])
    evidence['coverage']['manual_assets'].update(status='PRESENT', asset_refs=list(manual_classes))
    return source, manual_classes


def read(b3, tmp_path, monkeypatch, client):
    view, manual_classes = b3
    owner_file(view, tmp_path/'owner.json', monkeypatch, manual_classes=manual_classes)
    return request(view, tmp_path, monkeypatch, client)


@pytest.mark.parametrize('stable_shift,hit', [('0',False), ('50',False), ('-50',False),
                                               ('50.00001',True), ('-50.00001',True)])
def test_hierarchy_closes_and_compares_unverified_scope_strictly(active, b3, tmp_path, monkeypatch, client, stable_shift, hit):
    view, _ = b3
    inputs = view['data_quality']['security_inputs']
    inputs[4]['market_value_cny'] = str(300 + D(stable_shift))
    inputs[6]['market_value_cny'] = str(200 - D(stable_shift))
    policy_file = active[0].path
    before = hashlib.sha256(policy_file.read_bytes()).hexdigest()
    result = read(b3, tmp_path, monkeypatch, client)
    assert result.status_code == 200, result.text
    assert hashlib.sha256(policy_file.read_bytes()).hexdigest() == before
    body = result.json()
    meta = body['policy_allocation']
    h = meta['hierarchy']
    assert body['total_investable_assets'] == 1000
    assert meta['snapshot_sha256'] == digest(view)
    assert meta['snapshot_id'] == view['snapshot_id']
    assert meta['policy_reference'] == runtime.current(7).context['policy_reference']
    assert meta['status'] == 'QUALIFIED_MAJOR_CLASSES' and h['status'] == 'QUALIFIED'
    review = h['strategic_review']
    assert review['status'] == 'RULE_SCOPE_UNVERIFIED' and not h['execution_authorized']
    assert review['threshold_pp'] == '5.00' and review['operator'] == 'gt'
    assert review['triggered_classes'] == []
    assert review['candidate_hit_classes'] == (list(h['strategic']) if hit else [])
    assert D(h['strategic']['growth_and_alternatives']['gap_pp']) == D(stable_shift) / 10
    assert all(row['target_kind'] == 'NEUTRAL_POLICY'
               for level in (meta['classes'], h['strategic'], h['secondary'], h['etf']['products'])
               for row in level.values())
    assert [D(v['target_ratio']) for v in h['strategic'].values()] == [D('.7'), D('.3')]
    targets = dict(us_etf='.30', us_direct='.30', china='.05', gold='.03', btc='.02', ib01='.20', cbu3='.10', cash='0')
    assert {k: D(r['target_ratio']) for k, r in h['secondary'].items()} == {k: D(v) for k, v in targets.items()}
    for level in ('strategic', 'secondary'):
        assert sum(D(v['amount_cny']) for v in h[level].values()) == 1000
        assert sum(D(v['target_ratio']) for v in h[level].values()) == 1
        assert sum(D(v['amount_gap_cny']) for v in h[level].values()) == 0
    products = h['etf']['products']
    assert D(h['etf']['denominator_cny']) == 300
    assert sum(D(v['amount_cny']) for v in products.values()) == 300
    for ticker, internal, derived in [('SPY','.30','.09'), ('QQQ','.50','.15'), ('SPMO','.20','.06')]:
        assert D(products[ticker]['current_ratio']) == D(products[ticker]['target_ratio']) == D(internal)
        assert D(products[ticker]['current_ratio_of_d']) == D(products[ticker]['target_ratio_of_d']) == D(derived)
    assert h['classified_assets']['position:6']['bucket'] == 'china'
    assert h['classified_assets']['position:8']['bucket'] == 'cbu3'
    assert h['classified_assets']['wealth:2']['bucket'] == 'btc'
    assert len(h['classified_assets']) == 9  # zero-cash source does not enter D
    assert client.get('/api/allocation/snapshot?snapshot_id=asset-view:v1:99').status_code == 409


@pytest.mark.parametrize('change', [{'symbol':':US'}, {'symbol':'FXI:US'}, {'symbol':'SPY:HK', 'price_as_of': AT.date().isoformat(), 'price_time_basis':'close'},
                                    {'symbol':'SPY:US','vehicle_type':'OPTION'}])
def test_unproven_product_preserves_b2_without_precise_hierarchy(active, b3, tmp_path, monkeypatch, client, change):
    view, _ = b3
    view['data_quality']['security_inputs'][1].update(change)
    result = read(b3, tmp_path, monkeypatch, client)
    assert result.status_code == 200, result.text
    meta = result.json()['policy_allocation']
    assert D(meta['classes']['equity']['amount_cny']) == 650
    h = meta['hierarchy']
    assert h['status'] == 'PARTIAL'
    assert h['unclassified_asset_refs'] == ['position:2']
    assert D(h['unclassified_amount_cny']) == 90
    assert h['strategic_review']['status'] == 'RULE_SCOPE_UNVERIFIED'
    assert h['strategic_review']['candidate_hit_classes'] is None
    assert h['strategic_review']['triggered_classes'] == []
    assert h['etf']['products']['SPY']['current_ratio_of_d'] is None
    assert h['secondary']['us_etf']['amount_gap_cny'] is None


def test_bundle_change_during_u1_read_is_409(active, b3, tmp_path, monkeypatch, client):
    store, _, _ = active
    view, classes = b3
    from backend.services.wealth_service import freeze_investable_scope
    owner_file(view, tmp_path/'owner.json', monkeypatch, manual_classes=classes)
    freeze_investable_scope(view)
    publish(view, tmp_path/'frozen.sqlite', monkeypatch)
    v2 = policies.stage(store, tmp_path, version=2)
    load = asset_views.latest_view

    def switch_after_read(*args):
        result = load(*args)
        publish_synthetic(store, scope='portfolio:7', bundle_id=v2['bundle_id'], expected_sequence=1,
                          request_id='release-2', at=policies.utc_now())
        return result

    monkeypatch.setattr(asset_views, 'latest_view', switch_after_read)
    result = client.get('/api/allocation/snapshot')
    assert result.status_code == 409
    assert result.json()['detail'] == 'POLICY_VERSION_CONFLICT'


@pytest.mark.parametrize('domain,asset', [('gold','wealth:1'), ('btc','wealth:2')])
@pytest.mark.parametrize('mark_only', [False, True])
def test_coverage_without_specific_asset_form_is_partial(active, b3, tmp_path, monkeypatch, client, domain, asset, mark_only):
    view, _ = b3
    refs = ['wealth:1:GOLD_EXPOSURE', 'wealth:2:BTC_SPOT_UNLEVERED'] if mark_only else ['fixture:inventory']
    view['data_quality']['investable_scope_evidence']['coverage'][domain]['evidence_refs'] = refs
    response = read(b3, tmp_path, monkeypatch, client)
    assert response.status_code == 200, response.text
    h = response.json()['policy_allocation']['hierarchy']
    assert h['status'] == 'PARTIAL' and h['unclassified_asset_refs'] == [asset]
    assert h['secondary'][domain]['current_ratio'] is None


def test_zero_etf_sleeve_does_not_divide_by_zero(active, b3, tmp_path, monkeypatch, client):
    view, _ = b3
    rows = view['data_quality']['security_inputs']
    for row in rows[1:4]:
        row['market_value_cny'] = '0'
    rows[4]['market_value_cny'] = '600'
    response = read(b3, tmp_path, monkeypatch, client)
    assert response.status_code == 200, response.text
    h = response.json()['policy_allocation']['hierarchy']
    assert h['status'] == 'QUALIFIED'
    assert h['etf']['status'] == 'NO_ETF_HOLDINGS'
    spy = h['etf']['products']['SPY']
    assert spy['current_ratio'] is None and spy['gap_pp'] is None
    assert D(spy['current_ratio_of_d']) == 0 and D(spy['target_ratio_of_d']) == D('.09')


def test_other_evidenced_us_etf_counts_in_sleeve_without_claiming_product(active, b3, tmp_path, monkeypatch, client):
    view, _ = b3
    quality = view['data_quality']
    quality['security_inputs'][1]['symbol'] = 'OTHER:US'
    quality['investable_scope_evidence']['coverage']['accounts']['evidence_refs'].extend(
        ['position:2:US_EQUITY_ETF', 'position:2:US_EQUITY_ETF:source:fixture:other-us-fund-identity'])
    response = read(b3, tmp_path, monkeypatch, client)
    assert response.status_code == 200, response.text
    h = response.json()['policy_allocation']['hierarchy']
    assert h['status'] == 'QUALIFIED'
    assert D(h['etf']['denominator_cny']) == 300 and D(h['etf']['other_etf_amount_cny']) == 90
    assert D(h['etf']['products']['SPY']['amount_cny']) == 0
    assert D(h['etf']['products']['QQQ']['current_ratio']) == D('.5')
    assert h['classified_assets']['position:2']['product'] is None
    assert 'fixture:other-us-fund-identity' not in response.text


def test_conflicting_region_claim_cannot_override_known_issuer(active, b3, tmp_path, monkeypatch, client):
    view, _ = b3
    evidence = view['data_quality']['investable_scope_evidence']['coverage']['accounts']
    evidence['evidence_refs'].extend(['position:6:US_DIRECT_EQUITY', 'position:6:US_DIRECT_EQUITY:source:fixture:conflicting-claim'])
    response = read(b3, tmp_path, monkeypatch, client)
    assert response.status_code == 200, response.text
    h = response.json()['policy_allocation']['hierarchy']
    assert h['status'] == 'PARTIAL' and h['unclassified_asset_refs'] == ['position:6']


def test_generic_inventory_reference_cannot_support_specific_etf_claim(active, b3, tmp_path, monkeypatch, client):
    view, _ = b3
    quality = view['data_quality']
    quality['security_inputs'][1]['symbol'] = 'OTHER:US'
    quality['investable_scope_evidence']['coverage']['accounts']['evidence_refs'].append('position:2:US_EQUITY_ETF')
    response = read(b3, tmp_path, monkeypatch, client)
    assert response.status_code == 200, response.text
    h = response.json()['policy_allocation']['hierarchy']
    assert h['status'] == 'PARTIAL' and h['unclassified_asset_refs'] == ['position:2']


def test_conflicting_equity_claim_cannot_override_alternative(active, b3, tmp_path, monkeypatch, client):
    view, _ = b3
    evidence = view['data_quality']['investable_scope_evidence']['coverage']['accounts']
    evidence['evidence_refs'].extend(['wealth:2:US_DIRECT_EQUITY', 'wealth:2:US_DIRECT_EQUITY:source:fixture:conflict'])
    response = read(b3, tmp_path, monkeypatch, client)
    assert response.status_code == 200, response.text
    h = response.json()['policy_allocation']['hierarchy']
    assert h['status'] == 'PARTIAL' and h['unclassified_asset_refs'] == ['wealth:2']


@pytest.mark.parametrize('ticker', ['GOOG', 'GOOGL', 'AAPL', 'BRK.B', 'META', 'MSFT', 'TSLA'])
def test_active_core_share_classes_use_policy_tickers(active, b3, tmp_path, monkeypatch, client, ticker):
    view, _ = b3
    view['data_quality']['security_inputs'][4].update(symbol=f'{ticker}:US', name='Unrelated display name')
    response = read(b3, tmp_path, monkeypatch, client)
    assert response.status_code == 200, response.text
    hierarchy = response.json()['policy_allocation']['hierarchy']
    assert hierarchy['status'] == 'QUALIFIED'
    assert hierarchy['classified_assets']['position:5']['bucket'] == 'us_direct'
    assert D(hierarchy['secondary']['us_direct']['amount_cny']) == 300


@pytest.mark.parametrize('symbol,vehicle', [('ALPHABET:US','COMMON_STOCK'), ('GOOG:US','OPTION')])
def test_display_name_or_wrong_vehicle_cannot_authorize_core_identity(active, b3, tmp_path, monkeypatch, client, symbol, vehicle):
    view, _ = b3
    view['data_quality']['security_inputs'][4].update(symbol=symbol, vehicle_type=vehicle, name='Alphabet（GOOG、GOOGL合并）')
    response = read(b3, tmp_path, monkeypatch, client)
    assert response.status_code == 200, response.text
    hierarchy = response.json()['policy_allocation']['hierarchy']
    assert hierarchy['status'] == 'PARTIAL'
    assert hierarchy['unclassified_asset_refs'] == ['position:5']


def test_direct_policy_targets_merge_alphabet_and_keep_unassigned_budget(active, b3, tmp_path, monkeypatch, client):
    view, _ = b3
    q = view['data_quality']
    row = q['security_inputs'][4]
    row.update(symbol='GOOG:US', market_value_cny='120')
    q['security_inputs'].append({**row, 'id':9, 'symbol':'GOOGL:US', 'market_value_cny':'180'})
    evidence = q['investable_scope_evidence']
    evidence['dispositions']['position:9'] = evidence['dispositions']['position:5'].copy()
    for domain in ('accounts', 'deduplication'):
        evidence['coverage'][domain]['asset_refs'].append('position:9')
    response = read(b3, tmp_path, monkeypatch, client)
    assert response.status_code == 200, response.text
    result = response.json()['policy_allocation']['hierarchy']['direct_equity']
    assert result['status'] == 'QUALIFIED' and len(result['issuers']) == 6
    alpha = result['issuers']['Alphabet（GOOG、GOOGL合并）']
    assert alpha['issuer_id'] == 'SEC_CIK_0001652044'
    assert D(alpha['amount_cny']) == 300 and D(alpha['gap_pp']) == 25
    assert all(r['target_kind'] == 'NEUTRAL_POLICY' for r in result['issuers'].values())
    assert result['unallocated'] == {'status':'UNASSIGNED', 'target_kind':'NEUTRAL_POLICY', 'target_ratio':'0.03'}
