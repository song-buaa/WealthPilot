"""Issuer economic methods; all resulting returns use the existing holder settlement."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext

from backend.services.market_data.quality_valuation import dec, text
from backend.services.s3_holder_returns import attach_cash, settle

CODE = 'backend/services/s3_equity_bridges.py'
ALPHABET_METHOD = 'GOOG_CLASS_C_OPERATING_EV_BRIDGE_V2'


def evidence_by_digest(check):
    return {row['source_sha256']: row for row in check.get('evidence',[]) if row.get('source_sha256')}


def normalize_openai(card, profile, evidence):
    refs = [r['source_sha256'] for r in profile['adjusted_quarter_sources']]
    if not set([*refs,profile['annual_source_sha256']]) <= evidence.keys():
        return None
    from backend.services.s3_operating_research import Profile, _joint_qualify, project_card
    assumptions = Profile.model_validate(card['operating_assumptions'])
    baseline = deepcopy(card['operating_baseline'])
    if (baseline['start'],baseline['period']) != tuple(profile['window']):
        return None
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
        openai_after_tax_contribution_usd=str(adjustment), source_evidence=[evidence[k] for k in [*refs,profile['annual_source_sha256']]],
        model_window=profile['window'],
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
    combined = {}
    for name, values in profile.get('exit_cases',{}).items():
        investee_value,retention,realization,aftertax,royalty_runoff = map(dec,values)
        old = card['scenarios'][name]
        scenario_shares = dec(profile['reported_diluted_shares_m'])*dec(old['years'][-1]['relative_shares'])
        net_exit = investee_value*dec(profile['as_converted'])*retention*realization*aftertax-obligation
        multiple = dec(card['operating_assumptions'][name]['terminal_pe'])
        # Use a scenario tax proxy for the royalty, separate from investment exit tax.
        royalty_terminal = royalty_runoff*Decimal('.76')*multiple
        delta_per_share = (net_exit-royalty_terminal)/scenario_shares
        case = dict(old,terminal_price=text(dec(old['terminal_price'])+delta_per_share))
        value = settle(case,price,rate,start,end,cutoff,cash_contract['schedule'])
        combined[name] = dict(annualized_return=value['annualized_return'],total_return=value['total_return'],
            terminal_wealth=value['terminal_wealth'], terminal_price=value['terminal_price'],
            net_exit_usd_m=text(net_exit), royalty_runoff_terminal_usd_m=text(royalty_terminal),
            assumption_columns=['future_investee_equity_usd_m','retention_of_current_as_converted_share',
                'private_claim_realizability','exit_after_tax_retention','pretax_annual_royalty_runoff_usd_m'],
            assumptions=values)
    return dict(status='DECISION_THRESHOLD_SENSITIVE' if switches else 'NO_THRESHOLD_CROSSING_IN_TESTED_STRESS',
        obligation_adjusted_net_terminal_proceeds_needed_usd_m=text(hurdle*shares+obligation),
        bounded_economic_range=False, decision_threshold_crossed=switches, cases=rows,
        tested_scope='BASE_ANNUALIZED_RETURN_HURDLE_ONLY',
        relative_comparison_sensitive=None,cautious_downside_sensitive=None,
        combined_conditional_exit_cases=combined,
        exit_case_limit=profile.get('exit_rationale'),
        frozen_stress_inputs={key: profile[key] for key in (
            'reported_diluted_shares_m', 'commitment_remaining_usd_m', 'gross_reference_usd_m',
            'official_transaction_anchors', 'exit_cases', 'exit_rationale', 'as_converted') if key in profile},
        checked_at=cutoff.isoformat(),
        terms='Funding reference amounts are historical transaction-scale stresses. 204,480m is 24% times the investee historical 852,000m funding scale; it is neither a cap nor distributable value. Tax basis, transfer rights, preferences, timing and residual equity are not quantified.',
        reference_sources=profile['official_transaction_anchors'])


def tesla_reverse_value(card, price, remaining):
    """Invert the price and 10% hurdle; never capitalize speculative businesses."""
    stress = card['nonoperating_asset_stress']
    profile = stress['reported']
    price, remaining = dec(price), dec(remaining)
    baseline = card['scenarios']['base']
    # In the accepted operating model, the GAAP EPS starting point is divided
    # by the matched GAAP net-to-EBIT reference ratio. The terminal conversion
    # excludes repeat gains; a second proportional EPS haircut would double-strip.
    operating = dec(baseline['terminal_wealth'])
    diluted = dec(profile['outstanding_common_shares']) * dec(baseline['years'][-1]['relative_shares'])
    investment = dec(profile['spacex_carrying_usd']) * dec(profile['reverse_net_recovery']) / diluted
    hurdle = dec(card['assumptions']['required_return'])
    wealth_target = price * (1 + hurdle) ** remaining
    residual = max(Decimal(0), wealth_target - operating - investment)
    rows = []
    for multiple, margin, capital_turnover in profile['reverse_economics']:
        multiple, margin, capital_turnover = map(dec, (multiple, margin, capital_turnover))
        earnings = residual * diluted / multiple
        revenue = earnings / margin
        rows.append(dict(exit_pe=str(multiple), after_tax_margin=str(margin),
                         revenue_to_invested_capital=str(capital_turnover),
                         implied_net_income_usd=text(earnings), implied_sales_usd=text(revenue),
                         illustrative_invested_capital_usd=text(revenue / capital_turnover)))
    return dict(status='REVERSE_PRICE_REQUIREMENT_ONLY', required_return=str(hurdle), basis_price_usd=text(price),
                remaining_years=str(remaining), operating_terminal_per_share=text(operating),
                investment_terminal_per_share=text(investment),
                unexplained_current_price_usd=text(price - (operating + investment) / (1 + hurdle) ** remaining),
                required_other_terminal_per_share_usd=text(residual),
                required_other_terminal_equity_usd=text(residual * diluted),
                scenarios=rows,
                limitation='Residual includes anything outside the specified operating scenario. It is not attributed wholly to autonomous driving or robotics. Capital scales are illustrative stock of operating capital, not funded cash flows or proof of feasibility.')


def alphabet_option_overlay(vwap, overlay, state, coupon):
    """Apply cash-or-stock election once and keep conditional funding assets and claims paired."""
    atm_fraction, atm_keep, waymo_draw, waymo_keep, stock_coupon = map(dec,state[:5])
    settlement = state[5]
    atm_size = dec(overlay['unused_atm_authority_usd_m'])*atm_fraction
    waymo = dec(overlay['waymo_facility_usd_m'])*waymo_draw
    gross_hedge = sum(dec(units)*max(Decimal(0),min(vwap,dec(cap))-dec(strike))
                      for units,strike,cap in overlay['capped_calls'].values())/10**6
    cash_hedge = gross_hedge if settlement=='CASH' else Decimal(0)
    share_hedge = gross_hedge/vwap if settlement=='NET_SHARES' else Decimal(0)
    dividend_share_price = vwap * Decimal('.97')
    # The current cases price above both series' floors. A lower scenario needs the
    # contractual per-series share caps and cash shortfall rather than this proxy.
    if stock_coupon and dividend_share_price < Decimal('124.29'):
        raise ValueError('GOOG_STOCK_DIVIDEND_BELOW_CONTRACT_CASE_RANGE')
    return dict(atm_gross=atm_size,atm_net_retained=atm_size*atm_keep,atm_shares=atm_size/vwap,
        waymo_draw=waymo,waymo_net_claim=waymo*(1-waymo_keep),coupon_cash=coupon*(1-stock_coupon),
        coupon_shares=coupon*stock_coupon/dividend_share_price,hedge_cash=cash_hedge,hedge_shares=share_hedge)


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
        overlay = alphabet_option_overlay(vwap,profile['overlay'],profile['overlay']['states'][name],preferred_cash)
        net_assets = b['cash_and_non_equity_marketable']*liquid+(b['spacex_restricted']+b['other_marketable_equities'])*equities+b['nonmarketable_equity']*private
        net_bridge = net_assets-b['interest_bearing_notes']-b['finance_leases']-b['nci']-gross_new_debt*(1-retained)-overlay['coupon_cash']
        net_bridge += overlay['atm_net_retained']+overlay['hedge_cash']-overlay['waymo_net_claim']
        shares = (base_shares*(1+shares_change)**5+preferred_shares+overlay['atm_shares']
                  +overlay['coupon_shares']-overlay['hedge_shares'])
        ev = dec(research['cases'][name]['terminal_operating_ev_usd_m'])
        terminal = (ev+net_bridge)/shares
        if terminal <= 0:
            return False
        card['scenarios'][name] = dict(terminal_price=text(terminal))
        components[name] = dict(operating_ev_usd_m=text(ev),net_bridge_usd_m=text(net_bridge),
            forecast_shares_m=text(shares),new_debt_gross_usd_m=text(gross_new_debt),cash_retained_fraction=str(retained),
            preferred_cash_cost_usd_m=text(overlay['coupon_cash']),preferred_converted_shares_m=text(preferred_shares),
            scenario_overlay={key:text(value) for key,value in overlay.items()})
    card['holder_equity_bridge'] = dict(status='MODELED_CONDITIONAL_ORDINARY_SCOPE',
        observation_date='2026-09-30',
        ordinary_share_basis='October 21 cover; A/B/C liquidation and common dividend rights are identical except voting under the reviewed 10-Q.',
        financial_inputs=profile['quarter_balance_usd_m'],
        post_quarter_origin_currency=profile['post_quarter_gross_m'],
        scenario_parameter_columns=profile['scenario_columns'],scenario_parameters=profile['contract_states'],
        components=components,source_evidence=sources,contract_overlay=profile['overlay'],
        limitations=profile['classification_limit'],
        assumptions='Operating EV absorbs operating reinvestment; annual ordinary dividends are not added to terminal cash. Conditional conversion, cash/stock coupons and capped-call election to 2029. Ordinary share drift excludes the explicitly modeled ATM/convertibles. Waymo debit is consolidated and nonrecourse to the parent; announced credit capacity is not observed debt draw.')
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
    for growth_delta in ('-.03','0','.03'):
        for multiple_delta in ('-.2','0','.2'):
            growth = dec(research['cases']['base']['revenue_cagr'])
            ev = base_ev*((1+growth+dec(growth_delta))/(1+growth))**5*(1+dec(multiple_delta))
            case = settle(dict(terminal_price=text((ev+net)/shares)),quote['price'],rate,start,end,start,contract['schedule'])
            card['joint_sensitivity'].append(dict(stress_axis='REVENUE_EV',
                revenue_growth_shift=growth_delta,terminal_pe_shift=multiple_delta,result=case))
    card['joint_qualification'].update(
        complete=True, all_above_required=all(
            dec(item['result']['annualized_return']) >= rate for item in card['joint_sensitivity']))
    card['research_method_blockers']=['CONDITIONAL_CAPITAL_AND_EXIT_ASSUMPTIONS']
    card['sensitivity_method']='GOOG_OPERATING_EV_AND_EQUITY_BRIDGE'
    card.pop('failure_reason',None)
    return True


META_METHOD = 'META_CLASS_A_CONDITIONAL_HOLDER_VALUE_V1'


def _meta_coupon_cost(reported):
    # The certificate uses twelve 30-day months for the initial stub. Both issued tranches belong to the same series and accrue from
    # that series' original initial issue date.
    first_pay = date.fromisoformat(reported['preferred_first_pay_date'])
    origin = date.fromisoformat(reported['preferred_initial_issue_date'])
    stub_days = 360*(first_pay.year-origin.year) + 30*(first_pay.month-origin.month) + first_pay.day-origin.day
    years = dec(stub_days) / 360 + dec(reported['preferred_remaining_full_quarters']) / 4
    units = dec(reported['preferred_initial_issued_m']) + dec(reported['preferred_option_issued_m'])
    return units * dec(reported['preferred_annual_coupon_per_share']) * years


def meta_ordinary_case(profile, forward_shares, settings, *, distribution, capital=None):
    """Price one observed A share acquired before an unobserved distribution."""
    facts = profile['reported']
    p = {key: dec(value) for key, value in zip(profile['case_columns'], settings, strict=True)}

    foa = dec(facts['foa_nine_month_revenue_m']) * Decimal(4) / 3 * (1 + p['foa_cagr']) ** 5
    operating_ebit = foa * p['foa_terminal_margin'] - p['rl_terminal_loss_m']
    operating_ev = operating_ebit * p['operating_ev_multiple']

    issued_notes = dec(facts['usd_notes_issued_m']) + dec(facts['eur_notes_issued_m']) * p['eur_usd']
    net_notes = (dec(facts['usd_notes_proceeds_before_expenses_m'])
                 + dec(facts['eur_notes_estimated_net_m']) * p['eur_usd'])
    preferred_net = (dec(facts['preferred_initial_net_before_issuer_expenses_m'])
                     - dec(facts['preferred_initial_estimated_expenses_m'])
                     + dec(facts['preferred_option_net_actual_m']))
    net_assets = (dec(facts['liquid_cash_and_debt_securities_m']) - p['cash_reserve_m']
                  + dec(facts['marketable_equity_m']) * p['public_equity_recovery']
                  + dec(facts['private_investments_m']) * p['private_equity_recovery']
                  + dec(facts['restricted_cash_m']) * p['restricted_cash_release']
                  + net_notes * p['bond_proceeds_retention']
                  + preferred_net * p['preferred_proceeds_retention'])
    coupons = _meta_coupon_cost(facts)
    claims = (dec(facts['funded_note_face_m']) + issued_notes + coupons
              + dec(facts['legal_accrual_m']) * p['legal_payment_fraction']
              + p['infrastructure_net_financing_m'] + dec(facts['venture_funding_m'])
              + dec(facts['max_venture_residual_guarantees_m']) * p['venture_residual_fraction']
              + p['net_corporate_tax_claim_m'])

    existing = (dec(facts['a_shares_m']) + dec(facts['b_shares_m'])) * (1 + p['share_drift']) ** 5
    capital = capital or {}
    all_c = dec(capital.get('all_c_fraction', 0)) if distribution else Decimal(0)
    # The ordinary distribution predates delivery in the modeled trigger paths.
    # One pair per forward reference share, with only the A leg substitutable.
    a_and_b = existing + forward_shares * (1 - all_c)
    c = existing + forward_shares * (1 + all_c) if distribution else Decimal(0)
    forward_price = dec(facts['forward_initial_price_usd']) * p['forward_price_multiplier']
    ratio = p['a_to_c_ratio'] if distribution else Decimal(1)
    a_vwap = p['a_vwap']
    conversion_vwap = dec(capital.get('preferred_class_vwap', a_vwap / ratio)) if distribution else a_vwap
    reset_ratio = dec(capital.get('post_ex_ratio_40_day', ratio))
    forward_a_vwap = dec(capital.get('forward_a_vwap', a_vwap))
    forward_c_vwap = dec(capital.get('forward_c_vwap', a_vwap / ratio))
    if distribution:
        a_leg = forward_price / 2
        c_leg = a_leg * Decimal('.998')
        substitute = c_leg - max(Decimal(0), forward_a_vwap - forward_c_vwap)
        if all_c and substitute < 0:
            raise ValueError('S3_META_ALL_C_SETTLEMENT_TERMS_OUT_OF_RANGE')
        forward_price = c_leg + a_leg * (1 - all_c) + substitute * all_c
    ratio_reset = min(3, max(2, 1 + reset_ratio)) if distribution else Decimal(1)
    conversion_min = dec(facts['preferred_fixed_min']) * ratio_reset
    conversion_max = dec(facts['preferred_fixed_max']) * ratio_reset
    conversion = max(conversion_min, min(conversion_max, dec(facts['preferred_liquidation_per_share']) / conversion_vwap))
    conversion = conversion.quantize(Decimal('.0001'), rounding=ROUND_HALF_UP)
    preferred_m = dec(facts['preferred_initial_issued_m']) + dec(facts['preferred_option_issued_m'])
    converted_m = preferred_m * conversion
    c_per_original = Decimal(1) if distribution else Decimal(0)
    founder_events = capital.get('founder_events', 0) if distribution else 0
    extra_before_conversion = capital.get('founder_before_conversion', False)
    for _ in range(founder_events if extra_before_conversion else 0):
        new_c = a_and_b + c
        # Certificate §13(a)(i): after the trigger Class C replaces A;
        # the additional dividend is paid proportionally on existing C too.
        split_factor = (c + new_c) / c
        c += new_c
        converted_m *= split_factor
        c_per_original = 1 + 2 * c_per_original
    if distribution:
        c += converted_m
    else:
        a_and_b += converted_m
    for _ in range(founder_events if not extra_before_conversion else 0):
        c += a_and_b + c
        c_per_original = 1 + 2 * c_per_original

    fresh_cash = dec(capital.get('conditional_new_issue_usd_m', 0))
    if fresh_cash:
        # Net funding and modeled retention are separate from actual
        # November debt, preferred proceeds and unobserved forward proceeds.
        issue_class = capital['new_issue_class'] if distribution else 'A'
        if issue_class == 'C':
            c += fresh_cash / (a_vwap / ratio)
        else:
            a_and_b += fresh_cash / a_vwap
        net_assets += fresh_cash * dec(capital['new_issue_retention'])

    forward_cash = forward_price * forward_shares * p['forward_cash_retention']
    equity = operating_ev + net_assets - claims + forward_cash
    c_unit = equity / (a_and_b * ratio + c)
    a_unit = c_unit * ratio
    package = a_unit + c_unit * c_per_original
    if package <= 0:
        raise ValueError('S3_META_NONPOSITIVE_HOLDER_SCENARIO')
    return dict(terminal_price=text(package), state='A_PLUS_C' if distribution else 'A_ONLY',
                operating_five_year_ebit_usd_m=text(operating_ebit), operating_ev_usd_m=text(operating_ev),
                net_assets_before_forward_usd_m=text(net_assets), financial_claims_usd_m=text(claims),
                preferred_cash_coupon_usd_m=text(coupons), projected_a_and_b_m=text(a_and_b),
                projected_c_m=text(c), preferred_conversion_shares_m=text(converted_m),
                forward_cash_retained_usd_m=text(forward_cash), ordinary_equity_usd_m=text(equity),
                terminal_c_shares_per_original_a=text(c_per_original),
                forward_all_c_fraction=text(all_c), founder_additional_distributions=founder_events,
                founder_before_preferred_conversion=bool(founder_events and extra_before_conversion),
                new_conditional_equity_issuance_usd_m=text(fresh_cash),
                terminal_a_per_share=text(a_unit),
                terminal_c_per_share=text(c_unit) if distribution else None)


def meta_equity(card, claims, profile, sources, start, end):
    quote = card.get('meta_class_a_bridge_quote')
    contract = (claims or {}).get('observed_preferred_option_close')
    if not quote or not contract:
        return False
    facts = profile['reported']
    observed_preferred = claims['preferred']['actual_aggregate_issued']['underlying_preferred_shares']
    if (dec(observed_preferred) / 10**6 != dec(facts['preferred_initial_issued_m']) + dec(facts['preferred_option_issued_m'])
            or dec(contract['incremental_net_proceeds_usd']) / 10**6 != dec(facts['preferred_option_net_actual_m'])):
        return False
    with localcontext() as ctx:
        ctx.prec = 34
        forward_shares = dec(claims['forward_reference_shares']) / 10**6
        values = profile['cases']
        card.update(valuation_method=META_METHOD, valuation_status='CONDITIONAL_RESEARCH',
                    price_qualification=dict(quote, eps_interval_start=profile['reported']['share_date']),
                    cross_checks=dict(observed_price=dict(period=quote['price_date'], currency='USD',
                                                          values=dict(observed_price=quote['price']))),
                    scenarios={}, sensitivity=[], joint_sensitivity=[],
                    joint_qualification=dict(complete=False, all_above_required=False),
                    sensitivity_method='META_PAIRED_HOLDER_STATES',
                    assumptions=dict(required_return='.10'),
                    formula='Terminal FoA EBIT less RL losses × EV/EBIT + scenario financial assets/funding − claims, divided across modeled common classes')
        capital_cases = profile.get('capital_scenarios', {})
        for name, settings in values.items():
            capital = capital_cases.get(name)
            distribution = name != 'cautious'
            card['scenarios'][name] = meta_ordinary_case(profile, forward_shares, settings, distribution=distribution, capital=capital)
            alternate = meta_ordinary_case(profile, forward_shares, settings, distribution=not distribution, capital=capital)
            card['sensitivity'].append(dict(parameter='paired_dividend_state', delta=name,
                                             required_return='.10', result=alternate))
        for label, settings in profile.get('capital_stresses', {}).items():
            condition = meta_ordinary_case(profile, forward_shares, values['base'], distribution=True, capital=settings)
            card['sensitivity'].append(dict(parameter='contract_capital_state', delta=label, required_return='.10',
                                             result=condition))
        for growth in ('-.02', '0', '.02'):
            for multiple in ('-.2', '0', '.2'):
                settings = list(values['base'])
                settings[0] = str(dec(settings[0]) + dec(growth))
                settings[3] = str(dec(settings[3]) * (1+dec(multiple)))
                card['joint_sensitivity'].append(dict(stress_axis='REVENUE_EV',
                    revenue_growth_shift=growth, terminal_pe_shift=multiple,
                    result=meta_ordinary_case(profile, forward_shares, settings, distribution=True, capital=capital_cases.get('base'))))
        card['holder_equity_bridge'] = dict(status=('MODELED_CONDITIONAL_ORDINARY_SCOPE' if capital_cases else 'CONDITIONAL_ORDINARY_VALUE_BOUNDED'),
            method=META_METHOD, source_evidence=sources, definition=profile,
            observed_holder='ONE_A_BEFORE_ANY_DISTRIBUTION_WITH_CONDITIONAL_CONTINUOUS_OWNERSHIP',
            current_issued_forward_proceeds='UNOBSERVED_NO_AMOUNT_ADDED_TO_INITIAL_STATE',
            remaining_scope=[] if capital_cases else ['FOUNDER_EVENT_AND_ANTIDILUTION', 'FORWARD_ALL_C_OR_MIXED_DELIVERY',
                                                      'FINANCING_CASH_ALLOCATION'])
        attach_cash(card, dict(quarterly_usd=profile['reported']['ordinary_quarterly_anchor'],
                               locator='Report-based flat distribution per original holding package',
                               source=sources['quarter']), start, end)
    return True
