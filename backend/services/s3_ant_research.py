"""Anthropic conditional operating cash recovery from a disclosed pro forma base."""
from decimal import Decimal, localcontext

from backend.services.market_data.quality_valuation import dec, text
from backend.services.s3_holder_returns import qualify_joint_sensitivity, settle

METHOD = 'ANT_PRO_FORMA_CASH_RECOVERY_V1'
CODE = 'backend/services/s3_ant_research.py'


def opening_bridge(facts):
    reported_cash = dec(facts['pro_forma_liquidity_bn'])
    issuer_offer = dec(facts['closing_issuer_shares_bn']) * dec(facts['ipo_price'])
    secondary_offer = dec(facts['closing_selling_holder_shares_bn']) * dec(facts['ipo_price'])
    placement = dec(facts['closing_private_shares_bn']) * dec(facts['ipo_price'])
    shoe = dec(facts['exercise_shares_bn']) * dec(facts['ipo_price'])
    rounded_difference = abs(dec(facts['no_shoe_pro_forma_liquidity_bn']) + shoe - reported_cash)
    starting_cash = dec(facts['september_cash_bn'])
    if rounded_difference > Decimal('.05') or issuer_offer + placement <= reported_cash - starting_cash:
        raise ValueError('S3_ANT_DISCLOSED_CAPITAL_RECONCILIATION_FAILED')
    return dict(september_reported_liquidity_bn=text(starting_cash),
                issuer_ipo_gross_bn=text(issuer_offer), private_placement_gross_bn=text(placement),
                excluded_secondary_gross_bn=text(secondary_offer), shoe_gross_bn=text(shoe),
                implied_gross_to_pro_forma_adjustments_bn=text(starting_cash+issuer_offer+placement-reported_cash),
                pro_forma_liquidity_bn=text(reported_cash),
                scope='OFFER_CLOSED_PRO_FORMA_NOT_CLOSING_BALANCE')


def contract_cash(core, scheduled, overlap_fraction):
    if not 0 <= overlap_fraction <= 1 or min(core, scheduled) < 0:
        raise ValueError('S3_ANT_INVALID_CONTRACT_OVERLAP')
    overlap = min(core, scheduled * overlap_fraction)
    return core + scheduled - overlap, overlap


