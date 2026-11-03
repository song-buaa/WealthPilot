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

VERSION='s3-p2.operating-earnings.v1'
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
    role: str = Field(pattern='^(HISTORICAL_BUSINESS_CONTEXT|EXPIRED_GUIDANCE_NOT_FORWARD_INPUT|PRIOR_GUIDANCE_NOT_RECONFIRMED)$')


class Profile(engine.Contract):
    cautious: OperatingScenario
    base: OperatingScenario
    optimistic: OperatingScenario
    notes: dict[str,Note]
    business_thesis: str = Field(min_length=40,max_length=4000)
    risks: list[str] = Field(min_length=2)
    review_conditions: list[str] = Field(min_length=2)
    current_guidance: str = Field(pattern='^(UNKNOWN_NO_QUALIFIED_CURRENT_GUIDANCE|PARTIAL_PRIOR_GUIDANCE_NOT_RECONFIRMED)$')
    source_notes: list[SourceNote] = Field(min_length=1)


class Dossier(engine.Contract):
    version: str = Field(pattern='^s3-p2.research-dossier.v1$')
    parent_artifact_id: str = Field(pattern='^[a-f0-9]{64}$')
    research_recorded_at: AwareDatetime
    companies: dict[str,Profile] = Field(min_length=1,max_length=20)


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


def evaluate(qualified,dossier,assessments):
    """Same qualified facts/price/quality and research thresholds as P1."""
    dossier=Dossier.model_validate(dossier)
    with localcontext() as ctx:
        ctx.prec=34
        cards=[];failures={};baselines={}
        for old in qualified['cards']:
            symbol=old['symbol']
            try:
                profile=dossier.companies[symbol]
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
                    valuation_status='UNKNOWN',failure_reason=reason,rank=None)
                cards.append(card);failures[symbol]=reason
        params={c['symbol']:c['assumptions'] for c in cards}
        request=engine.ResearchRequest.model_validate(dict(pool_id='s3_p2',pool_version='v1',
            purpose='Retrospective same-input model comparison, not an investment signal',
            decision_at=qualified['decision_at'],source_manifest_sha256='0'*64,companies=params,
            tie_band=qualified['ranking']['tie_band'],downside_floor=qualified['ranking']['downside_floor'],
            downside_tie_band=qualified['ranking']['downside_tie_band']))
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
            ranking={**qualified['ranking'],'stable':not changes,'changes':changes})
        return result


def make_bundle(parent_path,dossier_raw):
    parent_path=Path(parent_path)
    parent=archive.read_bundle(parent_path);dossier=Dossier.model_validate(dossier_raw);p=parent['payload']
    if parent['artifact_id']!=dossier.parent_artifact_id:raise ValueError('P2_PARENT_ID_MISMATCH')
    if dossier.research_recorded_at<engine.stamp(p['request']['decision_at']):raise ValueError('P2_RESEARCH_CLOCK_INVALID')
    if not set(dossier.companies)<=set(p['request']['companies']):raise ValueError('P2_POOL_IDENTITY_MISMATCH')
    request=deepcopy(p['request']);request['companies']={s:request['companies'][s] for s in dossier.companies}
    request['assumption_context']={s:request['assumption_context'][s] for s in dossier.companies}
    # Requalify original bytes through their existing Owners. Never trust a
    # manually supplied metric or modify the parent to fit this new model.
    requalified,_=pool.make_bundle(parent_path.parent/'sources/inputs',request)
    q=requalified['payload'];old={c['symbol']:c for c in p['result']['cards']}
    root=parent_path.parent/'sources/inputs'
    manifest=archive._load(root,'manifest.json',p['request']['source_manifest_sha256'])
    for symbol,profile in dossier.companies.items():
        receipts=manifest['companies'][symbol].get('originals',[])
        for note in profile.source_notes:
            if not any(r.get('path')==note.path and r.get('sha256')==note.sha256 and r.get('url')==note.url for r in receipts):
                raise ValueError('P2_SOURCE_NOTE_IDENTITY_REJECTED')
            raw=archive.safe_file(root,note.path).read_bytes()
            if (archive.file_hash(archive.safe_file(root,note.path))!=note.sha256 or note.begin>=note.end
                    or note.end>len(raw) or hashlib.sha256(raw[note.begin:note.end]).hexdigest()!=note.excerpt_sha256):
                raise ValueError('P2_SOURCE_NOTE_EXCERPT_REJECTED')
    for card in q['result']['cards']:
        for field in ('quality','cross_checks','scenarios','assumptions','comparison_status'):
            if card[field]!=old[card['symbol']][field]:raise ValueError('P2_BASELINE_OWNER_REQUALIFICATION_DIFFERENCE')
    result=evaluate(q['result'],dossier,q['assessments'])
    files={'baseline/bundle.json':parent_path}
    files.update({'baseline/'+name:archive.safe_file(parent_path.parent,name) for name in p['view_hashes']})
    files.update({'baseline/sources/'+name:archive.safe_file(parent_path.parent/'sources',name) for name in p['source_files']})
    methods=tuple(q['method_files'])+('backend/services/s3_operating_research.py','scripts/s3_operating_verify.py')
    files.update({'methods/'+name:archive.ROOT/name for name in methods})
    payload=dict(request=request,dossier=dossier.model_dump(mode='json'),assessments=q['assessments'],
        qualified_baseline=q['result'],parent_artifact_id=parent['artifact_id'],result=result,
        method_files={name:archive.file_hash(archive.ROOT/name) for name in methods},
        source_files={name:archive.file_hash(path) for name,path in sorted(files.items())})
    payload['view_hashes']=archive.view_hashes(result)
    return dict(artifact_id=archive.digest(payload),payload=payload),files


def replay(path):
    b=archive.read_bundle(path);p=b['payload']
    for name,expected in p['method_files'].items():
        if archive.file_hash(archive.ROOT/name)!=expected:raise ValueError('METHOD_DRIFT_REPLAY_REQUIRES_ORIGINAL_VERSION')
    parent=archive.read_bundle(Path(path).parent/'sources/baseline/bundle.json')
    if parent['artifact_id']!=p['parent_artifact_id']:raise ValueError('P2_PARENT_ID_MISMATCH')
    rebuilt,_=make_bundle(Path(path).parent/'sources/baseline/bundle.json',p['dossier'])
    if rebuilt!=b:raise ValueError('RECOMPUTATION_DIFFERENCE')
    return dict(verified=True,artifact_id=b['artifact_id'],result_sha256=archive.digest(p['result']))
