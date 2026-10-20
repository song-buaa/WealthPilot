"""PR29 review reproductions become fail-closed regressions; synthetic data only."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest
from sqlalchemy.orm import Session

from tests.test_b2_history import (KEY, execution, closed, coverage, evidence, flex, persist,
                                   history_db)  # noqa: F401
from backend.services.portfolio_history.imports import import_batch, qualified_coverage, validate_events, Coverage
from backend.services.portfolio_history.sources import parse_flex, historical_fx, event_bounds, window_membership
from backend.services.portfolio_history.calculation import attribution
from backend.services.portfolio_history.query import query


def flow(day='2025-01-03', **kw):
    return dict(kind='DIVIDEND',external_id='cash-1',instrument_key=KEY,symbol='IB01:LSE',currency='USD',
        sec_type='STK',executed_at=day,time_precision='date',source_timezone='UTC',
        source_local_date=day,source_date_semantics='cashflow',fx=None,amount='10',**kw)


def test_r01_flags_and_arbitrary_reference_never_prove_history(history_db):
    raw=ET.fromstring(flex([{},dict(ibExecID='sell-1',dateTime='20250102;15:00:00',buySell='SELL',quantity='-10',tradePrice='120')]))
    statement=raw.find('.//FlexStatement');statement.remove(statement.find('CashTransactions'))
    data=ET.tostring(raw);facts,meta=parse_flex(data,'fixture',zone='UTC')
    with Session(history_db.engine) as db:
        import_batch(db,7,'ibkr','fixture','ibkr_flex',hashlib.sha256(data).hexdigest(),facts,coverage(),meta)
    with Session(history_db.engine) as db:r=query(db,7)
    a=r['attribution'][0]
    assert len(r['items'])==2 and a['total_holding_pnl'] is None
    assert a['holding_period_return'] is None and a['annualized_return'] is None
    assert a['actual_fees_by_currency']['known_amounts']=={'USD':'2'}
    assert not r['coverage'][0]['complete_executions'] and not r['coverage'][0]['complete_cashflows']
    assert 'REPORT_SCOPE_EVIDENCE_UNPROVEN' in a['issues']


@pytest.mark.parametrize('damage',[ 'no_cash_section','no_opening','unattached_opening','same_report_opening',
    'no_definition','filtered_scope','wrong_account','wrong_hash','wrong_window','wrong_instrument'])
def test_r01_concrete_independent_scope_required(damage):
    proof=evidence()
    if damage=='no_cash_section':proof['source_metadata']['sections_present'].remove('CashTransactions')
    elif damage=='no_opening':proof['coverage']['opening_evidence']=[]
    elif damage=='unattached_opening':proof['coverage']['opening_evidence'][0]['document_sha256']='b'*64
    elif damage=='same_report_opening':proof['coverage']['opening_evidence'][0]['document_sha256']='a'*64
    elif damage=='no_definition':proof['verified_document_sha256']=[]
    elif damage=='filtered_scope':proof['coverage']['report_evidence']['filter_scope']='account'
    elif damage=='wrong_account':proof['coverage']['report_evidence']['account_id']='other'
    elif damage=='wrong_hash':proof['coverage']['report_evidence']['document_sha256']='c'*64
    elif damage=='wrong_window':proof['coverage']['report_evidence']['coverage_end']='2025-01-01T00:00:00Z'
    elif damage=='wrong_instrument':proof['coverage']['opening_evidence'][0]['instrument_key']='conid:999'
    r=attribution(closed(),[proof])
    assert r['total_holding_pnl'] is None and r['holding_period_return'] is None and r['annualized_return'] is None
    if damage=='no_cash_section':assert r['realized_pnl']=='198'  # known execution result remains


def test_r01_valid_independent_sources_and_legacy_receipt_fail_closed(history_db):
    persist(history_db)
    with Session(history_db.engine) as db:
        r=query(db,7)['attribution'][0];assert r['realized_pnl']=='198' and r['total_holding_pnl']=='198'
        from backend.services.portfolio_history.models import HistoryImport
        record=db.query(HistoryImport).one();old=json.loads(record.evidence_json)
        old.pop('evidence_version');record.evidence_json=json.dumps(old);db.commit()
    with Session(history_db.engine) as db:
        r=query(db,7)['attribution'][0];assert r['total_holding_pnl'] is None


def test_r02_exclusive_midnight_excludes_next_day_dividend_198_not_208(history_db):
    persist(history_db,closed()+[flow()])
    with Session(history_db.engine) as db:
        r=query(db,7,end='2025-01-03T00:00:00Z')
        assert r['total']==2 and r['attribution'][0]['total_holding_pnl']=='198'
        assert r['attribution'][0]['attribution_as_of']=='2025-01-02'
        r=query(db,7,start='2025-01-03T00:00:00Z',end='2025-01-04T00:00:00Z')
        assert r['total']==1 and r['items'][0]['kind']=='DIVIDEND'
        assert r['attribution'][0]['total_holding_pnl']=='208'  # full prior cost retained


@pytest.mark.parametrize('zone,day,lower,upper,hours',[
    ('UTC','2025-01-03','2025-01-03T00:00:00Z','2025-01-04T00:00:00Z',24),
    ('Asia/Shanghai','2025-01-03','2025-01-02T16:00:00Z','2025-01-03T16:00:00Z',24),
    ('America/New_York','2024-03-10','2024-03-10T05:00:00Z','2024-03-11T04:00:00Z',23),
    ('America/New_York','2024-11-03','2024-11-03T04:00:00Z','2024-11-04T05:00:00Z',25)])
def test_r02_source_days_and_dst_are_intervals_not_midnight_instants(zone,day,lower,upper,hours):
    e=flow(day);e['source_timezone']=zone
    a,b=event_bounds(e);assert (b-a).total_seconds()==hours*3600
    assert window_membership(e,start=lower,end=upper)=='inside'
    assert window_membership(e,end=lower)=='outside'
    assert window_membership(e,start=upper)=='outside'
    assert window_membership(e,end=(a+(b-a)/2).isoformat())=='uncertain'
    assert e['executed_at']==day and e['time_precision']=='date'


def test_r02_intraday_unknown_boundary_blocks_total_without_losing_stored_flow(history_db):
    persist(history_db,closed()+[flow()])
    with Session(history_db.engine) as db:
        r=query(db,7,end='2025-01-03T12:00:00Z')
        assert r['total']==2 and len(r['uncertain_events'])==1
        assert r['attribution'][0]['realized_pnl']=='198' and r['attribution'][0]['total_holding_pnl'] is None
        assert 'HISTORY_TIME_BOUNDARY_UNCERTAIN' in r['issues']
        assert query(db,7)['total']==3


def test_r02_unknown_source_timezone_is_not_assumed_utc(history_db):
    e=flow();e.pop('source_timezone');persist(history_db,closed()+[e])
    with Session(history_db.engine) as db:
        r=query(db,7,end='2025-01-04T00:00:00Z')
        assert r['uncertain_events'] and r['attribution'][0]['total_holding_pnl'] is None


def test_r03_source_settlement_dates_not_utc_days_1170_4_not_911_9():
    buy=dict(symbol='SYNTH',conid='987654321',currency='AUD',ibCommissionCurrency='AUD',
        dateTime='20240103;10:00:00',settleDateTarget='20240103')
    sell={**buy,'ibExecID':'aud-sale','dateTime':'20250107;10:00:00','settleDateTarget':'20250107',
          'buySell':'SELL','quantity':'-10','tradePrice':'120'}
    facts,_=parse_flex(flex([buy,sell]),'fixture',zone='Australia/Sydney')
    assert facts[0]['executed_at']=='2024-01-02T23:00:00+00:00'
    assert facts[0]['source_time_raw']=='20240103;10:00:00' and facts[0]['source_local_date']=='2024-01-03'
    class Provider:
        def __init__(self):self.days=[]
        def get_quote(self,a,b,day):
            self.days.append(day)
            return SimpleNamespace(quality='historical',rate_date=day,source='synthetic',rate={'2024-01-03':4.7,'2025-01-07':4.9}[day])
    provider=Provider();facts=historical_fx(facts,provider)
    c=coverage(instrument_keys=['conid:987654321'],opening_zero_keys=['conid:987654321'])
    r=attribution(facts,[evidence(c)])
    expected=Decimal('1199')*Decimal('4.9')-Decimal('1001')*Decimal('4.7')
    assert r['translated_total_pnl_cny']==str(expected)=='1170.4'
    assert provider.days==['2024-01-03','2025-01-07']


@pytest.mark.parametrize('when,zone,utc,settlement',[
    ('20240311;09:30:00','America/New_York','2024-03-11T13:30:00+00:00','20240313'),
    ('20241104;09:30:00','America/New_York','2024-11-04T14:30:00+00:00','20241106')])
def test_r03_dst_preserves_execution_and_independent_settlement(when,zone,utc,settlement):
    facts,_=parse_flex(flex([dict(dateTime=when,settleDateTarget=settlement)]),'fixture',zone=zone)
    assert facts[0]['executed_at']==utc
    assert facts[0]['fx_date']==datetime.strptime(settlement,'%Y%m%d').date().isoformat()


def test_r03_missing_business_date_never_calls_fx_and_wrong_date_rejected():
    facts,_=parse_flex(flex([{}]),'fixture',zone='UTC')
    class NoCalls:
        def get_quote(self,*a):raise AssertionError('must not infer FX date from instant')
    assert historical_fx(facts,NoCalls())[0]['fx'] is None
    bad=execution();bad['fx']=dict(rate='7',rate_date='2024-01-01',source='synthetic',pair='USD/CNY')
    with pytest.raises(ValueError):validate_events([bad],Coverage.model_validate(coverage()))
    class WrongDate:
        def get_quote(self,*a):return SimpleNamespace(quality='historical',rate_date='2024-01-01',rate=7,source='synthetic')
    assert historical_fx([execution()],WrongDate())[0]['fx'] is None


def test_r03_actual_cashflow_date_survives_cross_utc_day():
    f,_=parse_flex(flex([],cash=[dict(dateTime='20240102;20:30:00')]),'fixture',zone='America/New_York')
    assert f[0]['executed_at']=='2024-01-03T01:30:00+00:00' and f[0]['fx_date']=='2024-01-02'
    assert f[0]['fx_date_basis']=='broker_cashflow_date'


def valuation(price='2025-01-02T16:30:00+00:00',positions='2025-01-03T06:50:00+00:00'):
    return dict(quantity='10',currency='USD',market_value='1100',qualified=True,as_of=price,positions_as_of=positions)


def test_i01_old_close_next_day_date_cashflow_has_no_false_total():
    r=attribution([execution(),flow()],[evidence()],valuation())
    assert r['unrealized_pnl']=='99' and r['known_dividends']=='10'
    assert r['total_holding_pnl'] is None and r['holding_period_return'] is None and r['annualized_return'] is None
    assert r['calculation_cutoff']=='2025-01-03T06:50:00+00:00' and r['attribution_as_of']=='2025-01-03'
    assert 'CASHFLOW_AFTER_COMMON_CUTOFF_OR_TIME_UNCERTAIN' in r['issues']


def test_i01_recent_close_with_proven_cashflow_before_common_cutoff_is_disclosed():
    income=flow('2025-01-03T06:00:00+00:00');income.update(time_precision='instant',source_local_date='2025-01-03')
    r=attribution([execution(),income],[evidence()],valuation())
    assert r['total_holding_pnl']=='109' and r['attribution_as_of']=='2025-01-03'
    assert r['price_as_of'].startswith('2025-01-02') and r['positions_as_of'].startswith('2025-01-03')
    assert 'RECENT_CLOSE_PRICE_PRECEDES_COMMON_CUTOFF' in r['issues']


@pytest.mark.parametrize('positions',['2025-01-03T06:50:00+00:00','2025-01-05T12:00:00+00:00'])
def test_i01_consistent_cutoff_and_non_trading_day_with_incomplete_coverage(positions):
    v=valuation(positions=positions)
    r=attribution([execution()],[evidence()],v)
    assert r['total_holding_pnl']=='99' and r['calculation_cutoff']==positions
    c=coverage(coverage_end='2025-01-03T00:00:00+00:00')
    r=attribution([execution()],[evidence(c)],v)
    assert r['total_holding_pnl'] is None and r['holding_period_return'] is None


def test_control_weighted_average_rebate_hand_golden_unchanged():
    facts=[execution('b1',commission='-2'),execution('b2','BUY','10','120','2024-02-02T15:00:00+00:00',commission='2'),
           execution('s1','SELL','5','130','2025-01-02T15:00:00+00:00')]
    v=dict(quantity='15',currency='USD',market_value='2100',qualified=True,
           as_of='2025-01-02T16:30:00+00:00',positions_as_of='2025-01-02T16:30:00+00:00')
    r=attribution(facts,[evidence()],v)
    assert (r['realized_pnl'],r['cost_basis'],r['unrealized_pnl'],r['total_holding_pnl'])==('99','1650','450','549')


def test_control_fact_conflict_atomicity_unchanged(history_db):
    persist(history_db);facts=deepcopy(closed());facts[0]['quantity']='11';facts.append(execution('new-id'))
    with pytest.raises(ValueError,match='HISTORY_EVENT_CONFLICT'):persist(history_db,facts)
    with Session(history_db.engine) as db:assert query(db,7)['total']==2

@pytest.mark.parametrize('price,basis,positions,at',[
    ('2025-01-02','close','2025-01-03T06:50:00+00:00','2025-01-03T07:00:00+00:00'),
    ('2025-01-03T16:30:00+00:00',None,'2025-01-05T12:00:00+00:00','2025-01-05T12:01:00+00:00')])
def test_i01_query_uses_real_recent_close_and_publication_proof(history_db,price,basis,positions,at):
    from app.models import Position
    from services.broker_sync.models import PositionSnapshotRun
    from services.broker_sync.repository import published_positions
    persist(history_db,[execution()])
    with Session(history_db.engine) as db:
        p=db.query(Position).one();p.quantity=10.0;p.current_price=110.0;p.original_value=1100.0;p.cost_price=100.1
        run=PositionSnapshotRun(portfolio_id=7,broker='snowball',account_id='fixture',sync_source='api',status='success')
        db.add(run);db.flush()
        run.context_json=json.dumps(dict(price_as_of=price,price_time_basis=basis,positions_as_of=positions,
            published_positions=published_positions(db,run)));db.commit()
    with Session(history_db.engine) as db:
        r=query(db,7,at=datetime.fromisoformat(at))['attribution'][0]
        assert r['current_holding']['qualified'] is True
        assert r['price_as_of']==price and r['calculation_cutoff']==positions
        assert r['total_holding_pnl']=='99' and 'RECENT_CLOSE_PRICE_PRECEDES_COMMON_CUTOFF' in r['issues']


def test_r03_alternate_time_representation_preserves_original_fact_and_provenance(history_db):
    facts=closed();persist(history_db,facts)
    variant=deepcopy(facts);variant[0]['source_time_raw']='20240102;150000'
    persist(history_db,variant)
    with Session(history_db.engine) as db:
        r=query(db,7);buy=r['items'][0]
        assert r['total']==2 and r['attribution'][0]['realized_pnl']=='198'
        assert buy['source_time_raw']==facts[0]['source_time_raw']
        assert {v['source_time_raw'] for v in buy['source_time_evidence'].values()}=={facts[0]['source_time_raw'],'20240102;150000'}
    bad=deepcopy(facts);bad[0]['fx_date']='2024-01-03'
    with pytest.raises(ValueError,match='HISTORY_EVENT_CONFLICT'):persist(history_db,bad)


def test_r03_conflicting_calendar_presentations_do_not_grant_annualization(history_db):
    facts=closed();persist(history_db,facts)
    variant=deepcopy(facts);variant[0].update(source_time_raw='20240103;02:00:00',source_local_date='2024-01-03',source_timezone='Australia/Sydney')
    persist(history_db,variant)
    with Session(history_db.engine) as db:
        r=query(db,7)['attribution'][0]
        assert r['realized_pnl']=='198' and r['total_holding_pnl']=='198'
        assert r['holding_period_return'] is None and r['annualized_return'] is None
        assert 'SOURCE_BUSINESS_DATE_AMBIGUOUS' in r['issues']


def test_r02_local_date_overlapping_prior_utc_day_is_preserved_as_uncertain():
    e=flow('2025-01-03');e['source_timezone']='Pacific/Kiritimati'
    c=Coverage.model_validate(coverage(coverage_start='2025-01-02T11:00:00Z',coverage_end='2025-01-02T12:00:00Z',source_timezone='Pacific/Kiritimati'))
    assert window_membership(e,c.coverage_start,c.coverage_end)=='uncertain'
    assert validate_events([e],c)[0]['executed_at']=='2025-01-03'