def cash_case(facts, case, *, growth_shift=Decimal(0), multiple_shift=Decimal(0)):
    liquidity = dec(facts['pro_forma_liquidity_bn'])
    shares = dec(facts['a_shares_bn']) + dec(facts['n_shares_bn'])
    cap = dec(facts['options_bn']) * dec(case['option_exercise_fraction'])
    shares += cap + dec(facts['rsus_bn']) * dec(case['rsu_settlement_fraction'])
    liquidity += cap * dec(facts['option_strike'])
    economic = 'contract_overlap' in case
    if economic:
        liquidity += dec(case['post_september_cash_change_bn'])
    # Conversion shares already included in the pro forma capital are excluded
    # from this incremental amount. A retained claim and shares never coexist.
    convert = case['convert_remaining_notes']
    converted_fraction = dec(case['legacy_note_conversion_fraction']) if economic else Decimal(int(convert))
    residual_claim = Decimal(0) if convert else dec(facts['remaining_note_carry_bn']) * dec(case['note_claim_factor'])
    shares += dec(facts['remaining_note_conversion_bn']) * converted_fraction
    if economic and not 0 <= converted_fraction <= 1:
        raise ValueError('S3_ANT_INVALID_CONVERSION_FRACTION')

    sales = dec(facts['quarter_revenue_bn']) * 4
    rows = []
    for year, (growth, compute_ratio, opex_ratio, minimum) in enumerate(zip(
            case['growth'], case['compute_cost_share'], case['non_compute_cost_share'],
            case['contract_cash_floor_bn'], strict=True), 1):
        sales *= 1 + dec(growth) + growth_shift
        operating_compute = sales * dec(compute_ratio)
        # Contract cash includes overlapping usage, prepayments and leases.
        # Count only the excess above the cash operating-cost assumption.
        scheduled = dec(minimum)
        if economic and year == 1:
            scheduled = (dec(facts['cash_due_in_12m_bn']) - dec(facts['terminable_near_term_bn'])
                         * (1-dec(case['near_term_contingent_performance_fraction']))
                         - dec(case['contract_cash_before_start_bn']))
        overlap_fraction = dec(case['contract_overlap'][year-1]) if economic else Decimal(1)
        compute_cash, overlap = contract_cash(operating_compute, scheduled, overlap_fraction)
        operating_margin = 1 - dec(compute_ratio) - dec(opex_ratio)
        pre_tax = sales - compute_cash - sales * dec(opex_ratio)
        tax = max(Decimal(0), pre_tax) * dec(case['tax_rate'])
        own_capex = sales * dec(case['incremental_capex_share'])
        free_cash = pre_tax - tax - own_capex
        liquidity += free_cash
        equity_raise = max(Decimal(0), dec(case['minimum_liquidity_bn']) - liquidity)
        liquidity += equity_raise
        foundation_release = (dec(facts['subsidiary_public_benefit_shares_bn'])
                              * dec(case['foundation_release_fraction']) if year == 3 else Decimal(0))
        shares += foundation_release
        shares = shares * (1 + dec(case['annual_new_share_drift'])) + equity_raise / dec(case['equity_issue_per_share'])
        rows.append(dict(year=year, revenue_bn=text(sales), operating_margin=text(operating_margin),
            compute_cash_bn=text(compute_cash), incremental_timing_outflow_bn=text(compute_cash-operating_compute),
            tax_bn=text(tax), capex_excluding_compute_commitments_bn=text(own_capex), free_cash_bn=text(free_cash),
            new_equity_bn=text(equity_raise), ending_liquidity_bn=text(liquidity), shares_bn=text(shares),
            subsidiary_shares_released_bn=text(foundation_release)))
        if economic:
            rows[-1].update(core_compute_cash_bn=text(operating_compute), contract_due_bn=text(scheduled),
                            contract_overlap_bn=text(overlap))

    # Operating capitalization excludes excess cash, interest and the note claim.
    multiple = dec(case['terminal_cash_multiple']) * (1 + multiple_shift)
    extra = {}
    if economic:
        # Terminal run rate capitalizes operating compute. Separately reserve only
        # incremental post-endpoint payments, never the entire contract inventory.
        financing_rate = dec(case['claim_discount_rate'])
        post_2030 = dec(facts['total_contract_inventory_bn']) - dec(facts['pre_2031_contract_inventory_bn'])
        post_endpoint = max(Decimal(0), post_2030 - dec(case['included_2031_contract_bn']))
        annual_tail = post_endpoint * dec(case['terminal_tail_performance']) / 14
        tail_pv = sum((annual_tail * (1-dec(case['terminal_tail_overlap'])) / (1+financing_rate)**year
                       for year in range(1, 15)), Decimal(0))
        normalized_pre_tax = sales * operating_margin
        normalized_cash = normalized_pre_tax - max(Decimal(0), normalized_pre_tax)*dec(case['tax_rate']) - own_capex
        gross_equity = normalized_cash * multiple + liquidity - dec(case['minimum_liquidity_bn']) - tail_pv
        remaining_fraction = 1-converted_fraction
        principal_pv = (dec(facts['remaining_note_nominal_bn']) * remaining_fraction
                        / (1+financing_rate)**dec(case['legacy_principal_maturity_years']))
        equivalent = (dec(facts['remaining_note_conversion_bn']) * remaining_fraction
                      * dec(case['legacy_residual_exercise_weight']))
        # Two alternative recovery branches share only the unconverted claim.
        # The equity alternative solves the dilution rather than pricing it with
        # an undiluted share price. Retained principal and option are not added.
        equity_value = min((gross_equity-principal_pv)/shares, gross_equity/(shares+equivalent))
        residual_claim = gross_equity-equity_value*shares
        equity = gross_equity-residual_claim
        extra = dict(terminal_normalized_fcf_bn=text(normalized_cash), post_endpoint_commitment_pv_bn=text(tail_pv),
                     residual_principal_pv_bn=text(principal_pv), residual_option_equivalent_shares_bn=text(equivalent),
                     explicit_legacy_conversion_fraction=text(converted_fraction))
    else:
        equity = free_cash * multiple + liquidity - dec(case['minimum_liquidity_bn']) - residual_claim
    if min(equity, shares) <= 0 or (not economic and free_cash <= 0):
        raise ValueError('S3_ANT_TERMINAL_RECOVERY_UNSUPPORTED')
    return dict(terminal_price=text(equity / shares), terminal_ordinary_equity_bn=text(equity),
                terminal_retained_note_claim_bn=text(residual_claim), model_years=rows,
                note_settlement=('CONDITIONAL_PARTIAL_CONVERSION_AND_ECONOMIC_RESERVE' if economic else
                                 ('CONDITIONAL_CONVERSION' if convert else 'CONDITIONAL_RETAINED_CLAIM')), **extra)


