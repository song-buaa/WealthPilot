"""S3-D frozen real-pool adapter. Original Owners qualify and calculate."""
from pathlib import Path
from decimal import Decimal, localcontext
import hashlib
import json

from pydantic import Field
from backend.services import quality_valuation_research as archive
from backend.services.s3_method_research import MethodRequest, compare_methods, identity_projection
from backend.services.market_data.evidence import assess, assess_quality_facts, mapping, stamp
from backend.services.market_data.financial_research import build
from backend.services.market_data.method_applicability import applicability, APPLICABLE
from backend.services.market_data.quality_valuation import Assumptions

VERSION = 's3-d.real-pool.v1'
PARAMETER_VERSION = 's3-d.transparent-candidates.v1'


def _error_code(error, default):
    code=str(error)
    return code if type(error) is ValueError and code.isupper() and code.replace('_','').isalnum() and len(code)<=120 else default


def _capture_diagnostics(entry):
    result={k:entry[k] for k in ('capture_failure','price_diagnostic') if k in entry}
    result['missing_sources']=[]
    for kind in ('directory','submissions','companyfacts','price','splits'):
        if kind not in entry:continue
        value=entry[kind]
        if not isinstance(value,dict):
            result['missing_sources'].append(dict(kind=kind,status='NOT_CAPTURED_OR_INVALID_METADATA'))
        elif not value.get('path'):
            result['missing_sources'].append(dict(kind=kind,status=value.get('status','NOT_CAPTURED'),diagnostics=value.get('diagnostics',[])))
    return result


class PoolRequest(MethodRequest):
    assumption_context: dict[str, dict] = Field(default_factory=dict)


def _base_request(request):
    return request.model_dump(mode='json', exclude={'assumption_context'})


def candidate_parameters(card):
    """Illustrative earnings scenarios, NOT predictions or inferred guidance.

    Observed revenue growth is only an explicit anchor for candidate earnings
    growth; margin persistence and the revenue-to-earnings bridge are unverified.
    """
    observations = card.get('quality', {}).get('operating_observations', [])
    growth = [Decimal(x['revenue_yoy']) for x in observations[-2:]]
    if not growth:
        raise ValueError('HISTORICAL_GROWTH_ANCHOR_MISSING')
    anchor = sum(growth) / len(growth)
    base = min(Decimal('.25'), max(Decimal('-.10'), anchor))
    parameters = dict(basis='S3_RESEARCH_ASSUMPTION_NOT_POLICY', required_return='.10',
        rationale='Research candidates anchored to observed revenue growth; earnings conversion, terminal multiples, shares and dividends remain explicit unverified assumptions, not guidance.')
    for name,g,shares,pe in [('cautious',base-Decimal('.08'),'.01','15'),
                              ('base',base,'0','20'),('optimistic',base+Decimal('.10'),'-.005','30')]:
        parameters[name] = dict(earnings_growth=str(g),annual_share_change=shares,terminal_pe=pe,dividend_payout='0')
    refs = sorted({r for x in observations[-2:] for r in x['evidence_refs']})
    context = dict(version=PARAMETER_VERSION, status='CANDIDATE_RESEARCH_NOT_GUIDANCE_OR_POLICY',
        fact_basis='OWNER_CALCULATED_HISTORICAL_GAAP_OBSERVATIONS_NOT_FORWARD_FACTS',
        facts=observations[-2:], management_guidance=dict(status='NOT_USED', reason='No separately qualified management guidance loaded.'),
        parameters={
            'earnings_growth':dict(kind='RESEARCH_ASSUMPTION',evidence_refs=refs,
                rationale='Mean of up to two observed revenue YoY rates, clipped to [-10%,25%], is an illustrative earnings-growth anchor; cautious -8pp, optimistic +10pp. This is not a verified earnings forecast.'),
            'annual_share_change':dict(kind='RESEARCH_ASSUMPTION',evidence_refs=refs,
                rationale='Cautious +1%, base flat, optimistic -0.5% annual relative shares; future SBC/buybacks unverified; no separate buyback yield.'),
            'terminal_pe':dict(kind='RESEARCH_ASSUMPTION',evidence_refs=refs,
                rationale='15/20/30x illustrative exit multiples; no claim of fair, industry-normal or management-approved multiples.'),
            'dividend_payout':dict(kind='RESEARCH_ASSUMPTION',evidence_refs=refs,
                rationale='Zero modeled dividends conservatively omits unqualified future distributions; not a claim that the company pays no dividends.'),
            'required_return':dict(kind='RESEARCH_ASSUMPTION',evidence_refs=refs,
                rationale='Uniform 10% research comparison scale retained from S3-A; not effective Investment Policy.')},
        uncertainty=['REVENUE_IS_NOT_EARNINGS_GROWTH','MARGIN_AND_TERMINAL_MULTIPLE_UNVERIFIED',
                     'FUTURE_SHARES_AND_DISTRIBUTIONS_UNVERIFIED','NOT_NORMALIZED_EARNINGS',
                     'CAUTIOUS_CASE_NOT_A_PROBABILITY_BOUND'])
    for field,note in context['parameters'].items():
        note['evidence_role']='OBSERVED_REVENUE_ANCHOR_NOT_EARNINGS_FORECAST' if field=='earnings_growth' else 'HISTORICAL_CONTEXT_NOT_PARAMETER_PROOF'
    return Assumptions.model_validate(parameters).model_dump(mode='json'), context


