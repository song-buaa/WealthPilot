"""Opt-in S3 P2 research over an immutable P1 pool, not a new data engine.

Original SEC/Evidence/Financial Research qualify facts. Original valuation and
rank owners settle returns/gates. Research inputs are explicit private dossiers;
no automatic predictions, model calls, holdings, policy, database or network.
"""
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path
import hashlib
import json

from pydantic import AwareDatetime, Field, model_validator
from backend.services import quality_valuation_research as archive
from backend.services import s3_pool_research as pool
from backend.services.market_data import quality_valuation as engine
from backend.services.market_data.financial_research import operating_ttm_bridge

VERSION='s3-p2.operating-earnings.v1.3'
STRESS=('-.03', '0', '.03')


class OperatingScenario(engine.Contract):
    revenue_growth: list[Decimal] = Field(min_length=5,max_length=5)
    terminal_operating_margin: Decimal = Field(gt=0,le='.8')
    terminal_conversion: Decimal = Field(gt=0,le='1.2')
    annual_share_change: Decimal = Field(ge='-.1',le='.2')
    terminal_pe: Decimal = Field(gt=0,le=100)
    dividend_payout: Decimal = Field(default=Decimal(0),ge=0,le=0)

    @model_validator(mode='after')
    def growth(self):
        if any(g<Decimal('-.5') or g>1 for g in self.revenue_growth):
            raise ValueError('P2_REVENUE_PATH_OUT_OF_RANGE')
        return self


class Note(engine.Contract):
    kind: str = Field(pattern='^RESEARCH_ASSUMPTION$')
    rationale: str = Field(min_length=40,max_length=4000)
    evidence_refs: list[str] = Field(min_length=1)


class SourceNote(engine.Contract):
    path: str
    sha256: str = Field(pattern='^[a-f0-9]{64}$')
    url: str
    begin: int = Field(ge=0)
    end: int = Field(gt=0)
    excerpt_sha256: str = Field(pattern='^[a-f0-9]{64}$')
    summary: str = Field(min_length=30,max_length=3000)
    role: str = Field(pattern='^(HISTORICAL_BUSINESS_CONTEXT|EXPIRED_GUIDANCE_NOT_FORWARD_INPUT|PRIOR_GUIDANCE_NOT_RECONFIRMED|CURRENT_DISCLOSURE_CONTEXT|HISTORICAL_PE_ANCHOR|OFFICIAL_IR_CONTEXT)$')


class Profile(engine.Contract):
    return_unit_qualification: str = Field(default='NO_KNOWN_UNRESOLVED_DISTRIBUTION',pattern='^(NO_KNOWN_UNRESOLVED_DISTRIBUTION|KNOWN_FUTURE_CLASS_DISTRIBUTION_REQUIRES_ENTITLEMENT_BRIDGE)$')
    cautious: OperatingScenario
    base: OperatingScenario
    optimistic: OperatingScenario
    notes: dict[str,Note]
    business_thesis: str = Field(min_length=40,max_length=4000)
    risks: list[str] = Field(min_length=2)
    review_conditions: list[str] = Field(min_length=2)
    current_guidance: str = Field(pattern='^(UNKNOWN_NO_QUALIFIED_CURRENT_GUIDANCE|PARTIAL_PRIOR_GUIDANCE_NOT_RECONFIRMED|QUALIFIED_NEAR_TERM_NOT_FIVE_YEAR_GUIDANCE)$')
    source_notes: list[SourceNote] = Field(min_length=1)


class Dossier(engine.Contract):
    version: str = Field(pattern='^s3-p2.research-dossier.v1$')
    parent_artifact_id: str = Field(pattern='^[a-f0-9]{64}$')
    research_recorded_at: AwareDatetime
    companies: dict[str,Profile] = Field(max_length=20)


def _eight(card):
    card['sensitivity']=[]
    eps=card['cross_checks']['diluted_eps_ttm']['values']['diluted_eps_ttm']
    price=card['cross_checks']['observed_price']['values']['observed_price']
    for field,offsets in engine.SENSITIVITY_OFFSETS:
        for delta in offsets:
            params=dict(card['assumptions']['base']);rate=Decimal(card['assumptions']['required_return'])
            if field=='required_return':rate+=delta
            elif field=='terminal_pe':params[field]=str(Decimal(params[field])*(1+delta))
            else:params[field]=str(Decimal(params[field])+delta)
            try: value=engine.scenario(eps,price,params,rate)
            except ValueError:value=None
            card['sensitivity'].append(dict(parameter=field,delta=str(delta),parameters=params if value else None,
                required_return=str(rate),result=value))
    engine._sensitivity_qualification(card)


