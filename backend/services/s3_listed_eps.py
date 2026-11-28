"""Listed EPS anchored forecast. No historical parent income is reconstructed."""
from decimal import Decimal, localcontext

from backend.services.market_data.quality_valuation import dec, text
from backend.services.s3_holder_returns import attach_cash, qualify_joint_sensitivity, settle

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


def portfolio_case(eps, margin, parameters, reference_tax, scope, name):
    assumptions = {k: dec(v) for k, v in scope['assumptions'][name].items()}
    rows = scope['normalization_rows']
    tax = assumptions['normalization_tax']
    reduction = sum(dec(row['other_income_m']) / dec(row['diluted_m']) for row in rows) * (1 - tax)
    adjusted = eps - reduction
    if adjusted <= 0:
        raise ValueError('S3_LISTED_EPS_NORMALIZATION_NONPOSITIVE')
    case = project(adjusted, margin, parameters, reference_tax)
    balance = {k: dec(v) for k, v in scope['balances_m'].items()}
    liquidity = balance['cash_and_market_debt'] - balance['convertible_included_in_market_debt']
    equity_like = (balance['short_market_equity'] + balance['locked_long_market_equity']
                   + balance['convertible_included_in_market_debt'])
    other_private = balance['nonmarketable_total'] - balance['private_equity_subtotal']
    if min(liquidity, equity_like, other_private) < 0:
        raise ValueError('S3_LISTED_EPS_ASSET_BUCKETS_DO_NOT_RECONCILE')
    reserve = assumptions['operating_cash_reserve_m']
    if reserve > liquidity or not 0 <= assumptions['guarantee_loss_fraction'] <= 1:
        raise ValueError('S3_LISTED_EPS_BALANCE_ASSUMPTIONS_INVALID')
    guarantee_loss = balance['maximum_guarantee_gross'] * assumptions['guarantee_loss_fraction']
    financial_assets = (liquidity - reserve + equity_like * assumptions['market_risk_net_recovery']
                        + balance['private_equity_subtotal'] * assumptions['private_equity_net_recovery']
                        + other_private * assumptions['other_private_net_recovery'])
    investment_funding = balance['signed_equity_commitments'] * assumptions['signed_equity_net_funding_fraction']
    acquisition_claim = (balance['pending_hf_consideration'] * assumptions['hf_consideration_fraction']
                         + balance['hf_equity_retention_max'] * assumptions['hf_retention_economic_claim_fraction'])
    funding_shortfall = assumptions['infrastructure_net_cash_shortfall_m']
    financial_claims = (balance['funded_debt'] + guarantee_loss + investment_funding
                        + acquisition_claim + funding_shortfall + balance['prior_reported_groq_payable_m'])
    diluted_shares = dec(rows[-1]['diluted_m']) * (1 + dec(parameters[4])) ** 5
    terminal_operating = dec(case['terminal_price'])
    financial_per_share = (financial_assets - financial_claims) / diluted_shares
    case.update(
        terminal_price=text(terminal_operating + financial_per_share),
        equity_scope_components=dict(
            normalized_research_eps=text(adjusted), removed_eps=text(reduction),
            terminal_operating_per_share=text(terminal_operating),
            risk_adjusted_assets_usd_m=text(financial_assets), claims_usd_m=text(financial_claims),
            guarantee_loss_usd_m=text(guarantee_loss), pending_acquisition_claim_usd_m=text(acquisition_claim),
            incremental_investment_funding_usd_m=text(investment_funding),
            infrastructure_funding_usd_m=text(funding_shortfall),
            terminal_diluted_shares_m=text(diluted_shares),
            net_financial_per_share=text(financial_per_share)))
    return case


def build(card, check, profile, evidence, start,end, *, portfolio=None):
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
        scope = portfolio and portfolio['definition']
        if scope and (
            scope['balance_date'] != bridge['period']
            or [row['period'] for row in scope['normalization_rows']] != eps_record['details'].get('periods')
            or not {row['source'] for row in scope['normalization_rows']} <= portfolio['sources'].keys()
        ):
            scope = None

        def researched_case(name, params):
            if scope:
                return portfolio_case(eps, margin, params, tax, scope, name)
            return project(eps, margin, params, tax)

        card.update(valuation_method=METHOD, valuation_status='CONDITIONAL_RESEARCH', scenarios={name: researched_case(name, params) for name, params in assumed.items()},
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
                case = researched_case('base', params)
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
        if scope:
            card['listed_eps_method']['residual'] = scope['limitations']
            card['listed_eps_method']['portfolio_extension'] = dict(
                status='MODELED_CONDITIONAL_ORDINARY_SCOPE', version='LISTED_EPS_PORTFOLIO_BRIDGE_V1',
                source_evidence=portfolio['sources'], definition=scope,
                observed_ttm_equity_gains_usd_m=sum(row['equity_gains_m'] for row in scope['normalization_rows']))
            card['formula'] += ' plus risk-adjusted financial assets less funded/contingent claims per terminal diluted share'
        price = dec(quote['price'])
        for growth_delta in ('-.04', '0', '.04'):
            for multiple_delta in ('-.2', '0', '.2'):
                params = list(assumed['base'])
                params[0] = [str(dec(v) + dec(growth_delta)) for v in params[0]]
                params[5] = str(dec(params[5]) * (1 + dec(multiple_delta)))
                case = researched_case('base', params)
                card['joint_sensitivity'].append(dict(
                    stress_axis='REVENUE_PE', revenue_growth_shift=growth_delta,
                    terminal_pe_shift=multiple_delta,
                    result=settle(case, price, rate, start, end, start,
                                  card['cash_return_contract']['schedule'])))
        qualify_joint_sensitivity(card, rate)
        for case in card['scenarios'].values():
            case['terminal_price_needed_for_hurdle']=text(dec(case['required_terminal_price_after_cash']))
    return True
