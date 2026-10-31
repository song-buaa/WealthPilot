"""S3-C offline adapter over S3-B calculations and the existing frozen archive."""
from pathlib import Path
import hashlib
import json

from pydantic import Field

from backend.services.market_data.evidence import assess, assess_quality_facts, stamp
from backend.services.market_data.method_applicability import applicability, APPLICABLE, VERSION, RETURN_BASIS
from backend.services.market_data.quality_valuation import ResearchRequest, Assumptions, compare, _facts, _quality
from backend.services.market_data.quality_evaluation import evaluate
from backend.services.market_data.financial_research import build
from backend.services import quality_valuation_research as archive


class MethodRequest(ResearchRequest):
    companies: dict[str, Assumptions | None] = Field(min_length=1, max_length=20)
    official_manifest_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


def compare_methods(raw_request, assessments):
    request = MethodRequest.model_validate(raw_request)
    eligible, withheld = {}, []
    for symbol, parameters in sorted(request.companies.items()):
        a = assessments.get(symbol, {})
        app = applicability(a)
        card = dict(symbol=symbol, status='partial', method_applicability=app,
                    comparison_status='NOT_COMPARABLE', valuation_status='INSUFFICIENT_EVIDENCE',
                    group=None, rank=None, rank_reason='Method or qualified comparable-return inputs unavailable; no adverse fundamental inference.',
                    scenarios={}, sensitivity=[], assumptions=parameters.model_dump(mode='json') if parameters else None,
                    ranking_unstable=False, execution_authorized=False,
                    configuration_priority=dict(status='UNKNOWN', reason='NO_QUALIFIED_HOLDINGS_OR_EFFECTIVE_POLICY_ATTACHED'),
                    limitations=['RESEARCH_ONLY_NOT_INVESTMENT_ADVICE', 'NORMALIZATION_UNKNOWN', 'NO_ALTERNATIVE_VALUATION_ENGINE'])
        try:
            if a.get('symbol') != symbol or stamp(a.get('as_of')) != request.decision_at:
                raise ValueError('COMPANY_IDENTITY_OR_DECISION_CLOCK_MISMATCH')
            if a.get('conflicts') or a.get('source_conflicts'):
                raise ValueError('UNRESOLVED_SOURCE_CONFLICT')
            if app['quality_screen']['status'] == APPLICABLE:
                rows = _facts(a, request.decision_at)
                derived = build(a)
                metrics = {r['field']: r for r in derived['records']}
                card['quality'] = evaluate(a, _quality(rows, metrics))
                if app['earnings_model']['status'] == APPLICABLE and parameters:
                    eligible[symbol] = parameters
                    continue
                card['cross_checks'] = {}  # Class-ambiguous EPS must not produce a PE ranking signal.
            else:
                state = app['quality_screen']['status']
                card['quality'] = dict(floors={k: dict(status=state, reason=app['quality_screen']['reason']) for k in
                    ('data_trust', 'financial_sustainability', 'profit_cash_support', 'core_business_change')},
                    priority_blockers=['QUALITY_METHOD_NOT_QUALIFIED'], major_risks=[], warnings=[],
                    normalization=dict(status='UNKNOWN', adjustments=[]),
                    capital_efficiency={k: dict(status=state) for k in ('roe', 'roic')},
                    fundamental_deterioration=dict(status='NOT_ESTABLISHED'),
                    evidence_catalog={r['record_id']: r for r in a.get('quality_field_records', [])})
            if app['earnings_model']['status'] == 'NOT_APPLICABLE':
                card['valuation_status'] = 'NOT_APPLICABLE'
        except (ValueError, KeyError, TypeError, ArithmeticError):
            card.update(status='unavailable', failure_reason='QUALIFIED_COMPANY_INPUT_REJECTED_OR_MISSING')
        withheld.append(card)
    # All numerical calculations, quality gates, sensitivity and ranking remain
    # the original implementation; ineligible firms never enter that engine.
    if eligible:
        legacy_request = request.model_dump(mode='json', exclude={'official_manifest_sha256'})
        legacy_request['companies'] = {s: p.model_dump(mode='json') for s, p in eligible.items()}
        result = compare(legacy_request, assessments)
        for card in result['cards']:
            card['method_applicability'] = applicability(assessments[card['symbol']])
            card['comparison_status'] = 'COMPARABLE' if card.get('scenarios') else 'NOT_COMPARABLE'
            if not card.get('scenarios'):
                card.update(group=None, rank=None, rank_reason='Qualified five-year return unavailable; no adverse fundamental inference.')
        # Normalize displayed sensitivity histories for non-comparable members.
        incomparable = {c['symbol'] for c in result['cards'] if not c.get('scenarios')}
        for change in result['ranking']['changes']:
            for symbol in incomparable:
                change['after'][symbol] = [None, None]
    else:
        result = dict(cards=[], ranking=dict(groups=['优先考虑', '继续观察', '暂不优先'], changes=[], stable=True),
                      decision_at=request.decision_at.isoformat(), maturity='RESEARCH_ONLY', status='partial',
                      execution_authorized=False, order_created=False)
    for change in result['ranking']['changes']:
        change['after'].update({c['symbol']: [None, None] for c in withheld})
    result['cards'] = sorted(result['cards'] + withheld, key=lambda c: c['symbol'])
    result.update(method_version=VERSION, schema_version='wealthpilot.s3-c.v1',
                  valuation_method_version='s3-a.earnings-scenarios.v1.1',
                  comparison_basis=RETURN_BASIS, configuration_priority=dict(status='UNKNOWN'))
    result['ranking']['not_comparable'] = [c['symbol'] for c in result['cards'] if c['comparison_status'] != 'COMPARABLE']
    return result


