"""Adapters into existing owners; no parallel engine, asset read or model call."""
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from .contracts import canonical
from .runtime import context, current, diagnose, assets_qualified
from .parser import PolicyError


def allocation_context(portfolio_id=None):
    value = context(portfolio_id, consumer="allocation", view="portfolio")
    if value["mode"] == "UNAVAILABLE":
        raise PolicyError("POLICY_CONTEXT_UNAVAILABLE")
    return value


def targets():
    from app.allocation.types import AssetTarget, AllocAssetClass
    c = allocation_context()
    n = c['targets']['neutral']
    n = {k: Decimal(v) for k,v in n.items()}
    weights = {'cash':n['cash'], 'fixed':n['ib01']+n['cbu3'],
               'equity':n['us_etf']+n['us_direct']+n['china'], 'alt':n['gold']+n['btc'], 'deriv':Decimal(0)}
    return [AssetTarget(asset_class=AllocAssetClass(k), floor_ratio=float(v), ceiling_ratio=float(v),
                        mid_ratio=float(v),cash_min_amount=None,cash_max_amount=None) for k,v in weights.items()]


def conditional_plan(amount, portfolio_id=None):
    from app.allocation.types import AllocationResult, DisciplineCheckResult
    allocation_context(portfolio_id)
    # User-entered money is not evidence of available cash/debt eligibility.
    return AllocationResult(total_amount=amount, allocations={}, plan_items=[],
        discipline_check=DisciplineCheckResult(passed=False, violations=[]))


def qualified_bucket(evidence):
    from .store import clock, utc_now
    try:
        return bool(evidence.value is not None and evidence.value.is_finite()
            and 0 <= evidence.value <= 1 and evidence.complete and evidence.fresh
            and evidence.identity_verified and evidence.denominator_qualified
            and evidence.as_of is not None
            and timedelta(0) <= clock(utc_now()) - clock(evidence.as_of) <= timedelta(minutes=5))
    except PolicyError:
        return False


def deviation(portfolio_id):
    from .contracts import Contract
    from pydantic import Field
    c = allocation_context(portfolio_id)
    session=current(portfolio_id)
    class Diagnostic(Contract):
        status: str
        policy_reference: dict
        buckets: dict = Field(default_factory=dict)
        risk_diagnostic: dict = Field(default_factory=dict)
        execution_authorized: bool = False
    if not assets_qualified(session,complete=True):
        return Diagnostic(status='UNKNOWN',policy_reference=c['policy_reference'],risk_diagnostic=diagnose(portfolio_id))
    rows={}
    for key,evidence in session.assets.bucket_weights.items():
        if key not in c['targets']['neutral'] or not qualified_bucket(evidence):
            continue
        rows[key]={'current':str(evidence.value),'target':c['targets']['neutral'][key],
                   'deviation':str(evidence.value-Decimal(c['targets']['neutral'][key])),
                   'action':'INFORMATION_ONLY_NOT_MAJOR_CLASS_TRIGGER'}
    return Diagnostic(status='QUALIFIED_SCOPE_DIAGNOSTIC' if len(rows)==len(c['targets']['neutral']) and sum(Decimal(row['current']) for row in rows.values())==1 else 'UNKNOWN',
        policy_reference=c['policy_reference'],buckets=rows,risk_diagnostic=diagnose(portfolio_id))


def snapshot(portfolio_id):
    from app.allocation.types import AllocationSnapshot,ClassAllocation
    c=allocation_context(portfolio_id)
    session=current(portfolio_id)
    if not assets_qualified(session,complete=True):
        raise PolicyError('POLICY_FULL_INVESTABLE_DENOMINATOR_UNVERIFIED')
    rows=session.assets.bucket_weights
    if set(rows)!=set(c['targets']['neutral']) or any(not qualified_bucket(e) for e in rows.values()):
        raise PolicyError('POLICY_BUCKET_CLASSIFICATION_UNVERIFIED')
    weights={k:e.value for k,e in rows.items()}
    if sum(weights.values())!=1:
        raise PolicyError('POLICY_ASSET_WEIGHTS_NOT_CLOSED')
    groups={'cash':weights['cash'],'fixed':weights['ib01']+weights['cbu3'],
        'equity':weights['us_etf']+weights['us_direct']+weights['china'],'alt':weights['gold']+weights['btc'],'deriv':Decimal(0)}
    return AllocationSnapshot(total_investable_assets=float(session.assets.denominator_cny),
        by_class={k:ClassAllocation(ratio=float(v),amount=float(v*session.assets.denominator_cny)) for k,v in groups.items()})


def discipline_tool(asset_name, portfolio_id):
    from backend.graph.tools import DisciplineCheckOutput
    value = diagnose(portfolio_id, issuer_symbol=asset_name)
    # Never guess issuer ID from a display name. Source-owner keyed evidence only.
    session = current(portfolio_id)
    evidence = session.assets.issuer_weights.get(asset_name) if session.assets else None
    weight = float(evidence.value) if evidence and evidence.value is not None else 0.0
    # Stored weight remains only a compatibility field; diagnostic quality carries
    # the authoritative denominator. Loading the original positions is unnecessary.
    return DisciplineCheckOutput(violation=value['status']=='HARD_BLOCK', warning=canonical(value),
        current_weight=weight, max_position=None, position_ratio=None, rule_details=[canonical(value)],
        rule_provenance=value.get('policy_reference') or {}, personal_discipline_status=value['status'],
        weight_basis='POLICY_QUALIFIED_EVIDENCE_OR_UNKNOWN')


