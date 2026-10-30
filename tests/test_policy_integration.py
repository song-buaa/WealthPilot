"""Isolated LEGACY/POLICY_ACTIVE consumers; fictional sources only."""
import asyncio
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal as D
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_investment_policy import PHILOSOPHY, POLICY
from backend.services.investment_policy.contracts import MetricEvidence
from backend.services.investment_policy.parser import PolicyError
from backend.services.investment_policy.publisher import publish_synthetic
from backend.services.investment_policy.runtime import PolicyAssets, PolicySession, bind, context, diagnose, sanitize_loaded
from backend.services.investment_policy.store import PolicyStore, utc_now


def stage(store, folder, version=1):
    paths={}
    for role,source in [('philosophy',PHILOSOPHY),('policy',POLICY)]:
        p=folder/(role+'.md');p.write_bytes(source.replace(b'2030-12-01',b'2020-01-01'));paths[role]=p
    hashes={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in paths.items()}
    b=store.stage(scope='portfolio:7',version=version,philosophy_path=paths['philosophy'],policy_path=paths['policy'],
        expected_hashes=hashes,request_id=f'stage-{version}',expected_head_version=version-1,created_at=utc_now())
    for before,after in [('DRAFT','VALIDATED'),('VALIDATED','APPROVED')]:
        store.transition(scope='portfolio:7',bundle_id=b['bundle_id'],state=after,expected_state=before,
            request_id=f'{after}-{version}',at=utc_now(),approval_ref='SYNTHETIC_TEST_REVIEW')
    return b


@pytest.fixture
def active(tmp_path,monkeypatch):
    tmp_path.chmod(0o700)
    store=PolicyStore(tmp_path/'synthetic.sqlite');store.init_candidate()
    b=stage(store,tmp_path)
    publish_synthetic(store,scope='portfolio:7',bundle_id=b['bundle_id'],expected_sequence=0,request_id='release-1',at=utc_now())
    monkeypatch.setenv('WEALTHPILOT_POLICY_DB_PATH',str(store.path))
    monkeypatch.setenv('WEALTHPILOT_POLICY_SCOPE','portfolio:7')
    return store,b,tmp_path


def metric(v,**kw):
    return MetricEvidence(value=D(v),fresh=True,complete=True,identity_verified=True,denominator_qualified=True,as_of=utc_now()).model_copy(update=kw)


def session(**weights):
    assets=PolicyAssets(portfolio_id=7,snapshot_id='SYNTHETIC_FULL_INVESTABLE',as_of=utc_now(),denominator_cny=D('100000'),
        denominator_qualified=True,complete_inventory=True,coverage_evidence=['TEST_PENSION_EXCLUDED','TEST_ACCOUNTS_DEDUPED','TEST_EXTERNAL_ASSETS_COMPLETE'],
        fx_qualified=True,pension_exclusion_verified=True,deduplication_verified=True,external_assets_verified=True,
        issuer_weights={key:metric(value) for key,value in weights.items()},btc_weight=metric('0'))
    return PolicySession(7,assets=assets)


def test_legacy_and_candidate_do_not_change_original_defaults(monkeypatch):
    monkeypatch.delenv('WEALTHPILOT_POLICY_DB_PATH',raising=False)
    from backend.services.discipline_service import get_rules_config
    from app.discipline.config import get_rules
    assert context(7)['mode']=='LEGACY'
    assert get_rules_config()==get_rules()


def test_shared_reference_all_consumers_and_thread_pool(active):
    from backend.services.allocation_service import get_targets
    from backend.services.discipline_service import get_rules_config
    from backend.services.philosophy_service import get_philosophy
    with bind(session(SYN2='.16')) as pinned:
        refs=[]
        for name in ['web','allocation','discipline','peer','mcp']:
            refs.append(context(7,consumer=name,view='portfolio')['policy_reference'])
        with ThreadPoolExecutor() as pool:
            threaded=pool.submit(copy_context().run,context,7).result()
        assert all(ref==refs[0] for ref in refs) and threaded['policy_reference']==refs[0]
        assert sum(D(str(t.mid_ratio)) for t in get_targets())==1
        assert get_rules_config()['policy_reference']==refs[0]
        assert get_philosophy()['policy_reference']==refs[0]