def build(card, profile, evidence, start, end, reviewed_at):
    quote = card.get('ant_fundamental_quote')
    if not quote or quote.get('scope') != 'FUNDAMENTAL_VALUATION_ONLY':
        return False
    facts, settings = profile['reported'], profile['cases']
    rate = '.10'
    with localcontext() as ctx:
        ctx.prec = 34
        def run(case, **stress):
            return settle(cash_case(facts, case, **stress), quote['price'], rate, start, end, start, [])

        scenarios = {name:run(case) for name, case in settings.items()}
        base = settings['base']
        joint = [dict(stress_axis='REVENUE_CASH_MULTIPLE', revenue_growth_shift=g, terminal_pe_shift=m,
                      result=run(base, growth_shift=dec(g), multiple_shift=dec(m)))
                 for g in ('-.03','0','.03') for m in ('-.2','0','.2')]
        evidence_bridge = opening_bridge(facts) if 'contract_overlap' in base else None
        if evidence_bridge:
            stresses = {
                'no_near_term_overlap': dict(contract_overlap=['0', *base['contract_overlap'][1:]]),
                'low_tail_overlap': dict(terminal_tail_overlap='.45'),
                'cash_draw_after_september': dict(post_september_cash_change_bn='-35'),
                'standing_conversion_cap': dict(legacy_note_conversion_fraction='0'),
                'all_notes_convert': dict(legacy_note_conversion_fraction='1'),
                'dilutive_financing': dict(equity_issue_per_share='20', annual_new_share_drift='.065'),
            }
        else:
            stresses = {'alternate':dict(convert_remaining_notes=not base['convert_remaining_notes'])}
        sensitivity = [dict(parameter='contract_and_capital_scope', delta=label, required_return=rate,
                            result=run(dict(base, **overrides))) for label, overrides in stresses.items()]
        card.update(valuation_method=METHOD, valuation_status='CONDITIONAL_RESEARCH',
            price_qualification=dict(quote,eps_interval_start='2026-11-19'),
            cross_checks=dict(observed_price=dict(period=quote['price_date'], currency='USD',
                                                   values=dict(observed_price=quote['price']))),
            scenarios=scenarios, joint_sensitivity=joint, sensitivity=sensitivity, joint_qualification={},
            cash_return_contract=dict(start=start.isoformat(),end=end.isoformat(),schedule=[],
                                      currency='USD',unit='ONE_LISTED_CLASS_A_SHARE'),
            assumptions=dict(required_return=rate), sensitivity_method='ANT_CASH_RECOVERY_STRESS',
            conditional_value_range=sorted((x['conditional_value'] for x in scenarios.values()),key=dec)[::2],
            formula='Terminal operating free cash × multiple + excess liquidity − retained note claim, divided by diluted ordinary economic shares',
            holder_equity_bridge=dict(status=('MODELED_CONDITIONAL_ORDINARY_SCOPE' if evidence_bridge
                                              else 'CONDITIONAL_ORDINARY_VALUE_BOUNDED'),
                source_evidence=evidence, definition=profile, reviewed_at=reviewed_at,
                opening_bridge=evidence_bridge,
                remaining_scope=[] if evidence_bridge else ['ACTUAL_POST_CLOSE_LIQUIDITY_AND_CASH_TIMING',
                                                             'REMAINING_CONVERSION_AND_UNDISCLOSED_SETTLEMENT_STATE']))
        qualify_joint_sensitivity(card, rate)
    return True
