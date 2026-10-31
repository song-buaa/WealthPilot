"""Service-level SSE adapter; existing PEER computes facts and conclusions once."""
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import uuid
from copy import deepcopy
from app.analysis_runtime import budgeted_analysis, current as runtime_budget
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Turn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=12000)


from backend.services.allocation_scenarios import StageTargets, ScenarioLeg
from backend.services.position_analysis import ResearchIntent


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=6000)
    symbol: str | None = Field(default=None, min_length=1, max_length=80)
    history: list[Turn] = Field(default_factory=list, max_length=16)
    snapshot_id: str | None = Field(default=None, pattern=r'^asset-view:v1:[1-9][0-9]{0,17}$')
    analysis_scope: Literal['investments', 'net_worth'] = 'investments'
    target_versions: list[StageTargets] = Field(default_factory=list,max_length=16)
    scenario_legs: list[ScenarioLeg] = Field(default_factory=list,max_length=20)
    research_context: ResearchIntent | None = None

    @model_validator(mode="after")
    def scenarios_use_same_view(self):
        if self.scenario_legs and (not self.snapshot_id or any(s.snapshot_id!=self.snapshot_id for s in self.scenario_legs)):
            raise ValueError("CROSS_SNAPSHOT_SCENARIO_REJECTED")
        return self

    @field_validator('question')
    @classmethod
    def question_has_content(cls, value: str) -> str:
        # Some connector validators full-match JSON Schema patterns instead of
        # searching. Keep the nonblank rule server-side, without changing text.
        if not value.strip():
            raise ValueError('question must contain non-whitespace content')
        return value


from app.llm_observation import observed_result, safe_metadata


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['wealthpilot.analysis.v1'] = 'wealthpilot.analysis.v1'
    synthetic: bool
    scenario: str
    status: Literal['ok', 'partial', 'unavailable']
    issues: list[str]
    queried_at: str
    conclusion: str
    intent: dict = Field(default_factory=dict)
    professional_result: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)
    data_quality: dict = Field(default_factory=dict)
    scope: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    professional_analysis_complete: bool = False
    missing_stages: list[str] = Field(default_factory=list)
    execution_authorized: Literal[False] = False
    order_created: Literal[False] = False
    snapshot_id: str | None = None
    llm_observation: dict | None = None

    @field_validator("llm_observation")
    @classmethod
    def safe_observation(cls, value):
        return safe_metadata(value)