def finalize_output(plan, executed, expressed):
    session = current(plan.portfolio_id)
    if session.mode == 'LEGACY':
        return
    session.ensure_current()
    value = context(plan.portfolio_id,consumer='peer',view='portfolio')
    expressed.actionable = False
    expressed.actionable_hint = None
    # Reuse the original evidence-qualified renderer. Arbitrary model prose and
    # action fields are never a qualified policy output, regardless of spelling.
    from backend.services.market_data.output_policy import _explanations_with_trace, render_explanation, facts_only_result
    from .store import clock, utc_now
    assessments = ([executed.position_evidence] if getattr(executed,'position_evidence',None)
                   else list(getattr(executed,'portfolio_evidence',[]) or []))
    combined={'records':[r for a in assessments for r in a.get('records',[])],
              'verified_field_records':[r for a in assessments for r in a.get('verified_field_records',[])]}
    explanation_input=SimpleNamespace(structured_payload=expressed.structured_payload or {},
        llm_result=getattr(expressed,'llm_result',None),raw_text=getattr(expressed,'raw_text',''))
    explanations, diagnostics = _explanations_with_trace(explanation_input,combined)
    fact_text=[]
    for assessment in assessments:
        retained=facts_only_result(assessment,at=clock(utc_now()))
        if retained:
            fact_text.append(retained[2])
    expressed.structured_payload = {'mode':'policy_qualified_research','policy_context':value,
        'policy_reference':value.get('policy_reference'), 'execution_authorized':False,
        'professional_explanation':explanations,'explanationDiagnostics':diagnostics,'policy_precision':'CANNOT_QUANTIFY'}
    expressed.chat_answer = ('\n\n'.join(fact_text) if fact_text else '当前未取得足够的可核验经营/技术事实；以下保留政策条件及UNKNOWN，不提供精确交易数量或金额。')
    expressed.chat_answer += render_explanation(explanations)
    expressed.raw_text=''
    loaded = getattr(executed,'loaded_data',None)
    if loaded:
        loaded.policy_context = value
    ref = value.get('policy_reference') or {}
    expressed.chat_answer = (expressed.chat_answer or '') + '\n\n政策引用：' + str(ref.get('bundle_id','UNAVAILABLE')) + ' / ' + str(ref.get('bundle_version','UNKNOWN')) + '。政策不是交易授权；发行人和ETF穿透资格不足时风险仍为UNKNOWN。'
    original=getattr(expressed,'llm_result',None)
    if original:
        from copy import copy
        projected=copy(original)
        projected.chat_answer=expressed.chat_answer
        if hasattr(projected,'structured_result'):
            projected.structured_result=expressed.structured_payload
        if hasattr(projected,'raw_payload'):
            projected.raw_payload=expressed.structured_payload
        if hasattr(projected,'raw_output'): projected.raw_output=''
        if hasattr(projected,'reasoning'): projected.reasoning=[p['text'] for p in explanations]
        if hasattr(projected,'risk'): projected.risk=[p['text'] for p in explanations if p.get('topic')=='risk']
        if hasattr(projected,'strategy'): projected.strategy=[]
        if hasattr(projected,'decision'): projected.decision='HOLD'
        if hasattr(projected,'original_decision'): projected.original_decision=None
        expressed.llm_result=projected


def policy_document(role, portfolio_id=None):
    c = context(portfolio_id,consumer='web',view='full')
    if c['mode'] != 'POLICY_ACTIVE':
        raise PolicyError('POLICY_CONTEXT_UNAVAILABLE')
    if role == 'philosophy':
        text = c['philosophy']['text']
    else:
        # Section chunks are source fragments, not a rewritten authority.
        text = '\n\n'.join(v['text'] for k,v in c['clauses'].items() if k!='philosophy')
    return {'source':'policy_bundle','content':text,'policy_reference':c['policy_reference']}


def portfolio_alerts(portfolio_id):
    from app.analyzer import DeviationAlert
    value=diagnose(portfolio_id)
    rows=[]
    for trigger in value.get('triggers',[]):
        rows.append(DeviationAlert(alert_type='统一政策',severity='高' if trigger['action']=='HARD_BLOCK' else '中',
            title=trigger['action']+' · '+trigger['rule_id']+(' · '+trigger['issuer'] if trigger.get('issuer') else ''),description=canonical({'trigger':trigger,'policy_reference':value['policy_reference'],'auto_trade_allowed':False}),
            current_value=float(trigger['value']) if 'value' in trigger else None,
            target_value=float(trigger['threshold']) if 'threshold' in trigger else None,deviation=None))
    if value.get('unknown') or value['status']=='UNKNOWN':
        rows.append(DeviationAlert(alert_type='政策数据资格',severity='中',title='UNKNOWN · 不能宣布纪律全部通过',
            description=canonical(value),current_value=None,target_value=None,deviation=None))
    return rows
