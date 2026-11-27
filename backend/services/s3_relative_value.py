"""Policy-scoped S3 and fixed-endpoint repricing over verified professional artifacts."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, localcontext
from pathlib import Path
import json
import re
from time import monotonic
import uuid

from backend.services import quality_valuation_research as archive
from backend.services.instruments.issuers import ISSUERS, VERSION as ISSUER_VERSION, policy_target_identity
from backend.services.market_data import quality_valuation as engine
from backend.services.s3_pool_research import PoolRequest, _error_code

VERSION = 's3-relative-value.fixed-endpoint.v1.1'
METHOD = 'backend/services/s3_relative_value.py'
AV_URL = 'https://www.alphavantage.co/query'
MAX_REVIEW_AGE = timedelta(days=30)


def policy_pool(context):
    if context.get('status') != 'ACTIVE':
        raise ValueError('S3_ACTIVE_POLICY_REQUIRED')
    snapshot = context['snapshot']
    allocation = snapshot['allocation']
    targets = {}
    for label, weight in allocation['direct_equity'].items():
        if label == 'unallocated':
            continue
        symbols, issuer = policy_target_identity(label)
        weight = engine.dec(weight)
        if not issuer or issuer in targets or ISSUERS[issuer][1] != 'US' or weight < 0:
            raise ValueError('S3_POLICY_ISSUER_UNRESOLVED_OR_DUPLICATE')
        # Deterministic representative listed class, never transfer its numbers to the other class.
        targets[issuer] = dict(symbol=sorted(symbols)[0]+':US', classes=sorted(symbols),
                               target=str(weight), issuer=ISSUERS[issuer][0])
    if not 1 <= len(targets) <= 20 or len({r['symbol'] for r in targets.values()}) != len(targets):
        raise ValueError('S3_POLICY_POOL_INVALID')
    return dict(reference=context['policy_reference'], effective_at=context['effective_at'],
                denominator='QUALIFIED_INVESTABLE_ASSETS_POLICY_ONLY',
                budget=allocation['neutral']['us_direct'], issuers=targets,
                issuer_registry_version=ISSUER_VERSION)


def source(root, row, url, earliest, cutoff):
    path = archive.safe_file(root, row['path'])
    timestamp = engine.stamp(row['fetched_at'])
    if timestamp is None or not earliest <= timestamp <= cutoff or row['url'] != url or archive.file_hash(path) != row['sha256']:
        raise ValueError('S3_REPRICE_SOURCE_CLOCK_OR_IDENTITY_REJECTED')
    return path.read_bytes(), row['fetched_at']


def check_recent_filings(recent, parent_at):
    """Use all recent SEC form dates, including forms outside the financial parser."""
    if not isinstance(recent, dict):
        raise ValueError('S3_REPRICE_FILING_CLOCK_UNQUALIFIED')
    accessions, dates, accepted = (recent.get(key) for key in
        ('accessionNumber', 'filingDate', 'acceptanceDateTime'))
    if not all(isinstance(rows, list) for rows in (accessions, dates, accepted)):
        raise ValueError('S3_REPRICE_FILING_CLOCK_UNQUALIFIED')
    if not accessions or len(accessions) != len(dates) or len(dates) != len(accepted):
        raise ValueError('S3_REPRICE_FILING_CLOCK_UNQUALIFIED')
    for day, accepted_at in zip(dates, accepted, strict=True):
        filed = engine.stamp(day)
        if filed is None or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(day)):
            raise ValueError('S3_REPRICE_FILING_CLOCK_UNQUALIFIED')
        if filed.date() > parent_at.date():
            raise ValueError('S3_REPRICE_NEW_FILING_REQUIRES_RESEARCH')
        if filed.date() == parent_at.date():
            acceptance = engine.stamp(accepted_at)
            if acceptance is None or 'T' not in str(accepted_at):
                raise ValueError('S3_REPRICE_FILING_CLOCK_UNQUALIFIED')
            if acceptance > parent_at:
                raise ValueError('S3_REPRICE_NEW_FILING_REQUIRES_RESEARCH')


def qualify_price(root, manifest, payload, card, cutoff, *, live):
    from backend.services.s3_method_research import identity_projection
    from backend.services.s3_professional_research import registry
    from research_v2 import sec_disclosures as sec, official_disclosures as official

    symbol = card['symbol']
    earliest = cutoff - timedelta(days=2)
    if live:
        earliest = max(engine.stamp(payload['result']['decision_at']), earliest)
    entry = manifest['companies'][symbol]
    directory = source(root, entry['directory'], sec.DIRECTORY, earliest, cutoff)
    if card.get('valuation_method') == 'BRK_CLASS_B_OPERATING_EARNINGS_V1':
        from backend.services.s3_berkshire_research import frozen_profile, check_identity
        profile = frozen_profile()
        sub = source(root, entry['submissions'], 'https://data.sec.gov/submissions/CIK0001067983.json',earliest,cutoff)
        check_identity(symbol,directory,sub,profile,cutoff)
        return listed_close(root,entry,symbol,profile['shares_as_of'],earliest,cutoff)
    reviewed = registry(relative=payload['professional_context'].get('relative_policy',False))['companies'][symbol]
    sub = source(root, entry['submissions'], 'https://data.sec.gov/submissions/CIK'+reviewed['cik']+'.json',
                 earliest, cutoff)
    issuer, _, _ = identity_projection(symbol, directory, sub)
    if issuer['cik'] != reviewed['cik'] or issuer.get('registered_tickers') != [symbol[:-3]]:
        raise ValueError('S3_REPRICE_LISTED_CLASS_CHANGED')
    submissions = json.loads(sub[0])
    filings = sec.discover_filings(issuer, submissions)
    latest = {form: next((row['accession'] for row in filings if row['form'] == form), None)
              for form in ('10-K', '10-Q', '8-K')}
    parent_at = require_review_clock(reviewed.get('reviewed_at', payload['professional_context']['reviewed_at']),cutoff)
    # Also reject new amendments, registration, proxy and ownership events.
    # Use the raw recent list so a filing unrecognized by discover_filings cannot pass silently.
    if latest != reviewed['latest_reviewed_filings']:
        raise ValueError('S3_REPRICE_NEW_FILING_REQUIRES_RESEARCH')
    check_recent_filings(submissions['filings']['recent'], parent_at)

    if reviewed.get('issuer_context_scope') == 'SEC_ONLY':
        return dict(listed_close(root, entry, symbol, card['price_qualification']['eps_interval_start'],
                                 earliest, cutoff), issuer_context_scope='SEC_RECENT_FILINGS_ONLY')

    expected_ir = next(x for x in reviewed['sources'] if x['role'] == 'OFFICIAL_IR_CONTEXT')
    ir = entry['reprice_ir']
    body, _ = source(root, ir, expected_ir['url'], earliest, cutoff)
    official.verify_current_issuer_context(body, ir, issuer, cutoff.isoformat())
    if official.research_context_fingerprint(body, issuer['cik']) != expected_ir['visible_context_sha256']:
        raise ValueError('S3_REPRICE_CONTEXT_CHANGED_REQUIRES_RESEARCH')

    return dict(listed_close(root, entry, symbol, card['price_qualification']['eps_interval_start'],
                            earliest, cutoff), ir_sha256=ir['sha256'])


def listed_close(root, entry, symbol, interval_start, earliest, cutoff):
    from backend.services.execution_plan.kline_provider import decode_av_daily, decode_av_splits
    from backend.services.market_data.evidence import assess, mapping
    from backend.services.market_data.financial_research import build
    from backend.services.market_data.technical_evidence import from_bars
    import exchange_calendars as calendars

    price, splits = entry['price'], entry['splits']
    for record, function in ((price, 'TIME_SERIES_DAILY'), (splits, 'SPLITS')):
        if record.get('function') != function or record.get('symbol') != symbol:
            raise ValueError('S3_REPRICE_PRICE_OR_SPLIT_IDENTITY_REJECTED')
    bars = decode_av_daily(json.loads(source(root, price, AV_URL, earliest, cutoff)[0]),
                           symbol, count=300, strict_identity=True)
    technical = from_bars(symbol, bars, at=cutoff)
    if not technical:
        raise ValueError('S3_REPRICE_CLOSED_PRICE_MISSING')
    technical = mapping(technical)
    technical['provenance'].update(fetched_at=price['fetched_at'], source_url=AV_URL,
                                  raw_sha256=price['sha256'], price_basis='RAW_AS_TRADED_NOT_ADJUSTED')
    qualified = build(assess({'technical': technical}, symbol=symbol, at=cutoff))['records']
    observed = next((row for row in qualified if row['field'] == 'observed_price'), None)
    calendar = calendars.get_calendar('XNYS')
    schedule = calendar.schedule.loc[(cutoff - timedelta(days=14)).date():cutoff.date()]
    latest_close = schedule[schedule['close'] <= cutoff].index[-1].date().isoformat()
    if not observed or observed['currency'] != 'USD' or technical['data_as_of'] != latest_close:
        raise ValueError('S3_REPRICE_PRICE_STALE_OR_UNQUALIFIED')
    events = decode_av_splits(json.loads(source(root, splits, AV_URL, earliest, cutoff)[0]), symbol)
    if any(e['effective_date'] >= interval_start and
           engine.dec(e['split_factor']) != 1 for e in events):
        raise ValueError('S3_REPRICE_SPLIT_REQUIRES_RESEARCH')
    return dict(price=str(observed['values']['observed_price']), price_date=latest_close, currency='USD',
                raw_price_sha256=price['sha256'], split_sha256=splits['sha256'],
                filings_sha256=entry['submissions']['sha256'],
                fetched_at=price['fetched_at'], price_basis='RAW_AS_TRADED_NOT_ADJUSTED')


def require_review_clock(value, cutoff):
    reviewed = engine.stamp(value)
    if not reviewed or reviewed > cutoff:
        raise ValueError('S3_REPRICE_REVIEW_CLOCK_UNQUALIFIED')
    if cutoff - reviewed > MAX_REVIEW_AGE:
        raise ValueError('S3_REPRICE_REVIEW_WINDOW_EXPIRED')
    return reviewed


def endpoint(start):
    try:
        return start.replace(year=start.year + 5)
    except ValueError:  # Feb 29 to a non-leap year.
        return start.replace(year=start.year + 5, day=28)


def reprice_case(old, old_price, price, remaining, rate):
    if not old:
        return None
    if engine.dec(old['dividends_cash']) != 0:
        raise ValueError('S3_REPRICE_NONZERO_PAYOUT_UNSUPPORTED')
    if price == old_price and remaining == 5:
        return deepcopy(old)
    with localcontext() as ctx:
        ctx.prec = 34
        wealth = engine.dec(old['terminal_wealth'])
        pv = wealth / (1 + rate) ** remaining
        # Implied growth refers to the ORIGINAL operating window. Today's
        # required wealth is compounded only to the unchanged terminal date.
        ratio = price / old_price * (1 + rate) ** (remaining - 5)
        out = dict(old, total_return=engine.text(wealth / price - 1),
                   annualized_return=engine.text((wealth / price) ** (1 / remaining) - 1),
                   conditional_value=engine.text(pv), margin_to_conditional_value=engine.text(1 - price / pv),
                   implied_terminal_eps_no_dividends=engine.text(
                       engine.dec(old['implied_terminal_eps_no_dividends']) * ratio))
        factor = ratio ** (Decimal(1) / 5)
        out['implied_earnings_growth_no_dividends'] = engine.text(
            (1 + engine.dec(old['implied_earnings_growth_no_dividends'])) * factor - 1)
        if 'implied_revenue_cagr_for_required_return' in old:
            out['implied_revenue_cagr_for_required_return'] = engine.text(
                (1 + engine.dec(old['implied_revenue_cagr_for_required_return'])) * factor - 1)
        return out


def reprice_at(card, case, old_price, price, remaining, rate, cutoff):
    if not case or 'cash_return_contract' not in card:
        return reprice_case(case, old_price, price, remaining, rate)
    from backend.services.s3_holder_returns import settle
    contract = card['cash_return_contract']
    return settle(case,price,rate,engine.stamp(contract['start']),engine.stamp(contract['end']),
                  cutoff,contract['schedule'])


def rank_comparisons(cards, request):
    from backend.services import s3_holder_returns as holder
    tagged = any('holder_return_scope' in c for c in cards)
    if tagged:
        for card in cards:
            card['ranking_unstable'] = None
            for row in [*card.get('sensitivity',[]),*card.get('joint_sensitivity',[])]:
                row.pop('after', None)
                row.pop('method_blockers_after', None)
                row.pop('sensitivity', None)
    cohort = holder.scope(cards)
    engine._rank(cohort + [c for c in cards if not c['scenarios']], request)
    for card in cards:
        if card.get('scenarios') and card.get('holder_return_scope') == holder.BOUNDED:
            engine._sensitivity_qualification(card)
            card.update(group=None, rank=None, rank_reason=card['holder_return_scope_reason'])
            for key in ('relative_return','tie_reason','classification_reasons'):
                card.pop(key, None)
    for card in cards:
        if not card['scenarios']:
            card.pop('relative_return', None)
            card.update(group=None, rank=None, rank_reason=card['failure_reason'])
    comparable = cohort
    if len(comparable) == 1 and comparable[0].get('holder_return_scope') == holder.FULL:
        comparable[0]['rank'] = None
    for card in comparable:
        base = engine.dec(card['scenarios']['base']['annualized_return'])
        cautious = engine.dec(card['scenarios']['cautious']['total_return'])
        card['relative_return'] = [{
            'against': other['symbol'],
            'annualized_base_difference': engine.text(base - engine.dec(other['scenarios']['base']['annualized_return'])),
            'cautious_total_difference': engine.text(cautious - engine.dec(other['scenarios']['cautious']['total_return'])),
            'within_return_tie_band': abs(base - engine.dec(other['scenarios']['base']['annualized_return'])) <= request.tie_band,
        } for other in comparable if other is not card]
    return len(cohort)


def relative_stress(cards, request, *, remaining=Decimal(5)):
    """Re-rank a finite common and issuer-specific stress set; assign no probabilities."""
    from backend.services import s3_holder_returns as holder
    eligible = holder.scope(cards)
    if len(eligible) < 2:
        return dict(status='INSUFFICIENT_COMPARABLE_ISSUERS', stable=None, cells=[], pairs=[])
    grid = {}
    missing = []
    for card in eligible:
        symbol = card['symbol']
        points = {}
        for row in card.get('joint_sensitivity', []):
            if row['result']:
                axis = row.get('stress_axis','REVENUE_PE')
                points[(axis,row['revenue_growth_shift'],row['terminal_pe_shift'])] = row['result']
        for shift in ('-.02','.02'):
            try:
                params = dict(card['operating_assumptions']['base'])
                params['terminal_operating_margin'] = engine.dec(params['terminal_operating_margin']) + Decimal(shift)
                if not 0 < params['terminal_operating_margin'] <= Decimal('.8'):
                    raise ValueError('S3_MARGIN_STRESS_OUT_OF_RANGE')
                base = card['operating_baseline']
                current = card.get('current_price_evidence')
                historical = card.get('historical_cross_checks',card.get('cross_checks',{}))
                price = engine.dec(historical['observed_price']['values']['observed_price'])
                rate = engine.dec(card['assumptions']['required_return'])
                eps = base['eps']['values']['diluted_eps_ttm']
                scenario, _ = engine.operating_scenario(eps, price, base, params, rate)
                if contract := card.get('cash_return_contract'):
                    scenario = holder.settle(scenario,price,rate,engine.stamp(contract['start']),
                        engine.stamp(contract['end']),engine.stamp(contract['start']),contract['schedule'])
                if current:
                    scenario = reprice_at(card, scenario, price, engine.dec(current['price']), remaining, rate,
                                          engine.stamp(current['decision_at']) if contract else None)
                points[('OPERATING_MARGIN',shift,'0')] = scenario
            except (ValueError, KeyError, TypeError, ArithmeticError):
                continue
        grid[symbol] = points
        expected = 9 if card.get('valuation_method') == 'BRK_CLASS_B_OPERATING_EARNINGS_V1' else 11
        if len(points) < expected:
            missing.append(symbol)
    baseline = {c['symbol']: [c['group'],c['rank']] for c in eligible}
    base_return = {c['symbol']: engine.dec(c['scenarios']['base']['annualized_return']) for c in eligible}
    cases = []
    for key in sorted(set().union(*grid.values())):
        variants = {symbol: v[key] for symbol,v in grid.items() if key in v}
        if len(variants) > 1:
            cases.append(('COMMON' if len(variants)==len(grid) else 'COMMON_METHOD_SUBSET', key, variants))
    for shift in ('-.2','.2'):
        variants = {symbol: value for symbol,points in grid.items()
                    for (axis,growth,multiple),value in points.items()
                    if axis in ('REVENUE_PE','OPERATING_EARNINGS_PE') and growth=='0' and multiple==shift}
        if len(variants) > 1:
            cases.append(('COMMON_EXIT_MULTIPLE', ('EXIT_MULTIPLE','0',shift), variants))
    cases.extend((symbol,key,{symbol: scenario}) for symbol,points in grid.items()
                 for key, scenario in sorted(points.items()))
    outcomes = []
    for scope, key, variants in cases:
        trial = [dict(card, scenarios=dict(card['scenarios'],
                 base=variants.get(card['symbol'],card['scenarios']['base']))) for card in eligible]
        engine._rank(trial,request)
        outcomes.append(dict(scope=scope,axis=list(key),
            ranks={c['symbol']: [c['group'],c['rank']] for c in trial},
            returns={c['symbol']: c['scenarios']['base']['annualized_return'] for c in trial}))
    pairs = []
    for i,left in enumerate(base_return):
        for right in list(base_return)[i+1:]:
            spread = base_return[left]-base_return[right]
            spreads = [engine.dec(o['returns'][left])-engine.dec(o['returns'][right]) for o in outcomes]
            low,high = min([spread]+spreads),max([spread]+spreads)
            if low < -request.tie_band and high > request.tie_band:
                status = 'REVERSAL'
            elif low > request.tie_band or high < -request.tie_band:
                status = 'TESTED_ADVANTAGE' if not missing else 'PARTIAL_GRID'
            else:
                status = 'NO_ROBUST_ADVANTAGE'
            pairs.append(dict(left=left,right=right,baseline_spread=engine.text(spread),
                min_spread=engine.text(low),max_spread=engine.text(high),status=status))
    return dict(status='PARTIAL_STRESS_GRID' if missing else 'FINITE_STRESS_GRID',
                stable=None if missing else all(o['ranks']==baseline for o in outcomes),
                missing=missing, cells=outcomes, pairs=pairs,
                scope='BASE_RETURN_PERTURBATION_WITH_FIXED_CAUTIOUS_CASE_AND_EVIDENCE; NO_PROBABILITIES')


def calculate(payload, root, manifest, cutoff, *, live):
    with localcontext() as ctx:
        ctx.prec = 34
        return _calculate(payload, root, manifest, cutoff, live=live)


def _calculate(payload, root, manifest, cutoff, *, live):
    old = payload['result']
    start = engine.stamp(old['decision_at'])
    end = endpoint(start)
    if not start <= cutoff < end:
        raise ValueError('S3_REPRICE_DECISION_CLOCK_REJECTED')
    remaining = Decimal(5) * Decimal(str((end-cutoff).total_seconds())) / Decimal(str((end-start).total_seconds()))
    result = deepcopy(old)
    cards = result['cards']
    for card in cards:
        symbol = card['symbol']
        card['historical_quality_as_of'] = old['decision_at']
        try:
            if not card['scenarios']:
                raise ValueError(card.get('failure_reason') or 'S3_REPRICE_PROFILE_UNAVAILABLE')
            context = payload['professional_context']
            review_clock = context['companies'].get(symbol, {}).get('reviewed_at', context['reviewed_at'])
            card['operating_reviewed_at'] = require_review_clock(review_clock, cutoff).isoformat()
            observation = qualify_price(root, manifest, payload, card, cutoff, live=live)
            if card.get('nonoperating_asset_stress'):
                card['nonoperating_asset_stress']['status'] = 'HISTORICAL_NONOPERATING_STRESS_NOT_REPRICED'
            old_price = engine.dec(card['cross_checks']['observed_price']['values']['observed_price'])
            price = engine.dec(observation['price'])
            required = engine.dec(card['assumptions']['required_return'])
            for name, case in card['scenarios'].items():
                card['scenarios'][name] = reprice_at(card, case, old_price, price, remaining, required, cutoff)
            for key in ('sensitivity', 'joint_sensitivity'):
                for variant in card[key]:
                    rate = engine.dec(variant['required_return']) if key == 'sensitivity' else required
                    variant['result'] = reprice_at(card, variant['result'], old_price, price, remaining, rate, cutoff)
                    variant.pop('after', None)
                    variant.pop('method_blockers_after', None)
                    if key == 'joint_sensitivity':
                        variant.pop('sensitivity', None)
            complete = len(card['joint_sensitivity']) == 9 and all(v['result'] for v in card['joint_sensitivity'])
            robust = complete and all(engine.dec(v['result']['annualized_return']) >= required
                                      for v in card['joint_sensitivity'])
            card['joint_qualification'].update(complete=complete, all_above_required=robust)
            card['research_method_blockers'] = ([] if robust else
                ['JOINT_STRESS_RETURN_THRESHOLD_CROSSING' if complete else 'JOINT_SENSITIVITY_INCOMPLETE'])
            values = [engine.dec(x['conditional_value']) for x in card['scenarios'].values()]
            card['conditional_value_range'] = [engine.text(min(values)), engine.text(max(values))]
            # Current quotes are distinct from price-dependent comparisons in the parent.
            if card.get('cash_return_contract'):
                observation['decision_at'] = cutoff.isoformat()
            card['current_price_evidence'] = observation
            if card.get('valuation_method') == 'BRK_CLASS_B_OPERATING_EARNINGS_V1':
                card['current_operating_earnings_multiple'] = engine.text(
                    price / engine.dec(card['operating_earnings_bridge']['earnings_per_b']))
            else:
                eps = engine.dec(card['cross_checks']['diluted_eps_ttm']['values']['diluted_eps_ttm'])
                card['current_pe'] = engine.text(price / eps)
            card['price_qualification'] = dict(observation, eps_interval_start=card['price_qualification']['eps_interval_start'])
            card['comparison_status'] = ('BOUNDED_NOT_FORMALLY_COMPARABLE'
                                         if card.get('holder_return_scope') == 'BOUNDED_CONDITIONAL' else 'COMPARABLE')
            card['profile_reuse_status'] = 'REQUALIFIED_WITHIN_OBSERVED_SOURCE_SCOPE'
        except (ValueError, KeyError, TypeError, ArithmeticError, OSError, IndexError) as exc:
            reason = _error_code(exc, 'S3_REPRICE_EVIDENCE_UNAVAILABLE')
            card.update(scenarios={}, sensitivity=[], joint_sensitivity=[], rank=None, group=None,
                        failure_reason=reason, comparison_status='NOT_COMPARABLE', valuation_status='UNKNOWN',
                        profile_reuse_status=('REQUIRES_RESEARCH' if 'RESEARCH' in reason or 'EXPIRED' in reason
                                              else 'UNKNOWN'))
            if 'holder_return_scope' in card:
                card['holder_return_scope'] = 'UNKNOWN'
                card['holder_return_scope_reason'] = reason
                if card.get('nonoperating_asset_stress'):
                    card['nonoperating_asset_stress']['status'] = 'HISTORICAL_NONOPERATING_STRESS_NOT_REPRICED'
                if card.get('economic_claim_states'):
                    card['economic_claim_states']['reuse_status'] = 'HISTORICAL_TERMS_NOT_REVALIDATED'
                if card.get('enterprise_research'):
                    card['enterprise_research']['status'] = 'HISTORICAL_ENTERPRISE_SCENARIOS_NOT_REVALIDATED'
            if 'current_price_evidence' in card:
                card['historical_price_evidence'] = card.pop('current_price_evidence')
            for key in ('conditional_value_range', 'assumptions', 'joint_qualification', 'formula',
                        'current_operating_earnings_multiple', 'current_pe', 'price_qualification'):
                card.pop(key, None)
        if 'current_price_evidence' not in card:
            card.pop('price_qualification', None)
        card['historical_cross_checks'] = card.pop('cross_checks', {})
        if 'reference_close' in card:
            card['historical_reference_close'] = card.pop('reference_close')
        for key in ('p1_comparison', 'valuation_context', 'rank_reason', 'tie_reason'):
            card.pop(key, None)
        card['ranking_unstable'] = None
    request = PoolRequest.model_validate(payload['request'])
    count = rank_comparisons(cards, request)
    if 'fully_comparable_count' in result:
        from backend.services.s3_holder_returns import counts
        result.update(counts(cards))
        result['bounded_valuations'] = [dict(symbol=c['symbol'],reason=c['holder_return_scope_reason'])
            for c in cards if c.get('holder_return_scope')=='BOUNDED_CONDITIONAL']
    stress = relative_stress(cards, request, remaining=remaining)
    historical_eligibility = result.pop('professional_qualification', None)
    result.update(original_professional_qualification=historical_eligibility, decision_at=cutoff.isoformat(), original_decision_at=start.isoformat(),
                  forecast_endpoint=end.isoformat(), remaining_years=str(remaining), method_version=VERSION,
                  status='ok' if count == len(cards) else 'partial', comparable_count=count,
                  relative_stress=stress,
                  ranking=dict(stable=stress['stable'], changes_status=stress['status'],
                               previous={c['symbol']: [c['group'], c['rank']] for c in old['cards']},
                               current={c['symbol']: [c['group'], c['rank']] for c in cards}),
                  not_comparable=[dict(symbol=c['symbol'], reason=c['failure_reason']) for c in cards if not c['scenarios']])
    return result


def verified_parent(root, artifact_id):
    from backend.services.s3_operating_research import replay
    if not re.fullmatch(r'[a-f0-9]{64}', artifact_id):
        raise ValueError('S3_ARTIFACT_INVALID')
    path = archive.safe_file(root, f'{artifact_id}/bundle.json')
    if path.parent.is_symlink():
        raise ValueError('S3_ARTIFACT_INVALID')
    parent = archive.read_bundle(path)
    if (parent['artifact_id'] != artifact_id or not parent['payload'].get('professional_context')
            or not parent['payload']['entry_provenance'].get('policy_pool')):
        raise ValueError('S3_REPRICE_REVIEWED_POLICY_PARENT_REQUIRED')
    replay(path)
    return parent


def capture(root, symbols, *, relative=False):
    from backend.services.s3_pool_capture import capture_pool, now
    from backend.services.s3_professional_research import registry
    from research_v2 import official_disclosures as official

    path = capture_pool(symbols, root, prices_only=True, listed_prices=relative)
    manifest = json.loads(path.read_text())
    for symbol in symbols:
        try:
            reviewed = registry(relative)['companies'][symbol]
            if reviewed.get('issuer_context_scope') == 'SEC_ONLY':
                continue
            cik = reviewed['cik']
            manifest['provider_requests']['issuer_ir'] = manifest['provider_requests'].get('issuer_ir', 0) + 1
            body, receipt = official.fetch_current_issuer_context(cik)
            filename = symbol[:-3]+'-reprice-ir.html'
            (root/filename).write_bytes(body)
            manifest['companies'][symbol]['reprice_ir'] = dict(receipt, path=filename)
        except (KeyError, ValueError, OSError):
            continue
    manifest['finished_at'] = now()
    path.write_text(json.dumps(manifest))


def run(root, parent_id, policy, *, source_root=None, frozen_at=None):
    from backend.services.s3_research_entry import project
    from backend.services.s3_pool_capture import now
    started = monotonic()
    parent = verified_parent(root, parent_id)
    p = parent['payload']
    provenance = p['entry_provenance']
    if policy != provenance['policy_pool']:
        raise ValueError('S3_REPRICE_POLICY_CHANGED_REBUILD_POOL')
    eligible = [c['symbol'] for c in p['result']['cards'] if c['scenarios']]
    live = source_root is None
    if live:
        if provenance['synthetic']:
            raise ValueError('S3_REPRICE_SYNTHETIC_PARENT_FOR_LIVE')
        source_root = root / '.captures' / uuid.uuid4().hex
        if not eligible:
            raise ValueError('S3_REPRICE_NO_ELIGIBLE_PROFILES')
        capture(source_root, eligible, relative=p['professional_context'].get('relative_policy',False))
    source_root = Path(source_root)
    manifest = archive._load(source_root, 'manifest.json')
    cutoff = engine.stamp(frozen_at or now())
    if live and frozen_at is not None:
        raise ValueError('S3_REPRICE_LIVE_CLOCK_OVERRIDE_FORBIDDEN')
    if (manifest.get('schema_version') != 's3-d.sources.v1' or cutoff is None
            or engine.stamp(manifest['finished_at']) > cutoff):
        raise ValueError('S3_REPRICE_CAPTURE_AFTER_DECISION')
    result = calculate(p, source_root, manifest, cutoff, live=live)
    new_methods = (METHOD, 'scripts/s3_relative_value_verify.py', 'backend/services/instruments/issuers.py')
    files = {'methods/'+name: archive.ROOT/name for name in new_methods}
    files['refresh/manifest.json'] = source_root/'manifest.json'
    for e in manifest['companies'].values():
        for kind in ('directory', 'submissions', 'price', 'splits', 'reprice_ir'):
            ref = e.get(kind, {})
            if ref.get('path'):
                files['refresh/'+ref['path']] = archive.safe_file(source_root, ref['path'])
    method_files = dict(p['method_files'], **{name: archive.file_hash(archive.ROOT/name) for name in new_methods})
    output = dict(request=dict(p['request'], source_manifest_sha256=archive.file_hash(source_root/'manifest.json')),
                  assessments={}, parent_artifact_id=parent_id, parent_result_sha256=archive.digest(p['result']),
                  entry_provenance=dict(provenance, operation='price_revaluation'), method_files=method_files,
                  source_files={n: archive.file_hash(f) for n, f in files.items()}, result=result,
                  view_hashes=archive.view_hashes(result),
                  execution=dict(mode='PUBLIC_PRICE_AND_ELIGIBILITY' if live else 'FROZEN_INPUT_NO_PROVIDER',
                                 llm_calls=0, full_l1_calls=0, full_research_fallback=False,
                                 companyfacts_requests=0, original_financial_document_requests=0,
                                 provider_call_counts=manifest['provider_requests'] if live else
                                     {'sec': 0, 'av': 0, 'issuer_ir': 0},
                                 accounting_scope='SEC_OWNER_CALLS_AV_TRANSPORT_ATTEMPTS_IR_OWNER_CALLS',
                                 seconds_to_calculation=round(monotonic()-started, 3)))
    bundle = dict(artifact_id=archive.digest(output), payload=output)
    target = archive.save_bundle(root, bundle, files)
    return project(bundle, verify(root, target/'bundle.json', bundle))


def verify(root, path, bundle):
    p = bundle['payload']
    for name, expected in p['method_files'].items():
        if archive.file_hash(archive.ROOT/name) != expected:
            raise ValueError('METHOD_DRIFT_REPLAY_REQUIRES_ORIGINAL_VERSION')
    parent = verified_parent(root, p['parent_artifact_id'])
    if archive.digest(parent['payload']['result']) != p['parent_result_sha256']:
        raise ValueError('S3_REPRICE_PARENT_RESULT_MISMATCH')
    sources = path.parent/'sources/refresh'
    manifest = archive._load(sources, 'manifest.json', p['request']['source_manifest_sha256'])
    expected = calculate(parent['payload'], sources, manifest, engine.stamp(p['result']['decision_at']),
                         live=p['execution']['mode'] == 'PUBLIC_PRICE_AND_ELIGIBILITY')
    if archive.digest(expected) != archive.digest(p['result']):
        raise ValueError('RECOMPUTATION_DIFFERENCE')
    return dict(verified=True, artifact_id=bundle['artifact_id'], result_sha256=archive.digest(expected))


def product_view(result, policy):
    """Deterministic report projection. Research evidence stays on the original cards."""
    cards = {c['symbol']: c for c in result['cards']}
    lines = ['# 美国核心个股相对价值比较', '',
             f"比较时点 {result['decision_at']}。合格可比 {result['comparable_count']}/{len(cards)} 家。",
             '公司质量与价格机会分列。分档供研究使用，实际配置金额和执行资格留给组合评估。']
    if 'fully_comparable_count' in result:
        lines[2] = (f"比较时点 {result['decision_at']}。完整条件回报 {result['fully_comparable_count']}/{len(cards)} 家，"
                    f"限定条件回报 {result['bounded_count']}/{len(cards)} 家。")
    if 'forecast_endpoint' in result:
        lines += [f"保留原预测终点 {result['forecast_endpoint']}，以下回报对应剩余期限。"]
    lines += ['', '| 证券 | 政策目标 | 合格价格 / 日期 | 谨慎 / 基准 / 乐观条件年化 | 结果 |',
              '|---|---|---|---|---|']
    profiles = {}
    for issuer, row in policy['issuers'].items():
        symbol = row['symbol']
        c = cards[symbol]
        scenarios = c['scenarios']
        quality = c.get('quality', {})
        price = c.get('current_price_evidence')
        if price is None and 'forecast_endpoint' not in result:
            metric = c.get('cross_checks', {}).get('observed_price') or {}
            if 'observed_price' in metric.get('values', {}):
                price = dict(price=metric['values']['observed_price'], price_date=metric.get('period'),
                             currency=metric.get('currency'))
        reference = c.get('reference_close')
        formatted = f"{price['price']} / {price['price_date']}" if price else 'UNKNOWN'
        if not price and reference:
            formatted = f"{reference['price']} / {reference['price_date']}（仅行情）"
        returns = ' / '.join(f"{engine.dec(scenarios[k]['annualized_return'])*100:.2f}%"
                             for k in ('cautious', 'base', 'optimistic')) if scenarios else 'UNKNOWN'
        reason = (c['group'] or '限定估值，无正式名次') if scenarios else c.get('failure_reason', 'INSUFFICIENT_EVIDENCE')
        lines.append(f"| {symbol} | {engine.dec(row['target'])*100:g}% | {formatted} | {returns} | {reason} |")
        dossier = result.get('quality_dossier', {}).get(symbol, {})
        profiles[symbol] = dict(issuer_id=issuer, listed_return_security=symbol, listed_classes=row['classes'],
            valuation_status=c['comparison_status'],
            **({'holder_return_scope':c['holder_return_scope'], 'holder_return_scope_reason':c.get('holder_return_scope_reason')}
               if 'holder_return_scope' in c else {}),
            valuation_method=c.get('valuation_method',dossier.get('valuation_method')),
            research_profile=dict(id=issuer, version=result.get('assumption_profile_version'),
                status=c.get('profile_reuse_status', c['comparison_status']),
                original_assumption_clock=c.get('historical_quality_as_of', result.get('research_recorded_at')),
                financial_window={k: c.get('operating_baseline', {}).get(k) for k in ('start', 'period')}, model_version=result.get('valuation_method_version'),
                invalidation_conditions=c.get('change_conditions', [])),
            quality_dimensions=dossier.get('dimensions') or {
                'business_model': dict(status='SEE_REVIEWED_SOURCE_CONTEXT' if c.get('calibration_evidence') else 'UNKNOWN'),
                'competitive_advantage': dict(status='UNKNOWN_NO_INDEPENDENT_DURABILITY_REVIEW'),
                'growth_runway': dict(status='CONDITIONAL_ASSUMPTIONS' if scenarios else 'UNKNOWN',
                                      historical_revenue=quality.get('historical_revenue_growth')),
                'earnings_and_cash': quality.get('floors', {}).get('profit_cash_support'),
                'capital_allocation': quality.get('capital_efficiency'),
                'certainty_risks_and_counterevidence': dict(warnings=quality.get('warnings', []),
                    major_risks=quality.get('major_risks', []), limitations=c.get('limitations', [])),
            },
            evidence_bound_quality=dossier,
            current_price=price,
            reference_close=reference,
            investment_opportunity=dict(group=c['group'], rank=c['rank'], status='RESEARCH_ONLY'),
            strategic_target_review=dict(status='INSUFFICIENT_FOR_POLICY_CHANGE',
                target_direction='OWNER_JUDGMENT_REQUIRED',
                evidence_gap=dossier.get('equity_bridge') if not scenarios else None,
                finite_stress=result.get('relative_stress',{}).get('status')),
            configuration_direction='CONDITIONAL_RESEARCH_ONLY_NO_PORTFOLIO_EVIDENCE')
    return '\n'.join(lines), dict(policy=policy, profiles=profiles,
        relative_stress=result.get('relative_stress'), actual_position_priority='UNKNOWN')