@contextmanager
def readonly_business_database(path, contract=None, *, worker_process=False):
    """Dedicated worker only: every original service session remains SQLite RO."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool
    from app import database
    path = Path(path).resolve(strict=True)
    if contract:
        from app.shared_sqlite import verify
        verify(path, contract, integrity=True)
    def connect():
        raw = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)
        raw.execute('PRAGMA query_only=ON')
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION}
        def authorize(action, arg1, arg2, db, trigger):
            return sqlite3.SQLITE_OK if action in allowed or (action == sqlite3.SQLITE_PRAGMA and arg2 is None) else sqlite3.SQLITE_DENY
        raw.set_authorizer(authorize)
        return raw
    previous_flag = os.environ.get("WEALTHPILOT_EPHEMERAL_ANALYSIS")
    os.environ["WEALTHPILOT_EPHEMERAL_ANALYSIS"] = "true"
    previous = database._engine, database._SessionLocal
    engine = create_engine('sqlite://', creator=connect, poolclass=NullPool)
    database._engine, database._SessionLocal = engine, None
    try:
        yield
    finally:
        # A single-use worker can still have optional provider threads finishing.
        # Keep its engine/flag read-only until process exit, including after timeout.
        if not worker_process:
            engine.dispose()
            database._engine, database._SessionLocal = previous
            if previous_flag is None:
                os.environ.pop("WEALTHPILOT_EPHEMERAL_ANALYSIS", None)
            else:
                os.environ["WEALTHPILOT_EPHEMERAL_ANALYSIS"] = previous_flag


def encode(value):
    if hasattr(value, 'model_dump'):
        return encode(value.model_dump(mode='json'))
    if is_dataclass(value):
        return encode(asdict(value))
    if isinstance(value, dict):
        return {str(k): encode(v) for k,v in value.items() if not any(x in str(k).lower() for x in
                ('api_key','private_key','token','account_id','parent_doc_path','raw_portfolio','raw_output'))}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    if isinstance(value, (str,int,float,bool)) or value is None:
        return value
    if hasattr(value,'isoformat'):
        return value.isoformat()
    if hasattr(value,'value'):
        return value.value
    return None


def quality(portfolio_id):
    from app.database import get_session
    from app.models import Position
    from pocs.chatgpt_skills.reader import source_quality
    issues, dates = [], []
    with get_session() as session:
        rows = session.query(Position).filter_by(portfolio_id=portfolio_id, segment='投资').all()
        for row in rows:
            updated, warnings = source_quality(session.connection(),row)
            issues.extend(warnings)
            if updated:
                dates.append(updated)
        if not rows:
            issues.append('EMPTY_INVESTMENT_DATA')
    return dict(issues=sorted(set(issues)), position_source_dates=sorted(set(dates)),
                all_position_dates_known=bool(rows) and len(dates)==len(rows))


def captured_evidence(result, captured, portfolio_id):
    executed, expressed = captured['executed'], captured['expressed']
    loaded = executed.loaded_data
    result.scope = dict(currency='CNY',amount_unit='major',
        analysis_denominator='aggregated stored investment holdings; excludes personal manual wealth and live cash',
        investment_holdings_total_cny=loaded.total_assets,
        holdings_cost_price_semantics='aggregated total cost value in CNY, not per-share price',
        profile_basis='existing DataLoader default profile, not verified personal risk preferences')
    pattern = result.professional_result.get('pattern_evidence')
    result.professional_result = {
        'analysis_result':encode(expressed.structured_payload), 'mode':expressed.mode,
        'decision_id':result.professional_result.get('decision_id'),
        'validator':result.professional_result.get('validator'),
    }
    result.evidence.update(holdings=encode(loaded.positions),target_position=encode(loaded.target_position),
        position_evidence=encode(getattr(executed,"position_evidence",{})),
        effective_rules_provenance=encode(getattr(loaded,"rules_provenance",{})),
        new_entry=loaded.is_new_entry,rules=encode(loaded.rules),rule_result=encode(executed.rule_result),
        signals=encode(executed.signal_result),market_data=encode(executed.market_data),research=encode(loaded.research),
        pattern_evidence=pattern,
        retrieved_principles=encode(loaded.retrieved_principles),retrieved_research_views=encode(loaded.retrieved_research_views),
        invoked_skills=executed.invoked_skills,data_warnings=encode(loaded.data_warnings))
    result.issues.extend(getattr(executed,'position_evidence',{}).get('issues',[]))
    if loaded.data_warnings:
        result.issues.append('DATA_LOADER_WARNINGS')
    if expressed.mode=='fallback' or getattr(expressed.llm_result,'is_fallback',False):
        result.status='unavailable';result.issues.append('PROFESSIONAL_MODEL_UNAVAILABLE')
    if not captured['reviewed'].passed:
        result.issues.append('PROFESSIONAL_REVIEW_WARNING')
    if result.scenario=='review_portfolio':
        from backend.services import allocation_service as allocation
        from app.allocation.calculator import build_deviation_snapshot
        snapshot,targets=allocation.get_snapshot(portfolio_id),allocation.get_targets()
        result.evidence['allocation']=dict(snapshot=encode(snapshot),targets=encode(targets),
            deviation=encode(build_deviation_snapshot(snapshot,targets)),
            target_basis='existing fixed default targets, not customized ranges',
            denominator='AllocationService positive, classified investment holdings only; excludes unclassified_amount, WealthItems and live cash')
        if snapshot.has_unclassified:
            result.issues.append('ALLOCATION_UNCLASSIFIED_HOLDINGS')
        result.limitations.append('Industry concentration unavailable unless supported by existing evidence; do not infer sectors.')
    if result.scenario=='analyze_performance':
        from decision_engine.llm_engine import _build_performance_data
        result.evidence['performance']=_build_performance_data(loaded)
        result.limitations.extend(['Current holdings book/unrealized P&L only; no period return, TWR, MWR, realized trade or causal strategy attribution.',
                                  'Original performance ranking rounds CNY to whole yuan.'])
    if result.scenario=='analyze_position':
        market=executed.market_data
        parts=[getattr(market,k,None) for k in ('quote','fundamentals','capital_flow','technical')] if market else []
        available=[p for p in parts if p is not None]
        if not available:
            result.issues.append('MARKET_DATA_UNAVAILABLE')
        for part in available:
            if not getattr(part,'data_as_of',None):
                result.issues.append('MARKET_SOURCE_DATE_UNKNOWN')
            if getattr(part,'missing_fields',None):
                result.issues.append('MARKET_DATA_INCOMPLETE')
            if any(s in str(getattr(part,'source','')).lower() for s in ('seed','demo','mock')):
                result.issues.append('DEMONSTRATION_MARKET_SOURCE')
    if not loaded.retrieved_principles:
        result.issues.append('PRINCIPLES_RETRIEVAL_UNAVAILABLE')
    if loaded.research and not loaded.retrieved_research_views:
        result.issues.append('RESEARCH_PROVENANCE_UNVERIFIED')
    if loaded.is_new_entry:
        result.limitations.append('Virtual-security evaluation path does not establish a current holding or purchase intent; use the qualified conclusion and risks.')
    result.limitations.append('Original research may fall back to examples without verified dates; do not present unverified research as live evidence.')


def retain_facts(result, assessment, *, asset_view=None, missing_stages=None):
    from backend.services.market_data.output_policy import facts_only_result
    retained=facts_only_result(assessment,at=datetime.now(timezone.utc),asset_view=asset_view)
    if not retained:return False
    assessment,facts,conclusion=retained
    result.status='partial'
    result.conclusion=conclusion
    result.professional_analysis_complete=False
    result.missing_stages=missing_stages or ['professional_result']
    result.evidence['position_evidence']=encode(assessment)
    result.evidence['qualified_facts']=encode(facts)
    result.professional_result=dict(analysis_result=dict(mode='facts_only',
        professional_analysis_complete=False,execution_authorized=False,
        professional_explanation=[],evidence_refs=[r.get('record_id') or r['source_url'] for r in facts]))
    result.issues=sorted(set(result.issues+['PROFESSIONAL_INFERENCE_INCOMPLETE']))
    result.limitations.append('Only qualified source facts survived; professional inference and decision review are incomplete. No investment decision or trade authorization.')
    return True


def deadline_result(value):
    """Late synchronous completion cannot be accepted as timely professional work."""
    result=AnalysisResult.model_validate(value)
    if result.professional_result.get('analysis_result',{}).get('mode')=='facts_only' and 'ANALYSIS_TIMEOUT' in result.issues:
        return result.model_dump(mode='json')
    result.status='unavailable'
    result.conclusion='研究期限已到，专业结果未按时交付；未创建交易。'
    result.professional_result={}
    result.professional_analysis_complete=False
    result.missing_stages=['timely_delivery']
    result.issues=sorted(set(result.issues+['ANALYSIS_TIMEOUT']))
    assessment=result.evidence.get('position_evidence')
    if assessment:retain_facts(result,assessment,missing_stages=['timely_delivery'])
    return result.model_dump(mode='json')


@observed_result
@budgeted_analysis(on_expired=deadline_result)
async def analyze(path, portfolio_id, scenario, arguments, *, contract=None, synthetic=False, worker_process=False, asset_view=None, async_l1=False, deadline_monotonic=None):
    request=AnalysisRequest.model_validate(arguments)
    if request.snapshot_id and asset_view is None:
        from app.asset_views import load_view, frozen_input, public_view
        view = load_view(path, portfolio_id, request.snapshot_id, contract)
        with frozen_input(view) as fixed_path:
            return await analyze(fixed_path, portfolio_id, scenario, arguments, synthetic=synthetic,
                                 worker_process=worker_process, asset_view=public_view(view,
                                     symbol=request.symbol if scenario == 'analyze_position' else None,
                                     analysis_scope=request.analysis_scope),async_l1=async_l1,deadline_monotonic=deadline_monotonic)
    conversation='mcp-analysis-'+uuid.uuid4().hex
    result=AnalysisResult(synthetic=synthetic,scenario=scenario,status='unavailable',issues=[],
                          queried_at=datetime.now(timezone.utc).isoformat(),conclusion='')
    with readonly_business_database(path,contract,worker_process=worker_process):
        from app.database import get_session
        from app.models import Portfolio
        with get_session() as session:
            if session.get(Portfolio,portfolio_id) is None:
                raise ValueError('PORTFOLIO_NOT_FOUND')
        if contract:
            from app.shared_sqlite import load_contract
            if load_contract(contract)['portfolio_id']!=portfolio_id:
                raise ValueError('PORTFOLIO_NOT_FOUND')
        from backend.services.decision_service_v3 import run_chat_stream_v3
        from backend.services import decision_service
        from backend.services.portfolio_history.query import query as historical_query
        with get_session() as session:
            history=historical_query(session,portfolio_id,symbol=request.symbol,synthetic=synthetic)
        history['snapshot_id']=request.snapshot_id
        result.evidence['historical_attribution']=history
        result.limitations.append('Historical attribution is separate from current-holdings book P&L; only broker-imported facts and expressly supported metrics are authoritative. No portfolio TWR/MWR.')
        captured={}
        stage_receipt={}
        def capture_stage(plan, executed, stage='evidence_ready'):
            if scenario=='analyze_position' and plan.route=='position_single' and executed.position_evidence:
                if stage=='evidence_ready':
                    stage_receipt.update(assessment=deepcopy(executed.position_evidence),missing=['expressing','reviewing'])
                elif stage_receipt:
                    completed={'expressing_complete':'expressing','reviewing_complete':'reviewing'}.get(stage)
                    stage_receipt['missing']=[s for s in stage_receipt['missing'] if s!=completed]
        def capture(plan,executed,expressed,reviewed):
            captured.update(plan=plan,executed=executed,expressed=expressed,reviewed=reviewed)
        prefix={'analyze_position':'单标的投资决策','review_portfolio':'投资组合与资产配置评估','analyze_performance':'当前持仓收益表现分析'}[scenario]
        question=prefix+'：'+request.question
        question+='\n独立历史归因证据（不可用项必须保持未知；不替代当前持仓账面盈亏；仅展示前20组，完整范围由get_trade_history查询）：'+json.dumps(
            dict(status=history['status'],issues=history['issues'],attribution=history['attribution'][:20],
                 coverage=history['coverage'][:20]),ensure_ascii=False)
        if request.target_versions:
            from backend.services.allocation_scenarios import select_targets
            confirmed = select_targets([t.model_dump(mode="json") for t in request.target_versions])
            question+='\n版本化分析目标及确认来源（未核验目标仅作假设，不修改正式纪律）：'+json.dumps(confirmed,ensure_ascii=False)
        if asset_view and asset_view['data_quality'].get('analysis_eligibility'):
            question+='\n本次分析资格（仅限所列范围；不能代替交易授权）：'+json.dumps(
                {k:asset_view['data_quality'][k] for k in ('qualification_scope','analysis_eligibility')},ensure_ascii=False)
        if asset_view and not asset_view['data_quality']['can_propose_rebalance']:
            question+='\n本次固定资产数据存在过期、未知或不完整证据。仅在明确允许的 A 战略配置/B 金额规划范围内分析并披露限制；不提供可直接执行的精确数量或限价。'
        if request.symbol:
            question+='\n本次明确分析标的：'+request.symbol
        if scenario=='analyze_performance':
            question+='\n仅分析当前持仓盈亏与贡献，不推断历史期间收益率、资金流调整收益率或交易决策归因。'
        text,done,errors=[],{},[]
        result.data_quality=quality(portfolio_id)
        try:
            budget=runtime_budget()
            if budget and budget.remaining()<=0:raise TimeoutError()
            timeout_context=budget.timeout(budget.remaining()) if budget else asyncio.timeout(240)
            async with timeout_context:
                async for event in run_chat_stream_v3(question,conversation,portfolio_id,
                        conversation_history=[t.model_dump() for t in request.history],result_callback=capture,stage_callback=capture_stage if async_l1 else None,analysis_only=True,analysis_context=asset_view,
                            analysis_options=dict(target_versions=[t.model_dump(mode="json") for t in request.target_versions],
                                research_context=request.research_context.model_dump() if request.research_context else None,
                                scenario_legs=[s.model_dump(mode="json") for s in request.scenario_legs])):
                    event_type,data=event.split('\n',1)
                    data=json.loads(data.split('data: ',1)[1]);kind=event_type.removeprefix('event: ')
                    if kind=='text':text.append(data.get('delta',''))
                    elif kind=='intent':result.intent=data
                    elif kind=='done':done=data
                    elif kind=='error':errors.append(data.get('code','ANALYSIS_FAILED'))
                    elif kind=='candidates':result.evidence['clarification_candidates']=data.get('items',[])
                    elif kind=='validator_warning':result.issues.append('PROFESSIONAL_REVIEW_WARNING')
            result.conclusion=''.join(text)
            result.professional_result={k:v for k,v in done.items() if k not in ('rawText','actionable','actionable_hint','llm_observation')}
            route,intent=result.intent.get('planner_route'),result.intent.get('primary_intent')
            expected={'analyze_position':{'PositionDecision'},'review_portfolio':{'PortfolioReview','AssetAllocation'},'analyze_performance':{'PerformanceAnalysis'}}[scenario]
            if errors:result.issues.extend('PEER_'+str(e).upper() for e in errors)
            elif route in ('clarify','low_confidence'):
                result.status='partial';result.issues.append('NEEDS_CLARIFICATION')
            elif intent not in expected or route != ('position_single' if scenario == 'analyze_position' else 'portfolio'):
                result.issues.append('ANALYSIS_ROUTE_MISMATCH')
                result.conclusion = '分析路由与请求不符；请明确单一标的或组合范围后重试。'
                result.professional_result = {}
            elif done.get('conclusion_level')=='aborted' or not captured or not result.conclusion:
                result.issues.append('PROFESSIONAL_ANALYSIS_UNAVAILABLE')
            else:
                result.status='ok';captured_evidence(result,captured,portfolio_id)
                result.professional_analysis_complete = result.status != 'unavailable'
        except TimeoutError:
            result.status='unavailable'
            result.professional_analysis_complete=False
            result.issues.append('ANALYSIS_TIMEOUT')
        except asyncio.CancelledError:
            budget=runtime_budget()
            if not async_l1 or budget is None or not budget.owns_timeout_cancellation():raise
            # The absolute outer deadline can interrupt async-generator cleanup
            # after the inner work timeout. Preserve its already-captured facts.
            result.status='unavailable'
            result.professional_analysis_complete=False
            result.issues.append('ANALYSIS_TIMEOUT')
        except Exception:
            if not async_l1:raise
            result.status='unavailable'
            result.professional_analysis_complete=False
            result.issues.append('PROFESSIONAL_STAGE_FAILURE')
        finally:
            if async_l1 and result.status=='unavailable' and runtime_budget() is not None:
                runtime_budget().finished.set()  # no abandoned SDK sends during facts finalization
            decision_service.clear_session(conversation)
        if result.status == 'unavailable':
            # Failed/partial streams can already contain model text. Never
            # return it as a recommendation after route/error/timeout failure.
            result.professional_result = {}
            if 'ANALYSIS_ROUTE_MISMATCH' not in result.issues:
                result.conclusion = '专业分析未完成；请根据来源与错误信息补齐条件后重试。'
        result.issues=sorted(set(result.issues+result.data_quality['issues']))
        if asset_view:
            from app.asset_views import public_view
            from backend.services.allocation_scenarios import qualification_view
            # Recheck before computing scenarios. Portfolio amounts need the
            # entire denominator, and repayments also need current loan facts.
            original_precision = asset_view['data_quality'].get('can_propose_rebalance', False)
            asset_view = public_view(asset_view)
            asset_view['data_quality']['can_propose_rebalance'] &= original_precision
            if scenario == 'review_portfolio':
                asset_view = qualification_view(asset_view, request.scenario_legs)
        if (scenario == 'analyze_position' and result.status != 'unavailable'
                and all(key in captured for key in ('plan', 'executed', 'expressed', 'reviewed'))):
            from backend.services.position_analysis import prepare
            from backend.services.market_data.output_policy import enforce
            # Reuse the captured PEER result and the SAME immutable snapshot.
            # Adapter latency may invalidate an initially qualified nested plan.
            executed, expressed = captured['executed'], captured['expressed']
            executed.position_evidence = prepare(captured['plan'], executed, asset_view=asset_view,
                options=dict(target_versions=[t.model_dump(mode='json') for t in request.target_versions],
                             research_context=request.research_context.model_dump() if request.research_context else None,
                             research_question=request.question,research_history=[t.model_dump() for t in request.history],
                             scenario_legs=[s.model_dump(mode='json') for s in request.scenario_legs]))
            enforce(expressed, executed.position_evidence)
            result.evidence['position_evidence'] = encode(executed.position_evidence)
            result.issues = sorted(set(result.issues + executed.position_evidence.get('issues', [])))
            if result.status != 'unavailable':
                result.conclusion = expressed.chat_answer
                result.professional_result['analysis_result'] = encode(expressed.structured_payload)
        if scenario=='review_portfolio':
            from backend.services.allocation_scenarios import target_context
            from backend.services.allocation_service import get_targets, simulate_exit_scenario
            rule_source=result.evidence.get('effective_rules_provenance',{})
            effective=result.evidence.get('rules',{})
            context=target_context(asset_view or {'portfolio_id':portfolio_id},None,[t.model_dump(mode="json") for t in request.target_versions],
                effective if rule_source.get('basis')=='configured_rule_file' else None,
                dict(allocation=[t.model_dump(mode="json") for t in get_targets()],effective_rules=effective,rule_source=rule_source))
            result.evidence['allocation_context']=encode(context)
            if request.scenario_legs and asset_view:
                result.evidence['allocation_scenarios']=simulate_exit_scenario(asset_view,
                    [s.model_dump(mode="json") for s in request.scenario_legs],
                    amount_qualified=asset_view['data_quality'].get('analysis_eligibility',{}).get('B',{}).get('allowed',False),
                    target=context.get('confirmed_stage_targets') or context.get('proposed_stage_targets'))
            elif asset_view:
                from backend.services.exit_candidates import build_candidates
                result.evidence['exit_candidates']=[candidate
                    for group in asset_view.get('issuer_exposure',{}).get('companies',[])
                    for candidate in build_candidates(asset_view,group['issuer_id'],
                        context.get('confirmed_stage_targets') or context.get('proposed_stage_targets'),
                        amount_qualified=asset_view['data_quality'].get('analysis_eligibility',{}).get('B',{}).get('allowed',False))]
        if asset_view:
            result.snapshot_id = request.snapshot_id
            result.data_quality['asset_view'] = asset_view['data_quality']
            result.data_quality['context_current'] = asset_view['context_current']
            result.scope['snapshot_id'] = request.snapshot_id
            result.evidence['asset_view'] = dict(wealth_summary=asset_view['views']['wealth_summary'],
                portfolio_summary=asset_view['views']['portfolio_summary'], issuer_exposure=asset_view['issuer_exposure'])
            result.issues = sorted(set(result.issues + asset_view['data_quality']['issues']))
            if not asset_view['data_quality']['can_propose_rebalance']:
                result.conclusion = '数据存在过期或未知项；以下仅作一般分析，不构成可直接执行的精确调仓方案。\n'+result.conclusion
                result.limitations.append('Precise rebalancing requires a new qualified asset view; source, security price and FX times are separate.')
            if not asset_view['context_current']:
                result.status = 'unavailable'
                result.professional_result = {}
                result.issues = sorted(set(result.issues + ['ANALYSIS_CONTEXT_EXPIRED']))
                result.conclusion = '分析上下文已到期，请重新准备资产数据；本次结果不可用于精确调仓。'
        if (scenario == 'review_portfolio' and result.status != 'unavailable'
                and (not asset_view or asset_view['context_current'])):
            # D1 precision is a holdings-quality gate, not L3 research/technical
            # qualification. Missing snapshots and a true D1 C flag must never
            # allow free model text or nested numerical trade instructions.
            result.conclusion = '组合仅作一般分析、配置方向与静态情景复核；未完成逐标的L3证据核验，不提供可执行股数或限价。'
            scenarios = result.evidence.get('allocation_scenarios')
            if scenarios:
                result.conclusion += '\n静态情景（非交易建议）：' + json.dumps(encode(scenarios), ensure_ascii=False)
            result.professional_result = dict(analysis_result=dict(
                mode='restricted_portfolio_analysis',
                allocation_context=result.evidence.get('allocation_context'),
                allocation_scenarios=scenarios,
                allocation_facts=result.evidence.get('allocation'),
                analysis_eligibility=(asset_view or {}).get('data_quality', {}).get('analysis_eligibility', {}),
                can_propose_rebalance=False, execution_authorized=False))
            from backend.services.position_analysis import prepare_portfolio
            from backend.services.market_data.output_policy import _explanations_with_trace, render_explanation
            assessments=prepare_portfolio(captured['plan'],captured['executed']) if captured else []
            from backend.agents.contracts import ExpressionOutput
            expressed = captured['expressed'] if captured else ExpressionOutput()
            explanation, diagnostics = _explanations_with_trace(expressed,
                {'records':[r for a in assessments for r in a['records']],
                 'verified_field_records':[r for a in assessments for r in a.get('verified_field_records', [])]})
            result.professional_result['analysis_result']['explanationDiagnostics']=diagnostics
            result.professional_result['analysis_result']['professional_explanation']=explanation
            result.professional_result['analysis_result']['exit_candidates']=result.evidence.get('exit_candidates',[])
            result.conclusion+=render_explanation(explanation)
            result.issues = sorted(set(result.issues + ['PRECISE_PORTFOLIO_OUTPUT_BLOCKED']))
        if result.status=='unavailable':
            result.professional_analysis_complete=False
            result.missing_stages=stage_receipt.get('missing',[])
        if result.status=='unavailable' and async_l1 and scenario=='analyze_position' and stage_receipt:
            retain_facts(result,stage_receipt['assessment'],asset_view=asset_view,missing_stages=stage_receipt['missing'])
        if result.status=='ok' and result.issues:result.status='partial'
    return result.model_dump(mode='json')


if __name__ == '__main__':
    import sys
    if sys.argv[1:] != ['--snapshot-chat-worker']:
        raise SystemExit('Explicit snapshot worker mode required')
    from backend.services.snapshot_chat import worker_main
    worker_main()
