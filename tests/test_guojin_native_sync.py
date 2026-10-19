"""Guojin native batch correctness and original publication, synthetic WAL only."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Portfolio, Position

from tests.guojin_native_fixtures import make_batch, write_json


@pytest.fixture
def native_api(monkeypatch):
    # Runtime-only path isolation: don't shadow the root scripts namespace
    # while pytest is collecting the existing Offline M5 harness.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'backend'))
    from services.broker_sync.models import PositionSnapshotRun, PositionSnapshot
    from services.broker_sync.guojin.native_exports import parse_exports
    from services.broker_sync.guojin.sync_service import GuojinSyncService, position_fingerprint
    from services.broker_sync.guojin.adapter import GuojinAdapter
    from services.broker_sync.repository import context
    from backend.services.restricted_sync import SyncExecutor
    return SimpleNamespace(Run=PositionSnapshotRun, Snapshot=PositionSnapshot, parse_exports=parse_exports,
        Service=GuojinSyncService, fingerprint=position_fingerprint, Adapter=GuojinAdapter,
        context=context, Executor=SyncExecutor)


@pytest.fixture
def env(tmp_path, monkeypatch, native_api):
    state=make_batch(tmp_path)
    monkeypatch.setenv('GUOJIN_NATIVE_CONFIG',str(state.config_path))
    monkeypatch.delenv('GUOJIN_ACCOUNT',raising=False)
    engine=create_engine('sqlite:///'+str(tmp_path/'isolated.db'))
    with engine.connect() as conn:conn.exec_driver_sql('PRAGMA journal_mode=WAL')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([Portfolio(id=7,name='Synthetic'),Portfolio(id=8,name='Other')]);session.commit()
    def sessions():return Session(engine,info={'portfolio_id':7})
    state.engine,state.sessions,state.api=engine,sessions,native_api
    yield state
    engine.dispose()


def publish(c):
    service=c.api.Service(c.config_path)
    executor=c.api.Executor(['guojin'],7,lambda _:service,session_factory=c.sessions)
    jobs,busy=executor.reserve('guojin');assert not busy
    run_id=executor.execute('guojin',jobs[0]['run_id'])
    with c.sessions() as db:
        run=db.get(c.api.Run,run_id)
        return SimpleNamespace(id=run.id,status=run.status,error=run.error_message,meta=c.api.context(run))


def facts(c):
    with c.sessions() as db:
        return [(r.id,c.api.fingerprint(r)) for r in db.query(Position).order_by(Position.id)]


def refresh_hash(c,key):
    name={'stock':'stock.txt','hgt':'hgt.xls','sgt':'sgt.xls'}[key]
    c.receipt['files'][key]['sha256']=hashlib.sha256((c.directory/name).read_bytes()).hexdigest()
    write_json(c.directory/'batch.json',c.receipt)


def test_actual_headers_cny_cost_pnl_and_distinct_channels(env):
    parsed=env.api.parse_exports(env.files)
    assert parsed.total_assets==1490 and parsed.available==Decimal('-.25')
    assert parsed.counts=={'stock':1,'hgt':2,'sgt':1}
    positions=[env.api.Adapter('fixture-owner').from_native_export(r,datetime.now(timezone.utc)) for r in parsed.rows]
    cars=[p for p in positions if p.raw_symbol=='02015']
    assert {p.symbol for p in cars}=={'2015.HGT:HK','2015.SGT:HK'}
    assert {p.quantity for p in cars}=={10,2}
    hgt=next(p for p in cars if p.symbol=='2015.HGT:HK')
    assert hgt.currency=='CNY' and hgt.market_value==800 and hgt.avg_cost==100
    assert hgt.unrealized_pnl==-250 and hgt.unrealized_pnl!=hgt.market_value-hgt.cost_basis
    assert hgt.raw_data['quote_currency']=='HKD' and hgt.raw_data['quote']==90
    assert 'SYNTHETIC-SHAREHOLDER' not in json.dumps(hgt.raw_data,default=str)


def test_original_executor_idempotent_publish_and_safe_quality(env,monkeypatch):
    from app.fx_service import fx_service
    original=fx_service.get_quote
    def quote(src,dst):
        assert src==dst=='CNY'  # no second HKD conversion or broker I/O
        with env.engine.connect() as writer:writer.exec_driver_sql('BEGIN IMMEDIATE');writer.rollback()
        return original(src,dst)
    monkeypatch.setattr(fx_service,'get_quote',quote)
    first=publish(env);assert first.status=='success',first.error
    before=facts(env)
    assert publish(env).status=='success' and facts(env)==before
    with env.sessions() as db:
        rows=db.query(Position).all()
        assert len(rows)==5 and sum(p.market_value_cny for p in rows)==1490
        assert sum(p.quantity for p in rows if p.symbol in {'2015.HGT:HK','2015.SGT:HK'})==12
        assert len([p for p in rows if p.vehicle_type=='CASH'])==1
    from backend.api.broker_sync import _public_quality
    with env.sessions() as db:quality=_public_quality(db.get(env.api.Run,first.id))
    assert quality['quality']=='partial' and quality['price_as_of'] is None
    assert 'PRICE_TIME_UNKNOWN' in quality['issues'] and quality['security_count']==4
    assert 'account_id' not in json.dumps(quality) and 'SYNTHETIC' not in json.dumps(quality)


@pytest.mark.parametrize('case',['missing_file','truncated','bad_hash','wrong_channel','missing_field','wrong_owner',
    'unverified_owner','missing_query','wrong_count','wrong_assets','shareholder_mismatch','future_query','old_query','old_export'])
def test_bad_batch_keeps_last_good(env,case):
    assert publish(env).status=='success';before=facts(env)
    if case=='missing_file':(env.directory/'sgt.xls').unlink()
    if case=='truncated':
        lines=(env.directory/'hgt.xls').read_bytes().splitlines(keepends=True)
        (env.directory/'hgt.xls').write_bytes(b''.join(lines[:-1]));refresh_hash(env,'hgt')
    if case=='bad_hash':env.receipt['files']['hgt']['sha256']='0'*64
    if case in {'wrong_channel','missing_field'}:
        s=(env.directory/'sgt.xls').read_bytes().decode('gb18030')
        s=s.replace('深HK','沪HK') if case=='wrong_channel' else s.replace('参考盈亏(人民币)','不存在')
        (env.directory/'sgt.xls').write_bytes(s.encode('gb18030'));refresh_hash(env,'sgt')
    if case=='wrong_owner':env.receipt['files']['hgt']['account_id']='other-owner'
    if case=='unverified_owner':env.receipt['files']['sgt']['account_verified_in_gui']=False
    if case=='missing_query':del env.receipt['files']['sgt']['queried_at']
    if case=='wrong_count':env.receipt['files']['hgt']['row_count']=1
    if case=='wrong_assets':env.receipt['account_total_assets_cny']='1'
    if case=='shareholder_mismatch':
        env.config['shareholder_accounts']=['other-shareholder'];write_json(env.config_path,env.config)
    if case in {'future_query','old_query','old_export'}:
        delta={'future_query':timedelta(days=1),'old_query':-timedelta(days=2),'old_export':timedelta(minutes=30)}[case]
        for evidence in env.receipt['files'].values():evidence['queried_at']=(env.query+delta).isoformat()
    write_json(env.directory/'batch.json',env.receipt)
    result=publish(env)
    assert result.status=='failed' and facts(env)==before
    assert result.error in {'NATIVE_BATCH_INVALID','NATIVE_BATCH_INCOMPLETE','NATIVE_RECONCILIATION_FAILED',
                            'NATIVE_ACCOUNT_UNPROVEN','NATIVE_BATCH_STALE'}


def test_duplicate_identity_and_nonfinite_amounts_fail(env):
    for bad in ['NaN','Infinity','not-number']:
        files={**env.files,'sgt':env.files['sgt'].replace(b'160.00',bad.encode())}
        with pytest.raises(ValueError):env.api.parse_exports(files)
    files={**env.files,'sgt':env.files['sgt']+env.files['sgt'].splitlines(keepends=True)[1]}
    with pytest.raises(ValueError,match='DUPLICATE'):env.api.parse_exports(files)


@pytest.mark.parametrize('cash',['0','-10','20.50'])
def test_confirmed_empty_and_native_cash_not_available_funds(env,cash):
    assert publish(env).status=='success'
    make_batch(env.root.parent,batch='empty',empty=True,cash=cash,query=env.query+timedelta(seconds=2))
    result=publish(env);assert result.status=='success',result.error
    with env.sessions() as db:
        row=db.query(Position).one()
        assert row.vehicle_type=='CASH' and row.original_value==float(cash) and row.quantity==float(cash)
    # A blank file cannot be used as an empty-account proof.
    (env.root/'empty/hgt.xls').write_bytes(b'')
    before=facts(env);assert publish(env).status=='failed' and facts(env)==before


def legacy(**kw):
    return Position(**{**dict(portfolio_id=7,name='Synthetic legacy',platform='国金证券',segment='投资',
        asset_class='权益',symbol=None,ticker='old',currency='CNY',market_value_cny=500),**kw})


def test_legacy_requires_explicit_cas_and_preserves_independent_and_other_scope(env):
    with env.sessions() as db:
        duplicate=legacy();independent=legacy(name='Independent manual')
        old_api=legacy(name='Old owned summary',symbol='HKCONNECT:SUMMARY',ticker='HKCONNECT',
            broker='guojin',broker_account_id='fixture-owner',sync_source='api')
        other=legacy(broker='guojin',broker_account_id='another-owner',sync_source='api')
        other_broker=legacy(broker='tiger',broker_account_id='tiger-owner',sync_source='api')
        other_portfolio=legacy(portfolio_id=8,broker='guojin',broker_account_id='third-owner',sync_source='api')
        db.add_all([duplicate,independent,old_api,other,other_broker,other_portfolio]);db.commit()
        ids=[p.id for p in [duplicate,independent,old_api,other,other_broker,other_portfolio]]
        env.config['legacy_positions']=[dict(position_id=p.id,fingerprint=env.api.fingerprint(p),action=action,
            evidence='synthetic explicit owner/duplicate confirmation') for p,action in [(duplicate,'retire'),(independent,'retain')]]
    before=facts(env)
    assert publish(env).error=='NATIVE_LEGACY_REVIEW_REQUIRED' and facts(env)==before
    write_json(env.config_path,env.config)
    assert publish(env).status=='success'
    after=facts(env)
    assert publish(env).status=='success' and facts(env)==after
    with env.sessions() as db:
        assert db.get(Position,ids[0]) is None and db.get(Position,ids[2]) is None
        assert all(db.get(Position,i) for i in [ids[1],ids[3],ids[4],ids[5]])
        assert all((i,h) in after for i,h in before if i in [ids[1],ids[3],ids[4],ids[5]])
        assert db.query(env.api.Snapshot).count()==10  # old history is not rewritten


def test_legacy_changed_after_review_fails_atomically(env):
    with env.sessions() as db:
        row=legacy();db.add(row);db.commit()
        env.config['legacy_positions']=[dict(position_id=row.id,fingerprint=env.api.fingerprint(row),action='retire',evidence='synthetic')]
        row.market_value_cny=900;db.commit()
    write_json(env.config_path,env.config);before=facts(env)
    assert publish(env).error=='NATIVE_LEGACY_REVIEW_REQUIRED' and facts(env)==before
    with env.sessions() as db:assert db.query(env.api.Snapshot).count()==0


def test_publication_failure_rolls_back_legacy_and_positions(env,monkeypatch):
    with env.sessions() as db:
        row=legacy();db.add(row);db.commit()
        env.config['legacy_positions']=[dict(position_id=row.id,fingerprint=env.api.fingerprint(row),action='retire',evidence='synthetic')]
    write_json(env.config_path,env.config);before=facts(env)
    def broken(*_):raise RuntimeError('PRIVATE_RAW_ERROR')
    monkeypatch.setattr(env.api.Service,'_remove_qmt_stale_positions',broken)
    assert publish(env).error=='SYNC_SOURCE_FAILED' and facts(env)==before
    with env.sessions() as db:assert db.query(env.api.Snapshot).count()==0


def test_native_claim_and_old_batch_cannot_publish_late(env):
    service=env.api.Service(env.config_path)
    executor=env.api.Executor(['guojin'],7,lambda _:service,session_factory=env.sessions)
    jobs,_=executor.reserve('guojin')
    other=env.api.Executor(['guojin'],7,lambda _:service,session_factory=env.sessions)
    assert other.reserve('guojin')==([],['guojin'])
    executor.execute('guojin',jobs[0]['run_id'])
    make_batch(env.root.parent,batch='new',query=env.query+timedelta(seconds=2))
    assert publish(env).status=='success';before=facts(env)
    write_json(env.root/'ready.json',dict(batch='first'))
    assert publish(env).error=='NATIVE_BATCH_STALE' and facts(env)==before


def test_publication_lease_replaced_during_file_read_cannot_publish(env):
    service=env.api.Service(env.config_path)
    original=service.fetch_positions
    def fetch():
        result=original()
        with env.sessions() as second:
            run=second.query(env.api.Run).filter_by(status='running').one()
            meta=env.api.context(run);meta['token']='replaced';run.context_json=json.dumps(meta);second.commit()
        return result
    service.fetch_positions=fetch
    executor=env.api.Executor(['guojin'],7,lambda _:service,session_factory=env.sessions)
    jobs,_=executor.reserve('guojin');executor.execute('guojin',jobs[0]['run_id'])
    assert facts(env)==[]


def test_web_mcp_and_issuer_views_read_same_four_holdings(env):
    assert publish(env).status=='success'
    from pocs.chatgpt_skills.reader import read_details
    from backend.services.instruments.issuers import exposure
    result=read_details(env.engine.url.database,7,'get_investment_details',{},synthetic=True)
    assert result['portfolio_summary']['total_assets']==1490
    cars=read_details(env.engine.url.database,7,'get_investment_details',{'symbol':'2015:HK'},synthetic=True)['items']
    assert len(cars)==2 and {p['quantity'] for p in cars}=={10,2}
    assert all(p['currency']=='CNY' for p in cars)
    group=exposure(result['items'])['companies'][0]
    assert group['issuer_id']=='LI_AUTO' and group['market_value_cny']==960 and len(group['members'])==2
    assert {p['cost_price'] for p in group['members']}=={100}
    assert [p['quantity'] for p in group['members']]==[10,2]
    assert all('broker_account_id' not in p for p in result['items'])


def test_no_http_gateway_constructor_or_guojin_cron_and_validation_cli(env):
    source=(Path(__file__).resolve().parents[1]/'backend/main.py').read_text()
    cron=source[source.index('lambda: [broker_sync_api._run_sync'):].split('\n')[0]
    assert 'guojin' not in cron and all(b in cron for b in ['tiger','futu','snowball'])
    assert 'httpx' not in __import__('inspect').getsource(env.api.Service)
    cli=Path(__file__).resolve().parents[1]/'backend/scripts/guojin_sync_e2e.py'
    result=subprocess.run([sys.executable,str(cli),'--files',str(env.directory)],capture_output=True,text=True)
    assert result.returncode==0 and json.loads(result.stdout)['status']=='VALIDATED_UNBOUND'
    result=subprocess.run([sys.executable,str(cli),'--files',str(env.directory),'--publish'],capture_output=True,text=True)
    assert result.returncode==1 and json.loads(result.stdout)['published'] is False


def test_retired_highest_row_id_can_be_reused_without_reapplying_deletion(env):
    with env.sessions() as db:
        row=legacy();db.add(row);db.commit();old_id=row.id
        env.config['legacy_positions']=[dict(position_id=old_id,fingerprint=env.api.fingerprint(row),action='retire',evidence='synthetic')]
    write_json(env.config_path,env.config)
    assert publish(env).status=='success'
    before=facts(env)
    assert publish(env).status=='success' and facts(env)==before
    with env.sessions() as db:
        assert db.query(Position).count()==5
        replacement=db.get(Position,old_id)
        assert replacement is None or replacement.sync_source=='api'


def test_invalid_guojin_config_does_not_reserve_or_rebind_other_sources(env,monkeypatch):
    env.config['portfolio_id']=8;write_json(env.config_path,env.config)
    from backend.api.broker_sync import _sync_account
    with env.sessions() as db:assert _sync_account(db,'guojin') is None
    executor=env.api.Executor(['guojin'],7,session_factory=env.sessions)
    with pytest.raises(ValueError,match='SYNC_ACCOUNT_UNCONFIGURED'):executor.reserve('guojin')
    assert facts(env)==[]


def test_original_aggregator_uses_native_broker_cost_independent_of_reference_pnl(env):
    assert publish(env).status=='success'
    from pocs.chatgpt_skills.analysis import readonly_business_database
    from app.utils.position_aggregator import aggregate_investment_positions
    with readonly_business_database(env.engine.url.database):
        positions,total=aggregate_investment_positions(7)
    car=next(p for p in positions if p.symbol=='2015:HK')
    assert car.market_value_cny==960 and car.cost_value==1200 and car.profit_loss_value==-295
    assert car.cost_value!=car.market_value_cny-car.profit_loss_value
    assert total==1490


def test_u1_new_view_and_old_immutable_payload(env,monkeypatch,tmp_path):
    from backend.services.asset_analysis import AssetAnalysisPreparation
    from app.asset_views import load_view
    monkeypatch.setenv('WEALTHPILOT_ANALYSIS_CACHE_DIR',str(tmp_path/'private-cache'))
    assert publish(env).status=='success'
    def prepare(key):
        executor=env.api.Executor(['guojin'],7,lambda _:env.api.Service(env.config_path),session_factory=env.sessions)
        service=AssetAnalysisPreparation(executor,env.engine.url.database,synthetic=True)
        _,claim=service.request(key);service.run(*claim)
        status=service.status(key)
        assert status['state']=='ready',status
        return load_view(env.engine.url.database,7,status['snapshot_id'])
    old=prepare('before');encoded=json.dumps(old,sort_keys=True)
    assert old['totals']['total_assets']==1490
    assert old['data_quality']['can_propose_rebalance'] is False  # unknown source valuation time
    make_batch(env.root.parent,batch='after',cash='0',empty=True,query=env.query+timedelta(seconds=2))
    assert publish(env).status=='success'
    new=prepare('after')
    assert new['totals']['total_assets']==0
    assert json.dumps(load_view(env.engine.url.database,7,old['snapshot_id']),sort_keys=True)==encoded


def test_receipt_reformat_is_same_batch_and_unknown_identity_is_not_inferred(env):
    from backend.utils.symbol import position_security_symbol
    assert publish(env).status=='success';before=facts(env)
    (env.directory/'batch.json').write_text(json.dumps(env.receipt,sort_keys=True,separators=(',',':')))
    assert publish(env).status=='success' and facts(env)==before
    assert position_security_symbol('2015.HGT:HK','guojin')=='2015.HGT:HK'
    assert position_security_symbol('2015.HGT:HK','tiger','QNX_NATIVE_POSITION')=='2015.HGT:HK'


def test_cash_collision_cannot_claim_independent_manual_cash(env):
    with env.sessions() as db:
        row=legacy(name='Independent cash',asset_class='货币',vehicle_type='CASH',symbol='CASH-CNY:CN',ticker='CASH-CNY')
        db.add(row);db.commit()
        env.config['legacy_positions']=[dict(position_id=row.id,fingerprint=env.api.fingerprint(row),action='retain',evidence='independent')]
    write_json(env.config_path,env.config);before=facts(env)
    assert publish(env).error=='CASH_IDENTITY_COLLISION_REQUIRES_REVIEW'
    assert facts(env)==before


def test_cross_broker_same_hk_security_uses_identity_currency_not_valuation_currency(env):
    assert publish(env).status=='success'
    with env.sessions() as db:
        db.add(legacy(name='Synthetic other HK holding',platform='老虎证券',broker='tiger',broker_account_id='other',
            sync_source='api',symbol='2015:HK',ticker='2015',currency='HKD',original_currency='HKD',
            quantity=20,cost_price=5,market_value_cny=800,profit_loss_value=100,
            vehicle_type='COMMON_STOCK',economic_asset_class='EQUITY'))
        db.commit()
    from pocs.chatgpt_skills.analysis import readonly_business_database
    from app.utils.position_aggregator import aggregate_investment_positions
    with readonly_business_database(env.engine.url.database):
        rows,total=aggregate_investment_positions(7)
    cars=[p for p in rows if p.symbol=='2015:HK']
    assert len(cars)==1 and cars[0].market_value_cny==1760 and cars[0].cost_value==1900
    assert total==2290
    from pocs.chatgpt_skills.reader import read_details
    from backend.services.instruments.issuers import exposure
    items=read_details(env.engine.url.database,7,'get_investment_details',{},synthetic=True)['items']
    company=exposure(items)['companies'][0]
    assert company['market_value_cny']==1760 and len(company['members'])==3
    assert {p['currency'] for p in company['members']}=={'CNY','HKD'}


def test_publish_cli_reuses_guarded_writer_connections_and_explicit_utc(env,tmp_path):
    import os,sqlite3
    from app.shared_sqlite import schema_digest
    database=Path(env.engine.url.database).resolve();env.engine.dispose();database.chmod(0o600)
    with sqlite3.connect(database) as db:digest=schema_digest(db)
    stat=database.stat();contract=tmp_path/'contract.json'
    write_json(contract,dict(schema='wealthpilot.shared-sqlite.v1',mode='shared_wal',portfolio_id=7,
        database_path=str(database),file_identity=[stat.st_dev,stat.st_ino],schema_sha256=digest))
    cli=Path(__file__).resolve().parents[1]/'backend/scripts/guojin_sync_e2e.py'
    # Observe the existing authority rather than duplicating its checks. Before
    # the fix preflight passed, but the real writes never used writer_connection.
    wrapper='''import json,runpy,sys
import app.shared_sqlite as shared
original=shared.writer_connection
calls=[]
def guarded(*args,**kwargs):
 calls.append(True)
 return original(*args,**kwargs)
shared.writer_connection=guarded
sys.argv=sys.argv[1:]
try:runpy.run_path(sys.argv[0],run_name='__main__')
except SystemExit as exc:
 assert exc.code==0
assert calls, 'CLI skipped the strict shared writer guard'
print(json.dumps({'guarded_connections':len(calls)}))
'''
    process=subprocess.run([sys.executable,'-c',wrapper,str(cli),'--config',str(env.config_path),
        '--publish','--database',str(database),'--contract',str(contract)],capture_output=True,text=True,
        env={**os.environ,'WEALTHPILOT_ENABLED_SYNC_BROKERS':'guojin','PYTHONPATH':str(cli.parents[2])})
    assert process.returncode==0,process.stderr+process.stdout
    result,guard=[json.loads(line) for line in process.stdout.splitlines()]
    assert result['status']=='success' and result['finished_at'].endswith('+00:00')
    assert guard['guarded_connections']>0
    with env.sessions() as db:assert db.query(Position).count()==5
