"""Synthetic history only; no production data or live broker/provider I/O."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app.models import Portfolio, Position
from backend.services.portfolio_history.models import HistoryImport, HistoryEvent
from backend.services.portfolio_history.sources import parse_flex, recent_ibkr, historical_fx
from backend.services.portfolio_history.imports import import_batch
from backend.services.portfolio_history.calculation import attribution
from backend.services.portfolio_history.query import query, history_account_ref

KEY='conid:354802220'

def execution(exec_id='fill-1',side='BUY',quantity='10',price='100',when='2024-01-02T15:00:00+00:00',**extra):
    return {**dict(kind='EXECUTION',external_id=exec_id,instrument_key=KEY,symbol='IB01:LSE',currency='USD',
        sec_type='STK',executed_at=when,time_precision='instant',source_timezone='UTC',source_local_date=when[:10],
        source_time_raw=when,source_date_semantics='execution',fx_date=when[:10],fx_date_basis='broker_settlement_date',side=side,quantity=quantity,price=price,
        multiplier='1',commission='1',commission_currency='USD',taxes='0',broker_realized_pnl=None,
        broker_cost_basis=None,broker_order_id='100',perm_id=None,order_ref=None,fx=None),**extra}

def coverage(**kw):
    return {**dict(coverage_start='2024-01-01T00:00:00+00:00',coverage_end='2026-01-01T00:00:00+00:00',
        source_timezone='UTC',scope='instruments',instrument_keys=[KEY],complete_executions=True,complete_cashflows=True,
        corporate_actions_reviewed=True,opening_zero_keys=[KEY],evidence_ref='synthetic-reviewed-statement'),**kw}

def evidence(c=None, *, source_sha='a'*64, metadata=None, broker='ibkr', account='fixture'):
    """Explicit synthetic opening statement and export-definition receipt; never inferred from fills."""
    c=dict(c or coverage())
    document=json.dumps(dict(synthetic=True,account=account,broker=broker,as_of=c['coverage_start'],
        opening={key:'0' for key in c['opening_zero_keys']},filter_scope=c['scope'],keys=c['instrument_keys'])).encode()
    digest=hashlib.sha256(document).hexdigest()
    c['report_evidence']=dict(broker=broker,account_id=account,document_sha256=source_sha,
        definition_sha256=digest,locator='synthetic export definition, all named sections and exact scope',
        coverage_start=c['coverage_start'],coverage_end=c['coverage_end'],source_timezone=c['source_timezone'],
        filter_scope=c['scope'],instrument_keys=c['instrument_keys'],sections=['Trades','CashTransactions','CorporateActions'])
    c['opening_evidence']=[dict(broker=broker,account_id=account,instrument_key=key,as_of=c['coverage_start'],
        quantity='0',document_sha256=digest,locator='synthetic independent opening statement row '+key)
        for key in c['opening_zero_keys']]
    meta=dict(source_sha256=source_sha,account_id=account,source_timezone=c['source_timezone'],format='xml',
        sections_present=['Trades','CashTransactions','CorporateActions'],report_bounds_utc=[(c['coverage_start'],c['coverage_end'])],issues=[])
    meta.update(metadata or {})
    return dict(evidence_version=2,broker=broker,account_id=account,source_sha256=source_sha,
        coverage=c,source_metadata=meta,verified_document_sha256=[digest],_documents=[document])

def closed():return [execution(),execution('fill-2','SELL','10','120','2025-01-02T15:00:00+00:00')]

def flex(rows,cash=(),other=False):
    root=ET.Element('FlexQueryResponse');statements=ET.SubElement(root,'FlexStatements')
    s=ET.SubElement(statements,'FlexStatement',accountId='fixture',fromDate='20240101',toDate='20251231')
    ET.SubElement(s,'CorporateActions')
    trades=ET.SubElement(s,'Trades')
    for row in rows:
        values={**dict(accountId='fixture',symbol='IB01',conid='354802220',currency='USD',assetCategory='STK',
            levelOfDetail='EXECUTION',ibExecID='synthetic-exec-1',dateTime='20240102;15:00:00',buySell='BUY',
            quantity='10',tradePrice='100',ibCommission='-1',ibCommissionCurrency='USD',taxes='0'),**row}
        ET.SubElement(trades,'Trade',**{k:str(v) for k,v in values.items()})
    flows=ET.SubElement(s,'CashTransactions')
    for row in cash:
        ET.SubElement(flows,'CashTransaction',**{**dict(accountId='fixture',symbol='IB01',conid='354802220',
            currency='USD',assetCategory='STK',transactionID='div-1',dateTime='2025-01-02',type='Dividends',amount='10'),**row})
    if other:
        o=ET.SubElement(statements,'FlexStatement',accountId='OTHER_PRIVATE',fromDate='20240101',toDate='20251231')
        ET.SubElement(o,'Trade',symbol='SECRET',quantity='999999')
    return ET.tostring(root)

@pytest.fixture
def history_db(tmp_path,monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'backend'))
    import services.broker_sync.models  # noqa: F401
    import backend.services.action.models  # noqa: F401
    path=tmp_path/'history.db';path.touch(mode=0o600);engine=create_engine('sqlite:///'+str(path),connect_args={'timeout':10})
    Base.metadata.create_all(engine)
    with engine.begin() as c:c.exec_driver_sql('PRAGMA journal_mode=WAL')
    with Session(engine) as db:
        db.add_all([Portfolio(id=7,name='Synthetic'),Portfolio(id=8,name='Other')]);db.flush()
        db.add(Position(portfolio_id=7,name='Synthetic IB01',platform='IBKR',asset_class='固收',
            broker='snowball',broker_account_id='fixture',sync_source='api',currency='USD',original_currency='USD',
            symbol='IB01:LSE',ticker='IB01',quantity=0,current_price=1,cost_price=1,original_value=0,
            classification_evidence_json=json.dumps({'con_id':354802220})))
        db.commit()
    yield SimpleNamespace(engine=engine,path=path)
    engine.dispose()

def persist(c,events=None,manifest=None,source='ibkr_flex',metadata=None):
    events=closed() if events is None else events
    sha=(metadata or {}).get('source_sha256') or hashlib.sha256(json.dumps(events).encode()).hexdigest()
    proof=evidence(manifest,source_sha=sha,metadata=metadata)
    with Session(c.engine) as db:
        return import_batch(db,7,'ibkr','fixture',source,sha,events,proof['coverage'],proof['source_metadata'],
            evidence_documents=proof['_documents'])

def test_weighted_buys_partial_sell_and_dividend_not_double_counted():
    facts=[execution(),execution('b2','BUY','10','120','2024-02-02T15:00:00+00:00'),
           execution('s1','SELL','5','130','2025-01-02T15:00:00+00:00'),
           dict(kind='DIVIDEND',external_id='d1',instrument_key=KEY,symbol='IB01:LSE',currency='USD',
                sec_type='STK',executed_at='2025-01-02',time_precision='date',source_timezone='UTC',amount='10',fx=None)]
    v=dict(quantity='15',currency='USD',market_value='2100',as_of='2025-02-02T15:00:00+00:00',positions_as_of='2025-02-02T15:00:00+00:00',qualified=True)
    r=attribution(facts,[evidence()],v)
    assert r['cost_basis']=='1651.5' and r['realized_pnl']=='98.5'
    assert r['unrealized_pnl']=='448.5' and r['total_holding_pnl']=='557'
    assert r['known_dividends']=='10' and r['actual_fees_by_currency']['known_amounts']=={'USD':'3'}
    assert r['holding_period_return'] is None and r['annualized_return'] is None
    assert 'MULTIPLE_DATED_CASHFLOWS_MWR_NOT_IMPLEMENTED' in r['issues']

def test_closed_holding_period_annual_return_and_no_synthetic_interest():
    r=attribution(closed(),[evidence()])
    assert r['computed_quantity']=='0' and r['realized_pnl']=='198'
    assert r['total_holding_pnl']=='198' and Decimal(r['holding_period_return'])==Decimal(198)/Decimal(1001)
    assert Decimal(r['annualized_return'])>0 and r['known_dividends']=='0'
    assert 'actual cashflow' in r['income_basis']

@pytest.mark.parametrize('change,reason',[
    ({'commission':None},'ACTUAL_FEES_UNKNOWN'),({'commission_currency':'HKD'},'FEE_CURRENCY_NOT_NATIVE'),
    ({'taxes':None},'ACTUAL_FEES_UNKNOWN'),({'sec_type':'OPT'},'SECURITY_TYPE_OR_MULTIPLIER_UNSUPPORTED'),
    ({'multiplier':'100'},'SECURITY_TYPE_OR_MULTIPLIER_UNSUPPORTED')])
def test_missing_actual_fees_or_contract_blocks_net_return(change,reason):
    facts=closed();facts[0].update(change);r=attribution(facts,[evidence()])
    assert r['total_holding_pnl'] is None and r['realized_pnl'] is None and reason in r['issues']

@pytest.mark.parametrize('field,value',[('complete_executions',False),('opening_zero_keys',[]),
    ('corporate_actions_reviewed',False),('complete_cashflows',False)])
def test_coverage_does_not_get_inferred_from_absence(field,value):
    c=coverage();c[field]=value;r=attribution(closed(),[evidence(c)])
    assert r['holding_period_return'] is None and r['annualized_return'] is None
    assert r['realized_pnl']==('198' if field=='complete_cashflows' else None)

def test_gaps_and_contiguous_intervals():
    a=coverage(coverage_end='2024-06-01T00:00:00+00:00')
    b=coverage(coverage_start='2024-07-01T00:00:00+00:00',opening_zero_keys=[])
    assert attribution(closed(),[evidence(a),evidence(b)])['realized_pnl'] is None
    b['coverage_start']=a['coverage_end']
    assert attribution(closed(),[evidence(a),evidence(b)])['realized_pnl']=='198'

def test_oversell_and_unknown_execution_sequence():
    f=closed();f[1]['quantity']='11'
    assert attribution(f,[evidence()])['realized_pnl'] is None
    f=closed();f[1]['executed_at']=f[0]['executed_at']
    assert 'EXECUTION_SEQUENCE_UNPROVEN' in attribution(f,[evidence()])['issues']

def test_dated_fx_bridge_and_missing_historical_rate():
    f=closed()
    for e,rate in zip(f,['7','7.2']):e['fx']=dict(rate=rate,rate_date=e['executed_at'][:10],source='fixture',pair='USD/CNY')
    r=attribution(f,[evidence()]);assert r['translated_total_pnl_cny']=='1625.8'
    assert r['fx_effect_cny']=='239.8'
    f[0]['fx']=None;r=attribution(f,[evidence()])
    assert r['fx_effect_cny'] is None and 'HISTORICAL_OR_VALUATION_FX_MISSING' in r['issues']

def test_original_fx_service_exact_date_only():
    from app.fx_service import FXService
    class Wrong:
        def fetch_rate(self,a,b,day):return 7,'2024-01-01'
    assert historical_fx([execution()],FXService(Wrong()))[0]['fx'] is None
    class Exact:
        def fetch_rate(self,a,b,day):return 7,day
    e=historical_fx([execution()],FXService(Exact()))[0]
    assert e['fx']['rate_date']=='2024-01-02' and e['fx']['rate']=='7'

def test_flex_actual_account_identity_fees_and_cashflow():
    f,m=parse_flex(flex([{}],cash=[{}],other=True),'fixture',zone='UTC')
    assert len(f)==2 and f[0]['instrument_key']==KEY and f[0]['symbol']=='IB01:LSE'
    assert f[0]['commission']=='1' and f[1]['amount']=='10'
    assert m['report_periods']==[('2024-01-01','2025-12-31')]
    assert 'OTHER_PRIVATE' not in json.dumps(f)

@pytest.mark.parametrize('row',[{'levelOfDetail':'ORDER'},{'levelOfDetail':'CLOSED_LOT'},{'ibExecID':''},
    {'accountId':'other'},{'conid':'354802220','isin':'US78462F1030'},{'assetCategory':'OPT'},
    {'origTradeID':'old'},{'tradePrice':'NaN'},{'quantity':'0'},{'dateTime':'20241103;01:30:00'}])
def test_bad_flex_summary_identity_correction_time_rejected(row):
    with pytest.raises(ValueError):parse_flex(flex([row]),'fixture',zone='America/New_York')

def test_no_owner_or_external_xml_entities():
    with pytest.raises(ValueError):parse_flex(flex([{}]),'other',zone='UTC')
    with pytest.raises(ValueError):parse_flex(b'<!DOCTYPE x [<!ENTITY x SYSTEM "file:///etc/passwd">]><x/>','fixture',zone='UTC')

def test_csv_execution_detail_and_missing_commission():
    data=b'ClientAccountID,Symbol,Conid,CurrencyPrimary,AssetClass,LevelOfDetail,IBExecID,DateTime,Buy/Sell,Quantity,TradePrice,IBCommission,IBCommissionCurrency,Taxes\nfixture,IB01,354802220,USD,STK,EXECUTION,e1,20240102;15:00:00,BUY,10,100,,,0\n'
    f,m=parse_flex(data,'fixture',zone='UTC')
    assert f[0]['commission'] is None and 'CSV_CASHFLOWS_NOT_INCLUDED' in m['issues']

def test_recent_scope_partials_and_unknown_fee():
    row=dict(exec_id='recent-1',observed_account_id='fixture',con_id=354802220,symbol='IB01',currency='USD',
        sec_type='STK',side='BOT',shares=2,price=100,timestamp='2024-01-02T15:00:00+00:00',commission=None)
    calls=[]
    a=SimpleNamespace(_account_id='fixture',list_execution_details=lambda:calls.append(True) or [row,{**row,'exec_id':'recent-2','shares':3}])
    f=recent_ibkr(a,'fixture');assert len(f)==2 and f[0]['commission'] is None
    with pytest.raises(ValueError):recent_ibkr(a,'OTHER_PRIVATE')
    assert len(calls)==1
    row['observed_account_id']='OTHER_PRIVATE'
    with pytest.raises(ValueError):recent_ibkr(a,'fixture')

def test_idempotency_conflict_atomicity_no_asset_changes(history_db):
    c=history_db;assert persist(c)['status']=='imported';assert persist(c)['status']=='unchanged'
    bad=closed();bad[0]['price']='999'
    with pytest.raises(ValueError,match='EVENT_CONFLICT'):persist(c,bad)
    with Session(c.engine) as db:
        assert db.query(HistoryEvent).count()==2 and db.query(HistoryImport).count()==1
        assert db.query(Position).one().quantity==0
        r=query(db,7,synthetic=True);assert len(r['items'])==2 and r['attribution'][0]['realized_pnl']=='198'
        assert 'fixture' not in json.dumps(r) and 'account_id' not in json.dumps(r)

def test_missing_fee_enrichment_and_new_known_conflict(history_db):
    f=closed();f[0].update(commission=None,commission_currency=None);persist(history_db,f);persist(history_db)
    with Session(history_db.engine) as db:
        assert db.query(HistoryEvent).count()==2 and db.query(HistoryImport).count()==2
        assert query(db,7)['attribution'][0]['realized_pnl']=='198'
    f=closed();f[0]['commission']='2'
    with pytest.raises(ValueError):persist(history_db,f)

def test_two_connections_import_same_exec_once(history_db):
    with ThreadPoolExecutor(2) as pool:r=list(pool.map(lambda _:persist(history_db),range(2)))
    assert sorted(i['status'] for i in r)==['imported','unchanged']
    with Session(history_db.engine) as db:assert db.query(HistoryEvent).count()==2

def test_correction_suffix_not_another_partial_fill(history_db):
    a=execution('abc.01.01');persist(history_db,[a,execution('abc.02.01',quantity='2')])
    with pytest.raises(ValueError,match='CORRECTION'):persist(history_db,[{**a,'external_id':'abc.01.02'}])
    with Session(history_db.engine) as db:assert db.query(HistoryEvent).count()==2

def test_recent_window_never_full_history(history_db):
    persist(history_db,source='ibkr_recent')
    with Session(history_db.engine) as db:
        r=query(db,7);assert r['attribution'][0]['realized_pnl'] is None
        assert not r['coverage'][0]['complete_executions']

def test_six_unknown_owners_not_claimed_and_cross_portfolio_denied(history_db):
    from backend.services.action.models import OrderRecord
    with Session(history_db.engine) as db:
        for n in range(6):db.add(OrderRecord(id='legacy-'+str(n),strategy_id='legacy',symbol='IB01:LSE',side='BUY',quantity=1,
            broker_name='ibkr',broker_order_id=str(100+n),status='filled',portfolio_id=None,broker_account_id=None))
        db.commit()
    persist(history_db)
    with Session(history_db.engine) as db:
        assert query(db,8)['items']==[]
        with pytest.raises(ValueError):query(db,8,account_ref=history_account_ref(7,'ibkr','fixture'))
        assert db.query(OrderRecord).filter(OrderRecord.portfolio_id.is_(None)).count()==6
        assert all(i['local_order_id'] is None for i in query(db,7)['items'])
    with Session(history_db.engine) as db:
        with pytest.raises(ValueError):import_batch(db,7,'ibkr','UNPROVEN','ibkr_flex','a'*64,closed(),coverage())
    with Session(history_db.engine) as db:
        db.add(Position(portfolio_id=8,name='Conflict',platform='IBKR',asset_class='固收',broker='snowball',broker_account_id='fixture'));db.commit()
    with pytest.raises(ValueError,match='AMBIGUOUS'):persist(history_db)

def test_window_limits_facts_not_cost_basis(history_db):
    persist(history_db)
    with Session(history_db.engine) as db:
        r=query(db,7,start='2025-01-01T00:00:00Z',end='2025-01-03T00:00:00Z',limit=1)
        assert r['total']==1 and r['items'][0]['side']=='SELL' and r['attribution'][0]['realized_pnl']=='198'
        assert query(db,7,end='2025-01-02T15:00:00Z')['total']==1
        assert query(db,7,symbol='IB01')['total']==2

def test_same_symbol_different_markets_remain_separate(history_db):
    a=closed();b=[{**e,'external_id':e['external_id']+'-other','instrument_key':'conid:9999','symbol':'IB01:US'} for e in closed()]
    c=coverage(instrument_keys=[KEY,'conid:9999'],opening_zero_keys=[KEY,'conid:9999']);persist(history_db,a+b,c)
    with Session(history_db.engine) as db:assert len(query(db,7)['attribution'])==2

def test_readonly_query_protocol_guards_no_network(history_db,monkeypatch):
    from pocs.chatgpt_skills.reader import read_history
    from pocs.chatgpt_skills.contracts import TradeHistoryQuery
    from pydantic import ValidationError
    import socket
    persist(history_db);monkeypatch.setattr(socket.socket,'connect',lambda *_:pytest.fail('network forbidden'))
    assert read_history(history_db.path,7,{},synthetic=True)['items'][0]['commission']=='1'
    for a in ({'account_id':'fixture'},{'portfolio_id':8},{'refresh':True},{'force':True},{'limit':201},{'limit':True},
            {'start':'2024-01-01'},{'symbol':'x; DROP TABLE'},{'start':'2025-01-01T00:00:00Z','end':'2024-01-01T00:00:00Z'}):
        with pytest.raises(ValidationError):TradeHistoryQuery.model_validate(a)


def test_reconciliation_and_valuation_evidence_are_independent():
    f=closed();f[1]['broker_realized_pnl']='199'
    r=attribution(f,[evidence()])
    assert r['broker_reported_realized_pnl']=='199' and r['realized_reconciliation_difference']=='1'
    assert r['native_attribution']==dict(price='200',dividend='0',income_tax='0',interest='0',other_cashflow='0',actual_trade_fees='-2',unexplained_residual='0')
    f=[execution()];v=dict(quantity='10',currency='USD',market_value='1200',broker_cost_basis='1000',
        as_of='2024-01-03T15:00:00+00:00',positions_as_of='2024-01-03T15:00:00+00:00',qualified=True)
    r=attribution(f,[evidence()],v)
    assert r['unrealized_pnl']=='199' and r['cost_reconciliation_difference']=='-1'
    for change,reason in [({'quantity':'9'},'POSITION_QUANTITY_RECONCILIATION_MISMATCH'),
            ({'qualified':False},'VALUATION_SOURCE_TIME_OR_QUALITY_UNVERIFIED'),
            ({'as_of':'2023-12-01T15:00:00+00:00'},'VALUATION_OR_POSITION_PRECEDES_FILL'),
            ({'as_of':'2026-02-01T15:00:00+00:00'},'HISTORY_DOES_NOT_COVER_VALUATION')]:
        r=attribution(f,[evidence()],{**v,**change})
        assert r['unrealized_pnl'] is None and reason in r['issues']


def test_later_backdated_fill_invalidates_older_complete_report(history_db):
    persist(history_db)
    addition=execution('uncovered-old-fill','BUY','1','100','2024-03-02T15:00:00+00:00')
    persist(history_db,[addition],coverage(complete_executions=False,opening_zero_keys=[]))
    with Session(history_db.engine) as db:
        r=query(db,7)
        assert r['attribution'][0]['realized_pnl'] is None
        assert 'REPORT_COVERAGE_CONTRADICTED_BY_IMPORTED_FACTS' in r['issues']
        assert all(not p['complete_executions'] for p in r['coverage'])


def test_cb_u3_and_instrument_name_conflict_are_not_silent_aliases():
    f,_=parse_flex(flex([{'symbol':'CSBGU3','conid':'79000224'}]),'fixture',zone='UTC')
    assert f[0]['symbol']=='CBU3:LSE'
    with pytest.raises(ValueError,match='IDENTITY_CONFLICT'):
        parse_flex(flex([{'symbol':'CBU3','conid':'354802220'}]),'fixture',zone='UTC')


def test_known_corporate_actions_prevent_old_coverage_from_claiming_costs():
    proof=evidence(metadata={'issues':['CORPORATE_ACTIONS_UNSUPPORTED']})
    assert attribution(closed(),[evidence(),proof])['realized_pnl'] is None


def test_real_cli_dry_run_apply_and_repeat_on_private_synthetic_contract(history_db,tmp_path):
    import os,sys,subprocess,sqlite3
    from app.shared_sqlite import schema_digest
    c=history_db;c.path.chmod(0o600);stat=c.path.stat()
    source=tmp_path/'private-flex.xml';source.write_bytes(flex([{},dict(ibExecID='sell-1',buySell='SELL',quantity='-10',
        tradePrice='120',dateTime='20250102;150000')]));source.chmod(0o600)
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    facts,meta=parse_flex(source.read_bytes(),'fixture',zone='UTC')
    proof=evidence(source_sha=sha,metadata=meta)
    document=tmp_path/'independent-statement.txt';document.write_bytes(proof['_documents'][0]);document.chmod(0o600)
    manifest=tmp_path/'coverage.json';manifest.write_text(json.dumps(proof['coverage']));manifest.chmod(0o600)
    with sqlite3.connect(c.path) as db:digest=schema_digest(db)
    contract=tmp_path/'contract.json';contract.write_text(json.dumps(dict(schema='wealthpilot.shared-sqlite.v1',mode='shared_wal',
        database_path=str(c.path.resolve()),file_identity=[stat.st_dev,stat.st_ino],schema_sha256=digest,portfolio_id=7)));contract.chmod(0o600)
    root=Path(__file__).resolve().parents[1]
    command=[sys.executable,str(root/'scripts/import_trade_history.py'),'--database',str(c.path),'--contract',str(contract),
        '--portfolio-id','7','--account-id','fixture','--flex-file',str(source),'--coverage',str(manifest),'--evidence-document',str(document)]
    env={k:os.environ[k] for k in ('PATH','HOME','LANG','TMPDIR','PYTHON_DOTENV_DISABLED') if k in os.environ}
    env['PYTHON_DOTENV_DISABLED']='1';env['WEALTHPILOT_DB_PATH']=str(c.path)
    first=subprocess.run(command,cwd=root,env=env,capture_output=True,text=True)
    assert first.returncode==0,first.stdout+first.stderr
    assert json.loads(first.stdout)['status']=='validated_only'
    assert json.loads(first.stdout)['coverage_quality']=='complete'
    missing=subprocess.run(command[:-2],cwd=root,env=env,capture_output=True,text=True)
    assert missing.returncode==0 and json.loads(missing.stdout)['coverage_quality']=='partial'
    with Session(c.engine) as db:assert db.query(HistoryImport).count()==0
    for expected in ('imported','unchanged'):
        r=subprocess.run(command+['--apply'],cwd=root,env=env,capture_output=True,text=True)
        assert r.returncode==0,r.stdout+r.stderr
        assert json.loads(r.stdout)['status']==expected
    with Session(c.engine) as db:assert db.query(HistoryEvent).count()==2


def test_multi_account_and_broker_scope_same_exec_ids_stay_separate(history_db):
    c=history_db;persist(c)
    with Session(c.engine) as db:
        db.add_all([Position(portfolio_id=7,name='Other IBKR',platform='IBKR',asset_class='固收',broker='snowball',broker_account_id='second'),
                    Position(portfolio_id=7,name='Tiger',platform='Tiger',asset_class='固收',broker='tiger',broker_account_id='fixture')]);db.commit()
    for broker,account in [('ibkr','second'),('tiger','fixture')]:
        with Session(c.engine) as db:import_batch(db,7,broker,account,'synthetic_statement','b'*64,closed(),coverage())
    with Session(c.engine) as db:
        assert len(query(db,7)['attribution'])==3
        r=query(db,7,account_ref=history_account_ref(7,'ibkr','fixture'))
        assert len(r['items'])==2 and len(r['accounts'])==1 and len(r['attribution'])==1
        assert r['items'][0]['broker']=='ibkr'


def test_clear_then_rebuy_weighted_cost_resets_but_not_annual_cashflow_history():
    f=closed()+[execution('rebuy','BUY','2','50','2025-02-01T15:00:00+00:00')]
    v=dict(quantity='2',currency='USD',market_value='120',as_of='2025-02-02T15:00:00+00:00',positions_as_of='2025-02-02T15:00:00+00:00',qualified=True)
    r=attribution(f,[evidence()],v)
    assert r['cost_basis']=='101' and r['realized_pnl']=='198' and r['unrealized_pnl']=='19'
    assert r['holding_period_return'] is None


def test_local_order_link_requires_matching_owner_side_and_security(history_db):
    from backend.services.action.models import OrderRecord
    with Session(history_db.engine) as db:
        db.add(OrderRecord(id='owned-buy',strategy_id='legacy',portfolio_id=7,broker_account_id='fixture',
            symbol='IB01:LSE',side='BUY',quantity=10,broker_name='ibkr',broker_order_id='100',status='filled'));db.commit()
    facts=closed();facts[0]['order_ref']='owned-buy';persist(history_db,facts)
    with Session(history_db.engine) as db:
        r=query(db,7)
        assert r['items'][0]['local_order_id']=='owned-buy' and r['items'][1]['local_order_id'] is None


@pytest.mark.parametrize('row',[{'assetCategory':''},{'symbol':'IB01:US'},{'taxes':'NaN'},{'ibCommission':'NaN'}])
def test_missing_security_evidence_or_bad_signed_fees_fail_closed(row):
    with pytest.raises(ValueError):parse_flex(flex([row]),'fixture',zone='UTC')


def test_date_precision_and_negative_zero_do_not_invent_execution_facts(history_db):
    from backend.services.portfolio_history.sources import number
    assert number('-0.000')==number('0')=='0'
    with pytest.raises(ValueError):parse_flex(flex([{'dateTime':'20240102'}]),'fixture',zone='UTC')
    f=closed();f[0]['taxes']='-0.0';persist(history_db,f);persist(history_db)
    with Session(history_db.engine) as db:assert db.query(HistoryEvent).count()==2


@pytest.mark.parametrize('field,value',[('TransactionType','TradeCancel'),('OrigTradeID','prior-fill'),('OrigOrderID','prior-order')])
def test_native_csv_correction_fields_cannot_bypass_xml_safety(field,value):
    data=('ClientAccountID,Symbol,Conid,CurrencyPrimary,AssetClass,LevelOfDetail,IBExecID,DateTime,Buy/Sell,Quantity,TradePrice,'+field+'\nfixture,IB01,354802220,USD,STK,EXECUTION,c1,20240102;15:00:00,BUY,1,100,'+value+'\n').encode()
    with pytest.raises(ValueError,match='CORRECTION'):parse_flex(data,'fixture',zone='UTC')


def test_order_id_number_alone_is_not_a_permanent_local_order_identity(history_db):
    from backend.services.action.models import OrderRecord
    with Session(history_db.engine) as db:
        db.add(OrderRecord(id='same-number',strategy_id='legacy',portfolio_id=7,broker_account_id='fixture',
            symbol='IB01:LSE',side='BUY',quantity=10,broker_name='ibkr',broker_order_id='100',status='filled'));db.commit()
    persist(history_db)
    with Session(history_db.engine) as db:assert query(db,7)['items'][0]['local_order_id'] is None