@pytest.mark.parametrize('symbol,weight,expected',[('SYN2','.11','WARNING'),('SYN2','.15','HUMAN_REVIEW'),('SYN1','.115','UNKNOWN')])
def test_original_skill_uses_policy_semantics_not_legacy_hard_limits(active,symbol,weight,expected):
    from backend.skills import invoke_skill
    with bind(session(**{symbol:weight})):
        value=invoke_skill('wp-check-discipline',asset_name=symbol,portfolio_id=7)
        assert not value.violation and value.max_position is None
        if expected=='UNKNOWN':
            assert 'issuer.warning.default' not in json.loads(value.warning)['triggers'].__str__()
        else:
            assert value.personal_discipline_status==expected


def test_no_qualified_denominator_is_unknown_and_no_precise_plan(active):
    from backend.services.allocation_service import compute_increment_plan,get_snapshot
    with bind(PolicySession(7)):
        assert diagnose(7,issuer_symbol='SYN2')['status']=='UNKNOWN'
        plan=compute_increment_plan(7,12345)
        assert plan.plan_items==[] and not plan.discipline_check.passed
        with pytest.raises(PolicyError):get_snapshot(7)


def test_old_editors_cannot_bypass_bundle(active,monkeypatch):
    from backend.services import discipline_service as ds, philosophy_service as ps
    with bind(session()):
        for fn,args in [(ds.update_rules_config,({},)),(ds.reset_rules,()),(ds.save_handbook,('edited',)),
                        (ds.reset_handbook,()),(ps.save_philosophy,('edited',)),(ps.reset_philosophy,())]:
            with pytest.raises(PolicyError,match='READ_ONLY'):fn(*args)


def test_unversioned_index_not_used_and_original_prompt_receives_no_old_numeric_limits(active):
    from backend.graph.tools import execute_retrieve_principles
    from decision_engine.data_loader import LoadedData,UserProfile,InvestmentRules
    from decision_engine.llm_engine import _build_portfolio_payload
    loaded=LoadedData(profile=UserProfile(),positions=[],target_position=None,rules=InvestmentRules(.4,.8,.2,1.35),full_discipline_rules={'old':True})
    with bind(session()):
        clean=sanitize_loaded(loaded,7)
        payload=_build_portfolio_payload('synthetic research',clean)
        from pocs.chatgpt_skills.analysis import encode
        assert 'NOT_APPLICABLE' in encode(clean.rules)['max_single_position']
        assert all('NOT_APPLICABLE' in v for v in payload['rules'].values())
        assert clean.full_discipline_rules=={} and clean.retrieved_principles==[]
        assert execute_retrieve_principles('review')['chunks']==[]


def test_release_retries_cas_conflict_rollback_history_and_source_immutability(active):
    store,b,folder=active
    original=store.read_bundle('portfolio:7',b['bundle_id'])
    old=PolicySession(7)
    b2=stage(store,folder,2)
    publish_synthetic(store,scope='portfolio:7',bundle_id=b2['bundle_id'],expected_sequence=1,request_id='release-2',at=utc_now())
    with pytest.raises(PolicyError,match='VERSION_CONFLICT'):old.ensure_current()
    publish_synthetic(store,scope='portfolio:7',bundle_id=b['bundle_id'],expected_sequence=2,request_id='rollback',at=utc_now(),action='ROLLBACK')
    assert context(7)['policy_reference']['bundle_version']==1
    assert store.read_bundle('portfolio:7',b['bundle_id'])==original
    assert len(store.release_history('portfolio:7'))==3


def test_active_service_scope_unknown_never_falls_back_to_old_rules(active):
    assert PolicySession(8).mode=='UNAVAILABLE'
    with pytest.raises(PolicyError):PolicySession(8).ensure_current()


def test_original_order_safety_confirmation_survives(active):
    from backend.services.action.risk_engine import check_order_risk,validate_confirmation
    with bind(session(SYN2='.5')):
        r=check_order_risk('SYN2','BUY',100,D('100'),D('100000'),D('50000'))
        assert any(w.rule=='single_order_pct' for w in r.warnings)
        assert any(w.rule=='policy' for w in r.warnings)
        assert not any(w.rule=='concentration' for w in r.warnings)
        assert not validate_confirmation(r,'')


def test_web_api_context_is_read_only_and_uses_same_scope(active,monkeypatch):
    from app import state
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.discipline import router
    monkeypatch.setattr(state,'portfolio_id',7)
    app=FastAPI();app.include_router(router,prefix='/discipline')
    with TestClient(app) as client:
        before=active[0].path.read_bytes()
        result=client.get('/discipline/policy-context').json()
        assert result['policy_reference']==context(7)['policy_reference']
        assert active[0].path.read_bytes()==before


