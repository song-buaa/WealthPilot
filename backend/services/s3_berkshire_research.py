"""Berkshire Class B research using issuer-reconciled operating earnings, not GAAP EPS."""
from datetime import timedelta
from decimal import Decimal, localcontext
import json

from backend.services.market_data import quality_valuation as engine
from backend.services.s3_method_research import identity_projection
from backend.services.s3_relative_value import check_recent_filings, listed_close, require_review_clock, source
from research_v2 import sec_disclosures as sec

METHOD = 'BRK_CLASS_B_OPERATING_EARNINGS_V1'
CODE = 'backend/services/s3_berkshire_research.py'


def frozen_profile():
    from backend.services.s3_relative_profiles import registry

    return registry()['berkshire_operating']


def check_identity(symbol, directory, submissions, profile, cutoff):
    issuer, scope, _ = identity_projection(symbol, directory, submissions)
    if (symbol != 'BRK.B:US' or issuer['cik'] != '0001067983' or scope['sic'] != '6331'
            or issuer['sec_ticker'] != 'BRK-B' or sorted(issuer['registered_tickers']) != ['BRK-A','BRK-B']):
        raise ValueError('S3_BRK_CLASS_B_IDENTITY_REJECTED')
    recent = json.loads(submissions[0])
    filings = sec.discover_filings(issuer, recent)
    latest = {form: next((f['accession'] for f in filings if f['form'] == form), None)
              for form in ('10-K','10-Q','8-K')}
    if latest != profile['latest_reviewed_filings']:
        raise ValueError('S3_BRK_NEW_FILING_REQUIRES_RESEARCH')
    check_recent_filings(recent['filings']['recent'], require_review_clock(profile['reviewed_at'],cutoff))


def earnings_bridge(profile):
    """Coherent FY plus YTD change for the six issuer-defined operating categories."""
    components = {name: str(values[0]+values[1]-values[2])
                  for name,values in profile['operating_millions'].items()}
    if any(sum(v[i] for v in profile['operating_millions'].values()) != total
           for i,total in enumerate(profile['reported_totals_millions'])):
        raise ValueError('S3_BRK_OPERATING_EARNINGS_RECONCILIATION_FAILED')
    total = sum(Decimal(value) for value in components.values())
    divisor = profile['class_a_outstanding']*1500 + profile['class_b_outstanding']
    if min(total,divisor) <= 0:
        raise ValueError('S3_BRK_NONPOSITIVE_EARNINGS_OR_SHARES')
    return dict(method=METHOD, components_usd_millions=components, ttm_usd_millions=str(total),
                b_equivalent_shares=divisor, earnings_per_b=str(total*1000000/divisor),
                shares_as_of=profile['shares_as_of'], basis='AS_REPORTED_NON_GAAP_OPERATING_EARNINGS_PER_B_EQUIVALENT')


def _case_parameters(bridge, definition):
    components = bridge['components_usd_millions']
    terminal = sum(engine.dec(value)*(1+engine.dec(definition['growth'][key]))**5
                   for key,value in components.items())
    rate = (terminal / engine.dec(bridge['ttm_usd_millions']))**(Decimal(1)/5)-1
    return dict(earnings_growth=str(rate), annual_share_change=definition['annual_share_change'],
                terminal_pe=definition['multiple'],dividend_payout='0')


