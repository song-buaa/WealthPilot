from datetime import datetime, timezone
from decimal import Decimal as D

import pytest

from backend.services.market_data.quality_valuation import scenario
from backend.services.s3_relative_value import endpoint, policy_pool, reprice_case
from backend.services.s3_research_entry import CompanyPoolRequest
from tests.test_policy_v3_targets import AMENDMENT_APPROVAL, parse, raw_amendment


def test_active_scope_uses_policy_issuer_map_and_preserves_explicit_contract():
    snapshot = parse(raw_amendment(AMENDMENT_APPROVAL)).model_dump(mode='json')
    active = dict(status='ACTIVE', snapshot=snapshot, policy_reference={'bundle_id': 'fixture'},
                  effective_at='2026-11-27T00:00:00Z')
    pool = policy_pool(active)
    rows = list(pool['issuers'].values())
    assert len(rows) == 8
    alphabet = next(row for row in rows if row['symbol'] == 'GOOG:US')
    assert alphabet['classes'] == ['GOOG', 'GOOGL']
    assert next(row for row in rows if row['symbol'] == 'ANT:US')['target'] == '0.01'
    assert CompanyPoolRequest(symbols=['AAPL'], purpose='explicit').method == 'standard'
    assert CompanyPoolRequest(scope='active_us_direct', method='professional', purpose='policy').symbols is None
    with pytest.raises(ValueError, match='S3_ACTIVE_SCOPE_REQUIRES'):
        CompanyPoolRequest(scope='active_us_direct', symbols=['AAPL'], method='professional', purpose='policy')
    with pytest.raises(ValueError, match='S3_ACTIVE_POLICY_REQUIRED'):
        policy_pool({'status': 'UNAVAILABLE'})


def test_fixed_endpoint_and_price_change_do_not_change_terminal_wealth():
    old = scenario('10', '100', dict(earnings_growth='0', annual_share_change='0',
                                     terminal_pe='20', dividend_payout='0'), '.1')
    assert reprice_case(old, D(100), D(100), D(5), D('.1')) == old
    new = reprice_case(old, D(100), D(125), D(4), D('.1'))
    assert D(new['total_return']) == D('.6')
    assert new['terminal_wealth'] == old['terminal_wealth']
    assert abs(D(new['annualized_return']) - (D('1.6') ** D('.25')-1)) < D('.00000001')
    assert D(new['implied_terminal_eps_no_dividends']) == D('9.15062500')
    assert endpoint(datetime(2024, 2, 29, tzinfo=timezone.utc)) == datetime(2029, 2, 28, tzinfo=timezone.utc)


def test_independent_verifier_rejects_optimized_python():
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / 'scripts/s3_relative_value_verify.py'
    result = subprocess.run([sys.executable, '-O', str(script), '/nonexistent'], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'OPTIMIZED_PYTHON_UNSAFE_FOR_VERIFICATION' in result.stderr


@pytest.mark.parametrize(('day', 'acceptance', 'error'), [
    ('bad date', '2026-11-26T16:00:00Z', 'S3_REPRICE_FILING_CLOCK_UNQUALIFIED'),
    ('2026-11-26', None, 'S3_REPRICE_FILING_CLOCK_UNQUALIFIED'),
    ('2026-11-26', '2026-11-26T16:00:00Z', 'S3_REPRICE_NEW_FILING_REQUIRES_RESEARCH'),
    ('2026-11-27', '2026-11-27T16:00:00Z', 'S3_REPRICE_NEW_FILING_REQUIRES_RESEARCH'),
    ('2026-11-26', '2026-11-26T14:00:00Z', None),
])
def test_recent_filing_clock_fails_closed(day, acceptance, error):
    from backend.services.s3_relative_value import check_recent_filings

    recent = {'accessionNumber': ['a'], 'filingDate': [day], 'acceptanceDateTime': [acceptance]}
    parent_at = datetime(2026, 11, 26, 15, tzinfo=timezone.utc)
    if error:
        with pytest.raises(ValueError, match=error):
            check_recent_filings(recent, parent_at)
    else:
        check_recent_filings(recent, parent_at)


def test_independent_verifier_keeps_card_with_no_quality_evidence(monkeypatch, tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / 'scripts/s3_relative_value_verify.py'
    spec = spec_from_file_location('s3_relative_value_verify', script)
    verifier = module_from_spec(spec)
    spec.loader.exec_module(verifier)

    parent = {'result': {'decision_at': '2026-11-03T10:00:00+00:00',
                         'cards': [{'symbol': 'UNKNOWN:US', 'scenarios': {}, 'rank': None}]}}
    child = {'parent_artifact_id': 'parent', 'parent_result_sha256': verifier.digest(parent['result']),
             'result': dict(parent['result'], original_decision_at=parent['result']['decision_at'],
                            forecast_endpoint='2031-11-03T10:00:00+00:00',
                            method_version='s3-relative-value.fixed-endpoint.v1', remaining_years='5')}
    monkeypatch.setattr(verifier, 'load', lambda path: parent if path.parent.name == 'parent' else child)
    assert verifier.verify(tmp_path / 'child' / 'bundle.json')['status'] == 'NO_QUALIFIED_CASES'