def test_stream_pin_conflict_never_delivers_buffered_result_or_callback(active,monkeypatch):
    from backend.services import decision_service_v3 as ds
    store,b,folder=active
    callbacks=[]
    async def stream(*args,**kwargs):
        yield ds._sse('text',{'delta':'DO_NOT_DELIVER_UNTIL_POLICY_VERIFIED'})
        b2=stage(store,folder,2)
        publish_synthetic(store,scope='portfolio:7',bundle_id=b2['bundle_id'],expected_sequence=1,request_id='race',at=utc_now())
        kwargs['result_callback']('not-deliver')
        yield ds._sse('done',{})
    monkeypatch.setattr(ds,'_run_chat_stream_v3',stream)
    async def run():
        return [item async for item in ds.run_chat_stream_v3('synthetic','test-run',7,result_callback=lambda *a:callbacks.append(a))]
    result=asyncio.run(run())
    assert not callbacks and len(result)==1 and 'POLICY_VERSION_CONFLICT' in result[0]
    assert 'DO_NOT_DELIVER' not in result[0]


@pytest.mark.parametrize('change',[{'denominator_qualified':False,'denominator_cny':None}, {'fx_qualified':False},
    {'external_assets_verified':False}, {'deduplication_verified':False}, {'pension_exclusion_verified':False},
    {'as_of':'2000-01-01T00:00:00Z'}])
def test_complete_inventory_does_not_override_missing_qualifications(active,change):
    from backend.services.investment_policy.runtime import assets_qualified
    from backend.services.investment_policy.consumers import finalize_output
    s=session(SYN2='.3');s.assets=s.assets.model_copy(update=change)
    with bind(s):
        assert not assets_qualified(s,complete=True)
        assert diagnose(7,issuer_symbol='SYN2')['status']=='UNKNOWN'
        out=SimpleNamespace(chat_answer='建议买入100股',structured_payload={},actionable=True)
        finalize_output(SimpleNamespace(portfolio_id=7),SimpleNamespace(loaded_data=None),out)
        assert '买入100股' not in out.chat_answer and out.structured_payload['policy_precision']=='CANNOT_QUANTIFY'


def test_major_class_review_reaches_top_level_without_becoming_hard_block(active):
    s=session(SYN2='0')
    s.assets=s.assets.model_copy(update={'major_class_weights':{'growth_and_alternatives':metric('.9'),'stable_pool':metric('.1')}})
    with bind(s):
        result=diagnose(7,issuer_symbol='SYN2')
        assert result['status']=='HUMAN_REVIEW'
        assert any(t.get('major_class')=='growth_and_alternatives' for t in result['triggers'])
        assert result['unknown'] and not result['auto_trade_allowed']


def test_actual_sse_delivers_only_final_qualified_text(active,monkeypatch):
    from backend.services import decision_service_v3 as ds
    from backend.services.investment_policy.consumers import finalize_output
    async def stream(*args,**kwargs):
        yield ds._sse('text',{'delta':'建议买入100股'})
        out=SimpleNamespace(chat_answer='建议买入100股',structured_payload={},actionable=True)
        finalize_output(SimpleNamespace(portfolio_id=7),SimpleNamespace(loaded_data=None),out)
        yield ds._sse('policy_final_text',{'text':out.chat_answer})
        yield ds._sse('done',{})
    monkeypatch.setattr(ds,'_run_chat_stream_v3',stream)
    async def run():return [x async for x in ds.run_chat_stream_v3('test','synthetic',7)]
    messages=asyncio.run(run())
    assert '买入100股' not in ''.join(messages)
    assert '政策引用' in ''.join(messages) and 'policy_final_text' not in ''.join(messages)


@pytest.mark.parametrize('text',['建议购买100股','Buy 100 TSLA shares','建议买入100股'])
def test_unqualified_text_and_structured_action_fields_never_escape(active,text):
    from backend.services.investment_policy.consumers import finalize_output
    from decision_engine.llm_engine import LLMResult
    model=LLMResult(decision='BUY',reasoning=[text],strategy=[text],risk=[],chat_answer=text,structured_result={'buy_shares':100})
    out=SimpleNamespace(chat_answer=text,structured_payload={'buy_shares':100},llm_result=model,actionable=True)
    with bind(session()):
        finalize_output(SimpleNamespace(portfolio_id=7),SimpleNamespace(loaded_data=None),out)
    assert text not in out.chat_answer and 'buy_shares' not in out.structured_payload
    assert text not in out.llm_result.chat_answer and text not in str(out.llm_result.reasoning)
    assert out.llm_result.strategy==[] and out.llm_result.structured_result==out.structured_payload
    assert model.chat_answer==text  # original provenance object not overwritten


