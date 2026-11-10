"""Read-only strategic allocation of the included rows of one B2 snapshot."""

from decimal import Decimal
import re

from backend.services.execution_batch.trusted_instruments import TRUSTED_INSTRUMENTS
from backend.services.instruments.issuers import IDENTITIES, ISSUERS
from backend.utils.symbol import parse_symbol

from .contracts import Snapshot
from .parser import PolicyError


STRATEGIC_BUCKETS = {
    'growth_and_alternatives': ('us_etf', 'us_direct', 'china', 'gold', 'btc'),
    'stable_pool': ('ib01', 'cbu3', 'cash'),
}
# Exact US listings independently identified by the issuers.
US_EQUITY_ETFS = {
    'SPY': 'https://www.ssga.com/us/en/intermediary/etfs/state-street-spdr-sp-500-etf-trust-spy',
    'QQQ': 'https://www.invesco.com/us/en/financial-products/etfs/invesco-qqq-trust-series-1.html',
    'SPMO': 'https://www.invesco.com/us/en/financial-products/etfs/invesco-sp-500-momentum-etf.html',
}
DIRECT_VEHICLES = {'COMMON_STOCK', 'ADR', 'DEPOSITARY_RECEIPT'}


def _identity(ref, row, economic, coverage, direct_symbols):
    """Return (policy bucket, optional ETF product, identity evidence)."""
    manual = ref.startswith('wealth:')
    vehicle = row.get('vehicle_type')
    alternative = [key for key in ('gold', 'btc') if ref in coverage[key]['asset_refs']]
    if alternative:
        # Domain coverage alone cannot distinguish BTC spot from a fund or CFD.
        if len(alternative) != 1:
            return None
        key = alternative[0]
        refs = coverage[key]['evidence_refs']
        fact = 'GOLD_EXPOSURE' if key == 'gold' else 'BTC_SPOT_UNLEVERED'
        if f'{ref}:{fact}' not in refs or not any(not value.endswith(':' + fact) for value in refs):
            return None
        if key == 'gold' and economic == 'COMMODITY' and (manual or vehicle in {'ETF', 'OTHER'}):
            return key, None, refs
        if key == 'btc' and economic == 'ALTERNATIVE' and manual and row.get('item_type') == 'other_asset':
            return key, None, refs
        return None
    if economic == 'CASH' and (row.get('item_type') == 'bank_cash' if manual else vehicle == 'CASH'):
        return 'cash', None, ['wealth:bank_cash' if manual else 'portfolio:canonical_cash']
    if manual:
        return None

    symbol = row.get('security_symbol') or row.get('symbol')
    currency = row.get('security_currency') or row.get('original_currency') or row.get('currency')
    if not isinstance(symbol, str):
        return None
    try:
        ticker, market = parse_symbol(symbol)
    except ValueError:
        return None
    if symbol != f'{ticker}:{market}' or not re.fullmatch(r'[A-Z0-9][A-Z0-9.-]*', ticker):
        return None

    if vehicle == 'ETF' and currency == 'USD':
        if economic == 'EQUITY' and market == 'US' and ticker in US_EQUITY_ETFS:
            return 'us_etf', ticker, [US_EQUITY_ETFS[ticker]]
        if economic == 'FIXED_INCOME' and market == 'LSE':
            for key in ('IB01', 'CBU3'):
                instrument = TRUSTED_INSTRUMENTS[key]
                if ticker in {instrument.symbol, instrument.local_symbol}:
                    return key.lower(), None, [instrument.verification_source]
    if economic == 'EQUITY' and vehicle in DIRECT_VEHICLES:
        issuer = IDENTITIES.get((symbol, currency))
        if issuer and ISSUERS[issuer][1] == 'CN':
            return 'china', None, ISSUERS[issuer][2]
        if market == 'US' and currency == 'USD' and vehicle == 'COMMON_STOCK' and ticker in direct_symbols:
            return 'us_direct', None, ['policy:3.2', 'portfolio:canonical_listing']
        if market in {'SH', 'SZ'} and currency == 'CNY' and vehicle == 'COMMON_STOCK' and re.fullmatch(r'\d{6}', ticker):
            return 'china', None, ['portfolio:canonical_mainland_listing']
    return None


