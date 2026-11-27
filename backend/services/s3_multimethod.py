"""Reviewed issuer-specific extensions over the existing professional pool."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json

from backend.services.quality_valuation_research import ROOT

from backend.services.market_data.quality_valuation import dec, stamp, text
from backend.services.s3_holder_returns import BOUNDED, FULL, UNKNOWN, attach_cash
from backend.services import s3_equity_bridges as bridges, s3_listed_eps

CODE = 'backend/services/s3_multimethod.py'
BOOK = 'research_v2/s3_profiles/multimethod-v1.json'

# Human-checked historical declarations from the exact 10-K/10-Q originals
# already admitted by professional.prepare; neither value predicts future payments.
DIVIDEND_ANCHORS = {
    'AAPL:US': ('0.27', 'edd39e7620e992bf51f3a8e82e383cc3d2faf931bdebf41b2d22dd50b872d380',
                'FY2026 Form 10-K quarterly common dividend'),
    'MSFT:US': ('0.98', 'ffccb87dda210fd462f177077620c3a15436b4aabf9ee8ff6f2baf0b929ead82',
                'FY2027 Q1 Form 10-Q common dividends declared per share'),
}


def _review_sources(dossier, profile, at):
    reviewed = stamp(dossier.get('reviewed_at'))
    if not reviewed or not reviewed <= at <= reviewed+timedelta(days=30):
        return None
    sources = dossier.get('sources') or {}
    if any(sources.get(label,{}).get('source_sha256') != digest
           for label,digest in profile['sources'].items()):
        return None
    return {label:sources[label] for label in profile['sources']}


def alphabet_operating_cases(dossier, profile, at):
    """Scenario operating EBIT × EV/EBIT; the share bridge remains separate."""
    sources = _review_sources(dossier, profile, at)
    if sources is None:
        return None
    with localcontext() as ctx:
        ctx.prec = 34
        reported = profile['reported_usd_m']
        annual_revenue = dec(reported['revenue'])*Decimal(4)/3
        cases = {}
        for name, (growth, margin, multiple) in profile['cases'].items():
            sales = annual_revenue*(1+dec(growth))**5
            ebit = sales*dec(margin)
            cases[name] = dict(revenue_cagr=growth,terminal_ebit_margin=margin,terminal_ev_ebit=multiple,
                terminal_sales_usd_m=text(sales),terminal_ebit_usd_m=text(ebit),
                terminal_operating_ev_usd_m=text(ebit*dec(multiple)))
    return dict(method='GOOG_SCENARIO_EBIT_ENTERPRISE_ONLY_V1', status='ENTERPRISE_SCENARIOS_ONLY',
        reported_usd_m=reported, sources=sources, reviewed_at=dossier['reviewed_at'],
        field_locators=profile['field_locators'],
        balance_limits='Marketable assets include limited-sale SpaceX holdings. Long-term debt excludes short debt, and the quarter predates November EUR and CAD issuance.',
        assumptions='Nine-month revenue x 4/3 as scenario starting scale; ignores seasonality. Terminal EV/EBIT multiples are research stress assumptions, not market-calibrated historical facts.',
        cases=cases, terminal_EV_excludes='Cash/debt, nonoperating investments and all security settlement',
        sensitivities='Revenue growth, terminal EBIT margin and exit multiple multiply independently.',
        reversal_conditions='Rising AI cash reinvestment, traffic acquisition, Cloud profitability or legal distributions can move the bridge and discount materially.',
        blockers=['EXECUTED_ATM_AND_ORDINARY_SHARE_ROLL_FORWARD', 'COMPLETE_NET_DEBT_AND_NONOPERATING_BRIDGE',
                  'CLASS_A_C_PREFERRED_DIVIDENDS_CONVERSION_CAPPED_CALL_SETTLEMENT'])


def equity_components(symbol, dossier):
    """Evidence-bound claims states without assigning an ordinary-share value."""
    components = dossier.get('qualification_components',{})
    if components.get('status') != 'COMPONENTS_ONLY_RETURN_QUALIFICATION_WITHHELD':
        return None
    if symbol == 'GOOG:US':
        output = {}
        with localcontext() as ctx:
            ctx.prec = 34
            for name, terms in components['published_inputs']['preferred'].items():
                minimum, maximum = map(dec,(terms['conversion_min'],terms['conversion_max']))
                units = components['derived']['preferred'][name]['underlying_preferred_shares']
                output[name] = []
                for vwap in ('300','420','600'):
                    conversion = (dec(terms['liquidation_usd'])/dec(vwap)).quantize(
                        Decimal('.0001'),rounding=ROUND_HALF_UP)
                    per_preferred = max(minimum,min(maximum,conversion))
                    output[name].append(dict(hypothetical_class_vwap_usd=vwap,
                        unadjusted_common_per_preferred=str(per_preferred),
                        unadjusted_common_shares=text(Decimal(units)*per_preferred)))
        return dict(status='CONTRACT_COMPONENT_STRESS_ONLY', converted_shares_by_hypothetical_vwap=output,
            exclusions=['ANTI_DILUTION', 'COUPON_PAID_IN_SHARES', 'CAPPED_CALL_SETTLEMENT', 'FUTURE_ATM'],
            sources=components['evidence'])
    if symbol == 'META:US':
        inputs = components['published_inputs']
        return dict(status='HOLDER_PACKAGE_STATES_ONLY', forward_reference_shares=sum(inputs['forward_contracts_shares']),
            preferred=components['derived']['preferred'],
            **({'observed_preferred_option_close':inputs['preferred_option_close']}
               if 'preferred_option_close' in inputs else {}),
            cases=[
                dict(state='STANDSTILL',security_package='A', outstanding_c='NOT_ISSUED_UNDER_PROPOSAL'),
                dict(state='DISTRIBUTION_IF_EFFECTIVE_AND_HELD_THROUGH_ENTITLEMENT',
                     security_package='A_PLUS_ONE_C_PER_ELIGIBLE_A', weights='UNKNOWN_ECONOMIC_SPLIT'),
                dict(state='POST_DISTRIBUTION_NEW_A_BUYER',security_package='A',
                     prior_distribution_entitlement='NOT_INCLUDED'),
            ],
            forwarding='Physical share delivery against contract price as adjusted at settlement; the reference shares do not prove issued shares or realized proceeds.',
            additional_claims='Founder conditional dividend, preferred settlement class, litigation trigger and actual post-financing shares remain unresolved.',
            sources=components['evidence'])
    return None


def tesla_investment_stress(card, dossier, profile, at):
    sources = _review_sources(dossier, profile, at)
    if not sources:
        return None
    price = dec(card['cross_checks']['observed_price']['values']['observed_price'])
    base = dec(card['scenarios']['base']['terminal_wealth'])
    with localcontext() as ctx:
        ctx.prec = 34
        years = card['scenarios']['base']['years']
        basis_shares = dec(profile['outstanding_common_shares'])*dec(years[-1]['relative_shares'])
        rows=[]
        for net_fraction in profile['hypothetical_retention_fractions']:
            increment = dec(profile['spacex_carrying_usd'])*dec(net_fraction)/basis_shares
            rows.append(dict(net_realized_cash_fraction_of_current_carrying_value=net_fraction,
                terminal_increment_per_share_usd=text(increment),
                hypothetical_annualized_return=text(((base+increment)/price)**(Decimal(1)/5)-1)))
    return dict(status='NONOPERATING_ASSET_STRESS_ONLY_NOT_FULL_EQUITY_VALUE',sources=sources,
        reported=profile, cases=rows,
        limitations='No verified future exit price, tax, restriction release or realization. Holding proceeds until terminal is a stress assumption. Autonomy and robotics are unquantified.')


def apply(cards, dossier, professional_checks, start, end, *, current_checks=None):
    profiles = json.loads((ROOT/BOOK).read_text())

    for card in cards:
        symbol = card['symbol']
        profile = profiles.get(symbol,{})
        card['holder_return_scope'] = UNKNOWN
        if not card['scenarios']:
            issuer_dossier = dossier.get(symbol,{})
            if event := profile.get('owner_declared_cash_event'):
                proof = next((item for item in issuer_dossier.get('sources',{}).values()
                    if item.get('source_sha256') == event['source_document_sha256']),None)
                if proof:
                    card['observed_dividend'] = dict(event,source=proof,owner_version=profile['owner_source_version'],
                        scope='DECLARED_ONLY_NOT_AN_ELIGIBLE_OR_PAID_CASH_FLOW')

            if eps_profile := profile.get('listed_eps'):
                sources = _review_sources(issuer_dossier,eps_profile,start)
                if sources:
                    alternative = deepcopy(card)
                    if s3_listed_eps.build(alternative,(current_checks or {}).get(symbol,{}),eps_profile,sources,start,end):
                        _set_bounded(alternative,'LISTED_EPS_VALUE_NONOPERATING_RECOVERY_AND_CONVERSION_NOT_CLOSED')
                        alternative['listed_eps_method']['reviewed_at'] = issuer_dossier['reviewed_at']
                        alternative['owner_qualification_reason'] = card['failure_reason']
                        card.update(alternative)
                        card.pop('failure_reason',None)
                        continue

            if claims := equity_components(symbol,issuer_dossier):
                card['economic_claim_states'] = claims
            if symbol == 'GOOG:US':
                operating = alphabet_operating_cases(issuer_dossier, profiles['GOOG:US'], start)
                if operating:
                    card['enterprise_research'] = operating
                    card['failure_reason'] = 'GOOG_CLASS_C_ORDINARY_ECONOMIC_BRIDGE_UNQUALIFIED'
                    scoped_sources = _review_sources(issuer_dossier,profile['holder_bridge'],start)
                    if scoped_sources:
                        candidate = deepcopy(card)
                        if bridges.alphabet_equity(candidate,operating,claims,profile['holder_bridge'],
                                {**operating['sources'],**scoped_sources},start,end):
                            candidate['holder_equity_bridge']['reviewed_at'] = operating['reviewed_at']
                            qualify_modeled_scope(candidate,candidate['holder_equity_bridge'])
                            card.update(candidate)
                            card.pop('failure_reason',None)
            continue
        if symbol == 'TSLA:US':
            stress = tesla_investment_stress(card,dossier.get(symbol,{}),profiles[symbol],start)
            if stress:
                card['nonoperating_asset_stress'] = stress
        _set_bounded(card,'LIMITED_OPERATING_VALUE_OR_UNMODELED_MATERIAL_DISTRIBUTION')
        if symbol not in DIVIDEND_ANCHORS:
            continue
        amount, digest, locator = DIVIDEND_ANCHORS[symbol]
        evidence = bridges.evidence_by_digest(professional_checks.get(symbol,{}))
        proof = evidence.get(digest)
        if not proof:
            _set_bounded(card,'REVIEWED_COMMON_CASH_ANCHOR_UNAVAILABLE')
            continue
        if 'adjusted_quarter_sources' in profile:
            with localcontext() as context:
                context.prec=34
                bridges.normalize_openai(card, profile, evidence)
        attach_cash(card, dict(quarterly_usd=amount,locator=locator,source=proof),start,end)
        if card.get('nonoperating_normalization'):
            card['nonoperating_materiality'] = bridges.investment_materiality(card,profile,start,end)
        qualify_economic_scope(card,profile.get('closed_ordinary_scope'),evidence)


def qualify_modeled_scope(card, bridge):
    """Model coverage is independent of whether the research conclusion is stable."""
    if (bridge.get('status')!='MODELED_CONDITIONAL_ORDINARY_SCOPE'
            or not {'quarter','preferred_terms','eur_notes','cad_notes'} <= bridge['source_evidence'].keys()
            or not card.get('cash_return_contract')
            or set(card.get('scenarios',{}))!={'cautious','base','optimistic'}):
        _set_bounded(card,'ORDINARY_ECONOMIC_SCOPE_NOT_CLOSED')
        return
    card.update(holder_return_scope=FULL,comparison_status='COMPARABLE',
                holder_return_scope_reason='MATERIAL_ECONOMICS_INCLUDED_IN_CONDITIONAL_CASES')


def _set_bounded(card, reason):
    card.update(holder_return_scope=BOUNDED,comparison_status='BOUNDED_NOT_FORMALLY_COMPARABLE',
                holder_return_scope_reason=reason)


def qualify_economic_scope(card, reviewed_scope, evidence):
    """Evidence coverage, not the company ticker, controls full qualification."""
    if (not reviewed_scope or not reviewed_scope.get('sources')
            or not set(reviewed_scope['sources']) <= evidence.keys()
            or not card.get('cash_return_contract')
            or card.get('economic_claim_states')
            or (reviewed_scope.get('residual_required') and not card.get('nonoperating_materiality'))
            or (card.get('nonoperating_materiality')
                and not residual_immaterial(card['nonoperating_materiality']))):
        _set_bounded(card,'ORDINARY_ECONOMIC_SCOPE_NOT_CLOSED')
        return
    card.update(holder_return_scope=FULL,comparison_status='COMPARABLE',
        holder_return_scope_reason='REVIEWED_ORDINARY_ECONOMIC_SCOPE_CLOSED',
        reviewed_holder_economic_scope=dict(rationale=reviewed_scope['rationale'],
            sources=[evidence[d] for d in reviewed_scope['sources']]))



def residual_immaterial(chart):
    # Research gates are applied in return space. Illustrative transaction
    # stresses alone never give a reliable envelope for the actual claim.
    return (chart.get('bounded_economic_range') is True
            and chart.get('decision_threshold_crossed') is False
            and chart.get('relative_comparison_sensitive') is False
            and chart.get('cautious_downside_sensitive') is False)
