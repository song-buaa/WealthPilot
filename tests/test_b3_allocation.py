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
    ('其他自主选股 | 3%', '其他自主选股 | 8%'),
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
        ('SYN1:US', 'COMMON_STOCK', 'EQUITY', 'USD', '300'),
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
                                         evidence_refs=[f'fixture:{key}', f'{ref}:{fact}'])
        evidence['coverage']['deduplication']['asset_refs'].append(ref)
        evidence['dispositions'][ref] = dict(state='INCLUDED', reason='FREE_INVESTMENT', evidence_refs=['fixture:purpose'])
        manual_classes[ref] = dict(economic_asset_class=economic, evidence_refs=['fixture:canonical'])
    evidence['coverage']['manual_assets'].update(status='PRESENT', asset_refs=list(manual_classes))
    return source, manual_classes


def read(b3, tmp_path, monkeypatch, client):
    view, manual_classes = b3
    owner_file(view, tmp_path/'owner.json', monkeypatch, manual_classes=manual_classes)
    return request(view, tmp_path, monkeypatch, client)


@pytest.mark.parametrize('stable_shift,review', [('0','NOT_TRIGGERED'), ('50','NOT_TRIGGERED'),
                                                 ('50.00001','HUMAN_REVIEW'), ('-50.00001','HUMAN_REVIEW')])
def test_hierarchy_closes_and_reviews_strictly(active, b3, tmp_path, monkeypatch, client, stable_shift, review):
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
    assert h['strategic_review']['status'] == review and not h['execution_authorized']
    assert h['strategic_review']['threshold_pp'] == '5.00'
    assert h['strategic_review']['triggered_classes'] == (list(h['strategic']) if review == 'HUMAN_REVIEW' else [])
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
    assert h['strategic_review']['status'] == 'UNKNOWN'
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
def test_coverage_without_specific_asset_form_is_partial(active, b3, tmp_path, monkeypatch, client, domain, asset):
    view, _ = b3
    view['data_quality']['investable_scope_evidence']['coverage'][domain]['evidence_refs'] = ['fixture:inventory']
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