def project_card(old,baseline,profile):
    card=deepcopy({k:v for k,v in old.items() if k!='joint_sensitivity'})
    eps=card['cross_checks']['diluted_eps_ttm']['values']['diluted_eps_ttm']
    price=card['cross_checks']['observed_price']['values']['observed_price']
    card['scenarios']={};compiled={}
    for name in engine.SCENARIOS:
        card['scenarios'][name],compiled[name]=engine.operating_scenario(eps,price,baseline,
            getattr(profile,name).model_dump(mode='json'),card['assumptions']['required_return'])
    assumptions=dict(basis='S3_RESEARCH_ASSUMPTION_NOT_POLICY',required_return=card['assumptions']['required_return'],
        rationale='Equivalent compound GAAP aggregate earnings endpoint from explicit revenue, operating-margin and below-line assumptions. Zero payout; not a validated forecast.',**compiled)
    card['assumptions']=engine.Assumptions.model_validate(assumptions).model_dump(mode='json')
    card['operating_assumptions']=profile.model_dump(mode='json')
    card['assumption_context']=dict(version='s3-p2.research-dossier.v1',
        status='EXPLICIT_RESEARCH_ASSUMPTIONS_NOT_FORECAST_FACTS_OR_POLICY',
        parameters={k:v.model_dump(mode='json') for k,v in profile.notes.items()},
        source_notes=[n.model_dump(mode='json') for n in profile.source_notes],
        management_guidance=dict(status=profile.current_guidance,used_in_projection=False),
        required_return=dict(value=card['assumptions']['required_return'],basis='UNCHANGED_P1_RESEARCH_SCALE_NOT_POLICY'),
        compilation='Revenue x operating-margin x below-line relative profit bridge; equivalent terminal compound earnings, zero payout only',
        uncertainty=['SCALED_TTM_EPS_NOT_EXACT_FUTURE_WEIGHTED_DILUTED_SHARES',
            'NO_VERIFIED_CURRENT_FIVE_YEAR_GUIDANCE','HISTORICAL_PIT_PE_UNKNOWN','GAAP_NORMALIZATION_UNKNOWN'])
    card['operating_baseline']=baseline
    card['formula']='EPS[t]=qualified_TTM_EPS * revenue_index[t] * (operating_margin[t]/TTM_operating_margin) * (net_to_operating[t]/TTM_net_to_operating) / relative_shares[t]; original zero-payout PE terminal settlement'
    card['research_method_blockers']=[]
    card['normalization_status']='GAAP_BASE_UNADJUSTED_FORWARD_CONVERSION_IS_RESEARCH_ASSUMPTION'
    card['forward_evidence_status']=profile.current_guidance
    card['valuation_context']=dict(current_pe=old['cross_checks'].get('pe_ttm'),
        historical_pe=dict(status='UNKNOWN',reason='No contemporaneous EPS-vintage plus split-qualified historical price pairs; latest-source restated EPS is not a historical PIT PE distribution.'),
        annual_gaap_trends=old['quality']['gaap_series'],cash_periods=old['quality']['financial_inputs']['cash_periods'],
        capital_efficiency=old['quality']['capital_efficiency'],
        terminal_pe_status='COMPANY_SPECIFIC_RESEARCH_RANGE_NOT_HISTORICAL_NORMAL_OR_VERIFIED_FAIR_MULTIPLE')
    card['p1_comparison']=dict(assumptions=old['assumptions'],scenarios=old['scenarios'],
        assumption_context=old.get('assumption_context'),group=old['group'],rank=old['rank'],quality_floors_unchanged=True)
    card['change_conditions']=profile.review_conditions
    _eight(card)
    values=[Decimal(v['conditional_value']) for v in card['scenarios'].values()]
    card['conditional_value_range']=[engine.text(min(values)),engine.text(max(values))]
    return card