def _check_context(context, assessment, observed_facts):
    if (not isinstance(context,dict) or not context.get('version')
            or context.get('status') != 'CANDIDATE_RESEARCH_NOT_GUIDANCE_OR_POLICY'
            or not isinstance(context.get('management_guidance'),dict)
            or context['management_guidance'].get('status') != 'NOT_USED'
            or not isinstance(context.get('parameters'),dict)
            or not context.get('uncertainty')):
        raise ValueError('ASSUMPTION_PROVENANCE_MISSING')
    if context.get('facts') != observed_facts:
        raise ValueError('ASSUMPTION_FACT_SNAPSHOT_MISMATCH')
    refs = {r['record_id'] for r in assessment.get('quality_field_records',[])}
    for field in ['earnings_growth','annual_share_change','terminal_pe','dividend_payout','required_return']:
        note = context.get('parameters',{}).get(field,{})
        if (not isinstance(note,dict) or note.get('kind') != 'RESEARCH_ASSUMPTION' or len(note.get('rationale','')) < 10
                or not note.get('evidence_refs') or not set(note['evidence_refs']) <= refs):
            raise ValueError('ASSUMPTION_REFERENCE_OR_FACT_BOUNDARY_REJECTED')


def compare_pool(raw_request, assessments, price_qualification=None):
    # Match the original valuation Owner's fixed precision for both the initial
    # quality-only pass and final comparison; never inherit a caller's context.
    with localcontext() as context:
        context.prec=34
        return _compare_pool(raw_request,assessments,price_qualification)


