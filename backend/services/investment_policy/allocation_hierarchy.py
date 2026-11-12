"""Read-only strategic allocation of the included rows of one B2 snapshot."""

from decimal import Decimal
from fractions import Fraction
import re

from backend.services.execution_batch.trusted_instruments import TRUSTED_INSTRUMENTS
from backend.services.instruments.issuers import IDENTITIES, ISSUERS, direct_issuer
from backend.utils.symbol import parse_symbol

from .contracts import PolicyReference, Snapshot, SnapshotV2, digest
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
ACCOUNT_FACTS = {
    'US_EQUITY_ETF': 'us_etf', 'US_DIRECT_EQUITY': 'us_direct', 'CN_DIRECT_EQUITY': 'china',
}


FACT_SUFFIXES = tuple(':' + key for key in (*ACCOUNT_FACTS, 'GOLD_EXPOSURE', 'BTC_SPOT_UNLEVERED'))


def _owner_attests(ref, fact, refs):
    marker = f'{ref}:{fact}'
    prefix = f'{marker}:source:'
    return marker in refs and any(
        value.startswith(prefix) and value[len(prefix):].strip() and not value.endswith(FACT_SUFFIXES)
        for value in refs
    )


def _identity(ref, row, economic, coverage, direct_symbols):
    """Return (policy bucket, optional ETF product, identity evidence)."""
    manual = ref.startswith('wealth:')
    vehicle = row.get('vehicle_type')
    account_refs = coverage['accounts']['evidence_refs']
    claims = [fact for fact in ACCOUNT_FACTS if f'{ref}:{fact}' in account_refs]
    if len(claims) > 1:
        return None
    claim = claims[0] if claims else None
    if claim and (economic != 'EQUITY' or not _owner_attests(ref, claim, account_refs)):
        return None

    alternative = [key for key in ('gold', 'btc') if ref in coverage[key]['asset_refs']]
    if alternative:
        # Domain coverage alone cannot distinguish BTC spot from a fund or CFD.
        if len(alternative) != 1:
            return None
        key = alternative[0]
        refs = coverage[key]['evidence_refs']
        fact = 'GOLD_EXPOSURE' if key == 'gold' else 'BTC_SPOT_UNLEVERED'
        if not _owner_attests(ref, fact, refs):
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
            return ('us_etf', ticker, [US_EQUITY_ETFS[ticker]]) if claim in {None, 'US_EQUITY_ETF'} else None
        if economic == 'FIXED_INCOME' and market == 'LSE':
            for key in ('IB01', 'CBU3'):
                instrument = TRUSTED_INSTRUMENTS[key]
                if ticker in {instrument.symbol, instrument.local_symbol}:
                    return key.lower(), None, [instrument.verification_source]
    if vehicle == 'ETF' and claim == 'US_EQUITY_ETF':
        return 'us_etf', None, account_refs
    if economic == 'EQUITY' and vehicle in DIRECT_VEHICLES:
        issuer = IDENTITIES.get((symbol, currency))
        if issuer and ISSUERS[issuer][1] == 'CN':
            return ('china', None, ISSUERS[issuer][2]) if claim in {None, 'CN_DIRECT_EQUITY'} else None
        if market == 'US' and currency == 'USD' and vehicle == 'COMMON_STOCK' and ticker in direct_symbols:
            return ('us_direct', None, ['policy:3.2', 'portfolio:canonical_listing']) if claim in {None, 'US_DIRECT_EQUITY'} else None
        if market in {'SH', 'SZ'} and currency == 'CNY' and vehicle == 'COMMON_STOCK' and re.fullmatch(r'\d{6}', ticker):
            return ('china', None, ['portfolio:canonical_mainland_listing']) if claim in {None, 'CN_DIRECT_EQUITY'} else None
        if claim in {'US_DIRECT_EQUITY', 'CN_DIRECT_EQUITY'}:
            return ACCOUNT_FACTS[claim], None, account_refs
    return None


def _gap(amount, base, target):
    row = {'target_kind': 'NEUTRAL_POLICY', 'target_ratio': str(target)}
    if amount is None:
        return {**row, 'amount_cny': None, 'current_ratio': None, 'gap_pp': None, 'amount_gap_cny': None}
    difference = amount - base * target
    return {**row, 'amount_cny': str(amount), 'current_ratio': str(amount / base) if base else None,
            'gap_pp': str(100 * difference / base) if base else None,
            'amount_gap_cny': str(difference) if base else None}


