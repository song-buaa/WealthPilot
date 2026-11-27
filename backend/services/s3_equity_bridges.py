"""Issuer economic methods; all resulting returns use the existing holder settlement."""
from copy import deepcopy
from decimal import Decimal, localcontext

from backend.services.market_data.quality_valuation import dec, text
from backend.services.s3_holder_returns import attach_cash, settle

CODE = 'backend/services/s3_equity_bridges.py'
ALPHABET_METHOD = 'GOOG_CLASS_C_OPERATING_EV_BRIDGE_V1'


def evidence_by_digest(check):
    return {row['source_sha256']: row for row in check.get('evidence',[]) if row.get('source_sha256')}


def normalize_openai(card, profile, evidence):
    refs = [r['source_sha256'] for r in profile['adjusted_quarter_sources']]
    if not set([*refs,profile['annual_source_sha256']]) <= evidence.keys():
        return None
    from backend.services.s3_operating_research import Profile, _joint_qualify, project_card
    assumptions = Profile.model_validate(card['operating_assumptions'])
    baseline = deepcopy(card['operating_baseline'])
    observed = dec(baseline['flows']['net_income']['value'])
    figures = profile['openai_gaap_after_tax_usd_m']
    adjustment = (dec(figures['fy26'])-dec(figures['fy26_q1'])+dec(figures['fy27_q1']))*10**6
    adjusted = observed-adjustment
    eps = sum(dec(r['adjusted_eps_usd']) for r in profile['adjusted_quarter_sources'])
    if min(eps,adjusted) <= 0:
        return None
    adjusts = dict(values=dict(diluted_eps_ttm=str(eps)),
                   scope='ISSUER_RECONCILED_OPENAI_ONLY_NON_GAAP',source_refs=refs)
    baseline['eps'] = adjusts
    baseline['net_to_operating_conversion'] = str(adjusted/dec(baseline['flows']['operating_income']['value']))
    original_checks = card['cross_checks']
    model_input = dict(card,cross_checks=dict(original_checks,diluted_eps_ttm=adjusts))
    refreshed = project_card(model_input,baseline,assumptions)
    _joint_qualify(refreshed,baseline,assumptions)
    refreshed['cross_checks'] = original_checks
    refreshed['normalization_status'] = 'OPENAI_INVESTMENT_ONLY_ISSUER_RECONCILED'
    refreshed['formula'] = refreshed['formula'].replace('qualified_TTM_EPS','OpenAI_adjusted_TTM_EPS')
    refreshed['operating_baseline']['model_net_income_usd'] = str(adjusted)
    refreshed['nonoperating_normalization'] = dict(
        gaap_eps=original_checks['diluted_eps_ttm'], model_adjusted_eps=adjusts,
        reported_gaap_parent_income_usd=str(observed), adjusted_parent_income_usd=str(adjusted),
        openai_after_tax_contribution_usd=str(adjustment), source_evidence=[evidence[k] for k in refs],
        scope='OPENAI_INVESTMENT_GAINS_REMOVED; AZURE, COMMERCIAL_REVENUE_AND_ROYALTIES_RETAINED; OTHER_INVESTMENTS_NOT_RESTATED')
    card.update(refreshed)
    return True


def investment_materiality(card, profile, start, end, *, cutoff=None, price=None):
    """No issuer-wide value cap inferred from ownership or a financing valuation."""
    base = card['scenarios']['base']
    cutoff = cutoff or start
    price = dec(price if price is not None else card['cross_checks']['observed_price']['values']['observed_price'])
    rate = dec(card['assumptions']['required_return'])
    shares = dec(profile['reported_diluted_shares_m'])*dec(base['years'][-1]['relative_shares'])
    obligation = dec(profile['commitment_remaining_usd_m'])
    hurdle = dec(base['required_terminal_price_after_cash'])-dec(base['terminal_price'])
    cash_contract = card['cash_return_contract']
    rows = []
    for gross in profile['gross_reference_usd_m']:
        gross = dec(gross)
        for recovery in map(dec,('0','.6','1')):
            proceeds = gross*recovery-obligation
            amount = proceeds/shares
            case = dict(base,terminal_price=text(dec(base['terminal_price'])+amount))
            value = settle(case,price,rate,start,end,cutoff,cash_contract['schedule'])
            rows.append(dict(gross_reference_usd_m=str(gross),after_tax_and_transfer_recovery_fraction=str(recovery),
                             current_contract_funding_stress_usd_m=str(obligation),
                             net_terminal_per_share_usd=text(amount),annualized_return=value['annualized_return'],
                             meets_required_return=dec(value['annualized_return'])>=rate))
    switches = len({r['meets_required_return'] for r in rows}) > 1
    return dict(status='DECISION_THRESHOLD_SENSITIVE' if switches else 'NO_THRESHOLD_CROSSING_IN_TESTED_STRESS',
        obligation_adjusted_net_terminal_proceeds_needed_usd_m=text(hurdle*shares+obligation),
        bounded_economic_range=False, decision_threshold_crossed=switches, cases=rows,
        frozen_stress_inputs={key:profile[key] for key in ('reported_diluted_shares_m',
            'commitment_remaining_usd_m','gross_reference_usd_m','official_transaction_anchors')},
        checked_at=cutoff.isoformat(),
        terms='Funding reference amounts are historical transaction-scale stresses. Tax basis, transfer rights, preferences, timing and residual equity are not quantified.',
        reference_sources=profile['official_transaction_anchors'])


