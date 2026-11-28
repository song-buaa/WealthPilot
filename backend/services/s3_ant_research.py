"""Anthropic conditional operating cash recovery from a disclosed pro forma base."""
from decimal import Decimal, localcontext

from backend.services.market_data.quality_valuation import dec, text
from backend.services.s3_holder_returns import qualify_joint_sensitivity, settle

METHOD = 'ANT_PRO_FORMA_CASH_RECOVERY_V1'
CODE = 'backend/services/s3_ant_research.py'


def cash_case(facts, case, *, growth_shift=Decimal(0), multiple_shift=Decimal(0)):
    liquidity = dec(facts['pro_forma_liquidity_bn'])
    shares = dec(facts['a_shares_bn']) + dec(facts['n_shares_bn'])
    cap = dec(facts['options_bn']) * dec(case['option_exercise_fraction'])
    shares += cap + dec(facts['rsus_bn']) * dec(case['rsu_settlement_fraction'])
    liquidity += cap * dec(facts['option_strike'])
    # Conversion shares already included in the pro forma capital are excluded
    # from this incremental amount. A retained claim and shares never coexist.
    convert = case['convert_remaining_notes']
    residual_claim = Decimal(0) if convert else dec(facts['remaining_note_carry_bn']) * dec(case['note_claim_factor'])
    if convert:
        shares += dec(facts['remaining_note_conversion_bn'])

    sales = dec(facts['quarter_revenue_bn']) * 4
    rows = []
    for year, (growth, compute_ratio, opex_ratio, minimum) in enumerate(zip(
            case['growth'], case['compute_cost_share'], case['non_compute_cost_share'],
            case['contract_cash_floor_bn'], strict=True), 1):
        sales *= 1 + dec(growth) + growth_shift
        operating_compute = sales * dec(compute_ratio)
        # Contract cash includes overlapping usage, prepayments and leases.
        # Count only the excess above the cash operating-cost assumption.
        compute_cash = max(operating_compute, dec(minimum))
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

    # Operating capitalization excludes excess cash, interest and the note claim.
    multiple = dec(case['terminal_cash_multiple']) * (1 + multiple_shift)
    equity = free_cash * multiple + liquidity - dec(case['minimum_liquidity_bn']) - residual_claim
    if min(free_cash, equity, shares) <= 0:
        raise ValueError('S3_ANT_TERMINAL_RECOVERY_UNSUPPORTED')
    return dict(terminal_price=text(equity / shares), terminal_ordinary_equity_bn=text(equity),
                terminal_retained_note_claim_bn=text(residual_claim), model_years=rows,
                note_settlement='CONDITIONAL_CONVERSION' if convert else 'CONDITIONAL_RETAINED_CLAIM')


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
        sensitivity = [dict(parameter='residual_note_settlement', delta='alternate', required_return=rate,
                            result=run(dict(base,convert_remaining_notes=not base['convert_remaining_notes'])))]
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
            holder_equity_bridge=dict(status='CONDITIONAL_ORDINARY_VALUE_BOUNDED',
                source_evidence=evidence, definition=profile, reviewed_at=reviewed_at,
                remaining_scope=['ACTUAL_POST_CLOSE_LIQUIDITY_AND_CASH_TIMING',
                                 'REMAINING_CONVERSION_AND_UNDISCLOSED_SETTLEMENT_STATE']))
        qualify_joint_sensitivity(card, rate)
    return True