def _joint_qualify(card,baseline,profile):
    card['joint_sensitivity']=[]
    card['research_method_blockers']=[]
    for shift in STRESS:
        for pe_shift in ('-.2','0','.2'):
            try:
                raw=profile.model_dump(mode='json')
                raw['base']['revenue_growth']=[str(Decimal(x)+Decimal(shift)) for x in raw['base']['revenue_growth']]
                raw['base']['terminal_pe']=str(Decimal(raw['base']['terminal_pe'])*(1+Decimal(pe_shift)))
                changed=project_card(card,baseline,Profile.model_validate(raw))
                card['joint_sensitivity'].append(dict(revenue_growth_shift=shift,terminal_pe_shift=pe_shift,
                    result=changed['scenarios']['base'],operating_parameters=raw['base'],
                    assumptions=changed['assumptions'],sensitivity=changed['sensitivity']))
            except (ValueError,ArithmeticError):
                card['joint_sensitivity'].append(dict(revenue_growth_shift=shift,terminal_pe_shift=pe_shift,result=None))
    complete=all(v['result'] for v in card['joint_sensitivity']) and len(card['joint_sensitivity'])==9
    robust=bool(complete and all(Decimal(v['result']['annualized_return'])>=Decimal(card['assumptions']['required_return']) for v in card['joint_sensitivity']))
    card['joint_qualification']=dict(complete=bool(complete),all_above_required=robust,
        rule_version='s3-p2.joint-stress.v1',scope='NINE_REVENUE_SHIFT_X_TERMINAL_PE_CELLS_NOT_PROBABILITY_OR_FULL_RISK_BOUND')
    if not complete:card['research_method_blockers'].append('JOINT_SENSITIVITY_INCOMPLETE')
    elif not robust:card['research_method_blockers'].append('JOINT_STRESS_RETURN_THRESHOLD_CROSSING')


def _positions(cards):
    return {c['symbol']:[c['group'],c['rank']] if c.get('scenarios') else [None,None] for c in cards}


