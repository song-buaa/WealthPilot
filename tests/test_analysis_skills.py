"""Original PEER and calculations on isolated fixtures; no live models/brokers."""
import asyncio
from datetime import date
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Portfolio, Position
from backend.agents.contracts import PlanningOutput, AgentTaskStatus, ReviewOutput
from pocs.chatgpt_skills.analysis import AnalysisRequest, analyze, readonly_business_database


@pytest.fixture
def analysis_db(tmp_path, monkeypatch):
    import backend.services.action.models  # noqa: F401
    from backend.services import portfolio_service
    monkeypatch.setattr(portfolio_service,'_get_tiger_account_cash',lambda:(0,[]))
    db=tmp_path/'analysis.db';engine=create_engine('sqlite:///'+str(db));Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add_all([Portfolio(id=7,name='fixture'),Portfolio(id=8,name='other')]);s.flush()
        s.add_all([Position(portfolio_id=7,name='Fixture Apple',ticker='AAPL',symbol='AAPL:US',platform='fixture',
                           currency='USD',segment='投资',asset_class='股票',quantity=100,market_value_cny=10000,
                           profit_loss_value=1000,profit_loss_rate=10),
                   Position(portfolio_id=7,name='Fixture Bond',ticker='BOND',symbol='BOND:US',platform='fixture',
                           currency='USD',segment='投资',asset_class='债券',quantity=10,market_value_cny=5000,
                           profit_loss_value=-200,profit_loss_rate=-4),
                   Position(portfolio_id=7,name='LIFE_ONLY',platform='fixture',asset_class='货币',segment='生活',market_value_cny=900000),
                   Position(portfolio_id=8,name='OTHER_PRIVATE',platform='fixture',asset_class='股票',segment='投资',market_value_cny=888888)])
        s.commit()
    engine.dispose()
    return db


@pytest.fixture
def peer(monkeypatch):
    from backend.services import decision_service_v3 as svc
    from backend.agents.planning_agent import PlanningAgent
    from backend.agents.reviewing_agent import ReviewingAgent
    from backend.agents import executing_agent
    from decision_engine import data_loader, llm_engine
    from decision_engine.llm_engine import GenericLLMResult, LLMResult
    from backend.graph.tools import DisciplineCheckOutput, GenerateSignalsOutput, LoadDecisionContextOutput
    calls=[]
    def plan(self,question,conversation,pid,history,positions):
        intent='PerformanceAnalysis' if question.startswith('当前持仓收益') else 'PortfolioReview' if question.startswith('投资组合') else 'PositionDecision'
        calls.append(('plan',intent,history))
        p=PlanningOutput(route='position_single' if intent=='PositionDecision' else 'portfolio',
                         intent={'primary_intent':intent,'asset':'Fixture Apple' if intent=='PositionDecision' else None,'confidence':.99},
                         portfolio_id=pid)
        p.mark_completed();return p
    monkeypatch.setattr(PlanningAgent,'run',plan)
    monkeypatch.setattr(data_loader,'_load_research',lambda *a:[])
    monkeypatch.setattr(data_loader,'search_portfolio_research',lambda *a:[])
    monkeypatch.setattr(data_loader,'_retrieve_principles',lambda *a,**kw:[]) if hasattr(data_loader,'_retrieve_principles') else None
    original=executing_agent.invoke_skill
    def invoke(name,**kw):
        if name=='wp-load-context':
            return LoadDecisionContextOutput(loaded_data=data_loader.load(asset_name=kw.get('asset_name'),pid=kw['portfolio_id']))
        if name=='wp-check-discipline':
            return DisciplineCheckOutput(violation=False,warning=None,current_weight=2/3,max_position=.4,position_ratio=1,rule_details=['original fixture rule'])
        if name=='wp-generate-signals':
            return GenerateSignalsOutput(asset_name='Fixture Apple',position_signal='偏高',fundamental_signal='中性',sentiment_signal='中性',event_uncertainty='低',event_direction='中性')
        return original(name,**kw)
    monkeypatch.setattr(executing_agent,'invoke_skill',invoke)
    def generic(kind):
        def f(query,data,*a,**kw):
            calls.append(('expression',kind))
            return GenericLLMResult(intent_type=kind,chat_answer='FIXTURE professional conclusion',raw_output='{}',raw_payload={'chat_answer':'FIXTURE professional conclusion','key_findings':['fixture evidence']})
        return f
    def reason(*a,**kw):
        calls.append(('expression','PositionDecision'))
        return LLMResult(decision='HOLD',reasoning=['fixture evidence'],risk=['fixture risk'],strategy=['wait'],chat_answer='FIXTURE professional conclusion',structured_result={'decisionType':'hold'})
    monkeypatch.setattr(llm_engine,'reason',reason)
    monkeypatch.setattr(llm_engine,'review_portfolio' ,generic('PortfolioReview'))
    monkeypatch.setattr(llm_engine,'analyze_allocation',generic('AssetAllocation'))
    monkeypatch.setattr(llm_engine,'analyze_performance',generic('PerformanceAnalysis'))
    monkeypatch.setattr(ReviewingAgent,'run',lambda *a,**kw:ReviewOutput(action='pass',score=1))
    monkeypatch.setattr(svc,'parse_trade_intent',lambda *a,**kw:None)
    async def no_pattern(*a):return None,None
    monkeypatch.setattr(svc,'_collect_decision_pattern_evidence',no_pattern)
    # Disable external research retrieval at its existing optional Knowledge boundary.
    from backend.knowledge.store import KnowledgeStore
    monkeypatch.setattr(KnowledgeStore,'get_instance',classmethod(lambda cls:SimpleNamespace(is_ready=lambda:False)))
    return calls


