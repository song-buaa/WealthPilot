from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import json

from backend.services import s3_holder_returns as holder
from backend.services import s3_multimethod as multimethod
from backend.services import s3_relative_value as relative


def date(year, month=1):
    return datetime(year, month, 1, tzinfo=timezone.utc)


def test_purchase_after_eligibility_never_receives_still_unpaid_cash():
    start, end = date(2026), date(2031)
    schedule = [
        dict(eligibility_at=date(2027).isoformat(), paid_at=date(2027,2).isoformat(), per_share_usd='5'),
        dict(eligibility_at=date(2028).isoformat(), paid_at=date(2028).isoformat(), per_share_usd='3'),
    ]
    case = dict(terminal_price='120', implied_revenue_cagr_for_required_return='.10')
    first = holder.settle(case, '100', '.10', start, end, start, schedule)
    refresh = holder.settle(first, '101', '.10', start, end, date(2027), schedule)
    assert first['dividends_cash'] == '8.00000000'
    assert refresh['dividends_cash'] == '3.00000000'
    assert refresh['terminal_price'] == '120'
    assert refresh['terminal_wealth'] == '123.00000000'
    assert refresh['zero_cash_revenue_hurdle_at_origin'] == '.10'
    assert refresh['included_cash_distributions'] == schedule[1:]
    assert Decimal(refresh['conditional_value']) < Decimal(refresh['terminal_wealth'])


def test_no_future_cash_event_outside_fixed_window():
    start, end = date(2026), date(2031)
    bad = [dict(eligibility_at=date(2030).isoformat(), paid_at=date(2032).isoformat(), per_share_usd='1')]
    with pytest.raises(ValueError, match='DISTRIBUTION_REJECTED'):
        holder.settle(dict(terminal_price='100'), '100', '.10', start, end, start, bad)


def test_googs_enterprise_method_does_not_assign_listed_returns():
    sources = {
        'quarter':dict(source_sha256='b74e29c9e29e1287ddfb71da78577fcf7f1d26dd0aad599dbea9b778b465a6b1'),
        'preferred_terms':dict(source_sha256='0564227c40214d75adafe3247980d853f2baaeb04f48f18de073beafbcfb4ff3'),
    }
    card = dict(symbol='GOOG:US', scenarios={})
    multimethod.apply([card], {'GOOG:US':dict(sources=sources, reviewed_at=date(2026).isoformat())}, {}, date(2026),date(2031))
    assert card['holder_return_scope'] == holder.UNKNOWN
    assert card['scenarios'] == {} and 'ordinary' in card['failure_reason'].lower()
    assert len(card['enterprise_research']['cases']) == 3
    profile=json.loads((multimethod.ROOT/multimethod.BOOK).read_text())['GOOG:US']
    assert multimethod.alphabet_operating_cases({'sources': sources, 'reviewed_at':date(2025).isoformat()},
        profile, date(2026)) is None


def test_bounded_results_cannot_enter_formal_rank_or_pairs(monkeypatch):
    def rank(cards, _):
        assert [card['symbol'] for card in cards] == ['FULL:US']
        cards[0].update(rank=1,group='candidate')

    monkeypatch.setattr(relative.engine,'_rank',rank)
    cases = dict(base=dict(annualized_return='.2'), cautious=dict(total_return='.1'))
    cards = [dict(symbol='FULL:US', scenarios=cases, holder_return_scope=holder.FULL),
             dict(symbol='BOUNDED:US', scenarios=cases, holder_return_scope=holder.BOUNDED,
                  relative_return=['stale'], holder_return_scope_reason='OPERATING_ONLY')]
    assert relative.rank_comparisons(cards,SimpleNamespace(tie_band=Decimal('.01'))) == 1
    assert all(c['rank'] is None and not c.get('relative_return') for c in cards)
    assert relative.relative_stress(cards,None)['status'] == 'INSUFFICIENT_COMPARABLE_ISSUERS'


def test_claim_states_use_physical_forward_and_do_not_invent_common_weights():
    module = multimethod
    terms = dict(status='COMPONENTS_ONLY_RETURN_QUALIFICATION_WITHHELD',
        published_inputs={'forward_contracts_shares':[40_000_000,6_000_000]},
        evidence={'filing':{}}, derived={'preferred':{}})
    result = module.equity_components('META:US',{'qualification_components':terms})
    assert result['forward_reference_shares'] == 46_000_000
    assert [c['security_package'] for c in result['cases']] == ['A','A_PLUS_ONE_C_PER_ELIGIBLE_A','A']
    assert result['cases'][1]['weights'] == 'UNKNOWN_ECONOMIC_SPLIT'
    assert 'scenarios' not in result and 'ordinary_price' not in result


def test_observed_tesla_asset_overlay_does_not_change_base_projection():
    profiles = json.loads((multimethod.ROOT/multimethod.BOOK).read_text())
    anchor = profiles['TSLA:US']
    source = dict(quarter=dict(source_sha256=anchor['sources']['quarter']))
    card = dict(cross_checks=dict(observed_price=dict(values=dict(observed_price='100'))),
                scenarios=dict(base=dict(terminal_wealth='105', years=[dict(relative_shares='1.05')])))
    results = multimethod.tesla_investment_stress(card,
        dict(sources=source, reviewed_at=date(2026).isoformat()),anchor,date(2026))
    assert results['cases'][0]['terminal_increment_per_share_usd']=='0.00000000'
    assert 0 < Decimal(results['cases'][2]['terminal_increment_per_share_usd']) < 1
    assert card['scenarios']['base']['terminal_wealth'] == '105'