def test_real_portfolio_capture_does_not_require_investment_only_snapshot(active,monkeypatch):
    from pocs.chatgpt_skills import analysis
    from backend.services import allocation_service
    from decision_engine.data_loader import LoadedData,UserProfile,InvestmentRules
    def forbidden(*a,**k):raise AssertionError('Active capture used old allocation snapshot')
    monkeypatch.setattr(allocation_service,'get_snapshot',forbidden)
    loaded=LoadedData(profile=UserProfile(),positions=[],target_position=None,rules=InvestmentRules(.4,.8,.2,1.35))
    with bind(session()):
        loaded=sanitize_loaded(loaded,7)
        captured=dict(executed=SimpleNamespace(loaded_data=loaded,position_evidence={},portfolio_evidence=[],rule_result=None,
            signal_result=None,market_data=None,invoked_skills=[]),
            expressed=SimpleNamespace(structured_payload={},mode='structured',llm_result=None),reviewed=SimpleNamespace(passed=True))
        result=analysis.AnalysisResult(synthetic=True,scenario='review_portfolio',status='partial',issues=[],queried_at=utc_now(),conclusion='')
        analysis.captured_evidence(result,captured,7)
        assert result.evidence['allocation']['status'].startswith('UNKNOWN')
        assert result.scope['policy_reference']==context(7)['policy_reference']


def test_final_mcp_projection_reuses_original_pin_after_adapter_latency(active,monkeypatch):
    from pocs.chatgpt_skills import analysis
    async def owned(*a,**k):
        return analysis.AnalysisResult(synthetic=True,scenario='review_portfolio',status='partial',issues=[],queried_at=utc_now(),
            conclusion='Buy 100 TSLA shares',professional_result={'analysis_result':{'buy_shares':100}}).model_dump(mode='json')
    monkeypatch.setattr(analysis,'_analyze_owned',owned)
    with bind(session()) as pinned:
        result=asyncio.run(analysis.analyze('not-opened',7,'review_portfolio',{'question':'test'}))
        assert 'Buy 100' not in result['conclusion']
        assert 'buy_shares' not in result['professional_result']['analysis_result']
        assert result['scope']['policy_reference']==pinned.context['policy_reference']


@pytest.mark.parametrize('offset', [timedelta(seconds=1), -timedelta(minutes=6), None])
def test_bucket_clock_must_qualify_even_when_fresh_flag_is_true(active,offset):
    from backend.services.investment_policy.consumers import snapshot,deviation
    from backend.services.investment_policy.store import clock
    pinned=session()
    with bind(pinned):
        neutral=context(7)['targets']['neutral']
        pinned.assets=pinned.assets.model_copy(update={'bucket_weights':{k:metric(v) for k,v in neutral.items()}})
        assert deviation(7).status=='QUALIFIED_SCOPE_DIAGNOSTIC'
        assert snapshot(7).total_investable_assets==100000
        key=next(iter(neutral))
        bad=(clock(utc_now())+offset).isoformat() if offset else '2026-01-01T00:00:00'
        pinned.assets.bucket_weights[key]=metric(neutral[key],as_of=bad)
        assert deviation(7).status=='UNKNOWN'
        with pytest.raises(PolicyError,match='POLICY_BUCKET_CLASSIFICATION_UNVERIFIED'):
            snapshot(7)