def evaluate(qualified,dossier,assessments,qualification_failures=None):
    """Same qualified facts/price/quality and research thresholds as P1."""
    dossier=Dossier.model_validate(dossier)
    with localcontext() as ctx:
        ctx.prec=34
        cards=[];failures={};baselines={}
        for old in qualified['cards']:
            symbol=old['symbol']
            try:
                if qualification_failures is not None:
                    if old['comparison_status']!='COMPARABLE':
                        raise ValueError(old.get('failure_reason') or 'P2_PARENT_NOT_COMPARABLE')
                    if symbol in qualification_failures:raise ValueError(qualification_failures[symbol])
                profile=dossier.companies[symbol]
                if profile.return_unit_qualification!='NO_KNOWN_UNRESOLVED_DISTRIBUTION':
                    raise ValueError('KNOWN_FUTURE_CLASS_DISTRIBUTION_REQUIRES_ENTITLEMENT_BRIDGE')
                if old['comparison_status']!='COMPARABLE':raise ValueError('P2_PARENT_NOT_COMPARABLE')
                catalog=set(old['quality']['evidence_catalog'])
                for field in ('revenue','operating_margin','conversion','shares','terminal_pe'):
                    note=profile.notes.get(field)
                    if not note or not set(note.evidence_refs)<=catalog:raise ValueError('P2_ASSUMPTION_EVIDENCE_REJECTED')
                baseline=operating_ttm_bridge(assessments[symbol])
                baselines[symbol]=baseline
                cards.append(project_card(old,baseline,profile))
            except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
                reason=pool._error_code(exc,'P2_INPUT_REJECTED')
                card=deepcopy(old);card.update(scenarios={},sensitivity=[],comparison_status='NOT_COMPARABLE',
                    valuation_status='UNKNOWN',failure_reason=reason,rank=None,assumptions=None,
                    group=None,rank_reason=reason+'; current comparable return withheld, not an adverse fundamental judgment.',
                    classification_reasons=dict(input_qualification=reason),
                    historical_benchmark_ref=dict(parent_artifact_id=dossier.parent_artifact_id,symbol=symbol,
                        scope='PARENT_NUMERIC_OUTPUT_NOT_CURRENT_VALUATION'))
                # A safe refusal must also withhold inherited target prices and
                # projection explanations, not merely empty the scenario array.
                for field in ('conditional_value_range','formula','assumption_context','operating_baseline',
                              'operating_assumptions','valuation_context','p1_comparison','high_sensitivity',
                              'sensitivity_complete','sensitivity_qualification','sensitivity_span',
                              'sensitivity_threshold_robust','tie_reason'):
                    card.pop(field,None)
                if symbol in dossier.companies:
                    card['unexecuted_operating_profile']=dict(status='UNEXECUTED_NOT_CURRENT_VALUATION',
                        profile=dossier.companies[symbol].model_dump(mode='json'))
                    card['change_conditions']=dossier.companies[symbol].review_conditions
                cards.append(card);failures[symbol]=reason
        fallback={c['symbol']:c.get('assumptions') for c in qualified['cards']}
        params={c['symbol']:c['assumptions'] or fallback[c['symbol']] for c in cards}
        thresholds={k:qualified['ranking'].get(k,pool.PoolRequest.model_fields[k].default)
                    for k in ('tie_band','downside_floor','downside_tie_band')}
        # Method-qualified parents can legitimately have no assumptions (META).
        # Ranking needs thresholds, not fabricated parameters for refused cards.
        request=pool.PoolRequest.model_validate(dict(pool_id='s3_p2',pool_version='v1',
            purpose='Retrospective same-input model comparison, not an investment signal',
            decision_at=qualified['decision_at'],source_manifest_sha256='0'*64,companies=params,
            **thresholds))
        for card in cards:
            card['joint_sensitivity']=[]
            if card['scenarios']:
                _joint_qualify(card,baselines[card['symbol']],dossier.companies[card['symbol']])
        engine._rank(cards,request)
        initial=_positions(cards);changes=[]
        for card in cards:
            for variant in card['joint_sensitivity']:
                if not variant['result']:continue
                others=deepcopy(cards);changed=next(c for c in others if c['symbol']==card['symbol'])
                changed['scenarios']['base']=variant['result'];changed['assumptions']=variant['assumptions'];changed['sensitivity']=variant['sensitivity']
                raw=dossier.companies[card['symbol']].model_dump(mode='json');raw['base']=variant['operating_parameters']
                _joint_qualify(changed,baselines[card['symbol']],Profile.model_validate(raw))
                variant['method_blockers_after']=changed['research_method_blockers']
                engine._rank(others,request)
                after=_positions(others)
                variant['after']=after
                if after!=initial:changes.append(dict(symbol=card['symbol'],revenue_growth_shift=variant['revenue_growth_shift'],terminal_pe_shift=variant['terminal_pe_shift'],after=after))
        for card in cards:
            card['ranking_unstable']=bool(changes)
            if card.get('research_method_blockers'):
                card['classification_reasons']['research_method_blockers']=card['research_method_blockers']
                card['rank_reason']+=' P2 joint revenue/terminal-PE stress gate: '+', '.join(card['research_method_blockers'])+'. This is an additional research gate, not a change to the four quality floors.'
            if not card['scenarios']:card.update(group=None,rank=None)
        result=deepcopy(qualified)
        result.update(method_version=VERSION,schema_version='wealthpilot.s3-p2.v1',cards=cards,
            valuation_method_version='s3-p2.operating-path-pe.v1',
            terminal_settlement_method_version=engine.VALUATION_METHOD_VERSION,
            assumption_method_version=dossier.version,comparable_count=sum(bool(c['scenarios']) for c in cards),
            not_comparable=[dict(symbol=s,reason=v) for s,v in failures.items()],
            research_recorded_at=dossier.research_recorded_at.isoformat(),
            research_scope='RETROSPECTIVE_METHOD_COMPARISON_NOT_OUT_OF_SAMPLE_FORECAST',
            ranking={**qualified['ranking'],**{k:str(getattr(request,k)) for k in thresholds},
                'stable':not changes,'changes':changes})
        return result