def _compare_pool(raw_request, assessments, price_qualification=None):
    request = PoolRequest.model_validate(raw_request)
    request_data = _base_request(request)
    contexts = dict(request.assumption_context)
    all_parameters = dict(request_data['companies'])
    initial_data = dict(request_data, companies={s:None for s in request.companies})
    initial = compare_methods(initial_data, assessments)
    failures = {}
    for card in initial['cards']:
        symbol = card['symbol']
        if card['method_applicability']['earnings_model']['status'] != APPLICABLE:
            request_data['companies'][symbol] = None
            continue
        try:
            if request_data['companies'][symbol] is None:
                request_data['companies'][symbol], contexts[symbol] = candidate_parameters(card)
            all_parameters[symbol] = request_data['companies'][symbol]
            _check_context(contexts.get(symbol), assessments.get(symbol,{}),
                           card.get('quality',{}).get('operating_observations',[])[-2:])
            price=(price_qualification or {}).get(symbol,{})
            if price.get('status') != 'QUALIFIED_RAW_LISTED_CLOSE':
                raise ValueError('PRICE_OR_SPLIT_QUALIFICATION_MISSING')
            if price.get('price_date') != (assessments[symbol].get('technical_levels') or {}).get('day',{}).get('end_at'):
                raise ValueError('PRICE_DATE_QUALIFICATION_MISMATCH')
        except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
            failures[symbol] = _error_code(exc,'RESEARCH_INPUT_MISSING')
            request_data['companies'][symbol] = None
    result = compare_methods(request_data,assessments)
    for card in result['cards']:
        symbol = card['symbol']
        card['assumption_context'] = contexts.get(symbol)
        if not card.get('scenarios'):
            card['assumptions'] = all_parameters.get(symbol)
        card['price_qualification'] = (price_qualification or {}).get(symbol,dict(status='UNKNOWN'))
        card['capture_diagnostics'] = assessments.get(symbol,{}).get('capture_diagnostics',{})
        if symbol in failures:
            card['research_input_failure'] = failures[symbol]
    result.update(method_version=VERSION,schema_version='wealthpilot.s3-d.v1',
                  assumption_method_version=PARAMETER_VERSION)
    result['comparable_count'] = sum(c['comparison_status']=='COMPARABLE' for c in result['cards'])
    result['not_comparable'] = [dict(symbol=c['symbol'],method=c['method_applicability'],
        reason=c.get('research_input_failure') or c.get('failure_reason') or c.get('rank_reason'))
        for c in result['cards'] if c['comparison_status']!='COMPARABLE']
    # Freeze generated parameters as actual numerical inputs, not an implicit cache.
    resolved = PoolRequest.model_validate(dict(request_data,companies=all_parameters,assumption_context=contexts))
    return result, resolved


