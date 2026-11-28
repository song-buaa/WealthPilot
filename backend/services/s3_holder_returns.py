"""Dated per-listed-share cash and comparability for new S3 policy artifacts."""
from decimal import Decimal, localcontext

from backend.services.market_data.quality_valuation import dec, stamp, text

VERSION = 's3-holder-return.v1'
CODE = 'backend/services/s3_holder_returns.py'
FULL = 'FULL_CONDITIONAL'
BOUNDED = 'BOUNDED_CONDITIONAL'
UNKNOWN = 'UNKNOWN'


def fixed_annual_cash(anchor, start, end):
    """Freeze future research cash, independent of the next price refresh."""
    return [dict(eligibility_at=(start+(end-start)*year/5).isoformat(),
                 paid_at=(start+(end-start)*year/5).isoformat(),
                 per_share_usd=str(dec(anchor)*4),
                 basis='FLAT_ANNUAL_CASH_ASSUMPTION_FROM_LATEST_HISTORICAL_DECLARATION')
            for year in range(1,6)]


def settle(case, price, rate, start, end, cutoff, schedule):
    """Cash retained at zero interest. PV discounts each payment separately."""
    with localcontext() as ctx:
        ctx.prec = 34
        price, rate, terminal = map(dec, (price, rate, case['terminal_price']))
        if not start <= cutoff < end or min(terminal, price, rate) <= 0:
            raise ValueError('S3_HOLDER_RETURN_INPUT_REJECTED')
        years = lambda date: Decimal(5)*dec((date-cutoff).total_seconds())/dec((end-start).total_seconds())
        remaining = years(end)
        cash = pv_cash = Decimal(0)
        included = []
        for event in schedule:
            eligible, paid = stamp(event['eligibility_at']), stamp(event['paid_at'])
            amount = dec(event['per_share_usd'])
            if not eligible or not paid or not start < eligible <= paid <= end or amount < 0:
                raise ValueError('S3_HOLDER_DISTRIBUTION_REJECTED')
            if cutoff < eligible:
                cash += amount
                pv_cash += amount/(1+rate)**years(paid)
                included.append(event)
        wealth = terminal+cash
        value = terminal/(1+rate)**remaining+pv_cash
        terminal_hurdle = max(Decimal(0), price*(1+rate)**remaining-cash)
        out = dict(case, cash_distribution_method=VERSION, included_cash_distributions=included,
                   terminal_wealth=text(wealth), dividends_cash=text(cash),
                   total_return=text(wealth/price-1), annualized_return=text((wealth/price)**(1/remaining)-1),
                   conditional_value=text(value), margin_to_conditional_value=text(1-price/value),
                   required_terminal_price_after_cash=text(terminal_hurdle))
        # Original window hurdle scales from its zero-cash projection; do not
        # relabel the old EPS hurdle as a cash-inclusive result.
        old_growth = case.get('implied_revenue_cagr_for_required_return')
        if 'zero_cash_revenue_hurdle_at_origin' in case:
            old_growth = case['zero_cash_revenue_hurdle_at_origin']
        original_price = dec(case.get('cash_origin_price', price))
        original_rate = dec(case.get('cash_origin_rate', rate))
        out.update(cash_origin_price=str(original_price), cash_origin_rate=str(original_rate))
        if old_growth is not None:
            out['zero_cash_revenue_hurdle_at_origin'] = old_growth
            if terminal_hurdle:
                ratio = terminal_hurdle/(original_price*(1+original_rate)**5)
                out['implied_revenue_cagr_for_required_return'] = text((1+dec(old_growth))*ratio**(Decimal(1)/5)-1)
            else:
                out.pop('implied_revenue_cagr_for_required_return',None)
                out['implied_revenue_hurdle_status'] = 'CASH_ALONE_MEETS_TERMINAL_WEALTH_HURDLE'
        out.pop('implied_terminal_eps_no_dividends',None)
        out.pop('implied_earnings_growth_no_dividends',None)
        return out


def attach_cash(card, anchor, start, end):
    schedule = fixed_annual_cash(anchor['quarterly_usd'], start, end)
    price = card['cross_checks']['observed_price']['values']['observed_price']
    rate = card['assumptions']['required_return']
    card['cash_return_contract'] = dict(version=VERSION, anchor=anchor, schedule=schedule,
        currency='USD', unit='ONE_LISTED_COMMON_SHARE', taxes='PRE_TAX_NO_REINVESTMENT',
        start=start.isoformat(), end=end.isoformat())
    for name, case in card['scenarios'].items():
        card['scenarios'][name] = settle(case, price, rate, start, end, start, schedule)
    for item in [*card['sensitivity'],*card['joint_sensitivity']]:
        if item['result']:
            item['result'] = settle(item['result'],price,item.get('required_return',rate),start,end,start,schedule)
    values = sorted(
        (case['conditional_value'] for case in card['scenarios'].values()),key=dec)
    card['conditional_value_range'] = [values[0], values[-1]]
    card['assumptions']['rationale'] = ('PE projection keeps its own zero-payout setting. '
        'The separately modeled flat cash schedule determines holder ending wealth and payment-date PV.')
    card['formula'] += '; dated flat research cash distributions in ending wealth and payment-date PV'
    qualify_joint_sensitivity(card, rate)


def qualify_joint_sensitivity(card, rate):
    outcomes = card['joint_sensitivity']
    complete = len(outcomes) == 9 and all(v['result'] for v in outcomes)
    robust = complete and all(dec(v['result']['annualized_return']) >= dec(rate) for v in outcomes)
    card['joint_qualification'].update(complete=complete, all_above_required=robust)
    joint_codes = {'JOINT_STRESS_RETURN_THRESHOLD_CROSSING', 'JOINT_SENSITIVITY_INCOMPLETE'}
    blockers = [code for code in card.get('research_method_blockers', ()) if code not in joint_codes]
    if not robust:
        blockers.append('JOINT_STRESS_RETURN_THRESHOLD_CROSSING' if complete else 'JOINT_SENSITIVITY_INCOMPLETE')
    card['research_method_blockers'] = blockers


def scope(cards):
    """Return formal cohort; legacy cards retain their exact old ranking behavior."""
    return [card for card in cards if card.get('scenarios')
            and card.get('holder_return_scope', FULL) == FULL]


def counts(cards):
    return dict(fully_comparable_count=len(scope(cards)),
                bounded_count=sum(c.get('holder_return_scope')==BOUNDED
                                  for c in cards if c.get('scenarios')))