def identity_projection(symbol, directory_source, submissions_source):
    """Owner identity with exact registry class references, not share conversion."""
    from backend.services.instruments.issuers import resolve_sec_issuer, sec_ticker_matches
    directory, submissions = json.loads(directory_source[0]), json.loads(submissions_source[0])
    issuer = resolve_sec_issuer(symbol, directory, submissions)
    if not issuer:
        raise ValueError('OFFICIAL_ISSUER_IDENTITY_UNRESOLVED')
    registered = sorted(r['ticker'] for r in directory.values() if isinstance(r, dict)
                        and str(r.get('cik_str', '')).zfill(10) == issuer['cik'])
    scope = dict(issuer_type=issuer['issuer_type'], sic=submissions.get('sic'),
                 source_url=issuer['submissions_url'], source_document_sha256=hashlib.sha256(submissions_source[0]).hexdigest(),
                 fetched_at=submissions_source[1])
    identity = dict(symbol=symbol, cik=issuer['cik'], sec_ticker=sec_ticker_matches(symbol, directory)[0]['ticker'],
                    registered_equity_tickers=registered, submissions_tickers=submissions['tickers'],
                    directory_sha256=hashlib.sha256(directory_source[0]).hexdigest(),
                    submissions_sha256=scope['source_document_sha256'])
    return issuer, scope, identity