def build(card, root, manifest, cutoff, quality):
    cutoff = engine.stamp(cutoff)
    profile = frozen_profile()
    if not {'quarter','annual_earnings','quarter_earnings','financing'} <= quality.get('sources',{}).keys():
        raise ValueError('S3_BRK_OPERATING_SOURCE_PROOFS_MISSING')
    entry = manifest['companies']['BRK.B:US']
    earliest = cutoff-timedelta(days=2)
    directory=source(root,entry['directory'],sec.DIRECTORY,earliest,cutoff)
    sub=source(root,entry['submissions'],'https://data.sec.gov/submissions/CIK0001067983.json',earliest,cutoff)
    check_identity(card['symbol'],directory,sub,profile,cutoff)
    quote = listed_close(root,entry,card['symbol'],profile['shares_as_of'],earliest,cutoff)
    with localcontext() as ctx:
        ctx.prec=34
        bridge = earnings_bridge(profile)
        rate = Decimal('.10')
        base = bridge['earnings_per_b']; price = quote['price']
        params = {k:_case_parameters(bridge,x) for k,x in profile['scenarios'].items()}
        scenarios = {k:engine.scenario(base,price,v,rate) for k,v in params.items()}
        sensitivity = []
        for field,offsets in engine.SENSITIVITY_OFFSETS:
            for delta in offsets:
                values = dict(params['base']); hurdle = rate
                if field == 'required_return':
                    hurdle += delta
                elif field == 'terminal_pe':
                    values[field] = str(engine.dec(values[field])*(1+delta))
                else:
                    values[field] = str(engine.dec(values[field])+delta)
                sensitivity.append(dict(parameter=field,delta=str(delta),required_return=str(hurdle),parameters=values,
                                        result=engine.scenario(base,price,values,hurdle)))
        joint = []
        for shift in ('-.03','0','.03'):
            for pe_shift in ('-.2','0','.2'):
                values = dict(params['base'], earnings_growth=str(engine.dec(params['base']['earnings_growth'])+Decimal(shift)),
                              terminal_pe=str(engine.dec(params['base']['terminal_pe'])*(1+Decimal(pe_shift))))
                joint.append(dict(stress_axis='OPERATING_EARNINGS_PE',revenue_growth_shift=shift,
                                  terminal_pe_shift=pe_shift,result=engine.scenario(base,price,values,rate)))
        robust = all(engine.dec(x['result']['annualized_return']) >= rate for x in joint)
        naive = sorted(engine.dec(x['conditional_value']) for x in scenarios.values())
        result = dict(card)
        result.pop('failure_reason',None)
        result.pop('historical_benchmark_ref',None)
        result.update(valuation_method=METHOD,comparison_status='COMPARABLE',valuation_status='CONDITIONAL_RESEARCH',
            operating_earnings_bridge=bridge, alternate_operating_profile=profile,
            current_operating_earnings_multiple=engine.text(engine.dec(price)/engine.dec(base)),
            current_price_evidence=quote, price_qualification=dict(quote,eps_interval_start=profile['shares_as_of']),
            cross_checks=dict(observed_price=dict(values=dict(observed_price=price),currency='USD',period=quote['price_date'])),
            scenarios=scenarios, sensitivity=sensitivity, joint_sensitivity=joint,
            joint_qualification=dict(complete=True,all_above_required=robust,scope='OPERATING_EARNINGS_GROWTH_X_EXIT_MULTIPLE'),
            assumptions=dict(required_return=str(rate),**params),
            conditional_value_range=[engine.text(naive[0]),engine.text(naive[-1])],
            quality=dict(floors={},warnings=['NONFINANCIAL_SCREENS_NOT_APPLICABLE'],
                         priority_blockers=['INSURANCE_QUALITY_FLOORS_NOT_FORMALLY_QUALIFIED']),
            research_method_blockers=[] if robust else ['JOINT_STRESS_RETURN_THRESHOLD_CROSSING'],
            change_conditions=profile['risks'], formula='Class B-equivalent terminal adjusted operating earnings × exit multiple; no separate NAV, cash or float additions',
            limitations=['ISSUER_NON_GAAP_OPERATING_EARNINGS_NOT_GAAP_EPS',
                         'INSURANCE_INVESTMENT_INCOME_ALREADY_CAPITALIZED_NO_SEPARATE_NAV', 'NO_INTERIM_CASH_DISTRIBUTIONS'])
        return result


ASSET_METHOD = 'BRK_CLASS_B_SEGMENT_AND_ASSET_VALUE_V1'