def test_real_multi_persistence_and_explain_use_the_projected_llm(active,monkeypatch):
    # Run the original multi-owner harness with POLICY_ACTIVE, then its real
    # decision store -> Explain endpoint. No second LLM/provider is invoked.
    from backend.services import decision_service
    # That legacy harness checks its original seven-section prose and L3 schema.
    # Reuse its source/worker inputs below, keeping policy-specific assertions here.
    from tests.test_v42_exit_plan import setup_case
    from tests.test_v42_evidence import AT
    from backend.services import decision_service_v3 as ds,position_analysis
    from backend.agents.contracts import PlanningOutput,ExpressionOutput
    from decision_engine.llm_engine import LLMResult
    view,_,_,_=setup_case('LI:US');view['portfolio_id']=7
    plan=PlanningOutput(portfolio_id=7,route='position_multi',multi_assets=['LI:US','PDD:US'],intent={'primary_intent':'PositionDecision'})
    def execute(single,question):
        from decision_engine.data_loader import LoadedData,UserProfile,PositionInfo
        executed=setup_case(single.intent['asset'])[1]
        symbol=single.intent['asset']
        pos=PositionInfo(name=symbol,ticker=symbol,asset_class='equity',weight=.1,market_value_cny=10000,cost_price=10000,current_price=10,profit_loss_rate=0,symbol=symbol)
        executed.loaded_data=LoadedData(profile=UserProfile(),positions=[pos],target_position=pos)
        return executed
    class Expressing:
        async def run_streaming(self,*args):
            self.last_output=ExpressionOutput(llm_result=LLMResult('BUY',['Buy 987654'],[],[],chat_answer='Buy 987654',structured_result={'buy_shares':987654}))
            yield 'Buy 987654'
    real_prepare=position_analysis.prepare
    def prepare(*a,**kw):return real_prepare(*a,**kw,at=AT)
    async def pattern(*a):return None,{}
    monkeypatch.setattr(ds,'get_executing_agent',lambda:SimpleNamespace(run=execute))
    monkeypatch.setattr(ds,'get_expressing_agent',Expressing)
    monkeypatch.setattr(ds,'_collect_decision_pattern_evidence',pattern)
    monkeypatch.setattr(position_analysis,'prepare',prepare)
    monkeypatch.setattr(decision_service,'save_conversation_turn',lambda *a,**kw:None)
    monkeypatch.setattr(decision_service,'_DECISION_STORE',{})
    async def run():return [e async for e in ds._handle_position_multi(plan,plan.multi_assets,'test','policy-multi',7,'test-id',analysis_context=view,analysis_options={})]
    with bind(session()):
        asyncio.run(run())
        results=decision_service._DECISION_STORE['policy-multi']
        assert len(results)==2
        for did,stored in results.items():
            assert '987654' not in stored.llm.chat_answer+str(stored.llm.structured_result)
            assert stored.llm.structured_result['policy_reference']==context(7)['policy_reference']
            explained=decision_service.get_decision_explain('policy-multi',did)
            assert '987654' not in json.dumps(explained,default=str)