def frozen_assessments(root, request):
    from research_v2 import sec_disclosures as sec
    from backend.services.execution_plan.kline_provider import decode_av_daily, decode_av_splits
    from backend.services.market_data.technical_evidence import from_bars
    root=Path(root);manifest=archive._load(root,'manifest.json',request.source_manifest_sha256)
    if manifest.get('schema_version')!='s3-d.sources.v1':raise ValueError('POOL_SOURCE_SCHEMA_REJECTED')
    files={'inputs/manifest.json':archive.safe_file(root,'manifest.json')};assessments={};failures={};prices={}
    def source(entry, expected_url=None):
        if not isinstance(entry,dict) or not entry.get('path'):raise ValueError('SOURCE_UNAVAILABLE')
        path=archive.safe_file(root,entry['path']);at=stamp(entry.get('fetched_at'))
        if archive.file_hash(path)!=entry.get('sha256') or not at or at>request.decision_at:
            raise ValueError('SOURCE_HASH_OR_CLOCK_REJECTED')
        if expected_url and entry.get('url')!=expected_url:raise ValueError('SOURCE_URL_REJECTED')
        files['inputs/'+entry['path']]=path
        return path.read_bytes(),entry['fetched_at']
    # Retain diagnostics and unused originals as raw evidence too. They acquire
    # no fact qualification merely by being copied into an immutable archive.
    for entry in manifest.get('requests',[]):
        if entry.get('path'):
            try:files['inputs/'+entry['path']]=archive.safe_file(root,entry['path'])
            except (ValueError,OSError):pass  # used sources still fail explicitly below
    for symbol in request.companies:
        try:
            entry=manifest['companies'][symbol]
            # A failed identity endpoint must retain its specific Owner
            # diagnostics rather than erase this company from the pool.
            pending=assess({},symbol=symbol,at=request.decision_at)
            pending['capture_diagnostics']=_capture_diagnostics(entry)
            assessments[symbol]=pending
            directory=source(entry['directory'],sec.DIRECTORY);sub=source(entry['submissions'])
            issuer,scope,identity=identity_projection(symbol,directory,sub)
            if entry['submissions']['url']!=issuer['submissions_url']:raise ValueError('ISSUER_SOURCE_URL_REJECTED')
            a=assess({},symbol=symbol,at=request.decision_at)
            a.update(quality_scope=scope,method_identity=identity,quality_field_records=[])
            a['capture_diagnostics']=_capture_diagnostics(entry)
            assessments[symbol]=a
            if applicability(a)['earnings_model']['status']=='NOT_APPLICABLE':
                prices[symbol]=dict(status='NOT_APPLICABLE',reason='VALUATION_METHOD_NOT_APPLICABLE')
                continue
            raw=source(entry['companyfacts'],'https://data.sec.gov/api/xbrl/companyfacts/CIK'+issuer['cik']+'.json')
            filings=sec.discover_filings(issuer,json.loads(sub[0]));identity_hash=hashlib.sha256(directory[0]+sub[0]).hexdigest()
            rows=sec.parse_companyfacts(raw,issuer,filings,identity_digest=identity_hash)
            qr=sec.parse_companyfacts(raw,issuer,filings,identity_digest=identity_hash,quality=True)
            for original in entry.get('originals',[]):
                filing=next((f for f in filings if f['full_url']==original.get('url')),None)
                if not filing:raise ValueError('ORIGINAL_FILING_NOT_DISCOVERED')
                r=sec.parse_filing(source(original,filing['full_url']),issuer,filing,identity_digest=identity_hash,
                    fiscal_eps_context=sec._domestic_fiscal_eps_context(rows,issuer))
                if r:rows.append(r)
            by_id={r['record_id']:r for r in rows};qi={r['record_id']:r for r in qr}
            market={};price_error=None
            try:
                p=entry.get('price',{});data=json.loads(source(p,'https://www.alphavantage.co/query')[0])
                if p.get('function')!='TIME_SERIES_DAILY' or p.get('symbol')!=symbol:raise ValueError('PRICE_FUNCTION_OR_IDENTITY_REJECTED')
                result=decode_av_daily(data,symbol,count=300,strict_identity=True)
                technical=mapping(from_bars(symbol,result,at=request.decision_at))
                if not technical:raise ValueError('NO_CLOSED_PRICE')
                technical['provenance']['fetched_at']=p['fetched_at']
                technical['provenance'].update(source_url=p['url'],raw_sha256=p['sha256'],price_basis='RAW_AS_TRADED_NOT_ADJUSTED')
                market['technical']=technical
                splits=entry.get('splits',{});events=decode_av_splits(json.loads(source(splits,'https://www.alphavantage.co/query')[0]),symbol)
                if splits.get('function')!='SPLITS' or splits.get('symbol')!=symbol:raise ValueError('SPLIT_FUNCTION_OR_IDENTITY_REJECTED')
                if any(e['effective_date']>request.decision_at.date().isoformat() for e in events):raise ValueError('SPLIT_HISTORY_HAS_FUTURE_EVENT')
                prices[symbol]=dict(status='PENDING_EPS_INTERVAL',price_date=technical['data_as_of'],
                    price_basis='RAW_AS_TRADED_NOT_ADJUSTED',split_events=events,price_source_sha256=p['sha256'],
                    split_source_sha256=splits['sha256'],price_fetched_at=p['fetched_at'],split_fetched_at=splits['fetched_at'])
            except (ValueError,KeyError,TypeError,ArithmeticError,OSError) as exc:
                price_error=_error_code(exc,'PRICE_SOURCE_OR_SPLITS_MISSING')
            a=assess(market,symbol=symbol,research=rows,disclosure_verifier=by_id.get,at=request.decision_at)
            a.update(quality_scope=scope,method_identity=identity,
                quality_field_records=assess_quality_facts(qr,symbol=symbol,at=request.decision_at,disclosure_verifier=qi.get))
            a['capture_diagnostics']=_capture_diagnostics(entry)
            assessments[symbol]=a
            if price_error:
                prices[symbol]=dict(status='UNKNOWN',reason=price_error)
            else:
                metrics=build(a)['records']
                if not any(r['field']=='observed_price' for r in metrics):raise ValueError('PRICE_EVIDENCE_NOT_QUALIFIED')
                eps=next((r for r in metrics if r['field']=='diluted_eps_ttm'),None)
                observations=a['verified_field_records']+a['historical_field_records']
                starts=[r['fiscal_period_start'] for r in observations if eps and r['record_id'] in eps['evidence_refs']]
                if not starts:
                    prices[symbol]['available_quarter_eps_periods']=sorted({r['period'] for r in observations if r['field']=='diluted_eps_ordinary_quarter'})
                    prices[symbol]['financial_owner_gaps']=build(a)['limitations']
                    raise ValueError('LISTED_EPS_INTERVAL_UNKNOWN')
                begin=min(starts);end=prices[symbol]['price_date']
                intervening=[e for e in prices[symbol]['split_events'] if begin<=e['effective_date']<=end and Decimal(e['split_factor'])!=1]
                prices[symbol].update(status='UNKNOWN' if intervening else 'QUALIFIED_RAW_LISTED_CLOSE',
                    eps_interval_start=begin,intervening_splits=intervening,
                    reason='INTERVENING_SPLIT_REQUIRES_OWNER_QUALIFIED_PER_SHARE_BRIDGE' if intervening else
                    'Vendor historical split response shows no intervening split within selected EPS interval; no conversion applied. Not a guarantee of complete corporate-action coverage.')
        except (ValueError,KeyError,TypeError,ArithmeticError,OSError) as exc:
            failures[symbol]=_error_code(exc,'COMPANY_SOURCE_OR_QUALIFICATION_MISSING')
            prices.setdefault(symbol,{})
            prices[symbol].update(status='UNKNOWN',reason=failures[symbol])
    return assessments,failures,prices,files