@pytest.mark.parametrize('scenario,intent',[('analyze_position','PositionDecision'),('review_portfolio','PortfolioReview'),('analyze_performance','PerformanceAnalysis')])
def test_real_peer_services_scope_results_and_no_writes(analysis_db,peer,scenario,intent):
    before=hashlib.sha256(analysis_db.read_bytes()).hexdigest()
    result=asyncio.run(analyze(analysis_db,7,scenario,{'question':'fixture query','history':[{'role':'user','content':'earlier context'}]},synthetic=True))
    assert result['status']=='partial',json.dumps(result,ensure_ascii=False)
    assert result['intent']['primary_intent']==intent
    if scenario=='analyze_position':
        assert result['professional_result']['analysis_result']['analysisQualification']['L1']['allowed']
        assert 'FIXTURE professional conclusion' not in result['conclusion']
    else:
        assert result['conclusion']=='FIXTURE professional conclusion'
    assert result['scope']['investment_holdings_total_cny']==15000
    assert len(result['evidence']['holdings'])==2
    assert 'OTHER_PRIVATE' not in json.dumps(result) and 'LIFE_ONLY' not in json.dumps(result)
    assert peer[0][2]==[{'role':'user','content':'earlier context'}]
    assert [c for c in peer if c[0]=='expression']==[('expression',intent)]
    assert 'VALUATION_DATE_UNKNOWN' in result['issues']
    if scenario=='review_portfolio':
        from backend.services import allocation_service
        with readonly_business_database(analysis_db):
            snapshot=allocation_service.get_snapshot(7).model_dump(mode='json')
        assert {k:v for k,v in result['evidence']['allocation']['snapshot'].items() if k!='snapshot_at'}=={k:v for k,v in snapshot.items() if k!='snapshot_at'}
    elif scenario=='analyze_performance':
        performance=result['evidence']['performance']
        assert performance['total_pnl']==800
        assert performance['profit_top3'][0]['pnl_amount']==1000
        assert performance['loss_top3'][0]['pnl_amount']==-200
        assert any('no period return' in s for s in result['limitations'])
    if scenario=='analyze_position':
        assert result['evidence']['target_position']['ticker']=='AAPL'
        assert result['evidence']['rule_result']['max_position']==.4
        assert result['professional_result']['analysis_result']['decisionType']=='hold'
    assert hashlib.sha256(analysis_db.read_bytes()).hexdigest()==before


