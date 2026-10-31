"""Framework-free projections retain Web compatibility and sanitization."""
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from app.sync_status import SAFE_SYNC_CODES, safe_sync_reason, public_sync_attempt, _public_quality


@pytest.mark.parametrize('code', sorted(SAFE_SYNC_CODES))
def test_allowlisted_codes_remain_unchanged(code):
    assert safe_sync_reason(SimpleNamespace(status='failed', error_message=code)) == code


@pytest.mark.parametrize('status,error,expected', [
    ('failed', None, 'SYNC_SOURCE_FAILED'), ('success', None, None),
    ('running', '', None), ('failed', 'SDK token=PRIVATE_SENTINEL', 'SYNC_SOURCE_FAILED'),
    ('success', 'UNKNOWN_CODE', 'SYNC_SOURCE_FAILED'),
])
def test_safe_failure_projection(status, error, expected):
    assert safe_sync_reason(SimpleNamespace(status=status, error_message=error)) == expected
    assert safe_sync_reason(None) is None


def test_existing_api_imports_are_same_shared_functions():
    from backend.api import broker_sync
    assert broker_sync.SAFE_SYNC_CODES is SAFE_SYNC_CODES
    assert broker_sync.safe_sync_reason is safe_sync_reason
    assert broker_sync.public_sync_attempt is public_sync_attempt
    assert broker_sync._public_quality is _public_quality


def test_attempt_clock_and_legacy_cash_quality_are_preserved():
    stamp = datetime(2026, 1, 1)
    run = SimpleNamespace(id=1, status='failed', started_at=stamp, error_message='PRIVATE_SENTINEL',
        context_json=json.dumps({'source':'ibkr_account_download', 'cash_as_of':'2026-01-01',
                                'quality':'complete', 'issues':[]}))
    attempt = public_sync_attempt(run)
    assert attempt['last_attempt_time'] == stamp.replace(tzinfo=timezone.utc).isoformat()
    assert attempt['last_attempt_reason_code'] == 'SYNC_SOURCE_FAILED'
    quality = _public_quality(run)
    assert quality['cash_as_of'] is None and quality['quality'] == 'partial'
    assert quality['issues'] == ['LEGACY_IBKR_CASH_SOURCE_TIME_UNVERIFIED']
    assert public_sync_attempt(None)['last_attempt_status'] == 'never'
    assert _public_quality(None) == {'quality':'unavailable', 'issues':['NO_SUCCESSFUL_SYNC']}