def make_bundle(root,raw_request):
    with localcontext() as context:
        context.prec=34
        return _make_bundle(root,raw_request)


def _make_bundle(root,raw_request):
    request=PoolRequest.model_validate(raw_request)
    assessments,failures,prices,files=frozen_assessments(root,request)
    result,resolved=compare_pool(request,assessments,prices)
    archive._project_source_failures(result,failures)
    for item in result['not_comparable']:
        if item['symbol'] in failures:item['source_failure']=failures[item['symbol']]
    methods=tuple(archive.METHOD_FILES)+('backend/services/s3_method_research.py',
        'backend/services/market_data/method_applicability.py','research_v2/sec_identity.py',
        'backend/services/s3_pool_research.py','backend/services/execution_plan/kline_provider.py')
    methods += ('backend/services/s3_pool_capture.py','scripts/s3_quality_valuation.py')
    files.update({'methods/'+n:archive.ROOT/n for n in methods})
    payload=dict(request=resolved.model_dump(mode='json'),assessments=assessments,input_failures=failures,
        price_qualification=prices,method_files={n:archive.file_hash(archive.ROOT/n) for n in methods},
        source_files={n:archive.file_hash(p) for n,p in sorted(files.items())},result=result)
    payload['view_hashes']=archive.view_hashes(result)
    return dict(artifact_id=archive.digest(payload),payload=payload),files


def replay(path):
    bundle=archive.read_bundle(path);p=bundle['payload']
    for n,h in p['method_files'].items():
        if archive.file_hash(archive.ROOT/n)!=h:raise ValueError('METHOD_DRIFT_REPLAY_REQUIRES_ORIGINAL_VERSION')
    result,resolved=compare_pool(p['request'],p['assessments'],p['price_qualification'])
    archive._project_source_failures(result,p['input_failures'])
    for item in result['not_comparable']:
        if item['symbol'] in p['input_failures']:item['source_failure']=p['input_failures'][item['symbol']]
    if archive.digest(result)!=archive.digest(p['result']):raise ValueError('RECOMPUTATION_DIFFERENCE')
    return dict(artifact_id=bundle['artifact_id'],verified=True,result_sha256=archive.digest(result))