def make_bundle(root, raw_request, official_root=None):
    """Requalify pinned old receipts and opt-in supplementary official originals.

    New source clocks cannot enter an old cutoff. Supplement hash is user-pinned;
    a hash proves integrity, not source authenticity. No collection here.
    """
    from research_v2.sec_disclosures import DIRECTORY, discover_filings, parse_companyfacts
    root = Path(root)
    request = MethodRequest.model_validate(raw_request)
    manifest = archive._load(root, 'P1_ACCEPTANCE_MANIFEST.json', request.source_manifest_sha256)
    if request.decision_at < stamp(manifest['decision_at']):
        raise ValueError('DECISION_PRECEDES_PINNED_INPUTS')
    original = request.model_dump(mode='json', exclude={'official_manifest_sha256'})
    original['decision_at'] = manifest['decision_at']
    original['companies'] = {s: p for s, p in original['companies'].items() if s[:-3] in manifest['symbols'] and p}
    assessments, failures, files = archive.frozen_inputs(root, original) if original['companies'] else ({}, {}, {})
    receipts = archive._load(root, 'SEC_ONLY_HANDOFF/requests.json')
    def old_source(url):
        item = next(r for r in receipts if r.get('url') == url and r.get('path'))
        path = archive.safe_file(root / 'SEC_ONLY_HANDOFF', item['path'])
        if archive.file_hash(path) != item['sha256']:
            raise ValueError('RAW_SEC_HASH_MISMATCH')
        return path.read_bytes(), item['fetched_at']
    for symbol, prior in list(assessments.items()):
        # Re-run original Registry at the common later clock; preserve all fact
        # publication/fetch clocks and let freshness degrade normally.
        rows = archive._load(root, symbol[:-3] + '-p1-sec-records.json')
        by_id = {r['record_id']: r for r in rows}
        market = {r['kind']: r['values'] for r in prior['records'] if r['kind'] in {'technical', 'quote', 'fundamentals', 'capital_flow'}}
        checked = assess(market, symbol=symbol, research=rows, disclosure_verifier=by_id.get, at=request.decision_at)
        cik = next(r['source_verification']['issuer_identity']['cik'] for r in rows if r.get('source_verification', {}).get('issuer_identity'))
        _, scope, identity = identity_projection(symbol, old_source(DIRECTORY), old_source('https://data.sec.gov/submissions/CIK'+cik+'.json'))
        checked.update(quality_scope=scope, method_identity=identity)
        checked['quality_field_records'] = prior['quality_field_records']  # Still independently checked by build_quality at new clock.
        assessments[symbol] = checked
    if official_root:
        if not request.official_manifest_sha256:
            raise ValueError('PINNED_OFFICIAL_MANIFEST_REQUIRED')
        supplement = archive._load(official_root, 'manifest.json', request.official_manifest_sha256)
        files['official/manifest.json'] = archive.safe_file(official_root, 'manifest.json')
        def source(symbol, kind):
            item = supplement['companies'][symbol][kind]
            if not item.get('path'):
                raise ValueError('SEC_'+kind.upper()+'_SOURCE_UNAVAILABLE')
            path = archive.safe_file(official_root, item['path'])
            if archive.file_hash(path) != item['sha256'] or not stamp(item['fetched_at']) or stamp(item['fetched_at']) > request.decision_at:
                raise ValueError('OFFICIAL_HASH_OR_HISTORICAL_CLOCK_REJECTED')
            files['official/'+item['path']] = path
            return path.read_bytes(), item['fetched_at']
        for symbol in request.companies:
            if symbol in assessments:
                continue
            try:
                directory, submissions = source(symbol, 'directory'), source(symbol, 'submissions')
                issuer, scope, identity = identity_projection(symbol, directory, submissions)
                if supplement['companies'][symbol]['directory']['url'] != DIRECTORY or supplement['companies'][symbol]['submissions']['url'] != issuer['submissions_url']:
                    raise ValueError('OFFICIAL_SOURCE_URL_IDENTITY_CONFLICT')
                metadata = assess({}, symbol=symbol, research=[], disclosure_verifier={}.get, at=request.decision_at)
                metadata.update(quality_scope=scope, method_identity=identity, quality_field_records=[])
                assessments[symbol] = metadata  # A failed facts endpoint must not erase qualified issuer applicability.
                filings = discover_filings(issuer, json.loads(submissions[0]))
                raw = source(symbol, 'companyfacts')
                if supplement['companies'][symbol]['companyfacts']['url'] != 'https://data.sec.gov/api/xbrl/companyfacts/CIK'+issuer['cik']+'.json':
                    raise ValueError('OFFICIAL_SOURCE_URL_IDENTITY_CONFLICT')
                identity_hash = hashlib.sha256(directory[0]+submissions[0]).hexdigest()
                rows = parse_companyfacts(raw, issuer, filings, identity_digest=identity_hash)
                by_id = {r['record_id']: r for r in rows}
                a = assess({}, symbol=symbol, research=rows, disclosure_verifier=by_id.get, at=request.decision_at)
                qr = parse_companyfacts(raw, issuer, filings, identity_digest=identity_hash, quality=True)
                qi = {r['record_id']: r for r in qr}
                a.update(quality_scope=scope, method_identity=identity,
                         quality_field_records=assess_quality_facts(qr, symbol=symbol, at=request.decision_at, disclosure_verifier=qi.get))
                assessments[symbol] = a
            except (ValueError, KeyError, TypeError, OSError) as error:
                code = str(error)
                failures[symbol] = code if type(error) is ValueError and code.isupper() and code.replace('_','').isalnum() else 'OFFICIAL_SOURCE_MISSING_OR_QUALIFICATION_REJECTED'
    for symbol in request.companies:
        if symbol not in assessments:
            failures.setdefault(symbol, 'OFFICIAL_SOURCE_NOT_FROZEN')
    methods = tuple(archive.METHOD_FILES) + ('backend/services/s3_method_research.py', 'backend/services/market_data/method_applicability.py', 'research_v2/sec_identity.py')
    files.update({'methods/'+name: archive.ROOT/name for name in methods})
    result = compare_methods(request, assessments)
    archive._project_source_failures(result, failures)
    payload = dict(request=request.model_dump(mode='json'), assessments=assessments, input_failures=failures,
                   method_files={n: archive.file_hash(archive.ROOT/n) for n in methods},
                   source_files={n: archive.file_hash(p) for n, p in sorted(files.items())}, result=result)
    payload['view_hashes'] = archive.view_hashes(result)
    return dict(artifact_id=archive.digest(payload), payload=payload), files


def replay(path):
    bundle = archive.read_bundle(path)
    p = bundle['payload']
    for name, expected in p['method_files'].items():
        if archive.file_hash(archive.ROOT/name) != expected:
            raise ValueError('METHOD_DRIFT_REPLAY_REQUIRES_ORIGINAL_VERSION')
    result = compare_methods(p['request'], p['assessments'])
    archive._project_source_failures(result, p['input_failures'])
    if archive.digest(result) != archive.digest(p['result']):
        raise ValueError('RECOMPUTATION_DIFFERENCE')
    return dict(artifact_id=bundle['artifact_id'], verified=True, result_sha256=archive.digest(result))
