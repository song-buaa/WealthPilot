"""Owner file -> frozen SQLite U1 -> A-min -> PolicySession -> original HTTP.

Fictional isolated sources only. No prepare, broker or production main lifespan.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
import sqlite3

import pytest

from app import database, asset_views
from backend.services import wealth_service
from backend.services.investment_policy import runtime, store
from backend.services.investment_policy.contracts import digest
from tests.test_investable_assets import view as original_view, AT, STAMP, pension
from tests.test_policy_integration import active  # noqa: F401
from tests.test_allocation_snapshot_errors import client  # noqa: F401


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(runtime, 'utc_now', lambda: STAMP)
    monkeypatch.setattr(store, 'utc_now', lambda: STAMP)
    monkeypatch.setattr(asset_views, 'now', lambda: AT)


@pytest.fixture
def source():
    with pytest.MonkeyPatch.context() as blocked:
        value = original_view.__wrapped__(blocked)
    q = value['data_quality']
    q['security_inputs'][0]['economic_asset_class'] = 'CASH'
    q['security_inputs'][0].update(classification_verification_status='VERIFIED',
        classification_source='SYNTHETIC_OWNER', classification_version='canonical-v1')
    for ident, symbol, economic in [(2,'IB01:LSE','FIXED_INCOME'), (3,'SYN:US','EQUITY')]:
        q['security_inputs'].append({**q['security_inputs'][0], 'id':ident, 'symbol':symbol,
            'vehicle_type':'ETF' if ident==2 else 'COMMON_STOCK', 'economic_asset_class':economic,
            'positions_as_of':STAMP, 'price_as_of':STAMP, 'market_value_cny':'500'})
        for domain in ['accounts', 'deduplication']:
            q['investable_scope_evidence']['coverage'][domain]['asset_refs'].append(f'position:{ident}')
        q['investable_scope_evidence']['dispositions'][f'position:{ident}'] = {
            'state':'INCLUDED', 'reason':'FREE_INVESTMENT', 'evidence_refs':['fixture:owner-purpose']}
    return value


def owner_file(view, path, monkeypatch, **changes):
    evidence = view['data_quality'].pop('investable_scope_evidence')
    raw = {'schema_version':'wealthpilot.owner-scope.v1', 'portfolio_id':7,
        'inventory_sha256':wealth_service.investable_inventory_identity(view),
        'confirmed_at':STAMP, 'valid_until':(AT+timedelta(days=7)).isoformat(),
        **{k:evidence[k] for k in ('dispositions', 'coverage', 'pension_links') if k in evidence}, **changes}
    path.write_text(json.dumps(raw)); path.chmod(0o600)
    monkeypatch.setenv('WEALTHPILOT_INVESTABLE_EVIDENCE_FILE', str(path))
    return raw


def publish(view, path, monkeypatch):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE wealth_snapshots (id INTEGER PRIMARY KEY, portfolio_id INTEGER, '
                   'analysis_request_id TEXT, payload_json TEXT)')
        db.execute('INSERT INTO wealth_snapshots VALUES (1,7,?,?)', ('synthetic',asset_views.dumps(view)))
    monkeypatch.setattr(database, 'DB_PATH', path)


def request(view, tmp_path, monkeypatch, client):
    wealth_service.freeze_investable_scope(view)
    publish(view, tmp_path/'frozen.sqlite', monkeypatch)
    before = hashlib.sha256((tmp_path/'frozen.sqlite').read_bytes()).hexdigest()
    response = client.get('/api/allocation/snapshot')
    assert hashlib.sha256((tmp_path/'frozen.sqlite').read_bytes()).hexdigest() == before
    return response


def test_actual_owner_to_http_success(active, source, tmp_path, monkeypatch, client):
    file = tmp_path/'owner.json'
    owner_file(source, file, monkeypatch)
    before = source['data_quality']['security_inputs'].copy()
    response = request(source, tmp_path, monkeypatch, client)
    assert response.status_code == 200, response.text
    value = response.json(); meta = value['policy_allocation']
    assert value['total_investable_assets'] == 2000
    assert value['by_class']['cash'] == {'amount':1000.,'ratio':.5}
    assert value['by_class']['fixed'] == {'amount':500.,'ratio':.25}
    assert meta['snapshot_sha256'] == digest(source)
    assert meta['snapshot_id'] == 'asset-view:v1:1'
    assert meta['policy_reference']['bundle_version'] == 1
    assert not meta['execution_authorized'] and meta['risk_status'] == 'NOT_ASSESSED_IN_B2'
    for row in meta['classes'].values():
        assert Decimal(row['gap_pp']) == (Decimal(row['current_ratio'])-Decimal(row['target_ratio']))*100
        assert Decimal(row['amount_gap_cny']) == (Decimal(row['amount_cny'])-2000*Decimal(row['target_ratio'])).quantize(Decimal('.01'))
    assert sum(Decimal(r['amount_cny']) for r in meta['classes'].values()) == 2000
    assert source['data_quality']['security_inputs'] == before
    selected = client.get('/api/allocation/snapshot?snapshot_id=asset-view:v1:1')
    assert selected.status_code == 200 and selected.json()['policy_allocation'] == meta


@pytest.mark.parametrize('change', ['coverage', 'pension', 'fx', 'stale', 'classification', 'unverified_class', 'scope', 'hash', 'expired', 'permission', 'missing'])
def test_real_chain_rejects_unknown(active, source, tmp_path, monkeypatch, client, change):
    if change == 'pension':
        pension(source, ['position:1'])  # linked cash remains freely investable
    file = tmp_path/'owner.json'
    owner_file(source, file, monkeypatch)
    raw = json.loads(file.read_text())
    if change == 'coverage': raw['coverage']['btc']['status'] = 'UNKNOWN'
    if change == 'scope': raw['portfolio_id'] = 8
    if change == 'hash': raw['inventory_sha256'] = '0'*64
    if change == 'expired': raw['valid_until'] = '2020-01-01T00:00:00Z'
    if change == 'fx':
        source['data_quality']['security_inputs'][0].update(currency='USD', original_currency='USD', fx_quality='unverified')
        raw['inventory_sha256'] = wealth_service.investable_inventory_identity(source)
    if change == 'stale': source['data_quality']['accounts'][0]['account_synced_at'] = '2020-01-01T00:00:00Z'
    if change == 'classification':
        source['data_quality']['security_inputs'][0]['economic_asset_class'] = 'UNKNOWN'
        raw['inventory_sha256'] = wealth_service.investable_inventory_identity(source)
    if change == 'unverified_class':
        source['data_quality']['security_inputs'][0]['classification_verification_status'] = 'UNVERIFIED'
        raw['inventory_sha256'] = wealth_service.investable_inventory_identity(source)
    file.write_text(json.dumps(raw))
    if change == 'permission': file.chmod(0o644)
    if change == 'missing': monkeypatch.delenv('WEALTHPILOT_INVESTABLE_EVIDENCE_FILE')
    response = request(source, tmp_path, monkeypatch, client)
    assert response.status_code == 409, response.text
    expected = 'POLICY_BUCKET_CLASSIFICATION_UNVERIFIED' if change in {'classification','unverified_class'} else 'POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED'
    assert response.json()['detail'] == expected
    assert response.json()['qualification']['status'] == 'UNKNOWN'


def test_no_stored_snapshot_is_controlled(active, tmp_path, monkeypatch, client):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path/'missing.sqlite')
    response = client.get('/api/allocation/snapshot')
    assert response.status_code == 409
    assert not (tmp_path/'missing.sqlite').exists()


def test_latest_attempt_does_not_resurrect_old_view(source, tmp_path, monkeypatch):
    path = tmp_path/'frozen.sqlite'; publish(source, path, monkeypatch)
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO wealth_snapshots VALUES(2,7,?,?)', ('pending', asset_views.dumps({
            'schema_version':asset_views.VERSION,'pipeline':asset_views.PIPELINE,'state':'preparing'})))
    with pytest.raises(ValueError, match='ASSET_VIEW_NOT_READY'):
        asset_views.latest_view(path, 7)


def test_bound_assets_cannot_ignore_requested_snapshot(active, client):
    from tests.test_policy_integration import session
    with runtime.bind(session()):
        response = client.get('/api/allocation/snapshot?snapshot_id=asset-view:v1:99')
    assert response.status_code == 409
    assert response.json() == {'detail':'POLICY_SCOPE_MISMATCH'}


def test_owner_validity_is_not_rejuvenated_by_capture(active, source, tmp_path, monkeypatch, client):
    owner_file(source, tmp_path/'owner.json', monkeypatch, valid_until=(AT+timedelta(seconds=1)).isoformat())
    wealth_service.freeze_investable_scope(source)
    publish(source, tmp_path/'frozen.sqlite', monkeypatch)
    later = (AT+timedelta(seconds=2)).isoformat()
    monkeypatch.setattr(runtime, 'utc_now', lambda:later)
    monkeypatch.setattr(store, 'utc_now', lambda:later)
    response = client.get('/api/allocation/snapshot')
    assert response.status_code == 409
    assert response.json()['qualification']['issues'][0] == 'INVESTABLE_OWNER_EVIDENCE_UNAVAILABLE_OR_EXPIRED'


@pytest.mark.parametrize('value', ['unstructured', {'wealth:1':'unknown'},
    {'wealth:1':{'economic_asset_class':'COMMODITY','evidence_refs':[' ']}}])
def test_invalid_manual_class_evidence_never_becomes_program_error(source, tmp_path, monkeypatch, value):
    owner_file(source, tmp_path/'owner.json', monkeypatch, manual_classes=value)
    wealth_service.freeze_investable_scope(source)
    assert 'investable_owner_evidence' not in source['data_quality']


def test_unrepresented_known_source_position_is_not_hidden(active, source, tmp_path, monkeypatch, client):
    source['views']['positions'] = {'items':[{'id':1},{'id':2},{'id':3},{'id':99,'segment':'养老'}]}
    owner_file(source, tmp_path/'owner.json', monkeypatch)
    response = request(source, tmp_path, monkeypatch, client)
    assert response.status_code == 409
    assert 'INVESTABLE_POSITION_SCOPE_INCOMPLETE' in response.json()['qualification']['issues']


def test_missing_asset_schema_is_controlled_but_sql_bug_is_not(active, tmp_path, monkeypatch, client):
    path = tmp_path/'empty.sqlite'
    with sqlite3.connect(path): pass
    monkeypatch.setattr(database, 'DB_PATH', path)
    assert client.get('/api/allocation/snapshot').status_code == 409
    def bug(*a, **k): raise sqlite3.OperationalError('syntax error in programmer SQL')
    monkeypatch.setattr(asset_views, 'latest_view', bug)
    assert client.get('/api/allocation/snapshot').status_code == 500