def test_readonly_all_service_sessions_including_threads(analysis_db):
    from app.database import get_session
    def denied():
        with get_session() as s:
            with pytest.raises(Exception):s.execute(text("UPDATE positions SET quantity=0"));s.commit()
    with readonly_business_database(analysis_db):
        denied();asyncio.run(asyncio.to_thread(denied))


@pytest.mark.parametrize('arguments',[{'question':' '},{'question':''},{'question':'x','history':[{'role':'system','content':'x'}]},{'question':'x','history':[{'role':'user','content':'x'}]*17}])
def test_invalid_requests_are_not_guessed(arguments):
    with pytest.raises(Exception):AnalysisRequest.model_validate(arguments)


def test_missing_portfolio_is_not_zero(analysis_db):
    with pytest.raises(ValueError,match='PORTFOLIO_NOT_FOUND'):
        asyncio.run(analyze(analysis_db,999,'review_portfolio',{'question':'x'}))


@pytest.mark.parametrize('kind',['error','wrong_route','clarify','aborted','timeout'])
def test_failure_and_clarification_explicit(analysis_db,monkeypatch,kind):
    from backend.services import decision_service_v3 as svc
    async def stream(*a,**kw):
        if kind=='timeout':raise TimeoutError()
        yield svc._sse('intent',{'primary_intent':'Education','planner_route':'clarify' if kind=='clarify' else 'general'})
        if kind=='error':yield svc._sse('error',{'code':'execution_failed','message':'SENSITIVE_PROVIDER_ERROR'})
        yield svc._sse('text',{'delta':'needs clarification' if kind=='clarify' else 'not analysis'})
        yield svc._sse('done',{'conclusion_level':'aborted' if kind=='aborted' else 'general_chat'})
    monkeypatch.setattr(svc,'run_chat_stream_v3',stream)
    result=asyncio.run(analyze(analysis_db,7,'review_portfolio',{'question':'x'}))
    assert result['status']==('partial' if kind=='clarify' else 'unavailable')
    assert result['issues'] and result['order_created'] is False
    assert 'SENSITIVE_PROVIDER_ERROR' not in json.dumps(result)


def test_sync_failure_and_staleness_are_preserved(analysis_db,peer,monkeypatch):
    from datetime import datetime,timedelta
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/"backend"))
    from services.broker_sync.models import PositionSnapshotRun
    engine=create_engine('sqlite:///'+str(analysis_db));Base.metadata.create_all(engine)
    with Session(engine) as s:
        row=s.query(Position).filter_by(portfolio_id=7,ticker='AAPL').one()
        row.broker='tiger';row.broker_account_id='FIXTURE_SECRET_ACCOUNT';row.sync_source='api'
        old=datetime.now()-timedelta(days=5)
        s.add_all([PositionSnapshotRun(portfolio_id=7, broker='tiger',account_id='FIXTURE_SECRET_ACCOUNT',started_at=old,finished_at=old,status='success'),
                   PositionSnapshotRun(portfolio_id=7, broker='tiger',account_id='FIXTURE_SECRET_ACCOUNT',started_at=datetime.now(),status='failed')]);s.commit()
    engine.dispose()
    result=asyncio.run(analyze(analysis_db,7,'analyze_performance',{'question':'fixture'}))
    assert {'LATEST_POSITION_SYNC_FAILED','STALE_COMPONENTS','VALUATION_DATE_UNKNOWN'}<=set(result['issues'])
    assert 'FIXTURE_SECRET_ACCOUNT' not in json.dumps(result)


