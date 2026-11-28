from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal

from backend.services import s3_tesla_optionality as tesla


def _core_card():
    return dict(
        scenarios={name: dict(terminal_wealth='45',
                              years=[dict(revenue_index='1.1', relative_shares='1.10')] * 5)
                   for name in tesla.SCENARIOS},
        nonoperating_asset_stress=dict(reported=dict(outstanding_common_shares='3900000000',
                                                    spacex_carrying_usd='2800000000')),
        cross_checks=dict(observed_price=dict(values=dict(observed_price='400'))),
        holder_return_scope='BOUNDED_CONDITIONAL',
        rank=None,
    )


def _dates():
    return (datetime(2026, 11, 28, tzinfo=timezone.utc), datetime(2031, 11, 28, tzinfo=timezone.utc))


def test_positive_tesla_requires_qualified_exhibit_and_preserves_operating_scope():
    card = _core_card()
    old = deepcopy(card)
    tesla.build(card, {}, *_dates())
    assert card == old

    proof = dict(q3_operating_update=dict(accession=tesla.OPERATING_ACCESSION))
    tesla.build(card, proof, *_dates())
    out = card.pop('positive_optional_research')
    assert card == old
    assert out['status'] == 'BOUNDED_COMMERCIAL_OPTION_SCENARIOS'
    assert out['scenarios']['cautious']['path'][0]['incremental_fsd_revenue_usd'] == '0.00000000'
    assert Decimal(out['scenarios']['optimistic']['incremental_terminal_equity_usd']) > 0


def test_positive_tesla_preserves_frozen_terminal_on_price_only_recompute():
    card = _core_card()
    tesla.build(card, dict(q3_operating_update=dict(accession=tesla.OPERATING_ACCESSION)), *_dates())
    before = deepcopy(card['positive_optional_research'])
    tesla.reprice(card, '390', '4')
    current = card['positive_optional_research']
    assert current['forecast_endpoint'] == before['forecast_endpoint']
    for name, row in current['scenarios'].items():
        assert row['terminal_per_share_usd'] == before['scenarios'][name]['terminal_per_share_usd']
        assert row['annualized_return'] != before['scenarios'][name]['annualized_return']
        expected = (Decimal(row['terminal_per_share_usd']) / 390)**Decimal('.25') - 1
        assert abs(expected - Decimal(row['annualized_return'])) < Decimal('1e-8')
