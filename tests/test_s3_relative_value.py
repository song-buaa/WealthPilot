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


def test_reuse_age_uses_profile_review_clock_and_rejects_future():
    from datetime import timedelta
    from backend.services.s3_relative_value import require_review_clock

    cutoff = datetime(2026, 11, 27, tzinfo=timezone.utc)
    assert require_review_clock(cutoff - timedelta(days=30), cutoff) == cutoff - timedelta(days=30)
    with pytest.raises(ValueError, match='S3_REPRICE_REVIEW_WINDOW_EXPIRED'):
        require_review_clock(cutoff - timedelta(days=30, seconds=1), cutoff)
    with pytest.raises(ValueError, match='S3_REPRICE_REVIEW_CLOCK_UNQUALIFIED'):
        require_review_clock(cutoff + timedelta(seconds=1), cutoff)


def test_berkshire_uses_reported_operating_components_and_class_b_equivalents():
    from copy import deepcopy
    from backend.services.s3_berkshire_research import earnings_bridge, frozen_profile

    profile = frozen_profile()
    value = earnings_bridge(profile)
    assert value['basis'] == 'AS_REPORTED_NON_GAAP_OPERATING_EARNINGS_PER_B_EQUIVALENT'
    assert value['ttm_usd_millions'] == '47234'
    assert value['b_equivalent_shares'] == 2124519594
    assert D(value['earnings_per_b']).quantize(D('.01')) == D('22.23')
    bad = deepcopy(profile)
    bad['operating_millions']['other'][1] += 1
    with pytest.raises(ValueError, match='S3_BRK_OPERATING_EARNINGS_RECONCILIATION_FAILED'):
        earnings_bridge(bad)


def test_relative_stress_detects_reversal_and_leaves_company_base_intact(monkeypatch):
    from types import SimpleNamespace
    from backend.services import s3_relative_value as relative

    def card(symbol, value, stressed):
        return dict(symbol=symbol, group='继续观察', rank=1,
                    scenarios={'base':dict(annualized_return=value)},
                    joint_sensitivity=[dict(revenue_growth_shift='-.03', terminal_pe_shift='-.2',
                                            result=dict(annualized_return=stressed))])

    def rank(rows, _request):
        for i,row in enumerate(sorted(rows,key=lambda c:-D(c['scenarios']['base']['annualized_return'])),1):
            row['rank']=i
    monkeypatch.setattr(relative.engine,'_rank',rank)
    cards = [card('AAPL:US','.12','.01'),card('MSFT:US','.10','.08')]
    result = relative.relative_stress(cards, SimpleNamespace(tie_band=D('.01')))
    assert result['pairs'][0]['status'] == 'REVERSAL'
    assert all(any(c['scope']==scope for c in result['cells']) for scope in ('COMMON','AAPL:US','MSFT:US'))
    assert cards[0]['scenarios']['base']['annualized_return'] == '.12'


def test_rank_drops_parent_spread_if_issuer_is_no_longer_qualified(monkeypatch):
    from backend.services import s3_relative_value as relative

    monkeypatch.setattr(relative.engine,'_rank',lambda cards, request: None)
    card = dict(symbol='BRK.B:US', scenarios={}, relative_return=[{'against':'AAPL:US'}],
                failure_reason='S3_BRK_NEW_FILING_REQUIRES_RESEARCH')
    assert relative.rank_comparisons([card], None) == 0
    assert 'relative_return' not in card
    assert card['rank'] is None


def test_preferred_entitlement_endpoints_preserve_depositary_and_share_classes():
    from backend.services.s3_entitlement_evidence import preferred_bounds

    terms = dict(depositary_units=192500000, units_per_preferred=20, conversion_min='2.274',
                 conversion_max='2.842', liquidation_usd='1000', annual_rate='.0625',
                 common_class='C', size_basis='DISCLOSED')
    got = preferred_bounds(terms)
    assert got['underlying_preferred_shares'] == 9_625_000
    assert [D(v) for v in got['gross_conversion_share_endpoints']] == [D('21887250'), D('27354250')]
    assert D(got['annual_coupon_equivalent_usd']) == D('601562500')
    assert got['common_class'] == 'C'
    with pytest.raises(ValueError, match='DEPOSITARY_BASIS_REJECTED'):
        preferred_bounds(dict(terms, depositary_units=192500001))
    with pytest.raises(ValueError, match='NONFINITE_TERMS'):
        preferred_bounds(dict(terms, annual_rate='NaN'))


def test_entitlement_components_reproduce_ant_without_admitting_equity_valuation():
    from backend.services.s3_entitlement_evidence import qualification_components
    from backend.services.s3_relative_profiles import registry

    spec=registry()['companies']['ANT:US']['entitlement_evidence']
    with pytest.raises(ValueError, match='SOURCE_NOT_QUALIFIED'):
        qualification_components(spec, {'prospectus': {}})
    out=qualification_components(spec, {'prospectus': {}, 'listing': {}})
    assert out['status']=='COMPONENTS_ONLY_RETURN_QUALIFICATION_WITHHELD'
    assert out['derived']['accounting_pro_forma_a_plus_n']==12_519_801_314
    assert out['derived']['legal_pro_forma_a_plus_n']==13_366_642_274
    assert out['derived']['cfo_less_named_capital_cash_outflows_usd_millions']==3601
    assert not {'scenarios', 'conditional_price', 'rank'} & out.keys()
