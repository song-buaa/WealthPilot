"""Listed EPS anchored forecast. No historical parent income is reconstructed."""
from decimal import Decimal, localcontext

from backend.services.market_data.quality_valuation import dec, text
from backend.services.s3_holder_returns import attach_cash, settle

CODE = 'backend/services/s3_listed_eps.py'
METHOD = 'LISTED_EPS_OPERATING_DRIVERS_V1'


def project(eps, margin, assumptions, reference_tax):
    growth, margin_end, tax, conversion, dilution, multiple = assumptions
    sales = Decimal(1)
    path = []
    margin_end, tax, conversion, dilution, multiple = map(dec,(margin_end,tax,conversion,dilution,multiple))
    if not 0 < margin <= 1 or not 0 < margin_end <= 1 or not 0 <= tax < 1 or not Decimal('-.2') < dilution < Decimal('.25') or len(growth) != 5 or multiple<=0:
        raise ValueError('LISTED_EPS_ASSUMPTIONS_INVALID')
    for year, increase in enumerate(growth,1):
        sales *= 1+dec(increase)
        margin_y = margin+(margin_end-margin)*year/5
        tax_y = reference_tax+(tax-reference_tax)*year/5
        earnings = eps*sales*(margin_y/margin)*(1-tax_y)/(1-reference_tax)
        earnings *= (1+(conversion-1)*year/5)/(1+dilution)**year
        path.append(dict(year=year, revenue_index=text(sales),operating_margin=text(margin_y),
                         tax_assumption=text(tax_y),relative_shares=text((1+dilution)**year),eps=text(earnings)))
    return dict(terminal_price=text(earnings*multiple),years=path,terminal_pe=str(multiple))


def build(card, check, profile, evidence, start,end):
    bridge = check.get('bridge') or {}
    eps_record = bridge.get('eps') or {}
    quote = card.get('independent_eps_quote')
    if (not quote or bridge.get('period') != profile['financial_period']
            or not {'revenue','operating_income','cfo'} <= (bridge.get('flows') or {}).keys()
            or not eps_record.get('evidence_refs') or eps_record.get('quality') != 'valid'
            or eps_record.get('historical_only') or eps_record.get('currency') != 'USD'
            or eps_record.get('unit') != 'currency_per_listed_unit'
            or eps_record.get('details',{}).get('share_basis') != 'diluted_eps_ordinary_quarter'):
        return False
    dividend = card.get('observed_dividend')
    proof = evidence[profile['dividend_source']]
    if not dividend or dividend['source_document_sha256'] != proof['source_sha256']:
        return False

    with localcontext() as ctx:
        ctx.prec = 34
        eps = dec(eps_record['values']['diluted_eps_ttm'])
        margin = dec(bridge['flows']['operating_income']['value'])/dec(bridge['flows']['revenue']['value'])
        rate, tax = map(dec,('.10',profile['reference_tax']))
        if eps <= 0:
            return False
        assumed = profile['parameters']
        card.update(valuation_method=METHOD,scenarios={n:project(eps,margin,p,tax) for n,p in assumed.items()},
            cross_checks=dict(observed_price=dict(period=quote['price_date'],currency='USD',
                                                 values=dict(observed_price=quote['price']),source_receipt=quote),
                              diluted_eps_ttm=eps_record),
            price_qualification=dict(quote,eps_interval_start=bridge['start']),
            formula='Listed EPS × revenue index × EBIT-margin index × modeled tax/nonoperating adjustment ÷ share index × exit PE',
            assumptions=dict(required_return='.10',rationale=profile['rationale']),
            sensitivity=[],joint_sensitivity=[],joint_qualification=dict(complete=False,all_above_required=False),
            sensitivity_method='LISTED_EPS_OPERATING_DRIVERS')
        axes = ((0,'revenue_growth',('-.04','.04')),(1,'terminal_margin',('-.06','.06')),
                (3,'nonoperating_conversion',('-.10','.10')),(5,'terminal_pe',('-.2','.2')))
        for idx,name,shifts in axes:
            for raw in shifts:
                params = list(assumed['base'])
                delta = dec(raw)
                if idx==0:
                    params[0] = [str(dec(v)+delta) for v in params[0]]
                else:
                    params[idx] = str(dec(params[idx])*(1+delta) if idx==5 else dec(params[idx])+delta)
                case = project(eps,margin,params,tax)
                card['sensitivity'].append(dict(parameter=name,delta=raw,required_return=str(rate),
                    parameters={name:params[idx]},result=case))
        attach_cash(card,dict(quarterly_usd=dividend['amount_per_share'],
            locator='Board declaration calibrates the hypothetical annual schedule',
            source=proof),start,end)
        card['assumptions']['rationale'] = profile['rationale']
        card['cash_return_contract']['anchor']['eligibility_limitation'] = (
            'The declaration calibrates the cash rate. The hypothetical annual schedule is independent '
            'of the declared payment, which is excluded because buyer entitlement is unproved.')
        card['listed_eps_method'] = dict(
            method=METHOD, qualified_base=eps_record, observed_flows=bridge['flows'],
            parent_income_scope='UNPROVEN_AND_NOT_USED', reference_tax_assumption=str(tax),
            scenario_parameters=assumed, review_basis=profile['rationale'],
            residual=profile['residual'], observed_dividend=dividend,
            sources=evidence)
        price = dec(quote['price'])
        for growth_delta in ('-.04', '0', '.04'):
            for multiple_delta in ('-.2', '0', '.2'):
                params = list(assumed['base'])
                params[0] = [str(dec(v) + dec(growth_delta)) for v in params[0]]
                params[5] = str(dec(params[5]) * (1 + dec(multiple_delta)))
                case = project(eps, margin, params, tax)
                card['joint_sensitivity'].append(dict(
                    stress_axis='REVENUE_PE', revenue_growth_shift=growth_delta,
                    terminal_pe_shift=multiple_delta,
                    result=settle(case, price, rate, start, end, start,
                                  card['cash_return_contract']['schedule'])))
        card['joint_qualification'].update(
            complete=True, all_above_required=all(
                dec(item['result']['annualized_return']) >= rate for item in card['joint_sensitivity']))
        for case in card['scenarios'].values():
            case['terminal_price_needed_for_hurdle']=text(dec(case['required_terminal_price_after_cash']))
    return True
