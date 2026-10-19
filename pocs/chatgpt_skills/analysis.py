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
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Turn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['user', 'assistant']
    content: str = Field(min_length=1, max_length=12000)


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=6000, pattern=r'\S')
    symbol: str | None = Field(default=None, min_length=1, max_length=80)
    history: list[Turn] = Field(default_factory=list, max_length=16)
    snapshot_id: str | None = Field(default=None, pattern=r'^asset-view:v1:[1-9][0-9]{0,17}$')


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
    order_created: Literal[False] = False
    snapshot_id: str | None = None


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
        new_entry=loaded.is_new_entry,rules=encode(loaded.rules),rule_result=encode(executed.rule_result),
        signals=encode(executed.signal_result),market_data=encode(executed.market_data),research=encode(loaded.research),
        pattern_evidence=pattern,
        retrieved_principles=encode(loaded.retrieved_principles),retrieved_research_views=encode(loaded.retrieved_research_views),
        invoked_skills=executed.invoked_skills,data_warnings=encode(loaded.data_warnings))
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
        result.limitations.append('Original new-entry buy_init tag identifies the evaluation path, not an unconditional buy instruction; use the actual conclusion and risks.')
    result.limitations.append('Original research may fall back to examples without verified dates; do not present unverified research as live evidence.')


async def analyze(path, portfolio_id, scenario, arguments, *, contract=None, synthetic=False, worker_process=False, asset_view=None):
    request=AnalysisRequest.model_validate(arguments)
    if request.snapshot_id and asset_view is None:
        from app.asset_views import load_view, frozen_input, public_view
        view = load_view(path, portfolio_id, request.snapshot_id, contract)
        with frozen_input(view) as fixed_path:
            return await analyze(fixed_path, portfolio_id, scenario, arguments, synthetic=synthetic,
                                 worker_process=worker_process, asset_view=public_view(view))
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
        captured={}
        def capture(plan,executed,expressed,reviewed):
            captured.update(plan=plan,executed=executed,expressed=expressed,reviewed=reviewed)
        prefix={'analyze_position':'单标的投资决策','review_portfolio':'投资组合与资产配置评估','analyze_performance':'当前持仓收益表现分析'}[scenario]
        question=prefix+'：'+request.question
        if asset_view and not asset_view['data_quality']['can_propose_rebalance']:
            question+='\n本次固定资产数据存在过期、未知或不完整证据，仅做一般非交易分析。明确说明限制，不给出可直接执行的精确调仓数量、比例或价格。'
        if request.symbol:
            question+='\n本次明确分析标的：'+request.symbol
        if scenario=='analyze_performance':
            question+='\n仅分析当前持仓盈亏与贡献，不推断历史期间收益率、资金流调整收益率或交易决策归因。'
        text,done,errors=[],{},[]
        result.data_quality=quality(portfolio_id)
        try:
            async with asyncio.timeout(240):
                async for event in run_chat_stream_v3(question,conversation,portfolio_id,
                        conversation_history=[t.model_dump() for t in request.history],result_callback=capture,analysis_only=True):
                    event_type,data=event.split('\n',1)
                    data=json.loads(data.split('data: ',1)[1]);kind=event_type.removeprefix('event: ')
                    if kind=='text':text.append(data.get('delta',''))
                    elif kind=='intent':result.intent=data
                    elif kind=='done':done=data
                    elif kind=='error':errors.append(data.get('code','ANALYSIS_FAILED'))
                    elif kind=='candidates':result.evidence['clarification_candidates']=data.get('items',[])
                    elif kind=='validator_warning':result.issues.append('PROFESSIONAL_REVIEW_WARNING')
            result.conclusion=''.join(text)
            result.professional_result={k:v for k,v in done.items() if k not in ('rawText','actionable','actionable_hint')}
            route,intent=result.intent.get('planner_route'),result.intent.get('primary_intent')
            expected={'analyze_position':{'PositionDecision'},'review_portfolio':{'PortfolioReview','AssetAllocation'},'analyze_performance':{'PerformanceAnalysis'}}[scenario]
            if errors:result.issues.extend('PEER_'+str(e).upper() for e in errors)
            elif route in ('clarify','low_confidence'):
                result.status='partial';result.issues.append('NEEDS_CLARIFICATION')
            elif intent not in expected or route not in ('position_single','portfolio'):
                result.issues.append('ANALYSIS_ROUTE_MISMATCH')
            elif done.get('conclusion_level')=='aborted' or not captured or not result.conclusion:
                result.issues.append('PROFESSIONAL_ANALYSIS_UNAVAILABLE')
            else:
                result.status='ok';captured_evidence(result,captured,portfolio_id)
        except TimeoutError:
            result.issues.append('ANALYSIS_TIMEOUT')
        finally:
            decision_service.clear_session(conversation)
        result.issues=sorted(set(result.issues+result.data_quality['issues']))
        if asset_view:
            result.snapshot_id = request.snapshot_id
            result.data_quality['asset_view'] = asset_view['data_quality']
            result.scope['snapshot_id'] = request.snapshot_id
            result.evidence['asset_view'] = dict(wealth_summary=asset_view['views']['wealth_summary'],
                portfolio_summary=asset_view['views']['portfolio_summary'], issuer_exposure=asset_view['issuer_exposure'])
            result.issues = sorted(set(result.issues + asset_view['data_quality']['issues']))
            if not asset_view['data_quality']['can_propose_rebalance']:
                result.conclusion = '数据存在过期或未知项；以下仅作一般分析，不构成可直接执行的精确调仓方案。\n'+result.conclusion
                result.limitations.append('Precise rebalancing requires a new qualified asset view; source, security price and FX times are separate.')
        if result.status=='ok' and result.issues:result.status='partial'
    return result.model_dump(mode='json')
