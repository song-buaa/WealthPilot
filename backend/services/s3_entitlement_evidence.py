"""Bounded issuer-disclosed capital structure components for blocked S3 cards.

These components never create an EPS observation, valuation, ranking or settlement.
Class coefficients, terms and source references remain issuer-specific.
"""
from decimal import Decimal, localcontext

VERSION = 's3-relative-entitlement-evidence.v1'


def preferred_bounds(terms):
    """Convert depositary units once and report gross contractual endpoints."""
    units = terms['depositary_units']
    ratio = terms['units_per_preferred']
    if type(units) is not int or type(ratio) is not int or units <= 0 or ratio <= 0 or units % ratio:
        raise ValueError('S3_ENTITLEMENT_DEPOSITARY_BASIS_REJECTED')
    shares = units // ratio
    low, high, par, coupon = (Decimal(terms[k]) for k in (
        'conversion_min', 'conversion_max', 'liquidation_usd', 'annual_rate'))
    if not all(x.is_finite() for x in (low, high, par, coupon)):
        raise ValueError('S3_ENTITLEMENT_NONFINITE_TERMS')
    if not (0 < low <= high and par > 0 and 0 <= coupon < 1):
        raise ValueError('S3_ENTITLEMENT_TERMS_REJECTED')
    return dict(underlying_preferred_shares=shares, aggregate_liquidation_preference_usd=str(shares*par),
                annual_coupon_equivalent_usd=str(shares*par*coupon),
                gross_conversion_share_endpoints=[str(shares*low), str(shares*high)],
                common_class=terms['common_class'], size_basis=terms['size_basis'])


def _ant(inputs):
    ledger = inputs['shares']
    class_a = ledger['pre_deal_pro_forma_a'] + ledger['issuer_primary_a']
    class_n = ledger['pre_deal_pro_forma_accounting_n'] + ledger['placement_n']
    legal_n = class_n + ledger['public_benefit_subsidiary_n']
    cash = inputs['cash_millions']
    return dict(accounting_pro_forma_a=class_a, accounting_pro_forma_n=class_n,
                accounting_pro_forma_a_plus_n=class_a+class_n,
                legal_pro_forma_a_plus_n=class_a+legal_n,
                share_basis='SEPTEMBER_30_PRO_FORMA_RECONCILED_TO_CLOSING_ISSUANCE_NOT_ACTUAL_CLOSE_LEDGER',
                cfo_less_named_capital_cash_outflows_usd_millions=cash['cfo']-cash['capex']-
                    cash['prepaid_leased_assets']-cash['finance_lease_principal'],
                cash_basis='HISTORICAL_SUBTOTAL_NOT_ORDINARY_FCFE')


def qualification_components(spec, proofs):
    """Bind documented inputs to already qualified SEC sources for this issuer."""
    names = spec['source_labels']
    if not names or not set(names) <= proofs.keys():
        raise ValueError('S3_ENTITLEMENT_SOURCE_NOT_QUALIFIED')
    with localcontext() as context:
        context.prec = 34
        result = {}
        if 'preferred' in spec['inputs']:
            result['preferred'] = {s: preferred_bounds(terms)
                                   for s,terms in spec['inputs']['preferred'].items()}
        if 'forward_contracts_shares' in spec['inputs']:
            result['forward_reference_shares'] = sum(spec['inputs']['forward_contracts_shares'])
            result['forward_basis'] = 'CONTRACT_REFERENCE_NOT_FINAL_ISSUANCE_OR_CASH_SETTLEMENT'
        if 'shares' in spec['inputs']:
            result.update(_ant(spec['inputs']))
    return dict(version=VERSION, status='COMPONENTS_ONLY_RETURN_QUALIFICATION_WITHHELD',
                evidence={name: proofs[name] for name in names}, published_inputs=spec['inputs'],
                derived=result, remaining_requirements=spec['remaining_requirements'])
