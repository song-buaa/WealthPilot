"""B1 HTTP boundary only: no backend lifespan, provider, refresh or asset assembly."""
import hashlib
import socket

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import state
from app.allocation.types import AllocationSnapshot, ClassAllocation
from backend.api import allocation as api
from backend.services import allocation_service
from backend.services.investment_policy.parser import PolicyError
from backend.services.investment_policy.runtime import PolicySession, bind
from tests.test_policy_integration import active, session, metric  # noqa: F401


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(state, 'portfolio_id', 7)
    from backend.services.restricted_sync import SyncExecutor
    from backend.services.action.order_manager import OrderManager
    from backend.services.action.brokers.ibkr import IBKRBrokerAdapter
    calls = []
    def forbidden(*args, **kwargs):
        calls.append('forbidden_side_effect')
        raise AssertionError('snapshot must not use network or legacy defaults')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(allocation_service, 'get_default_targets', forbidden)
    monkeypatch.setattr(SyncExecutor, 'reserve', forbidden)
    monkeypatch.setattr(SyncExecutor, 'execute', forbidden)
    monkeypatch.setattr(OrderManager, 'place_order', forbidden)
    monkeypatch.setattr(IBKRBrokerAdapter, 'what_if_limit_order', forbidden)
    app = FastAPI()  # Deliberately excludes production main/lifespan.
    app.include_router(api.router, prefix='/api/allocation')
    with TestClient(app, raise_server_exceptions=False) as result:
        yield result
    assert calls == []  # Applies even when a request is expected to return 500.


@pytest.mark.parametrize('code', [
    'POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED',
    'POLICY_BUCKET_CLASSIFICATION_UNVERIFIED',
    'POLICY_ASSET_WEIGHTS_NOT_CLOSED', 'POLICY_VERSION_CONFLICT',
])
def test_expected_domain_conflicts_keep_string_detail(client, monkeypatch, code):
    def fail(_):
        raise PolicyError(code)
    monkeypatch.setattr(api, 'get_snapshot', fail)
    response = client.get('/api/allocation/snapshot')
    assert response.status_code == 409
    assert response.json() == {'detail': code}  # No zero assets or success payload.


@pytest.mark.parametrize('exc', [RuntimeError('private diagnostic'),
                                ValueError('private diagnostic'),
                                PolicyError('UNRECOGNIZED_PRIVATE_DIAGNOSTIC')])
def test_unknown_errors_remain_500(client, monkeypatch, exc):
    def fail(_):
        raise exc
    monkeypatch.setattr(api, 'get_snapshot', fail)
    response = client.get('/api/allocation/snapshot')
    assert response.status_code == 500
    assert 'private' not in response.text.lower()


def test_existing_success_shape_unchanged(client, monkeypatch):
    snapshot = AllocationSnapshot(total_investable_assets=123.45,
        by_class={'cash': ClassAllocation(amount=123.45, ratio=1)})
    monkeypatch.setattr(api, 'get_snapshot', lambda _: snapshot)
    response = client.get('/api/allocation/snapshot')
    assert response.status_code == 200
    assert response.json() == snapshot.model_dump(mode='json')


def test_actual_active_without_asset_receipt_is_409(active, client):
    store, _, _ = active
    before = hashlib.sha256(store.path.read_bytes()).hexdigest()
    with bind(PolicySession(7)):
        response = client.get('/api/allocation/snapshot')
    assert response.status_code == 409
    assert response.json() == {'detail': 'POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED'}
    assert hashlib.sha256(store.path.read_bytes()).hexdigest() == before


def test_actual_unavailable_policy_is_503(active, client, monkeypatch, tmp_path):
    monkeypatch.setenv('WEALTHPILOT_POLICY_DB_PATH', str(tmp_path/'absent.sqlite'))
    response = client.get('/api/allocation/snapshot')
    assert response.status_code == 503
    assert response.json() == {'detail': 'POLICY_CONTEXT_UNAVAILABLE'}
    assert not (tmp_path/'absent.sqlite').exists()


def test_existing_qualified_active_success(active, client):
    # An already-bound session is an existing supported path, not new B2 wiring.
    pinned = session()
    neutral = pinned.context['snapshot']['allocation']['neutral']
    pinned.assets = pinned.assets.model_copy(update={
        'bucket_weights': {key: metric(value) for key, value in neutral.items()}})
    with bind(pinned):
        response = client.get('/api/allocation/snapshot')
    assert response.status_code == 200
    value = response.json()
    assert value['total_investable_assets'] == 100000
    assert set(value) == set(AllocationSnapshot.model_fields)
    assert sum(row['amount'] for row in value['by_class'].values()) == 100000


@pytest.mark.parametrize('change', [
    {'denominator_qualified': False}, {'pension_exclusion_verified': False},
    {'as_of': '2000-01-01T00:00:00+00:00'}, {'complete_inventory': False},
])
def test_incomplete_existing_session_never_becomes_success(active, client, change):
    pinned = session()
    pinned.assets = pinned.assets.model_copy(update=change)
    with bind(pinned):
        response = client.get('/api/allocation/snapshot')
    assert response.status_code == 409
    assert response.json() == {'detail': 'POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED'}