def alphabet_equity(card, research, claims, profile, sources, start, end):
    """Qualified raw close, independently reviewed operating EV and Class C claim."""
    quote = card.get('class_c_bridge_quote')
    if not quote or not claims or not profile['sources'].keys() <= sources.keys():
        return False
    b = {k:dec(v) for k,v in profile['quarter_balance_usd_m'].items()}
    base_shares = sum(dec(v) for v in research['reported_usd_m']['outstanding_common_millions'].values())
    card['scenarios'] = {}
    card['cross_checks'] = {'observed_price':dict(values={'observed_price':quote['price']},period=quote['price_date'],
                                                  currency='USD',source_receipt=quote)}
    card['price_qualification'] = dict(quote,eps_interval_start='2026-10-21')
    card['valuation_method'] = ALPHABET_METHOD
    card['assumptions'] = dict(required_return='.10',rationale='Scenario shares and retained assets disclosed separately')
    card['formula'] = '(operating EV + modeled nonoperating assets − notes − finance leases − NCI − preferred cash) / post-conversion ordinary shares'
    card['sensitivity'] = []
    card['joint_sensitivity'] = []
    card['joint_qualification'] = dict(complete=False,all_above_required=False)
    components = {}
    for name, values in profile['contract_states'].items():
        liquid, equities, private, retained, eur, cad, vwap, shares_change = map(dec,values)
        gross_new_debt = dec(profile['post_quarter_gross_m']['EUR'])*eur+dec(profile['post_quarter_gross_m']['CAD'])*cad
        # Non-debt operating leases are already within the EBIT convention.
        # Conversion replaces preference principal, so no dual subtraction.
        preferred_shares = sum(dec(next(r for r in rows
            if dec(r['hypothetical_class_vwap_usd'])==vwap)['unadjusted_common_shares'])
            for rows in claims['converted_shares_by_hypothetical_vwap'].values())/10**6
        preferred_cash = dec(profile['preferred_annual_cash_equivalent_m'])*Decimal('2.5')
        net_assets = b['cash_and_non_equity_marketable']*liquid+(b['spacex_restricted']+b['other_marketable_equities'])*equities+b['nonmarketable_equity']*private
        net_bridge = net_assets-b['interest_bearing_notes']-b['finance_leases']-b['nci']-gross_new_debt*(1-retained)-preferred_cash
        shares = base_shares*(1+shares_change)**5+preferred_shares
        ev = dec(research['cases'][name]['terminal_operating_ev_usd_m'])
        terminal = (ev+net_bridge)/shares
        if terminal <= 0:
            return False
        card['scenarios'][name] = dict(terminal_price=text(terminal))
        components[name] = dict(operating_ev_usd_m=text(ev),net_bridge_usd_m=text(net_bridge),
            forecast_shares_m=text(shares),new_debt_gross_usd_m=text(gross_new_debt),cash_retained_fraction=str(retained),
            preferred_cash_cost_usd_m=text(preferred_cash),preferred_converted_shares_m=text(preferred_shares))
    card['holder_equity_bridge'] = dict(status='CONDITIONAL_ORDINARY_VALUE_BOUNDED',
        observation_date='2026-09-30',financial_inputs=profile['quarter_balance_usd_m'],
        post_quarter_origin_currency=profile['post_quarter_gross_m'],
        scenario_parameter_columns=profile['scenario_columns'],scenario_parameters=profile['contract_states'],
        components=components,source_evidence=sources,
        limitations=profile['classification_limit'],
        assumptions='Operating EV absorbs operating reinvestment; annual ordinary dividends are not added to terminal cash. Preferred coupon-cash branch to 2029, unadjusted conversion, no assumed capped-call recovery, note retention and currency are conditional.')
    attach_cash(card,dict(quarterly_usd=profile['ordinary_quarterly_cash'],
        locator='10-Q October common dividend declaration for A/B/C',source=sources['quarter']),start,end)
    rate = dec(card['assumptions']['required_return'])
    base = card['scenarios']['base']
    base_ev,net,shares = (dec(components['base'][k]) for k in ('operating_ev_usd_m','net_bridge_usd_m','forecast_shares_m'))
    needed_ev = dec(base['required_terminal_price_after_cash'])*shares-net
    card['holder_equity_bridge']['base_required_operating_ev_usd_m']=text(needed_ev)
    contract=card['cash_return_contract']
    for variable,changes in (
            ('terminal_ev_multiple',('-.2','.2')),('net_equity_bridge_usd_m',('-75200','75200')),
            ('ordinary_shares',('-.03','.03')),('required_return',('-.02','.02'))):
        for raw in changes:
            change=dec(raw)
            factor=1+change if variable=='terminal_ev_multiple' else Decimal(1)
            bridge=net+change if variable=='net_equity_bridge_usd_m' else net
            share_case=shares*(1+change) if variable=='ordinary_shares' else shares
            required=rate+change if variable=='required_return' else rate
            price_case=(base_ev*factor+bridge)/share_case
            case=settle(dict(terminal_price=text(price_case)),quote['price'],required,start,end,start,contract['schedule'])
            card['sensitivity'].append(dict(parameter=variable,delta=raw,parameters={variable:raw},
                                             required_return=str(required),result=case))
    card['sensitivity_method']='GOOG_OPERATING_EV_AND_EQUITY_BRIDGE'
    card.pop('failure_reason',None)
    return True