def make_bundle(parent_path,dossier_raw,*,professional_context=None,entry_provenance=None,_frozen_owner=False):
    parent_path=Path(parent_path)
    parent=archive.read_bundle(parent_path);dossier=Dossier.model_validate(dossier_raw);p=parent['payload']
    if parent['artifact_id']!=dossier.parent_artifact_id:raise ValueError('P2_PARENT_ID_MISMATCH')
    if dossier.research_recorded_at<engine.stamp(p['request']['decision_at']):raise ValueError('P2_RESEARCH_CLOCK_INVALID')
    if not set(dossier.companies)<=set(p['request']['companies']):raise ValueError('P2_POOL_IDENTITY_MISMATCH')
    if not dossier.companies and professional_context is None:raise ValueError('P2_EMPTY_DOSSIER')
    request=deepcopy(p['request'])
    if professional_context is None:
        request['companies']={s:request['companies'][s] for s in dossier.companies}
        request['assumption_context']={s:request['assumption_context'][s] for s in dossier.companies}
    # Requalify original bytes through their existing Owners. Never trust a
    # manually supplied metric or modify the parent to fit this new model.
    if _frozen_owner:
        if professional_context is None:raise ValueError('P2_FROZEN_OWNER_CONTEXT_REQUIRED')
        pool.replay(parent_path)
        result,resolved=pool.compare_pool(request,p['assessments'],p['price_qualification'])
        archive._project_source_failures(result,p['input_failures'])
        for item in result['not_comparable']:
            if item['symbol'] in p['input_failures']:item['source_failure']=p['input_failures'][item['symbol']]
        q=dict(p,request=resolved.model_dump(mode='json'),result=result)
    else:
        requalified,_=pool.make_bundle(parent_path.parent/'sources/inputs',request)
        q=requalified['payload']
    old={c['symbol']:c for c in p['result']['cards']}
    root=parent_path.parent/'sources/inputs'
    manifest=archive._load(root,'manifest.json',p['request']['source_manifest_sha256'])
    calibration={}
    for symbol,profile in dossier.companies.items():
        for note in profile.source_notes:
            receipts=(manifest.get('requests',[]) if note.role=='OFFICIAL_IR_CONTEXT'
                      else manifest['companies'][symbol].get('originals',[]))
            if not any(r.get('path')==note.path and r.get('sha256')==note.sha256 and r.get('url')==note.url for r in receipts):
                raise ValueError('P2_SOURCE_NOTE_IDENTITY_REJECTED')
            raw=archive.safe_file(root,note.path).read_bytes()
            if (archive.file_hash(archive.safe_file(root,note.path))!=note.sha256 or note.begin>=note.end
                    or note.end>len(raw) or hashlib.sha256(raw[note.begin:note.end]).hexdigest()!=note.excerpt_sha256):
                raise ValueError('P2_SOURCE_NOTE_EXCERPT_REJECTED')
        snapshots=(professional_context['annual_eps_snapshots'][symbol] if professional_context is not None else None)
        calibration[symbol]=calibration_context(root,manifest,symbol,profile,p['request']['decision_at'],annual_snapshots=snapshots)
    for card in q['result']['cards']:
        for field in ('quality','cross_checks','scenarios','assumptions','comparison_status'):
            if card.get(field)!=old[card['symbol']].get(field):raise ValueError('P2_BASELINE_OWNER_REQUALIFICATION_DIFFERENCE')
    result=evaluate(q['result'],dossier,q['assessments'],
        professional_context['failures'] if professional_context is not None else None)
    for card in result['cards']:
        context=calibration.get(card['symbol'],dict(disclosures=[],research_documents=[],historical_pe=dict(samples=[])))
        if context['disclosures'] or context['historical_pe']['samples'] or context['research_documents']:
            card['calibration_evidence']=context
            if card.get('valuation_context'):
                card['valuation_context']['historical_pe']=context['historical_pe']
                if context['historical_pe']['samples']:
                    card['assumption_context']['uncertainty']=[x for x in card['assumption_context']['uncertainty'] if x!='HISTORICAL_PIT_PE_UNKNOWN']
                    card['assumption_context']['uncertainty'].append('SPARSE_ANNUAL_ASFILED_PE_ANCHORS_NOT_TTM_DISTRIBUTION_OR_FAIR_VALUE')
                card['assumption_context']['management_guidance']['used_as_near_term_research_anchor']=(
                    bool(context['disclosures']) and dossier.companies[card['symbol']].current_guidance=='QUALIFIED_NEAR_TERM_NOT_FIVE_YEAR_GUIDANCE')
    files={'baseline/bundle.json':parent_path}
    files.update({'baseline/'+name:archive.safe_file(parent_path.parent,name) for name in p['view_hashes']})
    files.update({'baseline/sources/'+name:archive.safe_file(parent_path.parent/'sources',name) for name in p['source_files']})
    methods=tuple(q['method_files'])+('backend/services/s3_operating_research.py','scripts/s3_operating_verify.py')
    if professional_context is not None:
        methods+=('backend/services/s3_professional_research.py','research_v2/s3_profiles/p22-v1.json')
        result['professional_qualification']=professional_context
        result['assumption_profile_version']=professional_context['profile_version']
        result['research_scope']='CURRENT_EVIDENCE_CONDITIONAL_RESEARCH_NOT_VALIDATED_FORECAST'
        if result['comparable_count']<len(result['cards']):result['status']='partial'
    files.update({'methods/'+name:archive.ROOT/name for name in methods})
    payload=dict(request=request,dossier=dossier.model_dump(mode='json'),assessments=q['assessments'],
        qualified_baseline=q['result'],parent_artifact_id=parent['artifact_id'],result=result,
        method_files={name:archive.file_hash(archive.ROOT/name) for name in methods},
        source_files={name:archive.file_hash(path) for name,path in sorted(files.items())})
    if professional_context is not None:payload['professional_context']=professional_context
    if entry_provenance is not None:payload['entry_provenance']=entry_provenance
    payload['view_hashes']=archive.view_hashes(result)
    return dict(artifact_id=archive.digest(payload),payload=payload),files