@pytest.mark.parametrize('route',['position_single','portfolio'])
def test_real_web_frozen_owner_and_parent_keep_one_policy_and_final_projection(active,monkeypatch,route):
    from contextlib import contextmanager
    import sqlite3
    from backend.services import snapshot_chat,decision_service_v3,decision_service,position_analysis
    from pocs.chatgpt_skills import analysis
    from tests.test_v42_snapshot_chat import view,parse,collect
    from backend.agents.contracts import PlanningOutput,ExpressionOutput
    folder=active[2]/'web-cache';folder.mkdir(mode=0o700)
    monkeypatch.setenv('WEALTHPILOT_ANALYSIS_CACHE_DIR',str(folder))
    cache=folder/('b'*32+'.sqlite')
    with sqlite3.connect(cache) as db:
        db.execute('CREATE TABLE portfolios (id INTEGER PRIMARY KEY,name TEXT)')
        db.execute("INSERT INTO portfolios VALUES(7,'SYNTHETIC FROZEN')")
    cache.chmod(0o600)
    frozen=view();frozen['portfolio_id']=7
    frozen['internal']={'cache_key':'b'*32,'database_sha256':hashlib.sha256(cache.read_bytes()).hexdigest()}
    live=active[2]/'synthetic-web.sqlite'
    with sqlite3.connect(live) as db:
        db.execute('CREATE TABLE wealth_snapshots (id INTEGER,portfolio_id INTEGER,analysis_request_id TEXT,payload_json TEXT)')
        db.execute('INSERT INTO wealth_snapshots VALUES(1,7,?,?)',('frozen',json.dumps(frozen)))
    boundary=analysis.readonly_business_database
    @contextmanager
    def restored(path,**kw):
        with boundary(path,worker_process=False):yield
    monkeypatch.setattr(analysis,'readonly_business_database',restored)
    async def peer(*args,**kwargs):
        assert context(7)['policy_reference']==expected
        kwargs['result_callback'](PlanningOutput(portfolio_id=7,route=route,intent={'primary_intent':'PositionDecision'}),
            SimpleNamespace(loaded_data=None),ExpressionOutput(chat_answer='Buy 987654',structured_payload={'buy_shares':987654}),None)
        yield snapshot_chat._sse('text',{'delta':'Buy 987654'})
        yield snapshot_chat._sse('done',{})
    from backend.services.market_data.evidence import assess
    assessment=assess(symbol='SYN2:US',asset_view=frozen)
    assessment['snapshot_id']=frozen['snapshot_id']
    monkeypatch.setattr(decision_service_v3,'run_chat_stream_v3',peer)
    monkeypatch.setattr(position_analysis,'prepare',lambda *a,**kw:assessment)
    monkeypatch.setattr(position_analysis,'prepare_portfolio',lambda *a,**kw:[assessment])
    expected=context(7)['policy_reference']
    payload=dict(database_path=str(live),contract=None,portfolio_id=7,snapshot_id=frozen['snapshot_id'],
        message='TEST ONLY',conversation_id='policy-web-'+route,conversation_history=[],options={})
    result=asyncio.run(snapshot_chat._run_frozen_chat(payload))
    assert result['policy_reference']==expected
    assert result['analysis_result']['policy_reference']==expected and '987654' not in json.dumps(result)
    with pytest.raises(PolicyError,match='POLICY_CLIENT_TARGET_OVERRIDE_UNSUPPORTED'):
        asyncio.run(snapshot_chat._run_frozen_chat(dict(payload,options={'target_versions':[{}]})))
    async def child(payload):
        if result.get('llm_observation'):
            result['llm_observation']['analysis_run_id']=payload['analysis_run_id']
        return result
    monkeypatch.setattr(snapshot_chat,'_invoke_worker',child)
    monkeypatch.setattr(decision_service,'get_conversation_history',lambda *a:[])
    saved=[]
    monkeypatch.setattr(decision_service,'save_conversation_turn',lambda *a,**kw:saved.append(a))
    monkeypatch.setattr(decision_service,'_ALLOC_EXPLAIN_STORE',{})
    events=parse(collect(snapshot_chat.stream_snapshot_chat('test',payload['conversation_id'],7,frozen)))
    assert any(kind=='done' for kind,_ in events) and len(saved)==1, events
    # IPC result missing/mismatching original reference is rejected before save.
    result['policy_reference']={**expected,'bundle_sha256':'0'*64}
    events=parse(collect(snapshot_chat.stream_snapshot_chat('test','bad-policy-web',7,frozen)))
    assert any(kind=='error' for kind,_ in events) and not any(kind=='done' for kind,_ in events)
    assert len(saved)==1 and not any(k.startswith('bad-policy-web:') for k in decision_service._ALLOC_EXPLAIN_STORE)



def test_original_portfolio_alert_owners_do_not_emit_old_policy_thresholds(active,monkeypatch):
    from backend.services import portfolio_service
    from app import analyzer
    def forbidden(*args,**kwargs):raise AssertionError('Old investment-only policy projection was used')
    monkeypatch.setattr(portfolio_service,'analyze_portfolio',forbidden)
    monkeypatch.setattr(analyzer,'get_session',forbidden)
    with bind(session()):
        service_rows=portfolio_service.get_alerts(7)
        old_entry=analyzer.check_deviations(7,None)
        assert service_rows['count']==len(old_entry)>0
        assert all(row['current_value'] is None and row['deviation'] is None for row in service_rows['items'])
        assert 'UNKNOWN' in str(service_rows) and 'policy_reference' in str(service_rows)



def test_portfolio_retains_both_issuer_reviews_and_special_issuer_exception(active):
    from backend.services.investment_policy.consumers import portfolio_alerts
    with bind(session(SYN2='.16',SYN3='.17',SYN1='.11')):
        value=diagnose(7)
        assert value['status']=='HUMAN_REVIEW'
        reviews=[t for t in value['triggers'] if t['rule_id']=='issuer.review']
        assert {t['issuer'] for t in reviews}=={'SYN2','SYN3'}
        assert not value['issuers']['SYN1']['triggers']
        alerts=portfolio_alerts(7)
        assert all(any(s in row.title and 'HUMAN_REVIEW' in row.title for row in alerts) for s in ['SYN2','SYN3'])
        assert sum('btc.' in row.title for row in alerts)==0
        assert all('policy_reference' in row.description for row in alerts)