def test_model_fallback_is_not_a_successful_professional_decision(analysis_db,peer,monkeypatch):
    from decision_engine import llm_engine
    monkeypatch.setattr(llm_engine,'analyze_performance',lambda *a,**kw:llm_engine.GenericLLMResult(intent_type='PerformanceAnalysis',chat_answer='model unavailable',error='fixture failure'))
    result=asyncio.run(analyze(analysis_db,7,'analyze_performance',{'question':'fixture'}))
    assert result['status']=='unavailable'
    assert 'PROFESSIONAL_MODEL_UNAVAILABLE' in result['issues']
    assert result['evidence']['performance']['total_pnl']==800


def test_analysis_is_ephemeral_without_changing_web_default(analysis_db,monkeypatch):
    from backend.services.decision_service import save_conversation_turn
    from backend.graph.decision_graph import build_decision_graph
    before=hashlib.sha256(analysis_db.read_bytes()).hexdigest()
    with readonly_business_database(analysis_db):
        assert build_decision_graph().checkpointer is None
        assert save_conversation_turn('mcp-fixture','question','answer') is None
    assert hashlib.sha256(analysis_db.read_bytes()).hexdigest()==before


def test_allocation_intent_uses_original_allocation_expression(analysis_db,peer,monkeypatch):
    from backend.agents.planning_agent import PlanningAgent
    original=PlanningAgent.run
    def plan(self,*a):
        value=original(self,*a);value.intent['primary_intent']='AssetAllocation';return value
    monkeypatch.setattr(PlanningAgent,'run',plan)
    result=asyncio.run(analyze(analysis_db,7,'review_portfolio',{'question':'allocation fixture'}))
    assert result['intent']['primary_intent']=='AssetAllocation'
    assert ('expression','AssetAllocation') in peer
    assert result['evidence']['allocation']['snapshot']['total_investable_assets']==5000
    assert result['evidence']['allocation']['snapshot']['unclassified_amount']==10000
    assert 'ALLOCATION_UNCLASSIFIED_HOLDINGS' in result['issues']


def test_existing_new_entry_path_preserves_its_original_structured_result(analysis_db,peer,monkeypatch):
    from decision_engine import data_loader
    from backend.agents import executing_agent
    from backend.graph.tools import LoadDecisionContextOutput
    from intent_engine import _llm_client
    from backend.services.market_data.schema import FundamentalsData
    original=executing_agent.invoke_skill
    def invoke(name,**kw):
        if name!='wp-load-context':return original(name,**kw)
        loaded=data_loader.load(asset_name=None,pid=kw['portfolio_id'])
        loaded.is_new_entry=True
        loaded.target_position=data_loader.PositionInfo(name='Fixture New',ticker='MSFT',symbol='MSFT:US',asset_class='权益',weight=0,market_value_cny=0,cost_price=0,current_price=0,profit_loss_rate=0,is_virtual=True)
        loaded.av_fundamentals=FundamentalsData(symbol='MSFT:US',pe_ttm=30,data_as_of='2026-01-01')
        return LoadDecisionContextOutput(loaded_data=loaded)
    monkeypatch.setattr(executing_agent,'invoke_skill',invoke)
    create=lambda **kw:SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='FIXTURE observe new entry'))])
    monkeypatch.setattr(_llm_client,'get_client',lambda:SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    result=asyncio.run(analyze(analysis_db,7,'analyze_position',{'question':'new entry fixture'}))
    assert result['status']=='partial',result
    assert result['evidence']['new_entry'] is True
    assert result['evidence']['target_position']['is_virtual'] is True
    assert result['professional_result']['analysis_result']['is_new_entry'] is True
    assert result['professional_result']['analysis_result']['analysisQualification']['L3']['allowed'] is False
    assert 'FIXTURE observe new entry' not in result['conclusion']


def test_ephemeral_analysis_does_not_create_knowledge_index(tmp_path,monkeypatch):
    from backend.knowledge import store
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('WEALTHPILOT_EPHEMERAL_ANALYSIS','true')
    knowledge=store.KnowledgeStore()
    assert not knowledge.is_ready()
    assert not (tmp_path/'knowledge_base/_index/chroma_db').exists()