def calibration_context(root,manifest,symbol,profile,decision_at,*,annual_snapshots=None):
    """Source/clock qualification in original Owners; never auto-predict parameters.

    A filing-vintage annual PE is only a sparse retrospective anchor, NOT a
    contemporaneously downloaded price vintage, TTM distribution or fair value.
    Split-affected fiscal windows are rejected uniformly, without conversion.
    """
    from zoneinfo import ZoneInfo
    from research_v2 import sec_disclosures as sec
    from backend.services.s3_method_research import identity_projection
    from backend.services.execution_plan.kline_provider import decode_av_daily,decode_av_splits
    entry=manifest['companies'][symbol]
    selected=[n for n in profile.source_notes if n.role in {'CURRENT_DISCLOSURE_CONTEXT','HISTORICAL_PE_ANCHOR','OFFICIAL_IR_CONTEXT'}]
    context=dict(disclosures=[],research_documents=[],historical_pe=dict(status='UNKNOWN',samples=[],rejected=[],
        scope='SPARSE_RECONSTRUCTED_ASFILED_ANNUAL_PE_NOT_TTM_DISTRIBUTION; RAW_PRICE_FETCHED_AT_CURRENT_RESEARCH_TIME',
        rule_version='s3-p2.1.asfiled-pe.v1'))
    if not selected:return context
    def raw(kind):
        rec=entry[kind];path=archive.safe_file(root,rec['path'])
        if archive.file_hash(path)!=rec['sha256'] or engine.stamp(rec['fetched_at'])>engine.stamp(decision_at):
            raise ValueError('P2_CALIBRATION_SOURCE_HASH_OR_CLOCK')
        return path.read_bytes(),rec['fetched_at']
    directory,submissions=raw('directory'),raw('submissions')
    issuer,_,_=identity_projection(symbol,directory,submissions)
    filings=sec.discover_filings(issuer,json.loads(submissions[0]))
    for note in selected:
        if note.role=='OFFICIAL_IR_CONTEXT':
            from research_v2.official_disclosures import verify_current_issuer_context
            receipt=next(r for r in manifest.get('requests',[]) if r.get('path')==note.path and r.get('url')==note.url)
            proof=verify_current_issuer_context(archive.safe_file(root,note.path).read_bytes(),receipt,issuer,decision_at)
            context['research_documents'].append(dict(**proof,summary=note.summary,
                role='HUMAN_CHECKED_CONTEXT_NOT_QUALIFIED_GAAP_OR_FIVE_YEAR_GUIDANCE'))
            continue
        filing=next((f for f in filings if f['full_url']==note.url),None)
        receipt=next(r for r in entry['originals'] if r['path']==note.path)
        body=archive.safe_file(root,note.path).read_bytes()
        proof=sec.verify_filing_envelope((body,receipt['fetched_at']),issuer,filing)
        if (not proof or engine.stamp(proof['published_at'])>engine.stamp(decision_at)
                or engine.stamp(proof['fetched_at'])>engine.stamp(decision_at)):
            raise ValueError('P2_CALIBRATION_DISCLOSURE_CLOCK_OR_IDENTITY')
        if note.role=='CURRENT_DISCLOSURE_CONTEXT':
            context['disclosures'].append(dict(**proof,form=filing['form'],report_date=filing['report_date'],
                summary=note.summary,role='HUMAN_VERIFIED_RESEARCH_CONTEXT_NOT_OWNER_GAAP_METRIC_OR_FIVE_YEAR_GUIDANCE'))
            continue
        try:
            eps=(sec.revalidate_asfiled_snapshot((body,receipt['fetched_at']),issuer,filing,annual_snapshots[note.url])
                 if annual_snapshots is not None else sec.asfiled_annual_eps((body,receipt['fetched_at']),issuer,filing))
            price_raw,_=raw('price');split_raw,_=raw('splits')
            if (entry['price'].get('function')!='TIME_SERIES_DAILY' or entry['splits'].get('function')!='SPLITS'
                    or entry['price'].get('symbol')!=symbol or entry['splits'].get('symbol')!=symbol):
                raise ValueError('HISTORICAL_PRICE_FUNCTION_OR_IDENTITY')
            bars=decode_av_daily(json.loads(price_raw),symbol,count=100000,strict_identity=True).bars
            public_day=engine.stamp(proof['published_at']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
            eligible=bars[bars['date']>public_day]
            if eligible.empty:raise ValueError('NO_POST_PUBLICATION_PRICE')
            row=eligible.iloc[0];day=row['date']
            from datetime import datetime
            closed=datetime.fromisoformat(day+'T16:00:00').replace(tzinfo=ZoneInfo('America/New_York'))
            if closed.weekday()>=5 or closed>min(engine.stamp(decision_at),engine.stamp(entry['price']['fetched_at'])):
                raise ValueError('HISTORICAL_PRICE_NOT_CLOSED_AT_RESEARCH_CLOCK')
            numbers=[Decimal(str(row[k])) for k in ('open','high','low','close','volume')]
            o,h,l,c,v=numbers
            if not all(n.is_finite() and n>0 for n in numbers) or not l<=min(o,c)<=max(o,c)<=h:
                raise ValueError('HISTORICAL_OHLCV_REJECTED')
            # No hidden split adjustment from fiscal start through observed close.
            events=decode_av_splits(json.loads(split_raw),symbol)
            if any(eps['start']<=e['effective_date']<=day and Decimal(e['split_factor'])!=1 for e in events):
                raise ValueError('SPLIT_AFFECTED_FISCAL_TO_PRICE_WINDOW')
            price=Decimal(str(row['close']))
            if not price.is_finite() or price<=0:raise ValueError('HISTORICAL_PRICE_NOT_POSITIVE')
            context['historical_pe']['samples'].append(dict(**eps,price_date=day,price=str(price),
                pe=engine.text(price/Decimal(eps['diluted_eps'])),
                operating_context_note=note.summary,
                price_source_sha256=entry['price']['sha256'],split_source_sha256=entry['splits']['sha256'],
                price_fetched_at=entry['price']['fetched_at'],split_fetched_at=entry['splits']['fetched_at']))
        except ValueError as exc:
            context['historical_pe']['rejected'].append(dict(source_ref=note.url,reason=pool._error_code(exc,'ASFILED_PE_INPUT_REJECTED')))
    if context['historical_pe']['samples']:context['historical_pe']['status']='PARTIAL_SPARSE_ANNUAL_ANCHORS'
    return context


def replay(path):
    b=archive.read_bundle(path);p=b['payload']
    for name,expected in p['method_files'].items():
        if archive.file_hash(archive.ROOT/name)!=expected:raise ValueError('METHOD_DRIFT_REPLAY_REQUIRES_ORIGINAL_VERSION')
    parent=archive.read_bundle(Path(path).parent/'sources/baseline/bundle.json')
    if parent['artifact_id']!=p['parent_artifact_id']:raise ValueError('P2_PARENT_ID_MISMATCH')
    parent_path=Path(path).parent/'sources/baseline/bundle.json'
    if 'professional_context' in p:
        from backend.services.s3_professional_research import prepare,provenance
        dossier,context=prepare(parent_path)
        rebuilt,_=make_bundle(parent_path,dossier,professional_context=context,
            entry_provenance=provenance(parent['payload']['entry_provenance'],context),_frozen_owner=True)
    else:rebuilt,_=make_bundle(parent_path,p['dossier'])
    if rebuilt!=b:raise ValueError('RECOMPUTATION_DIFFERENCE')
    return dict(verified=True,artifact_id=b['artifact_id'],result_sha256=archive.digest(p['result']))