def segment_asset_case(bridge, facts, setup, *, growth_shift=Decimal(0), multiple_shift=Decimal(0)):
    """Non-overlapping operating equity value and supporting financial assets."""
    d = engine.dec
    b = {key: d(value) for key, value in facts.items()}
    components = bridge['components_usd_millions']
    segments = {}
    for key, (growth, multiple) in setup['operating'].items():
        earnings = d(components[key]) * (1 + d(growth) + growth_shift) ** 5
        segments[key] = earnings * d(multiple) * (1 + multiple_shift)
    # Neither the insurer's interest/dividends nor consolidated "Other" is
    # capitalized: the corresponding investments and claims are valued below.
    securities = b['public_equity'] * d(setup['market_recovery'])
    tax = max(Decimal(0), securities - b['public_equity_cost']) * d(setup['realization_tax'])
    securities_net = securities - tax
    insurance_public = securities_net * b['insurance_public_equity'] / b['public_equity']
    cash_growth = (1 + d(setup['treasury_reinvestment'])) ** 5
    insurer_cash = b['insurer_cash_treasury'] - b['insurer_unsettled']
    insurer_net = (insurer_cash * cash_growth + insurance_public
                   + b['insurance_fixed_maturity'] * d(setup['fixed_income_recovery'])
                   - b['float_net_of_insurance_receivables'] * d(setup['float_claim_factor']))
    # Capital friction applies only to positive net invested assets, not to claims.
    regulatory = max(Decimal(0), insurer_net) * d(setup['insurance_capital_discount'])
    consolidated_cash = b['insurance_and_other_cash_treasury'] - b['group_unsettled']
    outside_insurer = consolidated_cash - insurer_cash
    outside_reserve = outside_insurer * (1 - d(setup['outside_surplus_fraction']))
    yen_debt = b['new_parent_jpy_notes_m'] * d(setup['usd_per_jpy'])
    parent_cash_net = ((outside_insurer - outside_reserve) * cash_growth - b['parent_debt']
                       - yen_debt * (1 - d(setup['yen_proceeds_retention'])))
    outside_investments = (securities_net - insurance_public
                           + (b['group_fixed_maturity'] - b['insurance_fixed_maturity']) * d(setup['fixed_income_recovery'])
                           + b['listed_equity_method_fair_value'] * d(setup['equity_method_recovery'])
                           + b['other_equity_method_carry'] * d(setup['equity_method_recovery']))
    corporate = b['parent_cost'] * d(setup['parent_cost_multiple'])
    total = sum(segments.values()) + insurer_net - regulatory + parent_cash_net + outside_investments - corporate
    shares = d(bridge['b_equivalent_shares']) * (1 + d(setup['annual_share_change'])) ** 5
    components = dict(operating_values_usd_m={k: engine.text(v) for k, v in segments.items()},
        insurance_net_invested_assets_usd_m=engine.text(insurer_net), insurance_capital_charge_usd_m=engine.text(regulatory),
        outside_insurer_cash_reserve_usd_m=engine.text(outside_reserve), parent_and_other_cash_net_usd_m=engine.text(parent_cash_net),
        outside_insurer_investments_usd_m=engine.text(outside_investments), corporate_cost_usd_m=engine.text(corporate),
        terminal_equity_usd_m=engine.text(total), terminal_class_b_shares=str(shares))
    return dict(terminal_price=engine.text(total * 10**6 / shares), **components)


def add_asset_scope(card, profile, sources, start, end):
    from backend.services.s3_holder_returns import qualify_joint_sensitivity, settle

    if card.get('valuation_method') != METHOD:
        return False
    basis = card['operating_earnings_bridge']
    quote = card['cross_checks']['observed_price']['values']['observed_price']
    rate = card['assumptions']['required_return']
    facts, cases = profile['financial_millions'], profile['cases']
    with localcontext() as ctx:
        ctx.prec = 34
        def valued(setup, **stress):
            return settle(segment_asset_case(basis, facts, setup, **stress), quote, rate, start, end, start, [])

        scenarios = {name: valued(case) for name, case in cases.items()}
        joint = [dict(stress_axis='OPERATING_EARNINGS_PE', revenue_growth_shift=g, terminal_pe_shift=m,
                      result=valued(cases['base'], growth_shift=Decimal(g), multiple_shift=Decimal(m)))
                 for g in ('-.03','0','.03') for m in ('-.2','0','.2')]
        sensitivity = []
        for key, shifts in (('insurance_capital_discount',('-.10','.10')),
                            ('market_recovery',('-.15','.15')),
                            ('outside_surplus_fraction',('-.10','.10')),
                            ('float_claim_factor',('-.05','.05'))):
            base = cases['base']
            for shift in shifts:
                values = dict(base, **{key: str(engine.dec(base[key])+Decimal(shift))})
                sensitivity.append(dict(parameter=key, delta=shift, required_return=rate, result=valued(values)))
        card.update(valuation_method=ASSET_METHOD, previous_operating_scenarios=card['scenarios'],
                    scenarios=scenarios, joint_sensitivity=joint, sensitivity=sensitivity,
                    sensitivity_method='BRK_SEGMENT_ASSET_RISK', conditional_value_range=sorted(
                        (x['conditional_value'] for x in scenarios.values()), key=engine.dec)[::2],
                    formula='After-tax segment value plus separate risk-adjusted investments net of claims and parent debt per Class B',
                    cash_return_contract=dict(start=start.isoformat(), end=end.isoformat(), schedule=[],
                                              taxes='NO_INTERIM_DISTRIBUTIONS_ASSUMED'),
                    holder_equity_bridge=dict(status='MODELED_CONDITIONAL_ORDINARY_SCOPE',
                        source_evidence=sources, definition=profile))
        qualify_joint_sensitivity(card, rate)
    return True