def formal_strategic_review(snapshot, hierarchy, *, policy_context, portfolio_id, snapshot_sha256, denominator):
    """Qualify the existing B3 calculation using the verified PolicyService context."""
    candidate = hierarchy["strategic_review"]
    if (not isinstance(snapshot, SnapshotV2) or hierarchy["status"] != "QUALIFIED"
            or snapshot.strategic_review_scope.content_status != "APPROVED_CONTENT"
            or policy_context.get("status") != "ACTIVE" or policy_context.get("mode") != "POLICY_ACTIVE"):
        return candidate

    release = policy_context.get("release") or {}
    if (release.get("synthetic") is not False
            or release.get("environment") not in {"PRODUCTION", "ISOLATED_REHEARSAL"}
            or not re.fullmatch(r"[0-9a-f]{64}", snapshot_sha256 or "")
            or denominator is None or not denominator.is_finite() or denominator <= 0):
        return candidate
    try:
        reference = PolicyReference.model_validate(policy_context["policy_reference"])
    except (ValueError, KeyError):
        return candidate
    if (reference.scope != f"portfolio:{portfolio_id}" or reference.scope != release.get("scope")
            or reference.bundle_id != release.get("bundle_id")
            or reference.bundle_version != snapshot.policy_version
            or reference.bundle_sha256 != release.get("bundle_sha256")
            or reference.structured_sha256 != digest(snapshot)
            or snapshot.source_hashes != policy_context.get("source_hashes")):
        return candidate

    nodes = snapshot.strategic_review_scope.nodes
    # Exact ratios of the source Decimal amounts avoid rounding a strictly
    # greater deviation down to equality under the process Decimal context.
    totals = {node: sum(Fraction(row["amount_cny"]) for row in hierarchy["classified_assets"].values()
                        if row["bucket"] in STRATEGIC_BUCKETS[node]) for node in nodes}
    base = Fraction(denominator)
    if sum(totals.values()) != base:
        return candidate
    rule = next(rule for rule in snapshot.rules if rule.rule_id == "allocation.deviation")
    hits = [node for node, target in nodes.items()
            if abs(totals[node] - base * Fraction(target)) > base * Fraction(rule.threshold)]
    identity = dict(policy_reference=reference.model_dump(), snapshot_sha256=snapshot_sha256,
                    rule_id=candidate["rule_id"])
    return {
        **candidate,
        **identity,
        "policy_schema_version": snapshot.schema_version,
        "status": "HUMAN_REVIEW" if hits else "NO_RULE_TRIGGERED",
        "candidate_hit_classes": hits,
        "triggered_classes": hits,
        "review_id": digest(identity) if hits else None,
    }


def build_hierarchy(snapshot: Snapshot, *, denominator, included, coverage):
    """Use B2-qualified CNY amounts and canonical economic classes from one freeze."""
    allocation = snapshot.allocation
    direct_symbols = set()
    direct_targets = {}
    for name, target in allocation.direct_equity.items():
        if name == 'unallocated':
            continue
        label, _, qualifier = name.partition('（')
        combined = re.fullmatch(r'([A-Z][A-Z0-9.-]*(?:、[A-Z][A-Z0-9.-]*)+)合并）', qualifier)
        symbols = combined[1].split('、') if combined else label.split('/')
        direct_symbols.update(symbols)
        identities = [IDENTITIES.get((ticker + ':US', 'USD')) for ticker in symbols]
        issuer = identities[0] if identities and all(key == identities[0] for key in identities) else None
        direct_targets[name] = (issuer, target)
    policy_issuers = {issuer for issuer, _ in direct_targets.values() if issuer}
    direct_symbols.update(symbol.removesuffix(':US') for (symbol, currency), issuer in IDENTITIES.items()
                          if symbol.endswith(':US') and currency == 'USD' and issuer in policy_issuers)
    direct_amounts = dict.fromkeys(policy_issuers, Decimal(0))
    direct_complete = True
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
                         'evidence_sha256': digest([*classification_refs, *identity_refs])}
        amounts[bucket] += item.amount_cny
        if bucket == 'us_direct':
            issuer = direct_issuer(row)
            if issuer in direct_amounts:
                direct_amounts[issuer] += item.amount_cny
            if issuer is None:
                direct_complete = False
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
    candidate_hits = []
    for key, buckets in STRATEGIC_BUCKETS.items():
        amount = sum(amounts[bucket] for bucket in buckets)
        target = targets[key]
        strategic[key] = _gap(amount if complete else None, denominator, target)
        # Compare CNY amounts before division so equality cannot round into a trigger.
        if complete and abs(amount - denominator * target) > denominator * rule.threshold:
            candidate_hits.append(key)

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
    issuer_ids = [issuer for issuer, _ in direct_targets.values()]
    direct_qualified = complete and direct_complete and None not in issuer_ids and len(set(issuer_ids)) == len(issuer_ids)
    direct_rows = {
        name: {**_gap(direct_amounts[issuer] if direct_qualified else None, denominator, target),
               'issuer_id': issuer}
        for name, (issuer, target) in direct_targets.items()
    }
    unallocated = allocation.direct_equity.get('unallocated')
    return {
        'direct_equity': {'status': 'QUALIFIED' if direct_qualified else 'UNKNOWN',
                          'issuers': direct_rows, 'unallocated': {
                              'status': 'UNASSIGNED', 'target_kind': 'NEUTRAL_POLICY',
                              'target_ratio': str(unallocated),
                          } if unallocated is not None else None},
        'status': 'QUALIFIED' if complete else 'PARTIAL' if assigned else 'UNKNOWN',
        'strategic': strategic, 'secondary': secondary,
        'etf': {'status': ('QUALIFIED' if etf_base else 'NO_ETF_HOLDINGS') if complete else 'UNKNOWN',
                'denominator_cny': str(etf_base) if complete else None,
                'other_etf_amount_cny': str(other_etf) if complete else None, 'products': etf_rows},
        'strategic_review': {
            # The ACTIVE contract has the rule scope but no authorized node IDs.
            'status': 'RULE_SCOPE_UNVERIFIED', 'triggered_classes': [],
            'candidate_hit_classes': candidate_hits if complete else None,
            'rule_id': rule.rule_id, 'operator': rule.operator,
            'threshold_pp': str(rule.threshold * 100), 'source_clause': rule.source_clause.model_dump(),
        },
        'classified_assets': assigned, 'unclassified_asset_refs': unknown,
        'unclassified_amount_cny': str(unclassified), 'execution_authorized': False,
    }