def _gap(amount, base, target):
    row = {'target_ratio': str(target)}
    if amount is None:
        return {**row, 'amount_cny': None, 'current_ratio': None, 'gap_pp': None, 'amount_gap_cny': None}
    difference = amount - base * target
    return {**row, 'amount_cny': str(amount), 'current_ratio': str(amount / base) if base else None,
            'gap_pp': str(100 * difference / base) if base else None,
            'amount_gap_cny': str(difference) if base else None}


def build_hierarchy(snapshot: Snapshot, *, denominator, included, coverage):
    """Use B2-qualified CNY amounts and canonical economic classes from one freeze."""
    allocation = snapshot.allocation
    direct_symbols = {ticker for name in allocation.direct_equity if name != 'unallocated'
                      for ticker in name.split('（', 1)[0].split('/')}
    amounts = dict.fromkeys(allocation.neutral, Decimal(0))
    products = dict.fromkeys(allocation.etf_internal, Decimal(0))
    assigned, unknown = {}, []
    unclassified = Decimal(0)
    other_etf = Decimal(0)
    for item, row, economic, classification_refs in included:
        if item.amount_cny == 0:
            continue
        ref = item.source_ref
        match = _identity(ref, row, economic, coverage, direct_symbols)
        if match is None:
            unknown.append(ref)
            unclassified += item.amount_cny
            continue
        bucket, product, identity_refs = match
        assigned[ref] = {'bucket': bucket, 'product': product, 'amount_cny': str(item.amount_cny),
                         'evidence_refs': [*classification_refs, *identity_refs]}
        amounts[bucket] += item.amount_cny
        if product in products:
            products[product] += item.amount_cny
        elif bucket == 'us_etf':
            other_etf += item.amount_cny
    if sum(amounts.values()) + unclassified != denominator or sum(products.values()) + other_etf != amounts['us_etf']:
        raise PolicyError('POLICY_ASSET_WEIGHTS_NOT_CLOSED')

    complete = not unknown
    targets = {'growth_and_alternatives': allocation.growth_total, 'stable_pool': allocation.stable_total}
    secondary = {key: _gap(amounts[key] if complete else None, denominator, target)
                 for key, target in allocation.neutral.items()}
    strategic = {}
    rule = next(r for r in snapshot.rules if r.rule_id == 'allocation.deviation')
    triggers = []
    for key, buckets in STRATEGIC_BUCKETS.items():
        amount = sum(amounts[bucket] for bucket in buckets)
        target = targets[key]
        strategic[key] = _gap(amount if complete else None, denominator, target)
        # Compare CNY amounts before division so equality cannot round into a trigger.
        if complete and abs(amount - denominator * target) > denominator * rule.threshold:
            triggers.append(key)

    etf_base = amounts['us_etf']
    etf_rows = {}
    for key, target in allocation.etf_internal.items():
        d_target = allocation.neutral['us_etf'] * target
        etf_rows[key] = {
            **_gap(products[key] if complete else None, etf_base, target),
            'target_ratio_of_d': str(d_target),
            'current_ratio_of_d': str(products[key] / denominator) if complete else None,
            'amount_gap_to_d_target_cny': str(products[key] - denominator * d_target) if complete else None,
        }
    return {
        'status': 'QUALIFIED' if complete else 'PARTIAL' if assigned else 'UNKNOWN',
        'strategic': strategic, 'secondary': secondary,
        'etf': {'status': ('QUALIFIED' if etf_base else 'NO_ETF_HOLDINGS') if complete else 'UNKNOWN',
                'denominator_cny': str(etf_base) if complete else None,
                'other_etf_amount_cny': str(other_etf) if complete else None, 'products': etf_rows},
        'strategic_review': {
            'status': ('HUMAN_REVIEW' if triggers else 'NOT_TRIGGERED') if complete else 'UNKNOWN',
            'triggered_classes': triggers, 'rule_id': rule.rule_id, 'operator': rule.operator,
            'threshold_pp': str(rule.threshold * 100), 'source_clause': rule.source_clause.model_dump(),
        },
        'classified_assets': assigned, 'unclassified_asset_refs': unknown,
        'unclassified_amount_cny': str(unclassified), 'execution_authorized': False,
    }
